"""GIS-correctness assertions that inspect real geodata via DuckDB Spatial.

See references/project-spec.md section 2.4 (processing) and 6 (validation).
No dependency on any one LLM; these run against whatever files the pipeline
(or agent) actually produced.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from . import AssertionResult, failed, get_in, not_testable, passed, project_root
from .spatial import connect_spatial


def _connect():
    """Load only a preinstalled Spatial extension; grading never downloads."""
    return connect_spatial()


def coordinate_pair_equals(
    workspace: Path, path: str, field: str, equals: list[float],
    tolerance: float = 1e-7, project_dir: str = ".",
) -> AssertionResult:
    """A JSON field contains the known point in the consuming interface's order.

    The oracle supplies an ordered pair; do not infer or normalize its order
    from a CRS label or from the submitted coordinates. This focused check
    needs no spatial runtime and does not prove reprojection or map rendering.
    """
    def finite_pair(value):
        try:
            return (isinstance(value, list) and len(value) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) for v in value))
        except OverflowError:
            return False

    if not finite_pair(equals) or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("equals must be two finite numbers and tolerance finite and non-negative")
    target = project_root(workspace, project_dir) / path
    if not target.is_file():
        return failed(f"{path} does not exist", code="file_missing")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        return failed(f"{path} is not valid JSON: {exc}", code="json_invalid")
    except OSError as exc:
        return not_testable(f"could not read {path}: {exc}", code="read_error")
    actual = get_in(payload, field)
    if not finite_pair(actual):
        return failed(f"{path}:{field} must contain two finite numeric coordinates",
                      code="coordinate_pair_invalid")
    if any(abs(a - e) > tolerance for a, e in zip(actual, equals)):
        return failed(f"{path}:{field} {actual} != expected ordered pair {equals}",
                      code="coordinate_pair_mismatch", actual=actual, expected=equals)
    return passed(f"{path}:{field} matches the expected ordered pair", actual=actual, expected=equals)


def _read(con, path: Path):
    escaped_path = path.as_posix().replace("'", "''")
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        # DuckDB Spatial's native GEOMETRY type round-trips through Parquet;
        # read_parquet keeps that typed column, unlike routing through GDAL.
        return f"read_parquet('{escaped_path}')"
    return f"ST_Read('{escaped_path}')"


# Preferred names, most conventional first. "geometry" is what GeoPandas and
# the GeoParquet spec write; "geom" is what PostGIS and this repository's own
# fixtures use. Both are common in the wild.
_GEOMETRY_NAMES = ("geom", "geometry", "geometry_col", "the_geom", "wkb_geometry", "shape")


def _geometry_column(con, rel: str, preferred: str | None = None) -> str | None:
    """Find the geometry column instead of assuming one is called ``geom``.

    Hardcoding a name is fine against fixtures a generator wrote and wrong
    against real data: the same check would report `not_testable` on most
    real-world GeoParquet, which names the column `geometry`. A check that
    cannot run on the data it is meant to inspect is not a check.
    """
    try:
        columns = con.execute(f"SELECT * FROM {rel} LIMIT 0").description or []
    except Exception:  # noqa: BLE001
        return preferred
    by_name = {str(name): str(type_name).upper() for name, type_name, *_ in columns}
    if preferred:
        return preferred if preferred in by_name else None
    typed = [name for name, type_name in by_name.items() if type_name == "GEOMETRY" or type_name.startswith("GEOMETRY(")]
    if len(typed) == 1:
        return typed[0]
    candidates = typed or list(by_name)
    for candidate in _GEOMETRY_NAMES:
        if candidate in candidates:
            return candidate
    return typed[0] if typed else preferred


def _geometry_expression(con, rel: str, preferred: str | None = None) -> str | None:
    """Resolve typed geometry or WKB without assigning an invented CRS.

    Spatial reads GeoParquet metadata into GEOMETRY (including CRS-qualified
    types). Bare WKB Parquet remains BLOB: it can be checked for validity, but
    ST_CRS on its decoded value correctly reports missing metadata.
    """
    column = _geometry_column(con, rel, preferred)
    if column is None:
        return None
    columns = con.execute(f"SELECT * FROM {rel} LIMIT 0").description or []
    types = {str(name): str(kind).upper() for name, kind, *_ in columns}
    identifier = '"' + column.replace('"', '""') + '"'
    if types.get(column) == "BLOB":
        return f"ST_GeomFromWKB({identifier})"
    if types.get(column, "").startswith("GEOMETRY"):
        return identifier
    return None


def _invalid_geometry_count(con, rel: str, preferred: str | None = None) -> int | None:
    expression = _geometry_expression(con, rel, preferred)
    if expression is None:
        return None
    # Missing geometries cannot silently count as valid via SQL NULL semantics.
    return con.execute(
        f"SELECT COUNT(*) FROM {rel} "
        f"WHERE {expression} IS NULL OR NOT ST_IsValid({expression})"
    ).fetchone()[0]


def row_count(workspace: Path, path: str, equals: int | None = None, at_least: int | None = None,
              at_most: int | None = None, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        count = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not read {path}: {exc}", code="read_error")

    if equals is not None and count != equals:
        return failed(f"{path} row count {count} != expected {equals}", code="row_count_equals")
    if at_least is not None and count < at_least:
        return failed(f"{path} row count {count} < minimum {at_least}", code="row_count_at_least")
    if at_most is not None and count > at_most:
        return failed(f"{path} row count {count} > maximum {at_most}", code="row_count_at_most")
    return passed(f"{path} row count {count} satisfies constraints", row_count=count)


def geometry_all_valid(workspace: Path, path: str, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        invalid = _invalid_geometry_count(con, rel)
        if invalid is None:
            return failed(f"{path} has no geometry column", code="geometry_column_missing")
        total = con.execute(f"SELECT COUNT(*) FROM {rel}").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not validate geometry in {path}: {exc}", code="read_error")
    invalid = invalid or 0
    if invalid:
        return failed(f"{path}: {invalid}/{total} features have invalid geometry", code="invalid_geometry")
    return passed(f"{path}: all {total} features have valid geometry")


def no_duplicate_ids(workspace: Path, path: str, id_field: str, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        dup = con.execute(
            f'SELECT COUNT(*) FROM (SELECT "{id_field}" FROM {rel} '
            f'GROUP BY "{id_field}" HAVING COUNT(*) > 1) t'
        ).fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not check duplicates in {path}: {exc}", code="read_error")
    if dup:
        return failed(f"{path}: {dup} duplicate value(s) for {id_field}", code="duplicate_ids")
    return passed(f"{path}: no duplicate {id_field} values")


def no_null_ids(workspace: Path, path: str, id_field: str, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        nulls = con.execute(f'SELECT COUNT(*) FROM {rel} WHERE "{id_field}" IS NULL').fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not check nulls in {path}: {exc}", code="read_error")
    if nulls:
        return failed(f"{path}: {nulls} null {id_field} values", code="null_ids")
    return passed(f"{path}: no null {id_field} values")


def feature_field_equals(
    workspace: Path, path: str, id_field: str, id: Any, field: str, equals: Any,
    project_dir: str = ".",
) -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        row = con.execute(
            f'SELECT "{field}" FROM {rel} WHERE "{id_field}" = ?', [id]
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not query {path}: {exc}", code="read_error")
    if row is None:
        return failed(f"{path}: no feature with {id_field} = {id!r}", code="feature_not_found")
    actual = row[0]
    if str(actual) != str(equals):
        return failed(
            f"{path}: feature {id!r} field {field} = {actual!r}, expected {equals!r}",
            code="field_value_mismatch",
        )
    return passed(f"{path}: feature {id!r} field {field} == {equals!r}")


def feature_present(workspace: Path, path: str, id_field: str, id: Any, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        row = con.execute(f'SELECT 1 FROM {rel} WHERE "{id_field}" = ? LIMIT 1', [id]).fetchone()
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not query {path}: {exc}", code="read_error")
    if row is None:
        return failed(f"{path}: feature {id_field}={id!r} not present (expected inclusion)", code="feature_missing")
    return passed(f"{path}: feature {id_field}={id!r} present")


def feature_absent(workspace: Path, path: str, id_field: str, id: Any, project_dir: str = ".") -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return not_testable(f"{path} does not exist, cannot confirm exclusion", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        row = con.execute(f'SELECT 1 FROM {rel} WHERE "{id_field}" = ? LIMIT 1', [id]).fetchone()
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not query {path}: {exc}", code="read_error")
    if row is not None:
        return failed(f"{path}: feature {id_field}={id!r} present but expected excluded", code="feature_present")
    return passed(f"{path}: feature {id_field}={id!r} correctly excluded")


def field_range(
    workspace: Path, path: str, field: str, min: float | None = None, max: float | None = None,
    project_dir: str = ".",
) -> AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        clauses = []
        if min is not None:
            clauses.append(f'"{field}" < {min}')
        if max is not None:
            clauses.append(f'"{field}" > {max}')
        where = " OR ".join(clauses) if clauses else "FALSE"
        out_of_range = con.execute(f"SELECT COUNT(*) FROM {rel} WHERE {where}").fetchone()[0]
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not check range in {path}: {exc}", code="read_error")
    if out_of_range:
        return failed(
            f"{path}: {out_of_range} feature(s) with {field} outside [{min}, {max}]",
            code="field_out_of_range",
        )
    return passed(f"{path}: all features have {field} within [{min}, {max}]")


def crs_not_used_for_metrics(workspace: Path, project_dir: str = ".",
                              forbidden_crs: tuple[str, ...] = ("EPSG:4326", "EPSG:3857")) -> AssertionResult:
    """Require a declared projected CRS for actual metric operations.

    Read, write, storage, and reprojection steps may legitimately mention a
    geographic CRS. Distance/area/buffer/length/nearest operations may not.
    """
    from . import get_in, load_project_yaml

    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    analysis_crs = get_in(proj, "processing.analysis_crs")
    if not isinstance(analysis_crs, str) or not analysis_crs.strip():
        return failed("processing.analysis_crs is required", code="analysis_crs_missing")
    steps = get_in(proj, "processing.steps", []) or []
    metric_tokens = ("area", "buffer", "distance", "length", "nearest", "proximity")
    metric_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and any(token in str(step.get("operation", "")).lower() for token in metric_tokens)
    ]
    if metric_steps and analysis_crs.upper() in {item.upper() for item in forbidden_crs}:
        return failed(
            f"processing.analysis_crs is {analysis_crs}, forbidden for metric operations",
            code="forbidden_analysis_crs",
        )
    forbidden = {item.upper() for item in forbidden_crs}
    bad_steps = [
        step.get("id")
        for step in metric_steps
        if isinstance(step.get("crs"), str) and step["crs"].upper() in forbidden
    ]
    if bad_steps:
        return failed(
            f"steps using forbidden CRS for metric operation: {bad_steps}",
            code="forbidden_step_crs",
        )
    return passed(
        f"analysis_crs {analysis_crs} is valid for {len(metric_steps)} metric operation(s); "
        "storage/load/reprojection steps were excluded"
    )


def dataset_crs_is(
    workspace: Path,
    path: str,
    expected: str,
    geometry_field: str | None = None,
    project_dir: str = ".",
) -> AssertionResult:
    """Read CRS metadata from the actual dataset rather than the manifest.

    ``geometry_field`` is resolved from the data when not given, so this works
    on real datasets whose geometry column is not called ``geom``.
    """
    target = project_root(workspace, project_dir) / path
    if not target.exists():
        return failed(f"{path} does not exist", code="file_missing")
    con = _connect()
    if con is None:
        return not_testable("duckdb spatial not available in this environment", code="duckdb_unavailable")
    try:
        rel = _read(con, target)
        expression = _geometry_expression(con, rel, geometry_field)
        if expression is None:
            return failed(f"{path} has no geometry column", code="geometry_column_missing")
        # The NULL row preserves a typed column's CRS even for an empty dataset.
        rows = con.execute(
            f"SELECT DISTINCT ST_CRS(geometry_value) FROM "
            f"(SELECT {expression} AS geometry_value FROM {rel} UNION ALL SELECT NULL)"
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return not_testable(f"could not inspect CRS metadata in {path}: {exc}", code="read_error")
    actual = sorted({str(row[0]).upper() for row in rows if row and row[0]})
    if not actual:
        return failed(f"{path} has no readable CRS metadata", code="dataset_crs_missing")
    if actual != [expected.upper()]:
        return failed(
            f"{path} CRS metadata {actual} != expected {expected.upper()}",
            code="dataset_crs_mismatch",
            actual=actual,
            expected=expected.upper(),
        )
    return passed(f"{path} actual CRS metadata is {expected.upper()}", actual=actual)


# GeoParquet always stores longitude/latitude and defaults WGS 84 to
# OGC:CRS84, so a declared EPSG:4326 is written as either identifier.
_EQUIVALENT_CRS = ({"EPSG:4326", "OGC:CRS84"},)


def dataset_crs_matches_storage_crs(
    workspace: Path,
    path: str,
    geometry_field: str | None = None,
    project_dir: str = ".",
) -> AssertionResult:
    """Require a dataset's real CRS metadata to match `processing.storage_crs`.

    Checks that the project writes what it declares, without prescribing
    which storage CRS it should choose.
    """
    from . import get_in, load_project_yaml

    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    declared = get_in(proj, "processing.storage_crs")
    if not isinstance(declared, str) or not declared.strip():
        return failed("processing.storage_crs is not declared", code="storage_crs_missing")
    declared = declared.strip().upper()
    for group in _EQUIVALENT_CRS:
        if declared in group:
            results = [dataset_crs_is(workspace, path, crs, geometry_field, project_dir) for crs in sorted(group)]
            match = next((result for result in results if result.status == "passed"), None)
            return match or results[0]
    return dataset_crs_is(workspace, path, declared, geometry_field, project_dir)
