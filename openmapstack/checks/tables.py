"""Checks for non-spatial tabular outputs (``outputs.<name>.kind: table``).

Analyses produce results that are not geodata: scenario comparisons, cost
tables, per-site rankings. They are first-class outputs (project-spec.md
section 2.5): declared with their columns, readable by a machine, and offered
for download in the formats the manifest names. These checks read the files
the pipeline wrote; they never trust the manifest's description of them.

Readers: CSV and JSON records use the standard library, so they always run.
XLSX and Parquet need DuckDB (the ``[geo]`` extra; XLSX also its ``excel``
extension) and report ``not_testable`` when it is unavailable, never a pass.
"""

from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Sequence

from . import AssertionResult, failed, not_testable, passed, project_root

TABLE_SUFFIXES = {".csv": "csv", ".xlsx": "xlsx", ".json": "json", ".parquet": "parquet"}
DOWNLOAD_FORMATS = tuple(TABLE_SUFFIXES.values())
COLUMN_TYPES = ("string", "integer", "number", "boolean", "date", "datetime")


class ReaderUnavailable(Exception):
    """The environment cannot read this format; the caller reports not_testable."""


def _duckdb_rows(sql: str, path: Path) -> tuple[list[str], list[tuple]]:
    try:
        import duckdb  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise ReaderUnavailable("duckdb is not installed (install openmapstack[geo])") from exc
    con = duckdb.connect()
    try:
        if "read_xlsx" in sql:
            try:
                con.execute("LOAD excel")
            except Exception:
                try:
                    con.execute("INSTALL excel; LOAD excel")
                except Exception as exc:
                    raise ReaderUnavailable(f"DuckDB excel extension is unavailable: {exc}") from exc
        relation = con.execute(sql, [str(path)])
        columns = [d[0] for d in relation.description]
        return columns, [tuple(row) for row in relation.fetchall()]
    finally:
        con.close()


def read_table(path: Path) -> tuple[list[str], list[tuple]]:
    """Return (column names, rows) for a CSV, XLSX, JSON-records or Parquet table.

    JSON tables are an array of objects, or an object whose ``rows`` member is
    one; column order follows the first record. Raises ``ReaderUnavailable``
    when the format needs a reader this environment lacks, and ``ValueError``
    for a file that is not a table.
    """
    kind = TABLE_SUFFIXES.get(path.suffix.lower())
    if kind == "csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                header = next(reader)
            except StopIteration:
                raise ValueError("CSV has no header row") from None
            return header, [tuple(row) for row in reader]
    if kind == "json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("rows") if isinstance(payload, dict) else payload
        if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
            raise ValueError("JSON table must be an array of objects (or an object with a `rows` array)")
        columns: list[str] = list(records[0].keys()) if records else []
        for record in records[1:]:
            columns.extend(key for key in record if key not in columns)
        return columns, [tuple(record.get(c) for c in columns) for record in records]
    if kind == "xlsx":
        return _duckdb_rows("SELECT * FROM read_xlsx(?, header = true, all_varchar = false)", path)
    if kind == "parquet":
        return _duckdb_rows("SELECT * FROM read_parquet(?)", path)
    raise ValueError(f"{path.suffix or 'extensionless'} is not a supported table format ({', '.join(TABLE_SUFFIXES)})")


def _load(workspace: Path, path: str, project_dir: str) -> tuple[Path, list[str], list[tuple]] | AssertionResult:
    target = project_root(workspace, project_dir) / path
    if not target.is_file():
        return failed(f"{path} does not exist", code="table_missing", path=path)
    try:
        columns, rows = read_table(target)
    except ReaderUnavailable as exc:
        return not_testable(f"cannot read {path}: {exc}", code="table_reader_unavailable", path=path)
    except (ValueError, UnicodeError, json.JSONDecodeError, csv.Error, OSError) as exc:
        return failed(f"{path} is not a readable table: {exc}", code="table_unreadable", path=path)
    except Exception as exc:  # DuckDB raises its own error types for corrupt files
        return failed(f"{path} is not a readable table: {exc}", code="table_unreadable", path=path)
    return target, columns, rows


def readable(workspace: Path, path: str, project_dir: str = ".") -> AssertionResult:
    """The declared table output is a readable table with a header row."""
    loaded = _load(workspace, path, project_dir)
    if isinstance(loaded, AssertionResult):
        return loaded
    _, columns, rows = loaded
    if not columns:
        return failed(f"{path} has no columns", code="table_no_columns", path=path)
    duplicates = sorted({c for c in columns if columns.count(c) > 1})
    if duplicates:
        return failed(f"{path} repeats column names {duplicates}", code="table_duplicate_columns", path=path)
    return passed(f"{path}: {len(rows)} rows x {len(columns)} columns", rows=len(rows), columns=len(columns), path=path)


def not_empty(workspace: Path, path: str, project_dir: str = ".") -> AssertionResult:
    """The declared table output has at least one data row."""
    loaded = _load(workspace, path, project_dir)
    if isinstance(loaded, AssertionResult):
        return loaded
    _, _, rows = loaded
    if not rows:
        return failed(f"{path} has a header but no rows", code="table_empty", path=path)
    return passed(f"{path} has {len(rows)} rows", rows=len(rows), path=path)


_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_INTEGER = re.compile(r"^[+-]?\d+$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "") or (isinstance(value, float) and math.isnan(value))


def _conforms(value: Any, kind: str) -> bool:
    """Machine-readable value check. Locale formatting ("1 234,5") is a view
    concern; a table output carries plain numbers and ISO dates."""
    if _blank(value):
        return True
    if kind == "string":
        return True
    if kind == "boolean":
        return isinstance(value, bool) or str(value).strip().lower() in {"true", "false", "0", "1"}
    if kind == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return value.is_integer()
        return bool(_INTEGER.match(str(value).strip()))
    if kind == "number":
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        return bool(_NUMBER.match(str(value).strip()))
    text = value.isoformat() if hasattr(value, "isoformat") else str(value).strip()
    if kind == "date":
        return bool(_DATE.match(text[:10])) and (len(text) == 10 or bool(_DATETIME.match(text)))
    if kind == "datetime":
        return bool(_DATETIME.match(text)) or bool(_DATE.match(text))
    return True


def columns_declared(workspace: Path, path: str, columns: Sequence[Any], project_dir: str = ".") -> AssertionResult:
    """Every declared column exists in the table and its values match the declared type."""
    loaded = _load(workspace, path, project_dir)
    if isinstance(loaded, AssertionResult):
        return loaded
    _, actual, rows = loaded
    declared: list[dict[str, Any]] = [c if isinstance(c, dict) else {"name": c} for c in columns or []]
    if not declared:
        return failed(f"{path}: no columns are declared for this table output", code="table_columns_undeclared", path=path)
    missing = [c.get("name") for c in declared if c.get("name") not in actual]
    if missing:
        return failed(f"{path} lacks declared columns {missing}", code="table_column_missing", path=path, missing=missing)
    mismatches: dict[str, list[Any]] = {}
    for column in declared:
        kind = column.get("type")
        if not kind:
            continue
        if kind not in COLUMN_TYPES:
            return failed(f"{path}: column {column['name']!r} declares unknown type {kind!r}", code="table_type_unknown", path=path)
        index = actual.index(column["name"])
        bad = [row[index] for row in rows if index < len(row) and not _conforms(row[index], kind)]
        if bad:
            mismatches[column["name"]] = bad[:3]
    if mismatches:
        return failed(
            f"{path}: values do not match declared types {mismatches} (write plain numbers and ISO dates; format them only in the view)",
            code="table_type_mismatch", path=path, mismatches={k: [str(v) for v in vals] for k, vals in mismatches.items()},
        )
    extra = [c for c in actual if c not in {d.get("name") for d in declared}]
    return passed(f"{path}: {len(declared)} declared columns present and typed", path=path, undeclared_columns=extra)


def key_unique(workspace: Path, path: str, key: Sequence[str] | str, project_dir: str = ".") -> AssertionResult:
    """The declared key columns identify each row: no blank and no repeated key."""
    loaded = _load(workspace, path, project_dir)
    if isinstance(loaded, AssertionResult):
        return loaded
    _, columns, rows = loaded
    keys = [key] if isinstance(key, str) else list(key or [])
    if not keys:
        return failed(f"{path}: empty key declaration", code="table_key_undeclared", path=path)
    missing = [k for k in keys if k not in columns]
    if missing:
        return failed(f"{path}: key columns {missing} are not in the table", code="table_column_missing", path=path, missing=missing)
    indexes = [columns.index(k) for k in keys]
    seen: set[tuple] = set()
    blanks, duplicates = 0, []
    for row in rows:
        value = tuple(row[i] if i < len(row) else None for i in indexes)
        if any(_blank(v) for v in value):
            blanks += 1
            continue
        normalised = tuple(str(v) for v in value)
        if normalised in seen:
            duplicates.append(list(normalised))
        seen.add(normalised)
    if blanks:
        return failed(f"{path}: {blanks} rows have a blank key {keys}", code="table_key_null", path=path, blank_rows=blanks)
    if duplicates:
        return failed(f"{path}: repeated key values {duplicates[:3]}", code="table_key_duplicate", path=path, duplicates=len(duplicates))
    return passed(f"{path}: key {keys} is unique across {len(rows)} rows", path=path)


def download_paths(path: str, formats: Sequence[str]) -> dict[str, str]:
    """Download file for each declared format: the output path with that format's suffix."""
    base = Path(path)
    suffix_of = {fmt: suffix for suffix, fmt in TABLE_SUFFIXES.items()}
    return {fmt: base.with_suffix(suffix_of[fmt]).as_posix() for fmt in formats if fmt in suffix_of}


def _same(a: Any, b: Any) -> bool:
    if _blank(a) and _blank(b):
        return True
    try:
        fa, fb = float(a), float(b)
        if math.isnan(fa) or math.isnan(fb):
            return math.isnan(fa) and math.isnan(fb)
        return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        pass
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).strip().lower() in {"true", "1"} and str(b).strip().lower() in {"true", "1"} or \
            str(a).strip().lower() in {"false", "0"} and str(b).strip().lower() in {"false", "0"}
    ta = a.isoformat() if hasattr(a, "isoformat") else str(a)
    tb = b.isoformat() if hasattr(b, "isoformat") else str(b)
    return ta.strip() == tb.strip()


def downloads_consistent(workspace: Path, path: str, formats: Sequence[str], project_dir: str = ".") -> AssertionResult:
    """Each declared download format exists next to the output and holds the same table."""
    unknown = [f for f in formats or [] if f not in DOWNLOAD_FORMATS]
    if unknown:
        return failed(f"{path}: unknown download formats {unknown}; use {list(DOWNLOAD_FORMATS)}", code="download_format_unknown", path=path)
    reference = _load(workspace, path, project_dir)
    if isinstance(reference, AssertionResult):
        return reference
    _, columns, rows = reference
    root = project_root(workspace, project_dir)
    problems: list[str] = []
    unavailable: list[str] = []
    for fmt, relative in download_paths(path, formats).items():
        target = root / relative
        if not target.is_file():
            problems.append(f"{relative} is missing")
            continue
        try:
            other_columns, other_rows = read_table(target)
        except ReaderUnavailable as exc:
            unavailable.append(f"{relative}: {exc}")
            continue
        except Exception as exc:
            problems.append(f"{relative} is unreadable: {exc}")
            continue
        if other_columns != columns:
            problems.append(f"{relative} columns {other_columns} differ from {columns}")
            continue
        if len(other_rows) != len(rows):
            problems.append(f"{relative} has {len(other_rows)} rows, {path} has {len(rows)}")
            continue
        for number, (left, right) in enumerate(zip(rows, other_rows), start=1):
            differing = [columns[i] for i, (a, b) in enumerate(zip(left, right)) if not _same(a, b)]
            if differing:
                problems.append(f"{relative} row {number} differs in {differing}")
                break
    if problems:
        code = "download_missing" if all(p.endswith("is missing") for p in problems) else "download_mismatch"
        return failed(f"{path}: " + "; ".join(problems), code=code, path=path, problems=problems)
    if unavailable:
        return not_testable(f"{path}: " + "; ".join(unavailable), code="table_reader_unavailable", path=path)
    return passed(f"{path}: {len(download_paths(path, formats))} download formats hold the same {len(rows)} rows", path=path)
