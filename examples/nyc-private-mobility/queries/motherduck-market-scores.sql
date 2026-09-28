-- Reader-visible market scores keyed to the warehouse's original zones.
-- H3 attribution happens locally by intersecting the corresponding BigQuery
-- zone polygons with the PostGIS H3 grid.
SELECT
    taxi_zone_id,
    market_score AS market_score_raw,
    competitor_hubs,
    parking_index,
    transit_index,
    source_vintage
FROM market.analysis_zone_scores
