# Northstar Mobility: private-database fixture design

Design and fixture contract for the private-database worked example
(`examples/nyc-private-mobility/`) and its deterministic eval fixture
(`evals/fixtures/mini-private-mobility/`). Motivated by
[openmapstack-skills#43](https://github.com/jaakla/openmapstack-skills/issues/43).

This document is navigation and rationale. Executable contracts live in the
files it points to.

## Scenario

One fictional company, **Northstar Mobility — NYC Operations Hub Analysis**.
Canonical question: identify promising NYC areas for a new operations/charging
hub for tenant `alpha` from historical trip demand, current fleet and hub
coverage, and market/POI context, while respecting all database access
restrictions. All synthetic business attributes are deterministic from the
checked-in seed `20260917`; no output may imply real NYC taxi records belong
to the fictional company.

Three backends play three enterprise roles:

| Backend | Role | Fixture database |
|---|---|---|
| BigQuery | historical analytical warehouse | `northstar_analytics` |
| PostGIS | operational GIS | `northstar_ops` |
| MotherDuck | private market-intelligence overlay | `northstar_market` |

## Core architecture rules

1. **Live warehouses are for read-only discovery and explicitly approved
   snapshotting only.** The accepted, reproducible analysis always runs from
   pinned local snapshots (`pin.class: local_snapshot`) under `data/source/`,
   exactly as `skills/open-map-stack/references/user-data-sources.md`
   requires. Backend time travel is never the long-term reproducibility
   mechanism.
2. **Provisioning identity ≠ analysis identity.** `provision.py` uses an
   admin credential; `project.yaml` always references the restricted reader
   (`env:OMS_DEMO_POSTGIS_DSN`, `env:GOOGLE_APPLICATION_CREDENTIALS`,
   `env:MOTHERDUCK_TOKEN`). Credentials never appear in `project.yaml`,
   snapshots, logs, errors, or committed artifacts — enforced by
   `openmapstack.sources.find_inline_credentials` and connector redaction.
3. **Backend-enforced security is the fixture, not a nice-to-have.** RLS row
   policies, column grants/policy tags, and inaccessible relations must be
   created by the provisioning scripts and exercised by the tests; the
   connector must behave correctly *under* them, not around them.
4. **Scoring logic lives in `project.yaml`** as an explicitly recomputable
   formula over derived columns, never hidden in Python only.

Acceptance gate: `unset` all warehouse credentials, then
`openmapstack verify examples/nyc-private-mobility` must still pass from the
committed snapshots.

## Cross-backend data model

Two conceptual keys join everything:

- `tenant_id` — drives RLS and permission tests (`alpha` is the RLS-visible
  analysis tenant; `beta`/`gamma` exist to prove isolation);
- `taxi_zone_id` — stable integer join key; geometry stays attached for real
  spatial operations.

All row generators are deterministic: a checked-in seed or stable hashes of
public source keys. Never platform `random()` without `setseed`, never
wall-clock data in fixture rows.

## PostGIS fixture (`northstar_ops`)

Owned by `examples/nyc-private-mobility/setup/postgis/{schema,seed,security}.sql`.

- Schemas/tables: `ops.taxi_zones` (public-origin reference geometry),
  `ops.hubs`, `ops.fleet_positions`, `ops.service_areas`,
  `ops.customer_accounts`, `hr.driver_private`.
- Restricted reader role `oms_alpha_reader`: RLS policies confine
  tenant-bearing tables to `tenant_id = 'alpha'`; column grants on
  `ops.customer_accounts` omit `contact_name`, `contact_email`,
  `contract_value`; `hr.driver_private` has no grants at all.
- Expected reader behaviour: permitted-column SELECT succeeds, `SELECT *`
  fails on the column-granted table, protected-column SELECT fails,
  other-tenant rows are invisible, `hr.driver_private` is inaccessible.
- Integration tests: `tests/test_private_fixture_postgis.py` (live tests run
  when `OPENMAPSTACK_TEST_PRIVATE_POSTGIS_ADMIN_DSN` points at an ephemeral
  PostGIS; the connector contract tests in `tests/test_connectors.py` remain
  the fake-driver layer).

## BigQuery fixture (`northstar_analytics`)

Owned by `examples/nyc-private-mobility/setup/bigquery/*.sql`.

- Two datasets, because the inaccessible path is part of the fixture:
  `northstar_analytics` (granted read-only to the reader) and
  `northstar_analytics_restricted` (`driver_costs`, granted to nobody).
- `taxi_zones` (public-origin reference geometry, no tenant column and no row
  access policy), then `trip_events`, `zone_daily_demand` and
  `vehicle_daily_metrics`, deterministic from
  `FARM_FINGERPRINT('northstar|20260917|' || label)`. The trip *shape* follows
  the public NYC dataset; every business attribute is synthetic.
- Row access policies confine the reader to `tenant_id = 'alpha'` on all
  three tenant-bearing tables.
- **Column-level security is deliberately optional.** Policy tags are Data
  Catalog objects with their own IAM, so the `ALTER COLUMN` statements live
  in `column-security.sql` and are applied only with `--policy-tag`.
  `provision.py verify` prints `NOT CONFIGURED` when they are absent — the
  one thing it must never do is report an unapplied restriction as a pass.
- A row access policy makes BigQuery withhold the dry-run byte estimate
  entirely, and makes the table un-truncatable even for its owner. Both bite
  in non-obvious ways and are written up in `debugging.md`.
- Connector `openmapstack/connectors/bigquery.py`: dry-run before every
  execution, `max_scan_bytes` refused pre-execution (`scan_limit_exceeded`)
  where an estimate exists and `scan_estimated: false` where it does not,
  `maximum_bytes_billed` on the executed job, `GEOGRAPHY` normalised to
  EPSG:4326 (`OGC:CRS84`) GeoParquet. `Table.num_rows` is reported as an
  estimate with a note; it ignores row access policies and must never be
  presented as an RLS-visible count.
- Tests: `tests/test_cloud_connectors.py` (fake client, records every job, so
  the dry-run-before-execute ordering is observable) and
  `tests/test_cloud_fixture_sql.py` (fixture SQL contract). Live behaviour is
  the canary's job, below.

## MotherDuck fixture (`northstar_market`)

Owned by `examples/nyc-private-mobility/setup/motherduck/*.sql`.

- `zone_market_scores`, `relevant_pois`, `analyst_annotations`: a shared
  POI-style source narrowed to a fixed NYC subset plus private enrichment —
  the "large shared source + private enrichment = private analytical dataset"
  enterprise pattern. Deterministic from
  `md5_number('northstar|20260917|' || label)`.
- Security is three independent claims, and they must not be conflated:
  a private database; the connector's `ATTACH ... (READ_ONLY)` session, which
  DuckDB enforces and which is **verified live** (an `INSERT` through it is
  refused); and a read-scoped token, which needs a higher MotherDuck plan
  tier and is reported `NOT CONFIGURED` when absent. Fine-grained table
  security remains an optional later profile, and `security.sql` says so
  rather than implying a boundary it does not create.
  `market.analyst_annotations` is a *convention*, not a grant, and
  `fixture.yaml` records it as `enforced_by: nothing`.
- Connector `openmapstack/connectors/motherduck.py` reuses the `md:`
  protocol; `LOAD`/`ATTACH`/`USE`/token setup is connector-controlled and
  happens before any analysis SQL exists. The database is attached
  `READ_ONLY` where the build supports it, and the discovery notes say so
  when it cannot be. A MotherDuck session needs network access, so
  `enable_external_access = false` is not available — stated, not hidden.
- **The fixture SQL is plain DuckDB SQL on purpose.** That is what lets
  `tests/test_cloud_fixture_sql.py` *execute* schema/seed/security against a
  local in-memory catalog on every PR, with no account and no network.

## Mini deterministic fixture (`evals/fixtures/mini-private-mobility/`)

Runs on ordinary CI with no credentials and no network. Regenerate with
`python gen.py` (idempotent, seed `20260917`); committed outputs are the
contract. File set:

| File | Content |
|---|---|
| `taxi-zones.geojson` | 8 zones, Polygon, EPSG:4326 |
| `trip-events.parquet` | 64 trips, two tenants, protected columns present |
| `fleet-positions.geojson` | 10 points, EPSG:2263 (deliberate mixed-CRS input), one NULL geometry |
| `hubs.geojson` | 2 existing hubs, tenant `alpha` |
| `pois.geojson` | 24 private-market POIs across categories |
| `market-scores.parquet` | per-zone market scores |
| `driver-private.parquet` | deliberately inaccessible relation (simulates `hr.driver_private`) |
| `access-matrix.yaml` | declares the simulated security model: visible tenant, protected columns, inaccessible relations |
| `expected.yaml` | exact hand-checkable values: visible row counts, accessible columns, candidate zone, aggregate demand, hub distances, POI counts, final score/ranking |

Deliberate hard cases baked in: two tenants (only `alpha` visible), protected
columns, one inaccessible relation, one mixed-CRS input, one NULL geometry,
one candidate zone with an unambiguous expected winner. `expected.yaml` values
are produced by the same generator that writes the data, so a later eval case
can recompute them independently and fail loudly on drift.

## Test layers (from issue #43)

- **A. Unit connector tests** — fake clients, no services (`tests/`).
- **B. Local PostGIS integration** — ephemeral PostGIS with the fixture SQL
  and RLS/column security; the mandatory live-DB leg (CI workflow
  `eval-warehouse.yml`).
- **C. Offline fixture evals** — committed mini snapshots, every PR, no
  credentials.
- **D. Optional live cloud integration** — BigQuery/MotherDuck only when
  secrets exist; PRs must not fail without them. Workflow
  `.github/workflows/cloud-canary.yml` (dispatch/weekly/release, never
  `pull_request`) runs `LiveBigQueryCanaryTests` and
  `LiveMotherDuckCanaryTests` and writes `not_testable` into the job summary
  for any backend whose secrets are absent.

## Status (issue #43 implementation sequence)

Done:

1. source/pin + credential-reference contract (`openmapstack/connectors/`);
2. PostGIS RLS/column-security integration;
3. `examples/nyc-private-mobility` skeleton on PostGIS + pinned snapshots;
4. BigQuery connector with the dry-run scan guard;
5. BigQuery demo provisioning and security;
6. MotherDuck connector and the private enrichment fixture;
8. cloud connector canary workflow;
9. `user-data-sources.md` updated with the verified capabilities *and* the
   limitations (metadata row counts, MotherDuck network access, BigQuery time
   travel as a refresh window rather than a pin).

7. the three-source canonical pipeline and presentation artifacts —
   `zone_demand` (BigQuery) and `zone_market` (MotherDuck) are captured from
   the real provisioned fixtures and pinned as `local_snapshot`, the scoring
   model reads all three backends, and `project.qgz` is generated by the
   pipeline rather than hand-authored so its datasources cannot drift from
   the outputs.

Not done, and why:

- **Step 10, the OpenMapBench hand-off.** Tracked separately with #45;
  `openmapstack api-info` and the connector surface are the released contract
  OpenMapBench builds on.

Two things the three-source stage left open, both outside this example:

- `openmapstack verify --rerun` failed at post-rerun artifact validation
  because the clean-rerun workspace did not copy `README.md`. Resolved in
  `openmapstack/rerun.py`: the rerun now carries `README.md` as
  documentation, not as an immutable input, and this example's rerun passes.
- Shipping `project.qgz` turns four PyQGIS checks `not_testable` wherever
  QGIS is absent, so `verify` reports WARNING rather than PASSED there. The
  Tartu example behaves the same way; it is the honest reading, not a
  regression.

## The QGIS project has two writers

`pipeline.py` writes `project.qgz` through **QGIS itself** when PyQGIS is
importable, and through a deterministic XML builder when it is not. The
pipeline must keep running wherever DuckDB does — the credential-free
acceptance gate and the fixture evals have no QGIS — but a hand-built file
should not be the authority on QGIS's own format when the real thing is
available.

`QGIS_LAYERS` is the single description both writers consume, and
`tests/test_cloud_fixture_sql.py::QgisProjectBuilderTests` asserts they agree
on layers, CRSs, datasources and renderers. Two writers for one artifact are
only safe while that holds.

**QGIS does not write reproducibly.** Measured against 3.40.15, a project
saved twice from identical inputs differs in six ways: `saveDateTime`; layer
ids (name + timestamp + UUID); one UUID per symbol layer; the annotation
layer's id; `Created`/`creation` metadata; a random attachment id for the
project style database; randomly coloured default symbols (the graduated
renderer's source symbol and all three elevation-profile symbols, none of
which this map draws); and attribute order, which Qt emits from a hash and
therefore varies between processes.

Ids and symbols are pinned where they are created; the rest is normalised on
the way out, including canonicalising the document with sorted attributes.
The result is byte-identical across runs **within one writer and one QGIS
version** — which is also what makes the two writers comparable as text.
