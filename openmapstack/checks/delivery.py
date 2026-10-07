"""Selected deliverables: declaration, input-bound evidence and runtime health."""

from __future__ import annotations

import json
import contextlib
import functools
import http.server
import threading
import zipfile
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from . import AssertionResult, failed, load_project_yaml, not_testable, passed, project_root
from .. import delivery
from ..project import project_path


def declaration_valid(workspace: Path, project_dir: str = ".") -> AssertionResult:
    project = load_project_yaml(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    errors = delivery.declaration_errors(project, project_root(workspace, project_dir))
    if errors:
        return failed("; ".join(errors), code="delivery_declaration_invalid", errors=errors)
    return passed("delivery declaration resolves" if "delivery" in project else "legacy v1 delivery semantics")


def selection_is(workspace: Path, kinds: list[str], project_dir: str = ".") -> AssertionResult:
    """Known requested delivery selection; useful for behavioral evals."""
    project = load_project_yaml(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    actual = [t.get("kind") for t in delivery.targets(project)]
    if "delivery" not in project or any(not isinstance(k, str) for k in actual) or sorted(actual) != sorted(kinds):
        return failed(f"requested {kinds}, declared {actual}", code="delivery_selection_mismatch")
    return passed(f"explicit selection matches {kinds}")


class _MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("id") == "openmapstack-view":
            if values.get("type") != "application/json":
                raise ValueError("openmapstack-view must be application/json")
            self.active = True
            self.payloads.append("")

    def handle_data(self, data: str) -> None:
        if self.active:
            self.payloads[-1] += data

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.active = False


def _embedded_metadata(path: Path, kind: str) -> dict:
    if kind == "qgis":
        from .qgis import _extract_qgs_xml
        xml = _extract_qgs_xml(path)
        if xml is None:
            raise ValueError("QGIS project contains no .qgs document")
        tree = ET.fromstring(xml)
        values = [p.get("value") for p in tree.findall(".//property")
                  if p.get("key") == "openmapstack.delivery"]
        values += [p.get("value") for p in tree.findall(".//customproperties//Option")
                   if p.get("name") == "openmapstack.delivery"]
        for variables in tree.findall(".//Variables"):
            names = [v.text for v in variables.findall("./variableNames/value")]
            records = [v.text for v in variables.findall("./variableValues/value")]
            values += [v for name, v in zip(names, records) if name == "openmapstack_delivery"]
    else:
        parser = _MetadataParser()
        parser.feed(path.read_text(encoding="utf-8"))
        values = parser.payloads
    if len(values) != 1:
        raise ValueError("view must embed exactly one OpenMapStack metadata record")
    return json.loads(values[0])


def evidence_matches(workspace: Path, target_id: str, project_dir: str = ".") -> AssertionResult:
    """Read back actual view metadata, receipt and input/output bytes.

    This proves binding and shared semantics, not the truth of a source or the
    correctness of every rendered pixel/metric. Runtime checks are separate.
    """
    project = load_project_yaml(workspace, project_dir)
    root = project_root(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    errors = delivery.declaration_errors(project, root)
    if errors:
        return failed("; ".join(errors), code="delivery_declaration_invalid")
    target = next((t for t in delivery.targets(project) if t["id"] == target_id), None)
    if target is None:
        return failed(f"unknown delivery target: {target_id}", code="delivery_target_unknown")
    for key in [target["output"], target["evidence"], *target["inputs"],
                *([target["build"]] if target.get("mode") == "hosted" else [])]:
        path = project_path(root, project["outputs"][key]["path"])
        if path is None or not path.is_file():
            return failed(f"missing target artifact/input: {key}", code="delivery_artifact_missing")
    path = project_path(root, project["outputs"][target["output"]]["path"])
    receipt = project_path(root, project["outputs"][target["evidence"]]["path"])
    try:
        expected = delivery.evidence_payload(root, project, target)
        actual = json.loads(receipt.read_text(encoding="utf-8"))
        embedded = _embedded_metadata(path, target["kind"])
    except (OSError, ValueError, TypeError, ET.ParseError, zipfile.BadZipFile) as exc:
        return failed(f"unreadable delivery evidence: {exc}", code="delivery_evidence_invalid")
    if actual != expected or embedded != expected["metadata"]:
        return failed("view metadata/evidence disagree with canonical inputs or semantics",
                      code="delivery_evidence_mismatch")
    return passed(f"{target_id}: view and evidence bind the declared analysis",
                  evidence={"view_sha256": expected["view_sha256"],
                            "analytical_outputs": expected["metadata"]["analytical_outputs"]})


@contextlib.contextmanager
def _serve_bundle(path: Path):
    """Module-based static apps require HTTP; bind only loopback, then stop."""
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass
    handler = functools.partial(QuietHandler, directory=str(path.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/{path.name}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def web_view_loads(workspace: Path, target_id: str, project_dir: str = ".") -> AssertionResult:
    """Smoke-test a local web deliverable without imposing dashboard tabs on BI."""
    project = load_project_yaml(workspace, project_dir)
    if project is None:
        return failed("project.yaml missing", code="manifest_missing")
    root = project_root(workspace, project_dir)
    errors = delivery.declaration_errors(project, root)
    if errors:
        return failed("; ".join(errors), code="delivery_declaration_invalid")
    target = next((t for t in delivery.targets(project) if t["id"] == target_id), None)
    if target is None or target["kind"] not in {"dashboard", "observable"}:
        return failed("target is not a selected web view", code="delivery_target_unknown")
    path = project_path(root, project["outputs"][target["output"]]["path"])
    if path is None or not path.is_file():
        return failed("selected web artifact missing", code="delivery_artifact_missing")
    from .visual import _playwright
    try:
        playwright = _playwright()
    except ImportError:
        return not_testable("Playwright unavailable", code="playwright_unavailable")
    with playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            return not_testable(f"Chromium unavailable: {exc}", code="browser_unavailable")
        try:
            page = browser.new_page()
            problems: list[str] = []
            page.on("pageerror", lambda error: problems.append(str(error)))
            page.on("console", lambda message: problems.append(message.text) if message.type == "error" else None)
            with contextlib.ExitStack() as stack:
                url = stack.enter_context(_serve_bundle(path)) if target["kind"] == "observable" else path.resolve().as_uri()
                page.goto(url, wait_until="networkidle", timeout=15000)
                text = page.locator("body").inner_text().strip()
                if not text:
                    problems.append("view has no visible content")
                # Generic metadata has a documented provenance surface shared by
                # all web targets, independently of their layout or chart library.
                if page.locator('[data-openmapstack-provenance]').count() != 1:
                    problems.append("view must expose one provenance surface")
                else:
                    provenance = page.locator('[data-openmapstack-provenance]')
                    if not provenance.is_visible():
                        problems.append("provenance is not visible")
                    visible = provenance.inner_text()
                    for source in (project.get("sources") or {}).values():
                        if str(source.get("provider", "")) not in visible:
                            problems.append("source provider missing from visible provenance")
                    for warning in project.get("warnings") or []:
                        statement = warning.get("statement")
                        if statement and str(statement) not in text:
                            problems.append("declared warning is not visible")
                if problems:
                    return failed("; ".join(problems), code="delivery_view_unhealthy")
                return passed(f"{target_id}: view loads and exposes provenance/warnings")
        except OSError as exc:
            return not_testable(f"local HTTP capability unavailable: {exc}", code="local_server_unavailable")
        except Exception as exc:
            return failed(f"view runtime failed: {exc}", code="delivery_view_unhealthy")
        finally:
            browser.close()
