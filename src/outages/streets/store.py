"""`street_matches` rows: read the inputs a match needs, write its results (spec 4.5)."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from outages.config import VECO_TOWNS
from outages.osm.extract import StreetIndex
from outages.streets.geo import round_lines
from outages.streets.match import load_aliases, match_street, scope_for, search_towns

DRAWN = ("matched", "alias")

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


@dataclass
class MatchSummary:
    written: int = 0
    by_status: dict[str, int] = field(default_factory=dict)


def _has_table(conn: sqlite3.Connection) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='street_matches'"
        ).fetchone()
    )


def _todo(conn: sqlite3.Connection, osm_date: str | None, rebuild: bool) -> list[str]:
    """Hashes to (re)match: all with street entities on rebuild; else those with an entity
    lacking a row, or a row made from another extract (or none)."""
    if rebuild or not _has_table(conn):
        return [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT areas_hash FROM area_entities WHERE kind = 'street'"
                " ORDER BY areas_hash"
            )
        ]
    return [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT e.areas_hash FROM area_entities e LEFT JOIN street_matches m"
            " ON m.areas_hash = e.areas_hash AND m.seq = e.seq WHERE e.kind = 'street'"
            " AND (m.status IS NULL OR m.osm_date IS NOT ?) ORDER BY e.areas_hash",
            (osm_date,),
        )
    ]


def run_match(
    conn: sqlite3.Connection,
    index: StreetIndex | None,
    boundaries: dict,
    rebuild: bool = False,
    dry_run: bool = False,
) -> MatchSummary:
    """Match street entities into `street_matches`, one transaction per 500 hashes. Rows whose
    entity is gone (or no longer a street) are deleted. A dry run writes nothing and works
    read-only, even before migration 004."""
    osm_date = index.osm_base if index else None
    aliases = load_aliases()
    summary = MatchSummary()
    counts: Counter[str] = Counter()
    if not dry_run:
        with conn:
            conn.execute(
                "DELETE FROM street_matches WHERE NOT EXISTS (SELECT 1 FROM area_entities e"
                " WHERE e.areas_hash = street_matches.areas_hash AND e.seq = street_matches.seq"
                " AND e.kind = 'street')"
            )
    for chunk in _chunks(_todo(conn, osm_date, rebuild)):
        inputs = scope_inputs(conn, chunk)
        marks = ",".join("?" * len(chunk))
        ents = conn.execute(
            f"SELECT areas_hash, seq, name FROM area_entities WHERE kind = 'street'"
            f" AND areas_hash IN ({marks}) ORDER BY areas_hash, seq",
            chunk,
        ).fetchall()
        rows = []
        scopes: dict[str, tuple] = {}
        for h, seq, name in ents:
            if h not in scopes:
                brgys, towns = inputs.get(h, ([], []))
                scopes[h] = (
                    scope_for(brgys, towns, boundaries),
                    search_towns(brgys, towns, boundaries),
                )
            scope, search = scopes[h]
            m = match_street(name, scope, search, index, aliases)
            counts[m.status] += 1
            geom = (
                json.dumps(round_lines(m.lines), separators=(",", ":"))
                if m.status in DRAWN
                else None
            )
            rows.append((h, seq, m.status, m.osm_name, geom, osm_date))
        if dry_run:
            continue
        with conn:
            conn.execute(f"DELETE FROM street_matches WHERE areas_hash IN ({marks})", chunk)
            conn.executemany(
                "INSERT INTO street_matches (areas_hash, seq, status, osm_name, geometry_json,"
                " osm_date) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        summary.written += len(rows)
    summary.by_status = dict(sorted(counts.items()))
    return summary
