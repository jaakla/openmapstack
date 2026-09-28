# Maintainer debugging notes

These are recurring, non-obvious traps observed in the current architecture. Prefer fixing the root cause and adding regression coverage; keep a note only when the diagnostic context is still useful across issues and tools.

## Start by identifying the evidence layer

A failure often looks contradictory because different commands answer different questions.

- `openmapstack validate` — manifest/bookkeeping and, without `--preflight`, produced-artifact/report/run-record consistency.
- `openmapstack verify` — reusable evidence checks against real artifacts; may report `not_testable` when the environment cannot establish a predicate.
- `python evals/run.py --mode fixture` — deterministic reference/mutation suite.
- `python evals/run.py --mode visual` — QGIS/browser integration in a capable environment.
- `python evals/run.py --mode live ...` — stochastic agent execution; setup/agent failures are not assertion results.

Before changing a checker because one layer is green and another red, confirm whether both were expected to inspect the same property.

## `--preflight` can hide a broken worked example

`openmapstack validate ... --preflight` deliberately skips checks that need produced artifacts, validation reports, and run records. It is useful before a project has run; it is not full health evidence.

In a clean checkout, a gitignored `local_snapshot` source file is absent. Preflight reports its `source.pin` as `not_testable` so CI can still check committed inputs; full validation fails until the source is fetched. A present file with the wrong hash fails in either mode (see `openmapstack/validation.py` and `tests/test_sources.py`).

Useful comparison:

```bash
openmapstack validate examples/tartu-development/project.yaml --preflight
openmapstack validate examples/tartu-development/project.yaml
openmapstack verify examples/tartu-development/project.yaml
```

## `not_testable` is evidence, not success

A check that cannot establish its predicate must not become a pass. Typical causes include:

- DuckDB Spatial unavailable or unable to read an artifact;
- no EPSG code/addressing for a CRS cross-check;
- PyQGIS unavailable for runtime QGIS checks;
- missing report/run evidence required by a checker.

`verify` reports applicability/execution coverage. A mixture of executed checks and `not_testable` checks aggregates to `warning`, not `passed`; if nothing applicable can execute, the result is `not_testable`.

When adding a check, include an unavailable-dependency path in tests where relevant. Catching an exception and returning `passed` is a regression; uncaught expected environmental failure is also wrong.

## DuckDB geometry columns are not always named `geom`

The fixture generator historically wrote `geom`, which hid a real-world bug: GeoPandas/GeoParquet commonly use `geometry`. The shipped geodata checks now resolve a typed GEOMETRY column first and then conventional names.

Do not reintroduce hardcoded `geom` assumptions in new generic checks. A checker intended for user projects should discover the geometry column or require explicit safe addressing.

## QGIS can be valid and still show the wrong map

Two failures have already escaped simpler validity checks:

1. **Missing or incomplete CRS in hand-written `.qgs` XML.** A Web-Mercator basemap with no `<srs>` may be interpreted in the project CRS and render ~1500 km from the analysis. An auth-id-only `<spatialrefsys>` is just as dangerous: `authid()` looks correct while QGIS treats the CRS as invalid and silently cannot transform or paint the layer. Prefer the PyQGIS API where possible. Static checks require full WKT/PROJ definitions and `ProjectionsEnabled`; real-QGIS checks require each manifest layer to change the rendered pixels.
2. **Layer-tree ordering.** MapLibre/web layers are conventionally declared bottom-to-top, while QGIS paints the first tree entry on top. Copying manifest order verbatim can bury point/line layers under opaque polygons. The generated QGIS tree needs the appropriate reverse paint order.

A nonblank render alone is insufficient. The visual suite includes layer-removal comparison and manifest reconciliation because a rendered image can be nonblank while a declared layer is absent.

## Visual mode expects its environment to be capable

The scheduled visual job runs in `qgis/qgis:3.44-trixie` and installs Playwright/Chromium. Assertions scoped to visual mode are hard gates there. Missing PyQGIS/browser dependencies in an ordinary local Python environment should not be mistaken for a visual regression; use the correct container/environment or interpret the explicit `not_testable` result.

Ordinary `.github/workflows/evals.yml` is **not** browser-free: its unit-test stage installs Playwright/Chromium and runs browser-assertion tests. The same workflow later has a distinct Docker fixture gate that runs with runtime networking disabled. Keep those environments separate when diagnosing failures; do not solve local visual dependency issues by making deterministic fixture grading depend on QGIS, live agents, or mutable external services.

## DuckDB Spatial is prepared explicitly for deterministic evals

Eval workflows call `evals/prepare_spatial.py` and set `OPENMAPSTACK_SPATIAL_EXTENSION_DIR` before running checks. The offline Docker gate then runs with network disabled.

If fixture geodata checks suddenly try to download DuckDB Spatial at runtime, inspect the controlled extension-directory setup before weakening the offline gate.

## Clean rerun intentionally removes some convenient ambient state

A clean rerun is meant to fail when the project only works because of the original agent session or undeclared machine state, but the current environment sanitization is a blacklist rather than a complete sandbox.

The protocol removes `PYTHONPATH` and variables whose names contain one of the current blacklist fragments: `ANTHROPIC`, `CHAT`, `CLAUDE`, `CODEX`, `CONVERSATION`, `OPENAI`, `PROMPT`, or `TRANSCRIPT`. It also rejects unsafe/escaping preserved paths and symlinked dependency trees and executes the canonical command without a shell. The rerun workspace itself preserves only the manifest, immutable sources/overrides, canonical implementation files, and declared dependencies.

Do not assume this proves independence from **all** ambient environment state. Variables such as `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and arbitrary service/configuration variables can currently survive. If a project unexpectedly passes only on one machine/provider environment, inspect surviving environment dependencies as well as copied project state. Moving the rerun process to an explicit environment allowlist would provide a stronger boundary.

If a project works in the original workspace but fails `verify --rerun`, first check whether a real runtime/config dependency is undeclared. Do not automatically copy more of the original workspace into the rerun; that can destroy the test's value.

Eval clean reruns additionally forbid calling the repository's reference generator. A project that reproduces itself by rerunning the oracle has not demonstrated independent reproducibility.

## Path comparisons must account for resolved roots

On macOS and some temporary-directory layouts, paths such as `/var/...` resolve through `/private/var/...`. Comparing a resolved child to an unresolved root with `Path.relative_to()` can therefore fail even when the path is genuinely inside the project.

Existing project/integrity code resolves the root before containment/relative comparisons. Reuse those helpers instead of recreating path-safety logic ad hoc.

## Integrity hashes include path identity

`canonical_file_set_hash` hashes both each sorted project-relative path and its bytes. This intentionally distinguishes inventories with identical concatenated bytes under different names.

When debugging a run-record mismatch, inspect both:

- which files are in the canonical input/output inventory; and
- the bytes of those files.

A renamed implementation/dependency is expected to change the canonical set hash even if file contents are unchanged.

## Live evals have stricter setup semantics than ordinary scripts

Live benchmark evidence is publishable only when the run identity is explicit. The runner requires an exact model identity and explicit skill arm (`enabled`/`disabled`) for live mode. Zero executed cases, missing agent CLI, adapter failure, generator failure, timeout, or malformed case configuration are setup failures (exit code 2), excluded from scored denominators.

Do not convert these into assertion failures just to keep a benchmark run green; setup failure and task failure answer different questions.

## Mutation tests require one isolated intended defect

A mutation case is valid only when its healthy control passes and the mutation produces the pinned intended failing assertion/code while isolation guards remain healthy. An unrelated broken setup or second defect should invalidate the mutation rather than count as successful detection.

When a plausible wrong project from a live/user run survives the current checks, the preferred feedback loop is:

1. minimize the defect;
2. add a healthy control and one deliberate break mode;
3. pin the intended failure code;
4. add checker/unit coverage if the defect exposes a checker bug;
5. only then add prose context here if the trap remains worth remembering.

## Metamorphic mutations must live in the pipeline copy, not only in the generator

A metamorphic relation reruns the project's own `pipeline.py` on perturbed input and compares with the produced outputs. For a fixture mutation that means the *copied* pipeline must reproduce the defect: if only `gen.py --break=` injects it at generation time, the variant run rebuilds the healthy analysis, every relation "fails" for the wrong reason, and the mutation is not isolated. `gen.py` therefore reads the `EVAL-BREAK` warning back in pipeline mode for the pipeline-logic break modes (`order_dependent`, `distance_inverted`, `duplicate_sensitive`) and nothing else.

Two related traps:

- a relation's detection power depends on the data and the variant size. Widening the mini-Tartu road threshold by 1.5× cannot expose an inverted predicate because the only far parcel sits at 5450 m; the fixture declares `variant: {multiply: 3}` for that reason. When a mutation survives, check the geometry before suspecting the relation;
- a GeoJSON output without a `crs` member reads back as EPSG:4326. A pipeline that writes analysis-CRS coordinates into plain GeoJSON and declares `EPSG:3301` in the manifest fails `geodata.dataset_crs_is` correctly. Write the `crs` member (or use GeoParquet) rather than relaxing the check.

## A red worked-example job is usually the world moving, not the branch

`.github/workflows/example.yml` regenerates `examples/tartu-development/` from
live Estonian services, so its failures split into two kinds that look
identical in the checks list:

- **the change broke the example** — reported by `validate`, `verify`, or the
  real-QGIS render step;
- **the sources moved** — reported only by the final artifact-currency step,
  which diffs the regenerated `project.qgz` against the committed one. The
  legend embeds facility counts (`Verified municipal schools (n=26)`), so one
  opening or closing in Tartu rewrites the file. It is advisory on a pull
  request and a hard failure on the schedule for exactly this reason.

Regenerating the example is what clears the second kind, and it must be done
with `pipeline.py --refresh`: `data/source/` is a cache with no expiry, so a
plain re-run can reproduce the committed artifacts exactly from stale local
data and look like it worked.

To tell the two kinds apart without guessing, read the job's uploaded
`worked-example-run` artifact rather than re-running anything: its run record inventories every
output by SHA-256 and its validation report carries feature counts, both
comparable with what the repository ships.

```bash
gh run download <run-id> -n worked-example-run -D /tmp/wx
```

Observed 2026-09-09 (issue #20's PR): the counts read 517 against a committed
518, seven of the eight inventoried outputs differed, and `project.qgz`
differed at an identical 3698 bytes — consistent with a same-length digit
substitution in one of those legend counts.

Note what that byte-identical size rules out. Comparing a `.qgz` compares a
deflate stream, so a zlib or QGIS change could in principle move the bytes with
the content unchanged. It was not the cause there and the container is
deterministic by construction — `write_qgis_project` pins the zip entry to a
1980 timestamp and fixed permissions — but a currency failure with *no*
accompanying digest drift in the run record is the signature to suspect.

## Generated benchmark artifacts are evidence, not source

Retained live/visual evidence belongs under `evals/results/<run-id>/...` and CI artifacts. Do not treat generated result JSON, screenshots, event streams, or temporary projects as canonical repository state unless a fixture intentionally owns them.

The normalized `agent.json` is vendor-neutral audit data; raw provider events are diagnostics. Assistant final-answer prose is never the correctness oracle.

## PostGIS hides column-granted tables from discovery

PostGIS's `geometry_columns` view filters relations by table-level
privilege. A reader holding only column-level grants (and row-level
security policies) on a table — exactly the private-fixture model in
`examples/nyc-private-mobility/setup/postgis/security.sql` — will not see
that table in `geometry_columns`, so `PostGISConnector.discover()` omits it
while snapshotting named columns still works. Do not "fix" this by granting
whole-table SELECT to satisfy a discovery assertion; the omission is the
backend telling the truth about the principal's grant shape (observed while
building issue #43's PostGIS fixture tests).

## DuckDB file confinement needs `enable_external_access = false`

`SET allowed_directories = [...]` on its own does **not** stop
`read_csv('/etc/passwd')`. It only takes effect together with
`SET enable_external_access = false`, which is why
`connectors/duckdb_local.py` sets both. Verified against DuckDB 1.5.5.

That pair is unavailable to any connector that needs the network, because
DuckDB refuses `SET enable_external_access = true` once a database is running
(`Cannot enable external access while database is running`). So the MotherDuck
connector has no session-level file confinement, and
`connectors.require_read_only_select` is the only thing refusing a query that
names a file or URL. Weakening that policy weakens MotherDuck's boundary, not
just its ergonomics.

`SET disabled_filesystems = 'LocalFileSystem'` *is* real, irreversible
enforcement (a later `RESET` is refused), and would close the gap — but it
also blocks `COPY … TO 'local.parquet'`, which is how the connector
materialises a snapshot. Using it would mean fetching rows through the `md:`
session and writing Parquet through a second local connection, the way
`connectors/postgis.py` already does. It has not been tried against a live
MotherDuck, where the client may need the local filesystem for its own cache;
that is the open question to answer before adopting it.

## A BigQuery row access policy suppresses the dry-run byte estimate

`total_bytes_processed` comes back as **`None`**, not a number and not zero,
for any query over a table carrying a row access policy — on the dry run *and*
on the executed job, for the table's owner as much as for a restricted reader.
BigQuery will not say how much data a query would read when some of that data
is filtered from the caller. Measured on the live `northstar_analytics`
fixture: `trip_events` (policy) → `None`, `driver_costs` (no policy) → `8077`.

The consequence is easy to get wrong, and shipped wrong once: reading `None`
as `0` leaves `max_scan_bytes` silently disabled on exactly the tables a
private fixture exists to protect. `_guarded_dry_run` returns `None` instead,
`QueryPlan.scan_estimated` records that the check could not run, and the
executed job's `maximum_bytes_billed` — which the service enforces — is what
bounds the cost. Unknown is not free.

`taxi_zones` exists in the BigQuery fixture partly for this: it is the one
analysis-dataset table with no row access policy, so it is the only one whose
dry run returns a number, which is what the live canary needs to prove the
guard still bites.

## Re-seeding a BigQuery table under a row access policy fails

`TRUNCATE TABLE` returns `User does not have full access for table … due to
row access policies` — for the admin that created both the table and the
policy. So `seed.sql` is not idempotent once `security.sql` has run, and a
second `provision.py bigquery` fails on the first truncate.

`seed.sql` therefore issues `DROP ALL ROW ACCESS POLICIES` for each
tenant-bearing table before truncating, and `security.sql` re-creates them in
the step straight after. Dropping when none exist is a no-op, so a first run
is unaffected. The PostGIS path clears its policies in `provision.py` for the
same reason.

## `motherduck_token` cannot be a DuckDB connect-time option

`duckdb.connect(config={"motherduck_token": ...})` fails with
`The following options were not recognized: motherduck_token`. The option is
registered *by* the MotherDuck extension, and connect-time config is validated
before any extension loads. The working order is:

```sql
LOAD motherduck;                       -- INSTALL first, in provisioning only
SET motherduck_token = '<token>';
ATTACH 'md:<database>' AS warehouse (READ_ONLY);
USE warehouse;
```

`SET` takes no bound parameter, so the token is quote-escaped into the
statement and every error from it is wrapped in a `ConnectorError`, whose
message is redacted — a driver error that echoes the statement would otherwise
carry the token.

Also verified against live MotherDuck: the `READ_ONLY` attach is accepted and
is **really enforced** — an `INSERT` through that session fails with
`Cannot execute statement of type "INSERT" on database "warehouse" which is
attached in read-only mode`. That matters because read-scoped *tokens* need a
higher plan tier, so on most accounts the attach is the only read-only layer
actually in force.

## Dropping a BigQuery row access policy widens access, it does not narrow it

Re-seeding the fixture drops the row access policies, because a table under
one cannot be truncated. The trap is what that does to a reader who already
holds `roles/bigquery.dataViewer` from a previous run: the policy was the
*filter*, so removing it does not lock the reader out — it hands them every
tenant's rows. Measured on the live fixture: 4800 rows across 3 tenants where
the filtered reader sees 2917 from tenant alpha.

The sequence therefore fails closed, and the ordering is the security
property:

1. `provision.py` revokes the reader's dataset grant **and waits until the
   reader is actually denied**;
2. `seed.sql` drops the policies and reloads the data;
3. `security.sql` re-creates every policy and grants the reader **last**.

At no point does the reader hold an unfiltered grant, and a run that aborts
part way leaves the fixture locked rather than open.

The revoke cannot live in `seed.sql`: it and the drops would be a single
BigQuery job, and a revoke is applied to the dataset policy immediately but
takes a moment to become effective for queries, so there would be nothing to
wait on. Without reader credentials provisioning cannot confirm the lock-out
and says so rather than assuming it.

One more sharp edge on the same path: a `reader_principal` naming a service
account in a *different* project is accepted silently by `GRANT` and rejected
much later by `CREATE ROW ACCESS POLICY` (`Service account ... does not
exist`), leaving a half-provisioned fixture. Copying `.env.example` and
forgetting that line is the easy way there, so `provision.py` now checks it up
front.
