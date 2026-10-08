# 0.4.0 final-state acceptance

Status: **live-trial gates closed by the maintainer on 2026-09-26; the map-layer source contract is
deferred to #67; packaging and hosted visual checks refreshed; live-dashboard gaps explicitly
deferred to #69 and publication authorized**. See the [final preparation review](https://github.com/jaakla/openmapstack-skills/blob/v0.4.0/docs/release-0.4.0-final.md)
for current evidence. This file records OpenMapStack's
release gate. Live trials used this repository's runner with the sandboxed Claude Code adapter ([ADR 0006](../docs/maintainers/decisions/0006-release-trials-in-repository-sandbox.md)).
The OpenMapBench trials below remain supporting evidence; generic benchmarking continues there.
Historical routing results remain in [the release record](https://github.com/jaakla/openmapstack-skills/blob/v0.4.0/docs/release-0.4.0.md).

## Historical candidate and benchmark (September 23)

- OpenMapStack: `e78bae896eb647cd436004b121b8659f34092ca6`, version 0.4.0.
- Exact wheel and full/subset snapshot hashes: [candidate evidence](../docs/acceptance-0.4.0-rc.json).
- OpenMapBench: `ba704e5fb968f0e97d10ca6a7c42888f824959d1` for the live set (review branch; not yet merged).
- Public development packs: `openmapstack-project` v1 (001, 070–073) and
  `openmapstack-routing` v1 (eight unhinted review tasks).
- Canonical [execution/review runbook](https://github.com/jaakla/OpenMapBench/blob/14409b3a9262b481bf9c806950e0eaf1363fbbe0/docs/openmapstack-acceptance.md).

Use retained wheel/snapshot bytes and a committed benchmark revision. A changed payload starts a
new acceptance set. The dirty QGIS worktree and historical mixed-payload trials are not this RC.
This is prepublication wheel acceptance, not evidence that 0.4.0 is already released on PyPI.

## Executed deterministic evidence (2026-09-23)

601 OpenMapStack unit tests passed with 46 optional-environment skips. Fixture evals passed
16/16 contract cases and detected 26/26 mutations; two contract assertions remain `not_testable`
and two soft gates remain unmet. OpenMapBench subsequently passed all 110 tests and Ruff, including its installed
wheel integration controls. The material-project control validates and cleanly reruns through the
public package; the Estonian known-answer control passes. Wrong CRS, mutated source, missing
pipeline, laundered validation and invented-feature controls fail with their intended stable codes.
These are harness/checker controls, not live-agent results. QGIS remains explicitly unavailable.

## Native trial outcome (2026-09-23)

A new **$10 aggregate cap** was explicitly authorized for `claude-sonnet-4-6`. The fixed set used
CLI 2.1.280, medium effort, an allowlisted rootless Bubblewrap runtime and the retained wheel/full
or subset snapshot. Preflight proved the intended mounts without a model request. Native activation
and raw provider events are retained; Bash read telemetry is partial. CLI builtins `design` and
`doctor` remained exposed despite the disable request and were not observed activated.

Nine attempts reported **$6.530115** in API-equivalent cost; **$3.469885** remains authorized. The
last attempt was rejected at the provider's organization monthly spend limit with zero usage/cost.
No further paid calls were made. This cost report is not an invoice. Both compilation caps overshot
slightly; the controller's aggregate safety reserve kept spending below the authorized total.

The negative lookup passed review. Collection SQL selected its specialist and repaired the supplied
query but added an inaccurate generalization; it was not executed. Discovery selected its specialist
but failed source/license/pinning review. Both compilation runs selected the owning specialist and
exhausted their per-trial budgets. All three material arms are unscorable: the v1 private grader
assumes output names/source IDs not specified by the public contract. Independent QGIS artifact,
Parquet metadata and checker errors are also recorded. No material clean rerun passed.

See the [trial review](https://github.com/jaakla/OpenMapBench/blob/14409b3a9262b481bf9c806950e0eaf1363fbbe0/docs/openmapstack-acceptance-20260923.md) and its hashed evidence index
for exact per-trial judgments and defect attribution. Native statuses and the frozen candidate
were preserved; no retrospective rubric changes or fixture-based live passes were applied.

## Corrected candidate outcome (2026-09-23)

The review above led to producer fixes `6d1e81b` (checker WKB/CRS handling) and `0749b07`
(licensing, building scope, pinning and PostGIS geography guidance). OpenMapBench v2 packs now
disclose every required delivery name. Focused trials against `0749b07` used the same harness:

- `chosen-engine-sql`: the geography claim was fixed. It still offers an equal-area CRS for
  distance work. `needs_review`.
- `bounded-discovery`: licensing, `tyyp` scope and content-hash pinning are correct. It made no
  provider lookup and claims without evidence that alternatives derive from ETAK. `needs_review`.
- `001-basic-spatial-analysis`: **failed** two required checks the task disclosed. Layers declared
  only an authority ID, and `cadastral_parcels` declared no `semantic_predicates`. The agent never
  activated a skill and inferred the contract from package source. `openmapstack validate` still
  reported all checks passed, because it kept a weaker copy of the layer-CRS check.

Follow-ups `908bf95` (fresh provider lookup; distance versus area CRS guidance) and `250c99c`
(`validate` delegates to `qgis.every_layer_declares_crs`) have no live evidence yet. Reported spend
is **$8.760220** of the $10 authorization; the remaining $1.239780 does not fit another material
trial with its reserve. See the [focused outcomes](https://github.com/jaakla/OpenMapBench/blob/feat/oms-release-acceptance/docs/openmapstack-acceptance-fixes-20260923.md#focused-live-outcomes).

## In-repository trials against `d4bfa5e` (2026-09-23)

A new authorization capped this round at $4.00 using `claude-sonnet-4-6` (Claude Code 2.1.281).
The candidate was frozen at `d4bfa5e`: collection content
`sha256:3f4d7b9da430c1f53cd65c4cc87189d25492febe6a5080903ff9bd6505df94b3`, the same skill payload
as `7b1a886`. Routing used local image `sha256:ab1c1835f312edd67d21b03e7ec75aba5a04c08e49e2c54367186288a0e52fe8`,
built from `evals/containers/routing.Dockerfile` on `node@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6`.
`d4bfa5e` also discloses the 001 artifact interface, whose earlier prompt hid the required names.

| Trial | Selection | Reviewed outcome | Status | Reported USD |
|---|---|---|---|---:|
| `chosen-engine-sql` (routing, collection) | `spatial-sql` | Passed: units, index use via matching expression index, duplicates, UTM projection for distance | `passed` | 0.135944 |
| `bounded-discovery` (routing, collection) | `geospatial-data-discovery` | Attempted three provider pages, which the harness refused; labeled those claims unverified. ETAK license, `tyyp` scope, pinning and paging correct. Wrong licenses for two alternatives, both from shipped guidance: Overture buildings are ODbL, Microsoft footprints CDLA-Permissive 2.0 | `not_testable` (`ToolSearch` telemetry gap) | 0.206338 |
| `001-basic-spatial-analysis` (live, sandboxed) | injected guidance | Not graded: harness defect refused almost every `Bash` command | setup failure (`error_max_budget_usd`) | 2.002112 |
| Sandbox tool check | — | `Bash`, DuckDB and `openmapstack` work after the fix | — | 0.037616 |

Record hashes: routing `3504530c…53ff35` (discovery), `20373d50…e874ed` (SQL); material agent
record `94a41843…e64c29`. Spend this round: **$2.382009 of $4.00**; $1.617991 remains, below one
material trial's cap. The material failure is a harness defect, not a skill result: the adapter had
relied on the maintainer's own tool allow rules, which the sandbox hides. `61bb27a` grants tools
explicitly; `bcb4131` stops counting `ToolSearch` and web tools as unattested skill reads. Neither
change regrades these trials. Routing still refuses `WebFetch`, so it cannot observe the fresh
provider lookup the discovery guidance now requires.

## Second in-repository round (2026-09-23)

A further **$10.00** was authorized for `claude-sonnet-4-6`. `5497c8d` let routing use web tools.
Each failure below traced to shipped guidance and was corrected before the next trial, so the
candidate moved: `7180e0e` (footprint licenses; `semantic_predicates` rule), then `cb4c009` (check
for the CLI; do not deliver while `validate` fails). Guidance regression tests guard each fix.

| Trial | Candidate | Selection | Reviewed outcome | Status | Reported USD |
|---|---|---|---|---|---:|
| material 001 (live) | `5497c8d` | injected | 35/36 required checks; zoning kept only as free-text `selection.filter` | failed `provenance.semantic_predicate_documented` | 1.986656 |
| `bounded-discovery` (collection) | `5497c8d` | passed | Live provider lookups; repeated the guidance's wrong Overture/Microsoft licenses | `passed` / needs review | 0.374675 |
| `bounded-discovery` (collection) | `7180e0e` | passed | Four live lookups, correct licenses, honest unverified PDF. Minor: unverified retention rationale | `passed` / passed | 0.293311 |
| `bounded-discovery` (discovery only) | `7180e0e` | passed | Read the license PDF; correct alternative licenses | `passed` / passed | 0.324528 |
| `ambiguous-architecture` | `7180e0e` | passed (`open-map-stack`) | Consequential questions and coupled choices | `passed` / passed | 0.076916 |
| `future-scale-architecture` | `7180e0e` | passed (`open-map-stack`) | Separates concurrent edits from pinned analytical snapshots at scale | `passed` / passed | 0.192059 |
| `casual-place-lookup` | `7180e0e` | passed (no GIS skill) | Brief and correct | `passed` / passed | 0.017089 |
| `material-analysis-generalist-only` | `7180e0e` | passed (single) | Complete plan; records land-use codes as `semantic_predicates`. Minor: unverified ETAK stop layer name | `passed` / passed | 0.413073 |
| material 001 (live) | `7180e0e` | injected | Predicates fixed; declared `parcel_area_range` but omitted it from the report. Never ran `validate`, which flags it | failed `validation.required_all_present` | 1.929765 |
| material 001 (live) | `cb4c009` | injected | Checked for the CLI, ran `validate`, was fixing its failures; about ten turns went on probing the runtime | setup failure (`error_max_budget_usd`, 70 turns) | 3.043631 |

Record hashes (SHA-256 prefix): material `a4403d5d`, `c94366d3`, `6df48698`; routing at `7180e0e`
`70e61a10` (collection discovery), `7ae36587` (standalone discovery), `bbb0e503`, `09f16719`,
`abd2e449`, `3f4c7f42`. `chosen-engine-sql` passed at `d4bfa5e`; `spatial-sql` content is unchanged
since. Round spend: **$8.651825 of $10.00**; $1.348175 remains, below one material trial.

Material 001 has not yet passed. The last trial shows the intended validate-and-repair loop, but
the prompt does not say which runtime exists (DuckDB Spatial and the CLI, no geopandas or `pip`),
so the agent spent turns probing it. Disclose the runtime in the 001 prompt, as OpenMapBench does,
before the next paid material trial, and allow a larger cap for the repair loop.

## Material 001 at `f142d95` (2026-09-24)

A further $4.50 was authorized. With the runtime stated in the prompt, material 001 **passed its eval**
at `f142d95` (skill payload identical to `cb4c009`): all 35 hard assertions, with `qgis.runtime_load`
soft and `not_testable`. The agent checked for the CLI, ran `validate`, fixed the two failures it
reported and delivered on a 30/30 `validate`. It used 49 turns and **$2.705749** (record `2ea24b65`).

Independent `openmapstack verify --rerun`, run inside the sandbox, still fails it. A runtime parameter
is bound to a field its step does not declare (`project.parameters_match_steps`). Neither `validate`
nor the 001 eval checked that. The clean rerun rebuilt equal outputs and a reproducible report, but
failed post-rerun validation, in part because the rerun copy had no README. Fixes:

- `9d2630c`: `validate` applies `verify`'s runtime-parameter rules, so the repair loop can see it.
- `60bbe14`: the clean-room rerun carries `README.md` as documentation.
- `4456691`: live clean reruns run in the sandbox rather than on the host, and live 001 now
  declares a clean rerun with the `rerun.*` assertions.

Replaying this delivered project through the updated runner fails only
`rerun.clean_execution_succeeded`, on the parameter defect. **Material 001 has therefore not
passed the full gate.** Round spend: $2.705749 of $4.50; the $1.794251 left does not cover another
material trial. The next one runs against a candidate frozen at or after `4456691`.

## Material 001 at `45063ca` (2026-09-24)

Candidate `45063ca` (`main` after #53 and #54; collection
`sha256:4dd248d87e3f54659077b4fcd8d97a0d3c3876751cae948a4beec9670b622937`). Material 001
**passed** all 35 hard assertions and the four `rerun.*` assertions. The clean rerun ran in the
sandbox, produced semantically equal outputs and reproduced the validation report.
`qgis.runtime_load` stayed soft and `not_testable`. The agent checked for the CLI, ran `validate`
and repaired what it reported: 44 turns, **$2.987357** at a $4.00 cap (record `25199bd4`).

Independent sandboxed `verify --rerun` found no failures; the earlier parameter defect did not
recur. Seven checks were `not_testable`: three need PyQGIS, and four outputs declare no EPSG code in
their format strings. The project reports `warning`, consistent with its own `not_testable`
QGIS runtime check.

The three PyQGIS checks were then run in the sandbox against the host's QGIS 3.40.15 (GDAL 3.12.2).
**All three failed.** The candidate layer points at GeoParquet, the Ubuntu GDAL build has no
Parquet or Arrow driver, and the map draws nothing. Shipped `qgis.md` blamed "an old GDAL"
instead. The follow-up adds `qgis.datasources_portable`, which warns on local layers outside an
allowlist of formats every GDAL build reads, and which `validate` reports as
`qgis.datasource_formats`. The QGIS guidance now requires
GeoPackage/GeoJSON/FlatGeobuf layers, and live 001 requires the check. One further material trial
must show agents follow it.

## Material 001 at `12fde5f` (2026-09-24)

Candidate `12fde5f` (collection `sha256:f9d5fe88cf56a24ce9ba353a1938f8e18da8ee742afc3d173a98f1ab538ce008`),
under a new $10 authorization. The portable-format rule held: `qgis.datasources_portable` passed,
and in the sandbox all three PyQGIS checks passed against the host's QGIS 3.40.15. Every layer
was valid, matched the manifest and rendered.

Material 001 **failed** `rerun.clean_execution_succeeded`. The agent built WGS84 GeoJSON layers for
the dashboard with one-off scripts, not the pipeline. It declared them as outputs, as `validate`
requires, and the pipeline hashes them in its run record. So the clean rerun crashed on a file the
pipeline never writes. The agent never ran a clean rerun; the guidance said to, without naming
`openmapstack verify project.yaml --rerun`. 63 turns, **$3.656927** (record `2357eb7d`).

Follow-up: both project skills now name that command and say every derived file must come from the
canonical pipeline.

## Material 001 at `70d110a` (2026-09-24)

Candidate `70d110a` (collection `sha256:4f55897533424d005de4fb7828c5c763cdfcebbd0ddb34eba11a0b4dc25f206c`).
Material 001 **passed its eval**: every hard assertion, including `qgis.datasources_portable` and
the four `rerun.*` assertions from the sandboxed clean rerun. 57 turns, **$3.048889** (record
`341de3ae`). The agent ran `validate` and `verify`, not `verify --rerun`.

Independent checks found two more issues. Sandboxed `verify --rerun` failed
`rerun.outputs_semantically_equal` on the GeoPackage output; its features were identical and only
`gpkg_contents.last_change` differed. That was a comparator defect, since GeoPackage was compared
by bytes. The real-QGIS render check failed: roads and POI GeoJSON were WGS84 but declared
EPSG:3301, so QGIS drew them near the grid's origin.

Follow-ups:
- GeoPackage outputs are compared semantically.
- New `qgis.layer_crs_matches_data` (in `validate` as `qgis.layer_crs_data`) fails a declared CRS
  that contradicts the data. Live 001 asserts it, and the QGIS guidance states the rule.
- The review also found absolute datasource paths in the committed NYC project (#54).
  `qgis.static_valid` now fails absolute paths, and the example writes relative ones.

Round spend so far: $6.705816 of the $10 authorization.

## Round at `b65a2b4` (2026-09-24)

A new **$20** authorization. Candidate `b65a2b4` (`main` after #59; collection
`sha256:6f9dad1290f3fd6656711c6e3f31e4ee0ec6a3797953058fdb2a7e5982ee1dcd`), `claude-sonnet-4-6`,
Claude Code 2.1.281. Routing used local image `sha256:01cd8a23c12db638fbf58873c36a603228ccd17fdf1ade6f020cd5839ddfcdbb`,
rebuilt from `evals/containers/routing.Dockerfile` on the same pinned Node base with Codex 0.156.1.
The maintainer reviewed task outcomes; verdicts below are theirs.

| Trial | Selection | Reviewed outcome | Verdict | Reported USD |
|---|---|---|---|---:|
| material 001 (live) | injected | All hard and `rerun.*` assertions; delivered P1, P2, P5. Ran `validate` and repaired, not `verify --rerun`. 54 turns | passed | 3.375187 |
| `chosen-engine-sql` (collection) | `spatial-sql` | Units, `ST_DWithin`, matching expression index, `DISTINCT`, plan check. Unexecuted | passed | 0.113604 |
| `chosen-engine-sql` (`spatial-sql` alone) | `spatial-sql` | Correct core. Proposed an arbitrary UTM zone for data of unknown extent, and wrongly said a plain index serves an `ST_Transform` predicate | needs review | 0.113567 |
| `bounded-discovery` (collection) | `geospatial-data-discovery` | Five live lookups; unreadable license PDF labelled unverified. GPKG listed though the live page offers SHP/TAB/DGN/DWG (from shipped `data-sources.md`). OSM/Overture "derive from" ETAK has no cited source | passed with notes | 0.301924 |
| `compile-existing-analysis` (collection) | `reproducible-gis-project`; `not_testable` (Bash read surface) | Algorithm kept; missing source details listed, not invented; honest that nothing ran. Unexecuted scaffold; extra outputs; pins unpublished `openmapstack==0.4.0` | needs review | 1.422498 |
| `billion-row-architecture` (collection) | `open-map-stack` | Sound overall architecture. Partitions on a `country_iso` column Overture buildings lack; H3 resolution 3 stated as ~1,000 cells (it has 41,162); pins a 2025 release while warning that only recent ones are retained | passed for now; errors stand | 0.395723 |

Record hashes (SHA-256 prefix): material `9e5faa71` (agent), `9589b565` (grading); routing
`2ace0d7d`, `4a07d100`, `0e1009c3`, `e8ecf94b`, `17f4523e`, in table order.

Independent sandboxed `verify --rerun` of the delivered 001 project, with the host's QGIS 3.40.15:
47 passed, 0 failed. All four PyQGIS checks passed, including `qgis.layer_crs_matches_data` and
`qgis.every_declared_layer_renders`. The one `not_testable` check is a candidate format string with
no EPSG code. The sandbox must also expose `/etc/alternatives`, or PyQGIS fails to load `libblas`
and those checks report `not_testable`.

Maintainer notes: Maa-amet data underlies both ETAK and, through community imports, parts of
Estonian OSM, but no public lineage record documents it, so the claim stays uncited. For lon/lat
data, geography on the spheroid is correct at any extent; a local projected CRS fits only a known
small area. `8e289e7` makes that the `spatial-sql` default. Rerun at `8e289e7`, the same case
chose geography because "the data extent is unspecified" when installed alone ($0.140620, record
`e0613e96`), and offered a projected CRS only for a confirmed extent in the collection
($0.118739, `86e788d3`). Both passed selection and the reviewed criterion. Review of that change
then limited the default to distance tests, since PostGIS buffers `geography` in a planar
projection; both reruns predate that wording and exercise only a distance query.

At the maintainer's direction, `24546c1` (#61) reduced `data-sources.md` to entry points and a
live-verification order, dropping formats, schemas, code values and licenses. Rerun there,
`bounded-discovery` passed selection ($0.335706, record `864ca91f`). It made eight live lookups,
took formats and attribution from the fetched ETAK page (GPKG and GDB, which the maintainer
confirms are offered), and dropped the uncited lineage claim. It still stated an Azure bucket
path, a `numberOfFeatures` catalog field and Tallinn attribute richness without a source; the
last is labelled unverified. Reviewer's read: passed with notes.

### Cases 070–073 at `f57e8c2` (2026-09-25)

`f57e8c2` states the runtime in all four prompts and asks 071–073 for the project they are graded
on (073 in Estonian). Collection `sha256:b1e08e3235e45bb1fd7380d65888a9531ea797361f111ff83731e277214bff9b`,
the same guidance as `1934129`. Injected guidance, sandboxed agent, $3 cap per trial.

| Case | Outcome | Status | Reported USD |
|---|---|---|---:|
| 070 vague request | Declared every choice as an assumption with rationale. Analysed in EPSG:3301, wrote candidates in lon/lat (valid GeoParquet, CRS84 by default) as the shipped `storage_crs: EPSG:4326` template does. 44 turns | failed `geodata.dataset_crs_is` (expects EPSG:3301) | 2.211000 |
| 071 impossible request | Empty result; project and report `failed`; high warning explains the contradiction. 35 turns | passed | 1.339321 |
| 072 missing attribute | Empty result, status `warning`. Listed the real code values, named `ELAMUMAA` as a possible meaning and asked for explicit authorisation instead of substituting. 36 turns | passed | 1.209080 |
| 073 Estonian prompt | Exactly P1, P2, P5 in EPSG:3301; warns the distance is straight-line. 46 turns | passed | 2.069766 |

Record hashes (agent / grading, SHA-256 prefix): 070 `3e32182a`/`dcbc38e6`, 071 `c6c331e4`/`cc8410bd`,
072 `cdb512ae`/`2980edc8`, 073 `d9ccad36`/`19a74a6d`.

The 070 failure is an eval defect, not a skill regression: the case requires EPSG:3301 output that
neither its prompt nor the shipped guidance asks for, and the templates default storage to
EPSG:4326. It is not regraded. Fixing it means either stating the output CRS in the prompt or
asserting that the output matches the project's declared `storage_crs`. 072's premise is also
wrong: the fixture has a `land_use` field, so the case tests a value absent from the code list,
not a missing attribute.

`08df564` then graded 070 with the new `geodata.dataset_crs_matches_storage_crs` (output CRS must
match the declared `processing.storage_crs`; EPSG:4326 and GeoParquet's default OGC:CRS84 count
as one) and restated 072 as a missing value. Reruns at `08df564`, same collection:

| Case | Outcome | Status | Reported USD |
|---|---|---|---:|
| 070 vague request | Ran out of budget in the validate-and-repair loop after 42 turns. The unfinished project declares `storage_crs: EPSG:4326` but wrote candidates in EPSG:3301, which the new check flags (not a grade) | setup failure (`error_max_budget_usd`) | 3.003894 |
| 072 missing value | Empty result, status `warning`; names the real codes and states nothing was imputed, mapped or substituted. 32 turns | passed | 1.021045 |

Record hashes (agent / grading): 070 `7cb1c35b`/`d557a5a1`, 072 `d3a1526c`/`e7b1a311`. The host
Claude Code updated itself to 2.1.282 before these two reruns; earlier trials in this round used
2.1.281. The new check passes on the first 070 trial's delivered project, but that trial stays
failed as graded. 070 has no graded pass under its current assertions.

Round spend: **$17.171674 of $20.00**.

## Refreeze at `861b58b` (2026-09-25)

A further **€5** was authorized, counted as $5.00 against USD-reported cost. Cases 070–073 are
postponed to OpenMapBench, their canonical home, which needs further work on them. Candidate
`861b58b` (`main` after #59–#62; collection
`sha256:b1e08e3235e45bb1fd7380d65888a9531ea797361f111ff83731e277214bff9b`), `claude-sonnet-4-6`,
Claude Code 2.1.282.

| Trial | Outcome | Status | Reported USD |
|---|---|---|---:|
| `chosen-engine-sql` (`spatial-sql` alone) | Geography "regardless of location"; projected CRS only for a known zone; `EXISTS` variant; matching index | passed | 0.149748 |
| `chosen-engine-sql` (collection) | Geography by default; labelled a reasoned review, `not_testable` without PostGIS | passed | 0.118384 |
| material 001 (live) | All hard assertions except the clean rerun. 88 turns | failed `rerun.clean_execution_succeeded`, `rerun.validation_report_reproducible` | 4.336621 |

`bounded-discovery` carries forward from `24546c1`: the discovery skill is unchanged since. Record
hashes: SQL `9394d756` (alone), `fe80ec90` (collection); material `b3f01fdb` (agent), `6545c917`
(grading).

Material 001 failed because the pipeline does not maintain the manifest's run pointer. The agent
patched `runs.latest.id`, output hashes and `project.status` into `project.yaml` with `sed` after
each pipeline run. A clean rerun writes a new timestamped run record, so the rebuilt project points
at `runs/run-20260925-192358.json`, which no longer exists. `project.qgz` was also not rebuilt by
the pipeline. The agent ran `validate` repeatedly but never `verify --rerun`, which would have
shown the failure. It read only `SKILL.md`, `project-spec.md` and the manifest template, none
changed since the `b65a2b4` pass, so this is not a regression from this round's guidance
changes: the same effective guidance passed once and failed once. No 001 agent this round ran
`verify --rerun`. The guidance's "every file under `data/derived` must come from the canonical
pipeline" does not cover the manifest's run pointer and status, or presentation artifacts.

Spend under the combined authorization: **$21.776427 of $25.00**; $3.223573 remains, below one
material trial.

## Material 001 at `66741f1` (2026-09-25)

A further **$10.00** was authorized. `66741f1` makes the template pipeline write the run record
and update `runs.latest` and `project.status` itself, as the worked example does, and makes the
last `verify --rerun` the delivery gate in both project skills (collection
`sha256:fb276c3ea313218ab5a2b8242f106ff496e1fa606f2d05df4a02e1c0becb2988`, Claude Code 2.1.282).

| Trial | Outcome | Status | Reported USD |
|---|---|---|---:|
| material 001, first | All hard and `rerun.*` assertions; P1, P2, P5. Adopted the template's `finalize_run`; no hand edits. Ran `inspect`, never `validate` or `verify`. 69 turns | passed | 3.663860 |
| material 001, second | Stopped after 38 turns when the provider rejected further calls at the organization's monthly spend limit. Not a skill result | setup failure | 2.184911 |

Record hashes (agent / grading): first `87643bc5`/`e049088f`, second `eafeb325`/`4df2f6c5`.

Independent sandboxed `verify --rerun` of the first trial's project with the host's QGIS 3.40.15:
46 passed, 0 failed, clean rerun passed, all nine `qgis.*` checks passed. Two `not_testable`
checks are outputs whose format strings name no EPSG code.

The template change fixed the failure mode seen at `861b58b`: the agent read the template
pipeline and its pipeline, not a hand edit, maintained the run pointer. The stronger delivery-gate
wording did not change behaviour: no 001 agent in four trials this round ran `verify --rerun`
before delivery, and the latest ran neither `validate` nor `verify`. One pass after the fix is
not yet evidence of reliability; the second trial could not complete.

Review of #64 then found that the template's run record lacked the `environment` mapping `validate`
requires, and inventoried only sources, overrides and `pipeline.py`, missing declared runtime
dependencies and command files. The follow-up adds both and tests the record against the
validator's run-record check. The live trial above used the earlier template; its agent's own
pipeline wrote a valid record, but the follow-up changes the shipped payload, so the next material
trial must run at or after it.

Spend under the combined authorizations: **$27.625198 of $35.00**; $7.374802 remains. The
provider's organization monthly limit blocks further paid calls until it is raised or resets.

## Material 001 at `480b5ad` (2026-09-25)

After the provider raised the organization limit, material 001 ran at `480b5ad` (`main` after #64,
including the `737666a` template follow-up), $4.50 cap. The agent read the templates, probed the
runtime and created the project folders in 12 turns, then announced it would write `pipeline.py`
and streamed that single file for about 25 minutes, with a `rate_limit_event` just before, until the
harness killed it at the 1800 s timeout. **Setup failure** (`agent_execution`: timed out); not a
skill result, and it gives no evidence about the final template. Record hashes (agent / grading):
`894299fa`/`8adf2ef9`.

The provider reported no cost for the killed session. Completed turns come to about $0.35 at list
prices; the cap bounds it at $4.50. Counting the cap, at least $2.874802 of the $35.00 remains,
below one material trial. The next trial should use a longer timeout, since throughput limits can
stretch a single large write.

Cases 070–073 are **parked** here: their remaining work moved to OpenMapBench, their canonical home,
as jaakla/OpenMapBench#7 (runtime disclosure, 072's false premise, the 070 CRS contract, v1's
hidden deliverable and CRS). They no longer block this repository's gate; see #39.

## Material 001 at `9ef5c45` (2026-09-26)

A separate **$5** was authorized for one material trial. Candidate `9ef5c45` (`main` after #65; collection
`sha256:2177d75e666914b4d4d87dba15c0741da1576d7e7509b8ebebcd5878b4d6e3a0`), $4.50 cap, 3600 s timeout.

Material 001 **passed its eval**: every hard and `rerun.*` assertion, candidates P1, P2, P5. 51 turns,
**$2.863507** (record `fa3c0951` agent, `2c146348` grading). The agent read the template pipeline,
kept its `finalize_run`, and was the first 001 agent in this round to run `verify project.yaml
--rerun`: twice, 0 failed each time, with PyQGIS checks correctly `not_testable`.

Independent sandboxed `verify --rerun` with the host's QGIS 3.40.15 **failed one check**:
`qgis.layers_match_manifest` — manifest layers `roads` and `pois` name the *sources* `roads` and
`pois`, not an output or override, so they resolve to no produced file. The worked example gives
context layers pipeline-written copies (`main_roads_geojson`, `education_pois_geojson`), and
`project-spec.md` requires every QGIS datasource to come from the canonical run. Clean rerun, the
other eight `qgis.*` checks and the candidates all passed.

The contract that a map layer's `source` names an `outputs` key or an override layer is stated only
in the checker (`_manifest_layer_files`), not in shipped guidance, and it is checked only under
PyQGIS, although resolving it is static. So neither the agent's `validate` nor its `verify
--rerun` could report it. Material 001 therefore has no full-gate pass at the final template.

## Running future trials

Run no further paid trials for 0.4.0: the maintainer closed the live-trial gates on 2026-09-26.
All earlier authorizations are closed and their unspent balances are not carried forward. The
commands below are for future work such as #67, which needs fresh approval and a candidate frozen
after that fix. Executed projects (guidance injected; the agent runs sandboxed; the cap is per
trial):

```bash
python3 evals/run.py --mode live --agent claude_code --model claude-sonnet-4-6 \
  --collection --arms oms --case 001-basic-spatial-analysis \
  --max-budget-usd 4.5 --credential-file ~/.claude/.credentials.json --timeout 3600
```

Recent material 001 trials cost $3.40–4.34, and one was killed at 1800 s during a single long
write; keep the cap and timeout above those.

Native selection (Docker, digest-pinned image; the cap is split across the selected cases; see
[routing smoke tests](routing.md)):

```bash
python3 evals/run.py routing --agent claude_code --model claude-sonnet-4-6 \
  --image sha256:EXACT_LOCAL_IMAGE_ID --profile collection \
  --case bounded-discovery --case chosen-engine-sql --max-budget-usd 1 \
  --credential-file ~/.claude/.credentials.json --out evals/results/routing-EXACT_RUN_ID
```

Project trials here do not measure native activation on an executed task. Selection is judged by
the routing cases; review outcomes and actual execution separately as below.

## Remaining release gates

- **Completed fixes:** 001 discloses the artifact interface and runtime; GeoPackage comparison,
  portable QGIS datasources, layer/data CRS reconciliation and template-owned run pointers are
  implemented. Discovery guidance requires live verification. The remaining map-layer source
  contract defect is deferred under #67, not a 0.4.0 blocker.
- **Closed live-trial gates:** the maintainer's September 26 decision stands. At `9ef5c45`, 001
  passed its eval and clean rerun but failed independent QGIS layer reconciliation. Preserve that
  failure. Cases 070–073 remain parked in jaakla/OpenMapBench#7. No additional paid release trials.
- **Deferred comparison:** the maintainer explicitly moved the next plain-versus-skill comparison
  after 0.4.0. Run such comparisons occasionally with a separately agreed model and budget, not
  after small changes. Existing unscorable arms are not valid comparative evidence.
- **Reviews completed with limits:** the [final review](https://github.com/jaakla/openmapstack-skills/blob/v0.4.0/docs/release-0.4.0-final.md) assesses the
  compilation scaffold against its actual prompt and the four companion contexts against a
  validated control and pinned external text. Neither review claims executed compilation or
  observed live companion behavior; those evidence gaps remain visible under live-gate closure.
- **Hosted checks passed at `612e396`:** deterministic CI, plugin validation, real-source worked
  example, and full QGIS/browser fixture integration. Wheel/sdist, five snapshots and isolated
  hosted project/global/plugin installation checks passed. The shipped payload is byte-identical
  to `9ef5c45`; the intervening change is acceptance documentation only.
- **Explicit visual exception (#69):** direct browser review of the retained `9ef5c45` dashboard found
  missing declared UI and a 70-pixel mobile map. The automated checker also assumes controls
  are visible without opening tabs. This is separate from #67. The maintainer explicitly deferred
  it and authorized publication on September 26; never record these checks as passed.
- **Publication authorized:** commit the reconciled evidence, confirm
  CI on the final revision, publish through the release workflow, and verify the actual tag and
  PyPI installation. [Final preparation](https://github.com/jaakla/openmapstack-skills/blob/v0.4.0/docs/release-0.4.0-final.md) distinguishes prepublication
  artifact hashes from the distributions the publishing workflow will build.

OpenMapBench #2 remains open for generic live comparison, provider/telemetry parity and historical
evidence migration. The release gate does not wait on it.
