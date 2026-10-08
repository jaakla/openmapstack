"""Versioned delivery selection and input-bound view metadata.

An absent declaration is legacy v1, not a request to migrate existing projects.
New authoring records ``default_delivery()`` explicitly in the manifest.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .integrity import sha256_file
from .project import get_in, project_path
from .schema import load_packaged_schema, validation_errors

SCHEMA = "openmapstack-delivery/v1"
EVIDENCE_SCHEMA = "openmapstack-delivery-evidence/v1"
METADATA_SCHEMA = "openmapstack-view/v1"
KINDS = {"dashboard", "qgis", "observable"}


def default_delivery(inputs: list[str]) -> dict[str, Any]:
    """Record the new-project default; output keys must be declared separately."""
    return {"schema": SCHEMA, "targets": [{
        "id": "dashboard", "kind": "dashboard", "output": "dashboard",
        "inputs": inputs, "evidence": "dashboard_evidence", "mode": "local",
    }]}


def targets(project: dict[str, Any]) -> list[dict[str, Any]]:
    declaration = project.get("delivery")
    if not isinstance(declaration, dict):
        return []
    values = declaration.get("targets")
    return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []


def selected(project: dict[str, Any], kind: str) -> bool:
    return any(t.get("kind") == kind for t in targets(project))


def declaration_errors(project: dict[str, Any], root: Path) -> list[str]:
    if "delivery" not in project:
        return []
    schema = load_packaged_schema("project-v1.schema.json")
    errors = validation_errors(project["delivery"], {
        "$defs": schema["$defs"], "$ref": "#/$defs/delivery",
    })
    if errors:
        return errors
    outputs = project.get("outputs")
    outputs = outputs if isinstance(outputs, dict) else {}
    ids: set[str] = set()
    bound: set[str] = set()
    paths: set[Path] = set()
    for target in targets(project):
        tid = target["id"]
        if tid in ids:
            errors.append(f"duplicate delivery target id: {tid}")
        ids.add(tid)
        roles = [target["output"], target["evidence"]]
        if target.get("mode", "local") == "hosted":
            roles.append(target["build"])
            try:
                host = urlsplit(target["url"]).hostname
            except ValueError:
                host = None
            if not host:
                errors.append(f"{tid}: hosted URL must identify a host")
            try:
                datetime.fromisoformat(target["retrieved_at"].replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{tid}: hosted retrieval timestamp is invalid")
        for key in roles:
            output = outputs.get(key)
            if not isinstance(output, dict) or output.get("kind") != "document":
                errors.append(f"{tid}: {key!r} must bind a declared document output")
                continue
            path = project_path(root, output.get("path"))
            if path is None:
                errors.append(f"{tid}: {key!r} has an unsafe output path")
                continue
            if key in bound or path in paths:
                errors.append(f"{tid}: delivery artifacts must have distinct keys and paths: {key}")
            bound.add(key)
            paths.add(path)
        for key in target["inputs"]:
            output = outputs.get(key)
            if not isinstance(output, dict) or output.get("kind", "geodata") not in {"geodata", "table"}:
                errors.append(f"{tid}: input {key!r} must bind an analytical output")
            elif project_path(root, output.get("path")) is None:
                errors.append(f"{tid}: input {key!r} has an unsafe output path")
        for table in get_in(project, "presentation", "tables", default=[]) or []:
            if isinstance(table, dict) and table.get("output") not in target["inputs"]:
                errors.append(f"{tid}: displayed table {table.get('output')!r} is not a target input")
        from .checks.qgis import _manifest_layer_files, _acceptable_files, _is_client_local
        resolved = _manifest_layer_files(project)
        input_paths = {outputs[k].get("path") for k in target["inputs"]
                       if isinstance(outputs.get(k), dict) and isinstance(outputs[k].get("path"), str)}
        for layer in get_in(project, "presentation", "map", "layers", default=[]) or []:
            if not isinstance(layer, dict) or _is_client_local(layer):
                continue
            files = _acceptable_files(resolved, layer.get("source"))
            # Overrides are immutable inputs, already represented in shared
            # semantics/provenance; normal view datasets are target inputs.
            override_files = {get_in(o, "geometry_file", "path") for o in project.get("overrides") or []
                              if isinstance(get_in(o, "geometry_file", "path"), str)}
            if files and not set(files) & (input_paths | override_files):
                errors.append(f"{tid}: displayed layer {layer.get('source')!r} is not a target input")
        view = outputs.get(target["output"])
        view = view if isinstance(view, dict) else {}
        suffix = Path(str(view.get("path", ""))).suffix.lower()
        allowed = {"qgis": {".qgz"}, "dashboard": {".html"}, "observable": {".html"}}
        if suffix not in allowed[target["kind"]]:
            errors.append(f"{tid}: output format is unsupported for {target['kind']}")
        evidence = outputs.get(target["evidence"])
        evidence = evidence if isinstance(evidence, dict) else {}
        if Path(str(evidence.get("path", ""))).suffix.lower() != ".json":
            errors.append(f"{tid}: evidence must be a JSON document output")
    for target in targets(project):
        for key in target["inputs"]:
            output = outputs.get(key)
            if isinstance(output, dict) and project_path(root, output.get("path")) in paths:
                errors.append(f"{target['id']}: analytical input {key!r} aliases a delivery artifact")
    return errors


def shared_semantics(project: dict[str, Any]) -> dict[str, Any]:
    """Shared analytical meaning; target layout and web engine are not meaning."""
    presentation = dict(project.get("presentation") or {})
    for key in ("layout", "provenance_ui", "editing"):
        presentation.pop(key, None)
    if isinstance(presentation.get("map"), dict):
        presentation["map"] = dict(presentation["map"])
        presentation["map"].pop("engine_preference", None)
        presentation["map"].pop("interaction", None)
    return {
        "project_id": get_in(project, "project", "id"),
        "interpretation": project.get("interpretation"),
        "sources": project.get("sources"), "overrides": project.get("overrides"),
        "processing": project.get("processing"), "presentation": presentation,
        "output_definitions": {key: value for key, value in (project.get("outputs") or {}).items()
                               if isinstance(value, dict) and value.get("kind", "geodata") in {"geodata", "table"}},
        "warnings": project.get("warnings") or [],
    }


def view_metadata(root: Path, project: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    analytical = {}
    for key in target["inputs"]:
        path = project_path(root, project["outputs"][key]["path"])
        if path is None or not path.is_file():
            raise ValueError(f"missing analytical output: {key}")
        analytical[key] = "sha256:" + sha256_file(path)
    # Normalize YAML timestamps and similar values to the JSON representation.
    semantics = json.loads(json.dumps(shared_semantics(project), default=str))
    return {"schema": METADATA_SCHEMA, "target": target["id"],
            "kind": target["kind"], "semantics": semantics,
            "analytical_outputs": analytical}


def metadata_json(root: Path, project: dict[str, Any], target: dict[str, Any]) -> str:
    """Embed in HTML script#openmapstack-view or QGIS custom property.

    Escape XML attributes separately for QGIS. Escaping '<' here prevents HTML
    data from terminating a script element; it does not change the JSON value.
    """
    return json.dumps(view_metadata(root, project, target), sort_keys=True,
                      ensure_ascii=False).replace("<", "\\u003c")


def evidence_payload(root: Path, project: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    path = project_path(root, project["outputs"][target["output"]]["path"])
    if path is None or not path.is_file():
        raise ValueError(f"missing delivery output: {target['output']}")
    payload = {"schema": EVIDENCE_SCHEMA, "metadata": view_metadata(root, project, target),
               "view_sha256": "sha256:" + sha256_file(path)}
    if target.get("mode", "local") == "hosted":
        build = project_path(root, project["outputs"][target["build"]]["path"])
        if build is None or not build.is_file():
            raise ValueError(f"missing hosted build/export configuration: {target['build']}")
        payload["hosted"] = {"url": target["url"], "retrieved_at": str(target["retrieved_at"]),
                             "build_sha256": "sha256:" + sha256_file(build)}
    return payload


def write_evidence(root: Path, project: dict[str, Any]) -> None:
    """Pipeline stage: bind completed views to their analytical inputs.

    This writes evidence, not verification results. Independent checks still
    inspect the metadata actually embedded in each artifact and its runtime.
    """
    errors = declaration_errors(project, root)
    if errors:
        raise ValueError("; ".join(errors))
    for target in targets(project):
        path = project_path(root, project["outputs"][target["evidence"]]["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence_payload(root, project, target), indent=2,
                                   ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def inspection(project: dict[str, Any]) -> dict[str, Any]:
    return {"schema": get_in(project, "delivery", "schema"),
            "mode": "explicit" if "delivery" in project else "legacy",
            "targets": targets(project)}
