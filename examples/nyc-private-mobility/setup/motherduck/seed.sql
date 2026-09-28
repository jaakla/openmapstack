-- Northstar Mobility MotherDuck fixture: deterministic seed
--
-- Every value derives from md5_number('northstar|20260917|' || label), so the
-- fixture is byte-reproducible: no random(), no wall-clock values. Re-running
-- is idempotent (tables are emptied first).
--
--   python provision.py motherduck      (runs after schema.sql)
--
-- Volumes: zone_market_scores 83 (one per zone), relevant_pois 240,
-- analyst_annotations 24. Zones are 101..183, matching the PostGIS fixture.

DELETE FROM market.analyst_annotations;
DELETE FROM market.relevant_pois;
DELETE FROM market.zone_market_scores;
DELETE FROM market.fixture_zones;

__H3_ZONES__

-- -- 83 zone market scores ---------------------------------------------------
INSERT INTO market.zone_market_scores
    (taxi_zone_id, market_score, competitor_hubs, parking_index, transit_index, source_vintage)
SELECT
    101 + g,
    round(abs(md5_number('northstar|20260917|mkt-score-' || g) % 1000) / 1000.0, 3),
    abs(md5_number('northstar|20260917|mkt-comp-' || g) % 4)::INTEGER,
    round(abs(md5_number('northstar|20260917|mkt-park-' || g) % 1000) / 1000.0, 3),
    round(abs(md5_number('northstar|20260917|mkt-transit-' || g) % 1000) / 1000.0, 3),
    DATE '2026-06-30'
FROM generate_series(0, (SELECT count(*)::INTEGER - 1 FROM market.fixture_zones)) AS t(g);

-- -- 240 POIs spread over the zone grid --------------------------------------
INSERT INTO market.relevant_pois (poi_id, taxi_zone_id, category, name, source, geom)
SELECT
    'POI-' || lpad(g::VARCHAR, 4, '0'),
    zone_id,
    category,
    upper(category[1]) || category[2:] || ' ' || lpad(g::VARCHAR, 4, '0'),
    CASE WHEN category = 'competitor' THEN 'northstar-private' ELSE 'shared-open-places' END,
    ST_Point(zone.longitude, zone.latitude)
FROM (
    SELECT
        g,
        101 + abs(md5_number('northstar|20260917|poi-zone-' || g) % (SELECT count(*) FROM market.fixture_zones))::INTEGER AS zone_id,
        (ARRAY['charging', 'parking', 'transit', 'competitor', 'retail'])[
            1 + abs(md5_number('northstar|20260917|poi-cat-' || g) % 5)::INTEGER] AS category
    FROM generate_series(1, 240) AS t(g)
) AS poi
JOIN market.fixture_zones AS zone USING (zone_id);

-- -- 24 private analyst annotations -------------------------------------------
INSERT INTO market.analyst_annotations (annotation_id, taxi_zone_id, analyst, note, confidence)
SELECT
    'AN-' || lpad(g::VARCHAR, 3, '0'),
    101 + abs(md5_number('northstar|20260917|ann-zone-' || g) % (SELECT count(*) FROM market.fixture_zones))::INTEGER,
    'analyst-' || (1 + abs(md5_number('northstar|20260917|ann-who-' || g) % 4)::INTEGER),
    'Private assessment note ' || lpad(g::VARCHAR, 3, '0'),
    round(0.5 + abs(md5_number('northstar|20260917|ann-conf-' || g) % 500) / 1000.0, 3)
FROM generate_series(1, 24) AS t(g);
