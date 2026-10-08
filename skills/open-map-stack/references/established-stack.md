# Established-stack integration boundary

OpenMapStack owns geospatial methodology, source suitability, immutable pins,
licenses, assumptions, CRS/units, executable overrides and analytical verification.
It is agent guidance and a project contract, not an ingestion service, scheduler,
warehouse or BI platform. Preserve the user's chosen stack. Put tool behavior on
an explicit integration surface; do not make every project install every tool.

## Who should use which combination

| Situation | Useful choice and tradeoff |
| --- | --- |
| Analyst needs a bounded, inspectable spatial study with source discovery, CRS decisions and a handoff | Agent + OpenMapStack can produce a local reproducible project; the analyst still reviews source meaning and assumptions. A conversation is not an operational scheduler. |
| Team already maintains ingestion jobs/cursors | Keep dlt owning extraction/loading/state. OpenMapStack adds a frozen analytical snapshot and GIS/provenance checks; it should not build a competing loader. |
| Recurring jobs, asset dependencies, retries, operators and operational failures | Keep Dagster (or the team's scheduler) owning operational execution. Added setup is justified by recurring team operations; a one-off local analysis need not run a scheduler. |
| Existing SQL model graph/tests or a warehouse team | Keep dbt owning SQL models, references and tests. GIS methodology still needs explicit CRS, area/boundary semantics and source suitability. Small direct SQL examples do not replace production dbt. |
| Editable native cartography or desktop GIS handoff | Select QGIS. Native dependencies add installation cost; portable datasources, styles and runtime checks remain required when selected. |
| Interactive narrative/report in a maintained JavaScript project | Select Observable Framework. Retain a complete local static build, source/lock/configuration and provenance; hosted notebooks have a separate export contract. |
| Simple local analytical handoff | Default built-in dashboard, plus provenance/run/validation artifacts. External delivery-only choices remain valid when explicitly selected. |

These choices compose. There is no claim that an agent terminal outperforms a
scheduler/GUI/BI stack, or that instructions replace a production SLA. The skill
is useful around those tools when a task requires defensible spatial meaning and
an inspectable rerun. Use the team's established tools for operational concerns.

## One authoritative owner

| Concern | Owner | Boundary |
| --- | --- | --- |
| Source decision, pins, CRS, method, assumptions and verification | OpenMapStack project | Declared immutable snapshots and executable methods; licenses and limitations stay visible. |
| Extraction/loading and incremental cursor/schema | dlt or existing loader | Operational cursor/database is not a source pin. Freeze selected rows and retrieval/schema evidence independently before analysis. |
| SQL transformations, model dependencies and SQL tests | dbt/DuckDB Spatial or existing SQL definition files | Entrypoint/asset invokes those files, never copies spatial formulas or separately maintains the same SQL DAG. |
| Operational scheduling, retries, runs and asset dependencies | Dagster or existing orchestrator | Operational lineage is useful evidence, but does not replace source provenance or immutable run inventories. |
| Desktop generation/styling/runtime | Optional QGIS integration | Consume declared outputs; do not silently apply a different analytical selection. |
| Interactive presentation/build | Observable integration | Consume validated outputs; label scenarios exploratory, provide canonical reset and retain known limitations. |

When the manifest needs an execution summary, derive it from the authoritative
model/configuration, bind both its contents and definition identity, and detect
drift. A manifest hash is consistency evidence, not proof arbitrary code ran.
Keep actual tool results (model/test status, asset/run/check events, build identity)
as separate operational evidence. Do not certify unavailable tools as passed.

## Versioned optional declaration

`integrations.schema: openmapstack-integrations/v1` is an additive extension to
`openmapstack-project/v1`, independent of delivery selection. Its owning JSON
schema is `openmapstack/schemas/project-v1.schema.json`; checks are
`integration.bindings_valid` and `integration.evidence_matches`. Negotiate via
`openmapstack api-info --json` / `openmapstack checks` before relying on it.
Older consumers that ignore integrations cannot verify this extension.

Each binding names a unique ID, role/owner, project-local definition file or
directory, canonical file-set SHA-256, owned step IDs and a SHA-256 of those step
summaries. No step may have competing owners. Allowed initial surfaces are
file/dlt ingestion, sql/dbt transformations, python/Dagster orchestration, and
built-in/Observable/QGIS presentation. These names identify tool surfaces, not
model/agent providers. Updating the supported owner vocabulary requires a
reviewed contract/check change; enterprise integration is not implied.

The declaration binds a document output for evidence, optionally a tool-run
output, and selected delivery bundle directories. The receipt binds current
immutable inputs, owner definitions/summaries, analytical output bytes, tool-run
bytes and every local bundle resource. With this extension declared, every
selected Observable target must have exactly one bundle entry containing its
view and complete local assets. Other target kinds may declare bundles as needed.
Keep integration, delivery and tool-run receipts outside all bundle directories;
the validator rejects paths that would include evidence in a bundle, even before
the receipt is written. This avoids circular hashing and order-dependent evidence.
Changed/missing scripts or data beyond
index.html therefore invalidate the build. The canonical rerun must preserve
integration ownership and delivery selection, rebuild selected artifacts from
pins, and execute normal validation. It must not silently refresh a stale binding.

Document inputs/outputs, selected dependencies and versions, configuration,
credential references, capability gaps, limits and one canonical entrypoint.
Install only selected optional packages. Never provision or publish services just
to produce a local deliverable. No credential values belong in a manifest/log.

## Runnable focused and composed examples

Project-capable skills ship the [established-stack example](https://github.com/jaakla/openmapstack-skills/tree/main/examples/established-stack). Its committed
synthetic CSV supports independent dashboard, dlt, dbt, Dagster, Observable and
QGIS projects plus Dagster → dlt → dbt/DuckDB Spatial → Observable with optional
QGIS. Each focused selection needs only its stated requirements. The same SQL
files and hand-derived polygon-hole oracle produce every view. Pins and setup
versions live in the executable example, not this general routing guidance.

Observable uses the real **Framework static-build surface**. Keep the whole
bundle and dependency lock; serve over local HTTP for browser module execution.
Scenario values are precomputed by the spatial engine, and the accepted CSV is
byte-identical across views. Hosted Observable notebook publication is not
implemented. Consult [Framework configuration](https://observablehq.github.io/framework/config)
and [data loaders](https://observablehq.github.io/framework/data-loaders) for other
surfaces and reproducibility decisions.

QGIS generation is external optional support at
`python -m openmapstack.integrations.qgis project.yaml --target qgis`. Use a
PyQGIS interpreter with OpenMapStack installed; normal core imports need no
PyQGIS. The Tartu combined example keeps its richer desktop adapter in
`qgis_delivery.py`. Static and native checks remain applicable when selected.
The native example adapter supports simple vector reports; map-primary basemaps
and richer styles require a project adapter and are rejected by this helper.
Unavailable native checks are `not_testable`. Editable override round-trip (#11)
and general dashboard rendering (#6) remain separate work.

Product authorities: [dlt pipeline/state](https://dlthub.com/docs/general-usage/pipeline),
[dlt incremental cursors](https://dlthub.com/docs/general-usage/incremental/cursor),
[dbt-duckdb configuration](https://github.com/duckdb/dbt-duckdb),
[Dagster assets](https://docs.dagster.io/api/dagster/assets).
Verify current APIs when adapting beyond the pinned fixture.

## Future enterprise surfaces

MotherDuck and BigQuery already have read-only discovery/snapshot connectors;
reuse `user-data-sources.md` and the CLI source commands. Those capabilities do
not imply integrated remote compute, scheduling or BI delivery. MotherDuck,
BigQuery/GCP, Snowflake and Esri extensions should each declare one owner,
credential references, snapshot/version retention, compute/format/CRS semantics,
selected outputs, reproducible export/build evidence and runtime applicability.
Document local deterministic controls independently from credentials/runtime
checks. They are extension points, not implemented integrations in this example.
