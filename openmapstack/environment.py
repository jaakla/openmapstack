"""Plan Python dependency setup without changing the CLI's own environment."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name

from .project import ProjectError, get_in


NATIVE_SETUP = {
    "gdal": (
        "Install GDAL using your system package manager, Conda, or a container; "
        "verify gdalinfo --version and the drivers your pipeline needs. Python "
        "osgeo bindings also need a compatible libgdal and must be available to "
        "the pipeline interpreter; system Python bindings are not automatically "
        "visible inside uvx or the project virtualenv. Use runtime.implementation.command "
        "to run an already prepared GIS environment. See https://gdal.org/en/stable/download.html"
    ),
    "proj": (
        "Install PROJ and its required data/grids through your system package manager "
        "or a GIS environment, or declare pyproj under environment.packages if that "
        "is the API the pipeline uses. Verify the actual transformations and grids "
        "in the pipeline runtime. See https://proj.org/en/stable/install.html"
    ),
    "qgis": (
        "Install QGIS with its matching Python bindings and use runtime.implementation.command "
        "to select that runtime. A uvx environment does not provide PyQGIS. "
        "See https://qgis.org/download/"
    ),
    "postgis": (
        "Install PostGIS on the PostgreSQL server hosting the target database (or use "
        "a PostGIS-enabled service/container). Have the database administrator enable "
        "CREATE EXTENSION postgis in that database, then verify with "
        "SELECT PostGIS_Full_Version(). Installing a Python client or local psql "
        "does not install the server extension. See https://postgis.net/documentation/getting_started/"
    ),
    "postgresql": (
        "Provision a PostgreSQL server or use an existing service; verify connectivity "
        "and the server version in the target database. Installing a Python client "
        "does not provision a database. See https://www.postgresql.org/download/"
    ),
}
NATIVE_SETUP["postgres"] = NATIVE_SETUP["postgresql"]


def runtime_warnings(project: dict[str, Any]) -> list[dict[str, str]]:
    """Non-blocking runtime advisories, separate from artifact validation.

    Native declarations do not tell us which binary, binding, container, or
    remote server the pipeline actually uses. Do not invent successful checks
    from a similarly named executable on the CLI's PATH.
    """
    environment = get_in(project, "runtime", "environment", default={})
    if not isinstance(environment, dict):
        return []
    warnings = []
    implementation = get_in(project, "runtime", "implementation", default={})
    requested = environment.get("python")
    uses_cli_python = implementation.get("command") is None and Path(
        implementation.get("pipeline", "")
    ).suffix.lower() == ".py"
    if requested is not None and uses_cli_python:
        requested = str(requested).strip()
        constraint = re.sub(r"(?<=\.)[xX]$", "*", requested)
        if re.fullmatch(r"\d+(?:\.\d+)?", constraint):
            constraint += ".*"
        if not constraint.startswith(("<", ">", "=", "!", "~")):
            constraint = f"=={constraint}"
        actual = platform.python_version()
        try:
            matches = SpecifierSet(constraint).contains(actual, prereleases=True)
        except InvalidSpecifier:
            warnings.append({"code": "runtime.python_unverified", "message": (
                f"Cannot compare runtime.environment.python={requested!r} with Python {actual}. "
                "Use a version such as '3.12' or a version constraint such as '>=3.12,<3.13'."
            )})
        else:
            if not matches:
                warnings.append({"code": "runtime.python_mismatch", "message": (
                    f"project.yaml requests Python {requested}; the pipeline will use Python {actual}. "
                    "Select a matching interpreter with uvx --python VERSION openmapstack run project.yaml, "
                    "or use runtime.implementation.command to select your prepared runtime."
                )})
    for name, instructions in NATIVE_SETUP.items():
        if name not in environment:
            continue
        declaration = str(environment[name]).strip()
        if name == "gdal" and declaration.casefold() == "duckdb spatial extension":
            # Bundled GDAL is not a request for a system libgdal installation.
            # The canonical pipeline remains responsible for loading spatial.
            continue
        warnings.append({"code": f"runtime.{name}_unverified", "message": (
            f"Native dependency {name} ({declaration}) is not installed or verified automatically. "
            + instructions
        )})
    return warnings


def python_requirements(project: dict[str, Any]) -> list[str]:
    """Translate flat version pins and explicit PEP 508 packages to requirements.

    Python and native GIS tools remain descriptive metadata. Their installers
    and version schemes are not interchangeable with Python distributions.
    """
    environment = get_in(project, "runtime", "environment", default={})
    if not isinstance(environment, dict):
        raise ProjectError("runtime.environment must be a mapping")
    packages = environment.get("packages", [])
    if not isinstance(packages, list) or not all(isinstance(item, str) for item in packages):
        raise ProjectError("runtime.environment.packages must be a list of Python requirements")
    requirements = list(packages)
    for name, version in environment.items():
        if name in {"python", "packages", *NATIVE_SETUP}:
            continue
        version = str(version).strip()
        if version.upper().startswith("TODO") or version in {"", "None"}:
            raise ProjectError(f"runtime.environment.{name} needs a Python package version")
        # The shipped contract historically uses pins such as 1.2.x.
        version = re.sub(r"(?<=\.)[xX](?=$)", "*", version)
        constraint = version if version.startswith(("<", ">", "=", "!", "~")) else f"=={version}"
        requirements.append(f"{name}{constraint}")
    parsed = []
    for value in requirements:
        try:
            parsed.append(Requirement(value))
        except InvalidRequirement as exc:
            raise ProjectError(f"invalid Python requirement in runtime.environment: {value!r}") from exc
    # The canonical project scaffold reads and writes YAML. Keep that support
    # available when moving execution out of the CLI interpreter.
    if parsed and not any(canonicalize_name(req.name) == "pyyaml" for req in parsed):
        parsed.append(Requirement("PyYAML>=6,<7"))
    return sorted({str(req) for req in parsed})


@dataclass
class PythonEnvironment:
    directory: Path
    python: Path
    requirements: list[str]
    commands: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.directory), "requirements": self.requirements, "commands": self.commands}

    def subprocess_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["VIRTUAL_ENV"] = str(self.directory)
        environment["PATH"] = str(self.python.parent) + os.pathsep + environment.get("PATH", "")
        return environment


def plan_python_environment(project_file: Path, project: dict[str, Any]) -> PythonEnvironment | None:
    implementation = get_in(project, "runtime", "implementation", default={})
    if implementation.get("command") is not None:
        return None
    if Path(implementation.get("pipeline", "")).suffix.lower() != ".py":
        return None
    requirements = python_requirements(project)
    if not requirements:
        return None
    # A changed dependency set or base interpreter gets a fresh environment,
    # rather than leaving obsolete dependencies available to the pipeline.
    identity = json.dumps([sys.executable, sys.version, requirements]).encode()
    key = hashlib.sha256(identity).hexdigest()[:16]
    directory = project_file.parent / ".openmapstack" / "venvs" / key
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    commands = []
    if not python.is_file():
        commands.append(
            [uv, "venv", "--python", sys.executable, str(directory)] if uv
            else [sys.executable, "-m", "venv", str(directory)]
        )
    if uv:
        commands.append([uv, "pip", "install", "--python", str(python), *requirements])
    else:
        # Also repairs an environment originally created by uv without pip.
        commands.append([str(python), "-m", "ensurepip", "--upgrade"])
        commands.append([str(python), "-m", "pip", "install", "--disable-pip-version-check", *requirements])
    return PythonEnvironment(directory, python, requirements, commands)
