# Reproducible project-first workflow

> **Core principle: reasoning may be exploratory; the delivered analysis must be deterministic, inspectable, and reproducible.**

For any material multi-stage GIS analysis, do not optimize for reaching the final map, dashboard, or answer quickly. A one-off polished dashboard is **not** the deliverable — a reproducible technical GIS project is. First establish a reusable project artifact, then derive the map/dashboard/report from it.

Before treating an analysis as complete, you MUST compile (or maintain) a project like `examples/tartu-development`: a canonical `project.yaml` (`openmapstack-project/v1`), `pipeline.py`, and `README.md`; pinned sources with timestamps, selections and licensing; explicit assumptions; every manual addition or correction stored as real geodata; deterministic ordered steps with explicit CRS; machine-readable validation rules plus the report from the run; and output definitions with semantic presentation intent and provenance surfaced in the rendered view. **`references/project-spec.md` is the full schema — read it before compiling a project.**

Workflow (agent may retry/experiment internally, but the accepted analysis is recompiled deterministically):

```
USER QUESTION
    ↓
interpretation / exploration        (internal, may be ad-hoc)
    ↓
COMPILE GIS PROJECT                 project.yaml + pipeline + manifest + overrides + validation
    ↓
EXECUTE PROJECT
    ↓
validated derived datasets
    ↓
selected delivery views (dashboard / desktop GIS / external presentation)
    ↓
FINAL ANALYSIS / DASHBOARD / ANSWER
```

The polished map/dashboard is a **view over the project**, not the canonical definition of the analysis.

## Select delivery before implementation

For new projects, record `delivery.schema: openmapstack-delivery/v1` and explicit
`targets`. The built-in dashboard is the default. Respect an explicit choice of
QGIS-only, Observable-only, or any supported combination; do not silently add a
dashboard or desktop companion. See `project-spec.md` section 2.0 for output
bindings, evidence, compatibility and migration. Unselected integrations have no
generation/runtime obligation. Core source, CRS, override, validation and rerun
requirements apply to every selection. Absence of `delivery` means legacy v1,
not a migrated dashboard-only project.

Hard rules for every material analysis — each is expanded in `references/project-spec.md`:

* **Real source data mandatory; never hallucinate coordinates.** Fabricating coordinates or synthesizing baseline geometry is forbidden without explicit, informed user consent. Hypothetical or planned features go in `data/overrides/` with provenance, rationale, and evidence.
* **Never mutate source data.** Immutable source + project override layer = effective input. Distinguish external facts, transformations, corrections, assumptions, and hypothetical data.
* **Overrides must be executable and verified.** Target a real source feature; for attribute changes the asserted prior value must match. Evidence must be non-placeholder. Validation reports each override `applied`, `rejected`, or `not_testable` — listing one is not applying it. Scenario features stay labeled hypothetical and visually distinct from authoritative layers.
* **Record, don't memoize on chat.** Encode every manual fix as data or pipeline logic. A fresh environment with the documented sources must reproduce the project; the transcript is not part of the dependency graph.
* **Prove semantic predicates from data.** Ownership, active status, public access, legal designation, zoning: the source must expose an authoritative field or documented mapping. Preserve unknown as unknown; never default a missing value to the desired class. Record every coded attribute the selection depends on as `selection.semantic_predicates` (`field`, `domain_value`) on the source it filters, even when the source is local and unfiltered on load; a free-text `selection.filter` does not document it.
* **Bounded APIs must prove completeness.** Record `numberMatched`/equivalent and page until returned == matched. A response filled to the request limit is incomplete until proven otherwise.
* **Validation is a pipeline stage**, not prose advice, with machine-readable results. Every declared check appears exactly once in the report; `warning`/`not_testable` propagate to run and project status; run IDs and hashes resolve to a real `runs/*.json` record.
* **Run the project CLI when available.** Use `openmapstack validate project.yaml` before delivery and `openmapstack run project.yaml` for the canonical execution path. The CLI audits the manifest, provenance, graph, artifacts, report, and run record; it does not replace domain GIS checks performed by the pipeline.
* **The manifest must resolve.** Every step input is a source key or an earlier step's output, spelled as the producer declared it; every `generated_by` names a real step (`manifest_graph_resolves`).
* **Prove a clean rerun.** When the CLI is available, finish with `openmapstack verify project.yaml --rerun`; rebuild selected deliverables from pinned inputs in an empty workspace. Missing runtime capability remains `not_testable`, and the canonical run cannot change the selected targets.
* **One canonical implementation creates every declared output.** Convenience/E2E entrypoints may wrap `pipeline.py` but must not duplicate its processing, QGIS, or report logic.
* **When QGIS is selected, build the declared layer- and style-perfect QGIS project (for example `project.qgz`)** matching the shared presentation semantics (and the web view when selected): declared layer-tree groups and categorized styles, `./path.gpkg|layername=name` datasources, and a regional tiled basemap for map-primary delivery. **Success means valid layers, not exit code 0** — pin the runtime, and when PyQGIS is available require every layer `isValid()`; otherwise record `not_testable`, never an implicit pass. Two traps make a project that passes every one of those checks still show the wrong map, so check them explicitly:
  * When PyQGIS is available, use `QgsProject.write("project.qgz")` and check its return value. Reopen the saved project from another working directory and require a valid project CRS plus valid layers. Bare `data/...` strings in hand-written XML are not QGIS's serialized `./data/...` relative paths. The canonical pipeline must rebuild the QGIS companion on every run; a one-off export is insufficient.
  * **Every layer declares a complete `<srs>`, basemaps included, and project reprojection is enabled.** A layer without one is assumed to be in the project CRS and never reprojected. An auth-id-only `<spatialrefsys>` is also broken: QGIS can still report `EPSG:3301` while treating the CRS as invalid and silently painting nothing. Emit WKT or PROJ alongside the identifiers and set `SpatialRefSys/ProjectionsEnabled` to `1`. Prefer building layers through the PyQGIS API, which serializes these fields for you; the trap is specific to hand-written `.qgs` XML.
  * **A QGIS layer tree stacks the opposite way to a web map.** `presentation.map.layers` is ordered bottom-to-top, while a layer tree paints its *first* entry on top, so write the tree in reverse manifest order with the basemap last. Copying the manifest order verbatim puts opaque analysis fills over the point layers that belong above them, and the points vanish.
* **Separate analysis semantics from rendering.** Declare semantic presentation roles; don't reinvent layout/colors/UX per run.
* **When the built-in dashboard is selected, ship a reconfigurable view, and never let it misrepresent the run.** Organise the sidebar into tabs of collapsible sections, give every layer group an on/off control, and expose the analysis parameters and scenario overrides as live controls. Each control opens at the value declared in `presentation.controls` and returning there must reproduce the published numbers; any other position labels itself exploratory and offers a reset. The browser re-applies published rules to values the pipeline measured — it never measures geometry, and a control that changes a shape switches between buffers the pipeline materialised.
* **Credit the toolkit, the author and the run.** The provenance tab of a dashboard, or the provenance section of a report, opens with a credits block in the view's language: a "made with AI using the free OpenMapStack toolkit" statement linked to https://github.com/jaakla/openmapstack-skills, the analysis date and time with its UTC offset taken from `runs.latest.completed_at`, and the author's name and email from `project.author` (email as a `mailto:` link, plus the optional `url`). Take the author from the requesting user or their version-control identity and ask when neither is known; never invent one. A canonical rerun refreshes the timestamp. See `project-spec.md` section 3, Credits header.
* **Tables are outputs too.** A non-spatial result (scenario comparison, cost table, ranking) is declared with `kind: table`, its columns and `downloads: [csv, xlsx]`; the pipeline writes every download format, and the view shows it as a scrollable table that links those files. See `project-spec.md` section 2.5, Non-spatial table outputs, and section 3, Tabular results.
* **Labels must match the operation.** A Euclidean buffer is a "2 km straight-line proxy", not a walking catchment, and column names must say so too. State the measurement basis (nearest edge vs centroid) as an assumption — it changes which features qualify.
* Cheat-sheet: `references/project-spec.md` defines the full schema; `templates/` gives ready scaffolds; `examples/tartu-development` is a worked reference project matching the acceptance scenario.

