from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
import zipfile

from evals import report
from tests.test_evals import eval_runner


class EvalReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def assertion(self, **changes):
        return {"assert": "geodata.row_count", "args": {"path": "candidate.parquet", "equals": 3}, "expect": "passed", "expect_code": None, "actual_status": "passed", "actual_code": None, "detail": "3 rows", "matched_expectation": True, "hard_gate": True, **changes}

    def trial(self, **changes):
        return {"id": "001-test", "trial": 1, "mode": "fixture", "score_type": "contract_ci", "case_type": "positive", "status": "passed", "duration_s": 1.5, "assertions": [self.assertion()], "hard_failures": [], **changes}

    def export(self, results, directory="allure", **config):
        summary = eval_runner.build_summary(results, {"mode": "fixture", "run_id": "test-run", **config})
        output = self.root / directory
        report.export_report(summary, output, artifact_root=self.root)
        return output, [json.loads(p.read_text()) for p in sorted(output.glob("*-result.json"))]

    def test_statuses_mutations_and_unavailable_evidence(self):
        results = [
            self.trial(),
            self.trial(id="failed", status="assertions_failed", assertions=[self.assertion(actual_status="failed", actual_code="count_mismatch", matched_expectation=False)]),
            self.trial(id="error", status="setup_failed", assertions=[], setup_error={"stage": "generator", "message": "boom"}),
            self.trial(id="skip", status="skipped", assertions=[], reason="unsupported mode"),
            self.trial(id="mutation", score_type="mutation_tests", case_type="mutation", assertions=[self.assertion(expect="failed", expect_code="count_mismatch", actual_status="failed", actual_code="count_mismatch")]),
            self.trial(id="unavailable", assertions=[self.assertion(actual_status="not_testable", actual_code="pyqgis_unavailable", matched_expectation=False, hard_gate=False)]),
        ]
        output, records = self.export(results)
        cases = {r["name"].split(" ")[0]: r for r in records}
        self.assertEqual(cases["001-test"]["status"], "passed")
        self.assertEqual(cases["failed"]["status"], "failed")
        self.assertEqual(cases["error"]["status"], "broken")
        self.assertEqual(cases["skip"]["status"], "skipped")
        self.assertEqual(cases["mutation"]["steps"][0]["status"], "passed")
        self.assertIn("expected failed, actual failed", cases["mutation"]["steps"][0]["name"])
        self.assertEqual(cases["unavailable"]["steps"][0]["status"], "skipped")
        self.assertIn({"name": "tag", "value": "capability-incomplete"}, cases["unavailable"]["labels"])
        self.assertIn("Score types have separate denominators", (output / "summary.md").read_text())

    def test_repetitions_arms_and_models_do_not_collapse_into_retries(self):
        results = [self.trial(trial=trial, arm=arm) for trial in (1, 2, 3) for arm in ("plain", "oms")]
        _, records = self.export(results, model="model-a")
        self.assertEqual(len({r["historyId"] for r in records}), 6)
        _, second = self.export(results, "second", model="model-a", run_id="next-run")
        self.assertEqual({r["historyId"] for r in records}, {r["historyId"] for r in second})
        _, changed_model = self.export(results, "third", model="model-b")
        self.assertTrue({r["historyId"] for r in records}.isdisjoint({r["historyId"] for r in changed_model}))

    def test_provider_error_is_visible_and_grouped_in_summary(self):
        diagnosis = "API Error: 400 workspace API usage limits. Reset 2026-11-01."
        results = [self.trial(trial=n, status="setup_failed", assertions=[], setup_error={"stage": "agent_execution", "message": "agent failed with status 1"}, agent_run={"final_message": diagnosis}) for n in (1, 2)]
        output, records = self.export(results)
        self.assertTrue(all(diagnosis in r["statusDetails"]["message"] for r in records))
        self.assertIn(f"| 2 | agent_execution: agent failed with status 1<br><br>{diagnosis} |", (output / "summary.md").read_text())

    def test_synthetic_error_model_does_not_replace_requested_model(self):
        _, records = self.export([self.trial(agent_run={"model": "<synthetic>"})], model="requested-model")
        parameters = {p["name"]: p for p in records[0]["parameters"]}
        self.assertEqual(parameters["model"]["value"], "requested-model")
        self.assertEqual(parameters["reported_model"]["value"], "<synthetic>")
        self.assertTrue(parameters["reported_model"]["excluded"])

    def test_bundle_rerun_and_visual_attachments_survive_relocation(self):
        bundle = self.root / "evidence/trial"
        project = bundle / "generated-project"
        project.mkdir(parents=True)
        (project / "dashboard.html").write_text("<script>throw Error('never execute')</script>")
        (bundle / "prompt.md").write_text("task")
        (bundle / "stderr.txt").write_text("provider error")
        (bundle / "visual").mkdir()
        (bundle / "visual/dashboard.png").write_bytes(b"PNG evidence")
        rerun = {"status": "failed", "execution": {"stderr": "ModuleNotFoundError: pyproj"}}
        output, records = self.export([self.trial(mode="visual", artifact_bundle="evidence/trial", clean_rerun=rerun)])
        attached = {a["name"]: a for a in records[0]["attachments"]}
        self.assertIn("ModuleNotFoundError", (output / attached["Clean rerun stderr"]["source"]).read_text())
        self.assertEqual(attached["visual/dashboard.png"]["type"], "image/png")
        with zipfile.ZipFile(output / attached["Generated project (ZIP)"]["source"]) as archive:
            self.assertEqual(archive.read("dashboard.html"), (project / "dashboard.html").read_bytes())
        self.assertTrue(all((output / a["source"]).is_file() for a in attached.values()))

    def test_missing_bundle_is_explicit_without_changing_grade(self):
        _, records = self.export([self.trial(mode="live", artifact_bundle="missing")])
        self.assertEqual(records[0]["status"], "passed")
        self.assertIn("bundle is missing", records[0]["description"])

    def test_run_setup_and_missing_input_never_create_green_zero_trial_report(self):
        missing = self.root / "missing.json"
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertEqual(report.main([str(missing), "--output", str(self.root / "strict")]), 2)
        self.assertFalse((self.root / "strict").exists())
        output = self.root / "ci"
        self.assertEqual(report.main([str(missing), "--allow-missing", "--output", str(output)]), 0)
        records = [json.loads(p.read_text()) for p in output.glob("*-result.json")]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "broken")
        self.assertIn("No eval JSON was produced", records[0]["statusDetails"]["message"])

    def test_path_escape_and_symlink_evidence_fail_closed(self):
        for relative in ("../outside", "/etc"):
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "escapes artifact root"):
                self.export([self.trial(artifact_bundle=relative)])
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "stdout.txt").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(ValueError, "escapes artifact root"):
            self.export([self.trial(artifact_bundle="bundle")])
        self.assertFalse((self.root / "allure").exists())

    def test_invalid_input_duplicate_trials_and_stale_output_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate trial identity"):
            self.export([self.trial(), self.trial()])
        self.export([self.trial()])
        with self.assertRaisesRegex(ValueError, "not empty"):
            self.export([self.trial()])
        path = self.root / "invalid.json"
        path.write_text('{"schema":"unknown"}')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(report.main([str(path), "--output", str(self.root / "invalid")]), 2)


if __name__ == "__main__":
    unittest.main()
