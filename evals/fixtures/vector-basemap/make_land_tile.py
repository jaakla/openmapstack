#!/usr/bin/env python3
"""Rebuild the synthetic full-tile land polygon used by browser tests."""
from pathlib import Path

def _varint(value: int) -> bytes:
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field(number: int, value: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(value)) + value


# MVT v2: layer "land", extent 4096, polygon (0,0)-(4096,4096).
# Geometry commands: MoveTo(1), LineTo(3), ClosePath(1), zigzag deltas.
_geometry = b"".join(_varint(v) for v in [9, 0, 0, 26, 8192, 0, 0, 8192, 8191, 0, 15])
_feature = b"\x08\x01\x18\x03" + _field(4, _geometry)
LAND_TILE = _field(3, _field(1, b"land") + _field(2, _feature) + b"\x28\x80\x20\x78\x02")


if __name__ == "__main__":
    Path(__file__).with_name("land.mvt").write_bytes(LAND_TILE)
