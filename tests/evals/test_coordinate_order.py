"""Known asymmetric API-boundary pairs and healthy-control swap mutations."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from openmapstack import api
from openmapstack.checks import geodata
from tests.evals.helpers import EVALS_DIR, make_workspace
from tests.test_evals import AgentRunResult, eval_runner


class CoordinatePairTests(unittest.TestCase):
    def setUp(self):
        self.workspace = make_workspace()
        self.target = self.workspace / "coordinates.json"

    def check(self, value, **args):
        self.target.write_text(json.dumps({"point": {"coordinates": value}}), encoding="utf-8")
        return geodata.coordinate_pair_equals(
            self.workspace, path="coordinates.json", field="point.coordinates",
            equals=[24.7536, 59.4370], **args)

    def test_correct_asymmetric_point_passes_and_swap_fails(self):
        self.assertEqual(self.check([24.7536, 59.4370]).status, "passed")
        result = self.check([59.4370, 24.7536])
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.data["code"], "coordinate_pair_mismatch")

    def test_latitude_first_oracle_is_not_normalized(self):
        self.target.write_text(json.dumps({"leaflet": [59.4370, 24.7536]}), encoding="utf-8")
        result = geodata.coordinate_pair_equals(self.workspace, path="coordinates.json",
                                              field="leaflet", equals=[59.4370, 24.7536])
        self.assertEqual(result.status, "passed", result.detail)

    def test_invalid_pairs_cannot_pass(self):
        for value in (None, [], [24.7536], [24.7536, 59.4370, 0],
                      [True, 59.4370], ["24.7536", 59.4370],
                      [float("nan"), 59.4370], [float("inf"), 59.4370], [10**400, 59.4370]):
            with self.subTest(value=value):
                result = self.check(value)
                self.assertEqual(result.status, "failed", result.detail)
                self.assertEqual(result.data["code"], "coordinate_pair_invalid")

    def test_missing_field_and_non_mapping_payload_fail(self):
        for payload in ({}, [], {"point": []}):
            with self.subTest(payload=payload):
                self.target.write_text(json.dumps(payload), encoding="utf-8")
                result = geodata.coordinate_pair_equals(self.workspace, path="coordinates.json",
                    field="point.coordinates", equals=[24.7536, 59.4370])
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.data["code"], "coordinate_pair_invalid")

    def test_missing_and_malformed_artifacts_fail(self):
        args = {"path": "coordinates.json", "field": "point", "equals": [24.7536, 59.4370]}
        self.assertEqual(geodata.coordinate_pair_equals(self.workspace, **args).data["code"], "file_missing")
        self.target.write_text("{", encoding="utf-8")
        self.assertEqual(geodata.coordinate_pair_equals(self.workspace, **args).data["code"], "json_invalid")

    def test_numeric_tolerance_is_absolute_and_explicit(self):
        self.assertEqual(self.check([24.7536 + 1e-8, 59.4370]).status, "passed")
        self.assertEqual(self.check([24.7536 + 1e-5, 59.4370]).status, "failed")
        self.assertEqual(self.check([24.7536 + 1e-8, 59.4370], tolerance=0).status, "failed")

    def test_invalid_oracle_is_setup_error(self):
        for equals, tolerance in (([24.7536], 0), ([True, 59.4370], 0),
                                  ([24.7536, 59.4370], -1), ([24.7536, 59.4370], float("nan"))):
            with self.subTest(equals=equals, tolerance=tolerance), self.assertRaises(ValueError):
                geodata.coordinate_pair_equals(self.workspace, path="coordinates.json", field="point",
                                               equals=equals, tolerance=tolerance)

    def test_unreadable_artifact_is_not_testable(self):
        self.target.write_text("{}", encoding="utf-8")
        with patch("pathlib.Path.read_text", side_effect=OSError("denied")):
            result = geodata.coordinate_pair_equals(self.workspace, path="coordinates.json", field="point",
                                                   equals=[24.7536, 59.4370])
        self.assertEqual(result.status, "not_testable")
        self.assertEqual(result.data["code"], "read_error")

    def test_public_api_marks_check_as_requiring_an_oracle(self):
        self.assertFalse(api.describe_check("geodata.coordinate_pair_equals").oracle_free)


class CoordinateOrderCaseTests(unittest.TestCase):
    def test_live_grader_rejects_each_swap_from_a_stub_agent(self):
        # This proves live-mode grading and fixture delivery, not model behavior.
        for swap in (None, "geojson", "maplibre", "leaflet_latlng", "leaflet_geojson"):
            with self.subTest(swap=swap):
                class StubAdapter:
                    executable = "test-stub"

                    @staticmethod
                    def is_available():
                        return True

                    @staticmethod
                    def run(prompt, workspace, **kwargs):
                        if "Leaflet" not in prompt or "MapLibre" not in prompt:
                            raise AssertionError("live task must ask for distinct API boundaries")
                        anchor = json.loads((workspace / "data/source/anchor.json").read_text())
                        lon, lat = anchor["longitude"], anchor["latitude"]
                        pairs = {"geojson": [lon, lat], "maplibre": [lon, lat],
                                 "leaflet_latlng": [lat, lon], "leaflet_geojson": [lon, lat]}
                        if swap:
                            pairs[swap].reverse()
                        payload = {key: {"type": "Point", "coordinates": pair}
                                   if key.endswith("geojson") else pair for key, pair in pairs.items()}
                        (workspace / "coordinate-payloads.json").write_text(json.dumps(payload))
                        return AgentRunResult(agent="codex", model="test-stub", workspace=workspace,
                            duration_s=0.0, success=True, returncode=0, command=["test-stub"],
                            metadata={"structured_completion": True})

                with patch.object(eval_runner, "_load_adapter", return_value=StubAdapter()):
                    result = eval_runner.run_case(EVALS_DIR / "cases/018-coordinate-order-boundaries",
                        "live", agent_override="codex", model="test-stub", skill_mode="enabled")
                self.assertEqual(result["status"], "assertions_failed" if swap else "passed", result)
                failures = [item for item in result["assertions"] if not item["matched_expectation"]]
                self.assertEqual(len(failures), 1 if swap else 0)
                if swap:
                    self.assertEqual(failures[0]["actual_code"], "coordinate_pair_mismatch")

    def test_reference_fixture_passes_without_a_spatial_runtime(self):
        with patch.object(geodata, "_connect", side_effect=AssertionError("no spatial runtime needed")):
            result = eval_runner.run_case(EVALS_DIR / "cases/018-coordinate-order-boundaries", "fixture")
        self.assertEqual(result["status"], "passed", result)
        self.assertEqual(len(result["assertions"]), 5)  # Four interfaces plus input byte identity.

    def test_each_single_interface_swap_is_detected_against_a_healthy_twin(self):
        for case_id in ("929-geojson-coordinate-swapped", "930-maplibre-coordinate-swapped",
                        "931-leaflet-latlng-coordinate-swapped", "932-leaflet-geojson-coordinate-swapped"):
            with self.subTest(case=case_id):
                result = eval_runner.run_case(EVALS_DIR / "cases" / case_id, "fixture")
                self.assertEqual(result["status"], "passed", result)
                target = next(item for item in result["assertions"] if item["mutation_role"] == "target")
                self.assertEqual(target["actual_status"], "failed")
                self.assertEqual(target["data"]["code"], "coordinate_pair_mismatch")
                self.assertTrue(target["matched_expectation"])
                self.assertTrue(all(item["actual_status"] == "passed" for item in
                                    result["mutation_analysis"]["control"]["assertions"]))
