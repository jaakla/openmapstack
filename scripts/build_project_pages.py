#!/usr/bin/env python3
"""Assemble the static project homepage and two generated example dashboards."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
NYC = ROOT / "examples" / "nyc-private-mobility"
TARTU = ROOT / "examples" / "tartu-development"
WIDTH, HEIGHT = 720, 260


def _projector(bounds: tuple[float, float, float, float]):
    west, south, east, north = bounds
    mid_lat = (south + north) / 2
    lon_factor = math.cos(math.radians(mid_lat))
    span_x = (east - west) * lon_factor
    span_y = north - south
    if not span_x or not span_y:
        raise ValueError("map preview has empty geographic bounds")
    scale = min((WIDTH - 44) / span_x, (HEIGHT - 32) / span_y)
    left = (WIDTH - span_x * scale) / 2
    top = (HEIGHT - span_y * scale) / 2

    def point(coordinates: list[float]) -> tuple[float, float]:
        lon, lat = coordinates[:2]
        return (left + (lon - west) * lon_factor * scale,
                top + (north - lat) * scale)

    return point


def _path(geometry: dict, project) -> str:
    kind = geometry["type"]
    coordinates = geometry["coordinates"]
    if kind == "LineString":
        parts = [coordinates]
    elif kind == "MultiLineString":
        parts = coordinates
    elif kind == "Polygon":
        parts = coordinates
    elif kind == "MultiPolygon":
        parts = [ring for polygon in coordinates for ring in polygon]
    else:
        return ""
    out = []
    for part in parts:
        if len(part) < 2:
            continue
        points = [project(coord) for coord in part]
        out.append("M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in points)
                   + ("Z" if kind in ("Polygon", "MultiPolygon") else ""))
    return " ".join(out)


def _svg_start(label: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{html.escape(label)}">',
        '<rect width="720" height="260" fill="#edf3f3"/>',
        '<path d="M0 52H720M0 104H720M0 156H720M0 208H720'
        'M72 0V260M144 0V260M216 0V260M288 0V260M360 0V260'
        'M432 0V260M504 0V260M576 0V260M648 0V260"'
        ' stroke="#dce7e7" stroke-width=".8" fill="none"/>',
    ]


def _nyc_preview(destination: Path) -> None:
    zones = json.loads((NYC / "data/derived/zone-metrics.geojson").read_text())["features"]
    fleet = json.loads((NYC / "data/derived/fleet-positions.geojson").read_text())["features"]
    coordinates = [coord for feature in zones
                   for ring in feature["geometry"]["coordinates"] for coord in ring]
    bounds = (min(c[0] for c in coordinates), min(c[1] for c in coordinates),
              max(c[0] for c in coordinates), max(c[1] for c in coordinates))
    project = _projector(bounds)
    out = _svg_start("NYC H3 analysis cells and fleet points")
    for feature in zones:
        fill = "#88b8b6" if feature["properties"].get("eligible") else "#d0dddd"
        out.append(f'<path d="{_path(feature["geometry"], project)}" fill="{fill}" '
                   'stroke="#fff" stroke-width="1.5"/>')
    for feature in fleet:
        x, y = project(feature["geometry"]["coordinates"])
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.5" fill="#4a6179" opacity=".72"/>')
    out.append('</svg>')
    destination.write_text("\n".join(out) + "\n", encoding="utf-8")


def _dashboard_constant(name: str) -> dict:
    prefix = f"const {name} = "
    for line in (TARTU / "dashboard.html").read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return json.loads(line[len(prefix):].removesuffix(";"))
    raise ValueError(f"Tartu dashboard lacks {name} payload")


def _tartu_preview(destination: Path) -> None:
    west, south = _dashboard_constant("VIEW")["bounds"][0]
    east, north = _dashboard_constant("VIEW")["bounds"][1]
    project = _projector((west, south, east, north))
    roads = _dashboard_constant("ROADS")["features"]
    parcels = _dashboard_constant("CANDIDATES")["features"]
    out = _svg_start("Tartu roads and candidate parcels")
    for feature in roads:
        out.append(f'<path d="{_path(feature["geometry"], project)}" fill="none" '
                   'stroke="#bacdca" stroke-width=".8" opacity=".75"/>')
    for feature in parcels:
        tier = feature["properties"].get("suitability_tier", "")
        fill = "#168b85" if tier.startswith("Tier 1") else (
            "#b5a06a" if tier.startswith("Tier 2") else "#aebfbd")
        out.append(f'<path d="{_path(feature["geometry"], project)}" fill="{fill}" '
                   'stroke="#fff" stroke-width=".45" opacity=".88"/>')
    out.append('</svg>')
    destination.write_text("\n".join(out) + "\n", encoding="utf-8")


def build(destination: Path) -> None:
    destination = destination.resolve()
    if destination in (ROOT, SITE, NYC, TARTU):
        raise ValueError("output must be a dedicated build directory")
    assets = destination / "assets"
    nyc = destination / "demos" / "nyc"
    tartu = destination / "demos" / "tartu"
    for directory in (assets, nyc / "data" / "derived", tartu):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SITE / "index.html", destination / "index.html")
    shutil.copy2(SITE / "styles.css", destination / "styles.css")
    shutil.copy2(NYC / "dashboard.html", nyc / "index.html")
    shutil.copy2(TARTU / "dashboard.html", tartu / "index.html")
    for name in ("hub-candidates.geojson", "zone-metrics.geojson"):
        shutil.copy2(NYC / "data" / "derived" / name, nyc / "data" / "derived" / name)
    _nyc_preview(assets / "nyc-preview.svg")
    _tartu_preview(assets / "tartu-preview.svg")
    (destination / ".nojekyll").touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    build(args.output)
    print(f"built GitHub Pages artifact at {args.output.resolve()}")


if __name__ == "__main__":
    main()
