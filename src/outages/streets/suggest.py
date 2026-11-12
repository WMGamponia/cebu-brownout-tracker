"""Alias suggester: group `not_found` street names by (town, key), list the OSM names in
scope, ask a picker for the closest, queue the pick for the owner (Streets v2 spec 7)."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from outages.osm.extract import StreetIndex
from outages.streets.evaluate import TEST_ROWS, street_rows
from outages.streets.match import scope_for, search_towns
from outages.streets.normalize import GENERIC_STREET_KEYS, street_key

SCOPE_MENTIONS = 20  # candidates come from the first this-many mentions' scopes
EXAMPLES = 3


@dataclass(frozen=True)
class Group:
    town_psgc: str
    key: str
    veco_name: str
    mentions: list[tuple[str, int]]
    areas_examples: list[str]


def groups(
    conn: sqlite3.Connection, test_ids: set[tuple[str, int]], skip: set[tuple[str, str]]
) -> list[Group]:
    """`not_found` street mentions grouped by (town, key), test rows and `skip` pairs left
    out, most mentions first, then key."""
    not_found = set(
        conn.execute("SELECT areas_hash, seq FROM street_matches WHERE status = 'not_found'")
    )
    by: dict[tuple[str, str], list] = {}
    for r in street_rows(conn):
        if (r.areas_hash, r.seq) not in not_found or (r.areas_hash, r.seq) in test_ids:
            continue
        key = street_key(r.name)
        if not key or key in GENERIC_STREET_KEYS or r.town_psgc is None:
            continue
        if (r.town_psgc, key) in skip:
            continue
        by.setdefault((r.town_psgc, key), []).append(r)
    out = []
    for (town, key), rows in by.items():
        rows.sort(key=lambda r: (r.areas_hash, r.seq))
        spellings = Counter(r.name for r in rows)
        veco = min(spellings, key=lambda n: (-spellings[n], n))
        examples: list[str] = []
        for r in rows:
            if r.areas_raw not in examples and len(examples) < EXAMPLES:
                examples.append(r.areas_raw)
        out.append(Group(town, key, veco, [(r.areas_hash, r.seq) for r in rows], examples))
    return sorted(out, key=lambda g: (-len(g.mentions), g.key, g.town_psgc))


def candidates(
    group: Group,
    index: StreetIndex,
    boundaries: dict,
    inputs: dict[str, tuple[list[str], list[str]]],
    k: int = 12,
) -> list[str]:
    """The k in-scope OSM names closest to the group's key (its own key excluded)."""
    by_key: dict[str, str] = {}
    for h, _ in sorted(group.mentions)[:SCOPE_MENTIONS]:
        if h not in inputs:
            continue
        brgys, towns = inputs[h]
        scope = scope_for(brgys, towns, boundaries)
        if scope is None:
            continue
        for n in index.names_in(search_towns(brgys, towns, boundaries), scope):
            nk = street_key(n)
            if nk and nk != group.key:
                by_key.setdefault(nk, n)

    def rank(item: tuple[str, str]) -> tuple[float, str]:
        return (-SequenceMatcher(None, group.key, item[0]).ratio(), item[1])

    return [n for _, n in sorted(by_key.items(), key=rank)[:k]]


def log_inputs(path: Path, group: Group, now: datetime) -> None:
    """Append one line per mention shown to the picker (checked by the test-run guard)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for h, seq in group.mentions:
            row = {
                "areas_hash": h,
                "seq": seq,
                "town_psgc": group.town_psgc,
                "key": group.key,
                "at": now.isoformat(),
            }
            f.write(json.dumps(row) + "\n")


def load_test_ids(eval_dir: Path) -> set[tuple[str, int]]:
    path = eval_dir / TEST_ROWS
    if not path.is_file():
        return set()
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {(r["areas_hash"], r["seq"]) for r in rows}
