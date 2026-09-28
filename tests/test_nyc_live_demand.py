"""Observed BigQuery trip snapshots are aggregated on the PostGIS H3 grid."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from openmapstack.checks.spatial import connect_spatial


PIPELINE = Path(__file__).resolve().parents[1] / "examples/nyc-private-mobility/pipeline.py"


class LiveTripDemandTests(unittest.TestCase):
    def test_spatial_aggregation_counts_days_and_excludes_outside_trips(self) -> None:
        duck = connect_spatial()
        if duck is None:
            self.skipTest("DuckDB Spatial unavailable")
        spec = importlib.util.spec_from_file_location("nyc_pipeline", PIPELINE)
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        try:
            with tempfile.TemporaryDirectory() as directory:
                zones = Path(directory) / "zones.parquet"
                trips = Path(directory) / "trips.parquet"
                duck.execute(f"""
                    COPY (
                        SELECT 1 AS zone_id, 'cell-a' AS h3_cell,
                               ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))') AS geom
                        UNION ALL
                        SELECT 2, 'cell-b',
                               ST_GeomFromText('POLYGON((1 0,2 0,2 1,1 1,1 0))')
                    ) TO '{zones}' (FORMAT PARQUET)
                """)
                duck.execute(f"""
                    COPY (
                        SELECT 'a' AS trip_id, DATE '2026-08-01' AS pickup_date,
                               1.0 AS trip_distance_km, ST_Point(0.2, 0.2) AS pickup_point
                        UNION ALL SELECT 'b', DATE '2026-08-01', 2.0, ST_Point(0.3, 0.3)
                        UNION ALL SELECT 'c', DATE '2026-08-02', 3.0, ST_Point(0.4, 0.4)
                        UNION ALL SELECT 'outside', DATE '2026-08-02', 4.0, ST_Point(3, 3)
                    ) TO '{trips}' (FORMAT PARQUET)
                """)
                relation = pipeline.demand_relation(
                    duck, {"taxi_zones": zones, "trip_events": trips}
                )
                rows = duck.execute(
                    f"SELECT taxi_zone_id, trips_total, trips_peak_day, trips_stddev_day, "
                    f"active_days, mean_trip_km FROM {relation} ORDER BY taxi_zone_id"
                ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0][0:3], (1, 3, 2))
                self.assertAlmostEqual(rows[0][3], 0.70710678, places=6)
                self.assertEqual(rows[0][4:], (2, 2.0))
                self.assertEqual(rows[1], (2, 0, 0, 0.0, 0, None))
        finally:
            duck.close()


if __name__ == "__main__":
    unittest.main()
