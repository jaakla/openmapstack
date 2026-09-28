-- Reader-visible point observations for local H3 spatial aggregation.
-- Analyst annotations and POI names are outside the approved analysis input.
SELECT
    poi_id,
    taxi_zone_id,
    category,
    source,
    geom
FROM market.analysis_pois
WHERE geom IS NOT NULL
