"""Barangay resolution against the PSGC gazetteer with aliases (spec 4.1, 4.3)."""

from __future__ import annotations

import csv
from importlib import resources

import pytest

from outages.areas.gazetteer import BarangayMatch, Gazetteer, load, normalize_name
from outages.areas.segment import Segmented

CC, CO, LI, TC = "730600000", "702219000", "702227000", "702250000"
BRGYS = [
    {"psgc_code": "730600042", "name": "Lorega", "town": "Cebu City", "town_psgc": CC},
    {"psgc_code": "730600027", "name": "Duljo", "town": "Cebu City", "town_psgc": CC},
    {"psgc_code": "730600015", "name": "Bulacao", "town": "Cebu City", "town_psgc": CC},
    {"psgc_code": "730600099", "name": "Santo Niño", "town": "Cebu City", "town_psgc": CC},
    {"psgc_code": "730600060", "name": "Pung-ol-Sibugay", "town": "Cebu City", "town_psgc": CC},
    {"psgc_code": "702219018", "name": "Tayud", "town": "Consolacion", "town_psgc": CO},
    {
        "psgc_code": "702219014",
        "name": "Poblacion Oriental",
        "town": "Consolacion",
        "town_psgc": CO,
    },
    {"psgc_code": "702227013", "name": "Tayud", "town": "Liloan", "town_psgc": LI},
    {"psgc_code": "702227014", "name": "Yati", "town": "Liloan", "town_psgc": LI},
    {"psgc_code": "702250001", "name": "Bulacao", "town": "Talisay City", "town_psgc": TC},
    {"psgc_code": "702250007", "name": "Lawaan I", "town": "Talisay City", "town_psgc": TC},
]
ALIASES = [
    {"town": "Cebu City", "alias": "Duljo Fatima", "target": "730600027", "note": "old name"},
    {"town": "*", "alias": "Poblacion Orriental", "target": "702219014", "note": "typo"},
    {"town": "*", "alias": "South Reclamation Area", "target": "not_barangay", "note": "SRP"},
    {"town": "Cebu City", "alias": "Greenhills", "target": "not_barangay", "note": "area"},
]


@pytest.fixture
def gaz() -> Gazetteer:
    return Gazetteer.from_rows(BRGYS, ALIASES)


def _seg(towns: list[str], tokens: list[str]) -> Segmented:
    return Segmented("city_first", towns, tokens, [], [])


def test_exact_within_town(gaz):
    m, other = gaz.resolve(_seg(["Cebu City"], ["Bulacao"]))
    assert m == [BarangayMatch("Bulacao", CC, "730600015", "exact")]
    assert other == []


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Lorega San Miguel", "lorega-san miguel"),
        ("Sto. Niño", "Santo Niño"),
        ("Sta. Cruz", "santa cruz"),
        ("Pob. Oriental", "Poblacion Oriental"),
        ("  Pung-ol  Sibugay. ", "Pung-ol-Sibugay"),
        ("ＴＡＹＵＤ", "Tayud"),
    ],
)
def test_normalization(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_normalized_match_keeps_name_raw(gaz):
    m, _ = gaz.resolve(_seg(["Cebu City"], ["Sto. Niño", "Pung-ol Sibugay"]))
    assert [(x.name_raw, x.psgc_code, x.match) for x in m] == [
        ("Sto. Niño", "730600099", "exact"),
        ("Pung-ol Sibugay", "730600060", "exact"),
    ]


def test_alias_to_code(gaz):
    m, _ = gaz.resolve(_seg(["Cebu City"], ["Duljo Fatima"]))
    assert m == [BarangayMatch("Duljo Fatima", CC, "730600027", "alias")]


def test_alias_is_town_specific(gaz):
    m, _ = gaz.resolve(_seg(["Liloan"], ["Duljo Fatima"]))
    assert m[0].match != "alias"


def test_alias_star_town(gaz):
    m, _ = gaz.resolve(_seg(["Consolacion"], ["Poblacion Orriental"]))
    assert m == [BarangayMatch("Poblacion Orriental", CO, "702219014", "alias")]


def test_not_barangay_goes_to_entities(gaz):
    m, other = gaz.resolve(_seg(["Cebu City"], ["South Reclamation Area", "Greenhills"]))
    assert m == []
    assert other == ["South Reclamation Area", "Greenhills"]


def test_ambiguous_two_listed_towns(gaz):
    m, _ = gaz.resolve(_seg(["Consolacion", "Liloan"], ["Tayud"]))
    assert m == [
        BarangayMatch("Tayud", CO, "702219018", "ambiguous"),
        BarangayMatch("Tayud", LI, "702227013", "ambiguous"),
    ]


def test_same_name_one_listed_town_is_exact(gaz):
    m, _ = gaz.resolve(_seg(["Talisay City"], ["Bulacao"]))
    assert m == [BarangayMatch("Bulacao", TC, "702250001", "exact")]


def test_other_town(gaz):
    m, _ = gaz.resolve(_seg(["Cebu City"], ["Yati"]))
    assert m == [BarangayMatch("Yati", LI, "702227014", "other_town")]


def test_unmatched_kept(gaz):
    m, _ = gaz.resolve(_seg(["Cebu City"], ["Tagba-o"]))
    assert m == [BarangayMatch("Tagba-o", CC, None, "unmatched")]
    m, _ = gaz.resolve(_seg(["Cebu City", "Liloan"], ["Nowhere"]))
    assert m == [BarangayMatch("Nowhere", None, None, "unmatched")]


def test_roman_numeral_not_folded(gaz):
    m, _ = gaz.resolve(_seg(["Talisay City"], ["Lawaan 1", "Lawaan I"]))
    assert [x.match for x in m] == ["unmatched", "exact"]


def test_suggest(gaz):
    assert gaz.suggest("Lawan I")[0] == "Lawaan I (Talisay City)"
    assert gaz.suggest("zzzz") == []


def _packaged(name: str) -> list[dict]:
    text = (resources.files("outages") / "areas" / name).read_text(encoding="utf-8")
    return list(csv.DictReader(text.splitlines()))


def test_aliases_csv_targets_exist():
    codes = {r["psgc_code"] for r in _packaged("barangays.csv")}
    towns = {r["town"] for r in _packaged("barangays.csv")} | {"*"}
    rows = _packaged("aliases.csv")
    assert rows
    for r in rows:
        assert None not in r, f"extra columns (unquoted comma?): {r}"
        assert r["target"] == "not_barangay" or r["target"] in codes, r
        assert r["town"] in towns, r
        assert r["note"].strip(), r


def test_load_packaged():
    g = load()
    m, _ = g.resolve(_seg(["Cebu City"], ["Lahug"]))
    assert m[0].match == "exact" and m[0].psgc_code == "730600041"


# Phase 3 (spec 5.8, owner ruling 2026-11-11): Lipata and Sto. Niño are other names for
# PSA barangays already in the release, so they resolve through town-scoped aliases.
def _one(text: str) -> BarangayMatch:
    from outages.areas.segment import segment

    matches, _ = load().resolve(segment(text))
    assert len(matches) == 1, matches
    return matches[0]


def test_lipata_resolves_to_linao():
    m = _one("Portion of Minglanilla (Lipata)")
    assert (m.psgc_code, m.match) == ("702232007", "alias")


@pytest.mark.parametrize("name", ["Sto. Niño", "Santo Niño", "STO. NIÑO"])
def test_sto_nino_resolves_to_730600022(name):
    m = _one(f"Portion of Cebu City ({name})")
    assert (m.psgc_code, m.match) == ("730600022", "alias")


def test_place():
    from outages.areas.gazetteer import Place

    assert load().place("730600027") == Place("730600027", "Duljo", "Cebu City", "730600000")
    assert load().place("nope") is None


def test_search_two_towns():
    gaz = load()
    roque = gaz.search("San Roque")
    assert len({p.town for p in roque}) >= 2
    assert [p.town for p in gaz.search("tayud")] == ["Consolacion", "Liloan"]


def test_search_alias():
    from outages.areas.gazetteer import Place

    assert load().search("Duljo Fatima") == [
        Place("730600027", "Duljo", "Cebu City", "730600000")
    ]
    assert [p.code for p in load().search("sto nino")] == ["730600022"]


def test_search_exact_and_alias_deduped():
    assert [p.code for p in load().search("Lipata")] == ["702232007"]
    assert [p.code for p in load().search("  duljo ")] == ["730600027"]


def test_search_unknown_empty():
    assert load().search("Atlantis") == []
    assert load().search("") == []


def test_search_skips_not_barangay_alias():
    # Cebu City's `Greenhills -> not_barangay` alias adds nothing; San Fernando's barangay stays.
    assert [p.town for p in load().search("Greenhills")] == ["San Fernando"]
