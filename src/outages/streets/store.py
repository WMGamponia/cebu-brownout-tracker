"""`street_matches` rows: read the inputs a match needs, write its results (spec 4.5)."""

from __future__ import annotations

import json
import sqlite3

from outages.config import VECO_TOWNS

PLACED = ("exact", "alias", "ambiguous")


def _chunks(xs: list[str], n: int = 500):
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def scope_inputs(
    conn: sqlite3.Connection, hashes: list[str]
) -> dict[str, tuple[list[str], list[str]]]:
    """areas_hash -> (placed barangay PSGCs in parse order, listed VECO town PSGCs)."""
    out: dict[str, tuple[list[str], list[str]]] = {}
    for chunk in _chunks(hashes):
        marks = ",".join("?" * len(chunk))
        for h, towns_json in conn.execute(
            f"SELECT areas_hash, towns_json FROM area_parses WHERE areas_hash IN ({marks})", chunk
        ):
            towns = [VECO_TOWNS[t] for t in json.loads(towns_json) if t in VECO_TOWNS]
            out[h] = ([], towns)
        for h, psgc in conn.execute(
            f"SELECT areas_hash, psgc_code FROM area_barangays WHERE areas_hash IN ({marks})"
            f" AND match IN {PLACED} AND psgc_code IS NOT NULL ORDER BY areas_hash, seq",
            chunk,
        ):
            if h in out and psgc not in out[h][0]:
                out[h][0].append(psgc)
    return out
