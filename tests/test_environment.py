from __future__ import annotations

import io
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from openmapstack.cli import main
from openmapstack.environment import plan_python_environment, python_requirements, runtime_warnings
from openmapstack.project import ProjectError
from tests.test_cli import PIPELINE, valid_manifest

import yaml


class PythonEnvironmentTests(unittest.TestCase):
    def project(self, **environment):
        project = valid_manifest()
        project["runtime"]["environment"] = environment
        return project

    def test_flat_pins_ranges_and_native_metadata(self):
        requirements = python_requirements(self.project(
            python="3.12", duckdb="1.2.x", shapely=">=2,<3",
            gdal="DuckDB spatial extension", proj="pyproj 3.7.2", qgis="system install",
        ))
        self.assertEqual(requirements, ["PyYAML<7,>=6", "duckdb==1.2.*", "shapely<3,>=2"])

    def test_explicit_requirements_support_extras_markers_and_urls(self):
        packages = ["psycopg[binary]>=3", "pyproj==3.7.2; python_version >= '3.11'",
                    "local-package @ file:///tmp/local_package-1.0-py3-none-any.whl", "PyYAML==6.0.2"]
        requirements = python_requirements(self.project(packages=packages))
        self.assertEqual(len(requirements), 4)
        self.assertIn("PyYAML==6.0.2", requirements)
        self.assertIn("psycopg[binary]>=3", requirements)

    def test_invalid_dependencies_are_errors(self):
        for environment in ({"duckdb": "TODO"}, {"duckdb": "latest release"},
                            {"packages": "duckdb"}, {"packages": [3]},
                            {"packages": ["--index-url=https://example.com"]}):
            with self.subTest(environment=environment), self.assertRaises(ProjectError):
                python_requirements(self.project(**environment))

    def test_no_packages_keeps_existing_interpreter(self):
        self.assertIsNone(plan_python_environment(Path("/project/project.yaml"), self.project(python="3.12")))

    @patch("openmapstack.environment.platform.python_version", return_value="3.12.4")
    def test_python_versions_match_at_the_declared_precision(self, _version):
        for version in ("3", "3.12", "3.12.4", "3.12.x", ">=3.12,<3.13", "==3.12.*"):
            with self.subTest(version=version):
                self.assertEqual(runtime_warnings(self.project(python=version)), [])

    @patch("openmapstack.environment.platform.python_version", return_value="3.13.1")
    def test_python_mismatch_warns_with_a_remedy(self, _version):
        for version in ("3.12", "3.13.2", "<3.13"):
            with self.subTest(version=version):
                warnings = runtime_warnings(self.project(python=version))
                self.assertEqual(warnings[0]["code"], "runtime.python_mismatch")
                self.assertIn("3.13.1", warnings[0]["message"])
                self.assertIn("uvx --python", warnings[0]["message"])

    def test_unknown_python_version_is_unverified(self):
        warnings = runtime_warnings(self.project(python="TODO"))
        self.assertEqual(warnings[0]["code"], "runtime.python_unverified")

    def test_external_commands_do_not_compare_against_cli_python(self):
        project = self.project(python="0.0")
        project["runtime"]["implementation"]["command"] = ["conda", "run", "python", "pipeline.py"]
        self.assertEqual(runtime_warnings(project), [])
        project["runtime"]["implementation"] = {"pipeline": "pipeline.sh"}
        self.assertEqual(runtime_warnings(project), [])

    def test_native_packages_are_not_sent_to_pip(self):
        project = self.project(postgis="3.5", postgresql="17", gdal="3.10", duckdb="1.5.5")
        self.assertEqual(python_requirements(project), ["PyYAML<7,>=6", "duckdb==1.5.5"])
        warnings = {item["code"]: item["message"] for item in runtime_warnings(project)}
        self.assertIn("target database", warnings["runtime.postgis_unverified"])
        self.assertIn("SELECT PostGIS_Full_Version()", warnings["runtime.postgis_unverified"])
        self.assertIn("system package manager", warnings["runtime.gdal_unverified"])
        self.assertIn("not automatically visible", warnings["runtime.gdal_unverified"])
        self.assertIn("server version", warnings["runtime.postgresql_unverified"])

    def test_bundled_gdal_does_not_request_system_install(self):
        self.assertEqual(runtime_warnings(self.project(gdal="DuckDB spatial extension")), [])

    def test_native_guidance_does_not_claim_to_check_an_external_runtime(self):
        project = self.project(qgis="3.44", proj="9.4")
        project["runtime"]["implementation"]["command"] = ["conda", "run", "python", "pipeline.py"]
        for warning in runtime_warnings(project):
            self.assertIn("not installed or verified automatically", warning["message"])

    def test_custom_commands_and_non_python_pipelines_manage_their_own_environment(self):
        for implementation in ({"command": ["conda", "run", "python", "pipeline.py"]},
                               {"pipeline": "pipeline.sh"}):
            project = self.project(duckdb="managed externally")
            project["runtime"]["implementation"] = implementation
            self.assertIsNone(plan_python_environment(Path("/project/project.yaml"), project))

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    def test_uv_installs_into_project_environment_not_uvx_environment(self, _which):
        with tempfile.TemporaryDirectory() as temporary:
            project_file = Path(temporary) / "project.yaml"
            project = self.project(duckdb="1.2.2")
            with patch("openmapstack.environment.sys.executable", "/uv/cache/bin/python"):
                plan = plan_python_environment(project_file, project)
            self.assertTrue(plan.directory.is_relative_to(project_file.parent))
            self.assertEqual(plan.commands[0], ["/tools/uv", "venv", "--python", "/uv/cache/bin/python", str(plan.directory)])
            self.assertEqual(plan.commands[1][:5], ["/tools/uv", "pip", "install", "--python", str(plan.python)])
            self.assertIn("duckdb==1.2.2", plan.commands[1])
            child_env = plan.subprocess_environment()
            self.assertEqual(child_env["VIRTUAL_ENV"], str(plan.directory))
            self.assertEqual(child_env["PATH"].split(os.pathsep)[0], str(plan.python.parent))
            self.assertFalse(plan.directory.exists())

    @patch("openmapstack.environment.shutil.which", return_value=None)
    def test_without_uv_uses_venv_and_its_pip(self, _which):
        with tempfile.TemporaryDirectory() as temporary:
            plan = plan_python_environment(Path(temporary) / "project.yaml", self.project(duckdb="1.2.2"))
            self.assertEqual(plan.commands[0], [sys.executable, "-m", "venv", str(plan.directory)])
            self.assertEqual(plan.commands[-1][:4], [str(plan.python), "-m", "pip", "install"])

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    def test_reuse_and_changed_requirements(self, _which):
        with tempfile.TemporaryDirectory() as temporary:
            project_file = Path(temporary) / "project.yaml"
            project = self.project(duckdb="1.2.2")
            first = plan_python_environment(project_file, project)
            first.python.parent.mkdir(parents=True)
            first.python.touch()
            reused = plan_python_environment(project_file, project)
            self.assertEqual(first.python, reused.python)
            self.assertEqual(len(reused.commands), 1)
            changed = plan_python_environment(project_file, self.project(duckdb="1.3.2"))
            self.assertNotEqual(first.python, changed.python)


class RunEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "project.yaml"
        self.project = valid_manifest()
        self.project["runtime"]["environment"]["duckdb"] = "1.2.2"
        (self.root / "pipeline.py").write_text(PIPELINE)
        (self.root / "README.md").write_text("# Test project\n")

    def run_cli(self, *args):
        self.path.write_text(yaml.safe_dump(self.project))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = main(["run", str(self.path), "--json", *args])
        return code, json.loads(stdout.getvalue())

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    @patch("openmapstack.cli.subprocess.run")
    def test_dry_run_shows_setup_without_creating_or_installing(self, run, _which):
        code, payload = self.run_cli("--dry-run", "--pipeline-arg=--example")
        self.assertEqual(code, 0)
        self.assertIn("duckdb==1.2.2", payload["environment"]["requirements"])
        self.assertEqual(payload["command"][-1], "--example")
        self.assertIn(".openmapstack", payload["command"][0])
        self.assertFalse((self.root / ".openmapstack").exists())
        run.assert_not_called()

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    @patch("openmapstack.cli.subprocess.run")
    def test_setup_failure_stops_pipeline_and_keeps_json_clean(self, run, _which):
        run.side_effect = [subprocess.CompletedProcess([], 0, "", "created"),
                           subprocess.CompletedProcess([], 1, "installer output", "no matching distribution")]
        code, payload = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(payload["phase"], "environment")
        self.assertEqual(payload["stderr"], "no matching distribution")
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs["capture_output"] for call in run.call_args_list))

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    @patch("openmapstack.cli.subprocess.run", side_effect=OSError("installer missing"))
    def test_setup_start_failure_is_reported(self, run, _which):
        code, payload = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(payload["phase"], "environment")
        self.assertIn("installer missing", payload["stderr"])

    @patch("openmapstack.environment.shutil.which", return_value="/tools/uv")
    @patch("openmapstack.cli.subprocess.run")
    def test_prepared_interpreter_runs_with_sampling_bindings_and_arguments(self, run, _which):
        self.project["runtime"]["environment"]["python"] = "0.0"
        self.project["runtime"]["implementation"]["parameters"] = [{
            "id": "rows", "type": "integer", "canonical": 0,
            "role": "sample_rows", "sample": 5, "binding": {"environment": "OMS_SAMPLE_ROWS"},
        }]
        run.side_effect = [subprocess.CompletedProcess([], 0, "", "created"),
                           subprocess.CompletedProcess([], 0, "", "installed"),
                           subprocess.CompletedProcess([], 7, "pipeline stdout", "pipeline stderr")]
        code, payload = self.run_cli("--sample", "--pipeline-arg=--example")
        self.assertEqual(code, 1)
        self.assertEqual(payload["phase"], "execute")
        self.assertEqual(payload["warnings"][0]["code"], "runtime.python_mismatch")
        call = run.call_args_list[-1]
        self.assertEqual(call.args[0][1:], ["pipeline.py", "--example"])
        self.assertIn(".openmapstack", call.args[0][0])
        self.assertEqual(call.kwargs["env"]["OMS_SAMPLE_ROWS"], "5")
        self.assertEqual(call.kwargs["env"]["OPENMAPSTACK_RUN_MODE"], "sampled")
        self.assertIn(".openmapstack", call.kwargs["env"]["VIRTUAL_ENV"])

    def test_explicit_command_bypasses_dependency_setup(self):
        self.project["runtime"]["implementation"]["command"] = [sys.executable, "pipeline.py"]
        code, payload = self.run_cli()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["phase"], "complete")
        self.assertFalse((self.root / ".openmapstack").exists())

    @patch("openmapstack.cli.subprocess.run")
    def test_invalid_package_list_fails_preflight_without_installing(self, run):
        self.project["runtime"]["environment"]["packages"] = "duckdb"
        code, payload = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual(payload["phase"], "preflight")
        run.assert_not_called()

    @patch("openmapstack.environment.platform.python_version", return_value="3.13.1")
    def test_runtime_warnings_in_json_do_not_block_a_run(self, _version):
        self.project["runtime"]["environment"] = {"python": "3.12", "postgis": "3.5"}
        code, payload = self.run_cli()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["phase"], "complete")
        self.assertEqual({item["code"] for item in payload["warnings"]}, {
            "runtime.python_mismatch", "runtime.postgis_unverified",
        })

    @patch("openmapstack.environment.platform.python_version", return_value="3.13.1")
    def test_dry_run_prints_actionable_warnings_without_setup(self, _version):
        self.project["runtime"]["environment"] = {"python": "3.12", "gdal": "3.10"}
        self.path.write_text(yaml.safe_dump(self.project))
        stderr = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = main(["run", str(self.path), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("runtime.python_mismatch", stderr.getvalue())
        self.assertIn("gdalinfo --version", stderr.getvalue())
        self.assertFalse((self.root / ".openmapstack").exists())

    @unittest.skipUnless(shutil.which("uv"), "offline environment integration needs uv")
    def test_real_install_from_local_wheels_and_repeat_run(self):
        # Build a tiny wheel for a module unavailable to the CLI interpreter.
        # Repackage the installed PyYAML distribution so this is fully offline.
        package = "oms_environment_test_dependency"
        wheel = self.root / f"{package}-1.0-py3-none-any.whl"
        info = f"{package}-1.0.dist-info"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(f"{package}.py", 'VALUE = "installed in project"\n')
            archive.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {package}\nVersion: 1.0\n")
            archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(f"{info}/RECORD", "")
        distribution = importlib.metadata.distribution("PyYAML")
        # Only Python sources are needed: PyYAML has a pure-Python fallback.
        yaml_wheel = self.root / f"pyyaml-{distribution.version}-py3-none-any.whl"
        yaml_info = f"pyyaml-{distribution.version}.dist-info"
        with zipfile.ZipFile(yaml_wheel, "w") as archive:
            yaml_source = Path(yaml.__file__).parent
            for file in yaml_source.rglob("*.py"):
                archive.write(file, str(Path("yaml") / file.relative_to(yaml_source)))
            archive.writestr(f"{yaml_info}/METADATA", f"Metadata-Version: 2.1\nName: PyYAML\nVersion: {distribution.version}\n")
            archive.writestr(f"{yaml_info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr(f"{yaml_info}/RECORD", "")
        self.project["runtime"]["environment"] = {"packages": [
            f"{package} @ {wheel.as_uri()}", f"PyYAML @ {yaml_wheel.as_uri()}",
        ]}
        (self.root / "pipeline.py").write_text(
            f'import {package}\nassert {package}.VALUE == "installed in project"\n' + PIPELINE
        )
        control = subprocess.run([sys.executable, "-c", f"import {package}"], capture_output=True)
        self.assertNotEqual(control.returncode, 0)
        with patch.dict(os.environ, {"UV_OFFLINE": "1", "PIP_NO_INDEX": "1"}):
            code, first = self.run_cli()
            self.assertEqual(code, 0, first)
            code, second = self.run_cli()
            self.assertEqual(code, 0, second)
        self.assertEqual(first["command"], second["command"])
        self.assertEqual(second["phase"], "complete")
        control = subprocess.run([sys.executable, "-c", f"import {package}"], capture_output=True)
        self.assertNotEqual(control.returncode, 0, "setup must not mutate the CLI interpreter")


if __name__ == "__main__":
    unittest.main()
