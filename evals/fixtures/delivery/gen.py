"""Generate delivery contract controls and isolated mutations, without an agent."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("delivery_create", ROOT / "examples/delivery-profiles/create.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def generate(destination: Path, kinds: list[str], mutation: str | None, hosted: bool = False):
    path = example.create(destination, kinds, hosted=hosted)
    subprocess.run([sys.executable, str(path.parent / "pipeline.py")], check=True)
    project = yaml.safe_load(path.read_text())
    if mutation == "unknown":
        project["delivery"]["targets"][0]["kind"] = "unknown-service"
    elif mutation == "missing":
        (path.parent / project["outputs"][kinds[0]]["path"]).unlink()
    elif mutation == "stale":
        page = path.parent / project["outputs"][kinds[0]]["path"]
        page.write_text(page.read_text().replace('"target": "' + kinds[0] + '"', '"target": "wrong"'))
        # Even a receipt rewritten for the modified view must not certify it.
        from openmapstack.delivery import write_evidence
        write_evidence(path.parent, project)
    elif mutation == "unpinned":
        project["sources"]["parcels"].pop("pin")
        project["sources"]["parcels"]["version"] = {"identifier": "latest"}
    elif mutation == "crs":
        project["processing"]["analysis_crs"] = "EPSG:4326"
    elif mutation == "lineage":
        project["presentation"]["map"]["layers"][0]["source"] = "parcels"
    elif mutation == "qgis_provenance":
        target = next(t for t in project["delivery"]["targets"] if t["kind"] == "qgis")
        archive_path = path.parent / project["outputs"][target["output"]]["path"]
        with zipfile.ZipFile(archive_path) as archive:
            xml = ET.fromstring(archive.read("project.qgs"))
        xml.remove(xml.find("projectMetadata"))
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("project.qgs", ET.tostring(xml))
        # Correct hidden metadata plus an updated view hash still cannot certify provenance.
        from openmapstack.delivery import write_evidence
        write_evidence(path.parent, project)
    path.write_text(yaml.safe_dump(project, sort_keys=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--targets", default="dashboard")
    parser.add_argument("--hosted", action="store_true")
    parser.add_argument("--break", dest="mutation", choices=["unknown", "missing", "stale", "unpinned", "crs", "lineage", "qgis_provenance"])
    args = parser.parse_args()
    generate(args.destination, args.targets.split(","), args.mutation, args.hosted)
