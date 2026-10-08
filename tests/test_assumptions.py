from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from openmapstack.checks import project as project_checks
from openmapstack.schema import project_schema_errors
from openmapstack.validation import validate_project
from tests.test_cli import valid_manifest


class AssumptionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assert_contract(self, assumptions, *, schema_status="passed", status="passed", missing=False):
        project = valid_manifest()
        if missing:
            del project["interpretation"]["assumptions"]
        else:
            project["interpretation"]["assumptions"] = assumptions
        path = self.root / "project.yaml"
        path.write_text(yaml.safe_dump(project), encoding="utf-8")

        errors = project_schema_errors(project)
        self.assertEqual("failed" if errors else "passed", schema_status, errors)
        schema_check = project_checks.conforms_to_schema(self.root)
        self.assertEqual(schema_check.status, schema_status, schema_check.detail)
        rationale_check = project_checks.assumptions_have_rationale(self.root)
        self.assertEqual(rationale_check.status, status, rationale_check.detail)
        audit = validate_project(path, artifacts=False)
        checks = {check.id: check for check in audit.checks}
        self.assertEqual(checks["manifest.json_schema"].status, schema_status)
        self.assertEqual(checks["interpretation.assumptions"].status, status)
        return errors

    def test_complete_assumption_passes(self) -> None:
        self.assert_contract(valid_manifest()["interpretation"]["assumptions"])

    def test_each_core_field_is_required(self) -> None:
        for field in ("id", "statement", "rationale"):
            with self.subTest(field=field):
                assumption = valid_manifest()["interpretation"]["assumptions"][0]
                del assumption[field]
                errors = self.assert_contract([assumption], schema_status="failed", status="failed")
                self.assertTrue(any(field in error for error in errors), errors)

    def test_core_fields_are_nonblank_strings(self) -> None:
        for field in ("id", "statement", "rationale"):
            for value in (None, "", " \t\n", 17, True, ["text"], {"text": "value"}):
                with self.subTest(field=field, value=value):
                    assumption = valid_manifest()["interpretation"]["assumptions"][0]
                    assumption[field] = value
                    errors = self.assert_contract([assumption], schema_status="failed", status="failed")
                    self.assertTrue(any(f"interpretation.assumptions.0.{field}" in error for error in errors), errors)

    def test_malformed_container_or_item_fails_without_crashing(self) -> None:
        for assumptions in (None, "A1", 0, False, {}, [None], ["text"], [17], [[]]):
            with self.subTest(assumptions=assumptions):
                self.assert_contract(assumptions, schema_status="failed", status="failed")

    def test_missing_assumptions_fails(self) -> None:
        self.assert_contract(None, missing=True, schema_status="failed", status="failed")

    def test_empty_assumptions_is_schema_valid_but_warns(self) -> None:
        self.assert_contract([], status="warning")

    def test_duplicate_ids_fail_even_with_different_statements(self) -> None:
        assumptions = valid_manifest()["interpretation"]["assumptions"]
        assumptions.append({"id": "A1", "statement": "Different statement.", "rationale": "Different rationale."})
        self.assert_contract(assumptions, status="failed")

    def test_distinct_ids_pass(self) -> None:
        assumptions = valid_manifest()["interpretation"]["assumptions"]
        assumptions.append({"id": "A2", "statement": "Different statement.", "rationale": "Different rationale."})
        self.assert_contract(assumptions)

    def test_scope_metadata_has_no_builtin_reference_semantics(self) -> None:
        assumptions = valid_manifest()["interpretation"]["assumptions"]
        assumptions[0].update({"scope": "project-defined-scope", "x-example": {"reviewed": True}})
        # Scope metadata needs no glossary to satisfy the standard contract.
        self.assert_contract(assumptions)
        project = valid_manifest()
        project["interpretation"]["assumptions"] = assumptions
        project["interpretation"]["scopes"] = {"project-defined-scope": "Project metadata."}
        self.assertEqual(project_schema_errors(project), [])

    def test_extra_metadata_cannot_replace_required_rationale(self) -> None:
        assumption = {"id": "A1", "statement": "A statement.", "scope": "analysis", "reason": "Custom field."}
        self.assert_contract([assumption], schema_status="failed", status="failed")
