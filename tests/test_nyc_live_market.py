"""Live MotherDuck scores and POIs are attributed to the H3 analysis grid."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from openmapstack.checks.spatial import connect_spatial


PIPELINE = Path(__file__).resolve().parents[1] / "examples/nyc-private-mobility/pipeline.py"


class LiveMarketTests(unittest.TestCase):
    def test_score_attribution_and_point_counts(self) -> None:
        duck = connect_spatial()
        if duck is None:
            self.skipTest("DuckDB Spatial unavailable")
        spec = importlib.util.spec_from_file_location("nyc_pipeline", PIPELINE)
        pipeline = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pipeline)
        try:
            with tempfile.TemporaryDirectory() as directory:
                paths = {key: Path(directory) / f"{key}.parquet" for key in (
                    "taxi_zones", "market_reference_zones", "market_scores", "market_pois"
                )}
                duck.execute(f"""
                    COPY (
                        SELECT 1 AS zone_id, 'a' AS h3_cell,
                               ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))') AS geom
                        UNION ALL SELECT 2, 'b',
                               ST_GeomFromText('POLYGON((1 0,2 0,2 1,1 1,1 0))')
                    ) TO '{paths['taxi_zones']}' (FORMAT PARQUET)
                """)
                duck.execute(f"""
                    COPY (
                        SELECT 10 AS zone_id,
                               ST_GeomFromText('POLYGON((0 0,2 0,2 1,0 1,0 0))') AS zone_area
                    ) TO '{paths['market_reference_zones']}' (FORMAT PARQUET)
                """)
                duck.execute(f"""
                    COPY (SELECT 10 AS taxi_zone_id, 0.8::DOUBLE AS market_score_raw)
                    TO '{paths['market_scores']}' (FORMAT PARQUET)
                """)
                duck.execute(f"""
                    COPY (
                        SELECT 'c' AS poi_id, 'charging' AS category, ST_Point(0.2, 0.2) AS geom
                        UNION ALL SELECT 'k', 'competitor', ST_Point(0.3, 0.3)
                        UNION ALL SELECT 'p', 'parking', ST_Point(1.2, 0.2)
                        UNION ALL SELECT 'outside', 'transit', ST_Point(3, 3)
                    ) TO '{paths['market_pois']}' (FORMAT PARQUET)
                """)
                relation = pipeline.market_relation(duck, paths)
                rows = duck.execute(
                    f"SELECT taxi_zone_id, h3_cell, market_score_raw, charging_pois, "
                    f"parking_pois, transit_pois, competitor_pois, total_pois "
                    f"FROM {relation} ORDER BY taxi_zone_id"
                ).fetchall()
                self.assertEqual(rows, [
                    (1, "a", 0.8, 1, 0, 0, 1, 2),
                    (2, "b", 0.8, 0, 1, 0, 0, 1),
                ])
                duck.execute(f"""
                    COPY (SELECT 10 AS zone_id,
                                 ST_GeomFromText('POLYGON((0 0,1 0,1 1,0 1,0 0))') AS zone_area)
                    TO '{paths['market_reference_zones']}' (FORMAT PARQUET)
                """)
                with self.assertRaisesRegex(SystemExit, "no unique source market zone"):
                    pipeline.market_relation(duck, paths)
        finally:
            duck.close()


if __name__ == "__main__":
    unittest.main()
