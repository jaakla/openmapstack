"""Create an offline delivery-contract example in a separate workspace."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent


def create(destination: Path, kinds: list[str] | None = None, *, hosted: bool = False) -> Path:
    kinds = ["dashboard"] if kinds is None else kinds
    if not kinds or len(set(kinds)) != len(kinds) or set(kinds) - {"dashboard", "qgis", "observable"}:
        raise ValueError("choose distinct targets from dashboard,qgis,observable")
    if hosted and "observable" not in kinds:
        raise ValueError("hosted evidence requires the observable target")
    destination = destination.resolve()
    if destination == HERE.resolve():
        raise ValueError("create a separate workspace, not generated artifacts in this example")
    destination.mkdir(parents=True, exist_ok=True)
    project = yaml.safe_load((HERE / "project.yaml").read_text())
    for name in ("pipeline.py", "README.md", "data/source/parcels.geojson"):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERE / name, target)
    project["outputs"] = {k: v for k, v in project["outputs"].items() if v.get("kind") != "document"}
    project["delivery"]["targets"] = []
    for kind in kinds:
        view = "project.qgz" if kind == "qgis" else f"{kind}.html"
        project["outputs"][kind] = {"path": view, "format": "QGZ" if kind == "qgis" else "HTML",
                                      "kind": "document", "generated_by": "render"}
        project["outputs"][kind + "_evidence"] = {
            "path": f"delivery/{kind}-evidence.json", "format": "JSON",
            "kind": "document", "generated_by": "render"}
        target = {"id": kind, "kind": kind, "output": kind,
                  "inputs": ["candidates", "areas"], "evidence": kind + "_evidence", "mode": "local"}
        if kind == "observable" and hosted:
            target.update(mode="hosted", url="https://example.invalid/observable-export",
                          retrieved_at="2026-10-07T00:00:00Z", build="observable_build")
            project["outputs"]["observable_build"] = {"path": "delivery/observable-build.json",
                "format": "JSON", "kind": "document", "generated_by": "render"}
        project["delivery"]["targets"].append(target)
    if "dashboard" not in kinds:
        project["presentation"].pop("layout", None)
        project["presentation"].pop("provenance_ui", None)
    if "qgis" in kinds:
        project["presentation"]["map"] = {
            "layer_groups": [{"id": "analysis", "title": "Analysis"}],
            "layers": [{"source": "candidates", "group": "analysis", "semantic_role": "primary_result"}],
        }
    path = destination / "project.yaml"
    path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--targets", default="dashboard")
    parser.add_argument("--hosted", action="store_true")
    args = parser.parse_args()
    print(create(args.destination, args.targets.split(","), hosted=args.hosted))
