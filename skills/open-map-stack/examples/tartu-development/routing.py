"""Pinned local Valhalla graph and per-facility pedestrian isochrones."""

from __future__ import annotations

import datetime
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


log = logging.getLogger("tartu-routing")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return "sha256:" + checksum.hexdigest()


def check_dependencies(settings: dict) -> None:
    for package, expected in settings["versions"].items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError("Install the example's requirements.txt before running pedestrian routing") from error
        if actual != expected:
            raise RuntimeError(f"Routing requires {package}=={expected}; found {actual}")


def fetch_network(root: Path, source: dict) -> dict:
    target = root / source["pin"]["path"]
    expected = source["pin"]["sha256"]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".download")
        log.info("Downloading pinned OSM network: %s", source["source_url"])
        try:
            with urllib.request.urlopen(source["source_url"], timeout=180) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if digest(temporary) != expected:
                raise RuntimeError("OSM snapshot checksum mismatch; refusing a different routing network")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if digest(target) != expected:
        raise RuntimeError("Cached OSM snapshot checksum mismatch")
    retrieved = datetime.datetime.fromtimestamp(target.stat().st_mtime, datetime.timezone.utc).isoformat()
    return {
        "key": "pedestrian_network", "role": "routing_input",
        "file": target.name, "format": "OSM PBF", "table_name": "OSM network",
        "source_url": source["source_url"], "portal_page": source["portal_page"],
        "download_timestamp": retrieved, "version": source["version"]["identifier"],
        "published_at": source["version"]["published_at"], "sha256": expected,
        "size_bytes": target.stat().st_size, "rows": None, "n_columns": 0, "columns": [],
    }


def extract_network(source: Path, target: Path, bounds: list[float]) -> None:
    os.environ.setdefault("OSMIUM_POOL_THREADS", "1")
    import osmium

    west, south, east, north = bounds

    selected_nodes = set()
    selected_ways = set()
    selected_relations = set()
    required_nodes = set()
    required_ways = set()

    class SelectNodes(osmium.SimpleHandler):
        def node(self, node):
            if node.location.valid() and west <= node.lon <= east and south <= node.lat <= north:
                selected_nodes.add(node.id)

    class SelectWays(osmium.SimpleHandler):
        def way(self, way):
            if any(node.ref in selected_nodes for node in way.nodes):
                selected_ways.add(way.id)
                required_nodes.update(node.ref for node in way.nodes)

        def relation(self, relation):
            touches = any((member.type == "n" and member.ref in selected_nodes)
                          or (member.type == "w" and member.ref in selected_ways) for member in relation.members)
            if relation.tags.get("type") == "restriction" and touches:
                selected_relations.add(relation.id)
                for member in relation.members:
                    if member.type == "n":
                        required_nodes.add(member.ref)
                    elif member.type == "w":
                        required_ways.add(member.ref)
                    else:
                        raise RuntimeError("Nested restriction relation requires a reference-complete regional extract")

    class CompleteWays(osmium.SimpleHandler):
        def way(self, way):
            if way.id in required_ways:
                selected_ways.add(way.id)
                required_nodes.update(node.ref for node in way.nodes)

    SelectNodes().apply_file(str(source))
    SelectWays().apply_file(str(source))
    CompleteWays().apply_file(str(source))
    selected_nodes.update(required_nodes)
    if not required_ways.issubset(selected_ways):
        raise RuntimeError("OSM extract is missing ways referenced by a restriction")
    written_nodes = 0
    with osmium.SimpleWriter(str(target), overwrite=True) as writer:
        for entity in osmium.FileProcessor(str(source)):
            if ((entity.is_node() and entity.id in selected_nodes)
                    or (entity.is_way() and entity.id in selected_ways)
                    or (entity.is_relation() and entity.id in selected_relations)):
                writer.add(entity)
                written_nodes += int(entity.is_node())
    if written_nodes != len(selected_nodes):
        raise RuntimeError("OSM extract is missing nodes referenced by a way or restriction")

    log.info("Extracted %d nodes, %d ways and %d restrictions", len(selected_nodes), len(selected_ways), len(selected_relations))


def prepare_graph(root: Path, source: dict, settings: dict):
    check_dependencies(settings)
    import valhalla
    identity = {
        "source_sha256": source["pin"]["sha256"], "settings": settings,
        "implementation_sha256": digest(Path(__file__)),
    }
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    cache = root / "data/cache/routing" / fingerprint
    tiles = cache / "tiles"
    tiles.mkdir(parents=True, exist_ok=True)
    config = valhalla.get_config(tile_dir=tiles, tile_extract="")
    config["mjolnir"].update(
        concurrency=1, max_cache_size=67108864, id_table_size=1000000,
        admin="", timezone="", traffic_extract="", landmarks="",
    )
    config["mjolnir"]["data_processing"]["use_admin_db"] = False
    config["service_limits"]["isochrone"]["max_contours"] = len(settings["minutes"])
    config_file = cache / "valhalla.json"
    config_file.write_text(json.dumps(config, indent=2))
    evidence_file = cache / "graph.json"
    existing = json.loads(evidence_file.read_text()) if evidence_file.exists() else {}
    actual_tiles = {str(path.relative_to(cache)): digest(path) for path in sorted(tiles.rglob("*.gph"))}
    if not actual_tiles or existing.get("tiles") != actual_tiles:
        shutil.rmtree(tiles)
        tiles.mkdir()
        extract = cache / "tartu.osm.pbf"
        log.info("Extracting complete ways and restrictions within %s", settings["bounds"])
        extract_network(root / source["pin"]["path"], extract, settings["bounds"])
        log.info("Building local Valhalla graph (one worker); log: %s", cache / "build.log")
        with (cache / "build.log").open("w") as output:
            subprocess.run(
                [sys.executable, "-m", "valhalla", "valhalla_build_tiles", "-c", str(config_file),
                 "-j", "1", str(extract)], stdout=output, stderr=subprocess.STDOUT, check=True,
            )
        actual_tiles = {str(path.relative_to(cache)): digest(path) for path in sorted(tiles.rglob("*.gph"))}
        if not actual_tiles:
            raise RuntimeError("Valhalla produced no graph tiles")
        evidence_file.write_text(json.dumps({"identity": identity, "tiles": actual_tiles}, indent=2))
    log.info("Using pinned pedestrian graph: %d tiles", len(actual_tiles))
    return valhalla.Actor(config), {**identity, "tiles": actual_tiles, "fingerprint": fingerprint}


def isochrone_request(feature: dict, settings: dict) -> dict:
    longitude, latitude = feature["geometry"]["coordinates"][:2]
    west, south, east, north = settings["bounds"]
    margin_lat = settings["walking_speed_kmh"] * max(settings["minutes"]) / 60 / 110
    margin_lon = margin_lat / math.cos(math.radians(latitude))
    if not (west + margin_lon < longitude < east - margin_lon
            and south + margin_lat < latitude < north - margin_lat):
        raise RuntimeError(f"Facility {feature['properties']['source_id']} is too close to the routing extract boundary")
    return {
        "locations": [{"lat": latitude, "lon": longitude, "radius": 0,
                       "search_cutoff": settings["max_snap_m"]}],
        "costing": "pedestrian",
        "costing_options": {"pedestrian": {"walking_speed": settings["walking_speed_kmh"],
                                           "use_ferry": 0}},
        "contours": [{"time": minute} for minute in settings["minutes"]],
        "polygons": True, "reverse": True, "denoise": 0, "generalize": 0,
        "show_locations": True, "id": feature["properties"]["source_id"],
    }


def validate_response(response: dict, requested: list[int], source_id: str) -> list[dict]:
    if response.get("warnings"):
        raise RuntimeError(f"Valhalla warnings for {source_id}: {response['warnings']}")
    polygons = [feature for feature in response.get("features", [])
                if feature.get("geometry", {}).get("type") in {"Polygon", "MultiPolygon"}]
    if any(feature["properties"].get("metric") != "time" for feature in polygons):
        raise RuntimeError(f"Non-time isochrone for {source_id}")
    minutes = [feature.get("properties", {}).get("contour") for feature in polygons]
    if sorted(minutes) != sorted(requested):
        raise RuntimeError(f"Incomplete isochrones for {source_id}: expected {requested}, got {minutes}")
    return polygons


def facility_isochrones(root: Path, source: dict, settings: dict, facilities: dict) -> tuple[dict, dict]:
    import pyproj

    actor, graph = prepare_graph(root, source, settings)
    geodesic = pyproj.Geod(ellps="WGS84")
    features = []
    requests = []
    for facility in sorted(facilities["features"], key=lambda feature: feature["properties"]["source_id"]):
        properties = facility["properties"]
        if not properties.get("active_source"):
            continue
        request = isochrone_request(facility, settings)
        log.info("Walking isochrones: %s", properties["name"])
        response = actor.isochrone(request)
        snapped = [point for feature in response.get("features", [])
                   if feature.get("properties", {}).get("type") == "snapped"
                   for point in feature["geometry"]["coordinates"]]
        longitude, latitude = facility["geometry"]["coordinates"][:2]
        distances = [geodesic.inv(longitude, latitude, point[0], point[1])[2] for point in snapped]
        if not distances or any(not math.isfinite(distance) or distance > settings["max_snap_m"] + 1 for distance in distances):
            raise RuntimeError(f"Missing or excessive network snap for {properties['source_id']}")
        for feature in validate_response(response, settings["minutes"], properties["source_id"]):
            output_feature = {"type": "Feature", "geometry": feature["geometry"], "properties": {
                "source_id": properties["source_id"], "name": properties["name"],
                "amenity": properties["amenity"], "active": properties["active"],
                "active_source": properties["active_source"],
                "minutes": feature["properties"]["contour"], "method": "valhalla_pedestrian",
            }}
            features.append(output_feature)
        requests.append({"request": request, "response": response, "snap_distances_m": distances})
    if not features:
        raise RuntimeError("No active source facilities available for routing")
    evidence = {"graph": graph, "requests": requests}
    return {"type": "FeatureCollection", "features": features}, evidence
