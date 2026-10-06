#!/usr/bin/env python3
"""Export retained v2 eval evidence to Allure without running or regrading it.

Allure's JSON interface: https://allurereport.org/docs/how-it-works-test-result-file/
The original result schema and grading remain authoritative.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import uuid
import zipfile

from jsonschema import Draft202012Validator, ValidationError


STATUSES = {
    "passed": "passed",
    "assertions_failed": "failed",
    "setup_failed": "broken",
    "skipped": "skipped",
}


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"evidence path escapes artifact root: {relative}")
    return path


def failure_detail(result: dict[str, Any]) -> str:
    """Prefer a recorded provider diagnosis to a generic subprocess exit code."""
    if result["status"] == "skipped":
        return result.get("reason", "Case does not support the selected mode")
    error = result.get("setup_error") or {}
    if result["status"] == "setup_failed":
        agent = result.get("agent_run") or {}
        data = error.get("data") or {}
        parts = [f"{error.get('stage', 'setup')}: {error.get('message', 'Execution unavailable')}"]
        # Adapters retain structured provider failures in final_message even
        # when the CLI writes nothing to stderr.
        for value in (agent.get("final_message"), agent.get("stderr"), data.get("stderr")):
            if value and value not in parts:
                parts.append(str(value))
        return "\n\n".join(parts)
    failures = [a for a in result.get("assertions", []) if a.get("hard_gate") and not a.get("matched_expectation")]
    return "\n".join(
        f"{a['assert']} ({a.get('actual_code') or a.get('actual_status')}): {a.get('detail', '')}"
        for a in failures
    )


def _step(assertion: dict[str, Any]) -> dict[str, Any]:
    actual = assertion["actual_status"]
    # A mutation succeeds when its expected failure/code matches. An
    # unavailable assertion is never drawn as a successful assertion.
    status = "skipped" if actual == "not_testable" else ("passed" if assertion["matched_expectation"] else "failed")
    return {
        "name": f"{assertion['assert']} — expected {assertion.get('expect', 'passed')}, actual {actual}",
        "status": status,
        "stage": "finished",
        "parameters": [{"name": key, "value": _json(assertion.get(key))} for key in ("args", "expect_code", "actual_code", "hard_gate", "mutation_role")],
        "statusDetails": {"message": assertion.get("detail", ""), "trace": _json(assertion)},
    }


class _Attachments:
    def __init__(self, directory: Path):
        self.directory = directory

    def add(self, name: str, data: bytes, media_type: str = "text/plain", suffix: str = ".txt") -> dict[str, str]:
        source = f"{uuid.uuid4()}-attachment{suffix}"
        (self.directory / source).write_bytes(data)
        return {"name": name, "source": source, "type": media_type}

    def text(self, name: str, value: Any, *, json_value: bool = False) -> dict[str, str]:
        return self.add(name, (_json(value) if json_value else str(value)).encode(), "application/json" if json_value else "text/plain", ".json" if json_value else ".txt")


def _evidence(result: dict[str, Any], root: Path, attachments: _Attachments) -> tuple[list[dict], list[str]]:
    retained = [attachments.text("Grading record", result, json_value=True)]
    warnings: list[str] = []
    agent = result.get("agent_run") or {}
    if agent.get("final_message"):
        retained.append(attachments.text("Agent final message / provider error", agent["final_message"]))
    for key in ("generator", "extra_generators", "rerun_generator", "clean_rerun", "mutation_analysis"):
        if result.get(key):
            retained.append(attachments.text(key, result[key], json_value=True))
    rerun = result.get("clean_rerun") or {}
    execution = rerun.get("execution") or {}
    for key in ("stdout", "stderr"):
        if execution.get(key):
            retained.append(attachments.text(f"Clean rerun {key}", execution[key]))
    bundle_name = result.get("artifact_bundle")
    if not bundle_name:
        if result["mode"] in {"live", "visual"} and result["status"] != "skipped":
            warnings.append("No retained artifact bundle was recorded.")
        return retained, warnings
    bundle = _safe_path(root, bundle_name)
    if not bundle.is_dir():
        warnings.append(f"Retained artifact bundle is missing: {bundle_name}")
        return retained, warnings
    for filename in ("prompt.md", "agent.json", "stdout.txt", "stderr.txt", "events.ndjson"):
        path = _safe_path(bundle, filename)
        if path.is_file():
            # Raw event streams are displayed as text rather than interpreted.
            retained.append(attachments.add(filename, path.read_bytes(), "application/json" if filename.endswith(".json") else "text/plain", path.suffix))
    visual = _safe_path(bundle, "visual")
    if visual.is_dir():
        for path in sorted(visual.rglob("*.png")):
            safe = _safe_path(bundle, str(path.relative_to(bundle)))
            if safe.is_file():
                retained.append(attachments.add(str(path.relative_to(bundle)), safe.read_bytes(), "image/png", ".png"))
    project = _safe_path(bundle, "generated-project")
    if project.is_dir():
        # Deliver generated HTML/code only inside a downloadable archive;
        # reporting never executes the project or renders its HTML inline.
        source = f"{uuid.uuid4()}-attachment.zip"
        with zipfile.ZipFile(attachments.directory / source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(project.rglob("*")):
                safe = _safe_path(project, str(path.relative_to(project)))
                if safe.is_file():
                    archive.write(safe, str(path.relative_to(project)))
        retained.append({"name": "Generated project (ZIP)", "source": source, "type": "application/zip"})
    return retained, warnings


def markdown_summary(summary: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return html.escape(str(value)).replace("|", "&#124;").replace("\n", "<br>")

    outcomes = summary.get("outcomes", {})
    lines = ["## OpenMapStack eval review", "", f"**{outcomes.get('passed', 0)} passed · {outcomes.get('assertions_failed', 0)} failed grading · {outcomes.get('setup_failed', 0)} infrastructure failures**", "",
             "Allure is a review view; the v2 JSON remains the source of truth. Score types have separate denominators.", "",
             "| Score type | Passed / graded | Infrastructure failures | Unavailable assertions | Unmet soft gates |", "| --- | --- | --- | --- | --- |"]
    for name, score in sorted(summary.get("score_types", {}).items()):
        if score.get("trials_run"):
            capability = score.get("capability") or {}
            lines.append(f"| {cell(name)} | {score['passed']} / {score['graded_trials']} | {score['setup_failed']} | {capability.get('assertions_not_testable', 0)} | {capability.get('unmet_soft_gates', 0)} |")
    errors = Counter(failure_detail(r) for r in summary["results"] if r["status"] == "setup_failed")
    if summary.get("run_setup_failed"):
        lines.extend(["", "**Run setup failed.**", "", *[cell(e.get("message", e)) for e in summary.get("setup_errors", [])]])
    if errors:
        lines.extend(["", "### Infrastructure errors", "", "| Trials affected | Recorded diagnosis |", "| --- | --- |"])
        lines.extend(f"| {count} | {cell(detail)} |" for detail, count in errors.most_common())
    failed = [r for r in summary["results"] if r["status"] == "assertions_failed"]
    if failed:
        lines.extend(["", "### Grading failures", "", "| Case | Trial | Arm | Failure details |", "| --- | --- | --- | --- |"])
        for result in failed:
            failures = [a for a in result.get("assertions", []) if a.get("hard_gate") and not a.get("matched_expectation")]
            codes = Counter(a.get("actual_code") or a.get("actual_status") for a in failures)
            diagnosis = "; ".join(f"{code} ×{count}" for code, count in codes.items())
            if failures:
                # Keep the CI landing page short; Allure retains every full
                # assertion and argument rather than repeating cascades here.
                diagnosis += ": " + failures[0].get("detail", "")[:300]
            lines.append(f"| {cell(result['id'])} | {result['trial']} | {cell(result.get('arm') or '—')} | {cell(diagnosis)} |")
    lines.extend(["", "Download the Allure report artifact and open `index.html`. Assertion steps show expected and actual outcomes; attachments retain the evidence.", "",
                  "Independent repetitions are separate results, not retries. Unavailable checks appear as skipped steps. A passed trial may still have unmet soft gates; consult the capability counts above.", ""])
    return "\n".join(lines)


def export_report(summary: dict[str, Any], output: Path, *, artifact_root: Path) -> int:
    schema = json.loads((Path(__file__).parent / "schemas/results-v2.schema.json").read_text())
    Draft202012Validator(schema).validate(summary)
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output directory is not empty: {output}; use a new directory to avoid mixing runs")
    output.parent.mkdir(parents=True, exist_ok=True)
    identities: set[str] = set()
    records = list(summary["results"])
    if summary["run_setup_failed"]:
        records.append({"id": "run-setup", "trial": 1, "mode": summary["run_config"].get("mode", "fixture"), "score_type": "run_setup", "status": "setup_failed", "setup_error": {"stage": "run_setup", "message": _json(summary["setup_errors"])}})
    config = summary["run_config"]
    with tempfile.TemporaryDirectory(prefix=".allure-export-", dir=output.parent) as temporary:
        directory = Path(temporary) / "results"
        directory.mkdir()
        attachments = _Attachments(directory)
        for result in records:
            agent = result.get("agent_run") or {}
            identity = {
                "case": result["id"],
                "mode": result["mode"],
                "score_type": result.get("score_type"),
                "agent": agent.get("agent") or config.get("agent"),
                # Provider failures can report a synthetic response model.
                # Compare trials by the requested model, retaining the
                # adapter's observed value separately in the evidence.
                "model": config.get("model") or agent.get("model"),
                "arm": result.get("arm"),
                "trial": result["trial"],
                "seed": result.get("seed"),
            }
            history_id = _hash(identity)
            if history_id in identities:
                raise ValueError(f"duplicate trial identity: {identity}")
            identities.add(history_id)
            retained, warnings = _evidence(result, artifact_root, attachments)
            steps = [_step(a) for a in result.get("assertions", [])]
            unavailable = sum(a.get("actual_status") == "not_testable" for a in result.get("assertions", []))
            soft_unmet = sum(not a.get("hard_gate") and not a.get("matched_expectation") for a in result.get("assertions", []))
            duration = result.get("duration_s")
            duration_text = f"{duration:.2f}" if isinstance(duration, (int, float)) else "unrecorded"
            description = [
                f"Recorded outcome: {result['status']}",
                f"Duration: {duration_text} seconds",
                f"Unavailable assertions: {unavailable}; unmet soft gates: {soft_unmet}.",
                f"Artifact bundle: {result.get('artifact_bundle') or 'not retained'}",
                *warnings,
            ]
            record = {
                "uuid": str(uuid.uuid4()),
                "historyId": history_id,
                "testCaseId": _hash({"case": result["id"], "mode": result["mode"]}),
                "fullName": f"openmapstack.{result['mode']}.{result['id']}.trial-{result['trial']}",
                "name": f"{result['id']} [trial {result['trial']}, {result.get('arm') or result['mode']}]",
                "status": STATUSES[result["status"]],
                "stage": "finished",
                "description": "\n\n".join(description),
                "labels": [
                    {"name": "parentSuite", "value": "OpenMapStack evals"},
                    {"name": "suite", "value": result.get("score_type") or result["mode"]},
                    {"name": "subSuite", "value": result["id"]},
                    {"name": "tag", "value": result.get("case_type") or "run_setup"},
                ],
                "parameters": [
                    {"name": key, "value": str(value)}
                    for key, value in identity.items() if key != "case" and value is not None
                ],
                "steps": steps,
                "attachments": retained,
                "statusDetails": {
                    "message": failure_detail(result),
                    "trace": _json(result.get("hard_failures") or []),
                },
            }
            if agent.get("model") and agent["model"] != identity["model"]:
                record["parameters"].append({"name": "reported_model", "value": agent["model"], "excluded": True})
            if unavailable or soft_unmet:
                record["labels"].append({"name": "tag", "value": "capability-incomplete"})
            # Historical summaries have no wall-clock trial timestamps. Do
            # not invent a timeline from export time or sequential durations.
            (directory / f"{record['uuid']}-result.json").write_text(_json(record), encoding="utf-8")
        (directory / "categories.json").write_text(_json([{"name": "Infrastructure / agent execution", "matchedStatuses": ["broken"]}, {"name": "Grading expectation mismatch", "matchedStatuses": ["failed"]}]), encoding="utf-8")
        (directory / "environment.properties").write_text("\n".join(f"{key}={json.dumps(config.get(key), ensure_ascii=True)}" for key in ("run_id", "mode", "agent", "model", "skill_commit")), encoding="utf-8")
        # Whole-run provenance and denominators remain available alongside
        # the rendering; they are not reinterpreted as one Allure pass rate.
        (directory / "openmapstack-summary.json").write_text(_json(summary), encoding="utf-8")
        (directory / "summary.md").write_text(markdown_summary(summary), encoding="utf-8")
        if output.exists():
            output.rmdir()
        directory.replace(output)
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="existing openmapstack-eval-results/v2 JSON")
    parser.add_argument("--output", type=Path, required=True, help="new or empty Allure results directory")
    parser.add_argument("--artifact-root", type=Path, default=Path.cwd(), help="base for artifact_bundle paths (repository or extracted CI artifact root)")
    parser.add_argument("--allow-missing", action="store_true", help="CI only: report a missing input as a broken run-setup result")
    args = parser.parse_args(argv)
    try:
        if args.allow_missing and not args.results.exists():
            summary = {"schema": "openmapstack-eval-results/v2", "run_config": {}, "environment": {}, "selection": {}, "outcomes": {}, "run_setup_failed": True, "setup_errors": [{"stage": "report_input", "message": f"No eval JSON was produced: {args.results}. Inspect earlier job steps; no trials can be reported."}], "score_types": {}, "agent_benchmark": {"task_success_rate": None, "task_success_rate_95ci": None, "hard_safety_gate_rate": None, "success_at_1": None, "trials": 0, "graded_trials": 0, "median_duration_s": None, "p95_duration_s": None, "median_tokens": None, "median_cost_usd": None, "setup_failures": 0, "dimensions": {}}, "mutation_score": {"total": 0, "valid": 0, "detected": 0, "survived": 0, "invalid": 0, "isolated": 0, "score": None}, "results": []}
        else:
            summary = json.loads(args.results.read_text(encoding="utf-8"))
        count = export_report(summary, args.output, artifact_root=args.artifact_root)
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as exc:
        print(f"Report export failed: {exc}", file=sys.stderr)
        return 2
    print(f"Exported {count} Allure results to {args.output}; summary: {args.output / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
