#!/usr/bin/env python3
"""Copy canonical standalone resources; --check reports drift without writing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_FILES = ("README.md", "project.yaml", "pipeline.py", "routing.py", "requirements.txt", "run_e2e.py", "data/overrides/planned-road.geojson")
NOTES = """# Installed example contents

Generated maps, QGIS archives, source downloads, derived data, validation
reports and run logs are intentionally omitted. The manifest is the worked
project's definition, not evidence that this installed copy has already run.
Follow README.md to prepare the GIS runtime, then execute pipeline.py from a
writable copy. This downloads real source data; it is not an offline fixture.
The pedestrian network is pinned and built locally; entrance uncertainty and
the unresolved education-source license remain explicit.
"""


def expected_assets(root=ROOT):
    collection = json.loads((root / "collection.json").read_text())
    generalist = root / "skills/open-map-stack"
    outputs = {}
    for skill in collection["skills"]:
        target = root / skill["path"]
        if skill.get("project_assets", False):
            for source in (root / "templates").iterdir():
                if source.is_file():
                    outputs[target / "templates" / source.name] = source.read_bytes()
            for relative in EXAMPLE_FILES:
                outputs[target / "examples/tartu-development" / relative] = (root / "examples/tartu-development" / relative).read_bytes()
            outputs[target / "examples/tartu-development/PACKAGE-NOTES.md"] = NOTES.encode()
            outputs[target / "schemas/project-v1.schema.json"] = (root / "openmapstack/schemas/project-v1.schema.json").read_bytes()
        for reference in skill["shared_references"]:
            source = generalist / "references" / reference
            outputs[target / "references" / reference] = source.read_bytes()
    return outputs


def sync(*, check=False, root=ROOT):
    different = []
    for path, content in expected_assets(root).items():
        if not path.is_file() or path.read_bytes() != content:
            different.append(str(path.relative_to(root)))
            if not check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
    return different


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changes = sync(check=args.check)
    for path in changes:
        print(("DRIFT " if args.check else "WROTE ") + path)
    sys.exit(1 if args.check and changes else 0)
