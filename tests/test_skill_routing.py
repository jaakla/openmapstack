"""SKILL.md is a router, so its pointers must resolve (issue #26).

`SKILL.md` is the always-loaded entry point: consuming agents read it and then
open whichever `references/*.md` it names. A pointer to a file that does not
exist sends an agent looking for guidance that is not there, and a reference
nothing points at is guidance no agent will ever load. Neither shows up in the
fixture evals, which grade produced projects rather than the routing table.
"""

from __future__ import annotations

import re
import hashlib
import json
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills/open-map-stack"

#: References SKILL.md is not required to route to, with the reason why.
UNROUTED_ALLOWED: dict[str, str] = {}


def _routed_references(skill_text: str) -> set[str]:
    return set(re.findall(r"references/[A-Za-z0-9._-]+\.md", skill_text))


# Intentional additions to the frozen project-workflow contract. Each needs
# its own focused evidence: a guidance regression test plus the owning live case.
WORKFLOW_ADDITIONS = (
    # tests/test_guidance_regressions.py; live 001 semantic_predicate_documented.
    ", zoning",
    " Record every coded attribute the selection depends on as `selection.semantic_predicates`"
    " (`field`, `domain_value`) on the source it filters, even when the source is local and"
    " unfiltered on load; a free-text `selection.filter` does not document it.",
    # tests/test_guidance_regressions.py native QGIS delivery guidance;
    # tests/evals/test_qgis_assertions.py native CRS/relocation controls;
    # live 001 qgis.runtime_load, static_valid and reproducibility checks.
    "  * When PyQGIS is available, use `QgsProject.write(\"project.qgz\")` and check its return value."
    " Reopen the saved project from another working directory and require a valid project CRS plus valid layers."
    " Bare `data/...` strings in hand-written XML are not QGIS's serialized `./data/...` relative paths."
    " The canonical pipeline must rebuild the QGIS companion on every run; a one-off export is insufficient.\n",
)


class SkillRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.references = {
            f"references/{path.name}" for path in (SKILL_ROOT / "references").glob("*.md")
        }

    def test_every_reference_skill_md_names_exists(self) -> None:
        missing = sorted(
            name for name in _routed_references(self.skill) if not (SKILL_ROOT / name).is_file()
        )
        self.assertEqual(missing, [], f"SKILL.md routes to missing reference(s): {missing}")

    def test_every_reference_is_reachable_from_skill_md(self) -> None:
        """A reference no agent is told to read is dead weight in the payload."""
        unrouted = sorted(self.references - _routed_references(self.skill) - set(UNROUTED_ALLOWED))
        self.assertEqual(unrouted, [], f"reference(s) nothing in SKILL.md points at: {unrouted}")

    def test_project_workflow_preserves_the_frozen_contract_verbatim(self) -> None:
        """#33 moves normative guidance; a lost requirement is a regression.

        This guards the initial lossless extraction, not future wording changes.
        A later intentional behavior change must replace this migration guard
        with its own focused eval evidence instead of silently updating the hash.
        Intentional additions are listed in ``WORKFLOW_ADDITIONS`` with their
        evidence and removed before hashing, so the frozen text itself still
        cannot be lost or reworded.
        """
        baseline = json.loads((REPO_ROOT / "evals/baselines/pre-refactor.json").read_text())
        reference = (SKILL_ROOT / "references/project-workflow.md").read_text()
        body = reference.removeprefix("# Reproducible project-first workflow\n")
        for addition in WORKFLOW_ADDITIONS:
            self.assertIn(addition, body)
            body = body.replace(addition, "", 1)
        self.assertEqual(
            "sha256:" + hashlib.sha256(body.encode()).hexdigest(),
            baseline["skill"]["project_contract_body_sha256"],
        )
        self.assertIn("Before compiling or delivering a material analysis", self.skill)
        self.assertIn("references/project-workflow.md", self.skill)
        self.assertIn("references/project-spec.md", self.skill)

    def test_portolan_routes_to_the_reference_that_documents_it(self) -> None:
        """Consuming a Portolan catalog is discovery, so it must route like it.

        This is a routing assertion and nothing more: it proves an agent
        reaching for the term lands on the section, not that the section makes
        the agent behave. Eval case `017-portolan-catalog` grades the behaviour,
        by consequence, against a fixture catalog.
        """
        self.assertIn("Portolan", self.skill)
        data_sources = (SKILL_ROOT / "references/data-sources.md").read_text(encoding="utf-8")
        self.assertIn("## Portolan catalogs", data_sources)
        self.assertIn("AGENTS.md", data_sources)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
