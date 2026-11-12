"""Review queue: `not_found` street names by frequency, with close OSM names in scope.

Suggestions come from `difflib` and are never applied: an accepted one goes into
`areas/street_aliases.csv` by hand (spec 4.5)."""

from __future__ import annotations

import difflib
import sqlite3
from dataclasses import dataclass

from outages.osm.extract import StreetIndex
from outages.streets.match import scope_for, search_towns
from outages.streets.normalize import street_key
from outages.streets.store import scope_inputs


@dataclass(frozen=True)
class ReviewItem:
    name: str
    count: int
    suggestions: list[str]
    town_psgc: str | None = None


def review(
    conn: sqlite3.Connection, index: StreetIndex | None, boundaries: dict
) -> list[ReviewItem]:
    """Grouped by (town, key), like the suggester: one street key can be two roads in two
    towns."""
    rows = conn.execute(
        "SELECT e.areas_hash, e.name FROM street_matches m JOIN area_entities e"
        " ON e.areas_hash = m.areas_hash AND e.seq = m.seq"
        " WHERE m.status = 'not_found' ORDER BY e.areas_hash, e.seq"
    ).fetchall()
    inputs = scope_inputs(conn, sorted({r[0] for r in rows}))
    groups: dict[tuple[str | None, str], list[tuple[str, str]]] = {}
    for h, name in rows:
        brgys, towns = inputs.get(h, ([], []))
        town = next(iter(search_towns(brgys, towns, boundaries)), None)
        groups.setdefault((town, street_key(name)), []).append((h, name))
    items = []
    for (town, key), mentions in groups.items():
        h, name = mentions[0]
        suggestions: list[str] = []
        if index is not None and h in inputs:
            brgys, towns = inputs[h]
            scope = scope_for(brgys, towns, boundaries)
            if scope is not None:
                names = index.names_in(search_towns(brgys, towns, boundaries), scope)
                by_key: dict[str, str] = {}
                for n in names:
                    by_key.setdefault(street_key(n), n)
                close = difflib.get_close_matches(key, list(by_key), n=3, cutoff=0.75)
                suggestions = [by_key[k] for k in close]
        items.append(ReviewItem(name, len(mentions), suggestions, town))
    return sorted(items, key=lambda i: (-i.count, i.name, i.town_psgc or ""))
