"""Resolve barangay tokens to PSGC codes: exact within the listed towns, then alias, then
other VECO town, else unmatched (spec 4.1, 4.3). Pure: the only I/O is `load()` reading the
packaged CSVs. No fuzzy auto-match; `suggest()` only feeds the review queue.
"""

from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from dataclasses import dataclass
from importlib import resources

from outages.areas.segment import Segmented
from outages.config import VECO_TOWNS

NOT_BARANGAY = "not_barangay"
_ABBREV = (
    (re.compile(r"\bsto\b\.?\s*"), "santo "),
    (re.compile(r"\bsta\b\.?\s*"), "santa "),
    (re.compile(r"\bpob\b\.?\s*"), "poblacion "),
)


def normalize_name(s: str) -> str:
    """Matching key: NFKC, casefold, `-` = space, collapsed, no trailing `.`, Sto./Sta./Pob.

    Roman numerals stay as written (`Lawaan I` != `Lawaan 1`)."""
    s = unicodedata.normalize("NFKC", s).casefold().replace("-", " ")
    for pat, repl in _ABBREV:
        s = pat.sub(repl, s)
    return " ".join(s.split()).rstrip(".").strip()


@dataclass(frozen=True)
class BarangayMatch:
    name_raw: str
    town_psgc: str | None
    psgc_code: str | None
    match: str  # exact | alias | ambiguous | other_town | unmatched


@dataclass(frozen=True)
class _Brgy:
    code: str
    name: str
    town: str
    town_psgc: str


class Gazetteer:
    def __init__(self, barangays: list[_Brgy], aliases: dict[tuple[str, str], str]):
        self._by_code = {b.code: b for b in barangays}
        self._by_name: dict[str, list[_Brgy]] = {}
        for b in barangays:
            self._by_name.setdefault(normalize_name(b.name), []).append(b)
        self._aliases = aliases  # (town or "*", normalized alias) -> code or NOT_BARANGAY

    @classmethod
    def from_rows(cls, barangays: list[dict], aliases: list[dict]) -> Gazetteer:
        brgys = [_Brgy(r["psgc_code"], r["name"], r["town"], r["town_psgc"]) for r in barangays]
        al = {(r["town"], normalize_name(r["alias"])): r["target"] for r in aliases}
        return cls(brgys, al)

    def resolve(self, seg: Segmented) -> tuple[list[BarangayMatch], list[str]]:
        """Matches for every barangay token, in order, and the `not_barangay` names."""
        listed = [t for t in seg.towns if t in VECO_TOWNS]
        matches: list[BarangayMatch] = []
        not_brgy: list[str] = []
        for raw in seg.barangay_tokens:
            key = normalize_name(raw)
            cands = self._by_name.get(key, [])
            inside = [b for b in cands if b.town in listed]
            if inside:
                kind = "exact" if len(inside) == 1 else "ambiguous"
                matches += [BarangayMatch(raw, b.town_psgc, b.code, kind) for b in inside]
                continue
            target = next(
                (self._aliases[(t, key)] for t in [*listed, "*"] if (t, key) in self._aliases),
                None,
            )
            if target == NOT_BARANGAY:
                not_brgy.append(raw)
                continue
            if target is not None:
                b = self._by_code[target]
                matches.append(BarangayMatch(raw, b.town_psgc, b.code, "alias"))
                continue
            if cands:
                matches += [BarangayMatch(raw, b.town_psgc, b.code, "other_town") for b in cands]
                continue
            town = VECO_TOWNS[listed[0]] if len(listed) == 1 else None
            matches.append(BarangayMatch(raw, town, None, "unmatched"))
        return matches, not_brgy

    def suggest(self, name: str, n: int = 3) -> list[str]:
        """Closest gazetteer names (`Name (Town)`), for the review queue only."""
        keys = difflib.get_close_matches(normalize_name(name), list(self._by_name), n, 0.6)
        return [f"{b.name} ({b.town})" for k in keys for b in self._by_name[k]][:n]


def _read(name: str) -> list[dict]:
    text = (resources.files("outages") / "areas" / name).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def load() -> Gazetteer:
    """The packaged `barangays.csv` and `aliases.csv`."""
    return Gazetteer.from_rows(_read("barangays.csv"), _read("aliases.csv"))
