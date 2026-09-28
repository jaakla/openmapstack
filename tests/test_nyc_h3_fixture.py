"""H3 geography, cross-backend seed parity and regenerated synthetic evidence."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import h3
import yaml

from openmapstack.checks.spatial import connect_spatial
from tests.test_nyc_dashboard import EXAMPLE, pipeline_module


class H3FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connection = connect_spatial()
        if cls.connection is None:
            raise unittest.SkipTest("DuckDB Spatial unavailable")
        cls.addClassCleanup(cls.connection.close)
        specification = importlib.util.spec_from_file_location("nyc_fixture", EXAMPLE / "rebuild_fixture.py")
        cls.fixture = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(cls.fixture)
        cls.cells = cls.fixture.build_cells(cls.connection)
        cls.fixture.populate_local(cls.connection, cls.cells)

    def test_catalogue_is_real_resolution_eight_with_land_centres(self):
        self.assertEqual(self.cells, json.loads((EXAMPLE / "setup/h3/cells.json").read_text()))
        self.assertEqual(len(self.cells), 83)
        self.assertEqual(len({cell["h3_cell"] for cell in self.cells}), 83)
        self.assertEqual({cell["borough"] for cell in self.cells}, {"Manhattan", "Queens", "Brooklyn"})
        mask = json.loads((EXAMPLE / "setup/h3/land-mask.geojson").read_text())
        for cell in self.cells:
            self.assertEqual(h3.get_resolution(cell["h3_cell"]), 8)
            self.assertEqual(h3.latlng_to_cell(cell["latitude"], cell["longitude"], 8), cell["h3_cell"])
            ring = cell["geometry"]["coordinates"][0]
            self.assertEqual(ring[:-1], [[longitude, latitude] for latitude, longitude in h3.cell_to_boundary(cell["h3_cell"])])
            self.assertEqual(ring[0], ring[-1])
            land = next(feature for feature in mask["features"] if feature["properties"]["borough"] == cell["borough"])
            self.assertTrue(self.connection.execute("SELECT ST_Contains(ST_GeomFromGeoJSON(?),ST_Point(?,?))",
                                                   [json.dumps(land["geometry"]), cell["longitude"], cell["latitude"]]).fetchone()[0])

    def test_every_backend_uses_the_identical_catalogue(self):
        for backend in ("postgis", "bigquery", "motherduck"):
            self.assertEqual((EXAMPLE / "setup" / backend / "zones.sql").read_text(), self.fixture.zone_sql(self.cells, backend))
        self.assertEqual(self.connection.execute("SELECT zone_id,h3_cell FROM ops.taxi_zones ORDER BY 1").fetchall(),
                         self.connection.execute("SELECT zone_id,h3_cell FROM market.fixture_zones ORDER BY 1").fetchall())
        postgis = (EXAMPLE / "setup/postgis/seed.sql").read_text()
        self.assertIn("mins => ops.fixture_rand('fleet-min-' || g, 60)", postgis)

    def test_events_and_pois_belong_to_their_assigned_hexagons(self):
        for table in ("trips", "ops.fleet_positions", "ops.hubs", "market.relevant_pois"):
            rows = self.connection.execute(f"SELECT zones.h3_cell,ST_Y(events.geom),ST_X(events.geom) FROM {table} events JOIN ops.taxi_zones zones ON zones.zone_id=events.taxi_zone_id").fetchall()
            self.assertGreater(len(rows), 0)
            for cell, latitude, longitude in rows:
                self.assertEqual(h3.latlng_to_cell(latitude, longitude, 8), cell)

    def test_optional_local_seed_builder_is_deterministic_and_conserves_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self.fixture.write_snapshots(self.connection, output)
            first = {path.name: self.fixture.digest(path) for path in output.glob("*.parquet")}
            self.fixture.write_snapshots(self.connection, output)
            self.assertEqual(first, {path.name: self.fixture.digest(path) for path in output.glob("*.parquet")})
            self.assertEqual(set(first), {"taxi_zones.parquet", "hubs.parquet", "fleet_positions.parquet",
                                          "customer_accounts.parquet", "zone_demand.parquet", "zone_market.parquet"})
            total = self.connection.execute(f"SELECT sum(trips_total) FROM read_parquet('{output / 'zone_demand.parquet'}')").fetchone()[0]
            self.assertEqual(total, self.connection.execute("SELECT count(*) FROM trips").fetchone()[0])
            counts = self.connection.execute(f"SELECT taxi_zone_id,trips_total FROM read_parquet('{output / 'zone_demand.parquet'}') ORDER BY 1").fetchall()
            self.assertEqual(counts, self.connection.execute("SELECT taxi_zone_id,count(*) FROM trips GROUP BY 1 ORDER BY 1").fetchall())

    def test_existing_pins_are_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = output / "taxi_zones.parquet"
            path.write_bytes(b"immutable prior capture")
            with self.assertRaisesRegex(ValueError, "Refusing to change pinned input"):
                self.fixture.write_snapshots(self.connection, output)
            self.assertEqual(path.read_bytes(), b"immutable prior capture")

    def test_geometry_validation_accepts_control_and_rejects_moved_hexagon(self):
        pipeline = pipeline_module()
        manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text())
        snapshots = pipeline.pinned_snapshot_paths(manifest)
        metrics = pipeline.compute_zone_metrics(self.connection, snapshots)
        pipeline.score_candidates(metrics, manifest["scoring"])
        candidates = [zone for zone in metrics if zone["final_score"] is not None]
        with tempfile.TemporaryDirectory() as directory, patch.object(pipeline, "DERIVED", Path(directory)):
            pipeline.write_outputs(self.connection, metrics, candidates)
            checks = pipeline.run_checks(self.connection, snapshots, metrics, candidates, manifest["scoring"]["eligibility"])
            self.assertEqual(next(check for check in checks if check["id"] == "h3_grid_valid")["status"], "passed")
            metrics[0]["geom_wkb"] = self.connection.execute("SELECT ST_AsWKB(ST_Translate(ST_GeomFromWKB(?),0.01,0))", [metrics[0]["geom_wkb"]]).fetchone()[0]
            checks = pipeline.run_checks(self.connection, snapshots, metrics, candidates, manifest["scoring"]["eligibility"])
            failed = next(check for check in checks if check["id"] == "h3_grid_valid")
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["invalid_zones"], [metrics[0]["zone_id"]])

    def test_live_capture_provenance_keeps_seeded_data_distinct_from_backend_checks(self):
        manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text())
        record = json.loads((EXAMPLE / manifest["runtime"]["fixture"]["source_manifest"]).read_text())
        captures = json.loads((EXAMPLE / "data/source/live-20260928-tlc-v1/capture-records.json").read_text())["records"]
        self.assertEqual(set(record["sources"]), set(manifest["sources"]))
        for key, source in manifest["sources"].items():
            self.assertTrue(source["origin"].startswith("live_backend_capture_of_seeded_demo_data"))
            self.assertEqual(source["pin"], captures[key]["pin"])
            self.assertEqual(record["sources"][key]["sha256"], source["pin"]["sha256"])
            self.assertEqual(self.fixture.digest(EXAMPLE / source["pin"]["path"]), source["pin"]["sha256"])
            self.assertTrue(source["access"]["connection"]["ref"].startswith("env:"))

    def test_h3_reference_check_rejects_a_mismatched_alias(self):
        pipeline = pipeline_module()
        manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text())
        snapshots = pipeline.pinned_snapshot_paths(manifest)
        metrics = pipeline.compute_zone_metrics(self.connection, snapshots)
        pipeline.score_candidates(metrics, manifest["scoring"])
        candidates = [zone for zone in metrics if zone["final_score"] is not None]
        with tempfile.TemporaryDirectory() as directory, patch.object(pipeline, "DERIVED", Path(directory)):
            pipeline.write_outputs(self.connection, metrics, candidates)
            for expected in ("passed", "failed"):
                checks = pipeline.run_checks(self.connection, snapshots, metrics, candidates, manifest["scoring"]["eligibility"])
                result = next(check for check in checks if check["id"] == "h3_references_consistent")
                self.assertEqual(result["status"], expected)
                if expected == "passed":
                    bad = Path(directory) / "bad-accounts.parquet"
                    self.connection.execute(f"COPY (SELECT * REPLACE('882a100d01fffff' AS h3_cell) FROM read_parquet('{snapshots['customer_accounts']}')) TO '{bad}' (FORMAT PARQUET)")
                    snapshots = {**snapshots, "customer_accounts": bad}
                else:
                    self.assertGreater(result["mismatches"]["customer_accounts"], 0)
