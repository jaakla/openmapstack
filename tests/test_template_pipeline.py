"""The shipped pipeline template must own the manifest's run pointer.

At candidate 861b58b a live material trial patched `runs.latest` into
project.yaml by hand after each run, because the template wrote no run
record and never touched the manifest. The clean rerun then wrote a new
timestamped record and the rebuilt manifest pointed at one that did not
exist. These tests load the template from a project workspace and call its
`finalize_run` directly, so they need neither DuckDB Spatial nor a network.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import shutil
import unittest
from pathlib import Path

import yaml

from openmapstack.checks import validation as validation_checks
from openmapstack.integrity import canonical_file_set_hash, declared_output_paths
from openmapstack.validation import validate_project
from tests.evals.helpers import make_workspace, minimal_project, write_project

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "pipeline.py"
RUN_ID = "run-20260925-120000"


def _workspace(**runtime_overrides) -> Path:
    workspace = make_workspace().resolve()
    project = minimal_project()
    project["runtime"]["implementation"].update(runtime_overrides)
    write_project(workspace, project)
    (workspace / "data" / "source").mkdir(parents=True)
    (workspace / "data" / "source" / "parcels.geojson").write_text('{"type": "FeatureCollection", "features": []}')
    # The template's analytical steps are placeholders; stand in for their output.
    (workspace / "data" / "derived").mkdir(parents=True)
    (workspace / "data" / "derived" / "final.json").write_text('{"type": "FeatureCollection", "features": []}')
    for folder in ("runs", "validation"):
        (workspace / folder).mkdir()
    shutil.copy(TEMPLATE, workspace / "pipeline.py")
    return workspace


def _finalize(workspace: Path, status: str = "warning") -> dict:
    spec = importlib.util.spec_from_file_location(f"template_pipeline_{id(workspace)}", workspace / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = {"run_id": RUN_ID, "status": status, "checks": [{"id": "geometry_valid", "status": status}]}
    module.finalize_run(report, datetime.datetime(2026, 9, 25, 12, tzinfo=datetime.timezone.utc).isoformat())
    return yaml.safe_load((workspace / "project.yaml").read_text())


class TemplatePipelineOwnsRunPointerTests(unittest.TestCase):
    def test_manifest_points_at_the_record_the_run_wrote(self) -> None:
        workspace = _workspace()
        latest = _finalize(workspace)["runs"]["latest"]
        record_path = workspace / latest["record"]["path"]
        self.assertTrue(record_path.is_file(), latest)
        self.assertEqual(record_path.name, f"{RUN_ID}.json")
        record = json.loads(record_path.read_text())
        report = json.loads((workspace / "validation" / "latest-report.json").read_text())
        self.assertEqual((latest["id"], record["run_id"], report["run_id"]), (RUN_ID, RUN_ID, RUN_ID))

    def test_recorded_hashes_are_the_canonical_file_set_hashes(self) -> None:
        workspace = _workspace()
        latest = _finalize(workspace)["runs"]["latest"]
        inputs = ["data/source/parcels.geojson", "pipeline.py"]
        self.assertEqual(latest["inputs_hash"], canonical_file_set_hash(workspace, inputs))
        self.assertEqual(latest["outputs_hash"], canonical_file_set_hash(workspace, ["data/derived/final.json"]))

    def test_a_warning_run_does_not_leave_the_project_validated(self) -> None:
        project = _finalize(_workspace())
        self.assertEqual((project["runs"]["latest"]["status"], project["project"]["status"]), ("warning", "warning"))

    def test_the_record_passes_the_validators_run_record_check(self) -> None:
        # The run record needs an `environment` and every declared input,
        # including runtime dependencies, or `openmapstack validate` fails it.
        workspace = _workspace(dependencies=["lib"])
        (workspace / "lib").mkdir()
        (workspace / "lib" / "helpers.py").write_text("VALUE = 1\n")
        _finalize(workspace)
        record = json.loads((workspace / "runs" / f"{RUN_ID}.json").read_text())
        self.assertIn("python", record["environment"])
        self.assertIn("lib/helpers.py", {item["path"] for item in record["inputs"]})
        checks = {check.id: check for check in validate_project(workspace).checks}
        self.assertEqual(checks["runs.latest"].status, "passed", checks["runs.latest"].message)


class TableDownloadInventoryTests(unittest.TestCase):
    """A table's downloads are declared outputs (project-spec.md s.2.5).

    The template hashes them in the run record. The validators must require
    them there too, or a record that leaves one out still passes while that
    published file goes unhashed and unprotected from sample runs.
    """

    def _workspace(self) -> Path:
        workspace = _workspace()
        project = yaml.safe_load((workspace / "project.yaml").read_text())
        project["outputs"]["summary"] = {
            "path": "data/derived/summary.csv", "format": "CSV", "kind": "table", "generated_by": "export",
            "table": {"columns": [{"name": "scenario", "type": "string"}]}, "downloads": ["csv", "json"],
        }
        write_project(workspace, project)
        (workspace / "data" / "derived" / "summary.csv").write_text("scenario\nwalk\n")
        (workspace / "data" / "derived" / "summary.json").write_text('[{"scenario": "walk"}]')
        return workspace

    def test_the_template_record_covers_every_download(self) -> None:
        workspace = self._workspace()
        project = _finalize(workspace)
        self.assertEqual(
            declared_output_paths(project),
            ["data/derived/final.json", "data/derived/summary.csv", "data/derived/summary.json"],
        )
        record = json.loads((workspace / "runs" / f"{RUN_ID}.json").read_text())
        self.assertIn("data/derived/summary.json", {item["path"] for item in record["outputs"]})
        checks = {check.id: check for check in validate_project(workspace).checks}
        self.assertEqual(checks["runs.latest"].status, "passed", checks["runs.latest"].message)
        self.assertEqual(validation_checks.run_record_matches(workspace).status, "passed")

    def test_a_record_that_omits_a_download_fails(self) -> None:
        workspace = self._workspace()
        _finalize(workspace)
        # Drop the JSON download and re-hash consistently everywhere, so the
        # omission is the only defect left.
        record_path = workspace / "runs" / f"{RUN_ID}.json"
        record = json.loads(record_path.read_text())
        record["outputs"] = [item for item in record["outputs"] if item["path"] != "data/derived/summary.json"]
        outputs_hash = canonical_file_set_hash(workspace, [item["path"] for item in record["outputs"]])
        record["outputs_hash"] = outputs_hash
        record_path.write_text(json.dumps(record))
        report_path = workspace / "validation" / "latest-report.json"
        report = json.loads(report_path.read_text())
        report["outputs_hash"] = outputs_hash
        report_path.write_text(json.dumps(report))
        project = yaml.safe_load((workspace / "project.yaml").read_text())
        project["runs"]["latest"]["outputs_hash"] = outputs_hash
        write_project(workspace, project)

        checks = {check.id: check for check in validate_project(workspace).checks}
        self.assertEqual(checks["runs.latest"].status, "failed")
        self.assertIn("data/derived/summary.json", checks["runs.latest"].message)
        result = validation_checks.run_record_matches(workspace)
        self.assertEqual((result.status, result.data["code"]), ("failed", "hash_inventory_incomplete"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
