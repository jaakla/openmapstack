#!/usr/bin/env python3
"""Provision the Northstar Mobility private-database fixture.

Infrastructure and setup only — provisioning never becomes part of the
accepted analytical pipeline (`pipeline.py` is the single canonical
analysis implementation and runs exclusively from pinned local snapshots).

Commands:

    python provision.py postgis          # schema.sql -> seed.sql -> TLC point seed -> security.sql
    python provision.py augment-fleet    # add pinned TLC-derived demo points to existing PostGIS
    python provision.py bigquery         # + column-security.sql with --policy-tag
    python provision.py motherduck
    python provision.py all              # every backend whose credentials are set
    python provision.py verify           # check each boundary through the reader
    python provision.py destroy          # drop everything this fixture created

Credentials (admin/provisioning first, restricted analysis reader second):

    OMS_DEMO_POSTGIS_ADMIN_DSN            admin DSN used by provision/destroy
    OMS_DEMO_POSTGIS_READER_PASSWORD      password for the restricted reader
    OMS_DEMO_POSTGIS_DSN                  restricted reader DSN used by verify

    OMS_DEMO_BIGQUERY_ADMIN_CREDENTIALS   admin service-account key file
    OMS_DEMO_BIGQUERY_PROJECT             GCP project holding the fixture
    OMS_DEMO_BIGQUERY_READER_PRINCIPAL    e.g. serviceAccount:oms-alpha-reader@...
    GOOGLE_APPLICATION_CREDENTIALS        restricted reader key used by verify

    OMS_DEMO_MOTHERDUCK_ADMIN_TOKEN       admin token used by provision/destroy
    MOTHERDUCK_TOKEN                      read-scoped reader token used by verify

The admin identity must never be used by the OpenMapStack project itself;
`project.yaml` references only the restricted reader
(`env:OMS_DEMO_POSTGIS_DSN`, `env:GOOGLE_APPLICATION_CREDENTIALS`,
`env:MOTHERDUCK_TOKEN`).

`all` and `verify` never turn a missing credential into a pass: a backend
with no credentials configured is reported as skipped, and `verify` exits
non-zero only for a boundary that is actually wrong.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETUP = ROOT / "setup" / "postgis"
BIGQUERY_SETUP = ROOT / "setup" / "bigquery"
MOTHERDUCK_SETUP = ROOT / "setup" / "motherduck"
TLC_SAMPLE = ROOT / "data" / "source" / "tlc-2016-pickups.json"

READER_NAME_DEFAULT = "oms_alpha_reader"
BIGQUERY_DATASET_DEFAULT = "northstar_analytics"
BIGQUERY_RESTRICTED_DATASET_DEFAULT = "northstar_analytics_restricted"
BIGQUERY_LOCATION_DEFAULT = "US"
MOTHERDUCK_DATABASE_DEFAULT = "northstar_market"


def _connect(dsn: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "provision.py requires psycopg: pip install 'openmapstack[postgis]'"
        ) from exc
    return psycopg.connect(dsn)


def substitute_variables(sql: str, params: dict[str, str]) -> str:
    """Substitute ``:name``, ``:'name'`` and ``:"name"`` like psql ``-v``.

    String literals, ``--`` comments, and ``::`` casts are left untouched;
    quoted forms quote the value safely and an unknown variable is an error,
    never a silent empty string.
    """
    spans: list[str] = []

    def mask(match: re.Match) -> str:
        spans.append(match.group(0))
        return f"\x00{len(spans) - 1}\x00"

    def quoted(match: re.Match) -> str:
        name = match.group(1) or match.group(2)
        quote = "'" if match.group(1) else '"'
        if name not in params:
            raise SystemExit(
                f"missing psql variable for placeholder :{name} (set it with -v or the environment)"
            )
        return quote + params[name].replace(quote, quote * 2) + quote

    # Quoted psql variables first (before string masking can swallow them),
    # then mask literals/comments and substitute bare :name placeholders.
    unquoted = re.sub(r":'([A-Za-z_]\w*)'|:\"([A-Za-z_]\w*)\"", quoted, sql)
    masked = re.sub(r"'(?:[^']|'')*'|--[^\n]*", mask, unquoted)

    def replacement(match: re.Match) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        quote = "'" if match.group(1) else '"' if match.group(2) else ""
        if name not in params:
            raise SystemExit(
                f"missing psql variable for placeholder :{name} (set it with -v or the environment)"
            )
        value = params[name].replace(quote, quote * 2)
        return f"{quote}{value}{quote}"

    substituted = re.sub(
        r"(?<!:):'(\w+)'|(?<!:):\"(\w+)\"|(?<!:):(\w+)",
        replacement,
        masked,
    )
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], substituted)


def fixture_sql(path: Path) -> str:
    sql = path.read_text(encoding="utf-8")
    if "__H3_ZONES__" in sql:
        sql = sql.replace("__H3_ZONES__", (path.parent / "zones.sql").read_text(encoding="utf-8"))
    return sql


def fixture_zone_count() -> int:
    return len(json.loads((ROOT / "setup/h3/cells.json").read_text()))


def augment_fleet_from_tlc(connection) -> int:
    """Idempotently add synthetic alpha vehicles at pinned 2016 TLC pickups.

    The public rows are trip pickup coordinates. Vehicle IDs, statuses and
    2026 observation times are generated demo attributes, never TLC claims.
    The spatial join refuses points outside the provisioned H3 catalogue.
    """
    sample = json.loads(TLC_SAMPLE.read_text(encoding="utf-8"))
    if sample.get("schema") != "northstar-tlc-pickup-coordinate-seed/v1":
        raise ValueError("unrecognized TLC coordinate seed")
    records = sample["records"]
    if len(records) != sample["selected_rows"] or not records:
        raise ValueError("TLC coordinate seed count mismatch")
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE TEMP TABLE tlc_demo_points (
                sample_id integer PRIMARY KEY, h3_cell text NOT NULL,
                longitude double precision NOT NULL, latitude double precision NOT NULL
            ) ON COMMIT DROP
        """)
        with cursor.copy("COPY tlc_demo_points (sample_id,h3_cell,longitude,latitude) FROM STDIN") as copy:
            for index, row in enumerate(records, 1):
                copy.write_row((index, row["h3_cell"], row["longitude"], row["latitude"]))
        cursor.execute("""
            SELECT count(*) FROM tlc_demo_points p
            LEFT JOIN ops.taxi_zones z ON z.h3_cell = p.h3_cell
              AND ST_Contains(z.geom, ST_SetSRID(ST_Point(p.longitude,p.latitude),4326))
            WHERE z.zone_id IS NULL
        """)
        if cursor.fetchone()[0]:
            raise ValueError("TLC coordinate seed contains points outside the H3 grid")
        cursor.execute("""
            INSERT INTO ops.fleet_positions
                (tenant_id,vehicle_id,taxi_zone_id,status,recorded_at,geom)
            SELECT 'alpha', 'TLC16-DEMO-' || lpad(p.sample_id::text,4,'0'), z.zone_id,
                   (ARRAY['idle','on_trip','charging','maintenance'])[1 + p.sample_id % 4],
                   timestamptz '2026-09-01 09:00:00+00' + p.sample_id * interval '1 minute',
                   ST_SetSRID(ST_Point(p.longitude,p.latitude),4326)
            FROM tlc_demo_points p
            JOIN ops.taxi_zones z ON z.h3_cell = p.h3_cell
            WHERE NOT EXISTS (
                SELECT 1 FROM ops.fleet_positions existing
                WHERE existing.tenant_id = 'alpha'
                  AND existing.vehicle_id = 'TLC16-DEMO-' || lpad(p.sample_id::text,4,'0')
            )
        """)
        inserted = cursor.rowcount
        cursor.execute("""
            SELECT count(*), count(DISTINCT vehicle_id)
            FROM ops.fleet_positions
            WHERE tenant_id = 'alpha' AND vehicle_id LIKE 'TLC16-DEMO-%'
        """)
        total, unique = cursor.fetchone()
        if total != len(records) or unique != total:
            raise ValueError("TLC-derived fleet seed is incomplete or duplicated")
    connection.commit()
    return inserted


def _apply_sql_file(connection, path: Path, params: dict[str, str]) -> None:
    """Apply a SQL file with psql-compatible ``:var`` substitution."""
    sql = substitute_variables(fixture_sql(path), params)
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.commit()


# -- commands ------------------------------------------------------------------


def provision_postgis(admin_dsn: str, reader_name: str, reader_password: str) -> None:
    if not reader_password:
        raise SystemExit(
            "refusing to create the restricted reader without a password "
            "(--reader-password or OMS_DEMO_POSTGIS_READER_PASSWORD)"
        )
    params = {"reader_name": reader_name, "reader_password": reader_password}
    # Recreate the reader role deterministically. Grants and RLS policies
    # depend on the role, so they are cleared first (idempotent re-runs); the
    # tables themselves keep their deterministic data until seed.sql reruns.
    with _connect(admin_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE usename = %s AND pid <> pg_backend_pid()",
                (reader_name,),
            )
            for table, policy in (
                ("ops.hubs", "hubs_alpha_read"),
                ("ops.fleet_positions", "fleet_positions_alpha_read"),
                ("ops.service_areas", "service_areas_alpha_read"),
                ("ops.customer_accounts", "customer_accounts_alpha_read"),
            ):
                cursor.execute(f"SELECT to_regclass('{table}')")
                if cursor.fetchone()[0] is not None:
                    cursor.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
            for schema in ("ops", "hr"):
                cursor.execute("SELECT to_regnamespace(%s)", (schema,))
                if cursor.fetchone()[0] is not None:
                    cursor.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA "{schema}" FROM "{reader_name}"')
                    cursor.execute(f'REVOKE ALL ON SCHEMA "{schema}" FROM "{reader_name}"')
            cursor.execute(f'DROP ROLE IF EXISTS "{reader_name}"')
        connection.commit()
        for name in ("schema.sql", "seed.sql"):
            print(f"applying {name}")
            _apply_sql_file(connection, SETUP / name, params)
        print(f"added {augment_fleet_from_tlc(connection)} TLC-derived synthetic fleet observations")
        print("applying security.sql")
        _apply_sql_file(connection, SETUP / "security.sql", params)
    print("postgis fixture provisioned")


def verify_postgis(reader_dsn: str) -> int:
    """Sanity-check the security boundary through the restricted reader."""
    from openmapstack.connectors import ConnectorLimits
    from openmapstack.connectors.postgis import PostGISConnector

    connector = PostGISConnector(reader_dsn)
    discovery = connector.discover(ConnectorLimits(timeout_s=30.0))
    report = {
        "database": discovery.identity.get("database"),
        "read_only": discovery.read_only,
        "tables": [table.to_dict() for table in discovery.tables],
    }
    print(json.dumps(report, indent=2))

    connection = _connect(reader_dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM ops.taxi_zones")
            zones = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM ops.fleet_positions")
            fleet = cursor.fetchone()[0]
            cursor.execute("SELECT count(DISTINCT tenant_id) FROM ops.hubs")
            hub_tenants = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM ops.customer_accounts WHERE tenant_id <> 'alpha'")
            other_tenant_rows = cursor.fetchone()[0]
            failures = []
            for denied, label in (
                ("SELECT * FROM ops.customer_accounts LIMIT 1", "SELECT * on customer_accounts"),
                ("SELECT contact_email FROM ops.customer_accounts LIMIT 1", "contact_email column"),
                ("SELECT count(*) FROM hr.driver_private", "hr.driver_private"),
            ):
                try:
                    cursor.execute(denied)
                    failures.append(label)
                except Exception:
                    connection.rollback()
    finally:
        connection.close()
    checks = {
        "H3 catalogue complete": zones == fixture_zone_count(),
        "fleet_positions RLS-visible": fleet > 0,
        "hubs only tenant alpha": hub_tenants == 1,
        "no other-tenant rows visible": other_tenant_rows == 0,
        "protected columns and relations denied": not failures,
    }
    for label, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {label}")
    if failures:
        print(f"  unexpectedly readable: {failures}")
    if not all(checks.values()):
        print("verification FAILED")
        return 1
    print("verification passed")
    return 0


def destroy_postgis(admin_dsn: str, reader_name: str) -> None:
    with _connect(admin_dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA IF EXISTS ops CASCADE")
            cursor.execute("DROP SCHEMA IF EXISTS hr CASCADE")
            cursor.execute(f'DROP ROLE IF EXISTS "{reader_name}"')
        connection.commit()
    print("postgis fixture destroyed")


# -- BigQuery ------------------------------------------------------------------


def _bigquery_client(credentials: str, project: str, location: str):
    try:
        from google.cloud import bigquery
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "provision.py bigquery requires google-cloud-bigquery: "
            "pip install 'openmapstack[bigquery]'"
        ) from exc
    if credentials:
        return bigquery.Client.from_service_account_json(credentials, project=project, location=location)
    return bigquery.Client(project=project, location=location)


def _run_bigquery_script(client, path: Path, params: dict[str, str]) -> None:
    """Submit one setup file as a single BigQuery script job."""
    sql = substitute_variables(fixture_sql(path), params)
    print(f"applying {path.name}")
    client.query(sql).result()


def _bigquery_params(project: str, dataset: str, restricted: str, location: str, principal: str) -> dict[str, str]:
    return {
        "project": project,
        "dataset": dataset,
        "restricted_dataset": restricted,
        "location": location,
        "reader_principal": principal,
    }


def lock_out_bigquery_reader(
    admin_client,
    reader_credentials: str,
    project: str,
    dataset: str,
    principal: str,
    location: str,
    *,
    timeout_s: float = 120.0,
) -> None:
    """Revoke the reader's dataset grant and wait until it really is denied.

    Re-seeding drops the row access policies, and a dropped policy does not
    restrict an existing grant -- it removes the filter. A reader that still
    holds `dataViewer` at that moment reads every tenant's rows. So the grant
    goes first, and provisioning waits for it, because an IAM revoke is
    applied to the dataset policy at once but takes a moment to become
    effective for queries.

    Without reader credentials the wait cannot happen and provisioning says
    so rather than assuming: a window it cannot observe is not a window it
    can promise is closed.
    """
    admin_client.query(
        f"REVOKE `roles/bigquery.dataViewer` ON SCHEMA `{project}`.`{dataset}` FROM '{principal}'"
    ).result()
    print(f"revoked {principal} on {dataset}")
    if not reader_credentials:
        print(
            "WARNING  no reader credentials (GOOGLE_APPLICATION_CREDENTIALS), so the lock-out "
            "could not be confirmed before the row access policies are dropped; re-provision "
            "with them set to close that window"
        )
        return
    probe = _bigquery_client(reader_credentials, project, location)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            list(probe.query(f"SELECT 1 FROM `{project}`.`{dataset}`.trip_events LIMIT 1").result())
        except Exception:  # noqa: BLE001 - any refusal means the revoke is in force
            print("confirmed: the reader is locked out before the policies are dropped")
            return
        time.sleep(2.0)
    raise SystemExit(
        f"the reader still reads {dataset} {timeout_s:.0f}s after the revoke; refusing to drop "
        "the row access policies, which would expose every tenant's rows"
    )


def provision_bigquery(
    credentials: str,
    project: str,
    dataset: str,
    restricted: str,
    location: str,
    principal: str,
    policy_tag: str,
    reader_credentials: str = "",
) -> None:
    if not principal:
        raise SystemExit(
            "refusing to provision without the restricted reader's principal "
            "(--reader-principal or OMS_DEMO_BIGQUERY_READER_PRINCIPAL); the grants "
            "in security.sql are the fixture"
        )
    # A service-account principal from another project is accepted silently by
    # GRANT and only rejected much later, by CREATE ROW ACCESS POLICY, leaving
    # a half-provisioned fixture. Copying .env from the template and forgetting
    # this line is the easy way to get there, so it is caught up front.
    domain = f"@{project}.iam.gserviceaccount.com"
    if ".iam.gserviceaccount.com" in principal and not principal.endswith(domain):
        raise SystemExit(
            f"reader principal {principal!r} is not a service account in {project}; "
            "check OMS_DEMO_BIGQUERY_READER_PRINCIPAL"
        )
    client = _bigquery_client(credentials, project, location)
    params = _bigquery_params(project, dataset, restricted, location, principal)
    _run_bigquery_script(client, BIGQUERY_SETUP / "schema.sql", params)
    # Between here and security.sql the tables carry no row filter, so the
    # reader must not be able to reach them.
    lock_out_bigquery_reader(client, reader_credentials, project, dataset, principal, location)
    for name in ("seed.sql", "security.sql"):
        _run_bigquery_script(client, BIGQUERY_SETUP / name, params)
    if policy_tag:
        _run_bigquery_script(client, BIGQUERY_SETUP / "column-security.sql", {**params, "policy_tag": policy_tag})
        print("column-level security applied to internal_cost and rider_reference")
    else:
        # Saying this out loud is the point: a fixture that silently skipped
        # column security while reporting a clean boundary would be a lie.
        print(
            "NOTE  column-level security NOT applied: pass --policy-tag to apply "
            "column-security.sql; internal_cost and rider_reference stay readable"
        )
    print("bigquery fixture provisioned")


def verify_bigquery(credentials: str, project: str, dataset: str, restricted: str, location: str) -> tuple[dict, list[str]]:
    """Check the BigQuery boundary through the restricted reader."""
    sys.path.insert(0, str(ROOT.parents[1]))
    from openmapstack.connectors import ConnectorLimits
    from openmapstack.connectors.bigquery import BigQueryConnector

    connector = BigQueryConnector(credentials or "service=default", project=project, dataset=dataset, location=location)
    discovery = connector.discover(ConnectorLimits(timeout_s=60.0))
    print(json.dumps({"backend": "bigquery", "tables": [t.to_dict() for t in discovery.tables]}, indent=2))
    client = _bigquery_client(credentials, project, location)

    def _scalar(sql: str):
        return list(client.query(sql).result())[0][0]

    def _denied(sql: str) -> bool:
        try:
            list(client.query(sql).result())
        except Exception:
            return True
        return False

    tenants = [
        row[0]
        for row in client.query(f"SELECT DISTINCT tenant_id FROM `{project}`.`{dataset}`.trip_events").result()
    ]
    trips = _scalar(f"SELECT count(*) FROM `{project}`.`{dataset}`.trip_events")
    checks = {
        "trip_events readable": trips > 0,
        "row access policy shows only tenant alpha": tenants == ["alpha"],
        "restricted dataset inaccessible": _denied(
            f"SELECT count(*) FROM `{project}`.`{restricted}`.driver_costs"
        ),
    }
    unconfigured: list[str] = []
    if _denied(f"SELECT internal_cost FROM `{project}`.`{dataset}`.trip_events LIMIT 1"):
        checks["protected columns denied"] = True
    else:
        # not_testable, never a pass: the taxonomy simply is not there.
        unconfigured.append("column-level security (no policy tag applied; see column-security.sql)")
    return checks, unconfigured


def destroy_bigquery(credentials: str, project: str, dataset: str, restricted: str, location: str) -> None:
    client = _bigquery_client(credentials, project, location)
    for name in (dataset, restricted):
        client.query(f"DROP SCHEMA IF EXISTS `{project}`.`{name}` CASCADE").result()
        print(f"dropped dataset {name}")
    print("bigquery fixture destroyed")


# -- MotherDuck ----------------------------------------------------------------


def _motherduck_connect(token: str, database: str | None):
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "provision.py motherduck requires duckdb: pip install 'openmapstack[motherduck]'"
        ) from exc
    connection = duckdb.connect()
    # `motherduck_token` is registered by the extension, so it cannot be a
    # connect-time option: DuckDB answers "options were not recognized". Load
    # first, then SET, then ATTACH. Unlike the connector, provisioning may
    # install the extension -- it is a setup tool, and setup is where a
    # download belongs.
    connection.execute("INSTALL motherduck")
    connection.execute("LOAD motherduck")
    connection.execute("SET motherduck_token = '" + token.replace("'", "''") + "'")
    if database:
        connection.execute(f"ATTACH 'md:{database}'")
        connection.execute(f"USE {database}")
    return connection


def provision_motherduck(admin_token: str, database: str) -> None:
    connection = _motherduck_connect(admin_token, None)
    try:
        connection.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
        connection.execute(f"USE {database}")
        try:
            connection.execute("LOAD spatial")
        except Exception:  # pragma: no cover - build dependent
            raise SystemExit("the motherduck fixture needs DuckDB Spatial (geometry columns in schema.sql)")
        for name in ("schema.sql", "seed.sql", "security.sql"):
            print(f"applying {name}")
            connection.execute(fixture_sql(MOTHERDUCK_SETUP / name))
    finally:
        connection.close()
    print("motherduck fixture provisioned")
    print(
        "NOTE  the read-only boundary is the token, not SQL: create a read-scoped "
        f"token for {database} in MotherDuck and give the project that one as "
        "MOTHERDUCK_TOKEN -- never this admin token"
    )


def verify_motherduck(reader_token: str, database: str) -> tuple[dict, list[str]]:
    """Check the MotherDuck boundary through the reader's own token."""
    sys.path.insert(0, str(ROOT.parents[1]))
    from openmapstack.connectors import ConnectorLimits
    from openmapstack.connectors.motherduck import MotherDuckConnector

    connector = MotherDuckConnector(reader_token, database=database)
    discovery = connector.discover(ConnectorLimits(timeout_s=60.0))
    print(json.dumps({"backend": "motherduck", "tables": [t.to_dict() for t in discovery.tables]}, indent=2))
    by_name = {table.name: table for table in discovery.tables}

    # Two different questions, and conflating them would misreport both:
    #   1. can the connector's own session write?  -- the boundary in use;
    #   2. can the raw token write?                -- defence in depth.
    # A zero-row INSERT is the least invasive probe there is: it needs write
    # permission but changes nothing if it is allowed.
    probe = (
        "INSERT INTO market.analyst_annotations "
        "SELECT * FROM market.analyst_annotations WHERE false"
    )

    def _can_write(connection) -> bool:
        try:
            connection.execute(probe)
            return True
        except Exception:
            return False

    session = connector._session()
    try:
        zones = session.execute("SELECT count(*) FROM market.zone_market_scores").fetchone()[0]
        visible_zones, distinct_zones = session.execute(
            "SELECT count(*), count(DISTINCT taxi_zone_id) FROM market.analysis_zone_scores"
        ).fetchone()
        pois = session.execute("SELECT count(*) FROM market.relevant_pois").fetchone()[0]
        session_writable = _can_write(session)
    finally:
        session.close()

    raw = _motherduck_connect(reader_token, database)
    try:
        token_writable = _can_write(raw)
    finally:
        raw.close()

    checks = {
        "analysis scores cover each source market zone once": zones > 0 and visible_zones == zones == distinct_zones,
        "relevant_pois complete (240)": pois == 240,
        "analysis views present": "analysis_zone_poi_counts" in by_name,
        "connector session refuses writes": not session_writable,
    }
    unconfigured: list[str] = []
    if not discovery.read_only:
        unconfigured.append("READ_ONLY attach (this DuckDB build could not attach the database read-only)")
    if token_writable:
        unconfigured.append(
            "read-scoped analysis token: this token can write when used outside the connector "
            "(MotherDuck read-scoped tokens need a higher plan tier). The connector's own "
            "session is still read-only, but the identity-layer boundary is absent"
        )
    return checks, unconfigured


def destroy_motherduck(admin_token: str, database: str) -> None:
    connection = _motherduck_connect(admin_token, None)
    try:
        connection.execute(f"DROP DATABASE IF EXISTS {database}")
    finally:
        connection.close()
    print(f"motherduck fixture destroyed (database {database})")


# -- reporting -----------------------------------------------------------------


def report(backend: str, checks: dict, unconfigured: list[str]) -> bool:
    print(f"{backend}:")
    for label, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}: {label}")
    for label in unconfigured:
        print(f"  NOT CONFIGURED: {label}")
    return all(checks.values())


def _verdict(results: list[tuple[str, bool]], unconfigured_total: int) -> str:
    """Never print a bare "passed" while a declared restriction is unapplied.

    An unconfigured restriction is not a failure of the connector, but it is
    also not evidence that the boundary holds, and a summary that hid it would
    be exactly the green-by-omission this fixture exists to argue against.
    """
    names = ", ".join(name for name, _ in results)
    if unconfigured_total:
        return (
            f"verification passed for: {names} -- with {unconfigured_total} declared "
            "restriction(s) NOT CONFIGURED (see above); those boundaries are not in place"
        )
    return f"verification passed for: {names}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["postgis", "augment-fleet", "bigquery", "motherduck", "all", "verify", "destroy"])
    parser.add_argument("--admin-dsn", default=None, help="defaults to OMS_DEMO_POSTGIS_ADMIN_DSN")
    parser.add_argument("--reader-dsn", default=None, help="defaults to OMS_DEMO_POSTGIS_DSN")
    parser.add_argument("--reader-name", default=READER_NAME_DEFAULT)
    parser.add_argument(
        "--reader-password",
        default=None,
        help="defaults to OMS_DEMO_POSTGIS_READER_PASSWORD; never committed or logged",
    )
    parser.add_argument("--bigquery-credentials", default=None, help="defaults to OMS_DEMO_BIGQUERY_ADMIN_CREDENTIALS")
    parser.add_argument("--bigquery-project", default=None, help="defaults to OMS_DEMO_BIGQUERY_PROJECT")
    parser.add_argument("--bigquery-dataset", default=BIGQUERY_DATASET_DEFAULT)
    parser.add_argument("--bigquery-restricted-dataset", default=BIGQUERY_RESTRICTED_DATASET_DEFAULT)
    parser.add_argument("--bigquery-location", default=BIGQUERY_LOCATION_DEFAULT)
    parser.add_argument(
        "--reader-principal",
        default=None,
        help="BigQuery grantee, e.g. serviceAccount:oms-alpha-reader@PROJECT.iam.gserviceaccount.com; "
        "defaults to OMS_DEMO_BIGQUERY_READER_PRINCIPAL",
    )
    parser.add_argument(
        "--policy-tag",
        default=None,
        help="Data Catalog policy tag for column-level security; without it the protected columns stay readable",
    )
    parser.add_argument("--motherduck-token", default=None, help="admin token; defaults to OMS_DEMO_MOTHERDUCK_ADMIN_TOKEN")
    parser.add_argument("--motherduck-database", default=MOTHERDUCK_DATABASE_DEFAULT)
    args = parser.parse_args(argv)

    admin_dsn = args.admin_dsn or os.environ.get("OMS_DEMO_POSTGIS_ADMIN_DSN", "")
    reader_dsn = args.reader_dsn or os.environ.get("OMS_DEMO_POSTGIS_DSN", "")
    reader_password = args.reader_password or os.environ.get("OMS_DEMO_POSTGIS_READER_PASSWORD", "")

    bigquery_credentials = args.bigquery_credentials or os.environ.get("OMS_DEMO_BIGQUERY_ADMIN_CREDENTIALS", "")
    bigquery_reader_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    bigquery_project = args.bigquery_project or os.environ.get("OMS_DEMO_BIGQUERY_PROJECT", "")
    reader_principal = args.reader_principal or os.environ.get("OMS_DEMO_BIGQUERY_READER_PRINCIPAL", "")
    policy_tag = args.policy_tag or os.environ.get("OMS_DEMO_BIGQUERY_POLICY_TAG", "")

    motherduck_admin = args.motherduck_token or os.environ.get("OMS_DEMO_MOTHERDUCK_ADMIN_TOKEN", "")
    motherduck_reader = os.environ.get("MOTHERDUCK_TOKEN", "")

    def _provision_bigquery() -> None:
        if not bigquery_project:
            raise SystemExit("a BigQuery project is required: --bigquery-project or OMS_DEMO_BIGQUERY_PROJECT")
        provision_bigquery(
            bigquery_credentials,
            bigquery_project,
            args.bigquery_dataset,
            args.bigquery_restricted_dataset,
            args.bigquery_location,
            reader_principal,
            policy_tag,
            bigquery_reader_credentials,
        )

    def _provision_motherduck() -> None:
        if not motherduck_admin:
            raise SystemExit("an admin token is required: --motherduck-token or OMS_DEMO_MOTHERDUCK_ADMIN_TOKEN")
        provision_motherduck(motherduck_admin, args.motherduck_database)

    if args.command == "postgis":
        if not admin_dsn:
            raise SystemExit("an admin DSN is required: --admin-dsn or OMS_DEMO_POSTGIS_ADMIN_DSN")
        provision_postgis(admin_dsn, args.reader_name, reader_password)
        return 0
    if args.command == "augment-fleet":
        if not admin_dsn:
            raise SystemExit("an admin DSN is required: --admin-dsn or OMS_DEMO_POSTGIS_ADMIN_DSN")
        with _connect(admin_dsn) as connection:
            inserted = augment_fleet_from_tlc(connection)
        print(f"added {inserted} TLC-derived synthetic fleet observations")
        return 0
    if args.command == "bigquery":
        _provision_bigquery()
        return 0
    if args.command == "motherduck":
        _provision_motherduck()
        return 0

    if args.command == "all":
        # A backend with no credentials is skipped and said to be skipped --
        # `all` must not look like it provisioned something it never touched.
        done, skipped = [], []
        for name, configured, run in (
            ("postgis", bool(admin_dsn), lambda: provision_postgis(admin_dsn, args.reader_name, reader_password)),
            ("bigquery", bool(bigquery_project), _provision_bigquery),
            ("motherduck", bool(motherduck_admin), _provision_motherduck),
        ):
            if not configured:
                skipped.append(name)
                continue
            run()
            done.append(name)
        print(f"provisioned: {', '.join(done) or 'nothing'}")
        if skipped:
            print(f"SKIPPED (no credentials configured): {', '.join(skipped)}")
        return 0 if done else 1

    if args.command == "verify":
        results, skipped = [], []
        unconfigured_total = 0
        if reader_dsn:
            results.append(("postgis", verify_postgis(reader_dsn) == 0))
        else:
            skipped.append("postgis")
        if bigquery_project:
            checks, unconfigured = verify_bigquery(
                bigquery_reader_credentials,
                bigquery_project,
                args.bigquery_dataset,
                args.bigquery_restricted_dataset,
                args.bigquery_location,
            )
            results.append(("bigquery", report("bigquery", checks, unconfigured)))
            unconfigured_total += len(unconfigured)
        else:
            skipped.append("bigquery")
        if motherduck_reader:
            checks, unconfigured = verify_motherduck(motherduck_reader, args.motherduck_database)
            results.append(("motherduck", report("motherduck", checks, unconfigured)))
            unconfigured_total += len(unconfigured)
        else:
            skipped.append("motherduck")
        if skipped:
            print(f"NOT VERIFIED (no reader credentials configured): {', '.join(skipped)}")
        failed = [name for name, ok in results if not ok]
        if not results:
            print("verification could not run: no reader credentials configured")
            return 1
        if failed:
            print(f"verification FAILED for: {', '.join(failed)}")
            return 1
        print(_verdict(results, unconfigured_total))
        return 0

    if args.command == "destroy":
        destroyed, skipped = [], []
        if admin_dsn:
            destroy_postgis(admin_dsn, args.reader_name)
            destroyed.append("postgis")
        else:
            skipped.append("postgis")
        if bigquery_project:
            destroy_bigquery(
                bigquery_credentials,
                bigquery_project,
                args.bigquery_dataset,
                args.bigquery_restricted_dataset,
                args.bigquery_location,
            )
            destroyed.append("bigquery")
        else:
            skipped.append("bigquery")
        if motherduck_admin:
            destroy_motherduck(motherduck_admin, args.motherduck_database)
            destroyed.append("motherduck")
        else:
            skipped.append("motherduck")
        print(f"destroyed: {', '.join(destroyed) or 'nothing'}")
        if skipped:
            print(f"SKIPPED (no admin credentials configured): {', '.join(skipped)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
