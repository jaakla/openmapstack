#!/usr/bin/env python3
"""Pin a small TLC pickup-coordinate sample for the synthetic fleet seed.

TLC publishes trip pickups, not Northstar vehicle positions. The coordinates
are reused only as plausible point locations for explicitly synthetic demo
vehicles. A saved capture is immutable; rerunning this script cannot silently
change an accepted seed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import h3

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data/source/tlc-2016-pickups.json"
CATALOGUE = ROOT / "setup/h3/cells.json"
DATASET = "https://data.cityofnewyork.us/Transportation/2016-Yellow-Taxi-Trip-Data/uacg-pexx"
API = "https://data.cityofnewyork.us/resource/uacg-pexx.json"
QUERY = {"$select": "tpep_pickup_datetime,pickup_longitude,pickup_latitude", "$limit": "10000"}
MAX_PER_CELL = 5


def select_points(rows: list[dict], cells: set[str]) -> list[dict]:
    by_cell: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, float, float]] = set()
    for row in rows:
        try:
            timestamp = str(row["tpep_pickup_datetime"])
            longitude = float(row["pickup_longitude"])
            latitude = float(row["pickup_latitude"])
            cell = h3.latlng_to_cell(latitude, longitude, 8)
        except (KeyError, TypeError, ValueError):
            continue
        if cell not in cells or (timestamp, longitude, latitude) in seen:
            continue
        seen.add((timestamp, longitude, latitude))
        by_cell[cell].append({"pickup_datetime": timestamp, "longitude": longitude,
                              "latitude": latitude, "h3_cell": cell})
    return [row for cell in sorted(by_cell)
            for row in sorted(by_cell[cell], key=lambda item: (
                item["pickup_datetime"], item["longitude"], item["latitude"]))[:MAX_PER_CELL]]


def main() -> None:
    import requests

    if OUTPUT.exists():
        raise SystemExit(f"refusing to overwrite pinned TLC sample: {OUTPUT}")
    url = API + "?" + urlencode(QUERY)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    raw = response.content
    rows = json.loads(raw)
    cells = {row["h3_cell"] for row in json.loads(CATALOGUE.read_text())}
    selected = select_points(rows, cells)
    if len(selected) < 150 or len({row["h3_cell"] for row in selected}) < 50:
        raise SystemExit("TLC response did not cover enough study cells; no capture written")
    record = {
        "schema": "northstar-tlc-pickup-coordinate-seed/v1",
        "source_page": DATASET,
        "api_url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "response_sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "response_rows": len(rows),
        "selection": "Valid pickup coordinates within pinned H3 study cells; first five by timestamp and coordinate per cell from the bounded API response. This is not a representative or current fleet sample.",
        "selected_rows": len(selected),
        "study_cells": len({row["h3_cell"] for row in selected}),
        "records": selected,
    }
    OUTPUT.write_text(json.dumps(record, indent=2) + "\n")
    print(f"captured {len(selected)} TLC pickup coordinates in {record['study_cells']} H3 cells")


if __name__ == "__main__":
    main()
