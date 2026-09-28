"""The published site uses the checked-in example dashboards unchanged."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree

from scripts.build_project_pages import ROOT, build


class ProjectPagesTests(unittest.TestCase):
    def test_build_contains_both_dashboards_and_resolved_local_links(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(output)
            homepage = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="./demos/nyc/"', homepage)
            self.assertIn('href="./demos/tartu/"', homepage)
            for relative in (
                "styles.css", "assets/nyc-preview.svg", "assets/tartu-preview.svg",
                "demos/nyc/index.html", "demos/tartu/index.html",
                "demos/nyc/data/derived/hub-candidates.geojson",
                "demos/nyc/data/derived/zone-metrics.geojson", ".nojekyll",
            ):
                self.assertTrue((output / relative).exists(), relative)
            for example, slug in (("nyc-private-mobility", "nyc"),
                                  ("tartu-development", "tartu")):
                self.assertEqual((ROOT / "examples" / example / "dashboard.html").read_bytes(),
                                 (output / "demos" / slug / "index.html").read_bytes())
            self.assertIn("License not stated in ArcGIS item metadata",
                          (output / "demos/tartu/index.html").read_text(encoding="utf-8"))
            for name in ("nyc-preview.svg", "tartu-preview.svg"):
                preview = ElementTree.parse(output / "assets" / name).getroot()
                self.assertTrue(any(child.tag.endswith("path") for child in preview))

    def test_build_refuses_to_write_into_source_directories(self) -> None:
        for destination in (ROOT, ROOT / "site", ROOT / "examples" / "nyc-private-mobility"):
            with self.assertRaises(ValueError):
                build(destination)


if __name__ == "__main__":
    unittest.main()
