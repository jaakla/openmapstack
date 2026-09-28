-- Northstar Mobility BigQuery fixture: deterministic seed
--
-- Every value derives from the first 32 bits of MD5('northstar|20260917|' || label),
-- a portable deterministic fingerprint, so the fixture is reproducible: no RAND(),
-- no CURRENT_TIMESTAMP() in fixture rows. Re-running is idempotent (tables
-- are truncated first).
--
--   python provision.py bigquery        (runs after schema.sql)
--
-- Volumes: trip_events 6640 over 30 days, zone_daily_demand aggregated,
-- vehicle_daily_metrics 1200, driver_costs 240. Tenants alpha/beta/gamma all
-- exist; only alpha is visible through the row access policy in security.sql.
--
-- Nothing here is a real NYC taxi record: the trip *shape* follows the public
-- dataset's structure, every business attribute is synthetic, and no output
-- may present these rows as belonging to a real operator.

CREATE TEMP FUNCTION fixture_rand(label STRING, modulus INT64) AS (
    MOD(CAST(CONCAT('0x', SUBSTR(TO_HEX(MD5(CONCAT('northstar|20260917|', label))), 1, 8)) AS INT64), modulus)
);

CREATE TEMP FUNCTION fixture_tenant(label STRING) AS (
    CASE
        WHEN MOD(CAST(CONCAT('0x', SUBSTR(TO_HEX(MD5(CONCAT('northstar|20260917|', label))), 1, 8)) AS INT64), 10) < 6 THEN 'alpha'
        WHEN MOD(CAST(CONCAT('0x', SUBSTR(TO_HEX(MD5(CONCAT('northstar|20260917|', label))), 1, 8)) AS INT64), 10) < 9 THEN 'beta'
        ELSE 'gamma'
    END
);

-- A row access policy makes its table unwritable even for the admin that
-- created it ("User does not have full access ... due to row access policies"),
-- so a re-run drops the policies here and security.sql re-creates them in the
-- step immediately after. Dropping when none exist is a no-op, so this is
-- equally correct on a first run. The PostGIS seed clears its policies for the
-- same reason.
--
-- The reader must already be locked out before these run. On a
-- re-provisioned fixture it still holds the dataset grant from the previous
-- run, and dropping a policy does not restrict an existing grant -- it
-- removes the *filter*, so the reader would see every tenant's rows rather
-- than none of them. Measured on the live fixture: 4800 rows across 3
-- tenants instead of 2917 from tenant alpha.
--
-- `provision.py` revokes the grant and waits until the reader is actually
-- denied before it applies this file, the way it tears the PostGIS role down
-- before re-applying that fixture's SQL. The revoke cannot live here: it and
-- the drops below are one BigQuery job, and an IAM revoke takes a moment to
-- take effect, so there would be nothing to wait on.
--
-- security.sql then grants the reader LAST, after every policy exists. The
-- sequence fails closed: at every point the reader either has no access at
-- all, or has access with the row filter in force. A seed that aborts half
-- way leaves the fixture locked, never open.
DROP ALL ROW ACCESS POLICIES ON `:project`.`:dataset`.trip_events;
DROP ALL ROW ACCESS POLICIES ON `:project`.`:dataset`.zone_daily_demand;
DROP ALL ROW ACCESS POLICIES ON `:project`.`:dataset`.vehicle_daily_metrics;

TRUNCATE TABLE `:project`.`:dataset`.taxi_zones;
TRUNCATE TABLE `:project`.`:dataset`.trip_events;
TRUNCATE TABLE `:project`.`:dataset`.zone_daily_demand;
TRUNCATE TABLE `:project`.`:dataset`.vehicle_daily_metrics;
TRUNCATE TABLE `:project`.`:restricted_dataset`.driver_costs;

-- -- H3 resolution-8 cells with land-based centres -----------------------
-- The same H3 catalogue the PostGIS fixture builds, so taxi_zone_id 101..183 means
-- the same polygon in every backend.
__H3_ZONES__

-- -- 80 trips per H3 cell over 30 days ------------------------------------------------
INSERT INTO `:project`.`:dataset`.trip_events
    (trip_id, tenant_id, taxi_zone_id, pickup_date, pickup_ts, trip_distance_km,
     fare_amount, internal_cost, rider_reference, pickup_point)
SELECT
    FORMAT('T-%05d', g),
    fixture_tenant(CONCAT('trip-tenant-', g)),
    zone_id,
    pickup_date,
    TIMESTAMP_ADD(TIMESTAMP(pickup_date), INTERVAL 6 * 60 + fixture_rand(CONCAT('trip-min-', g), 900) MINUTE),
    ROUND(0.5 + fixture_rand(CONCAT('trip-km-', g), 2400) / 100.0, 2),
    CAST(ROUND(3.0 + fixture_rand(CONCAT('trip-fare-', g), 7200) / 100.0, 2) AS NUMERIC),
    CAST(ROUND(1.0 + fixture_rand(CONCAT('trip-cost-', g), 4200) / 100.0, 2) AS NUMERIC),
    FORMAT('R-%s', TO_HEX(MD5(CONCAT('northstar|20260917|rider-', CAST(g AS STRING))))),
    ST_GEOGPOINT(zone.longitude, zone.latitude)
FROM (
    SELECT
        g,
        101 + fixture_rand(CONCAT('trip-zone-', g), (SELECT COUNT(*) FROM `:project`.`:dataset`.taxi_zones)) AS zone_id,
        DATE_ADD(DATE '2026-08-01', INTERVAL fixture_rand(CONCAT('trip-day-', g), 30) DAY) AS pickup_date
    FROM UNNEST(GENERATE_ARRAY(1, (SELECT COUNT(*) * 80 FROM `:project`.`:dataset`.taxi_zones))) AS g
) AS trip
JOIN `:project`.`:dataset`.taxi_zones AS zone USING (zone_id);

-- -- daily demand per zone, derived from the trips above ---------------------
INSERT INTO `:project`.`:dataset`.zone_daily_demand
    (taxi_zone_id, tenant_id, demand_date, trips, mean_trip_km)
SELECT
    taxi_zone_id,
    tenant_id,
    pickup_date,
    COUNT(*),
    ROUND(AVG(trip_distance_km), 3)
FROM `:project`.`:dataset`.trip_events
GROUP BY taxi_zone_id, tenant_id, pickup_date;

-- -- 1200 vehicle-days ------------------------------------------------------
INSERT INTO `:project`.`:dataset`.vehicle_daily_metrics
    (vehicle_id, tenant_id, metric_date, active_minutes, energy_kwh)
SELECT
    FORMAT('NV-%03d', 100 + fixture_rand(CONCAT('veh-id-', g), 900)),
    fixture_tenant(CONCAT('veh-tenant-', g)),
    DATE_ADD(DATE '2026-08-01', INTERVAL fixture_rand(CONCAT('veh-day-', g), 30) DAY),
    60 + fixture_rand(CONCAT('veh-min-', g), 600),
    ROUND(5.0 + fixture_rand(CONCAT('veh-kwh-', g), 7000) / 100.0, 2)
FROM UNNEST(GENERATE_ARRAY(1, 1200)) AS g;

-- -- 240 restricted payroll rows (never granted to the analysis reader) -----
INSERT INTO `:project`.`:restricted_dataset`.driver_costs
    (driver_id, tenant_id, payroll_band, hourly_rate_usd)
SELECT
    FORMAT('D-%04d', g),
    fixture_tenant(CONCAT('drv-tenant-', g)),
    ['A', 'B', 'C'][OFFSET(fixture_rand(CONCAT('drv-band-', g), 3))],
    CAST(ROUND(18.0 + fixture_rand(CONCAT('drv-rate-', g), 2200) / 100.0, 2) AS NUMERIC)
FROM UNNEST(GENERATE_ARRAY(1, 240)) AS g;
