-- Historical trip demand per taxi zone, from the BigQuery warehouse.
--
-- This is the A2 input the operational PostGIS database cannot answer: how
-- much demand a zone has actually seen over time, not how many accounts sit
-- in it today.
--
-- Rows are confined to tenant 'alpha' by the row access policy on
-- zone_daily_demand -- the query says nothing about tenants, and must not:
-- the boundary is the backend's, and a filter here would hide whether it
-- works. The dataset is named without a project so the query resolves
-- against whichever project the connector's client is pointed at.
--
--   openmapstack source snapshot . --source zone_demand \
--     --query-file queries/bigquery-zone-demand.sql \
--     --destination data/source/zone_demand.parquet --approve --write-manifest
--
-- STDDEV_SAMP is null for a zone with a single active day; a zone with one
-- day of data has no measured variability, and 0 is the honest reading.

SELECT
    taxi_zone_id,
    z.h3_cell,
    SUM(trips) AS trips_total,
    MAX(trips) AS trips_peak_day,
    ROUND(AVG(trips), 3) AS trips_mean_day,
    ROUND(COALESCE(STDDEV_SAMP(trips), 0), 3) AS trips_stddev_day,
    COUNT(DISTINCT demand_date) AS active_days,
    ROUND(AVG(mean_trip_km), 3) AS mean_trip_km
FROM northstar_analytics.zone_daily_demand AS demand
JOIN northstar_analytics.taxi_zones AS z ON z.zone_id = demand.taxi_zone_id
GROUP BY taxi_zone_id, z.h3_cell
ORDER BY taxi_zone_id
