"""The NYC dashboard is a view over reproducible, aggregate-only evidence."""

import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from openmapstack.checks.spatial import connect_spatial
from openmapstack.integrity import declared_input_paths


EXAMPLE = Path(__file__).resolve().parents[1] / "examples/nyc-private-mobility"


def pipeline_module():
    specification = importlib.util.spec_from_file_location("nyc_dashboard_pipeline", EXAMPLE / "pipeline.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class NycDashboardTests(unittest.TestCase):
    def setUp(self):
        self.pipeline = pipeline_module()
        self.manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text())

    def test_template_is_a_reproducible_input(self):
        self.assertIn("dashboard-template.html", declared_input_paths(EXAMPLE, self.manifest))

    def test_generated_dashboard_contains_complete_derived_evidence_only(self):
        rendered = (EXAMPLE / "dashboard.html").read_text()
        match = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', rendered, re.S)
        self.assertIsNotNone(match)
        payload = json.loads(match.group(1))
        zones = json.loads((EXAMPLE / "data/derived/zone-metrics.geojson").read_text())
        self.assertEqual(payload["zones"], zones)
        self.assertEqual(payload["fleet_points"], json.loads((EXAMPLE / "data/derived/fleet-positions.geojson").read_text()))
        self.assertEqual(len(payload["fleet_points"]["features"]), payload["fleet"]["mapped_vehicles"])
        self.assertEqual(sum("2016 TLC pickup" in feature["properties"]["location_basis"]
                             for feature in payload["fleet_points"]["features"]), 281)
        self.assertEqual(payload["run_id"], self.manifest["runs"]["latest"]["id"])
        self.assertEqual(len(payload["sources"]), len(self.manifest["sources"]))
        self.assertIn("Seeded demo data.", rendered)
        self.assertIn("not NYC taxi zones", rendered)
        self.assertIn('role="tablist"', rendered)
        self.assertNotIn("__DATA__", rendered)
        for feature in payload["zones"]["features"]:
            properties = feature["properties"]
            self.assertTrue({"borough", "demand_score", "coverage_gap_score", "market_score",
                             "saturation_score", "active_days", "transit_pois"}.issubset(properties))
            self.assertFalse({"vehicle_id", "position_id", "contact_email", "rider_reference"} & properties.keys())
            self.assertEqual(properties["eligible"], properties["final_score"] is not None)

    def test_embedded_data_cannot_break_out_of_the_json_script(self):
        manifest = copy.deepcopy(self.manifest)
        manifest["project"]["title"] = '<script>alert("title")</script>'
        payload = {"text": '</script><script>alert("data")</script>&'}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dashboard-template.html").write_text((EXAMPLE / "dashboard-template.html").read_text())
            with patch.object(self.pipeline, "ROOT", root), patch.object(self.pipeline, "dashboard_payload", return_value=payload):
                self.pipeline.write_dashboard(manifest, [], "passed", "test", {})
            rendered = (root / "dashboard.html").read_text()
            self.assertNotIn('<script>alert(', rendered)
            match = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', rendered, re.S)
            self.assertEqual(json.loads(match.group(1)), payload)


class DashboardBrowserTests(unittest.TestCase):
    def test_offline_controls_selection_and_responsive_layout(self):
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            self.skipTest("Playwright unavailable")
        runtime = sync_playwright().start()
        self.addCleanup(runtime.stop)
        try:
            browser = runtime.chromium.launch(headless=True)
        except Error as error:
            self.skipTest(f"Chromium unavailable: {error}")
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("https://**", lambda route: route.abort())
        page.goto((EXAMPLE / "dashboard.html").as_uri(), wait_until="domcontentloaded")
        page.wait_for_selector(".zone-row")
        page.locator("#tab-map").click()
        self.assertFalse(page.locator("#lineage-context").evaluate("node => node.open"))
        page.locator("#lineage-context summary").click()
        self.assertEqual(page.locator("#lineage-context .lineage-stage strong").all_text_contents(),
                         ["Source", "Snapshots", "Metrics & scoring", "Final"])
        context = page.locator("#lineage-context").inner_text()
        self.assertIn("Neon PostGIS · neondb.ops.taxi_zones", context)
        self.assertIn("data/source/live-20260928/taxi_zones.parquet", context)
        self.assertIn("BigQuery · northstar-demo-001.northstar_analytics.trip_events", context)
        self.assertIn("MotherDuck · northstar_market.market.analysis_pois", context)
        self.assertIn("data/derived/zone-metrics.geojson", context)
        page.locator("#lineage-hubs summary").click()
        self.assertIn("data/source/live-20260928/hubs.parquet", page.locator("#lineage-hubs").inner_text())
        self.assertFalse(page.locator("#lineage-fleet").evaluate("node => node.open"))
        page.locator("#lineage-fleet summary").click()
        self.assertIn("data/source/tlc-2016-pickups.json", page.locator("#lineage-fleet").inner_text())
        self.assertIn("data/derived/fleet-positions.geojson", page.locator("#lineage-fleet").inner_text())
        page.locator("#layer-fleet").uncheck()
        self.assertIn("fleet=0", page.url)
        page.locator("#lineage-basemap summary").click()
        self.assertIn("cartodb-positron", page.locator("#lineage-basemap").inner_text())
        self.assertEqual(page.locator("#lineage-basemap .lineage-stage").count(), 1)
        page.locator("#layer-hubs").uncheck()
        self.assertIn("data/derived/existing-hubs.geojson", page.locator("#lineage-hubs").inner_text())
        page.locator("#tab-analysis").click()
        count = page.locator(".zone-row").count()
        self.assertGreater(count, 0)
        layout = page.evaluate("""() => ({
            viewport: innerHeight, content: document.documentElement.scrollHeight,
            detailBottom: document.querySelector('#inspector').getBoundingClientRect().bottom
        })""")
        self.assertLessEqual(layout["content"], layout["viewport"] + 2)
        self.assertLessEqual(layout["detailBottom"], layout["viewport"])
        selected = page.locator(".zone-row").nth(1).get_attribute("data-zone")
        page.locator(".zone-row").nth(1).click()
        self.assertIn(f"Zone {selected}", page.locator("#inspector").inner_text())
        page.locator("#search").fill(selected)
        self.assertEqual(page.locator(".zone-row").count(), 1)
        page.reload(wait_until="domcontentloaded")
        self.assertEqual(page.locator("#search").input_value(), selected)
        page.locator("#search").fill("no-matching-zone")
        self.assertEqual(page.locator(".zone-row").count(), 0)
        self.assertTrue(page.locator("#export").is_disabled())
        self.assertTrue(page.locator("#inspector").is_hidden())
        page.locator("#clear-filters").click()
        self.assertEqual(page.locator(".zone-row").count(), count)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("document.querySelector('#inspector').parentElement.id === 'mobile-inspector'")
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
        self.assertEqual(errors, [])


class LatestFleetTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_spatial()
        if self.connection is None:
            self.skipTest("DuckDB Spatial unavailable")
        self.addCleanup(self.connection.close)
        self.pipeline = pipeline_module()
        self.manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text())
        self.snapshots = self.pipeline.pinned_snapshot_paths(self.manifest)

    def test_snapshot_presence_counts_latest_vehicles_not_position_records(self):
        metrics = self.pipeline.compute_zone_metrics(self.connection, self.snapshots)
        actual = sum(zone["fleet_present"] for zone in metrics)
        count = self.connection.execute(f"SELECT count(DISTINCT vehicle_id), count(*) FROM read_parquet('{self.snapshots['fleet_positions']}')").fetchone()
        self.assertEqual(actual, count[0])
        self.assertLess(actual, count[1])
        self.pipeline.score_candidates(metrics, self.manifest["scoring"])
        self.assertEqual(sum(zone["final_score"] is not None for zone in metrics), 6)

    def test_latest_position_wins_with_tie_breaking_and_no_old_geometry_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fleet.parquet"
            self.connection.execute("""
                CREATE TABLE test_fleet AS
                SELECT position_id, 'alpha' AS tenant_id, vehicle_id, taxi_zone_id,
                       CAST(recorded_at AS TIMESTAMP) AS recorded_at,
                       CASE WHEN missing THEN NULL ELSE ST_Point(-73.9,40.7) END AS geom
                FROM (VALUES
                  (1,'moving',101,'2026-09-01 10:00:00',false),
                  (2,'moving',102,'2026-09-01 11:00:00',false),
                  (3,'tie',101,'2026-09-01 12:00:00',false),
                  (4,'tie',102,'2026-09-01 12:00:00',false),
                  (5,'missing',101,'2026-09-01 10:00:00',false),
                  (6,'missing',102,'2026-09-01 11:00:00',true)
                ) AS positions(position_id, vehicle_id, taxi_zone_id, recorded_at, missing)
            """)
            zones = self.snapshots["taxi_zones"]
            self.connection.execute(f"COPY (SELECT test_fleet.*, zones.h3_cell FROM test_fleet JOIN read_parquet('{zones}') zones ON zones.zone_id = test_fleet.taxi_zone_id) TO '{path}' (FORMAT PARQUET)")
            metrics = self.pipeline.compute_zone_metrics(self.connection, {**self.snapshots, "fleet_positions": path})
            counts = {zone["zone_id"]: zone["fleet_present"] for zone in metrics}
            self.assertEqual(counts[101], 0)
            self.assertEqual(counts[102], 2)
            self.assertEqual(sum(counts.values()), 2)

    def test_fleet_check_rejects_inflated_counts(self):
        metrics = self.pipeline.compute_zone_metrics(self.connection, self.snapshots)
        self.pipeline.score_candidates(metrics, self.manifest["scoring"])
        candidates = [zone for zone in metrics if zone["final_score"] is not None]
        with tempfile.TemporaryDirectory() as directory, patch.object(self.pipeline, "DERIVED", Path(directory)):
            self.pipeline.write_outputs(self.connection, metrics, candidates)
            for expected in ("passed", "failed"):
                checks = self.pipeline.run_checks(self.connection, self.snapshots, metrics, candidates,
                                                  self.manifest["scoring"]["eligibility"])
                check = next(check for check in checks if check["id"] == "fleet_latest_observation")
                self.assertEqual(check["status"], expected)
                if expected == "failed":
                    self.assertEqual(check["mismatched_zones"], [metrics[0]["zone_id"]])
                metrics[0]["fleet_present"] += 1

    def test_zero_fleet_pool_has_no_division_by_zero(self):
        metrics = self.pipeline.compute_zone_metrics(self.connection, self.snapshots)
        for zone in metrics:
            zone["fleet_present"] = 0
        self.pipeline.score_candidates(metrics, self.manifest["scoring"])
        self.assertTrue(all(zone["saturation_score"] == 1 for zone in metrics if zone["final_score"] is not None))
