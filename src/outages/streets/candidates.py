"""Labelling helper (Streets v2 spec 6.2): every OSM way name in a row's scope, all name tags.

Read-only and offline. It shows what a labeller may choose from, never what the matcher would
pick, and never a row's `gold`.
"""

from __future__ import annotations

from collections import Counter

from outages.config import VECO_TOWNS
from outages.osm.extract import StreetIndex, primary_name
from outages.streets.match import scope_for, search_towns


def _inside_twice(coords, scope) -> bool:
    w_, s_, e_, n_ = scope.bbox
    inside = 0
    for p in coords:
        if w_ <= p[0] <= e_ and s_ <= p[1] <= n_ and scope.contains(p):
            inside += 1
            if inside >= 2:
                return True
    return False


def in_scope(row: dict, index: StreetIndex, boundaries: dict) -> list[dict]:
    """One entry per distinct set of name tags among the ways with ≥ 2 nodes inside the row's
    scope + buffer (as `names_in`): `{"primary", "tags", "ways"}`, most ways first."""
    towns = [VECO_TOWNS[t] for t in row.get("towns", []) if t in VECO_TOWNS]
    brgys = row.get("barangays", [])
    scope = scope_for(brgys, towns, boundaries)
    if scope is None:
        return []
    search = set(search_towns(brgys, towns, boundaries))
    counts: Counter[tuple[tuple[str, str], ...]] = Counter()
    primaries: dict[tuple[tuple[str, str], ...], str | None] = {}
    for w in index.ways:
        if w.town_psgc not in search or not w.names or not _inside_twice(w.coords, scope):
            continue
        tags = tuple(sorted(w.names.items()))
        counts[tags] += 1
        primaries[tags] = primary_name(w)
    out = [{"primary": primaries[t], "tags": dict(t), "ways": n} for t, n in counts.items()]
    return sorted(out, key=lambda c: (-c["ways"], c["primary"] or ""))


def format_row(row: dict, cands: list[dict]) -> str:
    """The lines `streets candidates` prints for a row: id, VECO name, then each candidate."""
    lines = [f"{row.get('id', '?')}  {row.get('name', '')}"]
    if not cands:
        lines.append("    (no named OSM way in scope)")
    for c in cands:
        tags = " ".join(f"{k}={v}" for k, v in c["tags"].items())
        lines.append(f"    {c['primary']}  [{tags}]  ({c['ways']} ways)")
    return "\n".join(lines)
