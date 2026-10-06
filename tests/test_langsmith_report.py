from __future__ import annotations

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from evals.integrations import langsmith
from tests.test_evals import eval_runner


class LangSmithReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.project = self.bundle / "generated-project"
        self.project.mkdir(parents=True)
        (self.bundle / "prompt.md").write_text("Find three candidate parcels.")
        (self.project / "dashboard.html").write_text("<h1>Real dashboard</h1>")
        derived = self.project / "data/derived"
        derived.mkdir(parents=True)
        (derived / "candidates.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"id": "P1", "properties": {"area_m2": 9000}, "geometry": None}]}))

    def assertion(self, **changes):
        return {"assert": "geodata.row_count", "args": {"equals": 3}, "expect": "passed", "expect_code": None,
                "actual_status": "passed", "actual_code": None, "matched_expectation": True,
                "hard_gate": True, "detail": "3 rows", "data": {}, **changes}

    def trial(self, **changes):
        return {"id": "001-case", "trial": 1, "mode": "live", "arm": "oms", "score_type": "agent_benchmark",
                "case_type": "positive", "status": "passed", "duration_s": 1.5,
                "assertions": [self.assertion()], "artifact_bundle": "bundle", **changes}

    def prepare(self, trials=None, **options):
        config = options.pop("config", {})
        summary = eval_runner.build_summary(trials or [self.trial()], {"mode": "live", "run_id": "saved-run", "skill_commit": "abc", **config})
        return langsmith.prepare(summary, artifact_root=self.root, dataset_name="frozen-tasks-abc",
                                experiment_name="pilot", imported_at="2026-10-06T00:00:00+00:00", **options)

    def test_prompt_real_artifacts_and_expected_criteria_are_distinct(self):
        payload = self.prepare()
        row = payload["results"][0]
        self.assertEqual(row["inputs"]["prompt"], "Find three candidate parcels.")
        self.assertIn("Real dashboard", row["actual_outputs"]["actual"]["html"])
        self.assertEqual(row["actual_outputs"]["actual"]["tables"][0]["rows"][0]["area_m2"], 9000)
        self.assertFalse(row["expected_outputs"]["reference"]["available"])
        self.assertEqual(row["expected_outputs"]["checks"][0]["args"], {"equals": 3})
        self.assertNotIn("actual_status", row["expected_outputs"]["checks"][0])
        self.assertEqual(row["start_time"], row["end_time"])
        self.assertEqual(row["run_metadata"]["timestamp_source"], "import_time")
        self.assertEqual(row["run_metadata"]["original_duration_s"], 1.5)

    def test_broken_and_unavailable_evidence_have_no_numeric_success_score(self):
        payload = self.prepare([
            self.trial(status="setup_failed", assertions=[], setup_error={"message": "agent failed"},
                       agent_run={"final_message": "Workspace API usage limits reached"}),
            self.trial(trial=2, assertions=[self.assertion(actual_status="not_testable", matched_expectation=False, hard_gate=False)]),
            self.trial(trial=3, assertions=[self.assertion(expect="failed", actual_status="failed", expect_code="count_mismatch", actual_code="count_mismatch")]),
        ])
        broken, unavailable, mutation = payload["results"]
        self.assertIn("usage limits", broken["error"])
        self.assertNotIn("score", broken["evaluation_scores"][0])
        self.assertNotIn("score", unavailable["evaluation_scores"][1])
        self.assertEqual(mutation["evaluation_scores"][1]["score"], 1)

    def test_repetitions_separate_and_comparison_arms_align(self):
        first = self.prepare([self.trial(), self.trial(trial=2)])
        second = self.prepare([self.trial(arm="plain"), self.trial(arm="plain", trial=2)], config={"model": "different"})
        self.assertEqual([r["row_id"] for r in first["results"]], [r["row_id"] for r in second["results"]])
        self.assertNotEqual(first["results"][0]["row_id"], first["results"][1]["row_id"])
        with self.assertRaisesRegex(ValueError, "duplicate trial"):
            self.prepare([self.trial(), self.trial()])
        with self.assertRaisesRegex(ValueError, "one mode, score type and arm"):
            self.prepare([self.trial(), self.trial(arm="plain")])
        selected = self.prepare([self.trial(), self.trial(arm="plain")], arm="plain")
        self.assertEqual(len(selected["results"]), 1)

    def test_references_must_be_explicit_and_commit_matched(self):
        references = self.root / "references"
        directory = references / "001-case"
        directory.mkdir(parents=True)
        (directory / "dashboard.html").write_text("<h1>Frozen reference</h1>")
        manifest = directory / "reference.json"
        manifest.write_text(json.dumps({"task_commit": "wrong", "provenance": "Frozen fixture control"}))
        with self.assertRaisesRegex(ValueError, "task_commit differs"):
            self.prepare(references=references)
        manifest.write_text(json.dumps({"task_commit": "abc", "provenance": "Frozen fixture control"}))
        reference = self.prepare(references=references)["results"][0]["expected_outputs"]["reference"]
        self.assertTrue(reference["available"])
        self.assertIn("Frozen reference", reference["html"])

    def test_path_escape_missing_evidence_and_size_limits(self):
        for path in ("../outside", "/etc"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "escapes"):
                self.prepare([self.trial(artifact_bundle=path)])
        (self.project / "dashboard.html").unlink()
        (self.project / "dashboard.html").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.prepare()
        (self.project / "dashboard.html").unlink()
        (self.project / "dashboard.html").write_text("x" * (langsmith.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "review limit"):
            self.prepare()
        view = self.prepare([self.trial(artifact_bundle="missing")])["results"][0]["actual_outputs"]
        self.assertIn("Retained artifact bundle is missing.", view["warnings"])

    def test_local_dashboard_assets_are_bundled_and_cannot_escape(self):
        assets = self.project / "assets"
        assets.mkdir()
        (assets / "map.js").write_text("window.MAP_READY=true;")
        (assets / "map.css").write_text("body{color:blue}")
        (self.project / "dashboard.html").write_text('<link rel="stylesheet" href="assets/map.css"><script src="assets/map.js"></script><h1>Map</h1>')
        html = self.prepare()["results"][0]["actual_outputs"]["actual"]["html"]
        self.assertIn("<script>window.MAP_READY=true;</script>", html)
        self.assertIn("<style>body{color:blue}</style>", html)
        (self.project / "dashboard.html").write_text('<script src="../../outside.js"></script>')
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.prepare()

    def test_api_request_uses_eu_and_workspace_without_disclosing_key_on_error(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://eu.api.smith.langchain.com", 403, "Forbidden", {}, io.BytesIO(b"secret-token"))
        with patch.object(langsmith, "build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "workspace permissions") as caught:
                langsmith.upload(self.prepare(), endpoint="https://eu.api.smith.langchain.com", api_key="secret-token", workspace_id="workspace")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://eu.api.smith.langchain.com/api/v1/datasets/upload-experiment")
        self.assertEqual(request.get_header("X-api-key"), "secret-token")
        self.assertEqual(request.get_header("X-tenant-id"), "workspace")
        self.assertNotIn("secret-token", str(caught.exception))
        for endpoint in ("http://example.com", "https://user:password@example.com", "https://example.com?key=secret"):
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValueError, "HTTPS API base"):
                langsmith.upload({}, endpoint=endpoint, api_key="secret-token")

    def test_cli_preview_is_offline_and_embedded_html_cannot_escape_script(self):
        hostile = '</script><script>window.PWNED=true</script>'
        (self.project / "dashboard.html").write_text(hostile)
        source = self.root / "results.json"
        source.write_text(json.dumps(eval_runner.build_summary([self.trial()], {"mode": "live"})))
        output = self.root / "review"
        with patch.object(langsmith, "upload") as upload:
            self.assertEqual(langsmith.main([str(source), "--artifact-root", str(self.root), "--output", str(output), "--experiment-name", "pilot"]), 0)
            upload.assert_not_called()
        preview = (output / "index.html").read_text()
        self.assertNotIn(hostile, preview)
        self.assertIn('\\u003c/script>', preview)
        with redirect_stderr(io.StringIO()), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(langsmith.main([str(source), "--output", str(self.root / "upload"), "--experiment-name", "pilot", "--upload"]), 2)
        self.assertFalse((self.root / "upload").exists())

    def test_validation_error_reports_fields_without_rejected_inputs_or_key(self):
        opener = MagicMock()
        body = {'detail': [{'loc': ['body', 'results', 0, 'start_time'], 'type': 'datetime_parsing',
                            'input': 'private-dashboard-content secret-token', 'msg': 'private-dashboard-content'}]}
        opener.open.side_effect = HTTPError('https://eu.api.smith.langchain.com', 422, 'Invalid', {},
                                           io.BytesIO(json.dumps(body).encode()))
        with patch.object(langsmith, 'build_opener', return_value=opener):
            with self.assertRaisesRegex(ValueError, 'datetime_parsing') as caught:
                langsmith.upload({}, endpoint='https://eu.api.smith.langchain.com', api_key='secret-token')
        self.assertNotIn('private-dashboard-content', str(caught.exception))
        self.assertNotIn('secret-token', str(caught.exception))

        for detail in ('Invalid workspace secret-token', ['Invalid workspace secret-token']):
            opener.open.side_effect = HTTPError('https://eu.api.smith.langchain.com', 422, 'Invalid', {},
                                               io.BytesIO(json.dumps({'detail': detail}).encode()))
            with patch.object(langsmith, 'build_opener', return_value=opener):
                with self.assertRaisesRegex(ValueError, 'Invalid workspace') as caught:
                    langsmith.upload({}, endpoint='https://eu.api.smith.langchain.com', api_key='secret-token')
            self.assertNotIn('secret-token', str(caught.exception))


if __name__ == "__main__":
    unittest.main()
