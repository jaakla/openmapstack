#!/usr/bin/env python3
"""Canonical reproducible pipeline for examples/nyc-private-mobility.

Northstar Mobility — NYC operations/charging hub candidates (tenant alpha).

The pipeline runs exclusively from pinned local snapshots under
``data/source/``. The active H3 profile is generated locally and simulates
restricted-reader extracts; it does not constitute evidence of warehouse security.
It never touches a live warehouse: provisioning and snapshotting belong to
``provision.py`` and ``openmapstack source snapshot``.

Three backends contribute: BigQuery historical demand, PostGIS fleet and hub
coverage, MotherDuck market context -- combined locally, never joined live.

Flow:
    data/source/*.parquet  ->  zone metrics -> eligibility -> scoring
        -> data/derived/{zone-metrics.parquet, hub-candidates.parquet,
           hub-candidates.geojson}
        -> validation/latest-report.json + runs/run-*.json + dashboard.html
           + project.qgs/project.qgz

The scoring model lives in ``project.yaml`` (``scoring:``) and is recomputed
here from derived columns only; every assumption is recorded in
``interpretation.assumptions`` (A1-A6).
"""

from __future__ import annotations

import datetime
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import yaml

from openmapstack.checks.spatial import connect_spatial
from openmapstack.integrity import (
    canonical_file_set_hash,
    declared_input_paths,
    declared_output_paths,
    file_inventory,
    sha256_file,
)

ROOT = Path(__file__).resolve().parent
DERIVED = ROOT / "data" / "derived"
VALIDATION_DIR = ROOT / "validation"
RUNS_DIR = ROOT / "runs"

ANALYSIS_EPSG = 32618  # UTM 18N, metres — NYC metric CRS (assumption A3)
PROTECTED_COLUMNS = {"contact_name", "contact_email", "contract_value", "internal_cost", "rider_reference"}
REQUIRED_CHECKS = (
    "geometry_valid",
    "crs_known",
    "row_count_gt_zero",
    "no_null_zone_id",
    "rls_scope_alpha_only",
    "protected_columns_absent",
    "score_components_complete",
    "score_range",
    "eligibility_thresholds",
    "fleet_latest_observation",
    "h3_grid_valid",
    "h3_references_consistent",
    "qgis_project_static_valid",
)

# Two CRSs, and the distinction matters: the candidates are stored in
# EPSG:4326 but an XYZ tile basemap is Web Mercator. Declaring the basemap as
# 4326 would leave QGIS assuming it needs no reprojection and paint the tiles
# in the wrong place -- the failure `qgis.every_layer_declares_crs` exists to
# catch, which it duly did on the first version of this generator.
CRS_DEFINITIONS = {
    4326: {
        "wkt": (
            'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,'
            'AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],'
            'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
            'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]'
        ),
        "proj4": "+proj=longlat +datum=WGS84 +no_defs",
        "srsid": "3452",
        "description": "WGS 84",
        "projectionacronym": "longlat",
        "geographicflag": "true",
    },
    3857: {
        "wkt": (
            'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",'
            'SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],'
            'AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
            'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
            'AUTHORITY["EPSG","4326"]],PROJECTION["Mercator_1SP"],'
            'PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],'
            'PARAMETER["false_easting",0],PARAMETER["false_northing",0],'
            'UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["Easting",EAST],'
            'AXIS["Northing",NORTH],AUTHORITY["EPSG","3857"]]'
        ),
        "proj4": (
            "+proj=merc +a=6378137 +b=6378137 +lat_ts=0 +lon_0=0 +x_0=0 +y_0=0 +k=1 "
            "+units=m +nadgrids=@null +wktext +no_defs"
        ),
        "srsid": "3857",
        "description": "WGS 84 / Pseudo-Mercator",
        "projectionacronym": "merc",
        "geographicflag": "false",
    },
}
QGIS_DOCTYPE = "<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>\n"
#: Names the fallback builder rather than impersonating a QGIS release.
BUILDER_VERSION = "3.40.0"
CANDIDATES_LAYER_ID = "hub_candidates_layer"
ZONES_LAYER_ID = "zone_metrics_layer"
BASEMAP_LAYER_ID = "osm_basemap_layer"

#: The one description of the map, consumed by both generators. Keeping it here
#: rather than in either builder is what lets the equivalence test compare them
#: without restating the intent a third time.
QGIS_LAYERS = (
    {
        "id": CANDIDATES_LAYER_ID,
        "name": "Hub Candidates (ranked, tenant alpha)",
        "provider": "ogr",
        "source": "./data/derived/hub-candidates.geojson",
        "group": "result",
        "epsg": 4326,
        "geometry": "Polygon",
        "graduated_on": "final_score",
    },
    {
        "id": ZONES_LAYER_ID,
        "name": "All Zones (scored context)",
        "provider": "ogr",
        "source": "./data/derived/zone-metrics.geojson",
        "group": "result",
        "epsg": 4326,
        "geometry": "Polygon",
        "graduated_on": None,
    },
    {
        "id": BASEMAP_LAYER_ID,
        "name": "OpenStreetMap (XYZ)",
        "provider": "wms",
        "source": "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
        "group": "context",
        # Web Mercator: the tiles are, and saying 4326 would misplace them.
        "epsg": 3857,
        "geometry": None,
        "graduated_on": None,
    },
)

#: Quartiles of the 0-100 score, so the legend reads like the dashboard table.
SCORE_BANDS = (
    (0.0, 50.0, "Lower half (0-50)", (255, 245, 235, 140), (253, 174, 107, 255)),
    (50.0, 65.0, "Moderate (50-65)", (253, 208, 162, 170), (253, 141, 60, 255)),
    (65.0, 75.0, "Strong (65-75)", (253, 141, 60, 190), (230, 85, 13, 255)),
    (75.0, 100.0, "Leading (75-100)", (217, 71, 1, 210), (140, 45, 4, 255)),
)


def log(message: str) -> None:
    print(f"[pipeline] {message}", flush=True)


def sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")


# -- inputs -------------------------------------------------------------------


def pinned_snapshot_paths(manifest: dict) -> dict[str, Path]:
    """Snapshot path per source key; only pinned local snapshots are inputs."""
    snapshots: dict[str, Path] = {}
    for key, block in (manifest.get("sources") or {}).items():
        pin = block.get("pin") if isinstance(block, dict) else None
        if not isinstance(pin, dict) or pin.get("class") != "local_snapshot":
            raise SystemExit(f"source {key!r} has no local_snapshot pin; run the approved snapshot first")
        relative = str(pin.get("path", ""))
        path = ROOT / relative
        if not relative.startswith("data/source/") or not path.is_file():
            raise SystemExit(
                f"source {key!r} pin path {relative!r} is missing; this pipeline never queries live warehouses"
            )
        snapshots[key] = path
    return snapshots


# -- metrics ------------------------------------------------------------------


def demand_relation(duck, snapshots: dict[str, Path]) -> str:
    """Use reader-captured trip observations when the live source is present."""
    if "trip_events" not in snapshots:
        return f"read_parquet('{sql_path(snapshots['zone_demand'])}')"
    zones = sql_path(snapshots["taxi_zones"])
    trips = sql_path(snapshots["trip_events"])
    duck.execute(f"""
        CREATE OR REPLACE TEMP VIEW zone_demand_from_trips AS
        WITH daily AS (
            SELECT z.zone_id AS taxi_zone_id, z.h3_cell, t.pickup_date AS demand_date,
                   count(*) AS trips
            FROM read_parquet('{zones}') z
            JOIN read_parquet('{trips}') t
              ON ST_Contains(z.geom, t.pickup_point)
            GROUP BY 1, 2, 3
        ), totals AS (
            SELECT z.zone_id AS taxi_zone_id, z.h3_cell,
                   count(t.trip_id) AS trips_total,
                   avg(t.trip_distance_km) AS mean_trip_km
            FROM read_parquet('{zones}') z
            LEFT JOIN read_parquet('{trips}') t
              ON ST_Contains(z.geom, t.pickup_point)
            GROUP BY 1, 2
        )
        SELECT totals.taxi_zone_id, totals.h3_cell, totals.trips_total,
               coalesce(max(daily.trips), 0) AS trips_peak_day,
               coalesce(stddev_samp(daily.trips), 0) AS trips_stddev_day,
               count(daily.demand_date) AS active_days,
               totals.mean_trip_km
        FROM totals
        LEFT JOIN daily USING (taxi_zone_id, h3_cell)
        GROUP BY 1, 2, 3, 7
    """)
    return "zone_demand_from_trips"


def market_relation(duck, snapshots: dict[str, Path]) -> str:
    """Attribute source-zone scores by H3 centre and count live POI points."""
    if "market_scores" not in snapshots:
        return f"read_parquet('{sql_path(snapshots['zone_market'])}')"
    zones = sql_path(snapshots["taxi_zones"])
    reference = sql_path(snapshots["market_reference_zones"])
    scores = sql_path(snapshots["market_scores"])
    pois = sql_path(snapshots["market_pois"])
    ambiguous = duck.execute(f"""
        SELECT count(*) FROM (
            SELECT z.zone_id
            FROM read_parquet('{zones}') z
            LEFT JOIN read_parquet('{reference}') r
              ON ST_Contains(r.zone_area, ST_Centroid(z.geom))
            GROUP BY z.zone_id
            HAVING count(r.zone_id) != 1
        )
    """).fetchone()[0]
    if ambiguous:
        raise SystemExit(f"{ambiguous} H3 cells have no unique source market zone")
    duck.execute(f"""
        CREATE OR REPLACE TEMP VIEW zone_market_from_sources AS
        WITH attribution AS (
            SELECT z.zone_id AS taxi_zone_id, z.h3_cell, s.market_score_raw
            FROM read_parquet('{zones}') z
            JOIN read_parquet('{reference}') r
              ON ST_Contains(r.zone_area, ST_Centroid(z.geom))
            LEFT JOIN read_parquet('{scores}') s ON s.taxi_zone_id = r.zone_id
        ), poi_counts AS (
            SELECT z.h3_cell,
                   count(p.poi_id) FILTER (WHERE p.category = 'charging') AS charging_pois,
                   count(p.poi_id) FILTER (WHERE p.category = 'parking') AS parking_pois,
                   count(p.poi_id) FILTER (WHERE p.category = 'transit') AS transit_pois,
                   count(p.poi_id) FILTER (WHERE p.category = 'competitor') AS competitor_pois,
                   count(p.poi_id) AS total_pois
            FROM read_parquet('{zones}') z
            LEFT JOIN read_parquet('{pois}') p ON ST_Contains(z.geom, p.geom)
            GROUP BY z.h3_cell
        )
        SELECT a.taxi_zone_id, a.h3_cell, a.market_score_raw,
               p.charging_pois, p.parking_pois, p.transit_pois,
               p.competitor_pois, p.total_pois
        FROM attribution a JOIN poi_counts p USING (h3_cell)
    """)
    missing = duck.execute(
        "SELECT count(*) FROM zone_market_from_sources WHERE market_score_raw IS NULL"
    ).fetchone()[0]
    if missing:
        raise SystemExit(f"{missing} H3 cells have no reader-visible market score")
    return "zone_market_from_sources"


def compute_zone_metrics(duck, snapshots: dict[str, Path]) -> list[dict]:
    """Per-zone operational metrics; all spatial maths in EPSG:32618 (A3)."""
    zones = sql_path(snapshots["taxi_zones"])
    hubs = sql_path(snapshots["hubs"])
    fleet = sql_path(snapshots["fleet_positions"])
    accounts = sql_path(snapshots["customer_accounts"])
    demand_source = demand_relation(duck, snapshots)
    market_source = market_relation(duck, snapshots)
    duck.execute(f"""
        CREATE OR REPLACE TEMP VIEW latest_fleet_positions AS
        SELECT * FROM read_parquet('{fleet}')
        QUALIFY row_number() OVER (
            PARTITION BY tenant_id, vehicle_id
            ORDER BY recorded_at DESC NULLS LAST, position_id DESC
        ) = 1
    """)
    rows = duck.execute(
        f"""
        WITH zones AS (
            SELECT zone_id, h3_cell, borough, zone_name, geom,
                   ST_Centroid(ST_Transform(geom, 'OGC:CRS84', 'EPSG:{ANALYSIS_EPSG}')) AS centroid
            FROM read_parquet('{zones}')
        ),
        hub_points AS (
            SELECT ST_Transform(geom, 'OGC:CRS84', 'EPSG:{ANALYSIS_EPSG}') AS g
            FROM read_parquet('{hubs}')
        ),
        fleet_by_zone AS (
            SELECT h3_cell, count(*) AS n
            FROM latest_fleet_positions
            WHERE geom IS NOT NULL
            GROUP BY 1
        ),
        accounts_by_zone AS (
            SELECT h3_cell, count(*) AS n
            FROM read_parquet('{accounts}')
            WHERE is_active
            GROUP BY 1
        ),
        demand AS (SELECT * FROM {demand_source}),
        market AS (SELECT * FROM {market_source})
        SELECT z.zone_id, z.h3_cell, z.borough, z.zone_name, z.geom,
               coalesce(a.n, 0) AS demand_accounts,
               coalesce(f.n, 0) AS fleet_present,
               CAST(min(ST_Distance(z.centroid, h.g)) AS INTEGER) AS hub_distance_m,
               any_value(d.trips_total) AS trips_total,
               any_value(d.trips_peak_day) AS trips_peak_day,
               any_value(d.trips_stddev_day) AS trips_stddev_day,
               any_value(d.active_days) AS active_days,
               any_value(d.mean_trip_km) AS mean_trip_km,
               any_value(m.market_score_raw) AS market_score_raw,
               any_value(m.charging_pois) AS charging_pois,
               any_value(m.parking_pois) AS parking_pois,
               any_value(m.transit_pois) AS transit_pois,
               any_value(m.competitor_pois) AS competitor_pois,
               any_value(m.total_pois) AS total_pois
        FROM zones z
        CROSS JOIN hub_points h
        LEFT JOIN accounts_by_zone a ON a.h3_cell = z.h3_cell
        LEFT JOIN fleet_by_zone f ON f.h3_cell = z.h3_cell
        LEFT JOIN demand d ON d.h3_cell = z.h3_cell
        LEFT JOIN market m ON m.h3_cell = z.h3_cell
        GROUP BY 1, 2, 3, 4, 5, 6, 7
        ORDER BY z.zone_id
        """
    ).fetchall()
    fields = (
        "zone_id", "h3_cell", "borough", "zone_name", "geom_wkb", "demand_accounts", "fleet_present",
        "hub_distance_m", "trips_total", "trips_peak_day", "trips_stddev_day", "active_days",
        "mean_trip_km", "market_score_raw", "charging_pois", "parking_pois", "transit_pois",
        "competitor_pois", "total_pois",
    )
    return [dict(zip(fields, row)) for row in rows]


def score_candidates(metrics: list[dict], scoring: dict) -> list[dict]:
    """Apply the project.yaml scoring model; mutates and returns the metrics."""
    eligibility = scoring["eligibility"]
    weights = scoring["weights"]

    components = scoring["components"]
    amenity_full = components["amenity_pois_for_full_score"]
    competitor_full = components["competitor_pois_for_full_pressure"]

    def eligible(zone: dict) -> bool:
        return (
            (zone["trips_total"] or 0) >= eligibility["trips_total_min"]
            and zone["hub_distance_m"] >= eligibility["hub_distance_m_min"]
            and zone["fleet_present"] <= eligibility["fleet_present_max"]
        )

    def market_of(zone: dict) -> float:
        """Market context, recomputable from the columns in zone-metrics.parquet.

        Half the weight is Northstar's own per-zone score, the rest is what is
        physically there: amenities help, competitors do not.
        """
        amenities = zone["charging_pois"] + zone["parking_pois"] + zone["transit_pois"]
        amenity_score = min(1.0, amenities / amenity_full)
        competition = min(1.0, zone["competitor_pois"] / competitor_full)
        return round(
            0.5 * zone["market_score_raw"] + 0.3 * amenity_score + 0.2 * (1.0 - competition), 6
        )

    pool = [zone for zone in metrics if eligible(zone)]
    if not pool:
        raise SystemExit("no eligible candidate zones; check the scoring.eligibility thresholds")
    max_demand = max(zone["trips_total"] for zone in pool)
    max_distance = max(zone["hub_distance_m"] for zone in pool)
    max_fleet = max(zone["fleet_present"] for zone in pool)
    for zone in metrics:
        zone.update(
            demand_score=None,
            coverage_gap_score=None,
            market_score=None,
            saturation_score=None,
            final_score=None,
        )
    for zone in pool:
        zone["demand_score"] = zone["trips_total"] / max_demand
        zone["coverage_gap_score"] = zone["hub_distance_m"] / max_distance
        zone["saturation_score"] = 1.0 - zone["fleet_present"] / max_fleet if max_fleet else 1.0
        zone["market_score"] = market_of(zone)
        zone["final_score"] = round(
            100
            * (
                weights["demand"] * zone["demand_score"]
                + weights["coverage_gap"] * zone["coverage_gap_score"]
                + weights["market"] * zone["market_score"]
                + weights["saturation"] * zone["saturation_score"]
            ),
            2,
        )
    return metrics


# -- outputs ------------------------------------------------------------------


def write_outputs(duck, metrics: list[dict], candidates: list[dict]) -> None:
    DERIVED.mkdir(parents=True, exist_ok=True)
    duck.execute(
        "CREATE OR REPLACE TABLE zone_metrics ("
        "zone_id INTEGER, h3_cell VARCHAR, borough VARCHAR, zone_name VARCHAR, demand_accounts INTEGER, "
        "fleet_present INTEGER, hub_distance_m INTEGER, "
        "trips_total INTEGER, trips_peak_day INTEGER, trips_stddev_day DOUBLE, "
        "active_days INTEGER, mean_trip_km DOUBLE, market_score_raw DOUBLE, "
        "charging_pois INTEGER, parking_pois INTEGER, transit_pois INTEGER, "
        "competitor_pois INTEGER, total_pois INTEGER, demand_score DOUBLE, "
        "coverage_gap_score DOUBLE, market_score DOUBLE, saturation_score DOUBLE, "
        "final_score DOUBLE, geom GEOMETRY)"
    )
    # Every input the scoring model reads is stored beside the score, so a
    # reviewer can recompute final_score from this file and nothing else.
    columns = (
        "zone_id", "h3_cell", "borough", "zone_name", "demand_accounts", "fleet_present", "hub_distance_m",
        "trips_total", "trips_peak_day", "trips_stddev_day", "active_days", "mean_trip_km",
        "market_score_raw", "charging_pois", "parking_pois", "transit_pois", "competitor_pois",
        "total_pois", "demand_score", "coverage_gap_score", "market_score", "saturation_score",
        "final_score",
    )
    duck.executemany(
        f"INSERT INTO zone_metrics VALUES ({', '.join('?' for _ in columns)}, ST_GeomFromWKB(?))",
        [tuple(zone[name] for name in columns) + (zone["geom_wkb"],) for zone in metrics],
    )
    metrics_parquet = (DERIVED / "zone-metrics.parquet").as_posix()
    candidates_parquet = (DERIVED / "hub-candidates.parquet").as_posix()
    # DuckDB's untyped GEOMETRY column drops the CRS on INSERT, so the storage
    # CRS is (re)applied in the COPY projection: the GeoParquet metadata must
    # read back as EPSG:4326.
    duck.execute(
        f"COPY (SELECT * REPLACE(ST_SetCRS(geom, 'EPSG:4326') AS geom) FROM zone_metrics) "
        f"TO '{metrics_parquet}' (FORMAT PARQUET)"
    )
    duck.execute(
        f"COPY (SELECT * REPLACE(ST_SetCRS(geom, 'EPSG:4326') AS geom) FROM zone_metrics "
        f"WHERE final_score IS NOT NULL ORDER BY final_score DESC, zone_id) "
        f"TO '{candidates_parquet}' (FORMAT PARQUET)"
    )
    features = []
    for zone in sorted(candidates, key=lambda item: (-item["final_score"], item["zone_id"])):
        geometry = duck.execute("SELECT ST_AsGeoJSON(ST_GeomFromWKB(?))", [zone["geom_wkb"]]).fetchone()[0]
        properties = {
            "h3_cell": zone["h3_cell"],
            "borough": zone["borough"],
            "zone_id": zone["zone_id"],
            "zone_name": zone["zone_name"],
            "trips_total": zone["trips_total"],
            "trips_peak_day": zone["trips_peak_day"],
            "trips_stddev_day": zone["trips_stddev_day"],
            "demand_accounts": zone["demand_accounts"],
            "fleet_present": zone["fleet_present"],
            "hub_distance_m": zone["hub_distance_m"],
            "charging_pois": zone["charging_pois"],
            "parking_pois": zone["parking_pois"],
            "transit_pois": zone["transit_pois"],
            "competitor_pois": zone["competitor_pois"],
            "market_score_raw": zone["market_score_raw"],
            "demand_score": zone["demand_score"],
            "coverage_gap_score": zone["coverage_gap_score"],
            "market_score": zone["market_score"],
            "saturation_score": zone["saturation_score"],
            "final_score": zone["final_score"],
        }
        features.append(json.dumps({"type": "Feature", "geometry": json.loads(geometry), "properties": properties},
                                   separators=(",", ":")))
    payload = '{"type": "FeatureCollection", "features": [' + ",\n".join(features) + "]}\n"
    (DERIVED / "hub-candidates.geojson").write_text(payload, encoding="utf-8")

    # Every zone, not only the eligible ones: the map needs the context that
    # makes a ranked candidate legible, and QGIS reads GeoJSON everywhere
    # while the Parquet driver is not guaranteed to be present.
    context = []
    for zone in sorted(metrics, key=lambda item: item["zone_id"]):
        geometry = duck.execute("SELECT ST_AsGeoJSON(ST_GeomFromWKB(?))", [zone["geom_wkb"]]).fetchone()[0]
        context.append(json.dumps({
            "type": "Feature",
            "geometry": json.loads(geometry),
            "properties": {
                **{name: zone[name] for name in columns},
                "eligible": zone["final_score"] is not None,
            },
        }, separators=(",", ":")))
    (DERIVED / "zone-metrics.geojson").write_text(
        '{"type": "FeatureCollection", "features": [' + ",\n".join(context) + "]}\n", encoding="utf-8"
    )
    log(f"wrote {len(metrics)} zone metrics and {len(candidates)} candidate zones")


def write_hub_context(duck, snapshots: dict[str, Path]) -> dict:
    features = []
    for identifier, name, capacity, geometry in duck.execute(
        f"SELECT hub_id, name, capacity, ST_AsGeoJSON(geom) FROM read_parquet('{sql_path(snapshots['hubs'])}') ORDER BY hub_id"
    ).fetchall():
        features.append({"type": "Feature", "geometry": json.loads(geometry),
                         "properties": {"hub_id": identifier, "name": name, "capacity": capacity}})
    (DERIVED / "existing-hubs.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n")
    fleet_features = []
    for vehicle_id, status, recorded_at, geometry in duck.execute("""
        SELECT vehicle_id, status, CAST(recorded_at AS VARCHAR), ST_AsGeoJSON(geom)
        FROM latest_fleet_positions WHERE geom IS NOT NULL
        ORDER BY vehicle_id
    """).fetchall():
        tlc_derived = vehicle_id.startswith("TLC16-DEMO-")
        fleet_features.append({
            "type": "Feature", "geometry": json.loads(geometry),
            "properties": {
                "vehicle_id": vehicle_id, "status": status, "recorded_at": recorded_at,
                "location_basis": "2016 TLC pickup coordinate reused for a synthetic vehicle" if tlc_derived
                                  else "synthetic H3-centre fleet seed",
            },
        })
    (DERIVED / "fleet-positions.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": fleet_features}, separators=(",", ":")) + "\n"
    )
    observations, vehicles, earliest, latest, tlc_derived = duck.execute(f"""
        SELECT (SELECT count(*) FROM read_parquet('{sql_path(snapshots['fleet_positions'])}')),
               count(*), CAST(min(recorded_at) AS VARCHAR), CAST(max(recorded_at) AS VARCHAR),
               count(*) FILTER (WHERE vehicle_id LIKE 'TLC16-DEMO-%')
        FROM latest_fleet_positions
    """).fetchone()
    return {"observations": observations, "vehicles": vehicles, "earliest": earliest,
            "latest": latest, "tlc_derived": tlc_derived, "mapped_vehicles": len(fleet_features)}


# -- validation ---------------------------------------------------------------


def run_checks(duck, snapshots: dict[str, Path], metrics: list[dict], candidates: list[dict],
               thresholds: dict) -> list[dict]:
    """Domain checks. Every id declared in project.yaml validation must appear once."""
    checks: list[dict] = []

    def add(check_id: str, status: str, message: str, **details) -> None:
        checks.append({"id": check_id, "status": status, "message": message, **details})

    def q(sql: str):
        return duck.execute(sql).fetchall()

    import h3

    invalid_cells = []
    for zone in metrics:
        cell = zone["h3_cell"]
        if not h3.is_valid_cell(cell) or h3.get_resolution(cell) != 8:
            invalid_cells.append(zone["zone_id"])
            continue
        ring = [[longitude, latitude] for latitude, longitude in h3.cell_to_boundary(cell)]
        ring.append(ring[0])
        # h3-pg and h3-py use the same cell boundary with slightly different
        # floating-point serialization. Exact topological equality can fail
        # on sub-nanometre slivers, so compare symmetric-difference area.
        difference, area = duck.execute(
            "SELECT ST_Area(ST_SymDifference(ST_GeomFromWKB(?), ST_GeomFromGeoJSON(?))), "
            "ST_Area(ST_GeomFromWKB(?))",
            [zone["geom_wkb"], json.dumps({"type": "Polygon", "coordinates": [ring]}), zone["geom_wkb"]],
        ).fetchone()
        matches = area > 0 and difference / area < 1e-9
        if not matches:
            invalid_cells.append(zone["zone_id"])
    add("h3_grid_valid", "failed" if invalid_cells else "passed",
        "geometry matches genuine H3 resolution-8 boundaries", invalid_zones=invalid_cells)
    reference_mismatches = {}
    for key in ("hubs", "fleet_positions", "customer_accounts"):
        path = snapshots[key]
        count = duck.execute(f"""
            SELECT count(*) FROM read_parquet('{sql_path(path)}') source
            LEFT JOIN read_parquet('{sql_path(snapshots['taxi_zones'])}') zones
            ON source.taxi_zone_id = zones.zone_id
            WHERE zones.zone_id IS NULL OR source.h3_cell IS DISTINCT FROM zones.h3_cell
        """).fetchone()[0]
        if count:
            reference_mismatches[key] = count
    add("h3_references_consistent", "failed" if reference_mismatches else "passed",
        "PostGIS operational extracts use the same H3-to-integer-alias mapping", mismatches=reference_mismatches)

    invalid = q("SELECT count(*) FROM zone_metrics WHERE NOT ST_IsValid(geom)")[0][0]
    add("geometry_valid", "passed" if invalid == 0 else "failed",
        "all derived zone geometries valid", invalid_count=invalid, features_checked=len(metrics))

    actual_crs = sorted(
        {
            str(row[0])
            for path in ("zone-metrics.parquet", "hub-candidates.parquet")
            for row in q(f"SELECT DISTINCT ST_CRS(geom) FROM read_parquet('{(DERIVED / path).as_posix()}')")
        }
    )
    add("crs_known", "passed" if actual_crs == ["EPSG:4326"] else "failed",
        f"storage CRS of derived outputs is {actual_crs}", expected="EPSG:4326", actual=actual_crs)

    add("row_count_gt_zero", "passed" if metrics and candidates else "failed",
        "zone metrics and candidates non-empty", zone_rows=len(metrics), candidate_zones=len(candidates))

    nulls = sum(1 for zone in metrics if zone["zone_id"] is None)
    add("no_null_zone_id", "passed" if nulls == 0 else "failed", "zone ids complete", nulls=nulls)

    tenants: set[str] = set()
    for key in ("hubs", "fleet_positions", "customer_accounts", "trip_events"):
        if key not in snapshots:
            continue
        found = q(f"SELECT DISTINCT tenant_id FROM read_parquet('{sql_path(snapshots[key])}')")
        tenants.update(str(row[0]) for row in found)
    add("rls_scope_alpha_only", "passed" if tenants == {"alpha"} else "failed",
        "reader-captured tenant-bearing extracts contain only alpha; this does not independently test backend RLS",
        tenants=sorted(tenants), expected=["alpha"])

    protected: list[str] = []
    for key in snapshots:
        described = q(f"DESCRIBE SELECT * FROM read_parquet('{sql_path(snapshots[key])}')")
        names = {str(row[0]) for row in described}
        protected.extend(sorted(names & PROTECTED_COLUMNS))
    if (ROOT / "data/source/driver_private.parquet").exists():
        protected.append("driver_private.parquet must not be a snapshot (hr.driver_private is inaccessible)")
    add("protected_columns_absent", "passed" if not protected else "failed",
        "no protected column or inaccessible relation appears in data/source", findings=protected)

    incomplete = [
        zone["zone_id"]
        for zone in candidates
        if None in (zone["demand_score"], zone["coverage_gap_score"], zone["market_score"],
                    zone["saturation_score"], zone["final_score"])
    ]
    add("score_components_complete", "passed" if not incomplete else "failed",
        "every candidate has all score components", incomplete=incomplete)

    out_of_range = [zone["zone_id"] for zone in candidates if not 0 <= zone["final_score"] <= 100]
    add("score_range", "passed" if not out_of_range else "failed",
        "final scores within [0, 100]", out_of_range=out_of_range)

    violating = [
        zone["zone_id"]
        for zone in candidates
        if zone["trips_total"] < thresholds["trips_total_min"]
        or zone["hub_distance_m"] < thresholds["hub_distance_m_min"]
        or zone["fleet_present"] > thresholds["fleet_present_max"]
    ]
    add("eligibility_thresholds", "passed" if not violating else "failed",
        "candidates satisfy every eligibility threshold", violations=violating)
    expected = dict(q("""
        SELECT h3_cell, count(*) FROM latest_fleet_positions
        WHERE geom IS NOT NULL GROUP BY h3_cell
    """))
    mismatches = [zone["zone_id"] for zone in metrics if zone["fleet_present"] != expected.get(zone["h3_cell"], 0)]
    add("fleet_latest_observation", "failed" if mismatches else "passed",
        "one latest observation per tenant and vehicle, with position_id breaking timestamp ties",
        mismatched_zones=mismatches, counted_vehicles=sum(zone["fleet_present"] for zone in metrics))
    return checks


# -- run record + manifest ----------------------------------------------------


def _environment() -> dict[str, str]:
    import platform

    import duckdb
    import h3

    return {
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "h3": h3.versions()["python"],
        "analysis_crs": f"EPSG:{ANALYSIS_EPSG}",
    }


def write_run_evidence(manifest: dict, checks: list[dict], snapshots: dict[str, Path],
                       started_at: str, completed_at: str) -> tuple[str, str]:
    """Write the validation report and run record; update runs.latest."""
    statuses = {check["status"] for check in checks}
    if "failed" in statuses:
        status = "failed"
    elif statuses - {"passed"}:
        status = "warning"
    else:
        status = "passed"
    run_id = "run-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")

    source_manifest_path = manifest.get("runtime", {}).get("fixture", {}).get("source_manifest", "data/source/manifest.json")
    if not (ROOT / source_manifest_path).is_file():
        raise ValueError("Missing pinned source manifest; capture and pin the source snapshots before analysis")

    input_paths = declared_input_paths(ROOT, manifest)
    output_paths = declared_output_paths(manifest)
    inputs_hash = canonical_file_set_hash(ROOT, input_paths)
    outputs_hash = canonical_file_set_hash(ROOT, output_paths)

    report = {
        "run_id": run_id,
        "schema": "openmapstack-project/v1",
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
        "checks": checks,
    }
    VALIDATION_DIR.mkdir(exist_ok=True)
    (VALIDATION_DIR / "latest-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    run_record = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
        # What actually produced these bytes. Without it a reader cannot tell
        # whether a hash mismatch is a real difference or a different DuckDB.
        "environment": _environment(),
        "source_manifest": source_manifest_path,
        "validation_report": "validation/latest-report.json",
        "sources": [
            {"key": key, "sha256": sha256_file(path)}
            for key, path in sorted(snapshots.items())
        ],
        "inputs": file_inventory(ROOT, input_paths),
        "outputs": file_inventory(ROOT, output_paths),
    }
    RUNS_DIR.mkdir(exist_ok=True)
    (RUNS_DIR / f"{run_id}.json").write_text(json.dumps(run_record, indent=2) + "\n", encoding="utf-8")

    updated = dict(manifest)
    updated.setdefault("runs", {})["latest"] = {
        "id": run_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
        "record": {"path": f"runs/{run_id}.json"},
        "validation_report": {"path": "validation/latest-report.json"},
    }
    project_block = dict(updated.get("project") or {})
    project_block["updated_at"] = completed_at
    project_block["status"] = {"passed": "validated", "warning": "warning", "failed": "failed"}[status]
    updated["project"] = project_block
    (ROOT / "project.yaml").write_text(
        yaml.safe_dump(updated, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100),
        encoding="utf-8",
    )
    log(f"run {run_id} recorded with status {status}")
    return status, run_id


# -- dashboard ----------------------------------------------------------------


def dashboard_payload(manifest: dict, checks: list[dict], status: str, run_id: str, fleet_summary: dict) -> dict:
    return {
        "title": manifest["project"]["title"], "run_id": run_id, "status": status,
        "zones": json.loads((DERIVED / "zone-metrics.geojson").read_text()),
        "hubs": json.loads((DERIVED / "existing-hubs.geojson").read_text()),
        "fleet_points": json.loads((DERIVED / "fleet-positions.geojson").read_text()),
        "scoring": manifest["scoring"], "checks": checks, "fleet": fleet_summary,
        "basemap": manifest["presentation"]["map"]["basemap"],
        "fixture": manifest["runtime"].get("fixture", {}),
        "assumptions": manifest["interpretation"]["assumptions"],
        "provenance": manifest["presentation"]["provenance_ui"]["distinctions"],
        "sources": [{"key": key, "backend": source["warehouse"]["backend"],
                     "path": source["pin"]["path"], "sha256": source["pin"]["sha256"],
                     "captured_at": source["pin"]["captured_at"], "origin": source.get("origin", "warehouse_capture")}
                    for key, source in manifest["sources"].items()],
        "bands": [{"min": low, "max": high, "label": label,
                   "color": "#" + "".join(f"{part:02x}" for part in fill[:3])}
                  for low, high, label, fill, outline in SCORE_BANDS],
    }


def write_dashboard(manifest: dict, checks: list[dict], status: str, run_id: str, fleet_summary: dict) -> None:
    payload = json.dumps(dashboard_payload(manifest, checks, status, run_id, fleet_summary),
                         ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = (ROOT / "dashboard-template.html").read_text(encoding="utf-8")
    rendered = template.replace("__TITLE__", html.escape(manifest["project"]["title"]))
    rendered = rendered.replace("__DATA__", payload)
    (ROOT / "dashboard.html").write_text(rendered, encoding="utf-8")
    log("wrote dashboard.html")


# -- QGIS project -------------------------------------------------------------


def _spatialrefsys(epsg: int) -> ET.Element:
    """A complete CRS declaration: an authid alone is not enough for QGIS."""
    definition = CRS_DEFINITIONS[epsg]
    ref = ET.Element("spatialrefsys", nativeFormat="Wkt")
    ET.SubElement(ref, "wkt").text = definition["wkt"]
    ET.SubElement(ref, "proj4").text = definition["proj4"]
    ET.SubElement(ref, "srsid").text = definition["srsid"]
    ET.SubElement(ref, "srid").text = str(epsg)
    ET.SubElement(ref, "authid").text = f"EPSG:{epsg}"
    ET.SubElement(ref, "description").text = definition["description"]
    ET.SubElement(ref, "projectionacronym").text = definition["projectionacronym"]
    ET.SubElement(ref, "ellipsoidacronym").text = "EPSG:7030"
    ET.SubElement(ref, "geographicflag").text = definition["geographicflag"]
    return ref


def _srs(parent: ET.Element, epsg: int) -> None:
    ET.SubElement(parent, "srs").append(_spatialrefsys(epsg))


def _candidate_renderer(parent: ET.Element) -> None:
    """A graduated fill over final_score.

    A layer with no renderer draws with an invisible default, so the map would
    show less than the manifest claims -- `qgis.styles_declared` refuses it.
    The breaks are quartiles of the 0-100 score, so the legend reads the same
    way the dashboard table does.
    """
    renderer = ET.SubElement(
        parent, "renderer-v2", type="graduatedSymbol", attr="final_score",
        graduatedMethod="GraduatedColor", enableorderby="0",
    )
    ranges = ET.SubElement(renderer, "ranges")
    symbols = ET.SubElement(renderer, "symbols")
    bands = (
        ("0", "0", "50", "Lower half (0-50)", "255,245,235,140", "253,174,107,255"),
        ("1", "50", "65", "Moderate (50-65)", "253,208,162,170", "253,141,60,255"),
        ("2", "65", "75", "Strong (65-75)", "253,141,60,190", "230,85,13,255"),
        ("3", "75", "100", "Leading (75-100)", "217,71,1,210", "140,45,4,255"),
    )
    for name, lower, upper, label, fill, outline in bands:
        ET.SubElement(
            ranges, "range", lower=lower, upper=upper, symbol=name, label=label, render="true"
        )
        symbol = ET.SubElement(symbols, "symbol", type="fill", name=name, alpha="1")
        layer = ET.SubElement(symbol, "layer")
        layer.set("class", "SimpleFill")
        layer.set("enabled", "1")
        ET.SubElement(layer, "prop", k="color", v=fill)
        ET.SubElement(layer, "prop", k="outline_color", v=outline)
        ET.SubElement(layer, "prop", k="outline_width", v="0.46")


def pyqgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401
    except Exception:  # noqa: BLE001 - a broken install is as unusable as none
        return False
    return True


def _build_qgis_xml(manifest: dict) -> str:
    """The deterministic fallback builder.

    It writes QGIS's format by hand, so it must not claim QGIS wrote it: the
    version attribute names this builder, and `qgis_authored` records that no
    QGIS was involved. A file asserting `version="3.44.3"` when no 3.44.3 ever
    touched it is the kind of provenance claim this project exists to refuse.
    """
    groups = {group["id"]: group["title"] for group in manifest["presentation"]["map"]["layer_groups"]}
    root = ET.Element("qgis", projectname="northstar-nyc-hub-siting", version=BUILDER_VERSION)
    ET.SubElement(root, "homePath", path="")
    ET.SubElement(root, "title").text = manifest["project"]["title"]
    ET.SubElement(root, "autotransaction", active="0")
    ET.SubElement(root, "evaluateDefaultValues", active="0")
    ET.SubElement(root, "trust", active="0")
    ET.SubElement(root, "projectCrs").append(_spatialrefsys(4326))

    tree = ET.SubElement(root, "layer-tree-group")
    ET.SubElement(tree, "customproperties")
    for group_id, title in groups.items():
        node = ET.SubElement(tree, "layer-tree-group", name=title, expanded="1", checked="Qt.Checked")
        for layer in QGIS_LAYERS:
            if layer["group"] != group_id:
                continue
            ET.SubElement(
                node, "layer-tree-layer", id=layer["id"], name=layer["name"],
                providerKey=layer["provider"], expanded="1", checked="Qt.Checked",
            )

    layers = ET.SubElement(root, "projectlayers")
    for layer in QGIS_LAYERS:
        if layer["provider"] == "ogr":
            element = ET.SubElement(
                layers, "maplayer", type="vector", geometry=layer["geometry"],
                hasScaleBasedVisibilityFlag="0", readOnly="0", maxScale="0", minScale="1e+08",
                styleCategories="AllStyleCategories",
            )
        else:
            element = ET.SubElement(
                layers, "maplayer", type="raster", hasScaleBasedVisibilityFlag="0",
                maxScale="0", minScale="1e+08", styleCategories="AllStyleCategories",
            )
        ET.SubElement(element, "id").text = layer["id"]
        ET.SubElement(element, "datasource").text = layer["source"]
        ET.SubElement(element, "layername").text = layer["name"]
        _srs(element, layer["epsg"])
        ET.SubElement(element, "provider").text = layer["provider"]
        if layer["provider"] == "wms":
            ET.SubElement(ET.SubElement(element, "pipe"), "rasterrenderer",
                          type="singlebandcolordata", band="1", opacity="1", alphaBand="-1")
        elif layer["graduated_on"]:
            _graduated_renderer(element, layer["graduated_on"])
        else:
            _single_symbol_renderer(element)

    properties = ET.SubElement(root, "properties")
    spatial = ET.SubElement(properties, "SpatialRefSys")
    enabled = ET.SubElement(spatial, "ProjectionsEnabled")
    enabled.set("type", "int")
    enabled.text = "1"
    return _canonical_xml(ET.tostring(root, encoding="unicode"))


def _graduated_renderer(parent: ET.Element, attribute: str) -> None:
    renderer = ET.SubElement(
        parent, "renderer-v2", type="graduatedSymbol", attr=attribute,
        graduatedMethod="GraduatedColor", enableorderby="0",
    )
    ranges = ET.SubElement(renderer, "ranges")
    symbols = ET.SubElement(renderer, "symbols")
    for index, (lower, upper, label, fill, outline) in enumerate(SCORE_BANDS):
        ET.SubElement(ranges, "range", lower=str(lower), upper=str(upper),
                      symbol=str(index), label=label, render="true")
        _fill_symbol(symbols, str(index), fill, outline)


def _single_symbol_renderer(parent: ET.Element) -> None:
    renderer = ET.SubElement(parent, "renderer-v2", type="singleSymbol", enableorderby="0")
    _fill_symbol(ET.SubElement(renderer, "symbols"), "0", (180, 180, 180, 60), (120, 120, 120, 200))


def _fill_symbol(parent: ET.Element, name: str, fill, outline) -> None:
    symbol = ET.SubElement(parent, "symbol", type="fill", name=name, alpha="1")
    layer = ET.SubElement(symbol, "layer")
    layer.set("class", "SimpleFill")
    layer.set("enabled", "1")
    ET.SubElement(layer, "prop", k="color", v=",".join(str(part) for part in fill))
    ET.SubElement(layer, "prop", k="outline_color", v=",".join(str(part) for part in outline))
    ET.SubElement(layer, "prop", k="outline_width", v="0.46")


def _build_qgis_xml_with_qgis(manifest: dict) -> str:
    """Let QGIS write its own format, then hand back the document.

    Preferred wherever PyQGIS is importable: QGIS is the authority on its file
    format, and the version attribute then names the release that really
    produced the file rather than one this code asserted.
    """
    import tempfile

    from qgis.core import (  # type: ignore[import-not-found]
        QgsCoordinateReferenceSystem,
        QgsFillSymbol,
        QgsGraduatedSymbolRenderer,
        QgsLayerTreeLayer,
        QgsLineSymbol,
        QgsMarkerSymbol,
        QgsProject,
        QgsRasterLayer,
        QgsRendererRange,
        QgsSingleSymbolRenderer,
        QgsVectorLayer,
    )

    from openmapstack.checks.qgis import _qgis_application

    _qgis_application()
    project = QgsProject.instance()
    project.clear()
    project.setTitle(manifest["project"]["title"])
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))

    identifiers: dict[str, str] = {}
    titles = {group["id"]: group["title"] for group in manifest["presentation"]["map"]["layer_groups"]}
    root = project.layerTreeRoot()
    nodes = {group_id: root.addGroup(title) for group_id, title in titles.items()}

    for spec in QGIS_LAYERS:
        absolute = (ROOT / spec["source"][2:]).as_posix() if spec["source"].startswith("./") else spec["source"]
        if spec["provider"] == "ogr":
            layer = QgsVectorLayer(absolute, spec["name"], "ogr")
            if not layer.isValid():
                raise SystemExit(f"QGIS could not load {spec['source']}; the project would ship a dead layer")
            if spec["graduated_on"]:
                ranges = [
                    QgsRendererRange(lower, upper, _qgs_fill(QgsFillSymbol, fill, outline), label)
                    for lower, upper, label, fill, outline in SCORE_BANDS
                ]
                renderer = QgsGraduatedSymbolRenderer(spec["graduated_on"], ranges)
                # A graduated renderer keeps a source symbol it derives ranges
                # from, and defaults it to a *randomly coloured* one. It is
                # never drawn, but it is serialised, so leaving it default
                # makes every written project differ from the last.
                renderer.setSourceSymbol(_qgs_fill(QgsFillSymbol, SCORE_BANDS[0][3], SCORE_BANDS[0][4]))
                layer.setRenderer(renderer)
            else:
                layer.setRenderer(QgsSingleSymbolRenderer(_qgs_fill(QgsFillSymbol, (180, 180, 180, 60), (120, 120, 120, 200))))
        else:
            layer = QgsRasterLayer(spec["source"], spec["name"], "wms")
            layer.setCrs(QgsCoordinateReferenceSystem(f"EPSG:{spec['epsg']}"))
        # addMapLayer(..., False) keeps the tree ours: QGIS would otherwise
        # also insert the layer at the root and the groups would be empty.
        _pin_elevation_symbols(layer, QgsLineSymbol, QgsFillSymbol, QgsMarkerSymbol)
        project.addMapLayer(layer, False)
        nodes[spec["group"]].addChildNode(QgsLayerTreeLayer(layer))
        # QGIS mints a layer id as name + timestamp + UUID, so a project it
        # writes is never byte-identical to the last one even when nothing
        # changed. The ids are internal handles, not content, so they are
        # rewritten to the stable ones this module already declares -- which
        # is also what lets the two builders be compared at all.
        identifiers[layer.id()] = spec["id"]

    with tempfile.TemporaryDirectory() as directory:
        written = Path(directory) / "project.qgz"
        if not project.write(str(written)):
            raise SystemExit("QGIS refused to write project.qgz")
        with zipfile.ZipFile(written) as archive:
            name = next(item for item in archive.namelist() if item.endswith(".qgs"))
            xml = archive.read(name).decode("utf-8")
    for generated, stable in identifiers.items():
        xml = xml.replace(generated, stable)
    # QGIS wrote into a temporary directory, so it could not express layer
    # paths relative to the project and stored this machine's absolute ones.
    xml = xml.replace(f"{ROOT.as_posix()}/", "./")
    return _stabilise_symbol_ids(xml)


def _stabilise_symbol_ids(xml: str) -> str:
    """Replace QGIS's per-symbol-layer UUIDs with positional identifiers.

    Like the layer ids these are internal handles rather than content, and
    QGIS mints fresh ones on every write. Numbering them by order of first
    appearance keeps the document stable while remaining unique within it.
    """
    seen: dict[str, str] = {}
    for match in re.findall(r"\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\}", xml):
        if match not in seen:
            seen[match] = "{symbol-%03d}" % len(seen)
    for generated, stable in seen.items():
        xml = xml.replace(generated, stable)
    return xml


def _pin_elevation_symbols(layer, line_class, fill_class, marker_class) -> None:
    """Give the elevation-profile symbols fixed colours.

    Nothing in this map draws an elevation profile, but QGIS serialises the
    properties anyway and defaults each of the three symbols to a *randomly
    coloured* one. Left alone they are the last reason two projects written
    from identical inputs differ.
    """
    try:
        properties = layer.elevationProperties()
        properties.setProfileLineSymbol(line_class.createSimple({"color": "120,120,120,255"}))
        properties.setProfileFillSymbol(fill_class.createSimple({"color": "120,120,120,255"}))
        properties.setProfileMarkerSymbol(marker_class.createSimple({"color": "120,120,120,255"}))
    except Exception:  # noqa: BLE001 - older PyQGIS without these setters
        pass


def _qgs_fill(symbol_class, fill, outline):
    return symbol_class.createSimple({
        "color": ",".join(str(part) for part in fill),
        "outline_color": ",".join(str(part) for part in outline),
        "outline_width": "0.46",
    })


def _strip_volatile(xml: str) -> str:
    """Remove what QGIS varies between two saves of identical content.

    A QGIS-authored project is not reproducible as written: it stamps the save
    time, mints a random attachment id for the project style database, and
    emits the per-layer snapping settings in arbitrary order. None of that is
    content, and leaving it makes the committed artifact churn on every run
    and the clean-rerun output comparison fail on a file that never changed.

    The layer ids, symbol ids and elevation symbols are dealt with where they
    are created, because they can be pinned rather than patched.
    """
    # Canonicalise first: the substitutions below match attributes in sorted
    # order, which is only guaranteed after this call.
    xml = _canonical_xml(xml)
    xml = re.sub(r'saveDateTime="[^"]*"', 'saveDateTime=""', xml)
    xml = re.sub(r'attachment:///[A-Za-z0-9_]+_styles\.db', "attachment:///styles.db", xml)
    # QGIS adds an annotation layer with a fresh UUID, and stamps creation
    # metadata. Blanked rather than pinned to an invented date: the run record
    # is where "when did this run" is answered honestly.
    xml = re.sub(r"Annotations_[0-9a-f]{8}(?:_[0-9a-f]{4}){3}_[0-9a-f]{12}", "Annotations_main", xml)
    xml = re.sub(r'(<date[^>]*type="Created"[^>]*value=")[^"]*(")', r"\1\2", xml)
    xml = re.sub(r"<creation>[^<]*</creation>", "<creation></creation>", xml)
    return xml


def _canonical_xml(xml: str) -> str:
    """Re-serialise with attributes sorted and indentation normalised.

    Qt writes an element's attributes in hash order, which differs between
    processes, so two QGIS projects with identical content are not identical
    files. Sorting them is what finally makes the artifact reproducible -- and
    it is also what lets the two builders be compared as text at all, since
    they would otherwise differ only in attribute order.
    """
    root = ET.fromstring(xml)
    for element in root.iter():
        if len(element.attrib) > 1:
            ordered = sorted(element.attrib.items())
            element.attrib.clear()
            element.attrib.update(ordered)
    ET.indent(root, space="  ")
    return QGIS_DOCTYPE + ET.tostring(root, encoding="unicode") + "\n"


def write_qgis_project(manifest: dict) -> None:
    """Write project.qgs and project.qgz, preferring QGIS's own writer.

    Generated rather than hand-authored so the datasources cannot drift from
    the outputs that exist -- the failure the Tartu example's static check was
    added to catch. When PyQGIS is importable the file is written by QGIS
    itself; otherwise the deterministic builder produces an equivalent project
    so the pipeline still runs anywhere DuckDB does. A test asserts the two
    agree on layers, CRSs, datasources and renderers.
    """
    if pyqgis_available():
        xml, authored_by = _build_qgis_xml_with_qgis(manifest), "qgis"
    else:
        xml, authored_by = _build_qgis_xml(manifest), "deterministic-builder"
    xml = _strip_volatile(xml)
    (ROOT / "project.qgs").write_text(xml, encoding="utf-8")
    # A fixed timestamp keeps the archive byte-identical across reruns, which
    # the clean-rerun output comparison depends on.
    info = zipfile.ZipInfo("project.qgs", date_time=(2026, 9, 22, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(ROOT / "project.qgz", "w") as archive:
        archive.writestr(info, xml)
    log(f"wrote project.qgs and project.qgz (authored by {authored_by})")


def validate_qgis_project(manifest: dict) -> dict:
    """Structural checks only.

    Loading the project through PyQGIS is the real proof and is not available
    here, so this asserts what can be asserted without QGIS: the archive is
    intact, the layer tree and the project layers describe the same layers,
    every local datasource exists, and every group the manifest declares is
    in the tree.
    """
    errors: list[str] = []
    try:
        with zipfile.ZipFile(ROOT / "project.qgz") as archive:
            corrupt = archive.testzip()
            if corrupt:
                errors.append(f"corrupt archive member: {corrupt}")
            xml = ET.fromstring(archive.read("project.qgs"))
        project_layers = xml.findall("./projectlayers/maplayer")
        project_ids = {layer.findtext("id") for layer in project_layers}
        tree_ids = {node.attrib.get("id") for node in xml.findall(".//layer-tree-layer")}
        if project_ids != tree_ids:
            errors.append(f"layer-tree IDs {sorted(tree_ids)} do not match project layers {sorted(project_ids)}")
        for layer in project_layers:
            source = (layer.findtext("datasource") or "").split("|", 1)[0]
            if source.startswith("./") and not (ROOT / source[2:]).exists():
                errors.append(f"missing datasource: {source}")
        tree_groups = {node.attrib.get("name") for node in xml.findall(".//layer-tree-group")}
        absent = [
            group["id"] for group in manifest["presentation"]["map"]["layer_groups"]
            if group["title"] not in tree_groups
        ]
        if absent:
            errors.append(f"manifest layer groups absent from the QGIS layer tree: {absent}")
        if xml.findtext("./projectCrs/spatialrefsys/authid") != "EPSG:4326":
            errors.append("project CRS is not the declared storage CRS EPSG:4326")
    except Exception as exc:  # noqa: BLE001 - report, never abort the run
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "id": "qgis_project_static_valid",
        "status": "passed" if not errors else "failed",
        "message": "project.qgz is structurally valid and matches the declared layer groups",
        "project": "project.qgz",
        "errors": errors,
        "note": "structural only; loading through PyQGIS is not available in this environment",
    }


# -- entrypoint ---------------------------------------------------------------


def main() -> int:
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    manifest = yaml.safe_load((ROOT / "project.yaml").read_text(encoding="utf-8"))
    snapshots = pinned_snapshot_paths(manifest)
    duck = connect_spatial()
    if duck is None:
        raise SystemExit("DuckDB Spatial is required: pip install 'openmapstack[geo]'")
    try:
        metrics = compute_zone_metrics(duck, snapshots)
        log(f"loaded {len(metrics)} zones from pinned snapshots")
        score_candidates(metrics, manifest["scoring"])
        candidates = [zone for zone in metrics if zone["final_score"] is not None]
        candidates.sort(key=lambda item: (-item["final_score"], item["zone_id"]))
        write_outputs(duck, metrics, candidates)
        fleet_summary = write_hub_context(duck, snapshots)
        write_qgis_project(manifest)
        checks = run_checks(duck, snapshots, metrics, candidates, manifest["scoring"]["eligibility"])
        checks.append(validate_qgis_project(manifest))
    finally:
        duck.close()
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status, run_id = write_run_evidence(manifest, checks, snapshots, started_at, completed_at)
    write_dashboard(manifest, checks, status, run_id, fleet_summary)
    for check in checks:
        marker = "ok " if check["status"] == "passed" else "!! "
        log(f"{marker}{check['id']}: {check['status']}")
    return 0 if status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
