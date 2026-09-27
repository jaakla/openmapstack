from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

from openmapstack.cli import main
from openmapstack.collection import collection_skills, create_collection_snapshot
from openmapstack.snapshot import SnapshotError, create_skill_snapshot, inspect_skill_snapshot
from scripts.sync_skill_assets import sync

ROOT = Path(__file__).resolve().parents[1]


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        skills = []
        for name in ("generalist", "specialist"):
            path = self.source / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(f'---\nname: {name}\ndescription: Test skill\nmetadata:\n  version: "0.4.0"\n---\nRead references/rules.md\n')
            for directory, filename in (("references", "rules.md"), ("templates", "project.yaml"), ("examples", "worked.md"), ("agents", "openai.yaml")):
                (path / directory).mkdir()
                (path / directory / filename).write_text("content\n")
            skills.append({"name": name, "path": f"skills/{name}"})
        (self.source / "collection.json").write_text(json.dumps({"schema": "openmapstack-collection/v1", "version": "0.4.0", "skills": skills}))
        (self.source / "secret-eval-answer.md").write_text("must not install")

    def test_complete_subset_payload_and_legacy_are_distinct(self):
        output = self.root / "snapshot"
        manifest = create_collection_snapshot(self.source, output, selected=["specialist"])
        paths = {f["path"] for f in manifest["files"]}
        self.assertIn("skills/specialist/examples/worked.md", paths)
        self.assertIn("skills/specialist/agents/openai.yaml", paths)
        self.assertNotIn("secret-eval-answer.md", paths)
        self.assertFalse((output / "skills/generalist").exists())
        self.assertTrue(inspect_skill_snapshot(output)["intact"])
        legacy = self.root / "legacy"
        create_skill_snapshot(self.source / "skills/generalist", legacy)
        self.assertEqual(inspect_skill_snapshot(legacy)["schema"], "openmapstack-skill-snapshot-inspection/v1")
        self.assertFalse((legacy / "examples").exists())

    def test_individual_install_can_be_snapshotted_without_repository(self):
        copied = self.root / "standalone"
        shutil.copytree(self.source / "skills/specialist", copied)
        manifest = create_collection_snapshot(copied, self.root / "snapshot")
        self.assertEqual([s["name"] for s in manifest["skills"]], ["specialist"])

    def test_invalid_selection_versions_and_paths_are_refused(self):
        for selected in (["absent"], ["specialist", "specialist"], ["../generalist"]):
            with self.assertRaises(SnapshotError):
                create_collection_snapshot(self.source, self.root / "snapshot", selected=selected)
        path = self.source / "skills/specialist/SKILL.md"
        path.write_text(path.read_text().replace("0.4.0", "0.3.0"))
        with self.assertRaises(SnapshotError):
            collection_skills(self.source)

    def test_symlink_and_inside_source_destination_are_refused(self):
        with self.assertRaises(SnapshotError):
            create_collection_snapshot(self.source, self.source / "output")
        (self.source / "skills/generalist/references/escape.md").symlink_to("/etc/hostname")
        with self.assertRaises(SnapshotError):
            create_collection_snapshot(self.source, self.root / "snapshot")
        self.assertFalse((self.root / "snapshot").exists())

    def test_missing_changed_extra_and_metadata_tampering(self):
        for change in ("missing", "changed", "extra", "metadata", "escape", "duplicate", "symlink"):
            with self.subTest(change=change):
                output = self.root / change
                manifest = create_collection_snapshot(self.source, output)
                file = output / "skills/generalist/examples/worked.md"
                if change == "missing":
                    file.unlink()
                elif change == "changed":
                    file.write_text("wrong")
                elif change == "extra":
                    (output / "unexpected.txt").write_text("wrong")
                elif change == "metadata":
                    manifest["skills"][0]["description"] = "wrong"
                elif change == "escape":
                    manifest["files"][0]["path"] = "../outside"
                elif change == "duplicate":
                    manifest["files"].append(manifest["files"][0])
                else:
                    file.unlink()
                    file.symlink_to("/etc/hostname")
                (output / "snapshot.json").write_text(json.dumps(manifest))
                self.assertFalse(inspect_skill_snapshot(output)["intact"])

    def test_cli_auto_selects_v2_and_explicit_v1_stays_v1(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["skill-snapshot", "--source", str(self.source), "--out", str(self.root / "v2"), "--skill", "specialist", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["schema"], "openmapstack-skill-snapshot/v2")
        with redirect_stdout(io.StringIO()) as out:
            code = main(["skill-snapshot", "--source", str(self.source / "skills/generalist"), "--out", str(self.root / "v1"), "--format", "v1", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["schema"], "openmapstack-skill-snapshot/v1")


class InstalledPayloadTests(unittest.TestCase):
    def test_generated_resources_match_canonical_sources(self):
        self.assertEqual(sync(check=True), [])

    def test_copied_installs_have_reachable_local_resources(self):
        for name, (source, _) in collection_skills(ROOT).items():
            with self.subTest(skill=name), tempfile.TemporaryDirectory() as tmp:
                skill = Path(tmp) / name
                shutil.copytree(source, skill)
                if name in {"open-map-stack", "reproducible-gis-project"}:
                    self.assertTrue((skill / "templates/project.yaml").is_file())
                    self.assertTrue((skill / "examples/tartu-development/pipeline.py").is_file())
                    self.assertTrue((skill / "schemas/project-v1.schema.json").is_file())
                    self.assertTrue((skill / "references/installation.md").is_file())
                else:
                    for absent in ("templates", "examples", "schemas", "references/installation.md"):
                        self.assertFalse((skill / absent).exists(), f"{name}: unexpected {absent}")
                for path in [skill / "SKILL.md", *(skill / "references").glob("*.md")]:
                    text = path.read_text()
                    for link in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", text):
                        if "://" in link or link.startswith(("mailto:", "#")):
                            continue
                        target = (path.parent / link).resolve()
                        self.assertTrue(target.is_relative_to(skill), f"{path.name}: escapes installed skill: {link}")
                        self.assertTrue(target.exists(), f"{path.name}: missing local link {link}")
                    for relative in re.findall(r"`((?:references|templates|examples|schemas)/[a-zA-Z0-9_./-]+)`", text):
                        self.assertTrue((skill / relative).exists(), f"{name}/{path.name}: missing {relative}")
