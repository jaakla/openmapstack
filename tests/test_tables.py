"""Non-spatial table outputs (`outputs.<name>.kind: table`).

A material trial (Tartu bridge comparison) produced its main answers as
scenario and cost tables. `verify` read every declared `.json` output as
GeoJSON and failed its geometry, and leaving the tables undeclared raised
`outputs.undeclared_derived_files`. These tests pin the contract that
replaced that: tables are declared with columns and downloads, read back as
tables, never as geodata, and linked from the delivered view.
"""

from __future__ import annotations

import csv
import json
import shutil
import unittest
from pathlib import Path

from openmapstack.checks import presentation as presentation_checks
from openmapstack.checks import tables
from openmapstack.schema import project_schema_errors as validation_errors
from openmapstack.validation import validate_project
from openmapstack.verify import verify_project
from tests.evals.helpers import make_workspace, minimal_project, write_project

COLUMNS = [
    {"name": "scenario", "type": "string"},
    {"name": "time_saved_h_year", "type": "number", "unit": "h/year"},
    {"name": "daily_crossings", "type": "integer"},
]
ROWS = [("tuglase", 12891.5, 643), ("marja", 11807.0, 917)]


def _duckdb_excel() -> bool:
    try:
        import duckdb

        con = duckdb.connect()
        try:
            con.execute("LOAD excel")
        except Exception:
            con.execute("INSTALL excel; LOAD excel")
        return True
    except Exception:
        return False


HAS_EXCEL = _duckdb_excel()


def _write_csv(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _write_json(path: Path, header, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([dict(zip(header, row)) for row in rows]))


def _write_xlsx(path: Path, header, rows) -> None:
    import duckdb

    con = duckdb.connect()
    con.execute("LOAD excel")
    values = ", ".join("(" + ", ".join(repr(v) for v in row) + ")" for row in rows)
    con.execute(f"COPY (SELECT * FROM (VALUES {values}) t({', '.join(header)})) TO '{path}' (FORMAT xlsx, HEADER true)")


def _table_project(**output_overrides) -> dict:
    project = minimal_project()
    output = {
        "path": "data/derived/summary.csv",
        "format": "CSV",
        "kind": "table",
        "generated_by": "export",
        "table": {"key": ["scenario"], "columns": COLUMNS},
        "downloads": ["csv", "json"],
    }
    output.update(output_overrides)
    project["outputs"] = {"summary": output}
    return project


class ReadTableTests(unittest.TestCase):
    def test_csv_and_json_records_read_with_the_standard_library(self) -> None:
        workspace = make_workspace()
        header = [c["name"] for c in COLUMNS]
        _write_csv(workspace / "t.csv", header, ROWS)
        _write_json(workspace / "t.json", header, ROWS)
        for name in ("t.csv", "t.json"):
            with self.subTest(name=name):
                columns, rows = tables.read_table(workspace / name)
                self.assertEqual(columns, header)
                self.assertEqual(len(rows), 2)

    @unittest.skipUnless(HAS_EXCEL, "DuckDB excel extension unavailable")
    def test_xlsx_reads_through_duckdb(self) -> None:
        workspace = make_workspace()
        header = [c["name"] for c in COLUMNS]
        _write_xlsx(workspace / "t.xlsx", header, ROWS)
        columns, rows = tables.read_table(workspace / "t.xlsx")
        self.assertEqual(columns, header)
        self.assertEqual(rows[0][0], "tuglase")

    def test_a_non_table_json_is_rejected(self) -> None:
        workspace = make_workspace()
        (workspace / "fc.json").write_text('{"type": "FeatureCollection", "features": []}')
        with self.assertRaises(ValueError):
            tables.read_table(workspace / "fc.json")


class TableCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = make_workspace()
        self.header = [c["name"] for c in COLUMNS]
        _write_csv(self.workspace / "data/derived/summary.csv", self.header, ROWS)

    def test_readable_and_not_empty_pass_on_a_real_table(self) -> None:
        self.assertEqual(tables.readable(self.workspace, path="data/derived/summary.csv").status, "passed")
        self.assertEqual(tables.not_empty(self.workspace, path="data/derived/summary.csv").status, "passed")

    def test_missing_and_header_only_tables_fail(self) -> None:
        missing = tables.readable(self.workspace, path="data/derived/absent.csv")
        self.assertEqual((missing.status, missing.data["code"]), ("failed", "table_missing"))
        _write_csv(self.workspace / "data/derived/empty.csv", self.header, [])
        empty = tables.not_empty(self.workspace, path="data/derived/empty.csv")
        self.assertEqual((empty.status, empty.data["code"]), ("failed", "table_empty"))

    def test_repeated_column_names_fail(self) -> None:
        _write_csv(self.workspace / "data/derived/dup.csv", ["a", "a"], [(1, 2)])
        result = tables.readable(self.workspace, path="data/derived/dup.csv")
        self.assertEqual(result.data["code"], "table_duplicate_columns")

    def test_declared_columns_and_types_hold(self) -> None:
        result = tables.columns_declared(self.workspace, path="data/derived/summary.csv", columns=COLUMNS)
        self.assertEqual(result.status, "passed", result.detail)

    def test_a_missing_declared_column_fails(self) -> None:
        result = tables.columns_declared(
            self.workspace, path="data/derived/summary.csv", columns=COLUMNS + [{"name": "cost_eur", "type": "number"}]
        )
        self.assertEqual((result.status, result.data["code"]), ("failed", "table_column_missing"))

    def test_locale_formatted_numbers_fail_the_type(self) -> None:
        _write_csv(self.workspace / "data/derived/fmt.csv", self.header, [("tuglase", "12 891,5", 643)])
        result = tables.columns_declared(self.workspace, path="data/derived/fmt.csv", columns=COLUMNS)
        self.assertEqual((result.status, result.data["code"]), ("failed", "table_type_mismatch"))

    def test_undeclared_columns_fail(self) -> None:
        result = tables.columns_declared(self.workspace, path="data/derived/summary.csv", columns=[])
        self.assertEqual(result.data["code"], "table_columns_undeclared")

    def test_key_must_be_unique_and_present(self) -> None:
        self.assertEqual(tables.key_unique(self.workspace, path="data/derived/summary.csv", key=["scenario"]).status, "passed")
        _write_csv(self.workspace / "data/derived/dupkey.csv", self.header, ROWS + [("marja", 1, 1)])
        dup = tables.key_unique(self.workspace, path="data/derived/dupkey.csv", key="scenario")
        self.assertEqual(dup.data["code"], "table_key_duplicate")
        _write_csv(self.workspace / "data/derived/nullkey.csv", self.header, [("", 1, 1)])
        null = tables.key_unique(self.workspace, path="data/derived/nullkey.csv", key="scenario")
        self.assertEqual(null.data["code"], "table_key_null")

    def test_downloads_hold_the_same_table(self) -> None:
        _write_json(self.workspace / "data/derived/summary.json", self.header, ROWS)
        result = tables.downloads_consistent(self.workspace, path="data/derived/summary.csv", formats=["csv", "json"])
        self.assertEqual(result.status, "passed", result.detail)

    def test_a_missing_download_fails(self) -> None:
        result = tables.downloads_consistent(self.workspace, path="data/derived/summary.csv", formats=["csv", "json"])
        self.assertEqual((result.status, result.data["code"]), ("failed", "download_missing"))

    def test_a_download_with_other_values_fails(self) -> None:
        _write_json(self.workspace / "data/derived/summary.json", self.header, [("tuglase", 1.0, 643), ROWS[1]])
        result = tables.downloads_consistent(self.workspace, path="data/derived/summary.csv", formats=["json"])
        self.assertEqual((result.status, result.data["code"]), ("failed", "download_mismatch"))

    @unittest.skipUnless(HAS_EXCEL, "DuckDB excel extension unavailable")
    def test_an_xlsx_download_matches_its_csv(self) -> None:
        _write_xlsx(self.workspace / "data/derived/summary.xlsx", self.header, ROWS)
        result = tables.downloads_consistent(self.workspace, path="data/derived/summary.csv", formats=["xlsx"])
        self.assertEqual(result.status, "passed", result.detail)


class VerifyPlanTests(unittest.TestCase):
    def _workspace(self, with_json: bool = True) -> Path:
        workspace = make_workspace()
        header = [c["name"] for c in COLUMNS]
        _write_csv(workspace / "data/derived/summary.csv", header, ROWS)
        if with_json:
            _write_json(workspace / "data/derived/summary.json", header, ROWS)
        write_project(workspace, _table_project())
        return workspace

    def test_a_table_output_is_read_as_a_table_not_as_geodata(self) -> None:
        result = verify_project(self._workspace() / "project.yaml")
        names = {run.name: run.result.status for run in result.checks if run.args.get("path") == "data/derived/summary.csv"}
        self.assertNotIn("geodata.geometry_all_valid", names)
        for name in ("tables.readable", "tables.not_empty", "tables.columns_declared", "tables.key_unique", "tables.downloads_consistent"):
            self.assertEqual(names.get(name), "passed", (name, names))

    def test_a_missing_download_fails_verify(self) -> None:
        result = verify_project(self._workspace(with_json=False) / "project.yaml")
        runs = [run for run in result.checks if run.name == "tables.downloads_consistent"]
        self.assertEqual(runs[0].result.status, "failed")

    def test_an_undeclared_kind_keeps_the_old_geodata_plan(self) -> None:
        workspace = self._workspace()
        project = _table_project()
        del project["outputs"]["summary"]["kind"]
        del project["outputs"]["summary"]["downloads"]
        write_project(workspace, project)
        result = verify_project(workspace / "project.yaml")
        self.assertTrue(any(run.name == "geodata.geometry_all_valid" for run in result.checks))
        self.assertFalse(any(run.name.startswith("tables.") for run in result.checks))


class ValidateDeclarationTests(unittest.TestCase):
    def _status(self, result, check_id: str) -> str | None:
        return next((c.status for c in result.checks if c.id == check_id), None)

    def test_downloads_count_as_declared_outputs(self) -> None:
        workspace = make_workspace()
        header = [c["name"] for c in COLUMNS]
        _write_csv(workspace / "data/derived/summary.csv", header, ROWS)
        _write_json(workspace / "data/derived/summary.json", header, ROWS)
        write_project(workspace, _table_project())
        result = validate_project(workspace / "project.yaml")
        self.assertEqual(self._status(result, "outputs.undeclared_derived_files"), "passed")
        self.assertEqual(self._status(result, "outputs.files"), "passed")
        self.assertEqual(self._status(result, "outputs.declaration"), "passed")

    def test_a_missing_download_file_fails_outputs_files(self) -> None:
        workspace = make_workspace()
        _write_csv(workspace / "data/derived/summary.csv", [c["name"] for c in COLUMNS], ROWS)
        write_project(workspace, _table_project())
        result = validate_project(workspace / "project.yaml")
        self.assertEqual(self._status(result, "outputs.files"), "failed")

    def test_a_table_without_columns_fails_its_declaration(self) -> None:
        workspace = make_workspace()
        write_project(workspace, _table_project(table={"key": ["scenario"]}))
        result = validate_project(workspace / "project.yaml", artifacts=False)
        self.assertEqual(self._status(result, "outputs.declaration"), "failed")

    def test_the_schema_rejects_an_unknown_kind_and_download_format(self) -> None:
        def output_errors(project: dict) -> list[str]:
            return [e for e in validation_errors(project) if "outputs" in e]

        for override in ({"kind": "spreadsheet"}, {"downloads": ["docx"]}):
            with self.subTest(override=override):
                self.assertTrue(output_errors(_table_project(**override)))
        self.assertEqual(output_errors(_table_project()), [])


class PresentationTableTests(unittest.TestCase):
    def _workspace(self, html: str | None, tables_view=None) -> Path:
        workspace = make_workspace()
        project = _table_project()
        project["presentation"]["tables"] = tables_view if tables_view is not None else [{"output": "summary", "downloads": ["csv", "json"]}]
        project["outputs"]["dashboard"] = {"path": "dashboard.html", "format": "HTML", "kind": "document", "generated_by": "export"}
        write_project(workspace, project)
        if html is not None:
            (workspace / "dashboard.html").write_text(html)
        return workspace

    def test_the_dashboard_links_every_download(self) -> None:
        html = '<a href="data/derived/summary.csv">CSV</a> <a href="./data/derived/summary.json">JSON</a>'
        workspace = self._workspace(html)
        self.assertEqual(presentation_checks.table_downloads_linked(workspace).status, "passed")
        self.assertEqual(presentation_checks.tables_reference_table_outputs(workspace).status, "passed")

    def test_an_unlinked_download_fails(self) -> None:
        workspace = self._workspace('<a href="data/derived/summary.csv">CSV</a>')
        result = presentation_checks.table_downloads_linked(workspace)
        self.assertEqual((result.status, result.data["code"]), ("failed", "table_download_unlinked"))

    def test_no_dashboard_is_not_testable(self) -> None:
        self.assertEqual(presentation_checks.table_downloads_linked(self._workspace(None)).status, "not_testable")

    def test_a_table_view_of_a_geodata_output_fails(self) -> None:
        workspace = self._workspace("", tables_view=[{"output": "dashboard"}])
        result = presentation_checks.tables_reference_table_outputs(workspace)
        self.assertEqual((result.status, result.data["code"]), ("failed", "table_view_drift"))

    def test_view_switches_open_at_an_option_and_show_declared_columns(self) -> None:
        workspace = make_workspace()
        project = _table_project()
        project["presentation"]["controls"] = {"views": [{
            "id": "mode", "options": ["walk", "bike"], "canonical": "walk", "output": "summary",
            "fields": {"walk": ["time_saved_h_year"], "bike": ["daily_crossings"]}}]}
        write_project(workspace, project)
        self.assertEqual(presentation_checks.controls_match_pipeline(workspace).status, "passed")
        project["presentation"]["controls"]["views"][0]["canonical"] = "car"
        project["presentation"]["controls"]["views"][0]["fields"]["bike"] = ["bike_time_saved"]
        write_project(workspace, project)
        result = presentation_checks.controls_match_pipeline(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(len(result.data["errors"]), 2)



@unittest.skipUnless(HAS_EXCEL, "DuckDB excel extension unavailable")
class TemplateWriteTableTests(unittest.TestCase):
    """The template's `write_table` produces downloads that verify accepts."""

    def test_every_format_comes_from_the_same_rows(self) -> None:
        import importlib.util

        import duckdb

        template = Path(__file__).resolve().parents[1] / "templates" / "pipeline.py"
        spec = importlib.util.spec_from_file_location("template_pipeline_tables", template)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        workspace = make_workspace()
        (workspace / "data/derived").mkdir(parents=True)
        con = duckdb.connect()
        query = "SELECT * FROM (VALUES ('tuglase', 12891.5, 643), ('marja', 11807.0, 917)) t(scenario, time_saved_h_year, daily_crossings)"
        written = module.write_table(con, query, workspace / "data/derived/summary.csv", ["csv", "xlsx", "json"])
        self.assertEqual(sorted(p.suffix for p in written), [".csv", ".json", ".xlsx"])
        result = tables.downloads_consistent(workspace, path="data/derived/summary.csv", formats=["csv", "xlsx", "json"])
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(tables.columns_declared(workspace, path="data/derived/summary.csv", columns=COLUMNS).status, "passed")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
