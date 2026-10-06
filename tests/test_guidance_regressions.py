"""Shipped corrections that a live acceptance run caught must not silently revert.

The 0.4.0 acceptance run (see `docs/release-0.4.0.md`) found two task-outcome
failures that traced to shipped guidance rather than to the adapter or model:
an agent claimed a plain `geom` GiST index serves a `::geography` predicate, and
another treated a WFS `resultType=hits` total as proof of completeness. Both
sentences were corrected, and both reruns then passed.

Nothing else in the repository can notice if that wording goes away. The routing
eval grades *selection* only -- `evals/routing.py` reports `task_success` as
`not_testable` by construction -- and the fixture evals grade produced projects,
not the reference text an agent reads before producing one. So a later edit
could reintroduce either error and every automated check would stay green.

These are content assertions, and that is their whole claim: they prove the
corrective guidance is still shipped in every payload that carries the file, and
that the retracted advice has not come back. They do **not** prove an agent
reads it or acts on it. That remains the job of the live cases
`chosen-engine-sql` and `bounded-discovery` in `evals/routing-cases.yaml`, graded
against the rubric in `evals/final-state-acceptance.md`.

Rewording a requirement intentionally means updating the assertion *and*
re-running the owning live case, not just relaxing the string.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


def _shipped_copies(name: str) -> dict[str, str]:
    """Every skill payload that ships `references/<name>`, keyed by repo path."""
    copies = {
        str(path.relative_to(REPO_ROOT)): path.read_text(encoding="utf-8")
        for path in sorted(SKILLS_ROOT.glob(f"*/references/{name}"))
    }
    assert copies, f"no shipped copy of references/{name}"
    return copies


class GuidanceCase(unittest.TestCase):
    """Substring assertions that report the needle, not the whole payload."""

    def assertShips(self, text: str, needle: str, path: str) -> None:
        self.assertTrue(needle in text, f"{path} no longer ships: {needle!r}")

    def assertRetracted(self, text: str, needle: str, path: str) -> None:
        self.assertTrue(needle not in text, f"{path} reintroduced retracted advice: {needle!r}")


class GeographyIndexGuidanceTests(GuidanceCase):
    """`chosen-engine-sql`: an index serves only the expression it was built on."""

    def setUp(self) -> None:
        self.copies = _shipped_copies("spatial-sql.md")

    def test_every_copy_states_that_a_cast_defeats_the_geometry_index(self) -> None:
        for path, text in self.copies.items():
            with self.subTest(path=path):
                self.assertShips(text, "An index serves only the expression it was built on.", path)
                self.assertShips(text, "ST_DWithin(p.geom::geography, s.geom::geography, 500)", path)

    def test_every_copy_offers_an_index_matching_the_predicate(self) -> None:
        for path, text in self.copies.items():
            with self.subTest(path=path):
                self.assertShips(text, "USING GIST ((geom::geography))", path)
                self.assertShips(text, "USING GIST (ST_Transform(geom, 3301))", path)

    def test_every_copy_names_the_plan_evidence_to_check(self) -> None:
        """A recommendation with no way to verify it is what failed the first time."""
        for path, text in self.copies.items():
            with self.subTest(path=path):
                self.assertShips(text, "Index Cond", path)
                self.assertShips(text, "Join Filter", path)

    def test_the_retracted_bare_column_advice_is_gone(self) -> None:
        """This exact sentence is what the failing trial followed."""
        retracted = "Create GIST indexes on production geometry columns"
        for path, text in self.copies.items():
            with self.subTest(path=path):
                self.assertRetracted(text, retracted, path)

    def test_geography_is_the_default_and_a_projected_crs_needs_a_known_extent(self) -> None:
        """The b65a2b4 standalone trial proposed an arbitrary UTM zone for data of unknown extent."""
        for path, text in self.copies.items():
            with self.subTest(path=path):
                self.assertShips(text, "For meter distances on lon/lat data in PostGIS, default to `geography`.", path)
                self.assertShips(text, "compute on the spheroid by default", path)
                self.assertShips(text, "Do not pick a UTM zone or national grid for an unknown or multi-zone extent.", path)
                self.assertRetracted(text, "use a suitable projected CRS or `geography`", path)
                # The default must not reach buffers: PostGIS buffers geography in a planar projection.
                self.assertShips(text, "`ST_Buffer` on `geography` is not geodesic.", path)
        skill = " ".join((SKILLS_ROOT / "spatial-sql/SKILL.md").read_text(encoding="utf-8").split())
        self.assertShips(skill, "For distance and within-distance tests on an unknown or wide extent", "skills/spatial-sql/SKILL.md")
        self.assertShips(skill, "do not guess a UTM zone or national grid", "skills/spatial-sql/SKILL.md")
        self.assertShips(skill, "A geography type does not make every operation geodesic", "skills/spatial-sql/SKILL.md")
        self.assertRetracted(skill, "For an unknown or wide extent, prefer the geodesic operation", "skills/spatial-sql/SKILL.md")


class AcceptanceGuidanceTests(GuidanceCase):
    """Content regressions only; native task-quality review remains a live gate."""

    def test_native_qgis_delivery_requires_save_reload_and_regeneration(self) -> None:
        for path, text in _shipped_copies("project-workflow.md").items():
            with self.subTest(path=path):
                self.assertShips(text, 'QgsProject.write("project.qgz")', path)
                self.assertShips(text, "check its return value", path)
                self.assertShips(text, "Reopen the saved project from another working directory", path)
                self.assertShips(text, "require a valid project CRS plus valid layers", path)
                self.assertShips(text, "serialized `./data/...` relative paths", path)
                self.assertShips(text, "canonical pipeline must rebuild the QGIS companion on every run", path)

    def test_geography_support_and_projected_input_are_not_confused(self) -> None:
        for path, text in _shipped_copies("spatial-sql.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "geodetic CRSs beyond EPSG:4326", path)
                self.assertShips(text, "ST_Transform(geom, 4326)::geography", path)
                self.assertRetracted(text, "rejects the cast for any SRID other than 4326", path)
                self.assertRetracted(text, "SRID 4326, only", path)

    def test_etak_license_filter_and_pin_corrections_ship_together(self) -> None:
        for path, text in _shipped_copies("data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "https://geoportaal.maaruum.ee/avaandmete-litsents", path)
                self.assertShips(text, "Do not substitute CC-BY for these terms", path)
                self.assertShips(text, "not a universal “real building” predicate", path)
                self.assertShips(text, "a registry linkage is not proof that a feature exists", path)
                self.assertShips(text, "actual bytes and SHA-256", path)
                self.assertRetracted(text, "Most data is open under CC-BY 4.0", path)
                self.assertRetracted(text, "require `ehr_gid IS NOT NULL` to drop", path)

    def test_licenses_are_read_from_the_provider_not_recorded_here(self) -> None:
        # A live discovery trial repeated two wrong footprint licenses from this
        # file. Licenses change per dataset, theme and release, so the guidance
        # now sends the agent to the provider's page instead of stating them.
        for path, text in _shipped_copies("data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "read the attribution page for the release you pin", path)
                self.assertShips(text, "Check the provider page for current coverage, formats, vintage and license.", path)
                self.assertRetracted(text, "Overture data is mostly CDLA-Permissive 2.0", path)
                self.assertRetracted(text, "Building Footprints** — global, public domain", path)

    def test_data_sources_records_entry_points_not_dataset_internals(self) -> None:
        # Formats, layer and field names, code values, counts and download
        # patterns change without notice and this file is not reviewed daily.
        # The maintainer's rule: point to catalogs and pages, verify live.
        for path, text in _shipped_copies("data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "This file lists entry points and methods, not dataset internals.", path)
                self.assertShips(text, "## Verify a source before relying on it", path)
                self.assertShips(text, "Do not claim lineage between datasets", path)
                for volatile in ("s3.pilw.io", "e_401_hoone_ka", "tyyp = 10", "`siht1`", "79,000", "351439"):
                    self.assertRetracted(text, volatile, path)

    def test_coded_selection_attributes_are_recorded_as_semantic_predicates(self) -> None:
        # A live material trial documented the zoning filter only as free-text
        # selection.filter and failed provenance.semantic_predicate_documented.
        for path, text in _shipped_copies("project-workflow.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "as `selection.semantic_predicates` (`field`, `domain_value`)", path)
                self.assertShips(text, "a free-text `selection.filter` does not document it", path)

    def test_project_skills_require_a_clean_validate_before_delivery(self) -> None:
        # Live material trials delivered projects that `openmapstack validate`
        # fails; the agent never ran it under "when the CLI is available".
        for name in ("open-map-stack", "reproducible-gis-project"):
            raw = (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")
            text = " ".join(raw.split())
            with self.subTest(skill=name):
                self.assertIn("python3 -m openmapstack --version", text)
                # A live 001 trial built dashboard layers with one-off scripts,
                # declared them as outputs, and failed its clean rerun.
                self.assertIn("openmapstack verify project.yaml --rerun", text)
                # At 861b58b no 001 agent ran it, and one hand-patched the run
                # pointer that the clean rerun then could not resolve.
                self.assertIn("Make that `verify --rerun` your last step and do not deliver until it passes", text)
                self.assertIn("a passing `validate` alone is not enough", text)
                self.assertIn("`runs.latest` and `project.status`, never a one-off script or a hand edit", text)
                # A source checkout may expose only the module form.
                self.assertIn("whichever form works for every", text)
                self.assertNotIn("path when the CLI\nis available", raw)

    def test_the_pipeline_owns_the_run_pointer(self) -> None:
        for path, text in _shipped_copies("project-spec.md").items():
            with self.subTest(path=path):
                self.assertShips(" ".join(text.split()),
                                 "updates `runs.latest` and `project.status` in `project.yaml` itself", path)
                self.assertShips(" ".join(text.split()), "Never patch them by hand", path)

    def test_qgis_layer_crs_must_match_its_data(self) -> None:
        # A live 001 map declared EPSG:3301 on WGS84 GeoJSON and drew nothing
        # where the analysis was.
        for name in ("qgis.md", "project-spec.md"):
            for path, text in _shipped_copies(name).items():
                with self.subTest(path=path):
                    self.assertShips(text, "**The declared CRS must also be the data's.**", path)
                    self.assertShips(text, "GeoJSON without a `crs` member is WGS84 (EPSG:4326, RFC 7946)", path)

    def test_qgis_layers_use_formats_every_build_reads(self) -> None:
        # A live 001 map on GeoParquet drew nothing in Ubuntu QGIS 3.40 / GDAL
        # 3.12, which the retracted "your GDAL is old" advice would not explain.
        for name in ("qgis.md", "project-spec.md"):
            for path, text in _shipped_copies(name).items():
                with self.subTest(path=path):
                    self.assertShips(text, "**Use formats every QGIS build reads:**", path)
                    self.assertShips(text, "Keep GeoParquet for analysis and export a QGIS-facing copy", path)
        for path, text in _shipped_copies("qgis.md").items():
            with self.subTest(path=path):
                self.assertRetracted(text, "your GDAL is old", path)

    def test_current_source_verification_is_not_inherited_from_reference_notes(self) -> None:
        text = (SKILLS_ROOT / "geospatial-data-discovery/SKILL.md").read_text()
        self.assertIn("pages during this task", text)
        self.assertIn("label the recommendation unverified", text)
        self.assertIn("support comparative claims with evidence", text)

    def test_projection_units_do_not_replace_operation_accuracy(self) -> None:
        for path, text in _shipped_copies("formats-and-crs.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "UTM is conformal, not equal-area", path)
                self.assertShips(text, "Equal-area does not mean distance-preserving", path)
                self.assertShips(text, "or explicit geodesic calculations", path)
                self.assertRetracted(text, "Metric computation:** ALWAYS", path)

    def test_parquet_guidance_requires_reopened_artifact_metadata(self) -> None:
        for path, text in _shipped_copies("formats-and-crs.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "does not by itself produce GeoParquet", path)
                self.assertShips(text, "Reopen the written artifact", path)
                self.assertShips(text, "including empty outputs", path)


class PaginationCompletenessGuidanceTests(GuidanceCase):
    """`bounded-discovery`: completeness comes from the paged total, not from hits."""

    def test_validation_checklist_rejects_a_bare_hits_request(self) -> None:
        for path, text in _shipped_copies("validation-and-ops.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "A separate hits/count request is not proof on its own", path)
                self.assertRetracted(text, "pagination or a hits/count request proves completeness", path)

    def test_wfs_paging_recipe_pages_to_the_reported_total(self) -> None:
        for path, text in _shipped_copies("data-sources.md").items():
            with self.subTest(path=path):
                for needle in ("sortBy", "startIndex", "numberMatched"):
                    self.assertShips(text, needle, path)
                self.assertShips(text, "Do not use `resultType=hits` as the completeness total", path)

    def test_the_worked_example_pages_the_way_the_guidance_says(self) -> None:
        """Guidance and the canonical example must not drift apart."""
        pipeline = (REPO_ROOT / "examples/tartu-development/pipeline.py").read_text(encoding="utf-8")
        self.assertIn("numberMatched", pipeline)
        self.assertIn("startIndex", pipeline)
        self.assertNotIn("resultType=hits", pipeline)


class VerifiedBackendGuidanceTests(GuidanceCase):
    """What the CLI can connect to, and what the shipped text claims, are one list.

    A backend the connector supports but the guidance still calls unsupported
    sends an agent off to the vendor CLI and an unpinned hand-copied file; a
    backend the guidance promises but the connector refuses fails at the first
    `source discover`. Either drift is silent, so the list is asserted from
    `connectors.BACKENDS` rather than transcribed.
    """

    def test_every_copy_names_the_backends_the_connector_actually_supports(self) -> None:
        from openmapstack.connectors import BACKENDS

        for path, text in _shipped_copies("user-data-sources.md").items():
            with self.subTest(path=path):
                for backend in BACKENDS:
                    self.assertShips(text, backend, path)

    def test_no_copy_still_calls_a_supported_backend_unsupported(self) -> None:
        from openmapstack.connectors import BACKENDS

        for path, text in _shipped_copies("user-data-sources.md").items():
            unsupported = text.split("## Other backends", 1)
            self.assertEqual(len(unsupported), 2, f"{path} no longer has an 'Other backends' section")
            listed = unsupported[1].split("For those:", 1)[0]
            for backend in BACKENDS:
                with self.subTest(path=path, backend=backend):
                    self.assertNotIn(
                        f"`{backend}`",
                        listed,
                        f"{path} lists the supported backend {backend!r} as one the CLI refuses",
                    )

    def test_the_metered_backend_ships_its_two_traps(self) -> None:
        """BigQuery's cost guard and its row-count caveat are the reasons this
        backend is not just 'another SQL database'."""
        for path, text in _shipped_copies("user-data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "max-scan-bytes", path)
                self.assertShips(text, "scan_limit_exceeded", path)
                self.assertShips(text, "is not what your reader can see", path)

    def test_the_scan_guard_admits_where_it_cannot_run(self) -> None:
        """Measured on the live service: BigQuery returns no byte estimate for
        a table under a row access policy. Guidance that promised the guard
        unconditionally would teach an agent to read a missing estimate as a
        cheap query."""
        for path, text in _shipped_copies("user-data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "The estimate is not always available", path)
                self.assertShips(text, "scan_estimated", path)
                self.assertShips(text, "Do not read a missing", path)

    def test_the_motherduck_confinement_limit_is_stated_not_implied_away(self) -> None:
        for path, text in _shipped_copies("user-data-sources.md").items():
            with self.subTest(path=path):
                self.assertShips(text, "a MotherDuck session needs network access", path)
                self.assertShips(text, "READ_ONLY", path)
                # `allowed_directories` alone does not confine anything; saying
                # so is what stops a reader assuming a fallback that is absent.
                self.assertShips(text, "does nothing without that switch", path)

    def test_the_query_policy_documents_what_it_refuses_to_read(self) -> None:
        """A query that names its own file or URL is the one shape that turns
        an approved snapshot into an exfiltration path on a backend with no
        session-level file confinement."""
        for path, text in _shipped_copies("user-data-sources.md").items():
            with self.subTest(path=path):
                for needle in ("read_csv()", "read_parquet()", "ST_Read()"):
                    self.assertShips(text, needle, path)
                self.assertShips(text, "relations the connector exposed", path)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
