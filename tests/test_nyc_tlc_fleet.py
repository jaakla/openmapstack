"""The TLC coordinate seed enriches synthetic fleet rows without changing provenance."""

import importlib.util
import json
import unittest
from collections import Counter
from pathlib import Path

import h3
import yaml

from openmapstack.checks.spatial import connect_spatial


ROOT = Path(__file__).resolve().parents[1] / "examples/nyc-private-mobility"


class TlcFleetSeedTests(unittest.TestCase):
    def test_selection_is_bounded_deduplicated_and_inside_the_grid(self):
        spec = importlib.util.spec_from_file_location("tlc_pickups", ROOT / "capture_tlc_pickups.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cell = json.loads((ROOT / "setup/h3/cells.json").read_text())[0]["h3_cell"]
        latitude, longitude = h3.cell_to_latlng(cell)
        rows = [{"tpep_pickup_datetime": f"2016-06-01T10:00:{second:02d}",
                 "pickup_longitude": str(longitude), "pickup_latitude": str(latitude)}
                for second in range(7)]
        rows.append(rows[0])
        rows.append({"tpep_pickup_datetime": "2016-06-01T11:00:00",
                     "pickup_longitude": "0", "pickup_latitude": "0"})
        selected = module.select_points(rows, {cell})
        self.assertEqual(len(selected), module.MAX_PER_CELL)
        self.assertEqual({row["h3_cell"] for row in selected}, {cell})
        self.assertEqual(len({row["pickup_datetime"] for row in selected}), len(selected))

    def test_reader_capture_matches_every_pinned_tlc_coordinate(self):
        duck = connect_spatial()
        if duck is None:
            self.skipTest("DuckDB Spatial unavailable")
        self.addCleanup(duck.close)
        manifest = yaml.safe_load((ROOT / "project.yaml").read_text())
        path = ROOT / manifest["sources"]["fleet_positions"]["pin"]["path"]
        sample = json.loads((ROOT / "data/source/tlc-2016-pickups.json").read_text())
        rows = duck.execute(
            f"SELECT vehicle_id,h3_cell,ST_X(geom),ST_Y(geom) FROM read_parquet('{path}') "
            "WHERE vehicle_id LIKE 'TLC16-DEMO-%' ORDER BY vehicle_id"
        ).fetchall()
        self.assertEqual(len(rows), sample["selected_rows"])
        self.assertEqual(len({row[0] for row in rows}), len(rows))
        self.assertLessEqual(max(Counter(row["h3_cell"] for row in sample["records"]).values()), 5)
        for index, (vehicle_id, cell, longitude, latitude) in enumerate(rows, 1):
            source = sample["records"][index - 1]
            self.assertEqual(vehicle_id, f"TLC16-DEMO-{index:04d}")
            self.assertEqual(cell, source["h3_cell"])
            self.assertAlmostEqual(longitude, source["longitude"])
            self.assertAlmostEqual(latitude, source["latitude"])


if __name__ == "__main__":
    unittest.main()
