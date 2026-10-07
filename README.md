# openmapstack

**AI agent skills for geospatial decisions, data discovery, spatial SQL and reproducible analysis projects.**

Explore the [project homepage and two interactive example dashboards](https://jaakla.github.io/openmapstack-skills/). The static Pages site is assembled from the generated NYC and Tartu dashboards with `python3 scripts/build_project_pages.py`; no warehouse credentials are needed to serve it.

Install (assuming you have git and nodejs with npx already installed):
```bash
npx skills@1.5.26 add jaakla/openmapstack-skills --skill open-map-stack -g
```

OpenMapStack gives your favorite AI agent: Claude Code, Codex, Cursor, OpenCode, PI and 50+ other agents not just a one-off analysis, but a well-defined **workflow project** (`project.yaml`) defining from data discovery through reusable analysis (`pipeline.py`) to interactive web (`dashboard.html`) and GIS deliverables (QGIS `project.qgz`). The workflow becomes **inspectable** and **repeatable** as a clearly specified project with pinned sources, explicit assumptions and CRS choices and deterministic processing in a python script. It allows also isolated overrides, machine-readable validation, and surfaced provenance.

It is open-first (both data and code-wise) and cloud-native by default, built on shoulders of the established Open GIS stack: STAC for discovery; GeoParquet, COG, and PMTiles for storage and delivery; DuckDB and PostGIS for compute; and QGIS, MapLibre, and Martin for presentation. It also teaches to deploy GDAL/OGR, GeoPandas, xarray/rioxarray, PDAL, routing engines, spatial SQL, and pragmatic hosted services when scale or reliability requires them.

## What's in this repo

The skill collection has four independently installable skills:

| Skill | Use it for |
|---|---|
| [open-map-stack](skills/open-map-stack/SKILL.md) | Ambiguous or multi-stage GIS work; choose sources, compute, storage and delivery together. |
| [reproducible-gis-project](skills/reproducible-gis-project/SKILL.md) | Compile or maintain the project manifest, canonical pipeline, pinned sources, corrections, validation and reruns. |
| [geospatial-data-discovery](skills/geospatial-data-discovery/SKILL.md) | Find and assess open geospatial data under known requirements. |
| [spatial-sql](skills/spatial-sql/SKILL.md) | Write, review or optimize spatial SQL on an already chosen engine. |

Each skill has a small entry point and local references loaded as needed. The
generalist retains bounded-task support and material-analysis requirements when
installed alone. Optional product help is documented in the installed
[companion reference](skills/open-map-stack/references/companion-skills.md).


### Open Map Stack - general guidance

Includes all the skills.

- [generalist SKILL.md](skills/open-map-stack/SKILL.md) — the skill entry point: triggers, global defaults, format and compute decision matrices, anti-patterns, and a quick triage guide.
- [references/data-sources.md](skills/open-map-stack/references/data-sources.md) - lists OSM, Overture, Sentinel/Landsat, regional portals, STAC catalogs and others.
- [references/services-and-scale.md](skills/open-map-stack/references/services-and-scale.md) - depending on case use local installs or hosted/SaaS services for global-scale basemaps, elevation, routing, geocoding, place search, and postcodes.
- [references/user-data-sources.md](skills/open-map-stack/references/user-data-sources.md) - the user's own warehouse data: credentials by reference, read-only discovery, approval-gated snapshots, and the pin classes that make a warehouse table reproducible.
- [references/formats-and-crs.md](skills/open-map-stack/references/formats-and-crs.md) - how to choose formats, conversions, projections, EPSG codes.
- [references/processing.md](skills/open-map-stack/references/processing.md) - when and how to use GDAL/OGR, GeoPandas, xarray, DuckDB, PostGIS, PDAL and other open geo processing tools.
- [references/analytics.md](skills/open-map-stack/references/analytics.md) — do vector/raster analytics, terrain, hydrology, network, point clouds, geocoding etc.
- [references/web-delivery.md](skills/open-map-stack/references/web-delivery.md) — renderer selection for maps, PMTiles, MVT, Martin, TiTiler, MapLibre, deck.gl, kepler.gl, and lonboard formats and engines.
- [references/qgis.md](skills/open-map-stack/references/qgis.md) — QGIS desktop, plugins, PyQGIS, Processing, QGIS MCP.
- [references/validation-and-ops.md](skills/open-map-stack/references/validation-and-ops.md) — validation, manifests, attribution, and deployment checks, including the machine-readable reproducible-project contract.
- [references/project-spec.md](skills/open-map-stack/references/project-spec.md) — the specific`openmapstack-project/v1` schema: compiling any material analysis into a reproducible GIS project (`project.yaml`, pipeline, source provenance, overrides, validation, semantic presentation, QGIS output).
- [references/project-workflow.md](skills/open-map-stack/references/project-workflow.md) — the mandatory material-analysis workflow and delivery rules, loaded when a task needs a reproducible project.
- [templates/](templates/) — ready scaffolds (`project.yaml`, `pipeline.py`, `presentation.yaml`, `validation.yaml`) for new projects.

### Reproducible GIS project

Use [reproducible-gis-project](skills/reproducible-gis-project/SKILL.md) when the
analysis method and stack are chosen and another analyst needs to inspect,
execute and verify the result without the original conversation. It guides the
agent to produce:

- A `project.yaml` manifest with pinned sources, CRS, assumptions, ordered steps
  and outputs, so the inputs and decisions are explicit.
- One canonical pipeline with corrections and scenarios recorded as data or
  executable logic, so derived results can be rebuilt without manual edits.
- Machine-readable validation and run evidence, including a clean rerun, so
  failures and reproducibility claims can be checked.
- QGIS and web presentation derived from the same project, so maps and reports
  reflect the validated analysis.

The skill includes its own [project templates](skills/reproducible-gis-project/templates/)
and these local references:

- [project-workflow.md](skills/reproducible-gis-project/references/project-workflow.md) — required steps and delivery rules for a material analysis.
- [project-spec.md](skills/reproducible-gis-project/references/project-spec.md) — the `openmapstack-project/v1` manifest and artifact contract.
- [data-sources.md](skills/reproducible-gis-project/references/data-sources.md) — public data discovery and source assessment.
- [user-data-sources.md](skills/reproducible-gis-project/references/user-data-sources.md) — warehouse access and reproducible snapshots.
- [formats-and-crs.md](skills/reproducible-gis-project/references/formats-and-crs.md) — format choices and coordinate-system correctness.
- [qgis.md](skills/reproducible-gis-project/references/qgis.md) — QGIS project delivery and desktop integration.
- [validation-and-ops.md](skills/reproducible-gis-project/references/validation-and-ops.md) — checks, provenance and operational validation.
- [installation.md](skills/reproducible-gis-project/references/installation.md) — CLI setup and local example requirements.
- [companion-skills.md](skills/reproducible-gis-project/references/companion-skills.md) — optional product-specific help.

Installing the other skills is optional. If the source, compute or delivery
architecture is still undecided, start with `open-map-stack`.

### Geospatial data discovery

Use [geospatial-data-discovery](skills/geospatial-data-discovery/SKILL.md) when
the data requirements are known and the task is to find and assess suitable
public datasets or the user's own geospatial data. It guides the agent to:

- Match coverage, time, feature meaning, attributes and resolution to the
  question, so a convenient dataset is not mistaken for the right one.
- Check current provider metadata, access and license terms, so availability
  and permission claims have evidence.
- Assess completeness and distinguish a sample or partial catalog from a full
  extract, so missing features are not misread as absent features.
- Report a reproducible version or snapshot strategy, retrieval details and
  limitations, so a later analysis can use the same source.

The standalone skill includes these local references:

- [data-sources.md](skills/geospatial-data-discovery/references/data-sources.md) — public data providers, catalogs and discovery methods.
- [user-data-sources.md](skills/geospatial-data-discovery/references/user-data-sources.md) — read-only warehouse discovery and snapshot rules.
- [formats-and-crs.md](skills/geospatial-data-discovery/references/formats-and-crs.md) — formats, coordinate systems and spatial extents.
- [companion-skills.md](skills/geospatial-data-discovery/references/companion-skills.md) — optional product-specific help.

A source assessment needs no project templates, worked example or OpenMapStack
CLI. Use `open-map-stack` when source selection depends on unresolved compute,
storage or delivery choices; add `reproducible-gis-project` if the user wants to
turn a chosen analysis into a full project.

### Spatial SQL

Use [spatial-sql](skills/spatial-sql/SKILL.md) to write, review, debug or
optimize a spatial query on an already chosen engine, such as PostGIS or DuckDB
Spatial. It guides the agent to:

- Check the engine, geometry types, CRS, input grain and expected result, so
  the query uses supported functions and returns the intended entities.
- Choose spatial predicates and boundary behavior deliberately, including how
  null, invalid or multiply matched geometries affect the result.
- Use appropriate metric or geodesic operations for distances, areas and
  buffers, so a numeric threshold has the intended units.
- Review indexes, candidate filters and query plans, then run small controls
  when the engine is available, so performance changes preserve the result.

The standalone skill includes these local references:

- [spatial-sql.md](skills/spatial-sql/references/spatial-sql.md) — engine-specific spatial SQL patterns and query review.
- [formats-and-crs.md](skills/spatial-sql/references/formats-and-crs.md) — coordinate, unit and format semantics.
- [companion-skills.md](skills/spatial-sql/references/companion-skills.md) — optional product-specific help.

A bounded query review needs no project templates, worked example or
OpenMapStack CLI. Use `open-map-stack` when engine selection or cross-system
architecture is undecided; add `reproducible-gis-project` if the user wants to
compile a chosen analysis into a full project.


### Additional materials:

- [examples/tartu-development/](examples/tartu-development/) — a fully-worked reproducible project matching the acceptance scenario: source provenance + timestamps, explicit assumptions, two verified project overrides (a scenario attribute change with prior-value verification, and hypothetical scenario geometry), deterministic pipeline, machine-readable validation, and semantic presentation.
- [evals/](evals/) — the eval suite grading whether an agent reaches the right analytical answer, respects the GIS-method guardrails, and reruns reproducibly, with the `openmapstack-project/v1` contract as the substrate that makes those independently checkable: `python evals/run.py --mode fixture` runs deterministic, no-LLM checks against real generated artifacts (analytical correctness against known geospatial truth, metric CRS, source immutability, schema, overrides, validation integrity, presentation contract, and clean reruns), plus adversarial cases and a pluggable live-agent benchmark (Claude Code, Codex, and any OpenAI-compatible API such as OpenRouter — URL and model via `OPENAI_COMPATIBLE_*` env, API key as a secret).
- [`openmapstack/`](openmapstack/) — the installable `openmapstack validate/run/inspect` CLI for auditing and executing `openmapstack-project/v1` projects, plus [`openmapstack/checks/`](openmapstack/checks/): the reusable, semantic check library. All but five of its checks are oracle-free, so the same functions that grade the eval suite also grade a user's own project on data this repository has never seen.
- [docs/openmapbench-interop.md](docs/openmapbench-interop.md) — the narrow, versioned contract a benchmark harness such as OpenMapBench consumes: `openmapstack checks` / `check` / `api-info` (`openmapstack-check-api/v1`), the packaged result schemas, skill snapshots, arm provenance, and exported task bundles.
- [`.claude-plugin/`](.claude-plugin/) — Claude Code plugin and marketplace manifests, so the repository can also be installed with `/plugin install`. Validated in CI by [`.github/workflows/plugin.yml`](.github/workflows/plugin.yml).

Some my local Estonia-specific guidance (Maa- ja Ruumiamet, ETAK, EPSG:3301 / L-EST97) is included for convenience. But most of the major global sources are included for world-wide coverage.

## Install

The 0.4.0 collection provides four independently installable skills. The
OpenMapStack CLI is a separate installation for project execution and validation.
From a checkout, install the generalist and CLI for project work:

```bash
npx skills@1.5.26 add . --skill open-map-stack -a codex -y
python -m pip install '.[geo]'
```

Use `-a claude-code` for Claude Code, `-g` for global installation and `--copy`
for independent copies. Select additional skills by repeating `--skill NAME`;
`--skill '*'` selects the full collection. `open-map-stack` and
`reproducible-gis-project` include local project templates, the project schema,
a trimmed worked Tartu example and CLI setup instructions. The two bounded
specialists ship their task guidance and local references; they need no CLI or
installed sibling. Generated example outputs and downloaded source data are
omitted; the example pipeline needs network access and its documented GIS
environment.

Once the release tag and Python package are published, pin both parts of the
coordinated release:

```bash
npx skills@1.5.26 add https://github.com/jaakla/openmapstack-skills/tree/v0.4.0/skills/open-map-stack -a codex -y
python -m pip install 'openmapstack[geo]==0.4.0'
```

For a floating install use `npx skills@1.5.26 add jaakla/openmapstack-skills
--skill open-map-stack`. Check installed skills with `npx skills@1.5.26 list`,
update with `npx skills@1.5.26 update open-map-stack --project`, and remove with
`npx skills@1.5.26 remove open-map-stack`. Use `--global` instead of `--project` for a global update; match the scope for list/remove too.
Updating a floating install advances its version; release-pinned installs
should be replaced with an explicitly chosen release.

### Claude Code plugin

```text
/plugin marketplace add jaakla/openmapstack-skills
/plugin install open-map-stack@open-map-stack
```

The plugin retains its identity and discovers `skills/<name>/SKILL.md`.
For local development use `claude --plugin-dir /path/to/checkout`; metadata
validation and component inventory require no paid model run.

### Manual installation and migration

Copy `skills/<name>/` into your agent's skill directory. Do not copy the whole
collection checkout into a single skill folder. If replacing a 0.3.0 root
installation, retain the `open-map-stack` name and replace its installed payload;
do not keep both root and nested copies. Inspect your actual installer lockfile
and scope before changing anything. Legacy `open-gis` is a different identifier:
remove it explicitly if it is an unwanted duplicate, rather than silently
rewriting its lock entry.

The CLI is separate from skill installation and is needed only for project
execution or validation. When installing it, verify `openmapstack --version`
against the project skill's `metadata.version`. Before publication, a wheel or
pinned Git commit from the matching checkout is the supported alternative.

## Use

Agents discover the installed skill descriptions and select relevant guidance.
Selection depends on the agent; explicitly naming a skill can help when you
want a particular owner. Installing the collection does not require loading
all four skills for every request.

- “We need regional analysis and browser maps for a billion building records; choose the architecture.” → `open-map-stack`.
- “Keep this analysis and stack, but make it reproducible and auditable.” → `reproducible-gis-project`.
- “Find authoritative Estonian building footprints; explain coverage, license and a reproducible pin.” → `geospatial-data-discovery`.
- “Review this PostGIS query for a 500-metre distance test on SRID 4326 geometries.” → `spatial-sql`.

The primary skill can use focused support. A bounded lookup or query review
does not require a project; material multi-stage analyses retain the complete
reproducibility contract. Casual place lookups and ordinary non-spatial coding
are outside the collection's scope.

See [0.4.0 release preparation](docs/release-0.4.0.md) for executed installation
checks, consumer migration and remaining release acceptance.

## Project CLI

> The key innovation of the skill is not just do the work every time again and then forget it, but to create special well-defined project with data and process descriptions and rerunnable scripts, so the whole process becomes investigatable and repeatable.

To help with that we have special CLI to work with the projects.

The CLI operates on an `openmapstack-project/v1` manifest. A project directory may
be supplied in place of its `project.yaml` file.

```bash
# Audit the complete artifact, including outputs, report, and run record.
openmapstack validate path/to/project.yaml

# Check the produced artifacts without requiring a golden answer.
openmapstack verify path/to/project.yaml

# Run the one canonical pipeline, then validate what it produced.
openmapstack run path/to/project.yaml

# Review sources, versions, overrides, ordered steps, outputs, and latest run.
openmapstack inspect path/to/project.yaml

# Copy SKILL.md, references/, and templates/ into a hashed, inspectable snapshot.
openmapstack skill-snapshot --out /tmp/oms-skill --json
openmapstack skill-snapshot --inspect /tmp/oms-skill

# Read-only discovery of a warehouse source, then an approval-gated snapshot.
openmapstack source discover path/to/project.yaml --source parcels
openmapstack source snapshot path/to/project.yaml --source parcels \
  --query "SELECT id, geom FROM cadastre.parcels" --destination data/source/parcels.parquet --approve
```

Useful automation options:

```bash
openmapstack validate project.yaml --json --output validation/cli-report.json
openmapstack validate project.yaml --strict       # warnings also return non-zero
openmapstack validate project.yaml --preflight    # skip not-yet-generated artifacts
openmapstack run project.yaml --dry-run        # print the command, execute nothing
openmapstack run project.yaml --json
openmapstack inspect project.yaml --json
```

`run` reads Python dependencies from `runtime.environment` in `project.yaml`
and installs them into a reusable project virtualenv, including when invoked
through `uvx`. It warns if the CLI's Python version differs from the declared
version: `python: '3.12'` accepts any 3.12 patch release. Select a matching
interpreter with `uvx --python 3.12 openmapstack run project.yaml`.

Native dependencies such as GDAL, PROJ, QGIS, PostgreSQL, and PostGIS require
external setup. `run` emits installation and verification guidance for declared
native dependencies; their availability remains **unverified**. A GDAL installation
may need particular drivers and matching Python bindings in the pipeline's runtime.
PostGIS must be installed on the database server and enabled in the target database;
installing a Python client is insufficient. Use a prepared system, Conda, or container
runtime via `runtime.implementation.command` when needed. The declaration
`gdal: DuckDB spatial extension` does not request a system GDAL installation:
the pipeline must install/load DuckDB's `spatial` extension. See the
[runtime contract](skills/open-map-stack/references/project-spec.md#28-runtime-runs-warnings)
for dependency syntax and execution behavior.

Runtime advisories appear on stderr, or in the JSON `warnings` array, including
for `--dry-run`. They are non-blocking and separate from artifact validation;
`--strict` continues to apply to validation, not these advisories. An explicit
launch command owns its interpreter, so the CLI does not compare that interpreter
against its own Python version.

### Sampled runs — nail it before you scale it

A wide-area analysis can run for hours before a late step fails. A sampled run
executes the same pipeline over a deliberately smaller slice, so failure
arrives in minutes:

```bash
openmapstack run project.yaml --sample                     # the manifest's declared sample
openmapstack run project.yaml --sample-area 26.6,58.3,26.8,58.4
openmapstack run project.yaml --sample-rows 5000
openmapstack run project.yaml --sample-fraction 1.0
```

Each flag binds a `runtime.implementation.parameters` entry that declares the
matching `role`; sampling a project that declares none is refused, naming what
the manifest must add. The canonical run still passes nothing.

**A sampled run proves the pipeline executes; it does not establish the
result.** Clipping to a test AOI breaks neighbourhood operations at the cut and
row sampling destroys the spatial coherence a join needs, so sampled counts are
not answers. That is enforced, not merely advised: a sampled run record is
marked `mode: sampled`, must record what it *realized* rather than only what
was requested, and can never become `runs.latest` — `openmapstack validate`
reports this as `runs.sample_isolation`, and `run --sample` fails outright if a
pipeline promotes its own sampled run — by moving `runs.latest`, by rewriting
the record it already points at, or by leaving no sampled record behind at all.
See `references/project-spec.md`.

### `openmapstack verify` — check the analysis, not just the paperwork

`validate` audits the manifest and its bookkeeping. `verify` runs the check
library in `openmapstack/checks/` against what the pipeline actually produced:
geometry read back through DuckDB Spatial, dataset CRS read from the artifact
rather than the manifest's claim, validation evidence recomputed from the
geodata it summarises, non-spatial `kind: table` outputs read back as tables
(declared columns and types, unique key, CSV/XLSX downloads holding the same
rows), QGIS project structure and runtime loading where PyQGIS is available, and
— for projects that declare a design-language version — every dashboard or
report page operated in a real browser.

```bash
openmapstack verify path/to/project.yaml
openmapstack verify path/to/project.yaml --rerun     # + rebuild from source and compare
openmapstack verify path/to/project.yaml --metamorphic   # + run declared no-oracle relations
openmapstack verify path/to/project.yaml --json --output validation/verify-report.json
openmapstack verify path/to/project.yaml --strict    # warnings and not-testable also return 1
```

These checks require no repository-owned golden answer, so they work on data
neither this repository nor the model has seen. They establish bounded
structural, provenance, artifact, and reproducibility predicates; they do not
prove every project-specific analytical answer.

`--rerun` is the strongest signal available without a known answer. It rebuilds
the project in an empty workspace from only the manifest, the declared
immutable inputs, and the declared dependencies, runs the one canonical
entrypoint, re-hashes the sources, and compares the outputs semantically. A
pipeline that cannot reproduce itself, or that mutates its own declared
immutable inputs, is not trustworthy whatever its numbers say.

The check plan is derived from the manifest rather than configured, so a
project cannot opt out of a check by omitting it: a declared output is a
checked output. A check whose dependency is missing reports `not_testable` and
is counted separately — never a silent pass. A mixture of executed and
`not_testable` checks has aggregate status `warning`, and every report includes
`applicable`, `executed`, and `execution_rate` coverage. Install
`openmapstack[geo]` for the DuckDB-backed geodata checks and for reading XLSX
(DuckDB `excel` extension) and Parquet tables; CSV and JSON tables need nothing
extra. PyQGIS comes from a system QGIS install. For the browser check, install
`openmapstack[visual]` and run `python -m playwright install chromium`.

The browser check runs when the manifest sets `presentation.design_language`
(see `references/design-language.md`). It opens every declared page and
operates each declared control through the language's state hooks, including
controls on inactive tabs: a control must change the page, an exploratory one
must show the exploratory label, and the reset must restore the published
state. Projects without a language version are not browser-checked by
`verify`.

See [the applicability reference](docs/verify-applicability.md) for the exact
plan conditions, dependencies, current regression evidence, and deliberate
exclusions.

Project-specific known answers can be declared under
`validation.expectations[]`. The five allowlisted checks cover row count,
feature presence/absence, one feature-field value, and field range. New
expectations start as `attestation.status: unverified`; they produce a warning
and are not executed. The JSON report supplies the exact
`expected_expectation_sha256` an independent reviewer must bind, together with
the current `runs.latest.inputs_hash`. Changing the expected check, arguments,
inputs, or a retained local evidence file invalidates the attestation and
returns it to warning status. See
[the project contract](skills/open-map-stack/references/project-spec.md#26-validation).

Where no golden answer exists at all, `validation.metamorphic[]` declares
relations that must hold under a controlled perturbation: shuffle a source and
the result must not change, duplicate every feature and a keyed set must not
change, widen an inclusion buffer and no candidate may disappear. Each relation
states the precondition that makes it valid, is executed by
`verify --metamorphic` in an isolated copy against the project's own pipeline,
and reports `not_testable` with the reason when the precondition does not hold
on the actual data. See [the project contract](skills/open-map-stack/references/project-spec.md#26-validation).

`openmapstack source` is the connector pilot for the user's own data
(DuckDB local files and PostGIS). Credentials are referenced, never stored;
discovery is read-only with a statement timeout; a snapshot is a dry run
until `--approve`, is limited by rows and bytes, lands only under
`data/source/`, and hands back the `pin` block that makes the source
reproducible. A warehouse table with only a timestamp is not pinned; an
expired backend snapshot is reported as `not_reproducible`. See
[user data sources](skills/open-map-stack/references/user-data-sources.md).

`validate` checks manifest structure, source retrieval/version/licensing data,
CRS declarations, processing graph resolution, override provenance and files,
output existence, validation-report parity/status propagation, override
application results, and run-record identity/hashes. GIS-specific checks such as
geometry validity remain the pipeline's responsibility; the CLI verifies that
each declared check appears exactly once with an explicit result.

Normal validation warnings return exit code 0 so known limitations remain
representable. Failures return 1; malformed invocation or an unstartable runtime
returns 2. `--strict` makes warnings return 1.

## What this skill will and won't do

**Will:**
- Recommend modern, cloud-native formats (GeoParquet, COG, PMTiles) and flag legacy patterns (Shapefile output, MBTiles for new deployments).
- Push spatial joins to DuckDB / PostGIS instead of Python loops.
- Discover data via STAC before downloading.
- Preserve license metadata (OSM ODbL, Overture per-source, Sentinel attribution).
- Pin dataset versions for reproducibility (Overture releases, STAC item IDs, OSM extract dates).
- Compile material multi-stage analysis into a reproducible GIS project (`project.yaml` + pipeline + overrides + validation), deriving the final map/dashboard from it.

**Won't:**
- Trigger on simple location lookups ("what city is this?") or casual map references with no analytical work.
- Default to proprietary services when an open/self-hosted option fits the scale, quality, privacy, and budget.

## License

Licensed under the [MIT License](LICENSE).

## Contributing

Issues and PRs welcome at [github.com/jaakla/openmapstack-skills](https://github.com/jaakla/openmapstack-skills). When adding a new tool or workflow, place it in the matching reference file and add a one-row entry to the relevant decision matrix in [generalist SKILL.md](skills/open-map-stack/SKILL.md).
