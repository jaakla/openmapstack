# =============================================================================
# OpenMapStack project pipeline template — deliberately boring, inspectable.
# -----------------------------------------------------------------------------
# This runs the deterministic pipeline exactly and reproducibly in a fresh
# environment (once it can reach the documented sources). The chat transcript
# is NOT part of the analytical dependency graph — this file + project.yaml
# are the source of truth.
#
# Fill per-step comments with source URL, retrieval timestamp, and rationale
# as you go (see project-spec.md section 4).
# =============================================================================

import datetime
import hashlib
import json
import logging
import platform
import shlex
from pathlib import Path

import duckdb
import yaml

from openmapstack.delivery import metadata_json, write_evidence

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "derived"
RUNS = ROOT / "runs"
VALIDATION = ROOT / "validation"
SOURCE = ROOT / "data" / "source"
OVERRIDES = ROOT / "data" / "overrides"

# A local projected CRS for all metric work. NEVER use EPSG:4326 for
# area/distance/buffer. Estonia default is EPSG:3301 (L-EST97).
ANALYSIS_CRS = "EPSG:3301"
STORAGE_CRS = "EPSG:4326"

log = logging.getLogger("pipeline")
logging.basicConfig(level=logging.INFO)


def load_parcels(con: duckdb.DuckDBPyConnection, source: dict) -> None:
    """STEP 1 — Load cadastral parcels.

    Source: {source['provider']} {source['dataset']}
    Retrieved: {source['access']['retrieved_at']}

    Rationale: authoritative cadastral geometry (see project.yaml sources).
    """
    # For WFS, use ogr2ogr/geopandas to pull the specific layer + bbox.
    log.info("loading parcels from %s", source["source_url"])


def apply_overrides(con: duckdb.DuckDBPyConnection, table: str) -> None:
    """STEP 3 — Apply project-specific overrides.

    Corrections live separately from source data (immutable source +
    override layer = effective input). Each override has provenance and
    rationale in project.yaml overrides.
    """
    ovpath = OVERRIDES / "parcels.geojson"
    if ovpath.exists():
        # Validate every target and asserted prior value before creating the
        # effective view; record applied/rejected status in the run report.
        con.execute(f"""
            CREATE OR REPLACE VIEW {table}_effective AS
            SELECT * FROM {table}
            WHERE fid NOT IN (
                SELECT feature_id FROM read_json_auto('{ovpath}', format='newline_delimited')
                WHERE action = 'hide_source_feature'
            )
        """)


TABLE_FORMATS = {"csv": "(FORMAT csv, HEADER true)", "xlsx": "(FORMAT xlsx, HEADER true)",
                 "parquet": "(FORMAT parquet)", "json": "(FORMAT json, ARRAY true)"}


def write_table(con: duckdb.DuckDBPyConnection, query: str, output: Path, formats: list[str]) -> list[Path]:
    """Write a non-spatial (kind: table) output and its download formats from one query.

    Every format comes from the same rows, so `tables.downloads_consistent`
    holds by construction (project-spec.md s.2.5). Values stay machine-readable:
    plain numbers and ISO dates; the view formats them for the reader. XLSX
    needs DuckDB's `excel` extension.
    """
    written = []
    for fmt in dict.fromkeys([output.suffix.lstrip("."), *formats]):
        if fmt == "xlsx":
            con.execute("INSTALL excel; LOAD excel")
        target = output.with_suffix("." + fmt)
        con.execute(f"COPY ({query}) TO '{target}' {TABLE_FORMATS[fmt]}")
        written.append(target)
    return written


def _file_set_hash(paths: list[Path]) -> str:
    """Canonical file-set hash (project-spec.md s.2.8): sorted paths, each
    length-prefixed, followed by the file bytes."""
    digest = hashlib.sha256()
    for relative in sorted({path.relative_to(ROOT).as_posix() for path in paths}):
        encoded = relative.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update((ROOT / relative).read_bytes())
    return "sha256:" + digest.hexdigest()


def _inventory(paths: list[Path]) -> list[dict]:
    return [
        {"path": relative, "sha256": "sha256:" + hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()}
        for relative in sorted({path.relative_to(ROOT).as_posix() for path in paths})
    ]


def _declared_inputs(project: dict) -> list[Path]:
    """Every input the validator requires in the run record (project-spec.md
    s.2.8): sources, overrides, the pipeline, project-local command files and
    declared runtime dependencies."""
    paths = {path for folder in (SOURCE, OVERRIDES) if folder.is_dir() for path in folder.rglob("*") if path.is_file()}
    paths.add(ROOT / "pipeline.py")
    implementation = (project.get("runtime") or {}).get("implementation") or {}
    command = implementation.get("command") or []
    tokens = shlex.split(command) if isinstance(command, str) else list(command)
    for entry in [implementation.get("pipeline"), *tokens, *(implementation.get("dependencies") or [])]:
        if not isinstance(entry, str) or entry.startswith("-"):
            continue
        target = (ROOT / entry).resolve()
        if not target.is_relative_to(ROOT):
            continue
        if target.is_file():
            paths.add(target)
        elif target.is_dir():
            paths.update(path for path in target.rglob("*") if path.is_file())
    return sorted(paths)


def finalize_run(report: dict, started_at: str) -> None:
    """STEP 7 — Write the report and run record, then point project.yaml at them.

    The pipeline owns `runs.latest` and `project.status`; never patch them by
    hand. A clean rerun (`openmapstack verify project.yaml --rerun`) writes a
    new timestamped record, so a hand-edited pointer names a record the rerun
    never wrote and fails.
    """
    project = yaml.safe_load((ROOT / "project.yaml").read_text())
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    inputs = _declared_inputs(project)
    outputs = []
    for output in (project.get("outputs") or {}).values():
        outputs.append(ROOT / output["path"])
        # A table's download formats are declared outputs too (project-spec.md s.2.5).
        outputs += [(ROOT / output["path"]).with_suffix("." + fmt) for fmt in output.get("downloads") or []]
    report["inputs_hash"] = _file_set_hash(inputs)
    report["outputs_hash"] = _file_set_hash(outputs)

    run_file = RUNS / f"{report['run_id']}.json"
    run_file.write_text(json.dumps({
        "run_id": report["run_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "status": report["status"],
        "inputs_hash": report["inputs_hash"],
        "outputs_hash": report["outputs_hash"],
        "validation_report": "validation/latest-report.json",
        # Record the versions that actually ran; add every tool the pipeline uses.
        "environment": {"python": platform.python_version(), "duckdb": duckdb.__version__},
        "inputs": _inventory(inputs),
        "outputs": _inventory(outputs),
    }, indent=2))
    (VALIDATION / "latest-report.json").write_text(json.dumps(report, indent=2, default=str))

    # Only an all-passed report may set `validated` (project-spec.md s.6).
    project.setdefault("project", {})["status"] = "validated" if report["status"] == "passed" else report["status"]
    project.setdefault("runs", {})["latest"] = {
        "id": report["run_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "status": report["status"],
        "inputs_hash": report["inputs_hash"],
        "outputs_hash": report["outputs_hash"],
        "record": {"path": run_file.relative_to(ROOT).as_posix()},
        "validation_report": {"path": "validation/latest-report.json"},
    }
    (ROOT / "project.yaml").write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True, width=100))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    RUNS.mkdir(parents=True, exist_ok=True)
    VALIDATION.mkdir(parents=True, exist_ok=True)
    started = datetime.datetime.now(datetime.timezone.utc)

    con = duckdb.connect()
    con.install_extension("spatial")
    con.load_extension("spatial")

    # STEP 2 — Reproject to L-EST97 before metric calcs.
    # (EPSG:4326 MUST NOT be used for metric work.)
    # parcels = st_transform(parcels_raw, '{ANALYSIS_CRS}')

    # STEP 3 — apply overrides.
    apply_overrides(con, "parcels_raw")

    # STEP 4 — filter, thresholds match project.yaml processing.steps.
    # candidate = con.query("SELECT * FROM parcels_effective WHERE area_m2 >= 2000")

    # STEP 5 — reproject to storage CRS and write derived output.
    # candidate = con.query(f"ST_Transform origin 'EPSG:3301'->'{STORAGE_CRS}'")
    # A non-spatial result goes out as a table plus its declared downloads:
    # write_table(con, "SELECT scenario, time_saved_h_year FROM summary ORDER BY scenario",
    #             OUT / "scenario-summary.csv", ["csv", "xlsx"])

    # STEP 6 — validation is a pipeline stage; write the report.
    # Every id below must match a name in project.yaml validation.required
    # or domain_checks verbatim (flat identifiers, no mappings).
    report = {
        # Run records are named run-<YYYYMMDD>-<HHMMSS> in UTC (project-spec.md s.1).
        "run_id": started.strftime("run-%Y%m%d-%H%M%S"),
        # A single not_testable or warning check makes the whole run "warning".
        # Never let "not tested" collect as an implicit pass (project-spec.md s.6).
        "status": "warning",
        "checks": [
            {"id": "geometry_valid", "status": "passed"},
            {"id": "crs_known", "status": "passed"},
            {"id": "row_count_gt_zero", "status": "passed"},
            {"id": "no_duplicate_cadastral_id", "status": "passed"},
            {"id": "no_null_cadastral_id", "status": "passed"},
            {"id": "source_semantics_verified", "status": "passed"},
            {"id": "source_result_complete", "status": "passed"},
            {"id": "overrides_applied", "status": "passed"},
            {"id": "manifest_graph_resolves", "status": "passed"},
            {"id": "view_controls_match_pipeline", "status": "passed"},
            {"id": "manifest_report_parity", "status": "passed"},
            {"id": "example_range_check", "status": "passed"},
        ],
    }
    # Generate only delivery.targets: default dashboard; external targets are
    # explicit. Embed metadata_json(ROOT, project, target) in each web view's
    # <script id="openmapstack-view" type="application/json">. QGIS uses the
    # custom property openmapstack.delivery. Expose source provenance/warnings
    # visibly too; metadata is evidence, not a substitute for the visible view.
    # Once the declared analytical outputs and views exist:
    # write_evidence(ROOT, project)
    # STEP 7 — the pipeline, not a hand edit, records the run in project.yaml.
    finalize_run(report, started.isoformat())

    log.info("pipeline complete -> %s", report["run_id"])


if __name__ == "__main__":
    main()
