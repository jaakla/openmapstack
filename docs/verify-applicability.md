# `openmapstack verify` applicability

`verify` builds its check plan from `project.yaml`; users do not select checks
individually. This document defines which checks enter that plan, what each
one establishes, and why an applicable check may report `not_testable`.

The report uses these coverage fields:

- `applicable`: checks added to the plan because the manifest makes their
  predicate relevant;
- `executed`: applicable checks that established `passed`, `warning`, or
  `failed`;
- `not_testable`: applicable checks that could not establish their predicate;
- `execution_rate`: `executed / applicable`, or `null` for an empty plan.

An omitted check is not part of the denominator. A planned check that lacks a
dependency, supported artifact, or required declaration is `not_testable` and
remains in the denominator. A mixture of executed and `not_testable` checks
has aggregate status `warning`, never `passed`.

## Current plan

“Regression evidence” names representative eval cases. Direct unit tests also
exercise positive, negative, and unavailable-dependency paths for every public
checker where applicable.

| Check | Plan condition | Establishes | External dependency / `not_testable` causes | Regression evidence |
|---|---|---|---|---|
| `project.parses` | always | manifest is readable YAML | none; malformed/missing manifest fails | cases 001, 007, 070–073 |
| `project.conforms_to_schema` | always | instance conforms to packaged v1 schema | none | broad contract suite; mutations 901–921 |
| `project.graph_resolves` | always | processing symbols form a resolvable graph | none | 003, 903 |
| `project.one_canonical_pipeline` | always | one executable entrypoint is declared | missing declaration is a failure | 005 |
| `project.assumptions_have_rationale` | always | assumptions are explicit and reasoned | none | 001, 070–073 |
| `project.status_agrees_with_validation_report` | always | project status does not launder report state | missing report is `not_testable` | 004 |
| `project.parameters_match_steps` | `runtime.implementation.parameters` is declared | parameters are well-formed and agree with the processing steps they are bound to | none; malformed or drifting declarations fail | direct parameter-contract tests; case 015 |
| `project.declared_files_exist` | at least one output path is declared | all declared outputs exist | none; missing output fails | 001, 909 |
| `provenance.every_source_has_provider_and_access` | always | sources identify provider, method, and retrieval time | none | broad contract suite |
| `provenance.every_source_pinned` | always | every source is pinned by version identity, a hash-matched local snapshot, or an unexpired, accessible backend snapshot | none; a mutable alias is `source_unpinned`, a snapshot that cannot deliver its bytes again is `not_reproducible`, a malformed pin is `pin_invalid` | 906, 926; direct pin-class tests |
| `provenance.no_inline_credentials` | always | no source embeds a secret and `access.connection` is a reference | none | 927; direct credential-hygiene tests |
| `provenance.license_present_where_required` | always | every source has declared licence metadata | none | broad contract suite |
| `provenance.rationale_present` | always | source selection rationale is recorded | none | broad contract suite |
| `overrides.every_override_has_provenance` | always | declared overrides carry provenance | none; zero overrides is valid | 002, 003, 012, 013, 920, 921 |
| `overrides.evidence_not_placeholder` | always | override evidence is not a placeholder | none | 002, 003, 012, 920 |
| `validation.required_all_present` | always | required checks appear exactly once | missing report is `not_testable` | 001, 908 |
| `validation.no_implicit_pass` | always | every report check has an explicit four-state result | missing report is `not_testable` | 004, 908 |
| `validation.warning_or_failed_propagates_to_status` | always | report aggregate reflects non-passing checks | missing report is `not_testable` | 004, 071, 072 |
| `validation.run_record_matches` | always | report, manifest, inventories, hashes, and files agree | missing report is `not_testable`; missing or false records fail | 001; mutations 901–921 |
| `expectation.<id>` | once per `validation.expectations[]` entry | an independently attested known answer agrees with the produced artifact | unverified, incomplete, changed, or input-stale attestations warn without executing; allowlisted checks need DuckDB Spatial | direct attestation, staleness, path-safety, and checker-failure tests |
| `metamorphic.declarations_valid` | `validation.metamorphic` is declared | every relation parses, names an implemented relation, and addresses declared outputs | none; structural only, nothing executes | direct declaration tests; cases 015, 923–925 |
| `metamorphic.<id>` | `--metamorphic` and the relation is declared | the declared invariant holds under the relation's controlled perturbation | executes the canonical entrypoint in an isolated copy; unmet data preconditions, unsupported source/output formats, DuckDB absent for Parquet, timeouts, and oversize sources are `not_testable`; a crashing variant or one that mutates the project's inputs fails | 015 (holds); 923 `permutation_changed_output`, 924 `monotonicity_violated`, 925 `duplicates_changed_output` |
| `geodata.crs_not_used_for_metrics` | always | manifest does not declare geographic CRS for metric work | none | 001, 007, 902, 914 |
| `geodata.geometry_all_valid` | once per declared geodata output (`kind` undeclared or `geodata`) | every geometry in the artifact is present and valid; WKB is decoded for inspection | DuckDB Spatial; unsupported formats and unreadable artifacts are `not_testable`, missing files fail separately | 001, 011, 918 |
| `geodata.dataset_crs_is` | once per readable geodata output with declared EPSG | artifact CRS agrees with the manifest | DuckDB Spatial; absent declared EPSG/addressing and unreadable metadata are `not_testable`; readable geometry lacking actual CRS metadata fails, including bare WKB Parquet | 001, 007, 914, 919 |
| `tables.readable` | once per `kind: table` output | the file is a table with a header row and distinct column names | CSV and JSON records always run; XLSX and Parquet need DuckDB (XLSX also its `excel` extension), otherwise `not_testable`; missing or malformed files fail | direct table-check tests |
| `tables.not_empty` | once per `kind: table` output | the table has at least one data row | as `tables.readable` | direct table-check tests |
| `tables.columns_declared` | once per `kind: table` output | declared columns exist and their values match the declared type (plain numbers; ISO dates and date-times that exist on the calendar, a date-time with its time of day) | as `tables.readable`; an output that declares no columns fails | direct table-check tests |
| `tables.key_unique` | `kind: table` output declares `table.key` | key columns are never blank and never repeat | as `tables.readable` | direct table-check tests |
| `tables.downloads_consistent` | `kind: table` output declares `downloads` | every download format exists beside the output and holds the same columns and values | as `tables.readable`; a missing format fails | direct table-check tests |
| `presentation.tables_reference_table_outputs` | `presentation.tables` is declared | every table view shows a `kind: table` output and offers only its declared downloads | none | direct presentation tests |
| `presentation.table_downloads_linked` | `presentation.tables` is declared | the delivered dashboard links every declared download file | no dashboard HTML is `not_testable` | direct presentation tests |
| `presentation.layers_use_semantic_roles` | always | declared map layers carry semantic roles | no layers produces a warning | 001, 006 |
| `presentation.controls_match_pipeline` | always | canonical controls agree with processing expressions and overrides; variant switches open at one of their options and show declared columns | controls without an addressable matching step are currently outside the predicate | 001, 003, 006 |
| `presentation.edit_targets_reference_real_sources` | always | editing targets resolve to declared sources | none; no targets is valid | 001, 003, 006 |
| `visual.dashboard_loads_in_browser` | `presentation.design_language` is declared | every page in `views:` (or the dashboard) loads without errors and every declared control, layer group, panel and reset works through the state protocol of `design-language.md` s. 5, on whichever tab it sits; the mobile map keeps half the screen width | Playwright with Chromium (`openmapstack[visual]`); missing Playwright or browser, and remote scripts or styles that cannot be fetched from this machine, are `not_testable`; an unknown language version fails | direct state-protocol browser tests |
| `qgis.static_valid` | `project.qgz` exists | archive, document, and local datasource structure are valid; local datasources are relative paths | none | 001, 910, 913 |
| `qgis.datasources_portable` | `project.qgz` exists | local file datasources use an allowlisted format every QGIS build reads (GeoPackage, GeoJSON, FlatGeobuf, Shapefile, GeoTIFF) | other formats warn (`datasource_format_not_portable`); remote/provider datasources are out of scope; malformed QGIS document fails | 001 |
| `qgis.styles_declared` | `project.qgz` exists | vector layers declare renderers/styles | malformed QGIS document fails | 001, 006, 912 |
| `qgis.groups_match_manifest` | `project.qgz` exists | QGIS layer groups match manifest groups | malformed QGIS document fails | 001, 006 |
| `qgis.every_layer_declares_crs` | `project.qgz` exists | every QGIS layer declares CRS | malformed QGIS document fails | 001, 006 |
| `qgis.layer_crs_matches_data` | `project.qgz` exists | each local GeoJSON/GeoPackage layer declares the CRS its data is in (GeoJSON without `crs` is EPSG:4326) | no comparable layer is `not_testable`; malformed QGIS document fails | 001 |
| `qgis.runtime_load` | `project.qgz` exists | QGIS opens the project and reports valid layers | system PyQGIS; missing PyQGIS is `not_testable` | 001, 006 |
| `qgis.layers_match_manifest` | `project.qgz` exists | QGIS layers correspond to declared presentation sources | malformed QGIS document fails | 001, 006 |
| `rerun.no_chat_dependency` | always | declared implementation files do not depend on chat/transcript state | missing canonical dependencies are `not_testable` | 005 |
| `rerun.clean_execution_succeeded` | `--rerun` | canonical pipeline succeeds and post-run validation passes in an empty workspace | unstartable or failed execution fails; absent runner evidence is `not_testable` | 005 |
| `rerun.outputs_semantically_equal` | `--rerun` and at least one readable geodata output | normalized outputs are equivalent across runs (GeoJSON/JSON, Parquet, GeoPackage without its write timestamp; other formats by bytes) | DuckDB Spatial for Parquet; normalization failures are `not_testable` | 005 |
| `rerun.tables_semantically_equal` | `--rerun` and at least one `kind: table` output | each table and every declared download holds the same columns and rows across runs, compared by value in any row order | as `tables.readable`; unreadable tables are `not_testable` | direct table-check tests |
| `rerun.validation_report_reproducible` | `--rerun` | validation evidence reproduces after nondeterministic fields are removed | missing report in either run is `not_testable` | 005 |
| `overrides.source_files_byte_identical` | `--rerun` and immutable source/override files exist | clean rerun did not mutate declared inputs | missing files/workspace are `not_testable` | 001, 002, 005, 911 |

## Deliberate exclusions from the automatic plan

The shipped library has additional assertions used by fixture cases. They do
not automatically enter user-project verification until the manifest can
address them without guessing:

- known-answer checks enter the plan only through an independently attested
  `validation.expectations[]` entry; arbitrary checker names are rejected;
- duplicate/null checks require a declared entity key and nullability
  contract;
- API completeness and semantic-predicate checks require a named source and
  backend-specific evidence;
- cross-project presentation consistency requires an explicit comparison
  project;
- browser behaviour of a project that declares no `presentation.design_language`
  stays out of the plan: the pre-language check assumes one DOM shape and would
  fail working dashboards for markup alone (ADR 0007); cartographic rendering is
  not in the plan either;
- validation evidence recomputation requires machine-readable declarations
  mapping report fields to artifacts and metrics;
- clean-rerun checks run only when the user supplies `--rerun`, and declared
  metamorphic relations execute only with `--metamorphic`; both rerun the
  pipeline, which the static plan must never do implicitly.

These omissions are reachability gaps, not implicit passes. They should enter
the plan only with a versioned addressing contract and their own mutation
coverage.

Geometry evidence recomputation resolves the actual geometry column rather than assuming `geom`.
An `invalid_geometry_count` declaration may provide `geometry_field` when needed to disambiguate;
null geometries count as invalid. GeoParquet CRS-qualified columns work with nonstandard names,
and typed CRS metadata is checked even for empty datasets. Missing Spatial remains `not_testable`.
