"""Offline synthetic delivery-contract example; never a factual parcel study.

The source is a committed pair of rectangles in EPSG:3301. The deliberately
bounded shoelace calculation applies only to these simple rings. General GIS
work should use an appropriate spatial engine and account for holes/multiparts.
"""

from __future__ import annotations

import csv
import datetime
import html
import json
import platform
import zipfile
from pathlib import Path

import yaml

from openmapstack.delivery import metadata_json, targets, write_evidence
from openmapstack.integrity import canonical_file_set_hash, declared_input_paths, declared_output_paths, file_inventory
from openmapstack.checks.delivery import evidence_matches

ROOT = Path(__file__).resolve().parent


def main() -> None:
    project = yaml.safe_load((ROOT / "project.yaml").read_text())
    started = datetime.datetime.now(datetime.timezone.utc)
    source = json.loads((ROOT / "data/source/parcels.geojson").read_text())
    minimum = project["processing"]["steps"][1]["minimum_area_m2"]
    chosen = []
    for feature in source["features"]:
        ring = feature["geometry"]["coordinates"][0]
        area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:]))) / 2
        if area >= minimum:
            chosen.append({**feature, "properties": {**feature["properties"], "area_m2": area}})
    candidate = ROOT / project["outputs"]["candidates"]["path"]
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps({**source, "features": chosen}, sort_keys=True) + "\n")
    table = ROOT / project["outputs"]["areas"]["path"]
    with table.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "area_m2"])
        writer.writerows((f["properties"]["id"], f["properties"]["area_m2"]) for f in chosen)

    completed = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for target in targets(project):
        path = ROOT / project["outputs"][target["output"]]["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        metadata = metadata_json(ROOT, project, target)
        providers = "; ".join(s["provider"] for s in project["sources"].values())
        limitations = " ".join(a["statement"] + " " + a["rationale"] for a in project["interpretation"]["assumptions"])
        warnings = " ".join(w["statement"] for w in project.get("warnings") or [])
        provenance = f"{providers}; synthetic-v1; CC0; EPSG:3301. {limitations} {warnings}"
        credits = (f'Generated with AI using the free OpenMapStack toolkit. Analysis made: {completed}. '
                   f'Author: {project["project"]["author"]["name"]} ({project["project"]["author"]["email"]}).')
        if target["kind"] == "qgis":
            # Contract fixture: complete CRS and portable source, not a claim of
            # native QGIS runtime success. That is checked independently.
            crs = '<spatialrefsys><authid>EPSG:3301</authid><proj4>+proj=lcc +lat_1=59.33333333333334 +lat_2=58 +lat_0=57.51755393055556 +lon_0=24 +x_0=500000 +y_0=6375000 +ellps=GRS80 +units=m +no_defs</proj4></spatialrefsys>'
            xml = f'''<qgis version="3.40.0"><projectCrs>{crs}</projectCrs>
<properties><SpatialRefSys><ProjectionsEnabled type="int">1</ProjectionsEnabled></SpatialRefSys></properties>
<customproperties><property key="openmapstack.delivery" value="{html.escape(metadata, quote=True)}"/></customproperties>
<projectMetadata><title>Synthetic parcel selection</title><abstract>{html.escape(credits + " " + provenance)}</abstract><author>{html.escape(project["project"]["author"]["name"])}</author></projectMetadata>
<layer-tree-group name=""><layer-tree-group name="Analysis"><layer-tree-layer id="candidates" name="candidates" checked="Qt::Checked"/></layer-tree-group></layer-tree-group>
<projectlayers><maplayer type="vector" geometry="Polygon"><id>candidates</id><layername>candidates</layername>
<datasource>./data/derived/candidates.geojson</datasource><provider>ogr</provider><srs>{crs}</srs>
<renderer-v2 type="singleSymbol"><symbols><symbol name="0" type="fill"><layer class="SimpleFill"><Option type="Map"><Option name="color" value="42,117,166,255" type="QString"/></Option></layer></symbol></symbols></renderer-v2>
</maplayer></projectlayers></qgis>'''
            with zipfile.ZipFile(path, "w") as archive:
                info = zipfile.ZipInfo("project.qgs", date_time=(2026, 10, 7, 0, 0, 0))
                archive.writestr(info, xml)
        else:
            # Observable target demonstrates its exported HTML contract, not
            # an installed Observable adapter or publication to its service.
            rows = "".join(f'<tr><td>{html.escape(f["properties"]["id"])}</td><td>{f["properties"]["area_m2"]}</td></tr>' for f in chosen)
            path.write_text(f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Delivery profile example</title>
<body><h1>Synthetic parcel selection</h1><p>Minimum area: {minimum} m². This is a contract demonstration.</p>
<table><thead><tr><th>Parcel</th><th>Area (m²)</th></tr></thead><tbody>{rows}</tbody></table>
<a href="data/derived/areas.csv">Download CSV</a>
<section data-openmapstack-provenance><h2>Provenance and limitations</h2>
<p>Generated with AI using the free <a href="{html.escape(project["project"]["generated_with"]["url"], quote=True)}">OpenMapStack toolkit</a>.</p>
<p>Analysis made: {completed}</p><p>Author: <a href="mailto:{html.escape(project["project"]["author"]["email"], quote=True)}">{html.escape(project["project"]["author"]["name"])}</a></p>
<p>{html.escape(provenance)}</p></section>
<script id="openmapstack-view" type="application/json">{metadata}</script></body></html>''')
        if target.get("mode") == "hosted":
            build = ROOT / project["outputs"][target["build"]]["path"]
            build.parent.mkdir(parents=True, exist_ok=True)
            build.write_text(json.dumps({"surface": "observable-export-contract-fixture",
                "inputs": target["inputs"], "publication": "not_performed"}, sort_keys=True) + "\n")
    write_evidence(ROOT, project)

    checks = [{"id": "selected_area", "status": "passed" if all(f["properties"]["area_m2"] >= minimum for f in chosen) else "failed"}]
    for target in targets(project):
        result = evidence_matches(ROOT, target["id"])
        checks.append({"id": f"delivery_{target['id']}", "status": result.status, "reason": result.detail})
    # External runtime checks belong to verify; pipeline checks certify only
    # predicates actually executed here, never pretend QGIS/Chromium ran.
    project["validation"]["required"] = [c["id"] for c in checks]
    status = "failed" if any(c["status"] == "failed" for c in checks) else "passed"
    run_id = started.strftime("run-%Y%m%d-%H%M%S")
    inputs = declared_input_paths(ROOT, project)
    outputs = declared_output_paths(project)
    inputs_hash = canonical_file_set_hash(ROOT, inputs)
    outputs_hash = canonical_file_set_hash(ROOT, outputs)
    report = {"run_id": run_id, "status": status, "checks": checks,
              "inputs_hash": inputs_hash, "outputs_hash": outputs_hash}
    (ROOT / "validation").mkdir(exist_ok=True)
    (ROOT / "validation/latest-report.json").write_text(json.dumps(report, indent=2) + "\n")
    record = {"run_id": run_id, "status": status, "started_at": started.isoformat(),
              "completed_at": completed, "inputs_hash": inputs_hash, "outputs_hash": outputs_hash,
              "inputs": file_inventory(ROOT, inputs), "outputs": file_inventory(ROOT, outputs),
              "environment": {"python": platform.python_version()}}
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / f"runs/{run_id}.json").write_text(json.dumps(record, indent=2) + "\n")
    project["project"]["status"] = "failed" if status == "failed" else "validated"
    project["runs"]["latest"] = {"id": run_id, "status": status, "started_at": started.isoformat(),
        "completed_at": completed, "inputs_hash": inputs_hash, "outputs_hash": outputs_hash,
        "record": {"path": f"runs/{run_id}.json"},
        "validation_report": {"path": "validation/latest-report.json"}}
    (ROOT / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False))


if __name__ == "__main__":
    main()
