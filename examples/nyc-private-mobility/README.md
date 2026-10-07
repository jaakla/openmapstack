# Northstar Mobility — NYC Operations Hub Analysis (tenant alpha)

Private-database worked example for
[openmapstack-skills#43](https://github.com/jaakla/openmapstack-skills/issues/43):
identify promising NYC areas for a new operations/charging hub for tenant
`alpha` using reproducible H3 aggregation and a separately testable live-backend security model.

The analysis uses **pinned reader captures from three provisioned services**. The business rows are synthetic demo data. A separate public NYC TLC capture supplies historical taxi pickup coordinates used to place some synthetic fleet vehicles; it is not vehicle tracking. The dashboard does not query any service at page load.

| Backend | Reader source | Contribution |
|---|---|---|
| Neon PostGIS | `ops.taxi_zones`, `ops.hubs`, `ops.fleet_positions`, `ops.customer_accounts` | H3 study cells, hubs, fleet and accounts |
| BigQuery | `northstar_analytics.trip_events`, `northstar_analytics.taxi_zones` | Trip pickup points and legacy market-reference polygons |
| MotherDuck | `market.analysis_zone_scores`, `market.analysis_pois` | Source-zone scores and POI points |
| NYC TLC Open Data | 2016 yellow-taxi trip pickups | Historical coordinates reused for synthetic fleet points |

`data/source/live-20260928/` contains the original reader captures. The active fleet pin and complete capture manifest are in `data/source/live-20260928-tlc-v1/`; `data/source/tlc-2016-pickups.json` pins 281 source coordinates across 68 H3 cells with the API response hash and selection rule. The reader-visible PostGIS fleet increased from 389 to 670 observations. Its latest located points now appear as a dashboard layer. The PostGIS H3 polygons are the analysis grid. BigQuery pickup points and MotherDuck POIs are spatially assigned to those cells. The BigQuery `taxi_zones` polygons only map MotherDuck source-zone scores to H3 cell centres; they are **not** a second H3 grid. Of 2,917 reader-visible BigQuery trip pickups, 2,090 lie inside the 83-cell study grid. The eligibility floor remains 30 trips, 1,500 metres from a hub and no more than six latest synthetic vehicles; the expanded fleet leaves six eligible candidates.

## Rebuild from the pinned captures

```bash
pip install -e . -r examples/nyc-private-mobility/requirements-fixture.txt
python examples/nyc-private-mobility/pipeline.py
openmapstack validate examples/nyc-private-mobility/project.yaml --preflight
```

This rebuild needs no warehouse credentials. Its inputs are the captured Parquet files and declared implementation dependencies. The H3 cells have land-based centres within a fixed study area covering parts of Manhattan, Brooklyn and Queens; coastal hexagons can cross water. `h3_cell` is the spatial identity, while the PostGIS integer `zone_id` and `taxi_zone_id` are compatibility aliases, **not TLC zone IDs**. The original seed catalogue and NYC DCP land-mask provenance remain under `setup/h3/` for provisioning; the dashboard reads the PostGIS capture.

The [2016 TLC trip dataset](https://data.cityofnewyork.us/Transportation/2016-Yellow-Taxi-Trip-Data/uacg-pexx) contains pickup coordinates, not fleet GPS or vehicle identities. `capture_tlc_pickups.py` selects at most five points per study cell from a bounded 10,000-row API response. `provision.py augment-fleet` idempotently places synthetic tenant-alpha vehicle IDs at those points with generated statuses and 2026 timestamps. A normal `provision.py postgis` rebuild applies the same pinned enrichment after its base seed. The sample is historical and not representative of present fleet availability.

For a fresh capture, configure the restricted reader identities described below and run `openmapstack source snapshot` with the matching file in `queries/`, a new versioned `data/source/` destination and `--approve --write-manifest`. Update the active pins, capture records and source manifest together, then rerun the pipeline. Do not overwrite an accepted capture.

## Explore the dashboard

Open `dashboard.html` directly, or serve this directory with `python -m http.server`.
It follows the Tartu example's map-with-sidebar layout:

- **Analysis:** ranked candidates, search, borough and eligibility filters, and a minimum-score slider.
- **Map:** zone, hub and synthetic fleet point layers, each with collapsed source lineage; fixed score bands, eligibility rules and scoring weights.
- **Provenance:** snapshot dates and hashes, fleet observation times, recorded checks and assumptions.
- Select a grid cell or list entry for the same zone details, eligibility failures and weighted score
  contributions. Existing hub markers show name and capacity. Reset and fit controls restore orientation.
- Export the visible zones as CSV or copy a URL containing the filters and selection. Filtering never
  recalculates the accepted scores. A minimum score above zero excludes unscored zones.

The synthetic-data warning is always visible. These are real H3 cells, **not NYC taxi-zone boundaries**,
and the scores are not real-world siting recommendations. Fleet presence means the **latest observation
of each distinct vehicle**, including all statuses—not the number of position records or live availability.

Results and the latest synthetic vehicle points are embedded in the page; customer records are not. The map library and street
basemap require internet, but list filtering, zone details and CSV export work without them. With the
map library available, zones, fleet points and hubs also render when basemap tiles are unavailable. On mobile the map
appears first, followed by the tabbed analysis and selected-zone details.

The street map uses the manifest's goplex.ee Protomaps global fallback and displays
its provider credit. An explicit user or appropriate local/regional web basemap
takes precedence; change `presentation.map.basemap` to select one. The QGIS
companion retains its OpenStreetMap XYZ background.

`dashboard-template.html` is the editable presentation source; `pipeline.py` embeds only approved derived
properties and snapshot metadata. Rerun the pipeline after editing either file. The template is included
in the run's input inventory and clean-rerun dependencies.

## Optional live-backend architecture

```
live PostGIS    (Neon neondb.ops)      reader: oms_alpha_reader   RLS + column grants
live BigQuery   (northstar_analytics)  reader: service account    row access policies
live MotherDuck (northstar_market)     connector: read-only attach; token may be writable
      ↓ openmapstack source snapshot --approve   (read-only, dry-run first)
data/source/<capture-version>/*.parquet  (separately pinned live captures)
      ↓ pipeline.py                             never touches a live warehouse
data/derived/* + dashboard.html + validation/ + runs/
```

- Live warehouses are used for read-only discovery and explicitly approved
  snapshotting only. The accepted analysis runs from pinned local snapshots
  (`pin.class: local_snapshot`), so `openmapstack verify` works with **all
  warehouse credentials unset**.
- Provisioning identity (admin) is separate from the analysis identity where the service permits it. This project references the PostGIS and BigQuery restricted readers and the MotherDuck token
  (`env:OMS_DEMO_POSTGIS_DSN`, `env:GOOGLE_APPLICATION_CREDENTIALS`,
  `env:MOTHERDUCK_TOKEN`).
- Backend-enforced security is the fixture, not decoration: row-level
  security and row access policies, column grants, and relations the reader
  cannot reach at all (`hr.driver_private`, `driver_costs`).

## Layout

| Path | Purpose |
|---|---|
| `project.yaml` | canonical manifest; scoring model and eligibility live here |
| `pipeline.py` | single canonical analysis from pinned snapshots (also `run_e2e.py`) |
| `provision.py` | backend setup and pinned fleet augmentation: `postgis` / `augment-fleet` / `bigquery` / `motherduck` / `all` / `verify` / `destroy` |
| `capture_tlc_pickups.py` / `data/source/tlc-2016-pickups.json` | bounded public pickup-coordinate capture and immutable seed provenance |
| `rebuild_fixture.py` / `requirements-fixture.txt` | optional deterministic seed catalogue builder and pinned development dependencies |
| `setup/h3/` / `setup/*/zones.sql` | land mask and provenance, shared cell catalogue and generated warehouse geometry seeds |
| `fixture.yaml` | the access matrix: what each backend enforces, and what is only a convention |
| `setup/postgis/{schema,seed,security}.sql` | fixture structure, deterministic seed (20260917), RLS/column-security boundary |
| `setup/bigquery/{schema,seed,security}.sql` | two datasets, row access policies, and the never-granted restricted dataset |
| `setup/bigquery/column-security.sql` | optional policy-tag step, applied only with `--policy-tag` |
| `setup/motherduck/{schema,seed,security}.sql` | private market overlay and its analysis views (plain DuckDB SQL, so tests execute it) |
| `queries/*.sql` | the exact approved-snapshot queries, one per source, run through the restricted readers |
| `project.qgs` / `project.qgz` | QGIS project over the ranked candidates and all scored zones, written by QGIS itself where PyQGIS exists |
| `data/source/live-20260928-tlc-v1/` | active fleet capture, source manifest and connector records; other pins remain in the original capture directory |
| `data/derived/` | zone metrics, ranked hub candidates and fleet point layer |
| `validation/`, `runs/` | validation report and run records |
| `dashboard-template.html` / `dashboard.html` | editable map dashboard template / generated view over project artifacts |

## Provision and capture from live backends

```bash
pip install "openmapstack[geo,postgis,bigquery,motherduck]"

# 1. Provision the fixtures (admin identities, one-time). `all` provisions
#    every backend whose credentials are set and reports the rest as skipped.
export OMS_DEMO_POSTGIS_ADMIN_DSN=...            # admin only, never in project.yaml
export OMS_DEMO_POSTGIS_READER_PASSWORD=...
export OMS_DEMO_BIGQUERY_PROJECT=...
export OMS_DEMO_BIGQUERY_ADMIN_CREDENTIALS=...   # admin key file
export OMS_DEMO_BIGQUERY_READER_PRINCIPAL=serviceAccount:oms-alpha-reader@PROJECT.iam.gserviceaccount.com
export OMS_DEMO_MOTHERDUCK_ADMIN_TOKEN=...
python provision.py all

# 2. Check the boundary through the restricted readers, not the admins.
export OMS_DEMO_POSTGIS_DSN=...                  # restricted reader DSN
export GOOGLE_APPLICATION_CREDENTIALS=...        # restricted reader key file
export MOTHERDUCK_TOKEN=...                      # connector uses a read-only attachment
python provision.py verify

```

To recapture, use the connection references configured in `project.yaml`, run each query in `queries/` through the read-only connector into a new versioned directory, and record the returned pins and query/schema hashes. The current run already uses live captures from all three backends; the service data itself remains seeded and synthetic. `provision.py verify` is the separate backend access check.

The current MotherDuck token can write outside the connector; the connector's read-only attachment refused writes. BigQuery column policy tags are also not configured. `provision.py verify` reports both as `NOT CONFIGURED`; the pipeline's passing source checks are not substitutes for those identity-level controls.

`provision.py destroy` removes every backend it has admin credentials for.

## Optional live security model

These restrictions apply to the provisioned backends. The pinned captures reflect their reader-visible results, while the pipeline checks only the captured rows and columns.
`fixture.yaml` is the full access matrix, including which restrictions the
backend enforces and which are only conventions. In summary:

### PostGIS (backend-enforced)

- `oms_alpha_reader` sees only `tenant_id = 'alpha'` rows on every
  tenant-bearing table (row-level security policies in `security.sql`).
- `contact_name`, `contact_email`, `contract_value` on
  `ops.customer_accounts` are withheld by column grants: `SELECT *` and
  protected-column queries fail at the backend, so they can never reach a
  snapshot.
- `hr.driver_private` has no grants and no schema usage: entirely
  inaccessible.

### BigQuery (backend-enforced, with one optional part)

- Row access policies confine the reader to tenant `alpha` on `trip_events`,
  `zone_daily_demand` and `vehicle_daily_metrics`. `taxi_zones` is
  public-origin reference geometry: no tenant column, no policy, fully
  readable — the same split the PostGIS fixture uses.
- `northstar_analytics_restricted.driver_costs` is granted to nobody.
- Column-level security on `internal_cost` and `rider_reference` needs a Data
  Catalog policy tag. Without `--policy-tag` it is **not applied**, and
  `provision.py verify` prints `NOT CONFIGURED` rather than a pass.
- Every query is dry-run before execution and refused above
  `--max-scan-bytes`; the executed job also carries `maximum_bytes_billed`.
  On a table with a row access policy BigQuery returns *no* byte estimate, so
  the pre-execution check cannot run there and the plan says so
  (`scan_estimated: false`) rather than implying a cheap query. `taxi_zones`
  carries no policy and does report an estimate.
- `Table.num_rows` ignores row access policies, so discovery reports it as an
  estimate and never as the reader's visible row count.

### MotherDuck (token-scoped)

- Two independent read-only layers, and only one is guaranteed:
  - the connector attaches the database `READ_ONLY`, which DuckDB enforces —
    verified against live MotherDuck, where an `INSERT` through that session
    is refused outright;
  - a dedicated **read-scoped token** bounds what the credential can do
    anywhere else. Read-scoped tokens need a higher MotherDuck plan tier, and
    without one `provision.py verify` reports the identity layer as
    `NOT CONFIGURED` — not as a pass.
- The admin token is never the analysis token.
- `market.analyst_annotations` informs the scores and is never republished.
  That is a convention this analysis honours, not an enforced grant, and
  `fixture.yaml` records it as such.
- A MotherDuck session needs network access, so file confinement via
  `enable_external_access` is not available here.

### Everywhere

- No credential appears in `project.yaml`, snapshots, logs, errors, or
  committed artifacts.

## Scope notes

- The scoring weights are 45/30/15/10 over demand, coverage gap, market and
  saturation. Every input the model reads is stored beside the score, so
  `final_score` is recomputable from `data/derived/zone-metrics.parquet`
  alone — and a test does exactly that, refusing any disagreement.
- Zone geometries are genuine H3 resolution-8 cells (A5). Switching to TLC zones would require
  regenerating spatial assignments and aggregates; it is not a geometry-only replacement.
- `openmapstack verify` reports **WARNING**, not PASSED, wherever PyQGIS is
  absent: shipping `project.qgz` activates four checks that need QGIS, and
  they report `not_testable`. Nothing fails; the checks simply cannot run,
  and saying so is the point. With PyQGIS installed all four run and pass.
- **The QGIS project has two writers.** Where PyQGIS is importable the file is
  written by QGIS itself, so its format and its version attribute are the real
  thing. Where it is not, a deterministic builder produces an equivalent
  project and the pipeline still runs anywhere DuckDB does; that builder
  declares its own version rather than impersonating a QGIS release. A test
  asserts the two agree on layers, CRSs, datasources and renderers.
  The project XML identifies the writer used for each regeneration.
- QGIS does not write reproducibly: it stamps the save time, mints UUIDs for
  layers, symbols and the annotation layer, colours default symbols at random,
  and orders attributes by hash. `pipeline.py` pins what it can and normalises
  the rest, so reruns are byte-identical **within one writer and one QGIS
  version**. Across versions the version attribute differs by design.
- `openmapstack verify --rerun` rebuilds the project in a clean workspace
  and reproduces every output with no failures; the rerun carries
  `README.md` as documentation.
