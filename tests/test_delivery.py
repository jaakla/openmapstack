"""Delivery selection must change obligations without weakening analytical QA."""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from openmapstack import delivery
from openmapstack.checks import delivery as checks
from openmapstack.checks import presentation, qgis
from openmapstack.cli import main
from openmapstack.rerun import perform_clean_rerun
from openmapstack.schema import project_schema_errors
from openmapstack.validation import validate_project
from openmapstack.verify import verify_project

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/delivery-profiles"
spec = importlib.util.spec_from_file_location("delivery_example_create", EXAMPLE / "create.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="oms-delivery-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def build(self, kinds=None, *, hosted=False):
        path = example.create(self.root, kinds, hosted=hosted)
        result = subprocess.run([sys.executable, str(self.root / "pipeline.py")],
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.project = yaml.safe_load(path.read_text())
        return path

    def save(self):
        (self.root / "project.yaml").write_text(yaml.safe_dump(self.project, sort_keys=False))

    def test_default_is_explicit_dashboard_and_has_no_qgis_obligation(self):
        path = self.build()
        self.assertEqual(self.project["delivery"], delivery.default_delivery(["candidates", "areas"]))
        self.assertTrue((self.root / "dashboard.html").is_file())
        self.assertFalse((self.root / "project.qgz").exists())
        validation = validate_project(path)
        self.assertEqual(validation.status, "passed", validation.to_dict())
        # An incidental stale archive must not activate an unselected integration.
        (self.root / "project.qgz").write_text("unselected stale archive")
        result = verify_project(path)
        self.assertFalse(any("qgis" in c.name for c in result.checks))
        self.assertEqual(result.counts["failed"], 0, result.to_dict())

    def test_external_only_and_combined_views_have_current_evidence(self):
        for kinds in (["qgis"], ["observable"], ["dashboard", "qgis", "observable"]):
            with self.subTest(kinds=kinds), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = example.create(root, kinds)
                subprocess.run([sys.executable, str(root / "pipeline.py")], check=True, capture_output=True)
                self.assertEqual(validate_project(path).status, "passed")
                self.assertEqual((root / "dashboard.html").exists(), "dashboard" in kinds)
                semantics = []
                for kind in kinds:
                    self.assertEqual(checks.evidence_matches(root, kind).status, "passed")
                    semantics.append(json.loads((root / f"delivery/{kind}-evidence.json").read_text())["metadata"]["semantics"])
                self.assertTrue(all(s == semantics[0] for s in semantics))
                plan = verify_project(path)
                self.assertEqual(plan.counts["failed"], 0, plan.to_dict())
                self.assertEqual(any("qgis" in c.name for c in plan.checks), "qgis" in kinds)
                if "dashboard" not in kinds:
                    self.assertFalse(any("dashboard" in c.name for c in plan.checks))

    def test_unknown_targets_malformed_selection_and_unsafe_bindings_fail(self):
        self.build()
        healthy = copy.deepcopy(self.project)
        mutations = [
            lambda p: p.update(delivery=None),
            lambda p: p["delivery"].update(schema="openmapstack-delivery/v99"),
            lambda p: p["delivery"].update(targets=[]),
            lambda p: p["delivery"].update(targets="dashboard"),
            lambda p: p["delivery"]["targets"][0].update(kind="snowflake"),
            lambda p: p["delivery"]["targets"][0].update(mode="unknown"),
            lambda p: p["delivery"]["targets"][0].update(output="unknown"),
            lambda p: p["delivery"]["targets"][0].update(output={"unexpected": True}),
            lambda p: p["delivery"]["targets"][0].pop("id"),
            lambda p: p["delivery"]["targets"][0].update(inputs=["dashboard"]),
            lambda p: p["delivery"]["targets"][0].update(evidence="dashboard"),
            lambda p: p["outputs"]["dashboard"].update(path="../escape.html"),
            lambda p: p["outputs"]["dashboard"].update(path="dashboard.pdf"),
            lambda p: p["outputs"].update(dashboard=[]),
            lambda p: p["outputs"]["areas"].update(path="../escape.csv"),
            lambda p: p["outputs"]["areas"].update(path="delivery/dashboard-evidence.json"),
            lambda p: p["delivery"]["targets"].append(copy.deepcopy(p["delivery"]["targets"][0])),
            lambda p: p["delivery"]["targets"][0].update(mode="hosted", url="https://example.invalid"),
        ]
        self.assertEqual(checks.declaration_valid(self.root).status, "passed")
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                self.project = copy.deepcopy(healthy)
                mutate(self.project)
                self.save()
                result = checks.declaration_valid(self.root)
                self.assertEqual(result.status, "failed", result.to_dict())
                self.assertEqual(result.data["code"], "delivery_declaration_invalid")
                self.assertEqual(validate_project(self.root / "project.yaml").status, "failed")
                self.assertEqual(verify_project(self.root / "project.yaml").status, "failed")

    def test_missing_selected_artifact_fails_and_remains_in_plan(self):
        for kind in ("dashboard", "qgis", "observable"):
            with self.subTest(kind=kind):
                path = self.build([kind])
                output = self.root / self.project["outputs"][kind]["path"]
                output.unlink()
                check = checks.evidence_matches(self.root, kind)
                self.assertEqual(check.data["code"], "delivery_artifact_missing")
                self.assertEqual(validate_project(path).status, "failed")
                plan = verify_project(path)
                matches = [c for c in plan.checks if c.name == f"delivery.{kind}.evidence_matches"]
                self.assertEqual(matches[0].result.status, "failed")
                if kind == "qgis":
                    self.assertTrue(any(c.name.endswith("qgis.static_valid") for c in plan.checks))

    def test_receipt_does_not_certify_itself_or_stale_input_or_semantics(self):
        self.build()
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").status, "passed")
        original = (self.root / "dashboard.html").read_text()
        (self.root / "dashboard.html").write_text(original.replace('"target": "dashboard"', '"target": "other"'))
        delivery.write_evidence(self.root, self.project)
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_evidence_mismatch")
        (self.root / "dashboard.html").write_text(original)
        delivery.write_evidence(self.root, self.project)
        self.project["interpretation"]["objective"] = "A changed interpretation"
        self.save()
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_evidence_mismatch")

    def test_modified_analytical_data_is_detected(self):
        self.build()
        (self.root / "data/derived/areas.csv").write_text("id,area_m2\nlarge,1\n")
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_evidence_mismatch")

    def test_changed_metric_units_are_stale_even_with_unchanged_data_bytes(self):
        self.build()
        self.project["outputs"]["areas"]["table"]["columns"][1]["unit"] = "km²"
        self.save()
        delivery.write_evidence(self.root, self.project)
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_evidence_mismatch")

    def test_broken_evidence_missing_receipt_and_unknown_target_are_failures(self):
        self.build()
        receipt = self.root / "delivery/dashboard-evidence.json"
        receipt.write_text("not JSON")
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_evidence_invalid")
        receipt.unlink()
        self.assertEqual(checks.evidence_matches(self.root, "dashboard").data["code"], "delivery_artifact_missing")
        self.assertEqual(checks.evidence_matches(self.root, "other").data["code"], "delivery_target_unknown")

    def test_runtime_unavailable_does_not_become_passed(self):
        path = self.build(["observable"])
        with patch("openmapstack.checks.visual._playwright", side_effect=ImportError):
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "not_testable")
            plan = verify_project(path)
        self.assertEqual(plan.status, "warning")
        self.assertGreater(plan.coverage["not_testable"], 0)

    def test_hosted_target_needs_export_config_and_retrieval_evidence(self):
        self.build(["observable"], hosted=True)
        self.assertEqual(checks.evidence_matches(self.root, "observable").status, "passed")
        target = self.project["delivery"]["targets"][0]
        healthy = copy.deepcopy(target)
        for patch_values in ({"url": "https:///no-host"}, {"retrieved_at": "2026-99-99T00:00:00Z"}):
            target.update(patch_values)
            self.save()
            self.assertEqual(checks.declaration_valid(self.root).status, "failed")
            target.update(healthy)
        self.project["delivery"]["targets"][0]["retrieved_at"] = "2026-10-08T00:00:00Z"
        self.save()
        self.assertEqual(checks.evidence_matches(self.root, "observable").data["code"], "delivery_evidence_mismatch")
        (self.root / "delivery/observable-build.json").unlink()
        self.assertEqual(checks.evidence_matches(self.root, "observable").data["code"], "delivery_artifact_missing")

    def test_browser_check_rejects_missing_provenance_and_hidden_warnings(self):
        self.build(["observable"])
        self.project["warnings"] = [{"statement": "A known limitation"}]
        self.save()
        context = MagicMock()
        browser = context.return_value.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        body, surface = MagicMock(), MagicMock()
        page.locator.side_effect = lambda selector: body if selector == "body" else surface
        body.inner_text.return_value = "Report with a known limitation"
        surface.count.return_value = 1
        surface.is_visible.return_value = True
        surface.inner_text.return_value = self.project["sources"]["parcels"]["provider"]
        with patch("openmapstack.checks.visual._playwright", return_value=context):
            # Warning comparison must reflect the declared statement exactly.
            result = checks.web_view_loads(self.root, "observable")
            self.assertEqual(result.data["code"], "delivery_view_unhealthy")
            body.inner_text.return_value = "Report. A known limitation"
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "passed")
            surface.count.return_value = 0
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "failed")
            surface.count.return_value = 1
            surface.is_visible.return_value = False
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "failed")
            surface.is_visible.return_value = True
            surface.inner_text.return_value = "A different provider"
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "failed")
            context.return_value.__enter__.return_value.chromium.launch.side_effect = RuntimeError("missing system library")
            self.assertEqual(checks.web_view_loads(self.root, "observable").status, "not_testable")

    def test_eval_source_fixture_matches_committed_example(self):
        fixture = EXAMPLE.parents[1] / "evals/fixtures/delivery/parcels.geojson"
        self.assertEqual(fixture.read_bytes(), (EXAMPLE / "data/source/parcels.geojson").read_bytes())

    def test_selection_cannot_disable_core_provenance_or_crs_checks(self):
        path = self.build(["observable"])
        self.project["sources"]["parcels"]["pin"]["sha256"] = "sha256:" + "0" * 64
        self.project["processing"]["analysis_crs"] = "EPSG:4326"
        self.save()
        plan = verify_project(path)
        states = {c.name: c.result.status for c in plan.checks}
        self.assertEqual(states["provenance.every_source_pinned"], "failed")
        self.assertEqual(states["geodata.crs_not_used_for_metrics"], "failed")

    def test_static_map_lineage_does_not_require_qgis(self):
        self.build(["qgis"])
        self.assertEqual(presentation.layers_reference_outputs(self.root).status, "passed")
        self.project["presentation"]["map"]["layers"][0]["source"] = "parcels"
        self.save()
        result = presentation.layers_reference_outputs(self.root)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["code"], "presentation_source_unresolved")

    def test_declared_layer_and_table_must_be_bound_to_target_inputs(self):
        self.build(["qgis"])
        self.project["delivery"]["targets"][0]["inputs"] = ["areas"]
        self.save()
        self.assertEqual(checks.declaration_valid(self.root).status, "failed")
        self.project["delivery"]["targets"][0]["inputs"] = ["candidates"]
        self.save()
        self.assertEqual(checks.declaration_valid(self.root).status, "failed")

    def test_inspection_records_explicit_and_legacy_selection(self):
        path = self.build()
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(["inspect", str(path), "--json"]), 0)
        self.assertEqual(json.loads(output.getvalue())["delivery"]["mode"], "explicit")
        self.project.pop("delivery")
        self.save()
        self.assertEqual(delivery.inspection(self.project)["mode"], "legacy")

    def test_legacy_map_retains_missing_qgis_warning(self):
        path = self.build(["qgis"])
        self.project.pop("delivery")
        self.project["presentation"].update(primary_view="map", layout={"type": "map"}, provenance_ui={"show_assumptions": True})
        self.project["presentation"]["map"]["basemap"] = {"id": "osm", "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], "attribution": "OSM"}
        self.save()
        (self.root / "project.qgz").unlink()
        self.assertFalse(project_schema_errors(self.project))
        result = validate_project(path)
        self.assertTrue(any(c.id == "qgis.project" and c.status == "warning" for c in result.checks))

    def test_clean_rerun_rebuilds_selected_deliverables_and_rejects_selection_downgrade(self):
        self.build(["dashboard", "observable"])
        with tempfile.TemporaryDirectory() as directory:
            result = perform_clean_rerun(self.root, Path(directory), 30)
            self.assertEqual(result["status"], "passed", result)
            self.assertTrue((Path(directory) / "observable.html").is_file())
            self.assertFalse((Path(directory) / "project.qgz").exists())
        script = self.root / "pipeline.py"
        script.write_text(script.read_text().replace('project["project"]["status"] =', 'project.pop("delivery", None)\n    project["project"]["status"] ='))
        with tempfile.TemporaryDirectory() as directory:
            result = perform_clean_rerun(self.root, Path(directory), 30)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "delivery_integrity")

    def test_renamed_qgis_artifact_binding_is_used_by_checks(self):
        path = self.build(["qgis"])
        self.project["outputs"]["qgis"]["path"] = "desktop.qgz"
        (self.root / "project.qgz").rename(self.root / "desktop.qgz")
        self.save()
        delivery.write_evidence(self.root, self.project)
        self.assertEqual(checks.evidence_matches(self.root, "qgis").status, "passed")
        plan = verify_project(path)
        static = next(c for c in plan.checks if c.name.endswith("qgis.static_valid"))
        self.assertEqual(static.args["path"], "desktop.qgz")
        self.assertEqual(static.result.status, "passed")

    def test_nested_qgis_artifact_resolves_datasources_relative_to_archive(self):
        self.build(["qgis"])
        (self.root / "desktop").mkdir()
        self.project["outputs"]["qgis"]["path"] = "desktop/project.qgz"
        with zipfile.ZipFile(self.root / "project.qgz") as archive:
            xml = archive.read("project.qgs").decode().replace("./data/", "../data/")
        with zipfile.ZipFile(self.root / "desktop/project.qgz", "w") as archive:
            archive.writestr("project.qgs", xml)
        self.save()
        delivery.write_evidence(self.root, self.project)
        self.assertEqual(qgis.static_valid(self.root, path="desktop/project.qgz").status, "passed")
        self.assertEqual(qgis.layer_crs_matches_data(self.root, path="desktop/project.qgz").status, "passed")
        self.assertEqual(checks.evidence_matches(self.root, "qgis").status, "passed")

    @unittest.skipUnless(importlib.util.find_spec("qgis"), "native QGIS integration requires PyQGIS")
    def test_native_qgis_metadata_and_single_layer_visibility(self):
        from qgis.core import QgsExpressionContextUtils, QgsProject

        self.build(["qgis"])
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.root)
        result = qgis.every_declared_layer_renders(self.root)
        self.assertEqual(result.status, "passed", result.detail)
        qgis._qgis_application()
        project = QgsProject.instance()
        project.clear()
        self.assertTrue(project.read(str(self.root / "project.qgz")))
        self.assertIn(self.project["sources"]["parcels"]["provider"], project.metadata().abstract())
        self.assertIn(self.project["interpretation"]["assumptions"][0]["statement"], project.metadata().abstract())
        target = self.project["delivery"]["targets"][0]
        QgsExpressionContextUtils.setProjectVariable(project, "openmapstack_delivery",
                                                     delivery.metadata_json(self.root, self.project, target))
        self.assertTrue(project.write())
        project.clear()
        delivery.write_evidence(self.root, self.project)
        result = checks.evidence_matches(self.root, "qgis")
        self.assertEqual(result.status, "passed", result.detail)
        self.assertTrue(project.read(str(self.root / "project.qgz")))
        next(iter(project.mapLayers().values())).setOpacity(0)
        self.assertTrue(project.write())
        project.clear()
        result = qgis.every_declared_layer_renders(self.root)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "declared_layer_not_visible")


if __name__ == "__main__":
    unittest.main()
