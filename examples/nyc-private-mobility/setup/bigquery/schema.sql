-- Northstar Mobility BigQuery fixture: schema (examples/nyc-private-mobility)
-- Issue: https://github.com/jaakla/openmapstack-skills/issues/43
--
-- The historical analytical warehouse: trip demand over time, which is the
-- one input the operational PostGIS database cannot answer. Structure only;
-- deterministic rows live in seed.sql and the boundary in security.sql.
--
--   python provision.py bigquery
--
-- Two datasets, because the inaccessible path is part of the fixture:
--   :dataset             -- the analysis surface the restricted reader may see
--   :restricted_dataset  -- never granted to the reader, at all
--
-- Tables carry a default expiry so an abandoned demo fixture ages out instead
-- of billing indefinitely. taxi_zone_id (101..183) is the same stable join key
-- the PostGIS and MotherDuck fixtures use; GEOGRAPHY is WGS84 by definition.

CREATE SCHEMA IF NOT EXISTS `:project`.`:dataset`
OPTIONS (
    location = :'location',
    default_table_expiration_days = 30,
    description = 'Northstar Mobility demo fixture: analysis surface (OpenMapStack issue #43)',
    labels = [('fixture', 'openmapstack-nyc-private-mobility')]
);

CREATE SCHEMA IF NOT EXISTS `:project`.`:restricted_dataset`
OPTIONS (
    location = :'location',
    default_table_expiration_days = 30,
    description = 'Northstar Mobility demo fixture: restricted, never granted to the analysis reader',
    labels = [('fixture', 'openmapstack-nyc-private-mobility')]
);

-- Public-origin zone reference, the BigQuery counterpart of ops.taxi_zones.
-- It carries no tenant_id and gets no row access policy: public reference
-- data is fully readable, exactly as it is in the PostGIS fixture. It is also
-- the only table here whose dry run returns a byte estimate, because BigQuery
-- withholds that estimate for any table under a row access policy.
CREATE TABLE IF NOT EXISTS `:project`.`:dataset`.taxi_zones (
    zone_id     INT64 NOT NULL,
    h3_cell STRING NOT NULL,
    latitude FLOAT64 NOT NULL,
    longitude FLOAT64 NOT NULL,
    borough     STRING NOT NULL,
    zone_name   STRING NOT NULL,
    zone_source STRING NOT NULL,
    zone_area   GEOGRAPHY NOT NULL
)
OPTIONS (description = 'Public-origin zone reference geometry; no tenant, no row access policy.');

ALTER TABLE `:project`.`:dataset`.taxi_zones ADD COLUMN IF NOT EXISTS h3_cell STRING;
ALTER TABLE `:project`.`:dataset`.taxi_zones ADD COLUMN IF NOT EXISTS latitude FLOAT64;
ALTER TABLE `:project`.`:dataset`.taxi_zones ADD COLUMN IF NOT EXISTS longitude FLOAT64;

-- Historical trips. internal_cost and rider_reference are the protected
-- columns: the analysis must never publish them, and column-level security
-- withholds them outright when a policy tag is configured
-- (setup/bigquery/column-security.sql).
CREATE TABLE IF NOT EXISTS `:project`.`:dataset`.trip_events (
    trip_id          STRING NOT NULL,
    tenant_id        STRING NOT NULL,
    taxi_zone_id     INT64 NOT NULL,
    pickup_date      DATE NOT NULL,
    pickup_ts        TIMESTAMP NOT NULL,
    trip_distance_km FLOAT64 NOT NULL,
    fare_amount      NUMERIC NOT NULL,
    internal_cost    NUMERIC NOT NULL,
    rider_reference  STRING NOT NULL,
    pickup_point     GEOGRAPHY
)
PARTITION BY pickup_date
CLUSTER BY taxi_zone_id
OPTIONS (description = 'Trip-level demand. Public-origin trip shape, synthetic business attributes.');

-- Pre-aggregated demand, the shape the canonical pipeline actually snapshots.
CREATE TABLE IF NOT EXISTS `:project`.`:dataset`.zone_daily_demand (
    taxi_zone_id  INT64 NOT NULL,
    tenant_id     STRING NOT NULL,
    demand_date   DATE NOT NULL,
    trips         INT64 NOT NULL,
    mean_trip_km  FLOAT64 NOT NULL
)
PARTITION BY demand_date
CLUSTER BY taxi_zone_id;

CREATE TABLE IF NOT EXISTS `:project`.`:dataset`.vehicle_daily_metrics (
    vehicle_id     STRING NOT NULL,
    tenant_id      STRING NOT NULL,
    metric_date    DATE NOT NULL,
    active_minutes INT64 NOT NULL,
    energy_kwh     FLOAT64 NOT NULL
)
PARTITION BY metric_date;

-- The inaccessible relation: the BigQuery counterpart of hr.driver_private.
CREATE TABLE IF NOT EXISTS `:project`.`:restricted_dataset`.driver_costs (
    driver_id       STRING NOT NULL,
    tenant_id       STRING NOT NULL,
    payroll_band    STRING NOT NULL,
    hourly_rate_usd NUMERIC NOT NULL
);
