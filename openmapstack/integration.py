"""Static owner bindings and input-bound evidence for optional integrations.

Definitions remain in the tool's files; this module binds their identity, not
their implementation. A receipt proves consistency, not arbitrary execution.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from .integrity import canonical_file_set_hash, declared_input_paths, sha256_file
from .project import project_path
from .schema import project_schema_errors

SCHEMA = "openmapstack-integrations/v1"
EVIDENCE_SCHEMA = "openmapstack-integration-evidence/v1"
OWNERS = {"ingestion": {"dlt", "file"}, "transformation": {"dbt", "sql"},
          "orchestration": {"dagster", "python"},
          "presentation": {"observable", "qgis", "dashboard"}}


def definition_files(root: Path, relative: str) -> list[str]:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("definition path must be a nonempty string")
    raw = root / relative
    if any(parent.is_symlink() for parent in [raw, *raw.parents] if parent.is_relative_to(root)):
        raise ValueError(f"symlink definition: {relative}")
    path = project_path(root, relative)
    if path is None or not path.exists() or path.is_symlink():
        raise ValueError(f"missing/unsafe definition: {relative}")
    files = [path] if path.is_file() else sorted(path.rglob("*"))
    if any(p.is_symlink() for p in files):
        raise ValueError(f"symlink in definition: {relative}")
    result = [p.relative_to(root.resolve()).as_posix() for p in files if p.is_file()]
    if not result:
        raise ValueError(f"empty definition: {relative}")
    return result


def definition_hash(root: Path, relative: str) -> str:
    return canonical_file_set_hash(root, definition_files(root, relative))


def steps_hash(project: dict, steps: list[str]) -> str:
    by_id = {s["id"]: s for s in project["processing"]["steps"]}
    payload = json.dumps([by_id[s] for s in steps], sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def binding_errors(root: Path, project: dict) -> list[str]:
    if "integrations" not in project:
        return []
    schema_errors = project_schema_errors(project)
    if schema_errors:
        return [f"invalid manifest: {error}" for error in schema_errors]
    value = project["integrations"]
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        return ["unsupported/malformed integration declaration"]
    bindings = value.get("bindings")
    if not isinstance(bindings, list) or not bindings:
        return ["integrations.bindings must be a nonempty list"]
    errors, ids, owned = [], set(), set()
    steps = {s.get("id"): s for s in project["processing"]["steps"]}
    for binding in bindings:
        if not isinstance(binding, dict):
            errors.append("binding must be an object")
            continue
        name, owner, role = (binding.get(k) for k in ("id", "owner", "role"))
        if not isinstance(name, str) or not name or name in ids:
            errors.append("binding IDs must be nonempty and unique")
        else:
            ids.add(name)
        if not isinstance(role, str) or owner not in OWNERS.get(role, set()):
            errors.append(f"{name}: unsupported owner/role")
        try:
            if definition_hash(root, binding.get("definition")) != binding.get("sha256"):
                errors.append(f"{name}: definition identity drift")
        except (TypeError, ValueError, OSError) as exc:
            errors.append(f"{name}: {exc}")
        bound_steps = binding.get("steps", [])
        if not isinstance(bound_steps, list) or any(not isinstance(s, str) for s in bound_steps):
            errors.append(f"{name}: steps must be a list of IDs")
            continue
        if all(step in steps for step in bound_steps) and steps_hash(project, bound_steps) != binding.get("steps_sha256"):
            errors.append(f"{name}: processing summary drift")
        for step in bound_steps:
            if step not in steps or step in owned:
                errors.append(f"{name}: missing or multiply owned step {step}")
            elif steps[step].get("integration") != name:
                errors.append(f"{name}: step summary disagrees with authoritative binding")
            owned.add(step)
    for sid, step in steps.items():
        if step.get("integration") and sid not in owned:
            errors.append(f"unbound integration step: {sid}")
    output = project["outputs"].get(value.get("evidence"))
    if not isinstance(output, dict) or output.get("kind") != "document" or project_path(root, output.get("path")) is None:
        errors.append("integration evidence must bind a declared document output")
    if "run_evidence" in value:
        run = project["outputs"].get(value["run_evidence"])
        if not isinstance(run, dict) or run.get("kind") != "document" or project_path(root, run.get("path")) is None:
            errors.append("run evidence must bind a declared document output")
    bundled = set()
    for bundle in value.get("bundles", []):
        if bundle["target"] in bundled:
            errors.append("bundle targets must be unique")
        bundled.add(bundle["target"])
        if not isinstance(bundle, dict):
            errors.append("bundle must be an object")
            continue
        path = project_path(root, bundle.get("path"))
        target = next((t for t in project.get("delivery", {}).get("targets", [])
                       if t.get("id") == bundle.get("target")), None)
        view = project_path(root, project["outputs"].get(target.get("output"), {}).get("path")) if target else None
        if path is None or path == root.resolve() or view is None or not view.is_relative_to(path):
            errors.append("bundle must contain its selected target view below the project root")
    return errors


def evidence_payload(root: Path, project: dict) -> dict:
    declaration = project["integrations"]
    analytical = {key: sha256_file(project_path(root, output["path"]))
                  for key, output in project["outputs"].items()
                  if output.get("kind", "geodata") in {"geodata", "table"}}
    bundles = {b["target"]: {p: sha256_file(root / p) for p in definition_files(root, b["path"])}
               for b in declaration.get("bundles", [])}
    return {"schema": EVIDENCE_SCHEMA, "bindings": declaration["bindings"],
            "inputs_hash": canonical_file_set_hash(root, declared_input_paths(root, project)),
            "analytical_outputs": analytical, "bundles": bundles,
            "run_evidence": sha256_file(project_path(root, project["outputs"][declaration["run_evidence"]]["path"])) if declaration.get("run_evidence") else None}


def write_evidence(root: Path, project: dict) -> None:
    errors = binding_errors(root, project)
    if errors:
        raise ValueError("; ".join(errors))
    path = project_path(root, project["outputs"][project["integrations"]["evidence"]]["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence_payload(root, project), indent=2) + "\n")
