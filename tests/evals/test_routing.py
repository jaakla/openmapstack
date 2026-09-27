from __future__ import annotations

import json
import gzip
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "evals"))

from adapters.routing import NON_READ_TOOLS, SURFACES, claude_events, codex_events, credentials
from routing import container_command, grade_evidence, load_cases, main, run_trial, stage_skill, stage_collection


def claude_trace(tool="Skill", arguments=None, output="skill instructions", error=False):
    return [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "call1", "name": tool, "input": arguments or {"skill": "open-map-stack"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "call1", "is_error": error, "content": output}]}},
        {"type": "result", "subtype": "success", "is_error": False},
    ]


class RoutingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.path = "/workspace/.claude/skills/open-map-stack/SKILL.md"
        self.inventory = {self.path: {"skill": "open-map-stack", "relative": "SKILL.md", "text": "skill instructions"}}
        self.expected = {"primary": "open-map-stack", "support": [], "forbidden": []}

    def test_successful_native_invocation_has_raw_event_reference(self):
        observed, gaps = claude_events(claude_trace(), self.inventory)
        self.assertEqual(observed[0]["kind"], "activation")
        self.assertEqual(observed[0]["event_index"], 1)
        self.assertEqual(observed[0]["verified_text_bytes"], len("skill instructions"))
        graded = grade_evidence(observed, gaps, self.expected)
        self.assertEqual(graded["status"], "passed")
        self.assertEqual(graded["primary_role"]["status"], "not_testable")
        self.assertEqual(graded["task_success"]["status"], "not_testable")

    def test_narrated_use_and_failed_invocations_do_not_count(self):
        narrated = {"type": "assistant", "message": {"content": [{"type": "text", "text": "I used open-map-stack"}]}}
        for trace in [[narrated, claude_trace()[-1]], claude_trace(error=True)]:
            observed, gaps = claude_events(trace, self.inventory)
            self.assertEqual(observed, [])
            self.assertEqual(grade_evidence(observed, gaps, self.expected)["status"], "failed")

    def test_failed_and_excess_activation_and_valid_negative_control(self):
        expected = {"primary": None, "support": [], "forbidden": ["open-map-stack"]}
        observed, gaps = claude_events(claude_trace(), self.inventory)
        result = grade_evidence(observed, gaps, expected)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["false_activation_count"], 1)
        self.assertEqual(grade_evidence([], [], expected)["status"], "passed")
        other, gaps = claude_events(claude_trace(arguments={"skill": "uncontrolled-skill"}), self.inventory)
        self.assertEqual(grade_evidence(other, gaps, self.expected)["status"], "failed")

    def test_reads_do_not_become_native_activations_and_bytes_are_lower_bounds(self):
        trace = claude_trace("Read", {"file_path": self.path}, "partial text")
        observed, gaps = claude_events(trace, self.inventory)
        self.assertEqual(observed[0]["kind"], "read")
        self.assertIsNone(observed[0]["verified_text_bytes"])
        negative = {"primary": None, "support": [], "forbidden": ["open-map-stack"]}
        result = grade_evidence(observed, gaps, negative)
        self.assertEqual(result["unexpected_consumption_count"], 1)
        self.assertEqual(result["false_activation_count"], 0)
        self.assertFalse(result["text_bytes_complete"])

    def test_numbered_read_output_verifies_source_bytes(self):
        trace = claude_trace("Read", {"file_path": self.path}, "1\tskill instructions")
        observations, _ = claude_events(trace, self.inventory)
        self.assertEqual(observations[0]["verified_text_bytes"], len("skill instructions"))

    def test_missing_and_opaque_telemetry_is_not_testable(self):
        for trace in [[], claude_trace()[:1], claude_trace("Bash", {"command": "python arbitrary.py"})]:
            observed, gaps = claude_events(trace, self.inventory)
            result = grade_evidence(observed, gaps, self.expected)
            self.assertEqual(result["status"], "not_testable")
            self.assertTrue(result["false_activation_count_is_lower_bound"])

    def test_tool_loading_and_web_fetches_are_not_skill_read_surfaces(self):
        # A discovery trial loaded WebFetch through ToolSearch before fetching
        # provider pages; neither can read a staged skill file.
        trace = [
            *claude_trace("ToolSearch", {"query": "select:WebFetch"}, "<functions>...</functions>")[:2],
            *claude_trace("WebFetch", {"url": "https://example.org/license"}, "page text")[:2],
            *claude_trace(),
        ]
        for index, event in enumerate(trace[:4]):
            block = event["message"]["content"][0]
            block["id" if "id" in block else "tool_use_id"] = f"call-{index // 2}"
        observed, gaps = claude_events(trace, self.inventory)
        self.assertEqual(gaps, [])
        self.assertEqual(grade_evidence(observed, gaps, self.expected)["status"], "passed")

    def test_claude_surface_grants_web_tools_but_not_bash(self):
        command = SURFACES["claude_code"]["command"]
        granted = command[command.index("--allowedTools") + 1].split(",")
        self.assertEqual(sorted(granted), ["WebFetch", "WebSearch"])
        # A granted tool the decoder does not know would mark every trial
        # that uses it not_testable.
        self.assertLessEqual(set(granted), NON_READ_TOOLS)

    def test_byte_metric_counts_unique_verified_sources(self):
        observations, gaps = claude_events(claude_trace(), self.inventory)
        graded = grade_evidence(observations * 3, gaps, self.expected)
        self.assertEqual(graded["verified_unique_text_bytes"], len("skill instructions"))

    def test_codex_requires_successful_read_command_not_path_mentions(self):
        def event(command, exit_code=0):
            return {"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": exit_code, "aggregated_output": "skill instructions"}}
        observed, gaps = codex_events([event(f"cat {self.path}")], self.inventory)
        self.assertEqual(observed[0]["kind"], "read")
        self.assertEqual(grade_evidence(observed, gaps, self.expected)["status"], "not_testable")
        for command, exit_code in [(f"echo {self.path}", 0), (f"cat {self.path}", 1), (f"cat {self.path}; echo fake", 0), ("cat /etc/SKILL.md", 0)]:
            observed, _ = codex_events([event(command, exit_code)], self.inventory)
            self.assertEqual(observed, [])

    def test_case_matrix_keeps_prompts_unhinted_and_profiles_distinct(self):
        cases = load_cases()
        self.assertEqual(len(cases), 8)
        for case in cases:
            for forbidden_hint in ["SKILL.md", ".agents/skills", ".claude/skills", "open-map-stack", "reproducible-gis-project", "geospatial-data-discovery", "spatial-sql"]:
                self.assertNotIn(forbidden_hint, case["prompt"])
        sql = next(c for c in cases if c["id"] == "chosen-engine-sql")
        self.assertEqual(sql["expectations"]["single"]["primary"], "open-map-stack")
        self.assertEqual(sql["expectations"]["collection"]["primary"], "spatial-sql")

    def test_retained_live_evidence_hashes_and_event_references_resolve(self):
        directory = REPO_ROOT / "evals/baselines/native-2026-09-13"
        report = json.loads((directory / "summary.json").read_text())
        prompts = {c["id"]: c["prompt"] for c in load_cases()}
        self.assertFalse(report["cost_complete"])
        self.assertEqual(len(report["trials"]), 11)
        for trial in report["trials"]:
            data = gzip.decompress((directory / trial["raw_events"]).read_bytes())
            self.assertEqual("sha256:" + hashlib.sha256(data).hexdigest(), trial["raw_events_sha256"])
            self.assertEqual(trial["prompt"], prompts[trial["case"]])
            self.assertEqual("sha256:" + hashlib.sha256(trial["prompt"].encode()).hexdigest(), trial["prompt_sha256"])
            events = json.loads(data)
            for observation in trial["observations_replayed"]:
                event = events[observation["event_index"]]
                self.assertEqual(event["type"], "user")
            if trial["phase"] == "excluded-pilot":
                self.assertEqual(trial["status"], "not_testable")


class RoutingExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        git = patch("openmapstack.snapshot._git", return_value={"commit": None, "dirty": None})
        git.start()
        self.addCleanup(git.stop)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "SKILL.md").write_text('---\nname: open-map-stack\ndescription: GIS tasks\n---\nSkill body.\n')
        (self.source / "references").mkdir()
        (self.source / "references" / "guide.md").write_text("Reference body.")
        (self.source / "templates").mkdir()
        self.image = "sha256:" + "a" * 64
        self.case = load_cases()[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_native_paths_use_controlled_copy_without_repository_payload(self):
        (self.source / "secret-repository-output").write_text("must not copy")
        for agent, surface in SURFACES.items():
            workspace = self.root / agent
            target, manifest, inventory = stage_skill(self.source, workspace, surface)
            self.assertEqual(target, workspace / surface["directory"] / "open-map-stack")
            self.assertFalse((target / "secret-repository-output").exists())
            self.assertEqual(manifest["file_count"], 2)
            self.assertEqual(len(inventory), 2)

    def test_collection_stages_complete_payload_at_both_native_surfaces(self):
        for agent, surface in SURFACES.items():
            target, manifest, inventory = stage_collection(REPO_ROOT, self.root / agent, surface, ["spatial-sql"])
            self.assertEqual(manifest["schema"], "openmapstack-skill-snapshot/v2")
            self.assertEqual([s["name"] for s in manifest["skills"]], ["spatial-sql"])
            path = self.root / agent / surface["directory"] / "spatial-sql"
            self.assertTrue((path / "references/spatial-sql.md").is_file())
            self.assertFalse((path / "templates").exists())
            self.assertFalse((path / "examples").exists())
            self.assertIn("/workspace/" + surface["directory"] + "/spatial-sql/SKILL.md", inventory)
            self.assertFalse((target / "evals").exists())

    def test_collection_selection_control_missing_discovery_and_payload_mutation(self):
        case = next(c for c in load_cases() if c["id"] == "chosen-engine-sql")
        for defect in (None, "undiscovered", "wrong-skill", "mutate"):
            def execute(command, **kwargs):
                # Snapshot Git provenance subprocesses are local, unpaid reads.
                if command[0] == "git":
                    return subprocess.CompletedProcess(command, 1, "", "")
                payload = json.loads(kwargs["input"])
                self.assertEqual(payload["prompt"], case["prompt"])
                self.assertEqual(len(payload["entrypoints"]), 1)
                self.assertNotIn("expectation", payload)
                mount = command[command.index("--mount") + 1]
                workspace = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
                # Native runtimes may create their own settings outside skills.
                (workspace / ".claude/settings.local.json").write_text("{}")
                if defect == "mutate":
                    (workspace / ".claude/skills/spatial-sql/references/spatial-sql.md").write_text("tampered")
                events = [
                    {"type": "oms.routing.runtime", "version": "test-cli", "returncode": 0},
                    {"type": "system", "subtype": "init", "skills": [] if defect == "undiscovered" else ["spatial-sql"]},
                    *claude_trace(arguments={"skill": "open-map-stack" if defect == "wrong-skill" else "spatial-sql"}),
                ]
                return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(e) for e in events), "")
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), patch("routing.subprocess.run", side_effect=execute):
                record = run_trial(case, source=REPO_ROOT, agent="claude_code", model="exact", image=self.image,
                                   max_budget_usd=0.2, destination=self.root / str(defect), profile="collection", selected=["spatial-sql"])
            self.assertEqual(record["schema"], "openmapstack-routing-smoke/v2")
            expected = "failed" if defect == "wrong-skill" else "not_testable" if defect else "passed"
            self.assertEqual(record["status"], expected, record)


    def test_stage_refuses_symlink_and_unsafe_names(self):
        (self.source / "references" / "outside.md").symlink_to(self.source / "SKILL.md")
        with self.assertRaises(ValueError):
            stage_skill(self.source, self.root / "work", SURFACES["codex"])
        (self.source / "SKILL.md").write_text('---\nname: ../../escape\n---\n')
        with self.assertRaises(ValueError):
            stage_skill(self.source, self.root / "work", SURFACES["codex"])

    def test_invalid_source_is_persisted_as_setup_gap(self):
        (self.source / "SKILL.md").unlink()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), patch("routing.subprocess.run") as execute:
            result = run_trial(self.case, source=self.source, agent="claude_code", model="exact", image=self.image, max_budget_usd=0.2, destination=self.root / "out")
        execute.assert_not_called()
        self.assertEqual(result["status"], "not_testable")
        self.assertTrue((self.root / "out/routing.json").is_file())

    def test_container_mounts_only_trial_and_hides_host_and_admin_scopes(self):
        command = container_command(self.image, self.root, "EXAMPLE_KEY", "test")
        self.assertEqual(command.count("--mount"), 1)
        self.assertIn(f"type=bind,src={self.root},dst=/workspace", command)
        self.assertIn("--pull=never", command)
        for directory in ["/root", "/home", "/etc/codex", "/etc/claude-code"]:
            self.assertTrue(any(c.startswith(directory + ":") for c in command))
        self.assertNotIn(str(REPO_ROOT), " ".join(command))
        with self.assertRaises(ValueError):
            container_command("node:latest", self.root, "EXAMPLE_KEY", "test")

    def test_unsupported_adapter_is_not_testable_without_starting_process(self):
        with patch("routing.subprocess.run") as run:
            record = run_trial(self.case, source=self.source, agent="openai_compatible", model="exact", image=self.image, max_budget_usd=0.2, destination=self.root / "out")
        run.assert_not_called()
        self.assertEqual(record["status"], "not_testable")
        self.assertEqual(record["reason"], "adapter_has_no_native_discovery")

    def test_execution_persists_prompt_and_events_and_does_not_send_expected_route(self):
        def execute(command, **kwargs):
            payload = json.loads(kwargs["input"])
            self.assertEqual(payload["prompt"], self.case["prompt"])
            self.assertNotIn("--safe-mode", payload["command"])
            self.assertNotIn("expected", payload)
            self.assertNotIn("sentinel-secret", json.dumps(payload) + " ".join(command))
            events = [{"type": "oms.routing.runtime", "version": "test-cli", "returncode": 0}, {"type": "system", "subtype": "init", "skills": ["open-map-stack"]}, *claude_trace()]
            return subprocess.CompletedProcess(command, 0, "\n".join(json.dumps(e) for e in events), "")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sentinel-secret"}), patch("routing.subprocess.run", side_effect=execute):
            record = run_trial(self.case, source=self.source, agent="claude_code", model="exact", image=self.image, max_budget_usd=0.2, destination=self.root / "out")
        self.assertEqual(record["status"], "passed")
        self.assertTrue((self.root / "out/events.json").is_file())
        self.assertEqual((self.root / "out/prompt.md").read_text(), self.case["prompt"])
        self.assertNotIn("sentinel-secret", (self.root / "out/routing.json").read_text())

    def test_changed_controlled_skill_and_bad_exit_cannot_pass(self):
        for defect in ["mutate", "exit", "unparsed", "undiscovered"]:
            def execute(command, **kwargs):
                if defect == "mutate":
                    mount = command[command.index("--mount") + 1]
                    workspace = Path(mount.split("src=", 1)[1].split(",dst=", 1)[0])
                    (workspace / ".claude/skills/open-map-stack/SKILL.md").write_text("tampered")
                events = [{"type": "oms.routing.runtime", "version": "test-cli", "returncode": 0}, {"type": "system", "subtype": "init", "skills": ["open-map-stack"]}, *claude_trace()]
                if defect == "undiscovered":
                    events[1]["skills"] = []
                output = "\n".join(json.dumps(e) for e in events)
                return subprocess.CompletedProcess(command, 1 if defect == "exit" else 0, output + ("\nmalformed" if defect == "unparsed" else ""), "")
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), patch("routing.subprocess.run", side_effect=execute):
                record = run_trial(self.case, source=self.source, agent="claude_code", model="exact", image=self.image, max_budget_usd=0.2, destination=self.root / defect)
            self.assertEqual(record["status"], "not_testable")

    def test_timeout_stops_container_and_preserves_partial_output(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}), patch("routing.subprocess.run", side_effect=[subprocess.TimeoutExpired("docker", 1, output=b"partial"), subprocess.CompletedProcess([], 0)]) as execute:
            record = run_trial(self.case, source=self.source, agent="claude_code", model="exact", image=self.image, max_budget_usd=0.2, destination=self.root / "out", timeout=1)
        self.assertEqual(record["reason"], "timeout")
        self.assertEqual(execute.call_args_list[1].args[0][:3], ["docker", "rm", "-f"])
        self.assertEqual((self.root / "out/stdout.jsonl").read_text(), "partial")

    def test_cli_does_not_return_green_for_unsupported_discovery(self):
        result = main(["--case", "bounded-discovery", "--agent", "openai_compatible", "--out", str(self.root / "out")])
        self.assertEqual(result, 2)

    def test_explicit_oauth_file_forwards_only_access_token(self):
        auth = self.root / "credential.json"
        auth.write_text(json.dumps({"claudeAiOauth": {"accessToken": "secret-access", "refreshToken": "secret-refresh"}, "unrelated": "private"}))
        name, environment = credentials("claude_code", auth)
        self.assertEqual(name, "CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(environment[name], "secret-access")
        self.assertNotIn("secret-refresh", json.dumps(environment))
        auth.write_text("not json")
        with self.assertRaisesRegex(ValueError, "cannot load"):
            credentials("claude_code", auth)

    def test_budget_is_divided_and_unknown_cost_stops_further_trials(self):
        cases = ["chosen-engine-sql", "casual-place-lookup"]
        results = [{"case": cases[0], "status": "passed", "reported_cost_usd": None, "returncode": 0}]
        destination = self.root / "out"
        destination.mkdir()
        with patch("routing.run_trial", side_effect=results) as run:
            status = main(["--agent", "claude_code", "--case", cases[0], "--case", cases[1], "--out", str(destination), "--max-budget-usd", "0.5"])
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["max_budget_usd"], 0.25)
        self.assertEqual(status, 2)
        self.assertEqual(json.loads((destination / "summary.json").read_text())["unrun_cases"], [cases[1]])

    def test_raw_streams_redact_forwarded_secret(self):
        token = "secret-value-do-not-persist"
        events = [{"type": "oms.routing.runtime", "version": "test-cli", "returncode": 0}, {"type": "system", "subtype": "init", "skills": ["open-map-stack"]}, *claude_trace()]
        events[-1]["result"] = token
        stdout = "\n".join(json.dumps(e) for e in events)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": token}), patch("routing.subprocess.run", return_value=subprocess.CompletedProcess([], 0, stdout, token)):
            run_trial(self.case, source=self.source, agent="claude_code", model="exact", image=self.image, destination=self.root / "out", max_budget_usd=0.2)
        for path in (self.root / "out").iterdir():
            self.assertNotIn(token, path.read_text())


if __name__ == "__main__":
    unittest.main()
