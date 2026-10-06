"""Outcome-based live grading: formats may vary; parcel positions may not."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import yaml

from openmapstack.checks import geodata
from tests.test_evals import eval_runner


class OutputReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, output):
        (self.root / "project.yaml").write_text(yaml.safe_dump({"outputs": output}))

    def test_nested_evidence_and_rerun_paths_resolve_to_declared_output(self):
        for suffix in ("json", "geojson", "gpkg", "parquet"):
            with self.subTest(suffix=suffix):
                path = f"data/derived/chosen-name.{suffix}"
                self.manifest({"candidate_parcels": {"path": path}})
                args = {"evidence": [{"path": "$OUTPUT:candidate_parcels"}],
                        "paths": ["$OUTPUT:candidate_parcels"], "other": "literal"}
                resolved = eval_runner._resolve_output_references(args, self.root)
                self.assertEqual(resolved["paths"], [path])
                self.assertEqual(resolved["evidence"], [{"path": path}])
                self.assertEqual(args["paths"], ["$OUTPUT:candidate_parcels"])

    def test_missing_or_escaping_output_is_a_grading_failure(self):
        for outputs in ({}, {"candidate_parcels": {"path": "../outside.json"}},
                        {"candidate_parcels": {"path": "/tmp/outside.json"}}):
            with self.subTest(outputs=outputs):
                self.manifest(outputs)
                results, _ = eval_runner._evaluate_assertions(
                    {"case_type": "positive"}, self.root,
                    [{"assert": "project.exists", "args": {"path": "$OUTPUT:candidate_parcels"}}], {}, None)
                self.assertEqual(results[0]["actual_status"], "failed")
                self.assertEqual(results[0]["actual_code"], "output_reference_invalid")

    def test_declared_missing_file_does_not_fall_back_to_another_format(self):
        self.manifest({"candidate_parcels": {"path": "missing.gpkg"}})
        (self.root / "candidate-parcels.json").write_text("{}")
        results, _ = eval_runner._evaluate_assertions(
            {"case_type": "positive"}, self.root,
            [{"assert": "project.exists", "args": {"path": "$OUTPUT:candidate_parcels"}}], {}, None)
        self.assertEqual(results[0]["actual_code"], "file_missing")


class SourceGeometryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture = Path(__file__).resolve().parents[2] / "evals/fixtures/mini-tartu/parcels.geojson"
        (self.root / "source.geojson").write_bytes(fixture.read_bytes())
        self.con = geodata._connect()
        if self.con is None:
            self.skipTest("DuckDB Spatial unavailable")
        self.addCleanup(self.con.close)

    def candidate(self, suffix="json", always_xy=True, feature_id="P1"):
        sql = ("SELECT cadastral_id, ST_Transform(geom, 'EPSG:3301', 'EPSG:4326', "
               f"always_xy := {'true' if always_xy else 'false'}) AS geometry "
               f"FROM ST_Read('{self.root}/source.geojson') WHERE cadastral_id = 'P1'")
        path = self.root / f"candidate.{suffix}"
        if suffix == "parquet":
            self.con.execute(f"COPY ({sql}) TO '{path}' (FORMAT PARQUET)")
        elif suffix == "gpkg":
            self.con.execute(f"COPY ({sql}) TO '{path}' (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:4326')")
        else:
            geometry = json.loads(self.con.execute(f"SELECT ST_AsGeoJSON(geometry) FROM ({sql})").fetchone()[0])
            path.write_text(json.dumps({"type": "FeatureCollection", "features": [
                {"type": "Feature", "properties": {"cadastral_id": feature_id}, "geometry": geometry}]}))
        return path

    def check(self, path):
        return geodata.feature_geometries_match_source(
            self.root, path.name, "source.geojson", "cadastral_id", "EPSG:3301")

    def test_correct_reprojection_passes_across_spatial_formats(self):
        for suffix in ("json", "geojson", "gpkg", "parquet"):
            with self.subTest(suffix=suffix):
                result = self.check(self.candidate(suffix))
                self.assertEqual(result.status, "passed", result.detail)

    def test_valid_geometry_and_crs_label_do_not_hide_wrong_axis_order(self):
        path = self.candidate(always_xy=False)
        self.assertEqual(geodata.geometry_all_valid(self.root, path.name).status, "passed")
        result = self.check(path)
        self.assertEqual(result.status, "failed", result.detail)
        self.assertEqual(result.data["code"], "source_geometry_mismatch")
        self.assertEqual(result.data["feature_ids"], ["P1"])

    def test_unknown_parcel_id_fails_even_at_a_correct_location(self):
        result = self.check(self.candidate(feature_id="invented"))
        self.assertEqual(result.data["code"], "source_geometry_mismatch")

    def test_resized_polygon_fails_even_with_unchanged_centroid(self):
        path = self.candidate()
        payload = json.loads(path.read_text())
        ring = payload["features"][0]["geometry"]["coordinates"][0]
        cx = sum(p[0] for p in ring[:-1]) / 4
        cy = sum(p[1] for p in ring[:-1]) / 4
        payload["features"][0]["geometry"]["coordinates"][0] = [[cx + (x-cx)*2, cy + (y-cy)*2] for x,y in ring]
        path.write_text(json.dumps(payload))
        self.assertEqual(self.check(path).data["code"], "source_geometry_mismatch")
