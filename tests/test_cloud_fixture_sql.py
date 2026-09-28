"""BigQuery and MotherDuck fixture SQL (examples/nyc-private-mobility/setup/).

Two layers, for two different reasons.

The MotherDuck fixture is written as ordinary DuckDB SQL precisely so it can
be *executed* here: the same schema, seed and view definitions that
`provision.py motherduck` sends to `md:` run against a local in-memory
catalog, so a syntax error or a drifted row count fails on every PR with no
account and no network.

The BigQuery fixture cannot be executed without a billed project, so these
are contract assertions on the text: the security boundary must be declared,
the seed must be deterministic and idempotent, every psql variable must
resolve, and column-level security must stay a separate, explicitly requested
step rather than something a reader could mistake for applied. Live BigQuery
behaviour is the canary workflow's job (`.github/workflows/cloud-canary.yml`),
and a missing account there is reported as `not_testable`, never as a pass.
"""

from __future__ import annotations

import importlib.util
import io
import re
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from openmapstack.checks.spatial import connect_spatial

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "examples" / "nyc-private-mobility"
BIGQUERY = EXAMPLE / "setup" / "bigquery"
MOTHERDUCK = EXAMPLE / "setup" / "motherduck"
DUCKDB_AVAILABLE = connect_spatial() is not None

BIGQUERY_PARAMS = {
    "project": "northstar-demo",
    "dataset": "northstar_analytics",
    "restricted_dataset": "northstar_analytics_restricted",
    "location": "US",
    "reader_principal": "serviceAccount:oms-alpha-reader@northstar-demo.iam.gserviceaccount.com",
    "policy_tag": "projects/northstar-demo/locations/us/taxonomies/1/policyTags/2",
}
PROTECTED_COLUMNS = ("internal_cost", "rider_reference")
TENANT_TABLES = ("trip_events", "zone_daily_demand", "vehicle_daily_metrics")


def _load_provision():
    spec = importlib.util.spec_from_file_location("nyc_provision_cloud", EXAMPLE / "provision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rendered(name: str) -> str:
    return _load_provision().substitute_variables(_load_provision().fixture_sql(BIGQUERY / name), BIGQUERY_PARAMS)


def _code(sql: str) -> str:
    """Drop `--` comments: a comment may discuss what the SQL must not do."""
    return re.sub(r"--[^\n]*", " ", sql)


def _outside_literals(sql: str) -> str:
    """Drop comments *and* string literals, so a value like
    `serviceAccount:oms-...` is not mistaken for a `:placeholder`."""
    return re.sub(r"'(?:[^']|'')*'", "''", _code(sql))


@unittest.skipUnless(DUCKDB_AVAILABLE, "DuckDB Spatial is not available")
class MotherDuckFixtureExecutionTests(unittest.TestCase):
    """The `md:` fixture is DuckDB SQL, so it is run, not just read."""

    def _apply(self, *names: str):
        connection = connect_spatial()
        self.addCleanup(connection.close)
        connection.execute("ATTACH ':memory:' AS northstar_market")
        connection.execute("USE northstar_market")
        for name in names or ("schema.sql", "seed.sql", "security.sql"):
            connection.execute(_load_provision().fixture_sql(MOTHERDUCK / name))
        return connection

    def test_the_fixture_applies_and_holds_its_declared_volumes(self) -> None:
        connection = self._apply()
        counts = {
            table: connection.execute(f"SELECT count(*) FROM market.{table}").fetchone()[0]
            for table in ("zone_market_scores", "relevant_pois", "analyst_annotations")
        }
        self.assertEqual(counts, {"zone_market_scores": 83, "relevant_pois": 240, "analyst_annotations": 24})
        low, high = connection.execute("SELECT min(taxi_zone_id), max(taxi_zone_id) FROM market.relevant_pois").fetchone()
        self.assertGreaterEqual(low, 101)
        self.assertLessEqual(high, 183)
        self.assertEqual(
            connection.execute("SELECT count(*) FROM market.relevant_pois WHERE geom IS NULL").fetchone()[0], 0
        )

    def test_the_seed_is_deterministic_and_idempotent(self) -> None:
        def digest(connection) -> tuple:
            return (
                connection.execute("SELECT round(sum(market_score), 6) FROM market.zone_market_scores").fetchone()[0],
                connection.execute("SELECT string_agg(poi_id, ',' ORDER BY poi_id) FROM market.relevant_pois").fetchone()[0],
                connection.execute(
                    "SELECT round(sum(confidence), 6) FROM market.analyst_annotations"
                ).fetchone()[0],
            )

        first = digest(self._apply())
        second_connection = self._apply()
        before_rerun = digest(second_connection)
        # Re-applying must land on the same rows, not double them.
        second_connection.execute(_load_provision().fixture_sql(MOTHERDUCK / "seed.sql"))
        self.assertEqual(first, before_rerun)
        self.assertEqual(digest(second_connection), first)
        self.assertEqual(
            second_connection.execute("SELECT count(*) FROM market.relevant_pois").fetchone()[0], 240
        )

    def test_the_analysis_views_exclude_the_private_annotations(self) -> None:
        connection = self._apply()
        views = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_catalog = 'northstar_market' AND table_type = 'VIEW'"
            ).fetchall()
        }
        self.assertIn("analysis_zone_scores", views)
        self.assertIn("analysis_zone_poi_counts", views)
        definitions = _code((MOTHERDUCK / "security.sql").read_text(encoding="utf-8"))
        self.assertNotIn("analyst_annotations", definitions, "no analysis view may republish the private notes")
        counted = connection.execute(
            "SELECT sum(total_pois) FROM market.analysis_zone_poi_counts"
        ).fetchone()[0]
        self.assertEqual(counted, 240)

    def test_the_seed_uses_a_checked_seed_not_a_random_source(self) -> None:
        body = _code((MOTHERDUCK / "seed.sql").read_text(encoding="utf-8"))
        self.assertIn("20260917", body)
        self.assertIn("md5_number(", body)
        for forbidden in ("random()", "now()", "current_timestamp", "current_date"):
            self.assertNotIn(forbidden, body.lower(), forbidden)
        self.assertIn("DELETE FROM", body)  # idempotent re-runs


class BigQueryFixtureContractTests(unittest.TestCase):
    """Executed only by the cloud canary; asserted as text on every PR."""

    def test_every_variable_resolves_in_every_file(self) -> None:
        for path in sorted(BIGQUERY.glob("*.sql")):
            with self.subTest(path=path.name):
                rendered = _load_provision().substitute_variables(
                    path.read_text(encoding="utf-8"), BIGQUERY_PARAMS
                )
                leftovers = re.findall(r"(?<!:):[A-Za-z_]\w*", _outside_literals(rendered))
                self.assertEqual(leftovers, [], f"unsubstituted placeholders in {path.name}")
                self.assertIn(BIGQUERY_PARAMS["dataset"], rendered)

    def test_the_public_reference_table_carries_no_tenant_and_no_policy(self) -> None:
        """`taxi_zones` is the public-origin layer, and the only analysis table
        whose dry run returns a byte estimate — BigQuery withholds it wherever
        a row access policy applies."""
        schema = _rendered("schema.sql")
        self.assertIn("taxi_zones", schema)
        definition = schema.split(".taxi_zones (", 1)[1].split(";", 1)[0]
        self.assertNotIn("tenant_id", definition)
        self.assertIn("GEOGRAPHY", definition)
        security = _code(_rendered("security.sql"))
        self.assertNotIn("taxi_zones", security, "a policy on taxi_zones would defeat its purpose")

    def test_the_protected_columns_exist_to_be_protected(self) -> None:
        schema = _rendered("schema.sql")
        for column in PROTECTED_COLUMNS:
            self.assertIn(column, schema, column)
        self.assertIn("GEOGRAPHY", schema)
        self.assertIn("PARTITION BY", schema)
        # The restricted dataset is a separate dataset, which is what makes it
        # grantable-to-nobody.
        self.assertIn(BIGQUERY_PARAMS["restricted_dataset"], schema)
        self.assertIn("driver_costs", schema)

    def test_the_reader_is_granted_the_analysis_dataset_and_revoked_the_restricted_one(self) -> None:
        security = _code(_rendered("security.sql"))
        granted = security[security.rindex("GRANT `roles/bigquery.dataViewer`"):].split(";", 1)[0]
        self.assertIn(BIGQUERY_PARAMS["dataset"], granted)
        self.assertNotIn(BIGQUERY_PARAMS["restricted_dataset"], granted)
        revoked = security.split("REVOKE ", 1)[1].split(";", 1)[0]
        self.assertIn(BIGQUERY_PARAMS["restricted_dataset"], revoked)
        self.assertIn(BIGQUERY_PARAMS["reader_principal"], security)

    def test_every_tenant_bearing_table_carries_a_row_access_policy(self) -> None:
        security = _code(_rendered("security.sql"))
        policies = re.findall(r"ROW ACCESS POLICY\s+(\w+)\s+ON\s+\S+\.(\w+)", security)
        self.assertEqual({table for _, table in policies}, set(TENANT_TABLES))
        self.assertEqual(security.count("FILTER USING (tenant_id = 'alpha')"), len(TENANT_TABLES))

    def test_the_reader_is_granted_only_after_every_policy_exists(self) -> None:
        """Order is the whole security property here.

        Dropping a row access policy does not restrict an existing grant, it
        removes the *filter*: a reader holding `dataViewer` while the policies
        are gone reads every tenant's rows. Measured on the live fixture at
        4800 rows across 3 tenants instead of 2917 from tenant alpha. So the
        grant must be the last thing security.sql does, and the seed must not
        grant at all.
        """
        security = _code(_rendered("security.sql"))
        last_policy = security.rindex("CREATE OR REPLACE ROW ACCESS POLICY")
        analysis_grant = security.rindex(
            f"GRANT `roles/bigquery.dataViewer`\nON SCHEMA `{BIGQUERY_PARAMS['project']}`"
            f".`{BIGQUERY_PARAMS['dataset']}`"
        )
        self.assertGreater(
            analysis_grant, last_policy,
            "the dataset grant must come after every row access policy, or the reader is "
            "briefly unfiltered",
        )
        seed = _code(_rendered("seed.sql"))
        self.assertNotIn("GRANT `roles/bigquery.dataViewer`", seed)

    def test_provisioning_confirms_the_lock_out_before_dropping_policies(self) -> None:
        """The revoke cannot live in seed.sql: it and the drops would be one
        BigQuery job, and an IAM revoke takes a moment to become effective, so
        there would be nothing to wait on."""
        provision = (EXAMPLE / "provision.py").read_text(encoding="utf-8")
        lock_out = provision.index("lock_out_bigquery_reader(client")
        seed_apply = provision.index('for name in ("seed.sql", "security.sql")')
        self.assertLess(lock_out, seed_apply)
        seed = _code(_rendered("seed.sql"))
        self.assertNotIn("REVOKE", seed, "the revoke belongs in provisioning, which can wait for it")

    def test_an_unconfirmable_lock_out_is_reported_not_assumed(self) -> None:
        provision = _load_provision()
        calls = []

        class _Admin:
            def query(self, sql):
                calls.append(sql)
                return self

            def result(self):
                return []

        out = io.StringIO()
        with redirect_stdout(out):
            provision.lock_out_bigquery_reader(_Admin(), "", "p", "d", "serviceAccount:x@p.iam", "US")
        self.assertIn("REVOKE", calls[0])
        self.assertIn("could not be confirmed", out.getvalue())

    def test_a_reader_that_never_loses_access_stops_provisioning(self) -> None:
        """Refusing is the only safe answer: dropping the policies anyway is
        precisely the exposure this step exists to prevent."""
        provision = _load_provision()

        class _Stubborn:
            """Still answering queries however long the revoke has been in."""

            def query(self, sql):
                return self

            def result(self):
                return [1]

        provision._bigquery_client = lambda *args, **kwargs: _Stubborn()
        out = io.StringIO()
        with self.assertRaises(SystemExit) as caught, redirect_stdout(out):
            provision.lock_out_bigquery_reader(
                _Stubborn(), "reader-key.json", "p", "d", "serviceAccount:x@p.iam", "US", timeout_s=0.1
            )
        self.assertIn("refusing to drop", str(caught.exception))
        self.assertIn("every tenant", str(caught.exception))

    def test_column_security_stays_a_separate_explicitly_requested_step(self) -> None:
        """A fixture that implied column security it had not applied would be
        worse than one that applies none: the security.sql text must not
        claim it, and the ALTERs must live in their own file."""
        security = _code(_rendered("security.sql"))
        self.assertNotIn("policy_tags", security)
        column_security = _rendered("column-security.sql")
        for column in PROTECTED_COLUMNS:
            self.assertIn(f"ALTER COLUMN {column}", column_security, column)
        self.assertIn(BIGQUERY_PARAMS["policy_tag"], column_security)

    def test_the_seed_is_deterministic_and_idempotent(self) -> None:
        body = _code(_rendered("seed.sql"))
        self.assertIn("20260917", body)
        self.assertIn("MD5(", body)
        for forbidden in ("RAND()", "CURRENT_TIMESTAMP()", "CURRENT_DATE()", "GENERATE_UUID()"):
            self.assertNotIn(forbidden, body, forbidden)
        self.assertEqual(body.count("TRUNCATE TABLE"), 5)
        # Found by re-running provisioning against the live service: a table
        # under a row access policy cannot be truncated even by the admin that
        # created it, so the policies must be dropped before the reseed and
        # security.sql re-creates them straight after.
        for table in TENANT_TABLES:
            self.assertIn(f"DROP ALL ROW ACCESS POLICIES ON `{BIGQUERY_PARAMS['project']}`"
                          f".`{BIGQUERY_PARAMS['dataset']}`.{table}", body, table)
        self.assertLess(
            body.index("DROP ALL ROW ACCESS POLICIES"),
            body.index("TRUNCATE TABLE"),
            "policies must be dropped before the first truncate, or the reseed fails",
        )

    def test_no_real_operator_is_implied_by_the_fixture(self) -> None:
        for path in sorted(BIGQUERY.glob("*.sql")):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("bigquery-public-data", _code(text))


class FixtureManifestTests(unittest.TestCase):
    """`fixture.yaml` describes the boundary; the SQL creates it. Drift between
    the two would make the documented access matrix quietly wrong, which is
    the one thing a security fixture may not be."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = yaml.safe_load((EXAMPLE / "fixture.yaml").read_text(encoding="utf-8"))

    def test_every_declared_relation_exists_in_the_setup_sql(self) -> None:
        sql = {
            "postgis": (EXAMPLE / "setup" / "postgis" / "schema.sql").read_text(encoding="utf-8"),
            "bigquery": (BIGQUERY / "schema.sql").read_text(encoding="utf-8"),
            "motherduck": (MOTHERDUCK / "schema.sql").read_text(encoding="utf-8"),
        }
        for backend, text in sql.items():
            for relation in self.fixture["backends"][backend]["relations"]:
                bare = relation["name"].rsplit(".", 1)[-1]
                with self.subTest(backend=backend, relation=relation["name"]):
                    self.assertIn(bare, text)

    def test_no_restriction_claims_an_enforcer_it_does_not_have(self) -> None:
        """A restriction whose enforcement is a convention must say so, and a
        restriction that is optional must name what applies it."""
        for backend, block in self.fixture["backends"].items():
            for restriction in block["restrictions"]:
                with self.subTest(backend=backend, kind=restriction["kind"]):
                    self.assertIn("enforced_by", restriction)
                    if restriction["kind"] == "convention":
                        self.assertIn("nothing", restriction["enforced_by"])
        column_level = next(
            item for item in self.fixture["backends"]["bigquery"]["restrictions"] if item["kind"] == "column_level"
        )
        self.assertIn("applied_when", column_level, "an optional restriction must name how it is applied")
        self.assertIn("--policy-tag", column_level["applied_when"])

    def test_the_analysis_identity_is_declared_by_reference_only(self) -> None:
        identities = self.fixture["identities"]
        self.assertNotEqual(identities["provisioning"]["credentials"], identities["analysis"]["credentials"])
        project = yaml.safe_load((EXAMPLE / "project.yaml").read_text(encoding="utf-8"))
        referenced = {
            source["access"]["connection"]["ref"].split(":", 1)[1] for source in project["sources"].values()
            if source["access"].get("connection")
        }
        for source in project["sources"].values():
            if not source["access"].get("connection"):
                self.assertEqual(source["origin"], "locally_generated_synthetic")
                self.assertEqual(source["access"]["method"], "local_synthetic_fixture")
                self.assertIn("NOT TESTED", source["selection"]["security_note"])
        self.assertTrue(referenced.issubset(set(identities["analysis"]["credentials"])), referenced)
        self.assertFalse(referenced & set(identities["provisioning"]["credentials"]))

    def test_verification_distinguishes_not_configured_from_passed(self) -> None:
        reports = self.fixture["verification"]["reports"]
        self.assertEqual(set(reports), {"pass", "fail", "not_configured"})
        self.assertIn("not a pass", reports["not_configured"])


class ThreeSourceExampleTests(unittest.TestCase):
    """The worked example must actually be three-source, and its scoring must
    be recomputable from the file it publishes.

    The placeholder stage scored `market_score = 0.0` for every zone and
    proxied demand from account counts. Both are easy to regress back into
    without any other check noticing: the pipeline would still run, the
    validation report would still pass, and the answer would quietly stop
    using two of the three backends.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = yaml.safe_load((EXAMPLE / "project.yaml").read_text(encoding="utf-8"))

    def test_the_manifest_reads_from_all_three_backends(self) -> None:
        from openmapstack.connectors import BACKENDS

        backends = {source["warehouse"]["backend"] for source in self.project["sources"].values()}
        self.assertEqual(backends, {"postgis", "bigquery", "motherduck"})
        for backend in backends:
            self.assertIn(backend, BACKENDS, "the example may only use verified backends")

    def test_each_warehouse_source_is_pinned_to_local_bytes(self) -> None:
        """Credential-free rerun is the acceptance gate; a backend_snapshot
        pin would make it depend on the vendor's retention instead."""
        for key, source in self.project["sources"].items():
            with self.subTest(source=key):
                self.assertEqual(source["pin"]["class"], "local_snapshot")
                self.assertTrue((EXAMPLE / source["pin"]["path"]).is_file())

    def test_the_cloud_snapshot_queries_are_committed_and_policy_clean(self) -> None:
        from openmapstack.connectors import require_read_only_select

        for name in ("bigquery-zone-demand.sql", "motherduck-zone-market.sql"):
            with self.subTest(query=name):
                text = (EXAMPLE / "queries" / name).read_text(encoding="utf-8")
                self.assertTrue(require_read_only_select(text))
                # The tenant boundary is the backend's. A filter here would
                # hide whether it works.
                self.assertNotIn("tenant_id", _code(text))
        market = (EXAMPLE / "queries" / "motherduck-zone-market.sql").read_text(encoding="utf-8")
        self.assertNotIn("analyst_annotations", _code(market))

    @unittest.skipUnless(DUCKDB_AVAILABLE, "DuckDB Spatial is not available")
    def test_the_derived_metrics_carry_evidence_from_every_backend(self) -> None:
        connection = connect_spatial()
        self.addCleanup(connection.close)
        path = (EXAMPLE / "data/derived/zone-metrics.parquet").as_posix()
        columns = {
            str(name) for name, *_ in connection.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
        }
        for column in ("trips_total", "trips_peak_day", "trips_stddev_day"):  # BigQuery
            self.assertIn(column, columns)
        for column in ("market_score_raw", "charging_pois", "competitor_pois"):  # MotherDuck
            self.assertIn(column, columns)
        for column in ("fleet_present", "hub_distance_m"):  # PostGIS
            self.assertIn(column, columns)
        distinct_market = connection.execute(
            f"SELECT count(DISTINCT market_score) FROM read_parquet('{path}') WHERE market_score IS NOT NULL"
        ).fetchone()[0]
        self.assertGreater(distinct_market, 1, "market_score is constant -- the A4 placeholder is back")

    @unittest.skipUnless(DUCKDB_AVAILABLE, "DuckDB Spatial is not available")
    def test_every_published_score_recomputes_from_the_published_columns(self) -> None:
        """project.yaml claims final_score is recomputable from
        zone-metrics.parquet alone. This recomputes it from the declared
        weights and thresholds and refuses any disagreement."""
        scoring = self.project["scoring"]
        weights, components, eligibility = scoring["weights"], scoring["components"], scoring["eligibility"]
        amenity_full = components["amenity_pois_for_full_score"]
        competitor_full = components["competitor_pois_for_full_pressure"]

        connection = connect_spatial()
        self.addCleanup(connection.close)
        path = (EXAMPLE / "data/derived/zone-metrics.parquet").as_posix()
        rows = connection.execute(
            "SELECT zone_id, trips_total, hub_distance_m, fleet_present, market_score_raw, "
            "charging_pois, parking_pois, transit_pois, competitor_pois, market_score, final_score "
            f"FROM read_parquet('{path}') ORDER BY zone_id"
        ).fetchall()
        pool = [
            row for row in rows
            if row[1] >= eligibility["trips_total_min"]
            and row[2] >= eligibility["hub_distance_m_min"]
            and row[3] <= eligibility["fleet_present_max"]
        ]
        self.assertTrue(pool, "no zone satisfies the declared eligibility thresholds")
        max_demand = max(row[1] for row in pool)
        max_distance = max(row[2] for row in pool)
        max_fleet = max(row[3] for row in pool)
        for row in pool:
            zone_id, trips, distance, fleet, raw, charging, parking, transit, competitor = row[:9]
            with self.subTest(zone=zone_id):
                market = round(
                    0.5 * raw
                    + 0.3 * min(1.0, (charging + parking + transit) / amenity_full)
                    + 0.2 * (1.0 - min(1.0, competitor / competitor_full)),
                    6,
                )
                final = round(
                    100 * (
                        weights["demand"] * (trips / max_demand)
                        + weights["coverage_gap"] * (distance / max_distance)
                        + weights["market"] * market
                        + weights["saturation"] * (1.0 - fleet / max_fleet)
                    ),
                    2,
                )
                self.assertAlmostEqual(market, row[9], places=6)
                self.assertAlmostEqual(final, row[10], places=6)
        ineligible = [row[10] for row in rows if row not in pool]
        self.assertTrue(all(score is None for score in ineligible),
                        "a zone outside the eligibility thresholds must carry no score")


def _load_pipeline():
    spec = importlib.util.spec_from_file_location("nyc_pipeline", EXAMPLE / "pipeline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:  # PyQGIS has no PyPI distribution; it comes from a system QGIS install.
    import qgis.core as _qgis_core  # noqa: F401

    PYQGIS_AVAILABLE = True
except Exception:  # noqa: BLE001
    PYQGIS_AVAILABLE = False


class QgisProjectBuilderTests(unittest.TestCase):
    """The project is written by QGIS where PyQGIS exists and by a
    deterministic builder where it does not. Two writers for one artifact is
    only safe if they agree, so the agreement is asserted rather than assumed.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.pipeline = _load_pipeline()
        cls.manifest = yaml.safe_load((EXAMPLE / "project.yaml").read_text(encoding="utf-8"))

    @staticmethod
    def _summary(xml: str) -> dict:
        """What the two writers must agree on: the map, not the file bytes."""
        root = ET.fromstring(xml)
        layers = {}
        for layer in root.findall("./projectlayers/maplayer"):
            identifier = layer.findtext("id")
            renderer = layer.find("renderer-v2")
            layers[identifier] = {
                "datasource": layer.findtext("datasource"),
                "name": layer.findtext("layername"),
                "provider": layer.findtext("provider"),
                "crs": layer.findtext("./srs/spatialrefsys/authid"),
                "renderer": renderer.get("type") if renderer is not None else None,
                "renderer_attr": renderer.get("attr") if renderer is not None else None,
            }
        return {
            "project_crs": root.findtext("./projectCrs/spatialrefsys/authid"),
            "tree": sorted(node.get("id") for node in root.findall(".//layer-tree-layer")),
            "groups": sorted(node.get("name") for node in root.findall(".//layer-tree-group") if node.get("name")),
            "layers": layers,
        }

    def test_the_fallback_builder_declares_every_layer_the_spec_names(self) -> None:
        summary = self._summary(self.pipeline._build_qgis_xml(self.manifest))
        self.assertEqual(
            sorted(summary["layers"]), sorted(spec["id"] for spec in self.pipeline.QGIS_LAYERS)
        )
        self.assertEqual(summary["project_crs"], "EPSG:4326")
        basemap = summary["layers"][self.pipeline.BASEMAP_LAYER_ID]
        # The tiles are Web Mercator; calling them 4326 misplaces the basemap.
        self.assertEqual(basemap["crs"], "EPSG:3857")
        candidates = summary["layers"][self.pipeline.CANDIDATES_LAYER_ID]
        self.assertEqual(candidates["renderer"], "graduatedSymbol")
        self.assertEqual(candidates["renderer_attr"], "final_score")

    def test_the_fallback_builder_does_not_claim_qgis_wrote_it(self) -> None:
        """A file asserting a QGIS version that never touched it is a
        provenance claim this project has no business making."""
        root = ET.fromstring(self.pipeline._build_qgis_xml(self.manifest))
        self.assertEqual(root.get("version"), self.pipeline.BUILDER_VERSION)

    def test_the_fallback_builder_is_byte_stable(self) -> None:
        first = self.pipeline._build_qgis_xml(self.manifest)
        self.assertEqual(first, self.pipeline._build_qgis_xml(self.manifest))

    @unittest.skipUnless(PYQGIS_AVAILABLE, "PyQGIS is not installed in this environment")
    def test_both_writers_describe_the_same_map(self) -> None:
        authored = self.pipeline._strip_volatile(self.pipeline._build_qgis_xml_with_qgis(self.manifest))
        built = self.pipeline._strip_volatile(self.pipeline._build_qgis_xml(self.manifest))
        by_qgis, by_builder = self._summary(authored), self._summary(built)
        self.assertEqual(by_qgis["project_crs"], by_builder["project_crs"])
        self.assertEqual(by_qgis["tree"], by_builder["tree"])
        self.assertEqual(by_qgis["groups"], by_builder["groups"])
        self.assertEqual(sorted(by_qgis["layers"]), sorted(by_builder["layers"]))
        for identifier, qgis_layer in by_qgis["layers"].items():
            with self.subTest(layer=identifier):
                builder_layer = by_builder["layers"][identifier]
                self.assertEqual(qgis_layer["name"], builder_layer["name"])
                self.assertEqual(qgis_layer["provider"], builder_layer["provider"])
                self.assertEqual(qgis_layer["crs"], builder_layer["crs"])
                # The committed project once carried this machine's absolute
                # paths; both writers must emit the portable relative form.
                self.assertEqual(qgis_layer["datasource"], builder_layer["datasource"])
                self.assertEqual(qgis_layer["renderer"], builder_layer["renderer"])
                self.assertEqual(qgis_layer["renderer_attr"], builder_layer["renderer_attr"])
                self.assertEqual(
                    Path(str(qgis_layer["datasource"])).name,
                    Path(str(builder_layer["datasource"])).name,
                )

    @unittest.skipUnless(PYQGIS_AVAILABLE, "PyQGIS is not installed in this environment")
    def test_the_qgis_writer_is_byte_stable_after_normalisation(self) -> None:
        """QGIS stamps a save time, mints UUIDs and colours default symbols at
        random, so its output is not reproducible as written."""
        first = self.pipeline._strip_volatile(self.pipeline._build_qgis_xml_with_qgis(self.manifest))
        second = self.pipeline._strip_volatile(self.pipeline._build_qgis_xml_with_qgis(self.manifest))
        self.assertEqual(first, second)
        self.assertIn(f'version="{_qgis_core.Qgis.QGIS_VERSION}"', first)


class ProvisionCommandTests(unittest.TestCase):
    """`all` and `verify` must report an unconfigured backend, never pass it."""

    def _run(self, argv, environment):
        import os

        provision = _load_provision()
        saved = dict(os.environ)
        os.environ.clear()
        os.environ.update(environment)
        out = io.StringIO()
        try:
            with redirect_stdout(out):
                code = provision.main(argv)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        return code, out.getvalue()

    def test_all_with_no_credentials_provisions_nothing_and_says_so(self) -> None:
        code, output = self._run(["all"], {})
        self.assertEqual(code, 1)
        self.assertIn("provisioned: nothing", output)
        self.assertIn("SKIPPED (no credentials configured): postgis, bigquery, motherduck", output)

    def test_verify_with_no_credentials_fails_rather_than_reporting_success(self) -> None:
        code, output = self._run(["verify"], {})
        self.assertEqual(code, 1)
        self.assertIn("NOT VERIFIED (no reader credentials configured)", output)
        self.assertNotIn("verification passed", output)

    def test_destroy_skips_what_it_has_no_admin_credential_for(self) -> None:
        code, output = self._run(["destroy"], {})
        self.assertEqual(code, 0)
        self.assertIn("destroyed: nothing", output)
        self.assertIn("SKIPPED (no admin credentials configured)", output)

    def test_bigquery_provisioning_refuses_without_the_reader_principal(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            self._run(["bigquery", "--bigquery-project", "northstar-demo"], {})
        self.assertIn("reader-principal", str(caught.exception))

    def test_the_report_helper_separates_failures_from_unconfigured_checks(self) -> None:
        provision = _load_provision()
        out = io.StringIO()
        with redirect_stdout(out):
            ok = provision.report("bigquery", {"a": True, "b": False}, ["column-level security"])
        self.assertFalse(ok)
        self.assertIn("FAIL: b", out.getvalue())
        self.assertIn("NOT CONFIGURED: column-level security", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            ok = provision.report("motherduck", {"a": True}, ["READ_ONLY attach"])
        self.assertTrue(ok, "an unconfigured check is not a failure, but it is not a pass either")
        self.assertIn("NOT CONFIGURED", out.getvalue())


if __name__ == "__main__":
    unittest.main()
