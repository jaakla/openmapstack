-- Northstar Mobility MotherDuck fixture: schema (examples/nyc-private-mobility)
-- Issue: https://github.com/jaakla/openmapstack-skills/issues/43
--
-- The private market-intelligence overlay: a shared POI-style source narrowed
-- to a fixed NYC subset, plus enrichment that only Northstar holds. That
-- combination -- public geometry, private scores -- is what makes the whole
-- database private, and it is why the analysis may publish derived scores but
-- not the annotations behind them.
--
-- Structural layer only; deterministic rows live in seed.sql and the access
-- boundary in security.sql. The database itself is created by provision.py
-- (`CREATE DATABASE IF NOT EXISTS ...; USE ...;`) so that everything below is
-- ordinary DuckDB SQL: it runs unchanged against MotherDuck and against a
-- local DuckDB, which is how tests/test_cloud_fixture_sql.py exercises it.
--
--   python provision.py motherduck
--
-- taxi_zone_id is the same stable join key the PostGIS and BigQuery fixtures
-- use (zones 101..183); geometry is EPSG:4326 throughout.

CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.fixture_zones (
    zone_id INTEGER PRIMARY KEY, h3_cell VARCHAR UNIQUE NOT NULL,
    borough VARCHAR, zone_name VARCHAR, zone_source VARCHAR,
    geom GEOMETRY, latitude DOUBLE, longitude DOUBLE
);

-- Per-zone market attractiveness. Public-origin inputs (POI density,
-- transit access) combined with Northstar's own competitor intelligence.
CREATE TABLE IF NOT EXISTS market.zone_market_scores (
    taxi_zone_id    INTEGER PRIMARY KEY,
    market_score    DOUBLE NOT NULL,       -- 0..1, higher is more attractive
    competitor_hubs INTEGER NOT NULL,
    parking_index   DOUBLE NOT NULL,       -- 0..1 availability proxy
    transit_index   DOUBLE NOT NULL,       -- 0..1 access proxy
    source_vintage  DATE NOT NULL
);

-- The narrowed shared POI subset. `source` records public origin per row so
-- the presentation can separate public-origin from private-enrichment data.
CREATE TABLE IF NOT EXISTS market.relevant_pois (
    poi_id       VARCHAR PRIMARY KEY,
    taxi_zone_id INTEGER NOT NULL,
    category     VARCHAR NOT NULL,         -- charging | parking | transit | competitor | retail
    name         VARCHAR NOT NULL,
    source       VARCHAR NOT NULL,
    geom         GEOMETRY
);

-- Private enrichment: never published, and never joined into a public output.
CREATE TABLE IF NOT EXISTS market.analyst_annotations (
    annotation_id VARCHAR PRIMARY KEY,
    taxi_zone_id  INTEGER NOT NULL,
    analyst       VARCHAR NOT NULL,
    note          VARCHAR NOT NULL,
    confidence    DOUBLE NOT NULL
);
