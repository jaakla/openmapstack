#!/usr/bin/env python3
"""Prepare or upload saved v2 evals to LangSmith; never run or regrade agents."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "site/langsmith/index.html"
FORMAT = "openmapstack-human-review/v1"
MAX_FILE_BYTES = 2_000_000


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"evidence path escapes its root: {name}")
    return path


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} byte review limit: {path.name}")
    return path.read_text(encoding="utf-8")


def inline_assets(document: str, project: Path) -> str:
    """Bundle local JS/CSS for srcdoc, without fetching remote dependencies."""
    class Assets(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.parts: list[str] = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            name = attributes.get("src") if tag == "script" else attributes.get("href") if tag == "link" and attributes.get("rel") == "stylesheet" else None
            if name:
                url = urlparse(name)
                if not url.scheme and not url.netloc:
                    path = safe_path(project, unquote(url.path).lstrip("/"))
                    if path.is_file():
                        text = read_text(path)
                        if tag == "script":
                            self.parts.append("<script>" + text.replace("</script", "<\\/script"))
                        else:
                            self.parts.append("<style>" + text.replace("</style", "<\\/style") + "</style>")
                        return
            self.parts.append(self.get_starttag_text())

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            self.parts.append(f"</{tag}>")

        def handle_data(self, data):
            self.parts.append(data)

        def handle_entityref(self, name):
            self.parts.append(f"&{name};")

        def handle_charref(self, name):
            self.parts.append(f"&#{name};")

        def handle_comment(self, data):
            self.parts.append(f"<!--{data}-->")

        def handle_decl(self, declaration):
            self.parts.append(f"<!{declaration}>")

    parser = Assets()
    parser.feed(document)
    parser.close()
    bundled = "".join(parser.parts)
    if len(bundled.encode()) > MAX_FILE_BYTES * 3:
        raise ValueError("bundled dashboard exceeds review limit")
    return bundled


def artifact_view(project: Path, images: Path | None = None) -> dict[str, Any]:
    """Read supported artifacts without executing generated code or pipelines."""
    view: dict[str, Any] = {"html": None, "images": [], "tables": []}
    if project.is_dir():
        dashboard = safe_path(project, "dashboard.html")
        if dashboard.is_file():
            view["html"] = inline_assets(read_text(dashboard), project)
        derived = safe_path(project, "data/derived")
        if derived.is_dir():
            for candidate in sorted(derived.glob("*.geojson"))[:12]:
                path = safe_path(project, candidate.relative_to(project).as_posix())
                data = json.loads(read_text(path))
                features = data.get("features", [])
                if not isinstance(features, list):
                    raise ValueError(f"invalid feature collection: {candidate.name}")
                view["tables"].append({
                    "name": candidate.name, "count": len(features),
                    "rows": [{"feature_id": feature.get("id"), **(feature.get("properties") or {})} for feature in features[:40]],
                    "truncated": len(features) > 40,
                })
    if images and images.is_dir():
        for candidate in sorted(images.glob("*.png"))[:6]:
            path = safe_path(images, candidate.name)
            if path.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"image exceeds review limit: {candidate.name}")
            view["images"].append({"name": candidate.name, "src": "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()})
    return view


def check_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [{
        "name": a["assert"], "args": a.get("args", {}),
        "expected_status": a.get("expect", "passed"), "expected_code": a.get("expect_code"),
        "actual_status": a.get("actual_status", "not_testable"), "actual_code": a.get("actual_code"),
        "matched": a.get("matched_expectation", False), "hard_gate": a.get("hard_gate", False),
        "detail": a.get("detail", ""), "evidence": a.get("data", {}),
    } for a in result.get("assertions", [])]


def review_row(result: dict[str, Any], root: Path, config: dict[str, Any], references: Path | None) -> dict[str, Any]:
    prompt = "Prompt was not retained for this trial."
    actual = {"html": None, "images": [], "tables": []}
    warnings: list[str] = []
    if result.get("artifact_bundle"):
        bundle = safe_path(root, result["artifact_bundle"])
        prompt_path = safe_path(bundle, "prompt.md")
        if prompt_path.is_file():
            prompt = read_text(prompt_path)
        else:
            warnings.append("Retained prompt is missing.")
        actual = artifact_view(safe_path(bundle, "generated-project"), safe_path(bundle, "visual"))
        if not bundle.is_dir():
            warnings.append("Retained artifact bundle is missing.")
    else:
        warnings.append("No retained artifact bundle; review uses recorded check evidence.")
    reference: dict[str, Any] = {"available": False, "provenance": "No independent reference artifacts supplied.", "html": None, "images": [], "tables": []}
    if references:
        directory = safe_path(references, result["id"])
        manifest_path = safe_path(directory, "reference.json")
        if manifest_path.is_file():
            manifest = json.loads(read_text(manifest_path))
            # Never present today's fixture as the oracle for a historical run.
            if not config.get("skill_commit") or manifest.get("task_commit") != config["skill_commit"]:
                raise ValueError(f"reference task_commit differs from recorded commit for {result['id']}")
            if not manifest.get("provenance"):
                raise ValueError("reference.json must explain reference provenance")
            reference = {"available": True, "provenance": manifest["provenance"],
                         **artifact_view(directory, safe_path(directory, "visual"))}
    agent = result.get("agent_run") or {}
    error = result.get("setup_error") or {}
    diagnosis = "\n\n".join(str(v) for v in (error.get("message"), agent.get("final_message") if result["status"] == "setup_failed" else None, agent.get("stderr")) if v)
    rerun = result.get("clean_rerun") or {}
    return {
        "schema": FORMAT, "case": result["id"], "trial": result["trial"], "mode": result["mode"],
        "arm": result.get("arm"), "model": config.get("model") or agent.get("model"),
        "status": result["status"], "duration_s": result.get("duration_s"),
        "prompt": prompt, "checks": check_rows(result), "reference": reference, "actual": actual,
        "answer": agent.get("final_message", ""), "diagnosis": diagnosis,
        "rerun": {"status": rerun.get("status"), "stderr": (rerun.get("execution") or {}).get("stderr", "")},
        "warnings": warnings,
    }


def prepare(summary: dict[str, Any], *, artifact_root: Path, dataset_name: str, experiment_name: str,
            case: str | None = None, trial: int | None = None, arm: str | None = None,
            references: Path | None = None, imported_at: str | None = None) -> dict[str, Any]:
    schema = json.loads((ROOT / "evals/schemas/results-v2.schema.json").read_text())
    Draft202012Validator(schema).validate(summary)
    config = summary["run_config"]
    results = [r for r in summary["results"] if r["status"] != "skipped"
               and (case is None or r["id"] == case) and (trial is None or r["trial"] == trial)
               and (arm is None or r.get("arm") == arm)]
    if not results:
        raise ValueError("no attempted trials match the selection")
    if len({(r["mode"], r.get("score_type"), r.get("arm")) for r in results}) != 1:
        raise ValueError("one experiment must contain one mode, score type and arm; select --arm or --case")
    # The import API requires timestamps; these explicitly describe import,
    # not execution. Original durations are retained in the view and metadata.
    imported_at = imported_at or datetime.now(timezone.utc).isoformat()
    rows = []
    seen: set[str] = set()
    for result in results:
        review = review_row(result, artifact_root, config, references)
        inputs = {"case": result["id"], "trial": result["trial"], "mode": result["mode"],
                  "seed": result.get("seed"), "prompt": review["prompt"]}
        # Arm/model belong to the experiment, so candidates align with a baseline.
        row_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dataset_name + ":" + digest(inputs)))
        if row_id in seen:
            raise ValueError("duplicate trial identity in selected results")
        seen.add(row_id)
        scores = [{"key": "trial_success", "value": result["status"],
                   **({"score": int(result["status"] == "passed")} if result["status"] in {"passed", "assertions_failed"} else {})}]
        for index, check in enumerate(review["checks"]):
            available = check["actual_status"] != "not_testable"
            scores.append({"key": f"{check['name']}:{index + 1}", "value": check["actual_status"],
                           "comment": check["detail"], **({"score": int(check["matched"])} if available else {})})
        rows.append({
            "row_id": row_id, "inputs": inputs,
            "expected_outputs": {"schema": FORMAT, "reference": review["reference"], "checks": [
                {key: check[key] for key in ("name", "args", "expected_status", "expected_code", "hard_gate")}
                for check in review["checks"]]},
            "actual_outputs": review, "evaluation_scores": scores,
            "start_time": imported_at, "end_time": imported_at,
            "run_name": f"{result['id']} / trial {result['trial']}",
            "run_metadata": {"timestamp_source": "import_time", "original_duration_s": result.get("duration_s"),
                             "score_type": result.get("score_type"), "arm": result.get("arm"), "run_id": config.get("run_id")},
            **({"error": review["diagnosis"] or "Trial setup failed"} if result["status"] == "setup_failed" else {}),
        })
    return {"experiment_name": experiment_name, "dataset_name": dataset_name,
            "experiment_description": "Imported retained eval evidence; no agent execution or regrading. Timestamps describe import, not execution.",
            "experiment_start_time": imported_at, "experiment_end_time": imported_at,
            "experiment_metadata": {"source_schema": summary["schema"], "timestamp_source": "import_time", "run_config": config,
                                    "source_outcomes": summary["outcomes"], "source_score_types": summary["score_types"]},
            "results": rows}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward an API key across a redirect.


def upload(payload: dict[str, Any], *, endpoint: str, api_key: str, workspace_id: str | None = None) -> dict[str, Any]:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("LANGSMITH_ENDPOINT must be an HTTPS API base URL")
    headers = {"Content-Type": "application/json", "X-Api-Key": api_key}
    if workspace_id:
        headers["X-Tenant-Id"] = workspace_id
    request = Request(endpoint.rstrip("/") + "/api/v1/datasets/upload-experiment",
                      data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with build_opener(NoRedirect).open(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as error:
        # Provider bodies can echo submitted evidence or headers. Keep diagnostics
        # useful without logging the token or entire imported project.
        hints = {401: "check the API key and region", 403: "check workspace permissions and LANGSMITH_WORKSPACE_ID",
                 413: "select fewer trials or smaller artifacts", 429: "wait before retrying"}
        raise ValueError(f"LangSmith HTTP {error.code}: {hints.get(error.code, 'inspect the request in LangSmith; upload was not retried')}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True, help="fresh local review directory")
    parser.add_argument("--dataset-name", default="openmapstack-live-review")
    parser.add_argument("--experiment-name", required=True)
    parser.add_argument("--case")
    parser.add_argument("--trial", type=int)
    parser.add_argument("--arm")
    parser.add_argument("--references", type=Path, help="explicit commit-matched reference artifacts, never inferred from today's fixtures")
    parser.add_argument("--upload", action="store_true", help="upload to LangSmith using LANGSMITH_API_KEY")
    parser.add_argument("--endpoint", default=os.environ.get("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"))
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ValueError("output already exists; choose a fresh directory")
        key = os.environ.get("LANGSMITH_API_KEY")
        if args.upload and not key:
            raise ValueError("set LANGSMITH_API_KEY in your environment before --upload; do not put it in a command argument")
        payload = prepare(json.loads(args.results.read_text()), artifact_root=args.artifact_root,
                          dataset_name=args.dataset_name, experiment_name=args.experiment_name,
                          case=args.case, trial=args.trial, arm=args.arm, references=args.references)
        args.output.mkdir(parents=True)
        (args.output / "upload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        # JSON in script elements must not allow </script> from generated HTML.
        preview = json.dumps([r["actual_outputs"] for r in payload["results"]], ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
        template = RENDERER.read_text()
        (args.output / "index.html").write_text(template.replace('/* SAVED_REVIEW_DATA */ null', preview), encoding="utf-8")
        print(f"Prepared {len(payload['results'])} trials; preview: {args.output / 'index.html'}")
        if args.upload:
            response = upload(payload, endpoint=args.endpoint, api_key=key, workspace_id=os.environ.get("LANGSMITH_WORKSPACE_ID"))
            (args.output / "receipt.json").write_text(json.dumps(response, indent=2), encoding="utf-8")
            print("Uploaded. Open Datasets & Experiments in your LangSmith region; dataset: " + args.dataset_name)
        else:
            print("Local preparation only. Add --upload to send this evidence to LangSmith.")
        return 0
    except (OSError, ValueError, URLError, ValidationError) as error:
        # Avoid printing request objects or raw provider responses containing credentials.
        if isinstance(error, ValidationError):
            print("langsmith: invalid eval results schema", file=sys.stderr)
        else:
            print(f"langsmith: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
