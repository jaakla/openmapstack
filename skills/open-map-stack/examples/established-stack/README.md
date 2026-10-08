# Established-stack integrations

This small CC0 synthetic example measures three polygons (including a hole) in
EPSG:3301. Accepted threshold: 15,000 m²; two candidates, 50,000 m² total.
Exploratory threshold: 30,000 m²; one candidate, 30,000 m². It is not a real
parcel study. There are no accounts, credentials, publication or paid services.

Prepare a writable copy, choosing exactly the runtime you want:

```sh
python examples/established-stack/create.py /tmp/spatial-demo --tool observable
# Choices: dashboard (default), dlt, dbt, dagster, observable, qgis, composed.
# Add --qgis to also generate a native desktop project.
```

Install OpenMapStack into your interpreter, then install only selected tools:

| Selection | Requirements in prepared copy | Additional runtime |
| --- | --- | --- |
| dashboard | requirements-base.txt | DuckDB Spatial extension |
| dlt | requirements-dlt.txt | No dbt, Dagster, Node or QGIS |
| dbt | requirements-dbt.txt | No dlt, Dagster, Node or QGIS |
| dagster | requirements-dagster.txt | No dlt, dbt, Node or QGIS |
| observable | requirements-base.txt | Node 20+ and npm; tested Node 24.19.0 |
| qgis | requirements-base.txt | System PyQGIS; tested QGIS 3.40.15 |
| composed | requirements-composed.txt | Node/npm; QGIS only with --qgis |

Python 3.12 is the supported example runtime. The files pin direct Python
versions, and the Observable npm lock pins its dependency tree. The run receipt
records actual Python tool versions. These are executable example pins, not
mandatory global versions for agent projects. Network access is needed for
package installation, initial DuckDB extension download and Framework build
resources; offline operation requires a prepared package/extension/module cache.

```sh
python -m pip install -r /tmp/spatial-demo/requirements-base.txt # or selected file
python /tmp/spatial-demo/pipeline.py
openmapstack validate /tmp/spatial-demo/project.yaml
openmapstack verify /tmp/spatial-demo/project.yaml --rerun
```

The canonical entrypoint is always `pipeline.py`. It verifies the frozen source
before any tool, executes the selected owners, runs the net-area oracle, exports
analytical artifacts, builds selected views, and writes input-bound receipts and
run/report inventories. A clean rerun discards operational state and rebuilds
from source pins and declared local definitions. It does not require the original
conversation. Missing selected artifacts fail; unavailable browser/PyQGIS checks
are `not_testable`, never evidence that those runtimes succeeded.

| Concern | Authoritative owner | Inputs → outputs/evidence |
| --- | --- | --- |
| Source pin, suitability, CRS, assumptions, acceptance | project.yaml + OpenMapStack | frozen CSV → integrity/validation records |
| Optional extraction/loading + cursor | ingest.py via dlt | CSV → raw.parcels + dlt schema/state/load metadata |
| Spatial SQL and model references/tests | dbt/models + dbt/tests | raw.parcels → measured/candidates/scenarios |
| Operational asset dependencies/retry/failure | orchestration.py via Dagster | load → models → validated outputs → delivery |
| Interactive web build | observable/ via Framework | validated exported data → views/observable/ bundle |
| Optional desktop generation | openmapstack.integrations.qgis | declared candidates → portable project.qgz |
| Simple built-in report | delivery.py | validated rows → dashboard.html |

The dbt and composed selections execute real `dbt build`. The other focused
examples use a deliberately bounded Jinja renderer of the **same** SQL model/test
files. Model references determine its execution order; spatial SQL is not copied
into Python/Dagster/renderers. It is not an alternative production dbt scheduler.
The manifest execution summary is derived at preparation, then hashed against
its owner definitions. Editing definitions requires deliberate preparation of a
new contract; reruns never silently refresh bindings.

Dagster executes the existing stage functions with asset/run metadata, a load
retry and an explicit asset check after independently validated exports. Any load,
model/test or export failure stops downstream delivery. Inspect
`work/dagster-run.json` and `work/dbt-console.txt` for operational failures. These
logs are operational lineage; source provenance and immutable input hashes remain
in project/run records. `delivery/tool-run.json` captures successful tool evidence.

## dlt incremental state versus a frozen snapshot

`data/source/parcels.csv` is the committed analytical snapshot. Its pin never
points at dlt's database or cursor. The canonical dlt run replays it with fresh
state. To exercise a persistent operational cursor separately:

```sh
python /tmp/spatial-demo/ingest.py /tmp/spatial-demo/data/source/parcels.csv /tmp/upstream.duckdb /tmp/upstream-state
python /tmp/spatial-demo/ingest.py /tmp/spatial-demo/data/source/parcels.csv /tmp/upstream.duckdb /tmp/upstream-state
```

The second load skips previously seen timestamps. Change a writable upstream
copy with a later `updated_at` to ingest a new revision. Freeze the resulting
selected rows into a new immutable source snapshot, record retrieval/schema and
hash metadata, and prepare a new analytical contract; do not mutate the existing
pinned file. This fixture uses file extraction; it does not claim an implemented
remote API connector.

## Observable Framework

This integrates the open-source **Framework static build**, not hosted Observable
notebooks or a publication API. The canonical run invokes locked `npm ci` and
`observable build` only when selected. It stages validated data in a disposable
application directory, then retains the complete HTML/module/data bundle in
`views/observable`. Serve it over HTTP (module apps do not run from file://):

```sh
python -m http.server 8080 --bind 127.0.0.1 --directory /tmp/spatial-demo/views/observable
```

Open http://127.0.0.1:8080. Choose the exploratory threshold; the visible status
and table change to one feature/30,000 m², explicitly marking the accepted run as
unchanged. Reset restores canonical two/50,000 m². The page exposes provider,
license, assumptions and warnings plus the accepted CSV download. Metrics are
precomputed by the spatial engine; the view does not recompute geometry. Bundle
hashes detect stale/missing resources beyond index.html. Local static exports
are reproducible; hosted notebooks/publication require separate export/retrieval
evidence under the delivery contract and are not implemented here.

## Optional QGIS

Set `OMS_QGIS_PYTHON` to an interpreter with PyQGIS **and OpenMapStack installed**
when your analysis interpreter lacks PyQGIS. Generation runs offscreen, saves
relative OGR paths, uses the declared CRS/layer groups/semantic role and embeds
provenance, then reloads the native project. Reusable static/runtime checks remain
in `openmapstack verify`. The richer Tartu combined example remains supported.
This example does not implement editable override round-trip (#11).
The reusable native example adapter is bounded to simple vector report layers;
it rejects map-primary/basemap delivery and richer style declarations rather
than silently omitting them. Use a project-specific adapter for those requirements.

## Limits and future integrations

This bounded fixture does not implement partitions, production backfills,
incremental dbt models, full GIS styling or a general dashboard renderer (#6).
Core provenance, assumptions, CRS, overrides and validation remain mandatory.
MotherDuck and BigQuery already support read-only discovery/snapshots; reuse those
connectors. Remote compute, warehouse orchestration, Snowflake and Esri delivery
are future integrations: declare one owner, immutable snapshot/version retention,
credential **references** (never values), selected artifacts and honest checks.
No enterprise integration or hosted provisioning is claimed by this example.

## Maintainer evidence

Core fixture tests require no optional tool accounts. To repeat actual runtime QA
from a checkout with the selected requirements already installed:

```sh
OMS_STACK_TOOLS=dashboard,dlt,dbt,dagster,observable,qgis,composed \
  python -m unittest tests.test_established_stack_tools -v
```

Set only the choices you installed. Add Playwright/Chromium for Observable checks,
and `OMS_QGIS_PYTHON` for native QGIS if needed. This opt-in test executes each
selected tool and clean rerun, checks equal analytical results, exercises
incremental ingestion and failed spatial/Dagster paths, and checks actual browser
interactions/native layer rendering. Ordinary unit/fixture runs do not install
or execute every optional tool. No live LLM trial is implied by these checks.
