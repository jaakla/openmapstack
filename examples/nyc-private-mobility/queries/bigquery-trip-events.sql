-- Reader-scoped trip observations for local H3 aggregation.
-- The BigQuery row access policy limits this table to tenant alpha.
-- Protected fare, cost and rider columns are deliberately excluded.
SELECT
    tenant_id,
    trip_id,
    pickup_date,
    trip_distance_km,
    pickup_point
FROM northstar_analytics.trip_events
WHERE pickup_point IS NOT NULL
