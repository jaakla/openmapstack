# Tartu Development Access & Education — worked example

A reproducible `openmapstack-project/v1` project that screens land near main
roads against **25-minute pedestrian-network catchments** of municipal schools
and kindergartens. `project.yaml` defines the analysis; `pipeline.py` produces
the datasets, QGIS project, dashboard, validation report and run evidence.

## Run

Python **3.12+** is required for the pinned Valhalla binary wheel. From this directory:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python pipeline.py
```

Or with uv:

```bash
uv run --python 3.12 --with-requirements requirements.txt python pipeline.py
```

`run_e2e.py` calls the same implementation. No Docker daemon, external routing
API, API key or long-running service is required. The pinned `pyvalhalla`
package contains the native graph builder and the in-process `Actor` API.
Platforms without a compatible wheel need Valhalla's native build prerequisites;
see the [upstream Python instructions](https://github.com/valhalla/valhalla/blob/3.9.0/src/bindings/python/README.md).

The first run downloads the official cadastral/road/education data and a
checksum-pinned Geofabrik Estonia OSM snapshot (about 117 MiB). It extracts a
buffered Tartu network, builds routing tiles with one worker and calculates
catchments. Subsequent runs reuse the verified graph in `data/cache/routing/`.
That cache is disposable: removing it forces a rebuild, not a methodological
fallback. No graph or routing failure is replaced with a circular buffer.

Use `pipeline.py --refresh` before committing regenerated artifacts. It refreshes
the official sources and downloads the declared OSM snapshot again, verifying
the same SHA-256. Change that pin explicitly when updating OSM. Rerun after
changing the implementation, dependencies or manifest so run hashes match.

## How the network calculation works

1. `routing.fetch_network` downloads and verifies the declared OSM snapshot.
2. `routing.prepare_graph` extracts complete ways touching the buffered study
   bounds, their nodes and applicable restriction relations. Node tags, including
   barriers, survive extraction. It builds Valhalla tiles and records their hashes.
3. `routing.facility_isochrones` calls `Actor.isochrone` once per active baseline
   facility, with `costing: pedestrian`, `reverse: true`, `polygons: true`, and
   contours at **15, 20, 25, 30 and 40 minutes**, using **4.8 km/h** walking speed.
   Reverse expansion models reaching the facility, rather than walking from it.
4. `pipeline.calculate_network_access` transforms the polygons to EPSG:3301,
   unions them by facility type/time/scenario, and calculates **positive-area
   parcel intersections**. The accepted threshold is 25 minutes.
5. The dashboard reads computed memberships and polygons. Changing its minute
   control swaps the corresponding catchment; it performs no browser-side routing.

Versions, bounds, speed, snap limit and thresholds are in `processing.routing`.
Raw requests/responses and snap distances are retained in
`validation/routing-evidence.json`, alongside graph identity and tile hashes.
The run record hashes that evidence and both implementation files.

The `isochrone_school_effective_min` / `isochrone_kg_effective_min` columns contain
the **first sampled contour that intersects the parcel**, not an exact route
duration. Their `_baseline_min` counterparts omit the facility-outage override.
Null means no intersection at any sampled threshold; it never means zero
minutes. Straight-line `dist_school_m` / `dist_kg_m` remain diagnostic columns and
do not determine tiers.

## What qualifies

- At least 20,000 m²; the declared agricultural, production or commercial land
  uses; municipality `Tartu linn`.
- Within 2,000 m of a qualifying official main road or the hypothetical connector,
  measured from the nearest parcel edge in EPSG:3301.
- **Tier 1:** overlaps both school and kindergarten 25-minute network catchments.
- **Tier 2:** overlaps either catchment.
- **Tier 3:** road proximity qualifies, but neither education catchment overlaps.

The road-proximity criterion remains planar. `OVERRIDE-002` affects that criterion
only: the hypothetical road is **not inserted into the pedestrian graph**.
`OVERRIDE-001` switches the Ilmatsalu kindergarten off; its isochrone remains in
the baseline comparison but is excluded from the accepted scenario's union.
Neither scenario modifies the immutable sources.

## Limits and validation

These are network-derived isochrones, but still a **land-screening model**.
Contours approximate network reachability; OSM may omit paths, gates, crossings
or access restrictions. A parcel can overlap a contour without a usable entrance
there. Facility points snap to nearby edges, with a checked 200 m maximum, rather
than surveyed entrances. No elevation or time-specific opening-hours dataset is
supplied. The pinned pedestrian costing defaults apply in addition to recorded
options; ferry use is discouraged by that profile, not categorically prohibited.

Contour topology repairs are recorded beside the untouched engine response and
accepted only when they change area by at most 1 m²; larger repairs are rejected.
Missing contours, engine warnings, remaining invalid geometries, excessive snapping,
insufficient study-boundary margin or nonmonotonic parcel membership fail the run.
Ownership/activity, pagination, overrides, manifest graph, QGIS structure,
control defaults and report parity are also checked.

The project remains `warning`: education-source reuse terms are unstated,
hypothetical scenarios are active, and network/entrance limitations remain
visible. OSM attribution and ODbL licensing appear in the manifest and dashboard.
PyQGIS runtime checks report `not_testable` when PyQGIS is unavailable.

After installing the repository CLI, from the repository root:

```bash
openmapstack validate examples/tartu-development/project.yaml --preflight
openmapstack verify examples/tartu-development
```

These checks complement rendered browser and native QGIS checks.

The cadastral GeoPackage is read through SQLite and its standard geometry header
is validated before passing WKB to DuckDB. This avoids an intermittent native
`ST_Read` failure observed with this snapshot and the pinned runtime; repeated
reads must retain the same rows and coordinates.

## Artifacts

- `data/source/`: official snapshots and pinned OSM PBF.
- `data/overrides/`: explicit hypothetical connector geometry.
- `data/cache/routing/`: disposable extract, graph, config and build log.
- `data/derived/final-candidates.{gpkg,parquet,json}`: parcels and accessibility.
- `data/derived/facility_isochrones.json`: per-facility network contours.
- `data/derived/education_catchments.json`: accepted 25-minute scenario unions.
- `data/derived/education_catchment_variants.json`: every threshold and scenario.
- `data/derived/education_pois.json`, `main_roads.json`: effective facilities and official roads.
- `dashboard.html`, `project.qgz`: generated web and desktop views. The web map uses the manifest's goplex.ee Protomaps global fallback with automatic/manual light/dark switching and visible provider credit. The QGIS companion retains its regional Maa- ja Ruumiamet background. Explicit user and appropriate local/regional web basemaps take precedence over the global fallback; see `references/web-delivery.md` in the skill.
- `validation/`, `runs/`: checks, routing evidence and hashes; current counts live here.

The dashboard retains layer switches, scenario comparisons, provenance and local
draft editing. Noncanonical settings are labelled exploratory; facility edits and
drawn geometry require a pipeline rerun to change network measurements.
`pipeline.py` rewrites manifest run metadata using YAML serialization, so persistent
explanations belong in data fields, not comments.
