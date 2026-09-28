#!/usr/bin/env python3
"""Build the H3 catalogue, warehouse seed geometry and honest local demo extracts."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path

import h3
import yaml

from openmapstack.checks.spatial import connect_spatial

ROOT = Path(__file__).resolve().parent
RESOLUTION = 8
BBOX = (-74.030, 40.700, -73.910, 40.775)
SEED = "northstar|20260917|"
PROFILE = "h3-r8-v1"


def digest(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_rand(label, modulus):
    return int(hashlib.md5((SEED + label).encode()).hexdigest()[:8], 16) % modulus


def pin_land(connection, path):
    raw = json.loads(path.read_text())
    if len(raw["features"]) != 5:
        raise ValueError("Expected all five boroughs in the shoreline-clipped NYC source")
    west, south, east, north = BBOX
    features = []
    for feature in raw["features"]:
        geometry = connection.execute("""
            SELECT ST_AsGeoJSON(ST_Transform(ST_SimplifyPreserveTopology(
                ST_Transform(ST_Intersection(ST_GeomFromGeoJSON(?), ST_MakeEnvelope(?,?,?,?)),
                             'OGC:CRS84', 'EPSG:32618'), 1), 'EPSG:32618', 'OGC:CRS84'))
        """, [json.dumps(feature["geometry"]), west, south, east, north]).fetchone()[0]
        if json.loads(geometry)["coordinates"]:
            features.append({"type": "Feature", "properties": {"borough": feature["properties"]["boroname"]},
                             "geometry": json.loads(geometry)})
    target = ROOT / "setup/h3"
    target.mkdir(parents=True, exist_ok=True)
    (target / "land-mask.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}) + "\n")
    (target / "land-provenance.json").write_text(json.dumps({
        "dataset": "NYC Department of City Planning borough boundaries, clipped to shoreline",
        "source_url": "https://data.cityofnewyork.us/City-Government/Borough-Boundaries/gthc-hcne",
        "download_url": "https://data.cityofnewyork.us/resource/gthc-hcne.geojson?$limit=10",
        "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_sha256": digest(path), "source_feature_count": 5,
        "license_url": "https://opendata.cityofnewyork.us/overview/#termsofuse",
        "processing": "Intersection with study bbox; topology-preserving simplification at 1 metre in EPSG:32618; WGS84 output",
        "land_mask_sha256": digest(target / "land-mask.geojson"), "bbox": BBOX,
    }, indent=2) + "\n")


def build_cells(connection):
    mask = json.loads((ROOT / "setup/h3/land-mask.geojson").read_text())
    west, south, east, north = BBOX
    region = h3.LatLngPoly([(south, west), (south, east), (north, east), (north, west)])
    cells = []
    for index in sorted(h3.polygon_to_cells(region, RESOLUTION)):
        latitude, longitude = h3.cell_to_latlng(index)
        for feature in mask["features"]:
            contained = connection.execute("SELECT ST_Contains(ST_GeomFromGeoJSON(?), ST_Point(?,?))",
                                           [json.dumps(feature["geometry"]), longitude, latitude]).fetchone()[0]
            if not contained:
                continue
            ring = [[longitude, latitude] for latitude, longitude in h3.cell_to_boundary(index)]
            ring.append(ring[0])
            cells.append({"zone_id": 101 + len(cells), "h3_cell": index,
                          "borough": feature["properties"]["borough"], "zone_name": "H3 " + index,
                          "zone_source": "h3-r8", "latitude": latitude, "longitude": longitude,
                          "geometry": {"type": "Polygon", "coordinates": [ring]}})
            break
    return cells


def zone_sql(cells, backend):
    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    if backend == "postgis":
        # The shared catalogue fixes cell identity; h3-pg supplies the actual
        # boundary and centre so this seed never embeds polygon coordinates.
        rows = [f"({cell['zone_id']},{literal(cell['h3_cell'])},{literal(cell['borough'])})" for cell in cells]
        return (
            "WITH catalogue(zone_id,h3_cell,borough) AS (VALUES\n"
            + ",\n".join(rows)
            + "\n), generated AS (\n"
            + "  SELECT zone_id,h3_cell,borough,\n"
            + "         h3_cell_to_boundary_geometry(h3_cell::h3index) AS geom,\n"
            + "         h3_cell_to_geometry(h3_cell::h3index) AS centre\n"
            + "  FROM catalogue\n"
            + ")\n"
            + "INSERT INTO ops.taxi_zones (zone_id,h3_cell,borough,zone_name,zone_source,geom,latitude,longitude)\n"
            + "SELECT zone_id,h3_cell,borough,'H3 ' || h3_cell,'h3-r8',geom,ST_Y(centre),ST_X(centre)\n"
            + "FROM generated ORDER BY zone_id;\n"
        )

    rows = []
    for cell in cells:
        ring = cell["geometry"]["coordinates"][0]
        wkt = "POLYGON((" + ",".join(f"{point[0]} {point[1]}" for point in ring) + "))"
        fields = [str(cell["zone_id"])] + [literal(cell[field]) for field in ("h3_cell", "borough", "zone_name", "zone_source")]
        geometry = f"ST_GeomFromText({literal(wkt)},4326)" if backend == "postgis" else f"ST_GEOGFROMTEXT({literal(wkt)})" if backend == "bigquery" else f"ST_GeomFromText({literal(wkt)})"
        rows.append("(" + ",".join(fields + [geometry, str(cell["latitude"]), str(cell["longitude"])]) + ")")
    table = {"postgis": "ops.taxi_zones", "bigquery": "`:project`.`:dataset`.taxi_zones", "motherduck": "market.fixture_zones"}[backend]
    geometry_field = "zone_area" if backend == "bigquery" else "geom"
    return f"INSERT INTO {table} (zone_id,h3_cell,borough,zone_name,zone_source,{geometry_field},latitude,longitude) VALUES\n" + ",\n".join(rows) + ";\n"


def populate_local(connection, cells):
    connection.execute("CREATE SCHEMA ops; CREATE SCHEMA northstar_analytics")
    connection.execute("CREATE TABLE ops.taxi_zones (zone_id INTEGER, h3_cell VARCHAR, borough VARCHAR, zone_name VARCHAR, zone_source VARCHAR, geom GEOMETRY, latitude DOUBLE, longitude DOUBLE)")
    connection.execute(zone_sql(cells, "motherduck").replace("market.fixture_zones", "ops.taxi_zones"))
    connection.execute("CREATE TABLE ops.hubs (hub_id VARCHAR,tenant_id VARCHAR,taxi_zone_id INTEGER,name VARCHAR,capacity INTEGER,activated_on DATE,geom GEOMETRY)")
    for number in range(1, 5):
        cell = cells[(number - 1) * (len(cells) // 4)]
        connection.execute("INSERT INTO ops.hubs VALUES (?, 'alpha', ?, ?, ?, ?, ST_Point(?,?))", [
            f"H{number}", cell["zone_id"], f"Northstar Hub {number}", 20 + fixture_rand(f"hub-cap-{number}", 31),
            datetime.date(2026, 3, 1) + datetime.timedelta(days=fixture_rand(f"hub-day-{number}", 180)), cell["longitude"], cell["latitude"]])
    connection.execute("CREATE TABLE ops.fleet_positions (position_id BIGINT,tenant_id VARCHAR,vehicle_id VARCHAR,taxi_zone_id INTEGER,status VARCHAR,recorded_at TIMESTAMPTZ,geom GEOMETRY)")
    fleet = []
    for number in range(1, 481):
        if fixture_rand(f"fleet-tenant-{number}", 10) >= 8:
            continue
        cell = cells[fixture_rand(f"fleet-zone-{number}", len(cells))]
        fleet.append((number, "alpha", f"NV-{100 + fixture_rand(f'fleet-veh-{number}', 900)}", cell["zone_id"],
                      ["idle", "on_trip", "charging", "maintenance"][fixture_rand(f"fleet-status-{number}", 4)],
                      datetime.datetime(2026, 9, 1, 6, tzinfo=datetime.timezone.utc) + datetime.timedelta(
                          hours=fixture_rand(f"fleet-hour-{number}", 17), minutes=fixture_rand(f"fleet-min-{number}", 60)),
                      cell["longitude"], cell["latitude"]))
    connection.executemany("INSERT INTO ops.fleet_positions VALUES (?,?,?,?,?,?,ST_Point(?,?))", fleet)
    connection.execute("CREATE TABLE ops.customer_accounts (account_id BIGINT,tenant_id VARCHAR,taxi_zone_id INTEGER,account_name VARCHAR,is_active BOOLEAN)")
    connection.executemany("INSERT INTO ops.customer_accounts VALUES (?, 'alpha', ?, ?, ?)", [
        (number, cells[fixture_rand(f"ca-zone-{number}", len(cells))]["zone_id"], f"Account {number:05d}", fixture_rand(f"ca-active-{number}", 10) < 9)
        for number in range(1, 3601) if fixture_rand(f"ca-tenant-{number}", 10) < 6])
    connection.execute("CREATE TABLE trips (trip_id INTEGER,taxi_zone_id INTEGER,h3_cell VARCHAR,pickup_date DATE,trip_distance_km DOUBLE,geom GEOMETRY)")
    trips = []
    for number in range(1, len(cells) * 80 + 1):
        if fixture_rand(f"trip-tenant-{number}", 10) >= 6:
            continue
        cell = cells[fixture_rand(f"trip-zone-{number}", len(cells))]
        trips.append((number, cell["zone_id"], cell["h3_cell"], datetime.date(2026, 8, 1) + datetime.timedelta(days=fixture_rand(f"trip-day-{number}", 30)),
                      round(0.5 + fixture_rand(f"trip-km-{number}", 2400) / 100, 2), cell["longitude"], cell["latitude"]))
    connection.executemany("INSERT INTO trips VALUES (?,?,?,?,?,ST_Point(?,?))", trips)
    connection.execute("CREATE VIEW northstar_analytics.taxi_zones AS SELECT * FROM ops.taxi_zones")
    connection.execute("CREATE VIEW northstar_analytics.zone_daily_demand AS SELECT taxi_zone_id,pickup_date AS demand_date,count(*) AS trips,round(avg(trip_distance_km),3) AS mean_trip_km FROM trips GROUP BY 1,2")
    for name in ("schema.sql", "seed.sql", "security.sql"):
        sql = (ROOT / "setup/motherduck" / name).read_text().replace("__H3_ZONES__", zone_sql(cells, "motherduck"))
        connection.execute(sql)


QUERIES = {"taxi_zones": "postgis-taxi-zones", "hubs": "postgis-hubs", "fleet_positions": "postgis-fleet",
           "customer_accounts": "postgis-customer-accounts", "zone_demand": "bigquery-zone-demand", "zone_market": "motherduck-zone-market"}


def write_snapshots(connection, output):
    output.mkdir(parents=True, exist_ok=True)
    counts = {}
    for key, query in QUERIES.items():
        sql = (ROOT / "queries" / (query + ".sql")).read_text()
        connection.execute(f"CREATE OR REPLACE TEMP VIEW snapshot AS {sql}")
        columns = [row[0] for row in connection.execute("DESCRIBE snapshot").fetchall()]
        select = "SELECT * REPLACE(ST_SetCRS(geom,'EPSG:4326') AS geom) FROM snapshot" if "geom" in columns else "SELECT * FROM snapshot"
        target = output / (key + ".parquet")
        temporary = output / (key + ".new.parquet")
        connection.execute(f"COPY ({select}) TO '{temporary.as_posix()}' (FORMAT PARQUET)")
        if target.exists():
            matches = digest(target) == digest(temporary)
            temporary.unlink()
            if not matches:
                raise ValueError(f"Refusing to change pinned input {target}; use a new output directory")
        else:
            temporary.replace(target)
        counts[key] = connection.execute("SELECT count(*) FROM snapshot").fetchone()[0]
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--land-input", type=Path, help="One-time pin of the complete five-borough NYC GeoJSON download")
    parser.add_argument("--output", type=Path, default=ROOT / "data/source" / PROFILE)
    arguments = parser.parse_args()
    if not arguments.output.is_absolute():
        arguments.output = ROOT / arguments.output
    arguments.output = arguments.output.resolve()
    if not arguments.output.is_relative_to(ROOT / "data/source") or arguments.output == ROOT / "data/source":
        raise SystemExit("Output must be a versioned directory below this example's data/source/")
    connection = connect_spatial()
    if connection is None:
        raise SystemExit("DuckDB Spatial is required")
    if arguments.land_input:
        if (ROOT / "setup/h3/land-mask.geojson").exists():
            raise SystemExit("Land mask already pinned; do not overwrite it")
        pin_land(connection, arguments.land_input)
    cells = build_cells(connection)
    (ROOT / "setup/h3/cells.json").write_text(json.dumps(cells, indent=2) + "\n")
    for backend in ("postgis", "bigquery", "motherduck"):
        (ROOT / "setup" / backend / "zones.sql").write_text(zone_sql(cells, backend))
    populate_local(connection, cells)
    counts = write_snapshots(connection, arguments.output)
    manifest_path = arguments.output / "manifest.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    generated_at = previous.get("generated_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
    generation_inputs = [ROOT / "rebuild_fixture.py", ROOT / "requirements-fixture.txt",
                         *sorted((ROOT / "setup").rglob("*.sql")), *sorted((ROOT / "setup/h3").glob("*")),
                         *sorted((ROOT / "queries").glob("*.sql"))]
    record = {"schema": "openmapstack-source-manifest/v1", "project": "nyc-private-mobility",
              "origin": "locally_generated_synthetic", "profile": PROFILE, "generated_at": generated_at,
              "h3_resolution": RESOLUTION, "h3_versions": h3.versions(), "cell_count": len(cells),
              "security_evidence": "NOT TESTED: local alpha-only projections simulate reader extracts; no warehouse was contacted",
              "generation_inputs": {path.relative_to(ROOT).as_posix(): digest(path) for path in generation_inputs},
              "sources": {key: {"path": (arguments.output / (key + '.parquet')).relative_to(ROOT).as_posix(),
                                "sha256": digest(arguments.output / (key + '.parquet')), "row_count": count} for key, count in counts.items()}}
    manifest_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    manifest = yaml.safe_load((ROOT / "project.yaml").read_text())
    for key, source in manifest["sources"].items():
        entry = record["sources"][key]
        source["pin"].update(path=entry["path"], sha256=entry["sha256"], captured_at=generated_at)
        source["access"].update(method="local_synthetic_fixture", retrieved_at=generated_at, downloaded_at=generated_at)
        source["access"].pop("connection", None)
        source["access"]["file"].update(row_count=entry["row_count"], size_bytes=(ROOT / entry["path"]).stat().st_size,
                                         format="GeoParquet" if key in ("taxi_zones", "fleet_positions", "hubs") else "Parquet")
        source["version"] = {"identifier": PROFILE, "published_at": generated_at[:10]}
        source["warehouse"].pop("query_sha256", None)
        source["warehouse"].pop("schema_sha256", None)
        source["selection"] = {"filter": "Synthetic intended-reader projection; H3 resolution 8 cell catalogue",
                               "security_note": record["security_evidence"]}
        source["origin"] = "locally_generated_synthetic"
    manifest["runtime"]["fixture"] = {"profile": PROFILE, "h3_resolution": RESOLUTION,
                                         "source_manifest": manifest_path.relative_to(ROOT).as_posix()}
    dependencies = manifest["runtime"]["implementation"].setdefault("dependencies", [])
    dependencies[:] = [path for path in dependencies if not (path.startswith("data/source/") and path.endswith("/manifest.json"))]
    dependencies.append(manifest_path.relative_to(ROOT).as_posix())
    dependencies[:] = sorted(set(dependencies) | {path.relative_to(ROOT).as_posix() for path in generation_inputs})
    (ROOT / "project.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True, width=100))
    connection.close()
    print(f"Built {len(cells)} H3 resolution {RESOLUTION} cells; local synthetic extracts: {counts}")


if __name__ == "__main__":
    main()
