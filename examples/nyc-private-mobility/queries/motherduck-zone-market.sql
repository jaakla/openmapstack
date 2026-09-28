-- Private market and POI context per taxi zone, from MotherDuck.
--
-- The A4 input: what is already around a zone -- charging, parking, transit,
-- and the competition -- combined with Northstar's own market scoring.
--
-- Reads the analysis views, never the base tables, and deliberately never
-- market.analyst_annotations: the private notes inform the scores and are
-- not republished. That is a convention this analysis honours rather than a
-- grant the backend enforces, and fixture.yaml records it as such.
--
--   openmapstack source snapshot . --source zone_market \
--     --query-file queries/motherduck-zone-market.sql \
--     --destination data/source/zone_market.parquet --approve --write-manifest
--
-- A zone with no POIs at all is absent from the counts view, so the join is
-- a LEFT JOIN and the counts coalesce to 0: "no amenities" is a real answer,
-- not missing data.

SELECT
    s.taxi_zone_id,
    z.h3_cell,
    s.market_score AS market_score_raw,
    s.competitor_hubs,
    s.parking_index,
    s.transit_index,
    COALESCE(p.charging_pois, 0) AS charging_pois,
    COALESCE(p.parking_pois, 0) AS parking_pois,
    COALESCE(p.transit_pois, 0) AS transit_pois,
    COALESCE(p.competitor_pois, 0) AS competitor_pois,
    COALESCE(p.total_pois, 0) AS total_pois
FROM market.analysis_zone_scores s
JOIN market.fixture_zones z ON z.zone_id = s.taxi_zone_id
LEFT JOIN market.analysis_zone_poi_counts p ON p.taxi_zone_id = s.taxi_zone_id

ORDER BY s.taxi_zone_id
