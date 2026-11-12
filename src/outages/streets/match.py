"""Rules + town-scoped aliases: a VECO street name in an outage's scope → OSM lines (spec 4.5).

Pure: no DB, no network. A wrong line is worse than none, so the rules are exact on the
normalised key; fuzzy similarity only feeds the review queue.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from outages.osm.extract import StreetIndex
from outages.streets.geo import Point, Scope, clip, polygons_of
from outages.streets.normalize import GENERIC_STREET_KEYS, street_key

ALIASES_PATH = Path(__file__).resolve().parent.parent / "areas" / "street_aliases.csv"


@dataclass(frozen=True)
class Match:
    status: str  # matched | alias | not_found | no_osm_data
    osm_name: str | None = None
    lines: list[list[Point]] = field(default_factory=list)


NOT_FOUND = Match("not_found")


def load_aliases(path: Path = ALIASES_PATH) -> dict[tuple[str, str], str]:
    """(town_psgc, street_key of VECO's name) -> OSM name."""
    with path.open(encoding="utf-8", newline="") as f:
        return {
            (r["town_psgc"].strip(), street_key(r["veco_key"])): r["osm_name"].strip()
            for r in csv.DictReader(f)
            if r.get("town_psgc") and r.get("veco_key") and r.get("osm_name")
        }


def _features(boundaries: dict) -> dict[str, dict]:
    return {f["properties"]["psgc"]: f for f in boundaries["features"]}


def scope_for(barangay_psgcs: list[str], town_psgcs: list[str], boundaries: dict) -> Scope | None:
    """The placed barangays' polygons; else every barangay of the listed towns; else None."""
    feats = _features(boundaries)
    polys = [p for c in barangay_psgcs if c in feats for p in polygons_of(feats[c])]
    if not barangay_psgcs:
        towns = set(town_psgcs)
        polys = [
            p
            for f in boundaries["features"]
            if f["properties"].get("town_psgc") in towns
            for p in polygons_of(f)
        ]
    return Scope(polys) if polys else None


def search_towns(barangay_psgcs: list[str], town_psgcs: list[str], boundaries: dict) -> list[str]:
    """Towns whose extract is searched: the placed barangays' towns, else the listed towns."""
    if not barangay_psgcs:
        return list(town_psgcs)
    feats = _features(boundaries)
    out: list[str] = []
    for c in barangay_psgcs:
        t = feats[c]["properties"].get("town_psgc") if c in feats else None
        if t and t not in out:
            out.append(t)
    return out


def match_street(
    name: str,
    scope: Scope | None,
    towns: list[str],
    index: StreetIndex | None,
    aliases: dict[tuple[str, str], str],
) -> Match:
    """`towns` are the town PSGCs whose ways are searched (the scope's towns)."""
    if scope is None:
        return NOT_FOUND
    if index is None:
        return Match("no_osm_data")
    key = street_key(name)
    status = "matched"
    for t in towns:
        if (t, key) in aliases:
            key, status = street_key(aliases[(t, key)]), "alias"
            break
    if not key or (status == "matched" and key in GENERIC_STREET_KEYS):
        return NOT_FOUND
    names: Counter[str] = Counter()
    lines: list[list[Point]] = []
    seen: set[int] = set()
    for t in towns:
        for way in index.ways_named(t, key):
            if way.id in seen:
                continue
            seen.add(way.id)
            runs = clip(way.coords, scope)
            if runs:
                lines += runs
                names[way.names.get("name", "")] += 1
    if not lines:
        return NOT_FOUND
    osm_name = min(names, key=lambda n: (-names[n], n))
    return Match(status, osm_name or None, lines)
