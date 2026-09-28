-- Existing warehouse zone polygons used to locate MotherDuck's per-zone scores.
-- These are source geometry, not the H3 analysis grid.
SELECT
    zone_id,
    borough,
    zone_name,
    zone_area
FROM northstar_analytics.taxi_zones
