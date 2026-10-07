"""Layer provenance shown by the Tartu example dashboard."""

import unittest
import json
import re
import tempfile
from pathlib import Path


DASHBOARD = Path(__file__).resolve().parents[1] / "examples/tartu-development/dashboard.html"
VENDOR = DASHBOARD.parents[2] / "evals/fixtures/vendor/maplibre-gl-3.6.2"


def route_basemap(page):
    """Real MapLibre renderer with deterministic, offline provider responses."""
    seen = []
    script = (VENDOR / "maplibre-gl.js").read_text() + """
const TestMap = maplibregl.Map;
maplibregl.Map = class extends TestMap {
  constructor(options) {
    super(options); window.testMap = this;
    this.on("style.load", () => { window.testStyleLoads = (window.testStyleLoads || 0) + 1; });
  }
};
"""
    page.route("https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js",
               lambda route: route.fulfill(body=script, content_type="application/javascript"))
    page.route("https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css",
               lambda route: route.fulfill(body=(VENDOR / "maplibre-gl.css").read_bytes(), content_type="text/css"))

    def provider(route):
        url = route.request.url
        seen.append(url)
        if "/styles/" in url or url.endswith("/style.json"):
            dark = url.endswith("/dark.json")
            body = {"version": 8, "sources": {"candidates": {
                "type": "vector", "tiles": ["https://custom.example/tiles/{z}/{x}/{y}.mvt" if "custom.example" in url
                                            else "https://tiles.goplex.ee/planet-20261006/{z}/{x}/{y}.mvt"]}},
                "layers": [{"id": "background", "type": "background",
                            "paint": {"background-color": "#222222" if dark else "#eeeeee"}},
                           {"id": "land", "type": "fill", "source": "candidates", "source-layer": "land"},
                           {"id": "pois", "type": "fill", "source": "candidates", "source-layer": "land"}]}
        elif url.endswith(".json"):
            body = {"tilejson": "3.0.0", "tiles": ["https://tiles.goplex.ee/planet-20261006/{z}/{x}/{y}.mvt"],
                    "minzoom": 0, "maxzoom": 15}
        else:
            # Serve a real synthetic land polygon through the vector renderer.
            tile = VENDOR.parent.parent / "vector-basemap/land.mvt"
            route.fulfill(body=tile.read_bytes(), content_type="application/x-protobuf",
                          headers={"Access-Control-Allow-Origin": "*"})
            return
        route.fulfill(body=json.dumps(body), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

    page.route("https://tiles.goplex.ee/**", provider)
    page.route("https://custom.example/**", provider)
    return seen


class TartuDashboardSourceTests(unittest.TestCase):
    def test_layer_switches_and_basemap_show_their_sources(self):
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            self.skipTest("Playwright unavailable")
        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Error as error:
                self.skipTest(f"Chromium unavailable: {error}")
            with browser:
                page = browser.new_page(viewport={"width": 1440, "height": 960})
                route_basemap(page)
                page.goto(DASHBOARD.as_uri(), wait_until="domcontentloaded")
                page.locator('[data-tab="map"]').click()
                page.locator('[data-lineage="candidates_tier1"]').wait_for()
                self.assertEqual(page.locator('[data-lineage]').count(), 7)
                self.assertFalse(page.locator('[data-lineage="candidates_tier1"]').evaluate("node => node.open"))
                page.locator('[data-lineage="candidates_tier1"] summary').click()
                self.assertEqual(page.locator('[data-lineage="candidates_tier1"] .stage-name').all_text_contents(),
                                 ["Source", "Snapshots", "Prepared inputs", "Screening", "Final"])
                self.assertIn("data/derived/final-candidates.json",
                              page.locator('[data-lineage="candidates_tier1"]').inner_text())
                self.assertIn("Tartu City Government GIS",
                              page.locator('[data-lineage="candidates_tier1"]').inner_text())
                page.locator('[data-lineage="catchments"] summary').click()
                self.assertIn("data/derived/education_catchment_variants.json",
                              page.locator('[data-lineage="catchments"]').inner_text())
                page.locator('[data-layerrow="infrastructure"]').click()
                self.assertFalse(page.locator('[data-layer="infrastructure"]').is_checked())
                page.locator('[data-lineage="infrastructure"] summary').click()
                self.assertIn("etak:e_501_tee_j",
                              page.locator('[data-lineage="infrastructure"]').inner_text())
                page.locator('[data-acc="basemap"] > summary').click()
                page.locator("#basemapLineage summary").click()
                page.locator('[data-basemap="dark"]').click()
                self.assertIn("styles/5.7.2/dark.json", page.locator("#basemapLineage").inner_text())
                self.assertEqual(page.locator("#basemapLineage .lineage-stage").count(), 1)

    def test_themes_preserve_overlays_and_credit_and_honor_manifest_choice(self):
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            self.skipTest("Playwright unavailable")
        with sync_playwright() as runtime:
            try:
                browser = runtime.chromium.launch(headless=True)
            except Error as error:
                self.skipTest(f"Chromium unavailable: {error}")
            with browser, tempfile.TemporaryDirectory() as directory:
                for custom in (False, True):
                    with self.subTest(custom=custom):
                        html = DASHBOARD.read_text()
                        if custom:
                            match = re.search(r"^const VIEW = (.*);$", html, re.M)
                            view = json.loads(match.group(1))
                            view["basemap"] = {"id": "regional-custom", "kind": "vector-style",
                                               "url": "https://custom.example/style.json",
                                               "attribution": "Regional provider"}
                            html = html[:match.start(1)] + json.dumps(view) + html[match.end(1):]
                        target = Path(directory) / "dashboard.html"
                        target.write_text(html)
                        context = browser.new_context(viewport={"width": 1440, "height": 960}, color_scheme="light")
                        page = context.new_page()
                        seen = route_basemap(page)
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                        page.goto(target.as_uri(), wait_until="domcontentloaded")
                        page.locator('[data-tab="map"]').click()
                        page.wait_for_function("window.testMap && testMap.isStyleLoaded() && !!testMap.getLayer('basemap-land') && !!testMap.getLayer('main-roads')")
                        credit = "Regional provider" if custom else "Protomaps tiles by goplex.ee"
                        self.assertIn(credit, page.locator(".maplibregl-ctrl-attrib").inner_text())
                        expected = "https://custom.example/style.json" if custom else "https://tiles.goplex.ee/styles/5.7.2/white.json"
                        self.assertIn(expected, seen)
                        page.locator('[data-layerrow="infrastructure"]').click()
                        page.locator('[data-acc="basemap"] > summary').click()
                        for theme in ("dark", "light"):
                            loads = page.evaluate("testStyleLoads")
                            page.locator(f'[data-basemap="{theme}"]').click()
                            page.wait_for_function("previous => testStyleLoads > previous && testMap.isStyleLoaded() && !!testMap.getLayer('main-roads')", arg=loads)
                            self.assertEqual(page.evaluate("testMap.getLayoutProperty('main-roads', 'visibility')"), "none")
                            self.assertTrue(page.evaluate("!!testMap.getSource('candidates')"))
                            self.assertIn(credit, page.locator(".maplibregl-ctrl-attrib").inner_text())
                        loads = page.evaluate("testStyleLoads")
                        page.locator('[data-basemap="auto"]').click()
                        page.locator("#themeToggle").click()
                        page.wait_for_function("previous => testStyleLoads > previous && testMap.isStyleLoaded() && !!testMap.getLayer('main-roads')", arg=loads)
                        if custom:
                            self.assertFalse(any("tiles.goplex.ee" in url for url in seen))
                        else:
                            self.assertIn("https://tiles.goplex.ee/planet-20261006.json", seen)
                            self.assertTrue(any(url.endswith(".mvt") for url in seen))
                            self.assertIn("https://tiles.goplex.ee/styles/5.7.2/dark.json", seen)
                        self.assertEqual(errors, [])
                        context.close()


if __name__ == "__main__":
    unittest.main()
