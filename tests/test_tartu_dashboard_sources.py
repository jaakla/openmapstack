"""Layer provenance shown by the Tartu example dashboard."""

import unittest
from pathlib import Path


DASHBOARD = Path(__file__).resolve().parents[1] / "examples/tartu-development/dashboard.html"


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
                self.assertIn("dark-matter-gl-style/style.json", page.locator("#basemapLineage").inner_text())
                self.assertEqual(page.locator("#basemapLineage .lineage-stage").count(), 1)


if __name__ == "__main__":
    unittest.main()
