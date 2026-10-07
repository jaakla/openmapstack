"""Visual integration assertions: rendered-map substantiveness and live
browser validation of the generated dashboard.

PR 7 of the eval epic. These checks supplement — never replace — the
deterministic structural assertions: a dashboard that loads cleanly but
shows an empty map, hides a declared warning, or renders a scenario layer
indistinguishable from the baseline must fail here.

Browser checks require Playwright with a Chromium install and return
``not_testable`` (never a silent pass) when the execution environment lacks
them. PNG analysis is stdlib-only so it runs anywhere, including offline
fixture CI.
"""

from __future__ import annotations

import json
import re
import tempfile
import struct
import zlib
from pathlib import Path
from typing import Any

from . import AssertionResult, failed, get_in, load_project_yaml, not_testable, passed, project_root
from .presentation import DESIGN_LANGUAGES, declared_controls, declared_views

# A rendered map is considered blank when fewer than this fraction of
# pixels differ from the modal (background) color. Genuine sparse vector
# content - a small parcel in a generous frame, a thin road line - still
# contributes at least ~0.05% ink; a truly blank render (missing layers,
# collapsed extent, displaced CRS) contributes none beyond encoder noise.
_BLANK_MAX_NON_MODAL_FRACTION = 0.0002

# Two screenshots count as "the same image" when fewer than this fraction
# of pixels differ. Headless Chromium renders identical content
# deterministically, so the threshold only absorbs encoder noise; it must
# stay far below the ink of even the smallest declared feature.
_SAME_IMAGE_DIFF_FRACTION = 0.00005


# ---------------------------------------------------------------------------
# Minimal PNG decoding (stdlib only — no Pillow/numpy dependency)
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png(path: str | Path) -> tuple[int, int, int, list[bytes]]:
    """Decode an 8-bit PNG into ``(width, height, bytes_per_pixel, rows)``.

    Supports color types 0 (gray), 2 (RGB), 4 (gray+alpha) and 6 (RGBA) —
    everything Chromium screenshots and Qt rendered images produce — with
    all five scanline filters. Anything else raises ValueError.
    """
    data = Path(path).read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    pos = len(_PNG_SIGNATURE)
    width = height = bit_depth = color_type = 0
    idat = bytearray()
    while pos + 8 <= len(data):
        length, chunk = struct.unpack(">I4s", data[pos:pos + 8])
        pos += 8
        chunk_data = data[pos:pos + length]
        pos += length + 4  # skip CRC
        if chunk == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        elif chunk == b"IDAT":
            idat.extend(chunk_data)
        elif chunk == b"IEND":
            break
    if bit_depth != 8:
        raise ValueError(f"unsupported bit depth {bit_depth}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if channels is None:
        raise ValueError(f"unsupported color type {color_type}")

    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"corrupt or truncated PNG data: {exc}") from exc
    stride = width * channels
    bpp = channels  # filter offset equals channel count for 8-bit depth
    rows: list[bytes] = []
    prev = bytearray(stride)
    cursor = 0
    for _ in range(height):
        if cursor >= len(raw):
            raise ValueError("truncated PNG data")
        filter_type = raw[cursor]
        cursor += 1
        line = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if len(line) != stride:
            raise ValueError("truncated PNG scanline")
        if filter_type == 1:  # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif filter_type == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif filter_type == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif filter_type == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"unsupported filter type {filter_type}")
        rows.append(bytes(line))
        prev = line
    return width, height, channels, rows


def image_stats(path: Path) -> dict[str, Any]:
    """Coarse color statistics of a rendered image, robust to encoder noise.

    Colors are quantized to 5 bits per channel before counting so that
    antialiasing dithering cannot make a blank render look "substantive".
    """
    width, height, channels, rows = decode_png(path)
    counts: dict[int, int] = {}
    total = width * height
    for row in rows:
        for i in range(0, len(row), channels):
            key = (row[i] >> 3) << 10 | (row[i + 1] >> 3) << 5 | (row[i + 2] >> 3)
            counts[key] = counts.get(key, 0) + 1
    modal = max(counts.values()) if counts else 0
    return {
        "width": width,
        "height": height,
        "distinct_colors_quantized": len(counts),
        "modal_color_fraction": round(modal / total, 6) if total else 1.0,
        "non_modal_fraction": round(1 - modal / total, 6) if total else 0.0,
    }


def images_differ(path_a: Path, path_b: Path) -> tuple[bool, float]:
    """Compare two decoded images; returns ``(differ, differing_fraction)``.

    Images with different dimensions always differ.
    """
    wa, ha, ca, rows_a = decode_png(path_a)
    wb, hb, cb, rows_b = decode_png(path_b)
    if (wa, ha, ca) != (wb, hb, cb):
        return True, 1.0
    differing = 0
    total = wa * ha
    for row_a, row_b in zip(rows_a, rows_b):
        if row_a == row_b:
            continue
        for i in range(0, len(row_a), ca):
            if row_a[i:i + ca] != row_b[i:i + ca]:
                differing += 1
    fraction = differing / total if total else 0.0
    return fraction > _SAME_IMAGE_DIFF_FRACTION, fraction


def _is_blank(stats: dict[str, Any]) -> bool:
    return (
        stats["non_modal_fraction"] < _BLANK_MAX_NON_MODAL_FRACTION
        or stats["distinct_colors_quantized"] < 2
    )


# ---------------------------------------------------------------------------
# render_substantive: a rendered PNG must show actual map content
# ---------------------------------------------------------------------------

def render_substantive(workspace: Path, path: str, project_dir: str = ".") -> AssertionResult:
    """A rendered map snapshot (PyQGIS render or dashboard screenshot) must
    contain real drawn content. Detects the empty-map failure mode: a valid
    project that renders to a single background color, whether from missing
    layers, a collapsed extent, gross CRS displacement, or styling that
    paints nothing."""
    image_path = project_root(workspace, project_dir) / path
    if not image_path.exists():
        return failed(f"rendered snapshot {path} does not exist", code="snapshot_missing")
    try:
        stats = image_stats(image_path)
    except ValueError as exc:
        return failed(f"snapshot {path} is not decodable: {exc}", code="snapshot_undecodable")
    if _is_blank(stats):
        return failed(
            f"rendered snapshot {path} is blank ({stats['modal_color_fraction']:.1%} one color, "
            f"{stats['distinct_colors_quantized']} quantized colors)",
            code="blank_render",
            stats=stats,
        )
    return passed(
        f"rendered snapshot {path} shows substantive content "
        f"({stats['distinct_colors_quantized']} quantized colors, "
        f"{stats['non_modal_fraction']:.1%} non-background)",
        stats=stats,
    )


# ---------------------------------------------------------------------------
# dashboard_loads_in_browser: live headless-browser validation
# ---------------------------------------------------------------------------

def credited_parties(attribution: str) -> list[str]:
    """The parties an attribution credits, which must each be visible.

    "© OpenStreetMap contributors © CARTO" credits "OpenStreetMap
    contributors" and "CARTO". Order and punctuation are free, so the map
    engine's own attribution control ("© CARTO, © OpenStreetMap
    contributors") satisfies it and the page needs no second copy. Text
    before the first © ("Map data") is a caption, not a party. Without a ©
    the whole string is the one credit.
    """
    text = " ".join(attribution.split())
    if "©" not in text:
        return [text] if text else []
    parties = (part.strip(" ,;|·") for part in text.split("©")[1:])
    return [party for party in parties if party]


def _playwright():
    from playwright.sync_api import sync_playwright  # type: ignore

    return sync_playwright


_MAP_CONTAINER_SELECTOR = '[data-testid="map"], #map, .maplibregl-map'
_MAP_SELECTOR = f"{_MAP_CONTAINER_SELECTOR}, canvas"
_LEGEND_SELECTOR = '[data-testid="legend"], #legend, .legend'
_PROVENANCE_SELECTOR = '[data-testid="provenance"], #provenance, .provenance'
_WARNINGS_SELECTOR = '[data-testid="warnings"], #warnings, .warnings'
_RESET_SELECTOR = '[data-testid="canonical-reset"], #reset'


def _first_visible(page: Any, selector: str, *, require_height: bool = False) -> Any | None:
    for element in page.query_selector_all(selector):
        try:
            box = element.bounding_box()
            if box and box["width"] > 0 and (box["height"] > 0 or not require_height):
                return element
        except Exception:  # noqa: BLE001
            continue
    return None


def _dashboard_layout_problems(page: Any) -> list[str]:
    """Inspect actual bounds; a positive bounding box can still be off-screen."""
    viewport = page.viewport_size
    if viewport is None:
        return ["browser viewport is unavailable"]
    # An explicit map container wins; a bare canvas may be a chart, so it is
    # the map only when no container is visible.
    selector = _MAP_CONTAINER_SELECTOR if _first_visible(page, _MAP_CONTAINER_SELECTOR) else "canvas"
    map_element = _first_visible(page, selector, require_height=True)
    if map_element is None:
        # A map container without a CSS height collapses to 0 px. It still has
        # a width, and a zero-height box satisfies every containment test below.
        if _first_visible(page, selector) is not None:
            return ["map element has zero height"]
        return ["map element is absent"]
    map_box = map_element.bounding_box()
    problems = []

    def contained(box, container):
        return (box["x"] >= container["x"] - 1 and box["y"] >= container["y"] - 1
                and box["x"] + box["width"] <= container["x"] + container["width"] + 1
                and box["y"] + box["height"] <= container["y"] + container["height"] + 1)

    screen = {"x": 0, "y": 0, **viewport}
    if not contained(map_box, screen):
        problems.append("map extends outside the viewport")
    selectors = _LEGEND_SELECTOR + ', .layer-control, [data-testid="layer-controls"], .maplibregl-ctrl'
    for element in page.query_selector_all(selectors):
        box = element.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            continue
        # Ordinary legends may live in a scrollable sidebar. Map overlays must
        # stay inside the map and viewport, including their clickable children.
        # MapLibre positions its corner wrappers; the `.maplibregl-ctrl`
        # children are static but still overlays whose bounds must be checked.
        overlay = element.evaluate("""e => e.matches('.maplibregl-ctrl') ||
            ['absolute', 'fixed'].includes(getComputedStyle(e).position)""")
        if overlay:
            if not contained(box, screen):
                problems.append("map legend or controls extend outside the viewport")
            if not contained(box, map_box):
                problems.append("map legend or controls extend outside the map")
    return sorted(set(problems))


def dashboard_layout_within_viewport(
    workspace: Path, dashboard: str = "dashboard.html", project_dir: str = ".",
    desktop_size: str = "1440x900", mobile_size: str = "390x844",
) -> Any:
    """A lightweight layout check usable in live CI without PyQGIS or tiles."""
    dashboard_path = project_root(workspace, project_dir) / dashboard
    if not dashboard_path.is_file():
        return failed(f"{dashboard} does not exist", code="file_missing")
    try:
        sync_playwright = _playwright()
    except ImportError:
        return not_testable("Playwright is not installed", code="playwright_unavailable")
    opened = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            problems = []
            for size in (desktop_size, mobile_size):
                page = browser.new_page(viewport=_viewport(size))
                page.goto(dashboard_path.as_uri(), wait_until="domcontentloaded")
                opened = True
                _settle(page, 300)
                problems.extend(f"{size}: {problem}" for problem in _dashboard_layout_problems(page))
                page.close()
            browser.close()
        if problems:
            return failed("; ".join(problems), code="dashboard_layout_invalid", problems=problems)
        return passed("map and overlays fit desktop and mobile viewports")
    except Exception as exc:  # noqa: BLE001
        if opened:
            return failed(f"layout inspection failed: {exc}", code="browser_check_error")
        return not_testable(f"browser unavailable: {exc}", code="browser_unavailable")


def _screenshot_map(page: Any, output_path: Path) -> str | None:
    element = _first_visible(page, _MAP_SELECTOR)
    if element is None:
        return "no visible map element"
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        element.screenshot(path=str(output_path))
    except Exception as exc:  # noqa: BLE001
        return f"map screenshot failed: {exc}"
    return None


def _settle(page: Any, settle_ms: int) -> None:
    """Wait for background-map tiles and render to settle: prefer the page's
    network going idle (tiles, CDN), then a fixed grace period. Best effort —
    an environment without network simply uses the grace period."""
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(settle_ms)


class _Problems:
    """Collected defects, each recorded with the code of the check that
    found it.

    Mutation cases pin `expect_code`, so these codes are load-bearing:
    inferring them by substring-matching the human-readable message made
    them depend on wording, and made a message that merely *mentions*
    another subject take that subject's code -- a layer group literally
    named "basemap", for instance, reporting `basemap_absent`. The check
    that detects a problem is the thing that knows what it is.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.codes: list[str] = []

    def add(self, code: str, message: str) -> None:
        self.messages.append(message)
        self.codes.append(code)

    def __bool__(self) -> bool:
        return bool(self.messages)

    @property
    def primary_code(self) -> str:
        return self.codes[0] if self.codes else "dashboard_visual_failure"


def _stable_screenshot(page: Any, output_path: Path, settle_ms: int, attempts: int = 4) -> str | None:
    """Capture the map only once two consecutive captures agree.

    A raster basemap loads and fades in asynchronously, so tiles arriving
    between a "before" and an "after" capture would register as a change and
    let a dead layer toggle look like a working one. Waiting for the frame to
    stop moving is what makes the later comparison mean what it claims.
    """
    # Keep the .png suffix: Playwright picks the encoder from the extension.
    probe = output_path.with_name(f"{output_path.stem}.probe.png")
    try:
        for _ in range(attempts):
            error = _screenshot_map(page, output_path)
            if error:
                return error
            _settle(page, settle_ms)
            error = _screenshot_map(page, probe)
            if error:
                return error
            differ, _fraction = images_differ(output_path, probe)
            if not differ:
                return None
        return f"map never stopped changing after {attempts} settle attempts"
    finally:
        probe.unlink(missing_ok=True)


def _toggle_changes_render(
    page: Any,
    control: Any,
    *,
    baseline: Path,
    toggled: Path,
    settle_ms: int,
    no_effect_detail: str = "toggle does not change the rendered map (layer absent or indistinguishable)",
    switch: Any = None,
) -> tuple[str | None, float | None]:
    """Switch ``control`` off, confirm the rendered map actually changes, and
    switch it back on again. ``switch(on)`` overrides how the control is
    operated; by default it is a checkbox.

    The restore runs in a ``finally`` block: a screenshot failure mid-check
    must not leave the layer hidden, because every later comparison — and
    the canonical-reset check, which compares against the control state
    captured while it was on — would then measure the wrong page.

    Returns ``(problem, differing_fraction)``; ``problem`` is None when the
    toggle demonstrably changes the render.
    """
    if switch is None:
        def switch(on: bool) -> None:
            control.check() if on else control.uncheck()
    error = _stable_screenshot(page, baseline, settle_ms)
    if error:
        return error, None
    try:
        switch(False)
        _settle(page, settle_ms)
        error = _stable_screenshot(page, toggled, settle_ms)
        if error:
            return error, None
        differ, fraction = images_differ(baseline, toggled)
        if not differ:
            return f"{no_effect_detail} (only {fraction:.4%} of pixels differ)", fraction
        return None, fraction
    finally:
        switch(True)
        _settle(page, settle_ms)


def _panel_and_basemap_problems(
    page: Any,
    problems: "_Problems",
    *,
    presentation: dict,
    warnings: list,
    requested_urls: list[str],
    reveal: bool = False,
) -> None:
    """Declared legend, provenance and warnings panels are visible; a declared
    basemap is really requested and attributed. With ``reveal`` a panel on an
    inactive tab is first brought into view through its tab."""

    def visible(selector: str) -> Any | None:
        element = _first_visible(page, selector)
        if element is None and reveal:
            hidden = page.query_selector(selector)
            if hidden is not None and _reveal(page, hidden):
                element = _first_visible(page, selector)
        return element

    legend_visible = bool(get_in(presentation, "legend.visible"))
    provenance_declared = bool(presentation.get("provenance_ui"))
    # Declared panels are actually visible.
    if legend_visible and visible(_LEGEND_SELECTOR) is None:
        problems.add("legend_absent", "manifest declares legend visible but no legend is rendered")
    if provenance_declared and visible(_PROVENANCE_SELECTOR) is None:
        problems.add("provenance_absent", "manifest declares provenance_ui but no provenance panel is rendered")
    if warnings:
        panel = visible(_WARNINGS_SELECTOR)
        body_text = page.inner_text("body")
        for w in warnings:
            warning_id = str(w.get("id", ""))
            if panel is None:
                problems.add("warning_not_visible", f"manifest warning {warning_id} has no visible warning panel")
                break
            if warning_id and warning_id not in body_text:
                problems.add("warning_not_visible", f"manifest warning {warning_id} not visible in the rendered product")

    # A declared interactive basemap is real.
    # A manifest that presents a map must declare its background
    # map; that omission is caught by the v1 schema
    # (project-spec.md s. 3), which every case checks in every
    # mode. What only a browser can prove is the rest: that the
    # declared tiles are really requested and the required
    # attribution is really visible.
    basemap = get_in(presentation, "map.basemap")
    if basemap:
        # Match any tile under the basemap's URL template:
        # "https://host/{z}/{x}/{y}.png" -> "https://host/".
        tile_prefix = ((basemap.get("tiles") or [basemap.get("url") or ""])[0] or "").split("{z}")[0]
        tile_requests = [url for url in requested_urls if tile_prefix and url.startswith(tile_prefix)]
        if _first_visible(page, f'{_MAP_SELECTOR}, .maplibregl-canvas') is None:
            problems.add("basemap_absent", "manifest declares a basemap but no interactive map canvas is rendered")
        if not tile_requests:
            problems.add(
                "basemap_absent",
                f"manifest declares basemap {basemap.get('id')!r} but the product never "
                f"requested its tiles ({tile_prefix}...) — the background map is not interactive",
            )
        attribution = basemap.get("attribution")
        if attribution:
            text = " ".join(page.inner_text("body").split())
            missing = [party for party in credited_parties(attribution) if party not in text]
            if missing:
                problems.add(
                    "basemap_absent",
                    f"basemap attribution {attribution!r} required by the manifest is not visible "
                    f"in the rendered product (missing: {', '.join(missing)})",
                )


def _checkbox_states(page: Any) -> dict[str, bool]:
    return page.evaluate(
        """() => Object.fromEntries(
            [...document.querySelectorAll('input[type="checkbox"]')]
            .map(cb => [cb.dataset.layerGroup || cb.dataset.scenario || cb.id || cb.name || '', cb.checked])
        )"""
    )


# ---------------------------------------------------------------------------
# State protocol (design language openmapstack-views, ADR 0007)
# ---------------------------------------------------------------------------

_BROWSER_HINT = "install openmapstack[visual] and run `python -m playwright install chromium`"

# Marks, outermost first, the tabs whose panels contain the element, so the
# checker can click them the way a reader would.
_MARK_OWNING_TABS_JS = """(element) => {
  const panels = [];
  for (let p = element.closest('[role="tabpanel"]'); p; p = p.parentElement && p.parentElement.closest('[role="tabpanel"]')) {
    panels.unshift(p);
  }
  let n = 0;
  for (const panel of panels) {
    let tab = panel.id ? document.querySelector('[role="tab"][aria-controls="' + CSS.escape(panel.id) + '"]') : null;
    if (!tab && panel.getAttribute('aria-labelledby')) tab = document.getElementById(panel.getAttribute('aria-labelledby'));
    if (tab && tab.getAttribute('role') === 'tab') tab.setAttribute('data-oms-check-tab', String(n++));
  }
  return n;
}"""

_CONTROL_STATE_JS = """(e) => {
  if (e.matches('input[type="checkbox"]')) return {kind: 'checkbox', state: e.checked};
  if (e.matches('input[type="range"]')) return {kind: 'range', state: e.value, min: e.min || '0', max: e.max || '100'};
  if (e.matches('select')) return {kind: 'select', state: e.value,
    options: [...e.options].filter((o) => !o.disabled).map((o) => o.value)};
  const buttons = [...e.querySelectorAll('[data-oms-value]')];
  if (buttons.length) {
    const on = (b) => ['aria-pressed', 'aria-checked', 'aria-selected'].some((a) => b.getAttribute(a) === 'true');
    return {kind: 'group', state: buttons.filter(on).map((b) => b.dataset.omsValue).sort(),
      values: buttons.map((b) => b.dataset.omsValue)};
  }
  return {kind: 'unknown', tag: e.tagName.toLowerCase()};
}"""

# The page's text with the exploratory label left out, so that the label
# appearing does not by itself count as the control changing the view.
_PAGE_TEXT_JS = """() => {
  const copy = document.body.cloneNode(true);
  copy.querySelectorAll('[data-oms-exploratory], script, style').forEach((e) => e.remove());
  return copy.textContent.replace(/\\s+/g, ' ');
}"""


def _reveal(page: Any, element: Any, settle_ms: int = 150) -> bool:
    """Open the tabs that hide ``element``; True when it is then visible."""
    try:
        if element.is_visible():
            return True
        count = element.evaluate(_MARK_OWNING_TABS_JS)
        for index in range(count):
            page.click(f'[data-oms-check-tab="{index}"]', timeout=3000)
            page.wait_for_timeout(settle_ms)
        return element.is_visible()
    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            page.evaluate("() => document.querySelectorAll('[data-oms-check-tab]').forEach((t) => t.removeAttribute('data-oms-check-tab'))")
        except Exception:  # noqa: BLE001
            pass


def _find_revealed(page: Any, selector: str) -> tuple[Any | None, bool]:
    """``(element, present)``: the first visible match, after opening the
    owning tab of the first match when none is visible."""
    matches = page.query_selector_all(selector)
    if not matches:
        return None, False
    for element in matches:
        if element.is_visible():
            return element, True
    return (matches[0] if _reveal(page, matches[0]) else None), True


def _alternative_state(info: dict) -> Any:
    """A state the control can be moved to, or None when it has only one."""
    kind = info["kind"]
    if kind == "checkbox":
        return not info["state"]
    if kind == "select":
        return next((o for o in info["options"] if o != info["state"]), None)
    if kind == "range":
        if info["min"] == info["max"]:
            return None
        return info["max"] if info["state"] != info["max"] else info["min"]
    if kind == "group":
        off = [v for v in info["values"] if v not in info["state"]]
        return off[0] if off else (info["values"][0] if info["values"] else None)
    return None


_PAGE_SCROLL_JS = """() => [window.scrollX, window.scrollY,
  document.body ? document.body.scrollLeft : 0, document.body ? document.body.scrollTop : 0]"""


def _click_like_reader(target: Any) -> int:
    """Click ``target`` and return how far the page itself scrolled because of
    the click, in pixels.

    Measured only from an unscrolled page with the target already in view, so
    neither Playwright's own scrolling nor a page that scrolls as a document
    counts. The typical cause is a visually hidden input that escapes its
    panel: the browser scrolls the whole page to focus it. The scroll is
    undone so later screenshots compare the same page.
    """
    target.scroll_into_view_if_needed(timeout=3000)
    before = target.evaluate(_PAGE_SCROLL_JS)
    target.click(timeout=3000)
    after = target.evaluate(_PAGE_SCROLL_JS)
    if any(before) or after == before:
        return 0
    target.evaluate("() => { window.scrollTo(0, 0); if (document.body) document.body.scrollTop = 0; }")
    return int(max(abs(v) for v in after))


def _set_control(control: Any, info: dict, value: Any) -> int:
    """Operate a protocol control the way a reader would. Returns the page
    jump the operation caused (see ``_click_like_reader``)."""
    kind = info["kind"]
    if kind == "checkbox":
        wanted = bool(value)
        if control.evaluate("e => e.checked") == wanted:
            return 0
        # A reader clicks the label: custom switches hide the box itself.
        label = control.evaluate_handle("e => (e.labels && e.labels[0]) || null").as_element()
        for target in (label, control):
            if target is None:
                continue
            try:
                jump = _click_like_reader(target)
            except Exception:  # noqa: BLE001  - covered or hidden; try the next target
                continue
            if control.evaluate("e => e.checked") == wanted:
                return jump
        control.evaluate("(e, v) => { if (e.checked !== v) e.click(); }", wanted)
    elif kind == "select":
        control.select_option(value=str(value), timeout=3000)
    elif kind == "range":
        control.evaluate("""(e, v) => { e.value = v;
          e.dispatchEvent(new Event('input', {bubbles: true}));
          e.dispatchEvent(new Event('change', {bubbles: true})); }""", str(value))
    elif kind == "group":
        return _click_like_reader(control.query_selector(f'[data-oms-value="{value}"]'))
    return 0


def _restore_control(control: Any, initial: dict) -> None:
    if initial["kind"] != "group":
        _set_control(control, initial, initial["state"])
        return
    for value in initial["values"]:
        current = control.evaluate(_CONTROL_STATE_JS)["state"]
        if (value in initial["state"]) != (value in current):
            control.query_selector(f'[data-oms-value="{value}"]').click(timeout=3000)


def _page_state(page: Any) -> str | None:
    return page.evaluate("() => document.documentElement.dataset.omsState || null")


def _protocol_page_problems(
    page: Any,
    problems: "_Problems",
    *,
    view: dict,
    shot_prefix: Path,
    settle_ms: int,
    evidence: dict,
) -> None:
    """Every declared control and layer group of one page, operated through
    the state protocol of design-language.md s. 5."""
    presentation = view["presentation"]
    controls = declared_controls(presentation)
    exploratory = [c for c in controls if c["effect"] != "published"]
    has_map = _first_visible(page, _MAP_SELECTOR) is not None
    # A report scrolls as a document; any other page keeps still while a
    # reader operates it, and only the panel a control sits in may scroll.
    keeps_still = view.get("archetype") != "report"

    def jumped(jump: int, what: str) -> None:
        if jump and keeps_still:
            problems.add("page_jumped", f"operating {what} scrolled the whole page by {jump}px; keep each "
                         "control's input inside its positioned row or panel so focusing it scrolls nothing")

    if exploratory and (_page_state(page) != "canonical" or _first_visible(page, "[data-oms-exploratory]")):
        problems.add("not_canonical_at_open",
                     "page does not open in the canonical state (html data-oms-state is not 'canonical' "
                     "or the exploratory label is already shown)")

    for group in get_in(presentation, "map.layer_groups", []) or []:
        group_id = group.get("id")
        toggle, present = _find_revealed(page, f'[data-oms-layer-group="{group_id}"]')
        if toggle is None:
            problems.add("layer_group_not_rendered", f"layer group {group_id} has "
                         + ("no reachable toggle" if present else "no toggle with data-oms-layer-group"))
            continue
        if not has_map:
            continue
        info = toggle.evaluate(_CONTROL_STATE_JS)
        # A group may start hidden (default_open: false): flip it from its
        # initial state and restore that state, never force it on.
        initially_on = info["state"] if info["kind"] == "checkbox" else toggle.get_attribute("aria-pressed") == "true"
        jumps: list[int] = []
        if info["kind"] == "checkbox":
            def switch(as_initial: bool, toggle=toggle, info=info, initially_on=initially_on, jumps=jumps) -> None:
                jumps.append(_set_control(toggle, info, initially_on if as_initial else not initially_on))
        else:
            def switch(as_initial: bool, toggle=toggle, initially_on=initially_on, jumps=jumps) -> None:
                wanted = initially_on if as_initial else not initially_on
                if (toggle.get_attribute("aria-pressed") == "true") != wanted:
                    jumps.append(_click_like_reader(toggle))
        problem, fraction = _toggle_changes_render(
            page, toggle, switch=switch, settle_ms=settle_ms,
            baseline=shot_prefix.with_name(f"{shot_prefix.name}-group-{group_id}-before.png"),
            toggled=shot_prefix.with_name(f"{shot_prefix.name}-group-{group_id}-after.png"),
        )
        jumped(max(jumps, default=0), f"the toggle of layer group {group_id}")
        if problem is not None:
            problems.add("layer_group_not_rendered", f"layer group {group_id}: {problem}")
        elif fraction is not None:
            evidence.setdefault("toggle_diff_fraction", {})[f"group:{group_id}"] = fraction

    for control in controls:
        control_id = control["id"]
        element, present = _find_revealed(page, f'[data-oms-control="{control_id}"]')
        if element is None:
            problems.add("control_absent", f"declared control {control_id} has "
                         + ("no control a reader can reach" if present else "no element with data-oms-control"))
            continue
        initial = element.evaluate(_CONTROL_STATE_JS)
        target = _alternative_state(initial)
        if initial["kind"] == "unknown" or target is None:
            problems.add("control_inoperable", f"control {control_id} exposes no second state through "
                         "checked, value or aria-pressed")
            continue
        text_before = page.evaluate(_PAGE_TEXT_JS)
        jumped(_set_control(element, initial, target), f"control {control_id}")
        _settle(page, settle_ms)
        if element.evaluate(_CONTROL_STATE_JS)["state"] == initial["state"]:
            problems.add("control_inoperable", f"control {control_id} does not change state when operated")
            continue
        changed = page.evaluate(_PAGE_TEXT_JS) != text_before
        if not changed and has_map:
            # Map-only effect: compare the map in both states. Screenshots are
            # costly to decode, so only controls that change no text pay.
            map_before = shot_prefix.with_name(f"{shot_prefix.name}-control-{control_id}-before.png")
            map_after = shot_prefix.with_name(f"{shot_prefix.name}-control-{control_id}-after.png")
            after_error = _stable_screenshot(page, map_after, settle_ms)
            _restore_control(element, initial)
            _settle(page, settle_ms)
            before_error = _stable_screenshot(page, map_before, settle_ms)
            _set_control(element, initial, target)
            _settle(page, settle_ms)
            if after_error is None and before_error is None:
                changed = images_differ(map_before, map_after)[0]
        if not changed:
            problems.add("control_no_effect", f"control {control_id} changes neither the page text nor the map")
        if control["effect"] == "published":
            _restore_control(element, initial)
            _settle(page, settle_ms)
            continue
        if _page_state(page) != "exploratory" or _first_visible(page, "[data-oms-exploratory]") is None:
            problems.add("exploratory_label_missing",
                         f"leaving the canonical position of {control_id} shows no exploratory label "
                         "(visible data-oms-exploratory and html data-oms-state='exploratory')")
        reset = _first_visible(page, "[data-oms-reset]")
        if reset is None:
            problems.add("canonical_reset_failed", f"no reset (data-oms-reset) is reachable after changing {control_id}")
            _restore_control(element, initial)
            _settle(page, settle_ms)
            continue
        reset.click(timeout=3000)
        _settle(page, settle_ms)
        restored = element.evaluate(_CONTROL_STATE_JS)["state"] == initial["state"]
        if not restored or _page_state(page) != "canonical" or _first_visible(page, "[data-oms-exploratory]"):
            problems.add("canonical_reset_failed",
                         f"reset after changing {control_id} does not restore the canonical state")
            if not restored:
                _restore_control(element, initial)
                _settle(page, settle_ms)


def _protocol_check(
    proj: dict,
    root: Path,
    *,
    dashboard: str,
    label: str,
    screenshot_dir: Path,
    desktop_size: str,
    mobile_size: str,
    settle_ms: int,
) -> AssertionResult:
    """``dashboard_loads_in_browser`` for a project that declares a design
    language: every declared page, every declared control."""
    views = declared_views(proj)
    if not proj.get("views"):
        views[0]["path"] = dashboard
    missing = [v["path"] for v in views if not (root / v["path"]).is_file()]
    if missing:
        return failed(f"declared page(s) do not exist: {missing}", code="file_missing", missing=missing)
    try:
        sync_playwright = _playwright()
    except ImportError:
        return not_testable(f"Playwright is not installed in this execution environment; {_BROWSER_HINT}",
                            code="playwright_unavailable")

    warnings = proj.get("warnings") or []
    problems = _Problems()
    evidence: dict[str, Any] = {}
    opened = False
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                return not_testable(f"headless browser unavailable in this environment ({_BROWSER_HINT}): {exc}",
                                    code="browser_unavailable")
            try:
                for view in views:
                    page_problems = _Problems()
                    uri = (root / view["path"]).as_uri()
                    shot_prefix = screenshot_dir / f"{label}-{view['id']}"
                    context = browser.new_context(viewport=_viewport(desktop_size))
                    page = context.new_page()
                    page_errors: list[str] = []
                    console_errors: list[str] = []
                    requested_urls: list[str] = []
                    page.on("pageerror", lambda exc, errors=page_errors: errors.append(str(exc)))
                    page.on("console", lambda msg, errors=console_errors: errors.append(msg.text) if msg.type == "error" else None)
                    page.on("request", lambda request, urls=requested_urls: urls.append(request.url))
                    unreachable: list[str] = []
                    page.on("requestfailed", lambda request, out=unreachable: out.append(request.url)
                            if request.url.startswith(("http://", "https://"))
                            and request.resource_type in ("script", "stylesheet") else None)
                    page.goto(uri, wait_until="domcontentloaded")
                    opened = True
                    _settle(page, settle_ms)
                    if unreachable:
                        # The page's own code or styles never arrived: what
                        # follows would grade this machine's network, not the
                        # product. Basemap tiles are images and do not count.
                        return not_testable(
                            f"page {view['id']} could not fetch its remote scripts or styles from here: "
                            f"{unreachable[:3]}", code="dependency_unreachable", unreachable=unreachable,
                        )
                    if page_errors:
                        page_problems.add("browser_page_error", f"{len(page_errors)} page error(s): {page_errors[:3]}")
                    if console_errors:
                        page_problems.add("browser_console_error", f"{len(console_errors)} console error(s): {console_errors[:3]}")

                    map_required = bool(view["presentation"].get("map"))
                    if _first_visible(page, _MAP_SELECTOR) is None:
                        if map_required:
                            page_problems.add("map_absent", "no visible map element")
                    else:
                        shot = shot_prefix.with_name(f"{shot_prefix.name}-desktop.png")
                        error = _screenshot_map(page, shot)
                        if error:
                            page_problems.add("map_screenshot_failed", f"desktop map screenshot: {error}")
                        else:
                            stats = image_stats(shot)
                            evidence[f"{view['id']}:desktop_map_stats"] = stats
                            if _is_blank(stats):
                                page_problems.add("blank_map", "map renders blank on desktop")

                    _panel_and_basemap_problems(
                        page, page_problems, presentation=view["presentation"], warnings=warnings,
                        requested_urls=requested_urls, reveal=True,
                    )
                    _protocol_page_problems(
                        page, page_problems, view=view, shot_prefix=shot_prefix,
                        settle_ms=settle_ms, evidence=evidence,
                    )
                    context.close()

                    if map_required:
                        mobile_context = browser.new_context(viewport=_viewport(mobile_size))
                        mobile = mobile_context.new_page()
                        mobile.goto(uri, wait_until="domcontentloaded")
                        _settle(mobile, settle_ms)
                        map_element = _first_visible(mobile, _MAP_SELECTOR, require_height=True)
                        width = _viewport(mobile_size)["width"]
                        if map_element is None:
                            page_problems.add("map_absent", "no visible map on the mobile viewport")
                        else:
                            box = map_element.bounding_box()
                            evidence[f"{view['id']}:mobile_map_width"] = box["width"]
                            if box["width"] < width / 2:
                                page_problems.add(
                                    "mobile_map_cramped",
                                    f"the map is {box['width']:.0f} px wide on a {width} px screen; "
                                    "it must keep at least half the width",
                                )
                            shot = shot_prefix.with_name(f"{shot_prefix.name}-mobile.png")
                            if _screenshot_map(mobile, shot) is None and _is_blank(image_stats(shot)):
                                page_problems.add("blank_map", "map renders blank on the mobile viewport")
                        mobile_context.close()

                    for code, message in zip(page_problems.codes, page_problems.messages):
                        problems.add(code, f"[{view['id']}] {message}")
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        if opened:
            return failed(f"browser validation crashed while inspecting an opened page: {type(exc).__name__}: {exc}",
                          code="browser_check_error")
        return not_testable(f"browser validation could not run: {type(exc).__name__}: {exc}", code="browser_error")

    if problems:
        return failed("; ".join(problems.messages), code=problems.primary_code,
                      problems=problems.messages, problem_codes=problems.codes, evidence=evidence)
    return passed(
        f"{len(views)} page(s) load cleanly; every declared control, layer group, panel and reset "
        "works through the state protocol",
        evidence=evidence,
    )


def dashboard_loads_in_browser(
    workspace: Path,
    project_dir: str = ".",
    dashboard: str = "dashboard.html",
    screenshots_dir: str | None = None,
    desktop_size: str = "1280x800",
    mobile_size: str = "390x844",
    settle_ms: int = 800,
) -> AssertionResult:
    """Open the generated dashboard in headless Chromium and verify the
    manifest's presentation claims against the actually rendered product.

    Fails on page/console errors, absent map, blank map, absent
    legend/provenance panels that the manifest declares visible, manifest
    warnings not visible in the product, layer controls missing or not
    affecting the render, a scenario control whose layer is
    indistinguishable from the baseline, and a broken canonical reset.
    Captures desktop and mobile screenshots as retained evidence.

    Returns ``not_testable`` only when the environment cannot run the check
    at all (no Playwright, no launchable browser). Once the dashboard is
    open, every outcome -- including an unexpected exception -- is graded as
    evidence about the product.
    """
    proj = load_project_yaml(workspace, project_dir)
    if proj is None:
        return failed("project.yaml missing", code="manifest_missing")
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", project_dir.strip("./")) or "project"
    if get_in(proj, "presentation.design_language") in DESIGN_LANGUAGES:
        with tempfile.TemporaryDirectory(prefix="openmapstack-visual-") as tmp:
            return _protocol_check(
                proj, project_root(workspace, project_dir), dashboard=dashboard, label=label,
                screenshot_dir=workspace / screenshots_dir if screenshots_dir else Path(tmp),
                desktop_size=desktop_size, mobile_size=mobile_size, settle_ms=settle_ms,
            )
    dashboard_path = project_root(workspace, project_dir) / dashboard
    if not dashboard_path.exists():
        return failed(f"{dashboard} does not exist", code="file_missing")

    try:
        sync_playwright = _playwright()
    except ImportError:
        return not_testable(
            "Playwright is not installed in this execution environment", code="playwright_unavailable"
        )


    warnings = proj.get("warnings") or []
    layer_groups = get_in(proj, "presentation.map.layer_groups", []) or []
    scenarios = get_in(proj, "presentation.controls.scenarios", []) or []
    canonical_reset = bool(get_in(proj, "presentation.controls.canonical_reset"))

    # Screenshot comparisons (toggle effects, scenario distinguishability,
    # blank-map detection) always run. When no retained screenshots_dir is
    # declared, a throwaway temp directory holds the intermediate frames.
    tmp_context = None
    if screenshots_dir:
        screenshot_dir = workspace / screenshots_dir
    else:
        tmp_context = tempfile.TemporaryDirectory(prefix="openmapstack-visual-")
        screenshot_dir = Path(tmp_context.name)

    # Everything up to and including opening the page is about the execution
    # environment; once the dashboard is open, an exception is evidence about
    # the product and must be reported as a failure, never as "could not
    # check" -- otherwise a hanging or self-destructing dashboard would score
    # the same as a machine without a browser.
    dashboard_opened = False

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                return not_testable(
                    f"headless browser unavailable in this environment: {exc}",
                    code="browser_unavailable",
                )
            try:
                context = browser.new_context(viewport=_viewport(desktop_size))
                page = context.new_page()
                page_errors: list[str] = []
                console_errors: list[str] = []
                requested_urls: list[str] = []
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                page.on(
                    "console",
                    lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
                )
                page.on("request", lambda request: requested_urls.append(request.url))
                # `domcontentloaded` rather than the default `load`: a slow or
                # unreachable third-party subresource must not decide whether
                # the dashboard is judged at all. `_settle` then gives tiles
                # and rendering their chance to finish.
                page.goto(dashboard_path.as_uri(), wait_until="domcontentloaded")
                dashboard_opened = True
                _settle(page, settle_ms)

                if page_errors:
                    return failed(
                        f"dashboard raised {len(page_errors)} page error(s): {page_errors[:3]}",
                        code="browser_page_error",
                        errors=page_errors,
                    )
                if console_errors:
                    return failed(
                        f"dashboard logged {len(console_errors)} console error(s): {console_errors[:3]}",
                        code="browser_console_error",
                        errors=console_errors,
                    )

                problems = _Problems()
                evidence: dict[str, Any] = {}

                # --- map present and substantive -------------------------
                if _first_visible(page, _MAP_SELECTOR) is None:
                    problems.add("map_absent", "no visible map element")
                else:
                    shot = screenshot_dir / f"{label}-desktop.png"
                    error = _screenshot_map(page, shot)
                    if error:
                        problems.add("map_screenshot_failed", f"desktop map screenshot: {error}")
                    else:
                        stats = image_stats(shot)
                        evidence["desktop_map_stats"] = stats
                        if _is_blank(stats):
                            problems.add("blank_map", "map renders blank on desktop")

                _panel_and_basemap_problems(
                    page, problems, presentation=proj.get("presentation") or {},
                    warnings=warnings, requested_urls=requested_urls,
                )

                # --- layer toggles must affect the render ----------------
                checkboxes = page.query_selector_all('input[type="checkbox"][data-layer-group]')
                if layer_groups and not checkboxes:
                    problems.add("layer_toggles_absent", "manifest declares layer groups but the product has no layer toggles")
                initial_states = _checkbox_states(page)
                for group in layer_groups:
                    group_id = group.get("id")
                    control = page.query_selector(f'input[type="checkbox"][data-layer-group="{group_id}"]')
                    if control is None:
                        problems.add("layer_group_not_rendered", f"layer group {group_id} has no toggle control")
                        continue
                    problem, fraction = _toggle_changes_render(
                        page,
                        control,
                        baseline=screenshot_dir / f"{label}-group-{group_id}-before.png",
                        toggled=screenshot_dir / f"{label}-group-{group_id}-after.png",
                        settle_ms=settle_ms,
                    )
                    if problem is not None:
                        problems.add("layer_group_not_rendered", f"layer group {group_id}: {problem}")
                    elif fraction is not None:
                        evidence.setdefault("toggle_diff_fraction", {})[f"group:{group_id}"] = fraction

                # --- scenario layer must be distinguishable --------------
                for scenario in scenarios:
                    scenario_id = scenario.get("id")
                    control = page.query_selector(f'input[type="checkbox"][data-scenario="{scenario_id}"]')
                    if control is None:
                        problems.add("scenario_layer_indistinguishable", f"scenario {scenario_id} has no toggle control")
                        continue
                    problem, fraction = _toggle_changes_render(
                        page,
                        control,
                        baseline=screenshot_dir / f"{label}-scenario-{scenario_id}-before.png",
                        toggled=screenshot_dir / f"{label}-scenario-{scenario_id}-after.png",
                        settle_ms=settle_ms,
                        no_effect_detail="is indistinguishable from the authoritative baseline when toggled off",
                    )
                    if problem is not None:
                        problems.add("scenario_layer_indistinguishable", f"scenario {scenario_id}: {problem}")
                    elif fraction is not None:
                        evidence.setdefault("toggle_diff_fraction", {})[f"scenario:{scenario_id}"] = fraction

                # --- canonical reset -------------------------------------
                if canonical_reset:
                    reset = _first_visible(page, _RESET_SELECTOR)
                    if reset is None:
                        buttons = page.query_selector_all("button")
                        reset = next(
                            (b for b in buttons if re.search(r"reset|canonical", (b.inner_text() or "").lower())),
                            None,
                        )
                    if reset is None:
                        problems.add("canonical_reset_failed", "manifest declares canonical_reset but no reset control exists")
                    else:
                        for cb in page.query_selector_all('input[type="checkbox"]'):
                            cb.uncheck()
                        page.wait_for_timeout(settle_ms)
                        reset.click()
                        page.wait_for_timeout(settle_ms)
                        if _checkbox_states(page) != initial_states:
                            problems.add("canonical_reset_failed", "canonical reset does not restore the canonical control state")

                # --- mobile snapshot --------------------------------------
                mobile_context = browser.new_context(viewport=_viewport(mobile_size))
                mobile_page = mobile_context.new_page()
                mobile_page.goto(dashboard_path.as_uri(), wait_until="domcontentloaded")
                _settle(mobile_page, settle_ms)
                mobile_shot = screenshot_dir / f"{label}-mobile.png"
                error = _screenshot_map(mobile_page, mobile_shot)
                if error:
                    problems.add("map_screenshot_failed", f"mobile map screenshot: {error}")
                else:
                    stats = image_stats(mobile_shot)
                    evidence["mobile_map_stats"] = stats
                    if _is_blank(stats):
                        problems.add("blank_map", "map renders blank on mobile viewport")
                mobile_context.close()
                context.close()

                if problems:
                    return failed(
                        "; ".join(problems.messages),
                        code=problems.primary_code,
                        problems=problems.messages,
                        problem_codes=problems.codes,
                    )
                return passed(
                    "dashboard loads cleanly; map, legend, provenance, toggles, "
                    "scenario and canonical reset all render as the manifest declares",
                    evidence=evidence,
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        if dashboard_opened:
            return failed(
                f"browser validation crashed while inspecting the opened dashboard: "
                f"{type(exc).__name__}: {exc}",
                code="browser_check_error",
            )
        return not_testable(f"browser validation could not run: {type(exc).__name__}: {exc}", code="browser_error")
    finally:
        if tmp_context is not None:
            tmp_context.cleanup()


def _viewport(size: str) -> dict[str, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", size.strip())
    if not match:
        raise ValueError(f"invalid viewport size {size!r}; expected WxH")
    return {"width": int(match.group(1)), "height": int(match.group(2))}
