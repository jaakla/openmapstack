"""Owner-definition drift and optional integration evidence, without runtimes."""

from pathlib import Path
import json

from . import AssertionResult, failed, passed, load_project_yaml, project_root
from ..integration import binding_errors, evidence_payload
from ..project import project_path


def bindings_valid(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """Each owned step and authoritative definition retains its declared identity."""
    project = load_project_yaml(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    errors = binding_errors(project_root(workspace, project_dir), project)
    if errors:
        return failed("; ".join(errors), code="integration_binding_invalid")
    return passed("owner bindings resolve" if "integrations" in project else "no integrations declared")


def evidence_matches(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """Receipts bind current definitions, immutable inputs, outputs and bundles."""
    project = load_project_yaml(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    root = project_root(workspace, project_dir)
    errors = binding_errors(root, project)
    if errors:
        return failed("; ".join(errors), code="integration_binding_invalid")
    if "integrations" not in project:
        return passed("no integrations declared")
    path = project_path(root, project["outputs"][project["integrations"]["evidence"]]["path"])
    try:
        if json.loads(path.read_text()) != evidence_payload(root, project):
            return failed("stale integration inputs/outputs/bundle", code="integration_evidence_mismatch")
    except (OSError, ValueError, TypeError) as exc:
        return failed(f"missing/unreadable integration evidence: {exc}", code="integration_artifact_missing")
    return passed("integration evidence binds current definitions, inputs, analytical outputs and bundles")
