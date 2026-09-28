"""PostGIS private-fixture security tests (examples/nyc-private-mobility).

Two layers, mirroring the connector test layout:

* Static contract tests run everywhere: the fixture SQL must declare the
  security boundary (RLS policies, column grants that withhold the protected
  columns, an inaccessible hr schema), no credentials may be committed, and
  the mini fixture's expected values must match its committed data.
* Live tests (``OPENMAPSTACK_TEST_PRIVATE_POSTGIS_ADMIN_DSN`` set) apply the
  fixture SQL to a real PostGIS and prove the enforced behaviour through the
  restricted reader, including the OpenMapStack connector's conduct under
  RLS, column denial, and inaccessible relations.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml

from openmapstack.checks.spatial import connect_spatial
from openmapstack.connectors import (
    ConnectorError,
    ConnectorLimits,
    apply_snapshot_to_manifest,
    discover_source,
    snapshot_source,
)
from openmapstack.sources import assess_pin
from tests.evals.helpers import make_workspace, minimal_project, write_project

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "nyc-private-mobility"
SETUP = EXAMPLE / "setup" / "postgis"
MINI = REPO_ROOT / "evals" / "fixtures" / "mini-private-mobility"
DUCKDB_AVAILABLE = connect_spatial() is not None
ADMIN_DSN = os.environ.get("OPENMAPSTACK_TEST_PRIVATE_POSTGIS_ADMIN_DSN", "")
READER_DSN = os.environ.get("OPENMAPSTACK_TEST_PRIVATE_POSTGIS_READER_DSN", "")
READER_NAME = "oms_alpha_reader"

EXPECTED_COLUMNS = [
    "hub_id", "tenant_id", "taxi_zone_id", "name", "capacity", "activated_on", "geom",
]
ACCOUNTS_GRANTED_COLUMNS = ["account_id", "tenant_id", "taxi_zone_id", "account_name", "is_active"]
ACCOUNTS_PROTECTED_COLUMNS = ["contact_name", "contact_email", "contract_value"]


def _postgis_project(key: str, table: str):
    project = minimal_project()
    project["sources"] = {
        key: {
            "provider": "Northstar Mobility fixture",
            "dataset": table,
            "source_url": "https://github.com/jaakla/openmapstack-skills/issues/43",
            "access": {"method": "postgis", "connection": "env:PRIVATE_FIXTURE_DSN", "retrieved_at": "2026-09-17T00:00:00Z"},
            "version": {"identifier": f"northstar_ops.ops.{table}", "published_at": "2026-09-17"},
            "warehouse": {"backend": "postgis", "schema": "ops", "table": table},
            "license": {"name": "Synthetic fixture data (CC0-1.0)", "url": "https://github.com/jaakla/openmapstack-skills/issues/43"},
            "rationale": "test fixture source",
        }
    }
    workspace = make_workspace()
    write_project(workspace, project)
    return workspace, project


def _load_provision():
    spec = importlib.util.spec_from_file_location("nyc_provision", EXAMPLE / "provision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_sql_file(cursor, path: Path, params: dict[str, str]) -> None:
    provision = _load_provision()
    cursor.execute(provision.substitute_variables(path.read_text(encoding="utf-8"), params))


class StaticFixtureContractTests(unittest.TestCase):
    """The committed SQL files must declare the whole security boundary."""

    def test_postgis_seed_derives_h3_geometry_from_pinned_cell_ids(self) -> None:
        schema = (SETUP / "schema.sql").read_text(encoding="utf-8")
        zones = (SETUP / "zones.sql").read_text(encoding="utf-8")
        cells = json.loads((EXAMPLE / "setup/h3/cells.json").read_text(encoding="utf-8"))
        self.assertIn("CREATE EXTENSION IF NOT EXISTS h3;", schema)
        self.assertIn("CREATE EXTENSION IF NOT EXISTS h3_postgis CASCADE;", schema)
        self.assertIn("h3_cell_to_boundary_geometry(h3_cell::h3index)", zones)
        self.assertIn("h3_cell_to_geometry(h3_cell::h3index)", zones)
        self.assertNotIn("ST_GeomFromText", zones)
        seeded = re.findall(r"^\((\d+),'([0-9a-f]+)','([^']+)'\)", zones, re.M)
        self.assertEqual([(int(zone_id), cell, borough) for zone_id, cell, borough in seeded],
                         [(item["zone_id"], item["h3_cell"], item["borough"]) for item in cells])

    def test_security_sql_declares_the_full_boundary(self) -> None:
        security = (SETUP / "security.sql").read_text(encoding="utf-8")
        self.assertIn(READER_NAME, security)
        self.assertIn("ENABLE ROW LEVEL SECURITY", security)
        for table in ("ops.hubs", "ops.fleet_positions", "ops.service_areas", "ops.customer_accounts"):
            self.assertIn(table, security, table)
        # column grants on customer_accounts exclude the protected columns
        granted = "(account_id" + security.split("GRANT SELECT (account_id", 1)[1].split(";", 1)[0]
        for column in ACCOUNTS_GRANTED_COLUMNS:
            self.assertIn(column, granted, column)
        for column in ACCOUNTS_PROTECTED_COLUMNS:
            self.assertNotIn(f" {column},", granted, column)
            self.assertNotIn(f" {column})", granted, column)
        # hr is closed to PUBLIC and the reader
        self.assertIn("REVOKE ALL ON SCHEMA hr", security)
        self.assertIn("REVOKE ALL ON ALL TABLES IN SCHEMA hr", security)

    def test_no_credentials_are_committed(self) -> None:
        offenders: list[str] = []
        for path in (EXAMPLE / "project.yaml", *(EXAMPLE / "setup").rglob("*.sql"), *EXAMPLE.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for marker in ("postgresql://", "postgres://"):
                if marker in text:
                    offenders.append(f"{path.name}: hardcoded DSN")
            # SQL files may discuss credentials in comments; only real code counts
            body = re.sub(r"--[^\n]*", " ", text) if path.suffix == ".sql" else text
            for marker in ("password='", 'password="'):
                if marker in body.lower():
                    offenders.append(f"{path.name}: hardcoded password literal")
        self.assertEqual(offenders, [])
        project = yaml.safe_load((EXAMPLE / "project.yaml").read_text(encoding="utf-8"))
        # Every source reads through a restricted analysis identity, by
        # reference. The admin credentials that provision.py uses must never
        # appear here: a manifest that named one would make the fixture's
        # whole identity split decorative.
        analysis_refs = {
            "env:OMS_DEMO_POSTGIS_DSN",
            "env:GOOGLE_APPLICATION_CREDENTIALS",
            "env:MOTHERDUCK_TOKEN",
        }
        admin_names = (
            "ADMIN", "OMS_DEMO_POSTGIS_READER_PASSWORD", "OMS_DEMO_BIGQUERY_READER_PRINCIPAL",
        )
        for key, source in project["sources"].items():
            with self.subTest(source=key):
                connection = source["access"].get("connection")
                if connection is None:
                    self.assertEqual(source["access"]["method"], "local_synthetic_fixture")
                    self.assertEqual(source["origin"], "locally_generated_synthetic")
                    continue
                ref = connection["ref"]
                self.assertIn(ref, analysis_refs)
                for name in admin_names:
                    self.assertNotIn(name, ref)
        provision = (EXAMPLE / "provision.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("OMS_DEMO_POSTGIS_READER_PASSWORD"', provision)

    def test_seed_declares_deterministic_derivation(self) -> None:
        seed = (SETUP / "seed.sql").read_text(encoding="utf-8")
        body = re.sub(r"--[^\n]*", " ", seed)  # comments may discuss the design
        self.assertIn("20260917", seed)
        self.assertIn("md5(", body)
        self.assertNotIn("random()", body)
        self.assertNotIn("now()", body)
        self.assertNotIn("clock_timestamp", body)
        self.assertIn("TRUNCATE", seed)  # idempotent re-runs


class MiniFixtureContractTests(unittest.TestCase):
    """The committed mini fixture matches its expected values and regenerates
    deterministically (offline; no credentials, no network)."""

    @classmethod
    def setUpClass(cls) -> None:
        if not DUCKDB_AVAILABLE:
            raise unittest.SkipTest("DuckDB Spatial is not available")
        cls.duck = connect_spatial()
        cls.expected = yaml.safe_load((MINI / "expected.yaml").read_text(encoding="utf-8"))

    def test_expected_counts_match_committed_data(self) -> None:
        counts = self.expected["counts"]
        parquet = lambda name: f"read_parquet('{(MINI / name).as_posix()}')"
        geojson = lambda name: f"st_read('{(MINI / name).as_posix()}')"
        self.assertEqual(self.duck.execute(f"SELECT count(*) FROM {geojson('taxi-zones.geojson')}").fetchone()[0], counts["taxi_zones"])
        self.assertEqual(
            self.duck.execute(f"SELECT count(*) FROM {parquet('trip-events.parquet')}").fetchone()[0],
            counts["trip_events"]["total"],
        )
        visible, hidden = self.duck.execute(
            f"SELECT count(*) FILTER (WHERE tenant_id = 'alpha'), count(*) FILTER (WHERE tenant_id <> 'alpha') FROM {parquet('trip-events.parquet')}"
        ).fetchone()
        self.assertEqual(visible, counts["trip_events"]["alpha_visible"])
        self.assertEqual(hidden, counts["trip_events"]["beta_hidden"])
        total, nulls = self.duck.execute(
            f"SELECT count(*), count(*) - count(geom) FROM {geojson('fleet-positions.geojson')}"
        ).fetchone()
        self.assertEqual((total, nulls), (counts["fleet_positions"]["total"], counts["fleet_positions"]["null_geometry"]))
        self.assertEqual(self.duck.execute(f"SELECT count(*) FROM {geojson('hubs.geojson')}").fetchone()[0], counts["hubs"])
        self.assertEqual(self.duck.execute(f"SELECT count(*) FROM {geojson('pois.geojson')}").fetchone()[0], counts["pois"])
        self.assertEqual(self.duck.execute(f"SELECT count(*) FROM {parquet('market-scores.parquet')}").fetchone()[0], counts["market_scores"])

    def test_mixed_crs_input_is_detected(self) -> None:
        declared = self.expected["security"]["mixed_crs_inputs"]
        actual = self.duck.execute(
            f"SELECT DISTINCT ST_CRS(geom) FROM st_read('{(MINI / 'fleet-positions.geojson').as_posix()}') WHERE geom IS NOT NULL"
        ).fetchall()
        self.assertEqual([str(row[0]) for row in actual], list(declared.values()))

    def test_demand_ranking_and_candidate_zone_recompute(self) -> None:
        security = self.expected["security"]
        visible = security["rls_visible_tenant"]
        demand = dict(
            self.duck.execute(
                f"SELECT pickup_zone_id, count(*) FROM read_parquet('{(MINI / 'trip-events.parquet').as_posix()}') "
                f"WHERE tenant_id = '{visible}' GROUP BY 1 ORDER BY 1"
            ).fetchall()
        )
        self.assertEqual(
            {int(key): value for key, value in self.expected["alpha_pickups_by_zone"].items()}, demand
        )
        ranking = self.expected["ranking"]
        self.assertEqual([item["zone"] for item in ranking], sorted((item["zone"] for item in ranking), key=lambda zone: (-next(r["score"] for r in ranking if r["zone"] == zone), zone)))
        self.assertEqual(ranking[0]["zone"], self.expected["candidate_zone"])
        best = ranking[0]["score"]
        self.assertTrue(all(item["score"] < best for item in ranking[1:]))  # unambiguous winner

    def test_regeneration_is_deterministic(self) -> None:
        import hashlib
        import subprocess
        import sys

        gen = MINI / "gen.py"

        def digests() -> dict[str, str]:
            return {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(MINI.glob("*"))
                if path.name not in {"gen.py", "__pycache__"} and path.is_file()
            }

        before = digests()
        result = subprocess.run(
            [sys.executable, str(gen)],
            capture_output=True,
            cwd=MINI,
            env={**os.environ, "OPENMAPSTACK_SPATIAL_EXTENSION_DIR": os.environ.get("OPENMAPSTACK_SPATIAL_EXTENSION_DIR", "")},
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode()[-500:])
        self.assertEqual(before, digests(), "regeneration must be byte-identical (seed 20260917)")


@unittest.skipUnless(ADMIN_DSN, "OPENMAPSTACK_TEST_PRIVATE_POSTGIS_ADMIN_DSN is not set")
class PrivatePostGISLiveTests(unittest.TestCase):
    """End-to-end against a real PostGIS: provision the fixture, then prove
    the enforced security model through the restricted reader and connector."""

    @classmethod
    def setUpClass(cls) -> None:
        import psycopg

        cls.psycopg = psycopg
        provision = _load_provision()
        reader_password = os.environ.get("OPENMAPSTACK_TEST_PRIVATE_POSTGIS_READER_PASSWORD", "ci-only-reader")
        provision.provision_postgis(ADMIN_DSN, READER_NAME, reader_password)
        cls.reader_dsn = READER_DSN
        cls.provision = provision

    def _reader_connection(self):
        return self.psycopg.connect(self.reader_dsn)

    def test_reader_sees_only_alpha_rows(self) -> None:
        with self._reader_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(DISTINCT tenant_id) FROM ops.fleet_positions")
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT DISTINCT tenant_id FROM ops.fleet_positions")
                self.assertEqual([row[0] for row in cursor.fetchall()], ["alpha"])
                cursor.execute("SELECT count(*) FROM ops.customer_accounts WHERE tenant_id <> 'alpha'")
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_select_star_fails_on_column_granted_table(self) -> None:
        with self._reader_connection() as connection:
            with connection.cursor() as cursor:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    cursor.execute("SELECT * FROM ops.customer_accounts LIMIT 1")

    def test_protected_column_is_denied(self) -> None:
        for column in ACCOUNTS_PROTECTED_COLUMNS:
            with self._reader_connection() as connection:
                with connection.cursor() as cursor:
                    with self.assertRaises(self.psycopg.errors.InsufficientPrivilege, msg=column):
                        cursor.execute(f"SELECT {column} FROM ops.customer_accounts LIMIT 1")

    def test_driver_private_is_inaccessible(self) -> None:
        with self._reader_connection() as connection:
            with connection.cursor() as cursor:
                with self.assertRaises(self.psycopg.errors.InsufficientPrivilege):
                    cursor.execute("SELECT count(*) FROM hr.driver_private")

    def test_connector_snapshot_respects_rls_and_column_grants(self) -> None:
        workspace, project = _postgis_project("fleet", "fleet_positions")
        environ = {"PRIVATE_FIXTURE_DSN": self.reader_dsn}
        discovery = discover_source(project, "fleet", project_root=workspace, environ=environ)
        self.assertTrue(discovery.read_only)
        # PostGIS's geometry_columns hides tables where the reader holds only
        # column-level grants, so discovery lists the reference table but not
        # the RLS/column-restricted relations; snapshotting still works.
        self.assertTrue(any(table.name == "taxi_zones" for table in discovery.tables))
        query = "SELECT position_id, tenant_id, vehicle_id, taxi_zone_id, status, geom FROM ops.fleet_positions"
        record = snapshot_source(
            project, "fleet", query, "data/source/fleet.parquet", project_root=workspace, approve=True, environ=environ
        )
        self.assertTrue(record["materialized"])
        self.assertTrue(record["rows"] > 0)
        self.assertNotIn("ci-only-reader", json.dumps(record))
        updated = apply_snapshot_to_manifest(project, "fleet", record)
        self.assertEqual(assess_pin(workspace, updated["sources"]["fleet"]).status, "pinned")
        duck = connect_spatial()
        try:
            foreign_rows = duck.execute(
                f"SELECT count(*) FROM read_parquet('{(workspace / 'data/source/fleet.parquet').as_posix()}') WHERE tenant_id <> 'alpha'"
            ).fetchone()[0]
        finally:
            duck.close()
        self.assertEqual(foreign_rows, 0)

    def test_connector_cannot_snapshot_a_protected_column_or_denied_table(self) -> None:
        workspace, project = _postgis_project("accounts", "customer_accounts")
        environ = {"PRIVATE_FIXTURE_DSN": self.reader_dsn}
        for query in (
            "SELECT contact_email FROM ops.customer_accounts",
            "SELECT * FROM ops.customer_accounts",
            "SELECT count(*) FROM hr.driver_private",
        ):
            with self.assertRaises(ConnectorError, msg=query):
                snapshot_source(project, "accounts", query, "data/source/leak.parquet", project_root=workspace, environ=environ)
        self.assertFalse((workspace / "data/source/leak.parquet").exists())


if __name__ == "__main__":
    unittest.main()
