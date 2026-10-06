from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from .helpers import make_workspace

from openmapstack.checks import qgis as qgis_assertions  # noqa: E402


def _write_qgz(path, datasources, extra_xml=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"<datasource>{ds}</datasource>" for ds in datasources)
    xml = f'<?xml version="1.0"?><qgis><projectlayers>{body}</projectlayers>{extra_xml}</qgis>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("project.qgs", xml)


class StaticValidTests(unittest.TestCase):
    def test_explicit_parent_relative_path_passes(self) -> None:
        workspace = make_workspace()
        (workspace / "project").mkdir()
        (workspace / "layer.geojson").write_text("{}", encoding="utf-8")
        _write_qgz(workspace / "project/project.qgz", ["../layer.geojson"])
        result = qgis_assertions.static_valid(workspace, project_dir="project")
        self.assertEqual(result.status, "passed", result.detail)

    def test_bare_relative_path_fails_even_when_python_can_resolve_it(self) -> None:
        workspace = make_workspace()
        (workspace / "data").mkdir()
        (workspace / "data/layer.geojson").write_text("{}", encoding="utf-8")
        _write_qgz(workspace / "project.qgz", ["data/layer.geojson"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "broken_datasource")
        self.assertIn("use ./", result.detail)

    def test_valid_relative_datasource_passes(self) -> None:
        workspace = make_workspace()
        (workspace / "data").mkdir()
        (workspace / "data" / "layer.geojson").write_text("{}", encoding="utf-8")
        _write_qgz(workspace / "project.qgz", ["./data/layer.geojson"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "passed")

    def test_absolute_path_fails_even_when_it_exists(self) -> None:
        workspace = make_workspace()
        layer = workspace / "data" / "layer.geojson"
        layer.parent.mkdir()
        layer.write_text("{}", encoding="utf-8")
        _write_qgz(workspace / "project.qgz", [str(layer.resolve()), "C:\\data\\layer.gpkg|layername=a"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "broken_datasource")
        self.assertEqual(len(result.data["errors"]), 2)
        self.assertIn("absolute path", result.detail)

    def test_missing_file_fails(self) -> None:
        workspace = make_workspace()
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "file_missing")

    def test_not_a_zip_fails(self) -> None:
        workspace = make_workspace()
        (workspace / "project.qgz").write_text("not a zip", encoding="utf-8")
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "not_a_zip")

    def test_no_qgs_document_fails(self) -> None:
        workspace = make_workspace()
        with zipfile.ZipFile(workspace / "project.qgz", "w") as zf:
            zf.writestr("readme.txt", "hello")
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "no_qgs_document")

    def test_no_layers_fails(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", [])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "no_layers")

    def test_broken_datasource_path_fails(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", ["./data/does-not-exist.geojson"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "broken_datasource")

    def test_geopackage_missing_layername_fails(self) -> None:
        workspace = make_workspace()
        (workspace / "data.gpkg").write_text("fake", encoding="utf-8")
        _write_qgz(workspace / "project.qgz", ["./data.gpkg"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "broken_datasource")
        self.assertIn("layername=", result.detail)

    def test_remote_wms_datasource_is_skipped(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", ["crs=EPSG:3301&url=https://example.invalid/wms"])
        result = qgis_assertions.static_valid(workspace)
        self.assertEqual(result.status, "passed")


class DatasourcesPortableTests(unittest.TestCase):
    """A live 001 trial pointed QGIS at GeoParquet; a stock Ubuntu QGIS 3.40
    (GDAL 3.12, no Parquet driver) opened the layer invalid and drew nothing."""

    def test_common_vector_formats_and_remote_tiles_pass(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", [
            "./data/derived/a.gpkg|layername=a", "./data/derived/b.geojson", "./data/derived/c.fgb",
            "type=xyz&amp;url=https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        ])
        result = qgis_assertions.datasources_portable(workspace)
        self.assertEqual(result.status, "passed", result.detail)

    def test_parquet_and_arrow_layers_warn_with_the_missing_driver(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", [
            "./data/derived/candidates.parquet", "./data/derived/zones.arrow|layername=zones",
        ])
        result = qgis_assertions.datasources_portable(workspace)
        self.assertEqual(result.status, "warning", result.detail)
        self.assertEqual(result.data["code"], "datasource_format_not_portable")
        self.assertEqual(
            result.data["datasources"],
            {"./data/derived/candidates.parquet": "Parquet", "./data/derived/zones.arrow|layername=zones": "Arrow"},
        )

    def test_formats_outside_the_allowlist_warn_not_only_known_ones(self) -> None:
        # A denylist passed any optional-driver format it did not name.
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", ["./data/legacy.mdb|layername=parcels", "./data/export"])
        result = qgis_assertions.datasources_portable(workspace)
        self.assertEqual(result.status, "warning", result.detail)
        self.assertEqual(
            result.data["datasources"],
            {
                "./data/legacy.mdb|layername=parcels": ".mdb is outside the portable set",
                "./data/export": "no extension is outside the portable set",
            },
        )

    def test_provider_connection_strings_are_out_of_scope(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", [
            "dbname='gis' host=localhost table=\"public\".\"parcels\" (geom)",
            "./data/derived/a.shp", "./data/raster/dem.tif",
        ])
        result = qgis_assertions.datasources_portable(workspace)
        self.assertEqual(result.status, "passed", result.detail)

    def test_missing_project_fails(self) -> None:
        result = qgis_assertions.datasources_portable(make_workspace())
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["code"], "file_missing")


def _write_layer_qgz(path, layers):
    body = "".join(
        f"<maplayer><layername>{name}</layername><datasource>{source}</datasource>"
        f"<srs><spatialrefsys><authid>{authid}</authid></spatialrefsys></srs></maplayer>"
        for name, source, authid in layers
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("project.qgs", f'<?xml version="1.0"?><qgis><projectlayers>{body}</projectlayers></qgis>')


def _write_geojson(path, crs=None):
    import json

    payload = {"type": "FeatureCollection", "features": []}
    if crs:
        payload["crs"] = {"type": "name", "properties": {"name": crs}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_gpkg(path, table, epsg):
    import sqlite3

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE gpkg_spatial_ref_sys (srs_id INTEGER, organization TEXT, organization_coordsys_id INTEGER)")
        connection.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, srs_id INTEGER)")
        connection.execute("INSERT INTO gpkg_spatial_ref_sys VALUES (?, 'EPSG', ?)", (epsg, epsg))
        connection.execute("INSERT INTO gpkg_geometry_columns VALUES (?, ?)", (table, epsg))
    connection.close()


class LayerCrsMatchesDataTests(unittest.TestCase):
    """A live 001 trial declared EPSG:3301 on WGS84 GeoJSON; QGIS drew the
    roads a few metres from the Estonian grid's origin."""

    def test_geojson_without_crs_member_is_wgs84(self) -> None:
        workspace = make_workspace()
        _write_geojson(workspace / "data" / "roads.geojson")
        _write_layer_qgz(workspace / "project.qgz", [("Roads", "./data/roads.geojson", "EPSG:3301")])
        result = qgis_assertions.layer_crs_matches_data(workspace)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "layer_crs_mismatch")
        self.assertEqual(result.data["layers"], {"Roads": {"declared": "EPSG:3301", "data": "EPSG:4326"}})

    def test_matching_geojson_and_geopackage_layers_pass(self) -> None:
        workspace = make_workspace()
        _write_geojson(workspace / "data" / "roads.geojson", crs="urn:ogc:def:crs:EPSG::3301")
        _write_geojson(workspace / "data" / "pois.geojson", crs="urn:ogc:def:crs:OGC:1.3:CRS84")
        _write_gpkg(workspace / "data" / "parcels.gpkg", "parcels", 3301)
        _write_layer_qgz(workspace / "project.qgz", [
            ("Roads", "./data/roads.geojson", "EPSG:3301"),
            ("POIs", "./data/pois.geojson", "EPSG:4326"),
            ("Parcels", "./data/parcels.gpkg|layername=parcels", "EPSG:3301"),
            ("Basemap", "type=xyz&amp;url=https://tile.openstreetmap.org/{z}/{x}/{y}.png", "EPSG:3857"),
        ])
        result = qgis_assertions.layer_crs_matches_data(workspace)
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(len(result.data["layers"]), 3)

    def test_geopackage_mismatch_fails(self) -> None:
        workspace = make_workspace()
        _write_gpkg(workspace / "data" / "parcels.gpkg", "parcels", 3301)
        _write_layer_qgz(workspace / "project.qgz", [("Parcels", "./data/parcels.gpkg|layername=parcels", "EPSG:4326")])
        result = qgis_assertions.layer_crs_matches_data(workspace)
        self.assertEqual(result.status, "failed", result.detail)

    def test_nothing_comparable_is_not_testable(self) -> None:
        workspace = make_workspace()
        _write_layer_qgz(workspace / "project.qgz", [("Missing", "./data/missing.geojson", "EPSG:4326")])
        result = qgis_assertions.layer_crs_matches_data(workspace)
        self.assertEqual(result.status, "not_testable", result.detail)


class RuntimeLoadUnavailableTests(unittest.TestCase):
    def test_missing_pyqgis_is_not_testable(self) -> None:
        workspace = make_workspace()
        _write_qgz(workspace / "project.qgz", ["./missing.geojson"])
        with patch.dict("sys.modules", {"qgis": None, "qgis.core": None}):
            result = qgis_assertions.runtime_load(workspace)
        self.assertEqual(result.status, "not_testable")
        self.assertEqual(result.data.get("code"), "pyqgis_unavailable")


def _pyqgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(
    _pyqgis_available(),
    "PyQGIS is not importable in this interpreter; see evals/README.md for "
    "the micromamba-based QGIS environment used to exercise this path manually",
)
class RuntimeLoadWithRealPyqgisTests(unittest.TestCase):
    """Only runs when PyQGIS is importable in the active interpreter (e.g. a
    `micromamba run -n qgis python -m unittest ...` invocation). Never runs
    in default fixture CI, which has no PyQGIS dependency.

    Uses the committed examples/tartu-development worked example rather
    than a hand-rolled minimal .qgs: PyQGIS requires the full real
    <maplayers> project structure QGIS itself writes to actually resolve
    layers, not the minimal <datasource>-only XML the static regex check
    in this module accepts.
    """

    WORKED_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "tartu-development"

    def make_native_project(self):
        from qgis.core import Qgis, QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer
        import json

        workspace = make_workspace()
        (workspace / "data").mkdir()
        (workspace / "data/layer.geojson").write_text(json.dumps({
            "type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                "geometry": {"type": "Point", "coordinates": [26.73, 58.31]}}]}))
        qgis_assertions._qgis_application()
        project = QgsProject.instance()
        project.clear()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:3301"))
        project.setFileName(str(workspace / "project.qgz"))
        project.setFilePathStorage(Qgis.FilePathType.Relative)
        layer = QgsVectorLayer(str(workspace / "data/layer.geojson"), "POI", "ogr")
        self.assertTrue(layer.isValid())
        project.addMapLayer(layer)
        self.assertTrue(project.write())
        project.clear()
        return workspace

    def test_native_project_loads_after_relocation_from_another_cwd(self):
        import shutil
        workspace = self.make_native_project()
        relocated = make_workspace() / "relocated"
        shutil.copytree(workspace, relocated)
        self.assertEqual(qgis_assertions.static_valid(relocated).status, "passed")
        result = qgis_assertions.runtime_load(relocated)
        self.assertEqual(result.status, "passed", result.detail)
        self.assertEqual(len(result.data["layers"]), 1)

    def test_missing_project_crs_is_not_a_runtime_pass(self):
        import xml.etree.ElementTree as ET
        workspace = self.make_native_project()
        with zipfile.ZipFile(workspace / "project.qgz") as archive:
            name = next(name for name in archive.namelist() if name.endswith(".qgs"))
            document = ET.fromstring(archive.read(name))
        document.remove(document.find("projectCrs"))
        with zipfile.ZipFile(workspace / "project.qgz", "w") as archive:
            archive.writestr(name, ET.tostring(document))
        result = qgis_assertions.runtime_load(workspace)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "project_crs_invalid")

    def test_real_worked_example_loads_and_reports_layers(self) -> None:
        if not (self.WORKED_EXAMPLE / "project.qgz").is_file():
            self.skipTest("examples/tartu-development/project.qgz is a regenerable artifact; run run_e2e.py first")
        result = qgis_assertions.runtime_load(self.WORKED_EXAMPLE)
        self.assertEqual(result.status, "passed", result.detail)
        self.assertGreater(len(result.data.get("layers", [])), 0)

    def test_real_worked_example_every_declared_layer_renders(self) -> None:
        if not (self.WORKED_EXAMPLE / "project.qgz").is_file():
            self.skipTest("examples/tartu-development/project.qgz is a regenerable artifact; run run_e2e.py first")
        result = qgis_assertions.every_declared_layer_renders(self.WORKED_EXAMPLE)
        self.assertEqual(result.status, "passed", result.detail)
        fractions = result.data.get("render_diff_fraction", {})
        self.assertGreater(len(fractions), 0)
        self.assertTrue(all(fraction > 0 for fraction in fractions.values()), fractions)

    def test_broken_datasource_reports_invalid_layers(self) -> None:
        workspace = make_workspace()
        real_qgz = self.WORKED_EXAMPLE / "project.qgz"
        if not real_qgz.is_file():
            self.skipTest("examples/tartu-development/project.qgz is a regenerable artifact; run run_e2e.py first")
        with zipfile.ZipFile(real_qgz) as source_zip:
            qgs_xml = source_zip.read("project.qgs").decode("utf-8")
        # Break every real datasource path so PyQGIS reports invalid layers,
        # without needing to hand-author a full project structure.
        broken_xml = qgs_xml.replace("./data/", "./nonexistent-data/")
        with zipfile.ZipFile(workspace / "project.qgz", "w") as zf:
            zf.writestr("project.qgs", broken_xml)
        result = qgis_assertions.runtime_load(workspace)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data.get("code"), "invalid_layers")


if __name__ == "__main__":
    unittest.main()
