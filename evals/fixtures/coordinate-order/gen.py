#!/usr/bin/env python3
"""Offline API-boundary control and single-interface coordinate-swap mutations."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def generate(output_dir: Path, swap: str | None = None) -> None:
    source = Path(__file__).with_name("anchor.json")
    anchor = json.loads(source.read_text(encoding="utf-8"))
    lon, lat = anchor["longitude"], anchor["latitude"]
    pairs = {
        "geojson": [lon, lat],
        "maplibre": [lon, lat],
        "leaflet_latlng": [lat, lon],
        "leaflet_geojson": [lon, lat],
    }
    if swap:
        pairs[swap].reverse()
    payload = {
        key: {"type": "Point", "coordinates": pair} if key.endswith("geojson") else pair
        for key, pair in pairs.items()
    }
    destination = output_dir / "data/source/anchor.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    (output_dir / "coordinate-payloads.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--swap", choices=["geojson", "maplibre", "leaflet_latlng", "leaflet_geojson"])
    args = parser.parse_args()
    generate(args.output_dir, args.swap)
