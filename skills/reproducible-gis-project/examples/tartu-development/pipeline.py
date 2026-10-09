# =============================================================================
# pipeline.py — Canonical reproducible run for examples/tartu-development
# =============================================================================
# Executes the full openmapstack-project/v1 loop for the Tartu development-access
# scenario using REAL, OFFICIAL Estonian datasets and renders the final HTML
# dashboard AS A VIEW over the project artifacts (project.yaml + derived data
# + validation report + source manifest).
#
# Real sources:
#   1. Maa- ja Ruumiamet Cadastral GeoPackage (Tartu maakond)
#      https://s3.pilw.io/rp-kemit-kataster/ANDMED/Tartu_maakond_KATASTER_GPKG.zip
#   2. ETAK National Road Network WFS (Environment Agency GeoServer)
#      https://gsavalik.envir.ee/geoserver/etak/wfs
#   3. Tartu municipal schools and kindergartens (official ArcGIS Feature Services)
#      https://gis.tartulv.ee/arcgis/rest/services/Haridus
#   4. Explicit hypothetical scenario (OVERRIDE-002: connector road)
#      data/overrides/planned-road.geojson
#
# Multi-criteria constraints:
#   - Minimum parcel size >= 20,000 m2 (2.0 ha) in EPSG:3301 (L-EST97)
#   - Land-use (siht1): Agricultural, Production, or Commercial in Tartu linn
#   - Arterial road proximity <= 2,000 m (Põhimaantee/Tugimaantee or planned road)
#   - Education access: overlap with 25-minute pedestrian-network catchments
#
# Execution: python pipeline.py (run_e2e.py is a thin wrapper)
# =============================================================================

import argparse
import contextlib
import datetime
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb
import pyproj
import yaml

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "data" / "source"
DERIVED = ROOT / "data" / "derived"
OVERRIDES = ROOT / "data" / "overrides"
VALIDATION = ROOT / "validation"
RUNS = ROOT / "runs"

log = logging.getLogger("tartu-pipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

PROJECT = yaml.safe_load((ROOT / "project.yaml").read_text())

ANALYSIS_CRS = 3301   # L-EST97 metric CRS
STORAGE_CRS = 4326    # WGS84 for MapLibre rendering
MIN_PARCEL_AREA_M2 = 20000    # accepted minimum developable parcel size
MAX_ROAD_DISTANCE_M = 2000    # accepted highway-accessibility threshold
CANONICAL_WALK_MINUTES = 25
WALK_THRESHOLDS_MINUTES = (15, 20, 25, 30, 40)

# The three suitability tiers, keyed by the manifest layer group that presents
# each one. The SQL CASE that assigns the tier, the QGIS layer subsets and the
# QGIS legend all read these strings from here, so re-wording a tier can never
# leave a QGIS layer filtering on a label the data stopped carrying.
SUITABILITY_TIERS = {
    "candidates_tier1": "Tier 1: Prime (25-minute walk catchments of School & Kindergarten)",
    "candidates_tier2": "Tier 2: Good (25-minute walk catchment of School or Kindergarten)",
    "candidates_highway": "Tier 3: Highway Access Only (outside 25-minute education catchments)",
}


def _run_id() -> str:
    return "run-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _round_geometry(geom: dict, ndigits: int = 6) -> dict:
    """Round GeoJSON coordinates for web payloads (~0.1 m at this latitude).

    Applied only to browser-bound copies and to the exploratory catchment
    variants. The canonical GPKG/Parquet/GeoJSON outputs keep full precision.
    """

    def walk(c):
        if isinstance(c[0], (int, float)):
            return [round(v, ndigits) for v in c]
        return [walk(sub_c) for sub_c in c]

    geom["coordinates"] = walk(geom["coordinates"])
    return geom


def _normalize_group_name(value: str | None) -> str:
    """Fold a layer-group id or title the way openmapstack's
    qgis.groups_match_manifest does, so the pipeline's own report and the
    external check agree on what counts as the same group."""
    return re.sub(r"[\s_-]+", " ", str(value or "")).strip().lower()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


#: A cached source older than this is reused, but says so loudly.
#
# `data/source/` exists so a re-run does not re-download 40 MB of cadastre.
# Nothing in that cache expires on its own, and the reuse test each source
# applies below -- counts agreeing with recorded metadata, ownership and
# active-status predicates holding -- establishes that the bytes on disk are
# internally coherent, never that they still match what the service publishes.
# The two look identical in a log until the committed artifacts are regenerated
# from a stale cache: CI always starts cold, so it fetches the current data and
# cannot reproduce them.
CACHE_MAX_AGE_DAYS = 7


def _cache_age_days(path: Path) -> float | None:
    """How long ago the cached file was written, or ``None`` if it is absent."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return max(0.0, (now - mtime) / 86400.0)


def _http_date_to_day(value: str | None) -> str | None:
    """The YYYY-MM-DD of an RFC 7231 ``Last-Modified``, or ``None`` if absent."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %Z")
    except ValueError:
        return None
    return parsed.date().isoformat()


def _reuse_cached(label: str, path: Path, coherent: bool, refresh: bool) -> bool:
    """Whether to reuse a cached source, and say why in the log.

    ``coherent`` is the caller's own integrity test. It is deliberately not a
    freshness test, so age is reported separately: a stale reuse is the one
    failure mode here that produces a plausible, self-consistent, wrong result.
    """
    if refresh:
        if path.exists():
            log.info("--refresh: discarding cached %s", label)
        return False
    if not coherent:
        return False
    age = _cache_age_days(path)
    if age is None:
        return False
    if age > CACHE_MAX_AGE_DAYS:
        log.warning(
            "Cached %s is %.1f days old and is being reused. It is internally coherent, "
            "which is not the same as current -- upstream may have moved. Re-run with "
            "--refresh before committing regenerated artifacts.",
            label,
            age,
        )
    else:
        log.info("Using cached %s (%.1f days old): %s", label, age, path)
    return True


def _read_json_url(base_url: str, params: dict, timeout: int = 120) -> dict:
    url = base_url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "openmapstack-pipeline/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


# ------------------------------- STEP 0: sources ----------------------------
def fetch_and_manifest_sources(refresh: bool = False) -> tuple[Path, Path, Path, list[dict]]:
    """Fetch complete, semantically fit sources and record exact runtime metadata.

    ``refresh`` empties ``data/source/`` and re-fetches every service. Use it
    before regenerating the committed artifacts; see ``CACHE_MAX_AGE_DAYS``.
    """
    if refresh and SOURCE.is_dir():
        # Empty the directory rather than bypassing reuse file by file. The run
        # record inventories *everything* under data/source/, so a file written
        # by an older version of this pipeline -- which nothing here reads and a
        # cold checkout never has -- would otherwise enter inputs_hash and make
        # the refreshed attestation differ from CI's. Discarding also means no
        # cached bytes are parsed, so a truncated cache cannot block the refresh
        # that exists to replace it.
        discarded = sorted(item.name for item in SOURCE.iterdir() if item.is_file())
        shutil.rmtree(SOURCE)
        log.info("--refresh: emptied %s (%d cached file(s))", SOURCE, len(discarded))
    SOURCE.mkdir(parents=True, exist_ok=True)

    # 1. Cadastral GeoPackage for Tartu county
    cadastre_zip = SOURCE / "Tartu_maakond_KATASTER_GPKG.zip"
    cadastre_gpkg = SOURCE / "Tartu_maakond_KATASTER_GPKG.gpkg"
    cadastre_meta_file = SOURCE / "Tartu_maakond_KATASTER_GPKG.meta.json"
    cadastre_url = "https://s3.pilw.io/rp-kemit-kataster/ANDMED/Tartu_maakond_KATASTER_GPKG.zip"

    # The cadastre is a *daily* snapshot behind a stable URL, so which snapshot
    # is on disk is only knowable from the response that delivered it. Record
    # the ETag and Last-Modified at download time and keep them beside the file;
    # a cache without that sidecar cannot say what it holds and is refetched.
    cadastre_meta: dict = {}
    if cadastre_meta_file.is_file():
        try:
            cadastre_meta = json.loads(cadastre_meta_file.read_text())
        except json.JSONDecodeError:
            cadastre_meta = {}
    cadastre_cached = cadastre_gpkg.exists() and bool(cadastre_meta.get("retrieved_at"))
    if not _reuse_cached("Cadastral GeoPackage", cadastre_gpkg, cadastre_cached, refresh):
        log.info("Downloading official Tartu county Cadastral GeoPackage from Maa- ja Ruumiamet S3...")
        req = urllib.request.Request(cadastre_url, headers={"User-Agent": "openmapstack-pipeline/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            content = resp.read()
            headers = resp.headers
        log.info("Downloaded %0.1f MB zip; extracting to %s", len(content) / (1024 * 1024), SOURCE)
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            z.extractall(SOURCE)
        cadastre_meta = {
            "source_url": cadastre_url,
            "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "etag": (headers.get("ETag") or "").strip('"') or None,
            "last_modified": headers.get("Last-Modified"),
            "zip_bytes": len(content),
        }
        cadastre_meta_file.write_text(json.dumps(cadastre_meta, indent=2, ensure_ascii=False))

    # 2. ETAK main roads via complete, paginated WFS query.
    roads_geojson = SOURCE / "etak_main_roads.geojson"
    roads_meta_file = SOURCE / "etak_main_roads.meta.json"
    roads_wfs_url = "https://gsavalik.envir.ee/geoserver/etak/wfs"
    cql_filter = (
        "BBOX(shape,640000,6455000,685000,6500000,'EPSG:3301') "
        "AND tyyp_tekst IN ('Põhimaantee','Tugimaantee')"
    )
    page_size = 1000
    roads_cache_valid = False
    if roads_geojson.exists() and roads_meta_file.exists():
        roads_meta = json.loads(roads_meta_file.read_text())
        roads_raw = json.loads(roads_geojson.read_text())
        roads_cache_valid = (
            roads_meta.get("cql_filter") == cql_filter
            and roads_meta.get("matched") == roads_meta.get("returned")
            and roads_meta.get("returned") == len(roads_raw.get("features", []))
        )
    roads_cache_valid = _reuse_cached("ETAK main roads", roads_geojson, roads_cache_valid, refresh)
    if not roads_cache_valid:
        log.info("Fetching complete ETAK main-road result with WFS pagination...")
        features: list[dict] = []
        matched = None
        pages = 0
        while matched is None or len(features) < matched:
            page = _read_json_url(
                roads_wfs_url,
                {
                    "service": "WFS",
                    "version": "2.0.0",
                    "request": "GetFeature",
                    "typeNames": "etak:e_501_tee_j",
                    "srsName": "EPSG:3301",
                    "outputFormat": "application/json",
                    "CQL_FILTER": cql_filter,
                    "count": page_size,
                    "startIndex": len(features),
                },
            )
            if matched is None:
                matched = int(page.get("numberMatched", page.get("totalFeatures", -1)))
                if matched < 0:
                    raise RuntimeError("ETAK WFS did not report numberMatched; completeness cannot be proven")
            returned = int(page.get("numberReturned", len(page.get("features", []))))
            page_features = page.get("features", [])
            if returned != len(page_features) or not page_features:
                raise RuntimeError("ETAK WFS pagination stopped before numberMatched was returned")
            features.extend(page_features)
            pages += 1
        if len(features) != matched:
            raise RuntimeError(f"ETAK completeness failure: matched={matched}, returned={len(features)}")
        roads_raw = {
            "type": "FeatureCollection",
            "numberMatched": matched,
            "numberReturned": len(features),
            "features": features,
        }
        roads_geojson.write_text(json.dumps(roads_raw, indent=2, ensure_ascii=False))
        roads_meta = {
            "source_url": roads_wfs_url,
            "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "cql_filter": cql_filter,
            "page_size": page_size,
            "pages": pages,
            "matched": matched,
            "returned": len(features),
        }
        roads_meta_file.write_text(json.dumps(roads_meta, indent=2, ensure_ascii=False))
    if "retrieved_at" not in roads_meta:
        roads_meta["retrieved_at"] = datetime.datetime.fromtimestamp(
            roads_geojson.stat().st_mtime, datetime.timezone.utc
        ).isoformat()
        roads_meta_file.write_text(json.dumps(roads_meta, indent=2, ensure_ascii=False))

    # 3. Tartu authoritative municipal schools and kindergartens.
    pois_geojson = SOURCE / "tartu_municipal_education.geojson"
    pois_meta_file = SOURCE / "tartu_municipal_education.meta.json"
    school_item = "45671f12e7864221976cb11c48c57cd1"
    kindergarten_item = "2500887fd6414aa18cc08c7b8e712623"
    school_url = "https://gis.tartulv.ee/arcgis/rest/services/Haridus/LU_koolid/FeatureServer/0/query"
    kindergarten_url = "https://gis.tartulv.ee/arcgis/rest/services/Haridus/LU_lasteaiad_lastehoiud/FeatureServer/0/query"
    school_where = "Omand=1 AND Liik<>5 AND Lopetamise_kp IS NULL"
    kindergarten_where = "Liik=10 AND Lopetamise_kp IS NULL"
    education_cache_valid = False
    if pois_geojson.exists() and pois_meta_file.exists():
        pois_raw = json.loads(pois_geojson.read_text())
        pois_meta = json.loads(pois_meta_file.read_text())
        education_cache_valid = (
            pois_meta.get("matched") == pois_meta.get("returned") == len(pois_raw.get("features", []))
            and all(
                f.get("properties", {}).get("ownership") == "municipal"
                and f.get("properties", {}).get("active") is True
                for f in pois_raw.get("features", [])
            )
        )
    education_cache_valid = _reuse_cached(
        "municipal education data", pois_geojson, education_cache_valid, refresh
    )
    if not education_cache_valid:
        log.info("Fetching authoritative Tartu municipal education layers...")
        normalized: list[dict] = []
        counts: dict[str, int] = {}
        for amenity, item_id, url, where in (
            ("school", school_item, school_url, school_where),
            ("kindergarten", kindergarten_item, kindergarten_url, kindergarten_where),
        ):
            out_fields = "OBJECTID,Nimi,Liik,GlobalID,Aadress,Lopetamise_kp"
            if amenity == "school":
                out_fields += ",Omand"
            count_result = _read_json_url(url, {"f": "json", "where": where, "returnCountOnly": "true"})
            matched = int(count_result["count"])
            result = _read_json_url(
                url,
                {
                    "f": "geojson",
                    "where": where,
                    "outFields": out_fields,
                    "outSR": 4326,
                    "returnGeometry": "true",
                },
            )
            returned = len(result.get("features", []))
            if returned != matched:
                raise RuntimeError(f"Tartu {amenity} completeness failure: matched={matched}, returned={returned}")
            counts[amenity] = matched
            for feature in result["features"]:
                props = feature["properties"]
                if amenity == "school" and props.get("Omand") != 1:
                    raise RuntimeError("Non-municipal school passed the authoritative ownership predicate")
                if amenity == "kindergarten" and props.get("Liik") != 10:
                    raise RuntimeError("Non-municipal kindergarten passed the authoritative type predicate")
                if props.get("Lopetamise_kp") is not None:
                    raise RuntimeError("Inactive education feature passed the active-status predicate")
                stable_id = (props.get("GlobalID") or str(props["OBJECTID"])).strip("{}")
                normalized.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "source_id": f"{amenity}:{stable_id}",
                            "name": props["Nimi"],
                            "amenity": amenity,
                            "ownership": "municipal",
                            "official_type_code": props.get("Liik"),
                            "address": props.get("Aadress") or "",
                            "source_item": item_id,
                            "active": True,
                        },
                        "geometry": feature["geometry"],
                    }
                )
        pois_raw = {"type": "FeatureCollection", "features": normalized}
        pois_geojson.write_text(json.dumps(pois_raw, indent=2, ensure_ascii=False))
        pois_meta = {
            "sources": [school_item, kindergarten_item],
            "retrieved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "predicates": {"school": school_where, "kindergarten": kindergarten_where},
            "counts": counts,
            "matched": sum(counts.values()),
            "returned": len(normalized),
        }
        pois_meta_file.write_text(json.dumps(pois_meta, indent=2, ensure_ascii=False))
    if "retrieved_at" not in pois_meta:
        pois_meta["retrieved_at"] = datetime.datetime.fromtimestamp(
            pois_geojson.stat().st_mtime, datetime.timezone.utc
        ).isoformat()
        pois_meta_file.write_text(json.dumps(pois_meta, indent=2, ensure_ascii=False))

    # Inspect exact metadata from the real files
    with contextlib.closing(sqlite3.connect(f"{cadastre_gpkg.as_uri()}?mode=ro", uri=True)) as source:
        parcels_info = source.execute('SELECT count(*) FROM "Tartu maakond"').fetchone()[0]
        parcels_cols = [row[1] for row in source.execute('PRAGMA table_info("Tartu maakond")')]

    roads_raw = json.loads(roads_geojson.read_text())
    roads_count = len(roads_raw.get("features", []))
    roads_cols = list(roads_raw["features"][0]["properties"].keys()) + ["geometry"] if roads_count > 0 else []

    pois_raw = json.loads(pois_geojson.read_text())
    pois_count = len(pois_raw.get("features", []))
    pois_cols = list(pois_raw["features"][0]["properties"].keys()) + ["geometry"] if pois_count > 0 else []

    # Which daily snapshot this is: the archive's own Last-Modified when the
    # server gave one, else the moment we retrieved it. Never a fixed literal --
    # that is how a refreshed download comes to be filed under an older date.
    _cadastre_retrieved_at = cadastre_meta.get("retrieved_at") or datetime.datetime.fromtimestamp(
        cadastre_gpkg.stat().st_mtime, datetime.timezone.utc
    ).isoformat()
    _cadastre_snapshot_day = _http_date_to_day(cadastre_meta.get("last_modified")) or _cadastre_retrieved_at[:10]

    manifest = [
        {
            "key": "cadastral_parcels",
            "role": PROJECT["sources"]["cadastral_parcels"]["role"],
            "file": "Tartu_maakond_KATASTER_GPKG.zip → Tartu_maakond_KATASTER_GPKG.gpkg",
            "format": "GeoPackage (GPKG/SQLite, EPSG:3301)",
            "table_name": "Tartu maakond",
            "source_url": cadastre_url,
            "portal_page": "https://geoportaal.maaruum.ee/eng/spatial-data/cadastral-data-p310.html",
            "download_timestamp": _cadastre_retrieved_at,
            "version": f"Tartu_maakond_KATASTER_GPKG (daily snapshot {_cadastre_snapshot_day})",
            "published_at": _cadastre_snapshot_day,
            "etag": cadastre_meta.get("etag"),
            "size_bytes": cadastre_gpkg.stat().st_size,
            "rows": parcels_info,
            "n_columns": len(parcels_cols),
            "columns": parcels_cols,
            "sha256": _sha256(cadastre_gpkg),
        },
        {
            "key": "roads",
            "role": PROJECT["sources"]["roads"]["role"],
            "file": "etak_roads.geojson (WFS GetFeature query result)",
            "format": "GeoJSON (FeatureCollection, EPSG:3301)",
            "table_name": "etak:e_501_tee_j",
            "source_url": roads_wfs_url,
            "portal_page": "https://geoportaal.maaruum.ee/est/ruumiandmed/eesti-topograafia-andmekogu/laadi-etak-andmed-alla-p609.html",
            "download_timestamp": roads_meta["retrieved_at"],
            "version": "etak:e_501_tee_j completeness-verified snapshot",
            "rows": roads_count,
            "n_columns": len(roads_cols),
            "columns": roads_cols,
            "completeness": roads_meta,
            "sha256": _sha256(roads_geojson),
        },
        {
            "key": "education_pois",
            "role": PROJECT["sources"]["education_pois"]["role"],
            "file": "tartu_municipal_education.geojson (normalized official ArcGIS queries)",
            "format": "GeoJSON (FeatureCollection, EPSG:4326)",
            "table_name": "municipal_education",
            "source_url": "https://gis.tartulv.ee/arcgis/rest/services/Haridus",
            "portal_page": "https://geohub.tartulv.ee/",
            "download_timestamp": pois_meta["retrieved_at"],
            "version": f"ArcGIS items {school_item} + {kindergarten_item}",
            "rows": pois_count,
            "n_columns": len(pois_cols),
            "columns": pois_cols,
            "semantic_predicates": pois_meta["predicates"],
            "completeness": {"matched": pois_meta["matched"], "returned": pois_meta["returned"]},
            "sha256": _sha256(pois_geojson),
        },
    ]
    from routing import fetch_network

    manifest.append(fetch_network(ROOT, PROJECT["sources"]["pedestrian_network"]))
    (SOURCE / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("Source manifest recorded: %d parcels, %d roads, %d education POIs", parcels_info, roads_count, pois_count)
    return cadastre_gpkg, roads_geojson, pois_geojson, manifest


# ----------------------------- STEP 1-7: pipeline ---------------------------
PLACEHOLDER_EVIDENCE = {"", "todo", "tbd", "n/a", "na", "none", "placeholder", "example", "xxx"}


def apply_attribute_overrides(source_collection: dict, source_key: str) -> tuple[dict, list[dict]]:
    """Apply project.yaml `modify_attribute` overrides to a source FeatureCollection.

    The source file is never rewritten: this returns the EFFECTIVE collection
    (immutable source + override layer). Each override is only applied when

      1. its target feature exists in the source, and
      2. the asserted prior value (`change.from`) matches the source value, and
      3. its evidence is present and non-placeholder,

    otherwise it is reported as `rejected` with a reason. Merely declaring an
    override is not applying it (project-spec.md section 2.3).
    """
    effective = json.loads(json.dumps(source_collection))
    by_id = {f["properties"].get("source_id"): f for f in effective["features"]}
    results: list[dict] = []

    for override in PROJECT.get("overrides", []):
        if override.get("action") != "modify_attribute":
            continue
        target = override.get("target", {})
        if target.get("source") != source_key:
            continue

        feature = by_id.get(target.get("feature_id"))
        change = override.get("change", {})
        field, want_from, want_to = change.get("field"), change.get("from"), change.get("to")
        evidence = [
            e for e in override.get("evidence", [])
            if str(e.get("value", "")).strip().lower() not in PLACEHOLDER_EVIDENCE
        ]

        if feature is None:
            results.append({"id": override["id"], "status": "rejected",
                            "detail": f"target feature {target.get('feature_id')!r} not found in {source_key}"})
            continue
        if not evidence:
            results.append({"id": override["id"], "status": "rejected",
                            "detail": "evidence is missing or a placeholder"})
            continue
        actual_from = feature["properties"].get(field)
        if actual_from != want_from:
            results.append({"id": override["id"], "status": "rejected",
                            "detail": f"prior value mismatch on {field}: source={actual_from!r}, asserted={want_from!r}"})
            continue

        feature["properties"][f"{field}_source"] = actual_from
        feature["properties"][field] = want_to
        feature["properties"]["override_id"] = override["id"]
        feature["properties"]["override_origin"] = override.get("origin", override.get("created_by", "analyst"))
        results.append({
            "id": override["id"],
            "status": "applied",
            "detail": f"{target.get('feature_id')}.{field}: {want_from!r} -> {want_to!r}",
            "target": target.get("feature_id"),
            "field": field,
            "from": want_from,
            "to": want_to,
            "origin": override.get("origin", override.get("created_by", "analyst")),
        })

    # map_class drives both the MapLibre and QGIS categorized styles, so scenario
    # facilities stay visually distinct from authoritative ones.
    for feature in effective["features"]:
        props = feature["properties"]
        props.setdefault("active_source", props.get("active"))
        props["map_class"] = (
            props["amenity"] if props.get("active") is True else "scenario_inactive"
        )
        props["map_class_baseline"] = (
            props["amenity"] if props.get("active_source") is True else "scenario_inactive"
        )
    return effective, results


def calculate_network_access(con: duckdb.DuckDBPyConnection, effective_pois: dict) -> None:
    from routing import facility_isochrones

    settings = PROJECT["processing"]["routing"]
    if settings["minutes"] != list(WALK_THRESHOLDS_MINUTES) or settings["canonical_minutes"] != CANONICAL_WALK_MINUTES:
        raise RuntimeError("Routing thresholds must match the published dashboard thresholds")
    contours, evidence = facility_isochrones(ROOT, PROJECT["sources"]["pedestrian_network"], settings, effective_pois)
    con.execute("""
        CREATE OR REPLACE TABLE facility_isochrones
        (source_id VARCHAR, amenity VARCHAR, active BOOLEAN, minutes INTEGER, geometry GEOMETRY)
    """)
    con.executemany("""
        INSERT INTO facility_isochrones VALUES (?, ?, ?, ?, ST_GeomFromGeoJSON(?))
    """, [(feature["properties"]["source_id"], feature["properties"]["amenity"],
           feature["properties"]["active"], feature["properties"]["minutes"], json.dumps(feature["geometry"]))
          for feature in contours["features"]])
    repairs = []
    for source_id, minutes, before_area, after_area, geometry in con.execute("""
        SELECT source_id, minutes,
               ST_Area(ST_Transform(geometry, 'OGC:CRS84', 'EPSG:3301', always_xy := true)),
               ST_Area(ST_Transform(ST_CollectionExtract(ST_MakeValid(geometry), 3), 'OGC:CRS84', 'EPSG:3301', always_xy := true)),
               ST_AsGeoJSON(ST_CollectionExtract(ST_MakeValid(geometry), 3))
        FROM facility_isochrones WHERE NOT ST_IsValid(geometry)
    """).fetchall():
        if abs(before_area - after_area) > 1:
            raise RuntimeError(f"Isochrone repair exceeds 1 m² for {source_id} at {minutes} minutes")
        con.execute("UPDATE facility_isochrones SET geometry = ST_GeomFromGeoJSON(?) WHERE source_id = ? AND minutes = ?",
                    [geometry, source_id, minutes])
        for feature in contours["features"]:
            if feature["properties"]["source_id"] == source_id and feature["properties"]["minutes"] == minutes:
                feature["geometry"] = json.loads(geometry)
                feature["properties"]["topology_repaired"] = True
        repairs.append({"source_id": source_id, "minutes": minutes, "area_before_m2": before_area,
                        "area_after_m2": after_area, "method": "ST_CollectionExtract(ST_MakeValid(geometry), 3)"})
    evidence["topology_repairs"] = repairs
    (DERIVED / "facility_isochrones.json").write_text(json.dumps(contours, separators=(",", ":")))
    (VALIDATION / "routing-evidence.json").write_text(json.dumps(evidence, separators=(",", ":")))
    con.execute("UPDATE facility_isochrones SET geometry = ST_Transform(geometry, 'OGC:CRS84', 'EPSG:3301', always_xy := true)")
    invalid = con.execute("SELECT count(*) FROM facility_isochrones WHERE NOT ST_IsValid(geometry) OR ST_IsEmpty(geometry)").fetchone()[0]
    if invalid:
        raise RuntimeError(f"Valhalla returned {invalid} invalid or empty isochrones")
    con.execute("""
        CREATE OR REPLACE TABLE catchment_variants AS
        SELECT amenity, minutes, variant,
               count(*) AS facility_count,
               ST_Union_Agg(geometry ORDER BY source_id) AS geometry
        FROM facility_isochrones
        CROSS JOIN (VALUES ('effective'), ('baseline')) AS variants(variant)
        WHERE variant = 'baseline' OR active
        GROUP BY amenity, minutes, variant
    """)
    con.execute("""
        CREATE OR REPLACE TABLE parcel_network_access AS
        SELECT parcel.cadastral_id, catchment.amenity, catchment.variant, catchment.minutes,
               ST_Area(ST_Intersection(parcel.geometry, catchment.geometry)) > 0 AS reachable
        FROM candidate_parcels AS parcel CROSS JOIN catchment_variants AS catchment
    """)
    nonmonotonic = con.execute("""
        SELECT count(*) FROM parcel_network_access AS shorter
        JOIN parcel_network_access AS longer USING (cadastral_id, amenity, variant)
        WHERE shorter.minutes < longer.minutes AND shorter.reachable AND NOT longer.reachable
    """).fetchone()[0]
    if nonmonotonic:
        raise RuntimeError("Isochrone parcel membership is not monotonic; cannot publish sampled accessibility thresholds")
    for amenity, field in (("school", "school"), ("kindergarten", "kg")):
        for variant in ("effective", "baseline"):
            column = f"isochrone_{field}_{variant}_min"
            con.execute(f"ALTER TABLE candidate_parcels ADD COLUMN {column} INTEGER")
            con.execute(f"""
                UPDATE candidate_parcels AS parcel SET {column} = access.minutes
                FROM (
                    SELECT cadastral_id, min(minutes) AS minutes FROM parcel_network_access
                    WHERE amenity = ? AND variant = ? AND reachable GROUP BY cadastral_id
                ) AS access WHERE parcel.cadastral_id = access.cadastral_id
            """, [amenity, variant])
    con.execute("""
        UPDATE candidate_parcels SET suitability_tier = CASE
            WHEN isochrone_school_effective_min <= ? AND isochrone_kg_effective_min <= ? THEN ?
            WHEN isochrone_school_effective_min <= ? OR isochrone_kg_effective_min <= ? THEN ?
            ELSE ? END
    """, [CANONICAL_WALK_MINUTES, CANONICAL_WALK_MINUTES, SUITABILITY_TIERS["candidates_tier1"],
          CANONICAL_WALK_MINUTES, CANONICAL_WALK_MINUTES, SUITABILITY_TIERS["candidates_tier2"],
          SUITABILITY_TIERS["candidates_highway"]])
    for amenity, table in (("school", "school_catchment"), ("kindergarten", "kg_catchment")):
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT geometry FROM catchment_variants WHERE amenity = ? AND variant = 'effective' AND minutes = ?", [amenity, CANONICAL_WALK_MINUTES])


def cadastre_wkb(geometry: bytes) -> bytes:
    """Decode the standard GeoPackageBinary header, preserving its WKB payload."""
    if geometry is None or len(geometry) < 8 or geometry[:3] != b"GP\x00":
        raise ValueError("Invalid GeoPackage geometry header")
    flags = geometry[3]
    envelope = (flags >> 1) & 7
    if flags & 0xF0 or envelope > 4:
        raise ValueError("Unsupported empty, extended or reserved GeoPackage geometry")
    byte_order = "little" if flags & 1 else "big"
    if int.from_bytes(geometry[4:8], byte_order, signed=True) != 3301:
        raise ValueError("Cadastre geometry must use EPSG:3301")
    offset = 8 + (0, 32, 48, 48, 64)[envelope]
    if len(geometry) < offset + 5:
        raise ValueError("Truncated GeoPackage geometry")
    return geometry[offset:]


def load_cadastre(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute("""
        CREATE OR REPLACE TABLE parcels_raw
        (fid BIGINT, cadastral_id VARCHAR, address VARCHAR, municipality VARCHAR,
         settlement VARCHAR, land_use VARCHAR, area_m2 DOUBLE, geometry GEOMETRY('EPSG:3301'))
    """)
    with contextlib.closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as source:
        metadata = source.execute("SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name = 'Tartu maakond'").fetchall()
        if metadata != [("geom", 3301)]:
            raise ValueError(f"Unexpected cadastre geometry metadata: {metadata}")
        cursor = source.execute('SELECT fid, tunnus, l_aadress, ov_nimi, ay_nimi, siht1, pindala, geom FROM "Tartu maakond" ORDER BY fid')
        con.execute("BEGIN TRANSACTION")
        try:
            while rows := cursor.fetchmany(2000):
                fields = ("fid", "cadastral_id", "address", "municipality", "settlement", "land_use", "area_m2", "wkb")
                batch = [dict(zip(fields, (*row[:-1], cadastre_wkb(row[-1])))) for row in rows]
                con.execute("""
                    INSERT INTO parcels_raw
                    SELECT record.fid, record.cadastral_id, record.address, record.municipality,
                           record.settlement, record.land_use, record.area_m2,
                           ST_SetCRS(ST_GeomFromWKB(record.wkb), 'EPSG:3301')
                    FROM unnest(?) AS records(record)
                """, [batch])
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


def run_pipeline(
    con: duckdb.DuckDBPyConnection, cadastre_gpkg: Path, roads_geojson: Path, pois_geojson: Path
) -> list[dict]:
    """Run every processing step; returns the per-override application results."""
    t_3301 = pyproj.Transformer.from_crs(4326, 3301, always_xy=True)
    t_4326 = pyproj.Transformer.from_crs(ANALYSIS_CRS, STORAGE_CRS, always_xy=True)

    # STEP 1 — Load authoritative cadastral parcels from GeoPackage (EPSG:3301)
    load_cadastre(con, cadastre_gpkg)

    # STEP 2 — Size and land-use filter (area >= 20000 m2, commercial/agricultural/production, Tartu linn)
    con.execute(f"""
        CREATE OR REPLACE TABLE large_parcels AS
        SELECT *
        FROM parcels_raw
        WHERE area_m2 >= {MIN_PARCEL_AREA_M2}
          AND land_use IN ('MAATULUNDUSMAA', 'TOOTMISMAA', 'ARIMAA')
          AND municipality = 'Tartu linn'
    """)

    # STEP 3 — Load completeness-verified official main roads.
    con.execute(f"""
        CREATE OR REPLACE TABLE official_roads AS
        SELECT ST_GeomFromGeoJSON(f.geometry) AS geometry,
               f.properties.nimetus AS name,
               f.properties.tyyp_tekst AS road_class
        FROM (
            SELECT unnest(features) as f FROM read_json_auto('{roads_geojson}')
        )
        WHERE f.properties.tyyp_tekst IN ('Põhimaantee', 'Tugimaantee')
    """)

    # STEP 4 — Load the hypothetical connector into a separate scenario table.
    con.execute("CREATE OR REPLACE TABLE scenario_roads (geometry GEOMETRY, name VARCHAR, road_class VARCHAR)")
    planned_road_file = OVERRIDES / "planned-road.geojson"
    if planned_road_file.exists():
        plan_raw = json.loads(planned_road_file.read_text())
        for ft in plan_raw.get("features", []):
            coords_3301 = [t_3301.transform(x, y) for x, y in ft["geometry"]["coordinates"]]
            wkt = "LINESTRING(" + ", ".join(f"{x} {y}" for x, y in coords_3301) + ")"
            con.execute(
                "INSERT INTO scenario_roads VALUES (ST_GeomFromText(?), ?, ?)",
                [wkt, ft["properties"].get("name", "Hypothetical connector road"), "Scenario (OVERRIDE-002)"],
            )

    # STEP 5 — Load education POIs, verify source semantics, then apply the
    # declared attribute overrides. Immutable source + override = effective input:
    # the source file on disk is never rewritten here.
    pois_raw = json.loads(pois_geojson.read_text())
    for f in pois_raw.get("features", []):
        props = f["properties"]
        if props.get("ownership") != "municipal" or props.get("active") is not True:
            raise RuntimeError(f"Education semantic predicate failed for {props.get('source_id')}")

    effective_pois, override_results = apply_attribute_overrides(pois_raw, "education_pois")
    (DERIVED / "education_pois.json").write_text(
        json.dumps(effective_pois, indent=2, ensure_ascii=False)
    )
    for result in override_results:
        log.info("Override %s: %s (%s)", result["id"], result["status"], result.get("detail", ""))

    # STEP 5b — Only facilities that are active AFTER overrides enter the analysis.
    # The `_source` twins hold the same facilities as the authoritative source states
    # them, with no override applied. They never feed the accepted result; they exist
    # so the view can show what the scenario actually costs, measured the same way.
    facility_tables = ("schools", "kindergartens", "schools_source", "kindergartens_source")
    for tbl in facility_tables:
        con.execute(
            f"CREATE OR REPLACE TABLE {tbl} "
            "(source_id VARCHAR, name VARCHAR, ownership VARCHAR, active BOOLEAN, geometry GEOMETRY)"
        )

    for f in effective_pois["features"]:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        x, y = t_3301.transform(lon, lat)
        base = "schools" if props["amenity"] == "school" else "kindergartens"
        targets = []
        if props.get("active") is True:
            targets.append(base)
        if props.get("active_source") is True:
            targets.append(f"{base}_source")
        for tbl in targets:
            con.execute(
                f"INSERT INTO {tbl} VALUES (?, ?, ?, ?, ST_Point(?, ?))",
                [props["source_id"], props["name"], props["ownership"], props["active"], x, y],
            )

    # STEP 6 — Multi-criteria Spatial Evaluation:
    # - Distance to highway network (<= 2000 m)
    con.execute(f"""
        CREATE OR REPLACE TABLE candidate_parcels AS
        WITH official_road_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM official_roads),
             scenario_road_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM scenario_roads),
             school_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM schools),
             kg_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM kindergartens),
             school_src_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM schools_source),
             kg_src_geom AS (SELECT ST_Union_Agg(geometry) AS u FROM kindergartens_source)
        SELECT p.cadastral_id,
               p.address,
               p.municipality,
               p.settlement,
               p.land_use,
               p.area_m2,
               round(least(ST_Distance(p.geometry, r.u), ST_Distance(p.geometry, sr.u)), 1) AS dist_main_road_m,
               round(ST_Distance(p.geometry, r.u), 1) AS dist_official_road_m,
               round(ST_Distance(p.geometry, sr.u), 1) AS dist_scenario_road_m,
               CASE WHEN ST_Distance(p.geometry, sr.u) < ST_Distance(p.geometry, r.u)
                    THEN 'scenario' ELSE 'official' END AS nearest_road_source,
               round(ST_Distance(p.geometry, s.u), 1) AS dist_school_m,
               round(ST_Distance(p.geometry, k.u), 1) AS dist_kg_m,
               round(ST_Distance(p.geometry, ss.u), 1) AS dist_school_baseline_m,
               round(ST_Distance(p.geometry, ks.u), 1) AS dist_kg_baseline_m,
               ''::VARCHAR AS suitability_tier,
               p.geometry
        FROM large_parcels p, official_road_geom r, scenario_road_geom sr, school_geom s, kg_geom k,
             school_src_geom ss, kg_src_geom ks
        WHERE least(ST_Distance(p.geometry, r.u), ST_Distance(p.geometry, sr.u)) <= {MAX_ROAD_DISTANCE_M}
    """)
    n_cand = con.execute("SELECT count(*) FROM candidate_parcels").fetchone()[0]
    log.info("Identified %d road-accessible candidate parcels across all suitability tiers", n_cand)

    calculate_network_access(con, effective_pois)

    # STEP 8 — Export derived outputs
    DERIVED.mkdir(parents=True, exist_ok=True)
    gpkg_out = DERIVED / "final-candidates.gpkg"
    con.execute(f"COPY candidate_parcels TO '{gpkg_out}' (FORMAT GDAL, DRIVER 'GPKG')")
    with sqlite3.connect(gpkg_out) as gpkg:
        gpkg.execute("UPDATE gpkg_contents SET last_change = '2026-08-25T00:00:00.000Z'")
    con.execute(f"COPY candidate_parcels TO '{DERIVED / 'final-candidates.parquet'}' (FORMAT PARQUET)")

    # 1. Transform candidate parcels to EPSG:4326 for web rendering
    def transform_coords(coords):
        if isinstance(coords[0], (int, float)):
            return list(t_4326.transform(coords[0], coords[1]))
        return [transform_coords(c) for c in coords]

    feats = con.execute("""
        SELECT cadastral_id, address, municipality, settlement, land_use,
               area_m2, dist_main_road_m, dist_official_road_m, dist_scenario_road_m,
               nearest_road_source, dist_school_m, dist_kg_m,
               dist_school_baseline_m, dist_kg_baseline_m,
               isochrone_school_effective_min, isochrone_kg_effective_min,
               isochrone_school_baseline_min, isochrone_kg_baseline_min, suitability_tier,
               ST_AsGeoJSON(geometry)
        FROM candidate_parcels
    """).fetchall()

    coll = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4326"}}, "features": []}
    for row in feats:
        (fid, addr, mun, sett, lu, area, dist_r, dist_ro, dist_rs, road_source,
         dist_s, dist_k, dist_s_base, dist_k_base, w_s, w_k, w_s_base, w_k_base, tier, gj_str) = row
        g = json.loads(gj_str)
        g["coordinates"] = transform_coords(g["coordinates"])
        coll["features"].append({
            "type": "Feature",
            "properties": {
                "cadastral_id": fid,
                "address": addr or "",
                "municipality": mun or "",
                "settlement": sett or "",
                "land_use": lu or "",
                "area_m2": float(area),
                "dist_main_road_m": float(dist_r),
                "dist_official_road_m": float(dist_ro),
                "dist_scenario_road_m": float(dist_rs),
                "nearest_road_source": road_source,
                "dist_school_m": float(dist_s),
                "dist_kg_m": float(dist_k),
                "dist_school_baseline_m": float(dist_s_base),
                "dist_kg_baseline_m": float(dist_k_base),
                "isochrone_school_effective_min": w_s,
                "isochrone_kg_effective_min": w_k,
                "isochrone_school_baseline_min": w_s_base,
                "isochrone_kg_baseline_min": w_k_base,
                "suitability_tier": tier,
            },
            "geometry": g,
        })
    (DERIVED / "final-candidates.json").write_text(json.dumps(coll, indent=2))

    # 2. Export main roads as GeoJSON
    road_feats = con.execute("SELECT name, road_class, ST_AsGeoJSON(geometry) FROM official_roads").fetchall()
    roads_coll = {"type": "FeatureCollection", "features": []}
    for rname, rclass, r_gj_str in road_feats:
        rg = json.loads(r_gj_str)
        rg["coordinates"] = transform_coords(rg["coordinates"])
        roads_coll["features"].append({
            "type": "Feature",
            "properties": {"name": rname or "", "class": rclass or ""},
            "geometry": rg,
        })
    (DERIVED / "main_roads.json").write_text(json.dumps(roads_coll, indent=2))

    # 3. Export Catchments GeoJSON
    school_gj_str = con.execute("SELECT ST_AsGeoJSON(geometry) FROM school_catchment").fetchone()[0]
    kg_gj_str = con.execute("SELECT ST_AsGeoJSON(geometry) FROM kg_catchment").fetchone()[0]

    def geom_to_4326(gj_str):
        g = json.loads(gj_str)
        g["coordinates"] = transform_coords(g["coordinates"])
        return g

    catchments_coll = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "Municipal schools: 25-minute pedestrian catchment", "type": "school_catchment"},
                "geometry": geom_to_4326(school_gj_str),
            },
            {
                "type": "Feature",
                "properties": {"name": "Municipal kindergartens: 25-minute pedestrian catchment", "type": "kindergarten_catchment"},
                "geometry": geom_to_4326(kg_gj_str),
            },
        ],
    }
    (DERIVED / "education_catchments.json").write_text(json.dumps(catchments_coll, indent=2))

    variant_feats = []
    for amenity, minutes, variant, count, geometry in con.execute(
        "SELECT amenity, minutes, variant, facility_count, ST_AsGeoJSON(geometry) "
        "FROM catchment_variants ORDER BY amenity, variant, minutes"
    ).fetchall():
        variant_feats.append({
            "type": "Feature",
            "properties": {
                "name": f"Municipal {amenity}: {minutes}-minute walk catchment",
                "type": f"{amenity}_catchment", "variant": variant, "minutes": minutes,
                "canonical": variant == "effective" and minutes == CANONICAL_WALK_MINUTES,
                "facility_count": count,
            },
            "geometry": _round_geometry(geom_to_4326(geometry)),
        })
    (DERIVED / "education_catchment_variants.json").write_text(
        json.dumps({"type": "FeatureCollection", "features": variant_feats}, indent=2)
    )
    log.info(
        "Exported %d catchment variants (%d walking thresholds x facility sets)",
        len(variant_feats),
        len(WALK_THRESHOLDS_MINUTES),
    )

    # 4. Education POIs were already exported in effective (post-override) form
    #    by STEP 5; the immutable source stays in data/source untouched.
    log.info("Derived datasets exported: GPKG, Parquet, GeoJSON (candidates, catchments, pois, roads)")
    return override_results


# ------------------------------ STEP 8: validation --------------------------
def _validate_qgis_project(con: duckdb.DuckDBPyConnection) -> tuple[dict, dict]:
    qgz = ROOT / "project.qgz"
    errors: list[str] = []
    try:
        with zipfile.ZipFile(qgz) as archive:
            bad_member = archive.testzip()
            if bad_member:
                errors.append(f"corrupt archive member: {bad_member}")
            xml = ET.fromstring(archive.read("project.qgs"))
        project_layers = xml.findall("./projectlayers/maplayer")
        project_ids = {layer.findtext("id") for layer in project_layers}
        tree_ids = {layer.attrib.get("id") for layer in xml.findall(".//layer-tree-layer")}
        if project_ids != tree_ids:
            errors.append("layer-tree IDs do not match project layer IDs")
        for layer in project_layers:
            source = layer.findtext("datasource") or ""
            local = source.split("|", 1)[0]
            if local.startswith("./") and not (ROOT / local[2:]).exists():
                errors.append(f"missing datasource: {local}")
        # Every layer group the manifest declares must exist in the tree. This
        # is the drift that shipped once already: a .qgz organised into four
        # thematic groups while project.yaml promised seven semantic ones.
        tree_groups = {
            _normalize_group_name(group.attrib.get("name", ""))
            for group in xml.findall(".//layer-tree-group")
        }
        declared_groups = PROJECT["presentation"]["map"]["layer_groups"]
        absent = [
            group["id"]
            for group in declared_groups
            if not {_normalize_group_name(group["id"]), _normalize_group_name(group.get("title"))} & tree_groups
        ]
        if absent:
            errors.append(f"manifest layer groups absent from the QGIS layer tree: {absent}")

        # Each tier is its own layer filtered by an OGR subset, so the styled
        # domain is the set of tiers the subsets actually select. A tier the
        # data carries but no layer selects would be invisible in QGIS while
        # the dashboard still shows it.
        selected = {
            match
            for layer in project_layers
            for match in re.findall(
                r'"suitability_tier"\s*=\s*\'([^\']*)\'', layer.findtext("datasource") or ""
            )
        }
        actual = {
            row[0]
            for row in con.execute("SELECT DISTINCT suitability_tier FROM candidate_parcels").fetchall()
        }
        if selected != actual:
            errors.append(f"candidate tier layers mismatch: selected={sorted(selected)}, actual={sorted(actual)}")

        # The POI classes are split across the verified-source layer and the
        # override layer; together they must cover every class in the data.
        poi_styled = {
            category.attrib["value"]
            for layer in project_layers
            if (layer.findtext("datasource") or "").endswith("education_pois.json")
            or "education_pois.json|" in (layer.findtext("datasource") or "")
            for renderer in layer.findall("renderer-v2")
            for category in renderer.findall("./categories/category")
        }
        poi_actual = set(_poi_class_counts())
        if not poi_actual.issubset(poi_styled):
            errors.append(f"POI style domain mismatch: styled={sorted(poi_styled)}, actual={sorted(poi_actual)}")
        roads = json.loads((DERIVED / "main_roads.json").read_text())
        if any(f.get("properties", {}).get("class", "").startswith("Scenario") for f in roads["features"]):
            errors.append("scenario road leaked into authoritative ETAK presentation layer")
    except Exception as exc:
        errors.append(str(exc))

    static_check = {
        "id": "qgis_project_static_valid",
        "status": "passed" if not errors else "failed",
        "project": "project.qgz",
        "errors": errors,
    }

    try:
        from qgis.core import QgsApplication, QgsProject  # type: ignore

        app = QgsApplication([], False)
        app.initQgis()
        loaded = QgsProject.instance().read(str(qgz))
        invalid = [layer.name() for layer in QgsProject.instance().mapLayers().values() if not layer.isValid()]
        app.exitQgis()
        runtime_check = {
            "id": "qgis_runtime_load",
            "status": "passed" if loaded and not invalid else "failed",
            "invalid_layers": invalid,
        }
    except ImportError:
        runtime_check = {
            "id": "qgis_runtime_load",
            "status": "not_testable",
            "reason": "PyQGIS is not installed in this execution environment",
        }
    except Exception as exc:
        runtime_check = {"id": "qgis_runtime_load", "status": "failed", "reason": str(exc)}
    return static_check, runtime_check


def _check_manifest_graph() -> dict:
    """Every step input must resolve to a source key or an earlier step's output."""
    produced = set(PROJECT["sources"])
    dangling: list[str] = []
    for step in PROJECT["processing"]["steps"]:
        consumed: list[str] = []
        for key in ("input", "inputs", "source", "target"):
            value = step.get(key)
            if isinstance(value, str):
                consumed.append(value)
            elif isinstance(value, list):
                consumed.extend(value)
        dangling += [f"{step['id']}: unresolved input {name!r}" for name in consumed if name not in produced]
        out = step.get("output")
        if isinstance(out, str):
            produced.update(part.strip() for part in out.split(","))
    step_ids = {step["id"] for step in PROJECT["processing"]["steps"]}
    dangling += [
        f"outputs.{name}: generated_by {spec.get('generated_by')!r} is not a step"
        for name, spec in PROJECT.get("outputs", {}).items()
        if spec.get("generated_by") not in step_ids
    ]
    return {
        "id": "manifest_graph_resolves",
        "status": "passed" if not dangling else "failed",
        "steps_checked": len(step_ids),
        "symbols_resolved": len(produced),
        "errors": dangling,
    }


def _check_view_controls(con: duckdb.DuckDBPyConnection) -> dict:
    """The view's canonical control positions must equal the accepted thresholds.

    A reconfigurable dashboard tells the reader "this is the accepted run" at one
    specific control position. If project.yaml drifts from the thresholds the
    pipeline ran, that claim silently becomes false, so it fails the run instead.
    """
    filters, scenarios = _declared_controls()
    land_use = [row[0] for row in con.execute(
        "SELECT DISTINCT land_use FROM candidate_parcels ORDER BY land_use"
    ).fetchall()]
    declared_overrides = {o["id"] for o in PROJECT.get("overrides", [])}

    mismatches = []

    def expect(control_id, key, actual):
        declared = filters.get(control_id, {}).get(key)
        if declared != actual:
            mismatches.append(f"{control_id}.{key}: declared {declared!r}, pipeline ran {actual!r}")

    expect("min_area", "canonical", MIN_PARCEL_AREA_M2)
    expect("max_road_distance", "canonical", MAX_ROAD_DISTANCE_M)
    expect("education_threshold", "canonical", CANONICAL_WALK_MINUTES)
    expect("land_use", "canonical", land_use)

    options = list(filters.get("education_threshold", {}).get("options", []))
    if options != list(WALK_THRESHOLDS_MINUTES):
        mismatches.append(
            f"education_threshold.options: declared {options}, materialised {list(WALK_THRESHOLDS_MINUTES)}"
        )
    for scenario in scenarios.values():
        override_id = scenario.get("override")
        if override_id and override_id not in declared_overrides:
            mismatches.append(f"{scenario['id']} targets unknown override {override_id}")
        if override_id and not scenario.get("canonical", True):
            mismatches.append(
                f"{scenario['id']} is declared off by default while {override_id} is applied by the run"
            )

    return {
        "id": "view_controls_match_pipeline",
        "status": "passed" if not mismatches else "failed",
        "controls": sorted(filters) + sorted(scenarios),
        "mismatches": mismatches,
    }


def write_validation(con: duckdb.DuckDBPyConnection, run_id: str, override_results: list[dict]) -> dict:
    def n(q):
        return int(con.execute(q).fetchone()[0])

    n_candidates = n("SELECT COUNT(*) FROM candidate_parcels")
    n_tier1 = n("SELECT COUNT(*) FROM candidate_parcels WHERE isochrone_school_effective_min <= 25 AND isochrone_kg_effective_min <= 25")
    bad_geom = n("SELECT COUNT(*) FROM candidate_parcels WHERE NOT ST_IsValid(geometry)")
    dup_ids = n("SELECT COUNT(*) - COUNT(DISTINCT cadastral_id) FROM candidate_parcels")
    null_ids = n("SELECT COUNT(*) FROM candidate_parcels WHERE cadastral_id IS NULL")
    out_of_range = n("SELECT COUNT(*) FROM candidate_parcels WHERE area_m2 <= 0 OR area_m2 >= 100000000")
    bad_road_distance = n("SELECT COUNT(*) FROM candidate_parcels WHERE dist_main_road_m > 2000")
    bad_education_semantics = n(
        "SELECT (SELECT COUNT(*) FROM schools WHERE ownership <> 'municipal' OR NOT active) + "
        "(SELECT COUNT(*) FROM kindergartens WHERE ownership <> 'municipal' OR NOT active)"
    )
    gpkg_meta = con.execute(f"SELECT layers FROM ST_Read_Meta('{DERIVED / 'final-candidates.gpkg'}')").fetchone()[0]
    crs = gpkg_meta[0]["geometry_fields"][0]["crs"]
    crs_ok = crs.get("auth_name") == "EPSG" and str(crs.get("auth_code")) == "3301"
    road_meta = json.loads((SOURCE / "etak_main_roads.meta.json").read_text())
    education_meta = json.loads((SOURCE / "tartu_municipal_education.meta.json").read_text())
    complete = (
        road_meta["matched"] == road_meta["returned"]
        and education_meta["matched"] == education_meta["returned"]
    )
    override_features = json.loads((OVERRIDES / "planned-road.geojson").read_text())["features"]
    scenario_rows = n("SELECT COUNT(*) FROM scenario_roads")
    geometry_override_ok = (
        scenario_rows == len(override_features)
        and all(f["properties"].get("geometry_origin") == "scenario" for f in override_features)
    )
    # Every declared override must have an application result; attribute overrides
    # come back from the pipeline, the scenario geometry is verified here.
    override_status = list(override_results) + [
        {
            "id": "OVERRIDE-002",
            "status": "applied" if geometry_override_ok else "rejected",
            "detail": f"{scenario_rows} scenario road geometry loaded from data/overrides/planned-road.geojson",
        }
    ]
    declared_ids = {o["id"] for o in PROJECT.get("overrides", [])}
    reported_override_ids = {o["id"] for o in override_status}
    for missing in sorted(declared_ids - reported_override_ids):
        override_status.append({"id": missing, "status": "not_testable",
                                "detail": "declared in project.yaml but not evaluated by this run"})
    override_ok = bool(override_status) and all(o["status"] == "applied" for o in override_status)
    poi_counts = _poi_class_counts()
    qgis_static, qgis_runtime = _validate_qgis_project(con)

    checks = [
        {
            "id": "geometry_valid",
            "status": "passed" if bad_geom == 0 else "failed",
            "features_checked": n_candidates,
            "invalid_count": bad_geom,
        },
        {
            "id": "no_duplicate_cadastral_id",
            "status": "passed" if dup_ids == 0 else "failed",
            "duplicates": dup_ids,
        },
        {
            "id": "crs_known",
            "status": "passed" if crs_ok else "failed",
            "expected": "EPSG:3301",
            "actual": f"{crs.get('auth_name')}:{crs.get('auth_code')}",
        },
        {
            "id": "no_null_cadastral_id",
            "status": "passed" if null_ids == 0 else "failed",
            "nulls": null_ids,
        },
        {
            "id": "row_count_gt_zero",
            "status": "passed" if n_candidates > 0 else "failed",
            "rows": n_candidates,
            "prime_tier1_rows": n_tier1,
        },
        {
            "id": "parcel_area_range",
            "status": "passed" if out_of_range == 0 else "failed",
            "out_of_range_count": out_of_range,
        },
        {
            "id": "highway_distance_check",
            "status": "passed" if bad_road_distance == 0 else "failed",
            "out_of_range_count": bad_road_distance,
        },
        {
            "id": "source_semantics_verified",
            "status": "passed" if bad_education_semantics == 0 else "failed",
            "invalid_education_rows": bad_education_semantics,
            "school_predicate": education_meta["predicates"]["school"],
            "kindergarten_predicate": education_meta["predicates"]["kindergarten"],
        },
        {
            "id": "education_ownership_check",
            "status": "passed" if bad_education_semantics == 0 else "failed",
            "invalid_rows": bad_education_semantics,
        },
        {
            "id": "source_result_complete",
            "status": "passed" if complete else "failed",
            "roads": {"matched": road_meta["matched"], "returned": road_meta["returned"]},
            "education": {"matched": education_meta["matched"], "returned": education_meta["returned"]},
        },
        {
            "id": "overrides_applied",
            "status": "passed" if override_ok else "failed",
            "declared": sorted(declared_ids),
            "results": override_status,
            "effective_education_pois": poi_counts,
        },
        _check_manifest_graph(),
        _check_view_controls(con),
        qgis_static,
        qgis_runtime,
        {
            "id": "education_source_license",
            "status": "warning",
            "reason": "The authoritative Tartu ArcGIS items do not publish explicit license text",
        },
        {
            "id": "education_access_method",
            "status": "passed",
            "reason": "Reverse Valhalla pedestrian isochrones; positive-area parcel intersection; 25-minute accepted threshold",
            "facility_contours": n("SELECT count(*) FROM facility_isochrones"),
            "evidence": "validation/routing-evidence.json",
            "topology_repairs": json.loads((VALIDATION / "routing-evidence.json").read_text()).get("topology_repairs", []),
            "limitations": "OSM completeness, approximate contour boundaries and unverified parcel/facility entrances",
        },
    ]
    expected_checks = set(PROJECT["validation"]["required"])
    expected_checks.update(check["name"] for check in PROJECT["validation"].get("domain_checks", []))
    reported_ids = [check["id"] for check in checks]
    parity_ok = (
        all(reported_ids.count(check_id) == 1 for check_id in expected_checks - {"manifest_report_parity"})
        and expected_checks.issubset(set(reported_ids) | {"manifest_report_parity"})
    )
    checks.insert(
        -2,
        {
            "id": "manifest_report_parity",
            "status": "passed" if parity_ok else "failed",
            "expected": sorted(expected_checks),
            "reported": sorted(set(reported_ids) | {"manifest_report_parity"}),
        },
    )
    if any(c["status"] == "failed" for c in checks):
        status = "failed"
    elif any(c["status"] in ("warning", "not_testable") for c in checks):
        status = "warning"
    else:
        status = "passed"
    report = {
        "run_id": run_id,
        "schema": "openmapstack-project/v1",
        "status": status,
        "checks": checks,
        "candidate_count": n_candidates,
        "prime_tier1_count": n_tier1,
        "sources": {k: v.get("source_url") for k, v in PROJECT["sources"].items()},
        "overrides": override_status,
    }
    VALIDATION.mkdir(parents=True, exist_ok=True)
    (VALIDATION / "latest-report.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("Validation report written (status: %s)", status)
    return report


def _combined_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: str(value)):
        relative = str(path.relative_to(ROOT)).encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def finalize_run(report: dict, manifest: list[dict], started_at: str) -> None:
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    input_paths = [
        path
        for directory in (SOURCE, OVERRIDES)
        for path in directory.rglob("*")
        if path.is_file()
    ]
    implementation = PROJECT["runtime"]["implementation"]
    for relative in [implementation["pipeline"], *implementation.get("dependencies", [])]:
        path = ROOT / relative
        if path.is_dir():
            input_paths.extend(item for item in path.rglob("*") if item.is_file())
        else:
            input_paths.append(path)
    input_paths = sorted(set(input_paths))
    output_paths = [
        DERIVED / "final-candidates.gpkg",
        DERIVED / "final-candidates.parquet",
        DERIVED / "final-candidates.json",
        DERIVED / "education_catchments.json",
        DERIVED / "education_catchment_variants.json",
        DERIVED / "education_pois.json",
        DERIVED / "main_roads.json",
        DERIVED / "facility_isochrones.json",
        VALIDATION / "routing-evidence.json",
        ROOT / "project.qgz",
    ]
    inputs_hash = _combined_hash(input_paths)
    outputs_hash = _combined_hash(output_paths)
    report["inputs_hash"] = inputs_hash
    report["outputs_hash"] = outputs_hash

    run_record = {
        "run_id": report["run_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "status": report["status"],
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
        "source_manifest": "data/source/manifest.json",
        "validation_report": "validation/latest-report.json",
        "sources": [{"key": item["key"], "sha256": item.get("sha256")} for item in manifest],
        "inputs": [{"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for path in input_paths],
        "outputs": [{"path": str(path.relative_to(ROOT)), "sha256": _sha256(path)} for path in output_paths],
        "environment": {
            "python": os.sys.version.split()[0],
            "duckdb": duckdb.__version__,
            "pyproj": pyproj.__version__,
            **PROJECT["processing"]["routing"]["versions"],
        },
    }
    run_file = RUNS / f"{report['run_id']}.json"
    run_file.write_text(json.dumps(run_record, indent=2, ensure_ascii=False))
    (VALIDATION / "latest-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    PROJECT["project"]["updated_at"] = completed_at
    PROJECT["project"]["status"] = "validated" if report["status"] == "passed" else report["status"]
    for item in manifest:
        source = PROJECT["sources"].get(item["key"])
        if not source:
            continue
        source["access"]["retrieved_at"] = item["download_timestamp"]
        source["access"]["downloaded_at"] = item["download_timestamp"]
        if "file" in source["access"]:
            source["access"]["file"]["row_count"] = item["rows"]
            # Measured, not declared: a size left at whatever prose once said
            # describes a file that is no longer there.
            if item.get("size_bytes") is not None:
                source["access"]["file"]["size_bytes"] = item["size_bytes"]
        # Snapshot identity travels with the bytes for a mutable-URL source, so
        # write back whatever the response actually reported.
        if item.get("version"):
            source.setdefault("version", {})["identifier"] = item["version"]
        if item.get("published_at"):
            source.setdefault("version", {})["published_at"] = item["published_at"]
        if item.get("etag"):
            source.setdefault("version", {})["etag"] = item["etag"]
        completeness = item.get("completeness")
        if completeness and item["key"] == "roads":
            source["selection"]["completeness"] = {
                key: completeness[key]
                for key in ("matched", "returned", "page_size", "pages")
            }
        elif completeness:
            source["selection"]["completeness"] = completeness
    PROJECT["runs"]["latest"] = {
        "id": report["run_id"],
        "started_at": started_at,
        "completed_at": completed_at,
        "status": report["status"],
        "inputs_hash": inputs_hash,
        "outputs_hash": outputs_hash,
        "record": {"path": str(run_file.relative_to(ROOT))},
        "validation_report": {"path": "validation/latest-report.json"},
    }
    (ROOT / "project.yaml").write_text(
        yaml.safe_dump(PROJECT, sort_keys=False, allow_unicode=True, width=100)
    )


# ----------------------------- QGIS project (.qgz) -------------------------
def _poi_class_counts() -> dict[str, int]:
    """map_class -> count over the effective (post-override) POI layer."""
    counts: dict[str, int] = {}
    pois = json.loads((DERIVED / "education_pois.json").read_text())
    for feature in pois["features"]:
        key = feature["properties"].get("map_class", "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def write_qgis_project(con: duckdb.DuckDBPyConnection) -> Path:
    """Invoke the optional desktop adapter for this combined worked example."""
    import qgis_delivery
    return qgis_delivery.write_qgis_project(con, ROOT=ROOT, PROJECT=PROJECT,
        ANALYSIS_CRS=ANALYSIS_CRS, STORAGE_CRS=STORAGE_CRS,
        SUITABILITY_TIERS=SUITABILITY_TIERS,
        scenario_inactive_count=_poi_class_counts().get("scenario_inactive", 0))


# ------------------------------ STEP 9: dashboard ---------------------------
# The dashboard is a VIEW over the project, never the definition of the analysis.
# Python assembles a semantic view descriptor from project.yaml + the run's
# artifacts; dashboard-template.html renders it. Nothing analytical is decided here.

# Stable semantic roles -> concrete symbology, shared by the map, the legend and
# the QGIS mirror. Agents must not invent per-run colors (project-spec.md s.3).
TIER_STYLE = [
    {
        "id": "tier1",
        "role": "primary_result",
        "label": "Prime",
        "fill": "#22a06b",
        "line": "#8ee0b8",
        "opacity": 0.72,
        "canonical_prefix": "Tier 1",
    },
    {
        "id": "tier2",
        "role": "secondary_result",
        "label": "Good",
        "fill": "#d98324",
        "line": "#f6cf8a",
        "opacity": 0.58,
        "canonical_prefix": "Tier 2",
    },
    {
        "id": "tier3",
        "role": "constraint",
        "label": "Road access only",
        "fill": "#5b6b7d",
        "line": "#a9b6c4",
        "opacity": 0.32,
        "canonical_prefix": "Tier 3",
    },
]

# Renderer bindings for the layer groups declared in project.yaml. The project
# says WHAT to show; this table says which MapLibre layers realise it.
LAYER_BINDINGS = {
    "candidates_tier1": {"layers": ["tier1-fill", "tier1-line"], "swatch": "fill:tier1", "count": "tier1"},
    "candidates_tier2": {"layers": ["tier2-fill", "tier2-line"], "swatch": "fill:tier2", "count": "tier2"},
    "candidates_highway": {"layers": ["tier3-fill", "tier3-line"], "swatch": "fill:tier3", "count": "tier3"},
    "catchments": {
        "layers": ["school-catchment-fill", "school-catchment-line", "kg-catchment-fill", "kg-catchment-line"],
        "swatch": "buffer",
    },
    "education_pois": {"layers": ["pois"], "swatch": "dots", "count": "facilities"},
    "user_overrides": {
        "layers": [
            "planned-road", "scenario-pois", "draft-overrides-fill",
            "draft-overrides-line", "draft-overrides-point",
        ],
        "swatch": "scenario",
        "count": "overrides",
    },
    "infrastructure": {"layers": ["main-roads"], "swatch": "road"},
}


def _source_cards(manifest: list[dict]) -> list[dict]:
    """Flatten the runtime manifest into inspectable provenance cards."""
    cards = []
    for m in manifest:
        key = m["key"]
        declared = PROJECT["sources"].get(key, {})
        columns = m.get("columns")
        cards.append({
            "key": key,
            "provider": declared.get("provider", "Unknown"),
            "license": (declared.get("license") or {}).get("name") if isinstance(declared.get("license"), dict) else declared.get("license"),
            "file": m.get("file", "n/a"),
            "table": m.get("table_name", "n/a"),
            "rows": m.get("rows"),
            "columns_n": m.get("n_columns"),
            "columns": columns if isinstance(columns, list) else ([] if columns is None else [str(columns)]),
            "downloaded_at": m.get("download_timestamp", "n/a"),
            "version": m.get("version", "n/a"),
            "sha256": m.get("sha256", ""),
            "source_url": m.get("source_url", declared.get("source_url", "")),
            "portal_page": m.get("portal_page", declared.get("portal_page", "")),
            "completeness": m.get("completeness"),
        })
    return cards


def _control_key(control_id: str) -> str:
    """`scenario_road` -> `scenarioRoad`: the state key the view uses."""
    head, *rest = control_id.split("_")
    return head + "".join(word.capitalize() for word in rest)


def _declared_controls() -> tuple[dict, dict]:
    """The filter and scenario declarations from project.yaml, keyed by id."""
    controls = PROJECT["presentation"].get("controls", {})
    filters = {f["id"]: f for f in controls.get("filters", [])}
    scenarios = {sc["id"]: sc for sc in controls.get("scenarios", [])}
    return filters, scenarios


def _override_cards() -> list[dict]:
    """Overrides, each tied to the control that switches it on and off."""
    _, scenarios = _declared_controls()
    controls = {
        sc["override"]: _control_key(sc["id"])
        for sc in scenarios.values() if sc.get("override")
    }
    cards = []
    for o in PROJECT.get("overrides", []):
        target = o.get("target", {})
        cards.append({
            "id": o["id"],
            "action": o.get("action", ""),
            "origin": o.get("origin", o.get("created_by", "analyst")),
            "target": target.get("feature_name") or target.get("feature_id") or o.get("layer", ""),
            "change": (
                f"{o['change']['field']}: {o['change']['from']} → {o['change']['to']}"
                if o.get("change") else o.get("geometry_file", {}).get("path", "")
            ),
            "rationale": (o.get("rationale") or "").strip(),
            "evidence": [str(e.get("value", "")).strip() for e in o.get("evidence", [])],
            "created_at": str(o.get("created_at", "")),
            "control": controls.get(o["id"]),
        })
    return cards

# Cadastral sihtotstarve codes present in the accepted selection.
LAND_USE_LABELS = {
    "MAATULUNDUSMAA": "Agricultural / profit-yielding land",
    "TOOTMISMAA": "Production land",
    "ARIMAA": "Commercial land",
}


def _area_slider_max(final_gj: dict) -> int:
    """Upper bound for the minimum-area control, rounded to the 5,000 m2 step."""
    areas = sorted(f["properties"]["area_m2"] for f in final_gj["features"])
    if not areas:
        return 100000
    p90 = areas[min(len(areas) - 1, int(len(areas) * 0.9))]
    return max(40000, int(round(p90 / 5000) * 5000))


def render_dashboard(con: duckdb.DuckDBPyConnection, validation: dict, manifest: list[dict]) -> None:
    pr = PROJECT["project"]
    pres = PROJECT["presentation"]
    inte = PROJECT["interpretation"]

    final_gj = json.loads((DERIVED / "final-candidates.json").read_text())
    roads_gj = json.loads((DERIVED / "main_roads.json").read_text())
    catchments_gj = json.loads((DERIVED / "education_catchment_variants.json").read_text())
    pois_gj = json.loads((DERIVED / "education_pois.json").read_text())

    plan_gj = {"type": "FeatureCollection", "features": []}
    planned_road_file = OVERRIDES / "planned-road.geojson"
    if planned_road_file.exists():
        plan_gj = json.loads(planned_road_file.read_text())

    # Bounds over the accepted result, so the initial view frames the analysis.
    lngs: list[float] = []
    lats: list[float] = []

    def collect(coords):
        if isinstance(coords[0], (int, float)):
            lngs.append(coords[0])
            lats.append(coords[1])
        else:
            for sub in coords:
                collect(sub)

    for feature in final_gj["features"]:
        collect(feature["geometry"]["coordinates"])
    bounds = (
        [[min(lngs), min(lats)], [max(lngs), max(lats)]]
        if lngs else [[26.55, 58.30], [26.85, 58.45]]
    )

    land_use_present = sorted({f["properties"]["land_use"] for f in final_gj["features"]})
    latest_run = PROJECT.get("runs", {}).get("latest", {}) or {}

    # The canonical settings ARE the accepted analysis, so they are read from
    # project.yaml rather than restated here. Any departure the user makes is
    # labelled in the UI as an exploratory reconfiguration.
    filters, scenarios = _declared_controls()
    missing = {"min_area", "max_road_distance", "education_threshold", "land_use"} - set(filters)
    if missing:
        raise RuntimeError(f"presentation.controls.filters is missing {sorted(missing)}")
    canonical = {
        "minAreaM2": filters["min_area"]["canonical"],
        "maxRoadM": filters["max_road_distance"]["canonical"],
        "educationMinutes": filters["education_threshold"]["canonical"],
        "landUse": filters["land_use"]["canonical"],
    }
    for sc in scenarios.values():
        canonical[_control_key(sc["id"])] = bool(sc.get("canonical", True))

    layer_groups = []
    for group in pres["map"].get("layer_groups", []):
        binding = LAYER_BINDINGS.get(group["id"])
        if not binding:
            continue
        layer_groups.append({**group, **binding})

    view = {
        "project": {
            "id": pr["id"],
            "title": pr["title"],
            "status": pr.get("status", ""),
            "updated_at": pr.get("updated_at", ""),
            "schema": PROJECT.get("schema", "openmapstack-project/v1"),
            "analysis_crs": f"EPSG:{ANALYSIS_CRS}",
        },
        "objective": " ".join(inte["objective"].split()),
        "assumptions": [
            {"id": a["id"], "statement": a["statement"], "rationale": a.get("rationale", "")}
            for a in inte.get("assumptions", [])
        ],
        "warnings": [
            {
                "id": w["id"],
                "severity": w.get("severity", "medium"),
                "issue": w.get("issue", ""),
                "statement": w.get("statement", ""),
                "mitigation": w.get("mitigation", ""),
            }
            for w in PROJECT.get("warnings", [])
        ],
        "overrides": _override_cards(),
        "sources": _source_cards(manifest),
        "validation": {
            "status": validation["status"],
            "run_id": validation.get("run_id", ""),
            "checks": [
                {"id": c["id"], "status": c["status"], "reason": c.get("reason", "")}
                for c in validation["checks"]
            ],
        },
        "run": {
            "id": latest_run.get("id", validation.get("run_id", "")),
            "completed_at": latest_run.get("completed_at", ""),
            "inputs_hash": latest_run.get("inputs_hash", ""),
            "outputs_hash": latest_run.get("outputs_hash", ""),
        },
        "outputs": [
            {"key": key, "path": spec["path"], "format": spec["format"]}
            for key, spec in PROJECT.get("outputs", {}).items()
        ],
        "tiers": TIER_STYLE,
        "layerGroups": layer_groups,
        "landUse": [
            {"code": code, "label": LAND_USE_LABELS.get(code, code)} for code in land_use_present
        ],
        "canonical": canonical,
        "catchmentMinutes": list(filters["education_threshold"]["options"]),
        "areaBounds": {
            "min": filters["min_area"]["canonical"],
            # The 90th percentile, not the maximum: a handful of 100 ha parcels
            # would otherwise make every useful slider position indistinguishable.
            "max": _area_slider_max(final_gj),
        },
        "walkingSpeedKmh": PROJECT["processing"]["routing"]["walking_speed_kmh"],
        "bounds": bounds,
        "provenanceUI": pres.get("provenance_ui", {}),
        "interaction": pres["map"].get("interaction", {}),
        "basemap": pres["map"]["basemap"],
        "editing": pres.get("editing", {}),
    }

    def embed(payload: dict, round_coords: bool = True) -> str:
        if round_coords:
            payload = json.loads(json.dumps(payload))
            for feature in payload.get("features", []):
                _round_geometry(feature["geometry"])
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

    html = (ROOT / pres["dashboard"]["template"]).read_text(encoding="utf-8")
    for token, value in {
        "__TITLE__": pr["title"],
        "__VIEW__": embed(view, round_coords=False),
        "__CANDIDATES__": embed(final_gj),
        "__ROADS__": embed(roads_gj),
        "__PLANNED__": embed(plan_gj),
        "__CATCHMENTS__": embed(catchments_gj, round_coords=False),
        "__POIS__": embed(pois_gj),
    }.items():
        assert token in html, f"dashboard template is missing {token}"
        html = html.replace(token, value)

    out = ROOT / "dashboard.html"
    out.write_text(html)
    log.info("Rendered dashboard: %s (%0.1f KB)", out, len(html) / 1024)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Regenerate the Tartu worked example from its authoritative sources."
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "discard the cached sources in data/source/ and re-fetch every service. "
            "Use this before committing regenerated artifacts: the cache never "
            "expires on its own, and CI always starts cold."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    run_id = _run_id()
    for d in (DERIVED, VALIDATION, RUNS, SOURCE):
        d.mkdir(parents=True, exist_ok=True)

    cadastre_gpkg, roads_geojson, pois_geojson, manifest = fetch_and_manifest_sources(
        refresh=args.refresh
    )

    con = duckdb.connect(config={"threads": 1, "memory_limit": "512MB"})
    con.install_extension("spatial")
    con.load_extension("spatial")

    override_results = run_pipeline(con, cadastre_gpkg, roads_geojson, pois_geojson)
    write_qgis_project(con)
    validation = write_validation(con, run_id, override_results)
    finalize_run(validation, manifest, started_at)
    render_dashboard(con, validation, manifest)
    log.info("E2E full run complete!")


if __name__ == "__main__":
    main()
