"""JSON Schema loading and validation helpers."""

from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def load_packaged_schema(name: str) -> dict[str, Any]:
    resource = files("openmapstack.schemas").joinpath(name)
    return json.loads(resource.read_text(encoding="utf-8"))


def validation_errors(instance: Any, schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(_json_value(instance)), key=lambda item: list(item.path))
    formatted: list[str] = []
    for error in errors:
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        formatted.append(f"{location}: {error.message}")
    return formatted


def project_schema_errors(project: Any) -> list[str]:
    errors = validation_errors(project, load_packaged_schema("project-v1.schema.json"))
    metadata = project.get("project") if isinstance(project, dict) else None
    aoi = metadata.get("aoi") if isinstance(metadata, dict) else None
    if isinstance(aoi, dict):
        errors.extend(f"project.aoi.bbox: {error}" for error in aoi_bbox_errors(aoi.get("bbox")))
    return errors


def aoi_bbox_errors(bbox: Any) -> list[str]:
    """Check finite ordered bounds, including inequalities JSON Schema cannot express."""
    try:
        valid = (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                 and all(type(value) in (int, float) and math.isfinite(value) for value in bbox)
                 and bbox[0] < bbox[2] and bbox[1] < bbox[3])
    except OverflowError:
        valid = False
    return [] if valid else ["must be four finite numbers [minx, miny, maxx, maxy] with min < max"]


def assumptions_errors(assumptions: Any) -> list[str]:
    """Check assumption shape from the schema, then the unique-ID invariant."""
    schema = load_packaged_schema("project-v1.schema.json")
    errors = validation_errors(
        assumptions,
        {
            "$defs": schema["$defs"],
            **schema["properties"]["interpretation"]["properties"]["assumptions"],
        },
    )
    if errors:
        return errors
    counts = Counter(a["id"] for a in _json_value(assumptions))
    duplicates = sorted(aid for aid, count in counts.items() if count > 1)
    if duplicates:
        errors.append(f"duplicate ids: {duplicates}")
    return errors
