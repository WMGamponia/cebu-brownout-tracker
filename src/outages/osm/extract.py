"""The local OSM extract (`data/osm/`) as an index of ways by town and normalised name."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from outages.osm.overpass import MANIFEST, town_file
from outages.streets.geo import Point, Scope
from outages.streets.normalize import street_key

NAME_KEYS = ("name", "alt_name", "old_name", "official_name", "short_name")


@dataclass(frozen=True)
class Way:
    id: int
    names: dict[str, str]  # name tag -> value
    coords: list[Point]
    town_psgc: str


class StreetIndex:
    def __init__(self, osm_base: str | None, ways: list[Way], version: str | None = None):
        self.osm_base = osm_base  # how old the OSM data is (its oldest town)
        # Changes on every town write, so a partial refetch (`--towns`) is noticed by run_match.
        self.version = version or osm_base
        self.ways = ways
        self._by_key: dict[tuple[str, str], list[Way]] = {}
        for w in ways:
            for key in {street_key(v) for v in w.names.values()}:
                if key:
                    self._by_key.setdefault((w.town_psgc, key), []).append(w)

    @classmethod
    def load(cls, osm_dir: Path) -> StreetIndex | None:
        """None when there is no manifest (the extract was never fetched)."""
        man_path = osm_dir / MANIFEST
        if not man_path.is_file():
            return None
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        ways = []
        for psgc in sorted(manifest.get("towns", {})):
            f = town_file(osm_dir, psgc)
            if not f.is_file():
                continue
            doc = json.loads(f.read_text(encoding="utf-8"))
            for w in doc["ways"]:
                names = {k: v for k, v in w["tags"].items() if k in NAME_KEYS and v}
                coords = [(float(x), float(y)) for x, y in w["geometry"]]
                ways.append(Way(int(w["id"]), names, coords, psgc))
        fetched = [t.get("fetched_at") for t in manifest.get("towns", {}).values()]
        version = max((f for f in fetched if f), default=None)
        return cls(manifest.get("osm_base"), ways, version)

    def ways_named(self, town_psgc: str, key: str) -> list[Way]:
        return list(self._by_key.get((town_psgc, key), []))

    def names_in(self, town_psgcs: list[str], scope: Scope) -> list[str]:
        """Name tag values of the towns' ways with ≥ 2 nodes inside the scope, most ways
        first, then by name."""
        out: Counter[str] = Counter()
        towns = set(town_psgcs)
        w_, s_, e_, n_ = scope.bbox
        for w in self.ways:
            if w.town_psgc not in towns:
                continue
            inside = 0
            for p in w.coords:
                if w_ <= p[0] <= e_ and s_ <= p[1] <= n_ and scope.contains(p):
                    inside += 1
                    if inside >= 2:
                        out.update(set(w.names.values()))
                        break
        return sorted(out, key=lambda n: (-out[n], n))
