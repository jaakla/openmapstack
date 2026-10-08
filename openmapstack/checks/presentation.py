"""Presentation-contract assertions: semantic roles, layer groups, controls parity.

See references/project-spec.md sections 2.7 and 3.
"""

from __future__ import annotations

from pathlib import Path

from . import AssertionResult, failed, get_in, load_project_yaml, not_testable, passed, project_root, warning

SEMANTIC_ROLES = {
    "primary_result", "secondary_result", "source", "context", "constraint",
    "excluded_area", "warning", "user_override", "planned", "hypothetical",
    "selected_feature",
}


def layers_reference_outputs(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """Static presentation lineage; no QGIS/browser runtime is required."""
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    # These helpers resolve format variants and override geometry from manifest
    # data only. They neither import PyQGIS nor open a desktop project.
    from .qgis import _manifest_layer_files, _acceptable_files, _is_client_local
    resolved = _manifest_layer_files(proj)
    errors = []
    for layer in get_in(proj, "presentation.map.layers", []) or []:
        if not isinstance(layer, dict):
            errors.append("layer must be a mapping")
        elif not _is_client_local(layer) and not _acceptable_files(resolved, layer.get("source")):
            errors.append(f"layer source {layer.get('source')!r} names no output or override geometry")
    if errors:
        return failed("; ".join(errors), code="presentation_source_unresolved")
    return passed("all persistent presentation layers resolve to outputs or override geometry")


def layers_use_semantic_roles(workspace: Path, project_dir: str = ".") -> AssertionResult:
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    layers = get_in(proj, "presentation.map.layers", []) or []
    if not layers:
        return warning("no layers declared under presentation.map.layers", code="no_layers_declared")
    missing = [layer.get("source", "?") for layer in layers if not layer.get("semantic_role")]
    if missing:
        return failed(f"layers missing semantic_role: {missing}", code="semantic_role_missing")
    return passed(f"all {len(layers)} layers declare a semantic_role")


def required_layer_groups_exist(workspace: Path, groups: list[str], project_dir: str = ".") -> AssertionResult:
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    declared = {g.get("id") for g in get_in(proj, "presentation.map.layer_groups", []) or []}
    missing = [g for g in groups if g not in declared]
    if missing:
        return failed(f"missing required layer groups: {missing}", code="layer_group_missing")
    return passed(f"all required layer groups present: {groups}")


def controls_match_pipeline(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """presentation.controls.filters[].canonical must equal the threshold the
    pipeline actually used for the equivalent processing.steps parameter, and
    presentation.controls.scenarios[].override must reference a real
    override id. A drift here means the view can misrepresent the run."""
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")

    override_ids = {o.get("id") for o in (proj.get("overrides") or [])}
    scenarios = get_in(proj, "presentation.controls.scenarios", []) or []
    errors: list[str] = []
    for s in scenarios:
        if s.get("override") not in override_ids:
            errors.append(f"scenario {s.get('id')} references unknown override {s.get('override')!r}")

    steps = get_in(proj, "processing.steps", []) or []
    filters = get_in(proj, "presentation.controls.filters", []) or []
    for f in filters:
        field = f.get("field")
        canonical = f.get("canonical")
        if field is None or canonical is None:
            continue
        # Look for a step expression mentioning this field and the canonical value.
        matching_steps = [
            s for s in steps
            if field in str(s.get("expression", "")) or field == s.get("output_field")
        ]
        if not matching_steps:
            continue  # not every control necessarily maps 1:1 to a single step; skip silently
        # A multi_select control's canonical position is a list of values, and
        # each must appear in the rule the pipeline ran. Stringifying the whole
        # list and substring-searching the SQL can never match.
        expressions = [str(s.get("expression", "")) for s in matching_steps]
        wanted = canonical if isinstance(canonical, list) else [canonical]
        absent = [v for v in wanted if not any(str(v) in e for e in expressions)]
        if absent:
            errors.append(
                f"control {f.get('id')} canonical value(s) {absent!r} not found in matching step expression(s)"
            )

    # View switches between variants the pipeline already measured (travel
    # mode, analysis year, metric). They change which precomputed columns the
    # view shows, never the analysis, so they reference outputs, not overrides.
    outputs = proj.get("outputs") or {}
    views = get_in(proj, "presentation.controls.views", []) or []
    for v in views:
        options = v.get("options") or []
        if not options or len(set(map(str, options))) != len(options):
            errors.append(f"view {v.get('id')} needs a non-empty list of distinct options")
            continue
        if v.get("canonical") not in options:
            errors.append(f"view {v.get('id')} canonical {v.get('canonical')!r} is not one of its options")
        fields = v.get("fields") or {}
        output = outputs.get(v.get("output")) if v.get("output") else None
        if fields and v.get("output") and not isinstance(output, dict):
            errors.append(f"view {v.get('id')} references unknown output {v.get('output')!r}")
            continue
        declared = {c.get("name") if isinstance(c, dict) else c for c in get_in(output or {}, "table.columns", []) or []}
        for option in options:
            if fields and str(option) not in {str(k) for k in fields}:
                errors.append(f"view {v.get('id')} option {option!r} has no fields mapping")
            for column in (fields.get(option) or fields.get(str(option)) or []) if fields else []:
                if declared and column not in declared:
                    errors.append(f"view {v.get('id')} option {option!r} shows undeclared column {column!r}")

    if errors:
        return failed("; ".join(errors), errors=errors, code="control_pipeline_drift")
    if views:
        return passed(f"{len(filters)} filter control(s), {len(scenarios)} scenario control(s) and {len(views)} view control(s) consistent")
    return passed(f"{len(filters)} filter control(s) and {len(scenarios)} scenario control(s) consistent")


def tables_reference_table_outputs(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """Every presentation.tables entry shows a declared table output and offers only its declared downloads."""
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    tables = get_in(proj, "presentation.tables", []) or []
    if not tables:
        return passed("no tables declared in the view (vacuously true)")
    outputs = proj.get("outputs") or {}
    errors: list[str] = []
    for entry in tables:
        name = entry.get("output") if isinstance(entry, dict) else None
        output = outputs.get(name) if name else None
        if not isinstance(output, dict):
            errors.append(f"table view references unknown output {name!r}")
            continue
        if _output_kind(output) != "table":
            errors.append(f"table view {name!r} shows an output that is not kind: table")
        offered = set(entry.get("downloads") or output.get("downloads") or [])
        undeclared = sorted(offered - set(output.get("downloads") or []))
        if undeclared:
            errors.append(f"table view {name!r} offers downloads the output does not declare: {undeclared}")
    if errors:
        return failed("; ".join(errors), errors=errors, code="table_view_drift")
    return passed(f"{len(tables)} table view(s) show declared table outputs")


def table_downloads_linked(workspace: Path, project_dir: str = ".", dashboard: str | None = None,
                           allow_identical_copies: bool = False) -> AssertionResult:
    """The delivered dashboard links every download file of every table it shows.

    The links must point at the files the pipeline wrote (hashed in the run
    record), not at data regenerated in the browser. Static app builds may opt
    into project-local bundled copies only when bytes match the declared file."""
    import os
    import re

    from .tables import download_paths

    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    tables = get_in(proj, "presentation.tables", []) or []
    if not tables:
        return passed("no tables declared in the view (vacuously true)")
    root = project_root(workspace, project_dir)
    outputs = proj.get("outputs") or {}
    # The dashboard first: a report declared before it must not stand in for
    # the view that shows the tables.
    candidates = [dashboard] if dashboard else ["dashboard.html"] + [
        o.get("path") for o in outputs.values() if isinstance(o, dict) and str(o.get("path", "")).lower().endswith((".html", ".htm"))
    ]
    page = next((root / c for c in candidates if c and (root / c).is_file()), None)
    if page is None:
        return not_testable("no dashboard HTML found to inspect", code="dashboard_missing")
    html = page.read_text(encoding="utf-8", errors="replace")
    hrefs = {h.strip().removeprefix("./") for h in re.findall(r"""href\s*=\s*["']([^"'#?]+)""", html)}
    missing: list[str] = []
    for entry in tables:
        output = outputs.get(entry.get("output")) if isinstance(entry, dict) else None
        if not isinstance(output, dict):
            continue
        formats = entry.get("downloads") or output.get("downloads") or []
        for relative in download_paths(output.get("path", ""), formats).values():
            expected = Path(os.path.relpath(root / relative, page.parent)).as_posix()
            if expected not in hrefs:
                matches = False
                if allow_identical_copies:
                    from ..integrity import sha256_file
                    from ..project import project_path
                    original = project_path(root, relative)
                    if original is not None and original.is_file():
                        for href in hrefs:
                            if ":" in href or href.startswith("/"):
                                continue
                            alias = project_path(root, str(page.parent.relative_to(root) / href))
                            if alias is not None and alias.is_file() and sha256_file(alias) == sha256_file(original):
                                matches = True
                                break
                if not matches:
                    missing.append(relative)
    if missing:
        return failed(f"{page.name} does not link downloads {missing}", code="table_download_unlinked", missing=missing)
    return passed(f"{page.name} links every declared table download")


def _output_kind(output: dict) -> str:
    """The declared ``outputs.<name>.kind``; undeclared outputs stay ``geodata``.

    Only an explicit declaration changes how an output is checked: an
    undeclared CSV or PDF keeps its ``unsupported_format`` not_testable entry
    instead of being silently dropped from the plan."""
    return str(output.get("kind") or "geodata")


def edit_targets_reference_real_sources(workspace: Path, project_dir: str = ".") -> AssertionResult:
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    sources = set((proj.get("sources") or {}).keys())
    targets = get_in(proj, "presentation.editing.targets", {}) or {}
    if not targets:
        return passed("no edit targets declared (vacuously true)")
    bad = [k for k, t in targets.items() if t.get("source") not in sources]
    if bad:
        return failed(f"edit targets referencing unknown sources: {bad}", code="edit_target_unknown_source")
    return passed(f"all {len(targets)} edit targets reference real project sources")


def consistent_with(
    workspace: Path, other_project_dir: str, project_dir: str = ".",
    required_shared_keys: list[str] | None = None,
) -> AssertionResult:
    """Two different analyses over the same skill must share stable UX
    semantics (layout type, sidebar organization, semantic-role vocabulary,
    provenance_ui shape, editing capability keys) even though their actual
    layers/results differ. Do not require byte-identical output."""
    proj_a = load_project_yaml(workspace, project_dir)
    proj_b = load_project_yaml(workspace, other_project_dir)
    if proj_a is None or proj_b is None:
        return failed("one of the two projects is missing project.yaml", code="manifest_missing")

    errors: list[str] = []

    layout_a = get_in(proj_a, "presentation.layout.type")
    layout_b = get_in(proj_b, "presentation.layout.type")
    if layout_a != layout_b:
        errors.append(f"layout.type differs: {layout_a!r} vs {layout_b!r}")

    org_a = get_in(proj_a, "presentation.layout.sidebar.organization")
    org_b = get_in(proj_b, "presentation.layout.sidebar.organization")
    if org_a != org_b:
        errors.append(f"sidebar.organization differs: {org_a!r} vs {org_b!r}")

    prov_a = set(get_in(proj_a, "presentation.provenance_ui", {}) or {})
    prov_b = set(get_in(proj_b, "presentation.provenance_ui", {}) or {})
    if prov_a != prov_b:
        errors.append(f"provenance_ui keys differ: {sorted(prov_a)} vs {sorted(prov_b)}")

    edit_a = set(get_in(proj_a, "presentation.editing", {}) or {})
    edit_b = set(get_in(proj_b, "presentation.editing", {}) or {})
    if edit_a != edit_b:
        errors.append(f"editing capability keys differ: {sorted(edit_a)} vs {sorted(edit_b)}")

    roles_a = {l.get("semantic_role") for l in get_in(proj_a, "presentation.map.layers", []) or []}
    roles_b = {l.get("semantic_role") for l in get_in(proj_b, "presentation.map.layers", []) or []}
    if not (roles_a & roles_b):
        errors.append(f"no shared semantic roles between projects: {roles_a} vs {roles_b}")

    for key in required_shared_keys or []:
        va = get_in(proj_a, key)
        vb = get_in(proj_b, key)
        if va != vb:
            errors.append(f"{key} differs: {va!r} vs {vb!r}")

    if errors:
        return failed("; ".join(errors), errors=errors, code="ux_semantics_drift")
    return passed("presentation semantics stable across both analyses")


def distinguishable_layer_semantics(workspace: Path, project_dir: str = ".") -> AssertionResult:
    """Source, result, override, and hypothetical data must remain visually
    distinguishable — i.e. use different semantic_role values, not all the
    same role."""
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    layers = get_in(proj, "presentation.map.layers", []) or []
    roles = {layer.get("semantic_role") for layer in layers if layer.get("semantic_role")}
    if len(layers) > 1 and len(roles) <= 1:
        return failed(
            "all layers share a single semantic_role; source/result/override/hypothetical indistinguishable",
            code="indistinguishable_layers",
        )
    return passed(f"layers use {len(roles)} distinct semantic role(s)")
