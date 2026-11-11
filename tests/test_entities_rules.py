"""Typed entities: naming conventions and the rules extractor (spec 4.4, evals/areas/GUIDE.md)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from outages.areas.entities import KINDS, Entity, entity_key, normalize_entity
from outages.areas.entities_rules import RulesExtractor
from outages.areas.segment import segment

DEV = Path(__file__).parent.parent / "evals" / "areas" / "dev.jsonl"


def _rules(text: str) -> list[tuple[str, str]]:
    return [(e.kind, e.name) for e in RulesExtractor().extract(segment(text))]


def test_kinds():
    assert KINDS == ("street", "sitio_purok", "subdivision", "establishment", "other")


def test_normalize_drops_cue_words():
    assert normalize_entity(Entity("street", "along portions of Pitogo St.")) == [
        Entity("street", "Pitogo St.")
    ]
    assert normalize_entity(Entity("other", "including road going to La Cittadella")) == [
        Entity("other", "La Cittadella")
    ]


def test_normalize_plural_prefix():
    assert normalize_entity(Entity("other", "Puroks Mangga")) == [Entity("sitio_purok", "Mangga")]
    assert normalize_entity(Entity("sitio_purok", "Sitio Kawayan")) == [
        Entity("sitio_purok", "Kawayan")
    ]


def test_normalize_keeps_numbered_prefix():
    assert normalize_entity(Entity("other", "Purok 3")) == [Entity("sitio_purok", "Purok 3")]
    assert normalize_entity(Entity("sitio_purok", "Purok 2 Dela Paz")) == [
        Entity("sitio_purok", "Purok 2 Dela Paz")
    ]


def test_normalize_expands_zone_list():
    assert normalize_entity(Entity("sitio_purok", "Sitios Zone 3,4,5")) == [
        Entity("sitio_purok", "Zone 3"),
        Entity("sitio_purok", "Zone 4"),
        Entity("sitio_purok", "Zone 5"),
    ]
    assert normalize_entity(Entity("subdivision", "Villa Lorena 1 & 2")) == [
        Entity("subdivision", "Villa Lorena 1"),
        Entity("subdivision", "Villa Lorena 2"),
    ]


def test_normalize_shared_head_and_intersections():
    assert normalize_entity(Entity("sitio_purok", "Lower & Central Calajoan")) == [
        Entity("sitio_purok", "Lower Calajoan"),
        Entity("sitio_purok", "Central Calajoan"),
    ]
    assert normalize_entity(Entity("street", "SRP corner F. Vestil Street")) == [
        Entity("street", "SRP"),
        Entity("street", "F. Vestil Street"),
    ]
    assert normalize_entity(Entity("street", "Cor. J. Luna")) == [Entity("street", "J. Luna")]


def test_normalize_keeps_properties():
    e = Entity("other", "Cebu South Road Properties")
    assert normalize_entity(e) == [e]


def test_entity_key():
    assert entity_key(Entity("street", "  Pitogo   ST. ")) == ("street", "pitogo st")


def test_structured_label_sets_kind():
    got = _rules(
        "Portion of Liloan\nBarangays: Catarman\nStreets: along Pitogo St., Fatima\n"
        "Sitios: Bonbon, Okra\nSubdivisions/Villages: Graceville\n"
        "Establishments: Advance Graphics & Concepts, Wilcon Depot, Inc."
    )
    assert got == [
        ("street", "Pitogo St."),
        ("street", "Fatima"),
        ("sitio_purok", "Bonbon"),
        ("sitio_purok", "Okra"),
        ("subdivision", "Graceville"),
        ("establishment", "Advance Graphics & Concepts"),
        ("establishment", "Wilcon Depot, Inc."),
    ]


def test_mixed_label_classifies_each():
    got = _rules(
        "Portion of Cebu City\nBarangays: Calamba\nSitios / Establishments: "
        "Sitio Urase, ABC Prime Inc."
    )
    assert got == [("sitio_purok", "Urase"), ("establishment", "ABC Prime Inc.")]


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("Fidel Bas St.", "street"),
        ("Cebu North Coastal Rd.", "street"),
        ("Gorordo Ave. Ext.", "street"),
        ("Central Nautical Highway", "street"),
        ("Balaga Drive", "street"),
        ("Holy Family Subd.", "subdivision"),
        ("Shangrila Village", "subdivision"),
        ("Bliss Homes", "subdivision"),
        ("Perez Compound", "subdivision"),
        ("Woodland Park Residences", "subdivision"),
        ("Palmax Solution Inc.", "establishment"),
        ("Mervir Realty Corp.", "establishment"),
        ("Sta. Cruz Chapel", "establishment"),
        ("Labangon Day Care Center", "establishment"),
        ("Upper Tingub", "sitio_purok"),
        ("El Pardo", "other"),
    ],
)
def test_suffix_kinds(name, kind):
    assert _rules(f"Portion of Cebu City (Lahug) along {name}") == [(kind, name)]


def test_sticky_prefix_until_suffix():
    got = _rules(
        "Portion of Talisay City (San Roque) including Puroks Mangga, Isla Verde, Sitios Zone "
        "3,4,5, Sombria, St. Ignatius De loyola Townhomes, Ramona Village, Dolores"
    )
    assert got == [
        ("sitio_purok", "Mangga"),
        ("sitio_purok", "Isla Verde"),
        ("sitio_purok", "Zone 3"),
        ("sitio_purok", "Zone 4"),
        ("sitio_purok", "Zone 5"),
        ("sitio_purok", "Sombria"),
        ("subdivision", "St. Ignatius De loyola Townhomes"),
        ("subdivision", "Ramona Village"),
        ("other", "Dolores"),
    ]


def test_free_text_and_splits_but_company_names_stay_whole():
    got = _rules(
        "Portion of Cebu City (Day-as) along D. Jakosalem St., & Sikatuna St., including "
        "Sitios Magay & Lubi, Alfonso & Sons Development Corporation, Anseca Corp. and "
        "Dean Angelo Industrial Co. Inc."
    )
    assert got == [
        ("street", "D. Jakosalem St."),
        ("street", "Sikatuna St."),
        ("sitio_purok", "Magay"),
        ("sitio_purok", "Lubi"),
        ("establishment", "Alfonso & Sons Development Corporation"),
        ("establishment", "Anseca Corp."),
        ("establishment", "Dean Angelo Industrial Co. Inc."),
    ]


def test_cue_word_without_comma_splits():
    got = _rules(
        "Portion of Minglanilla (Calajo-an) along MH Selena St. including portions of Sitios "
        "Sangi & Relis, Lower & Central Calajoan"
    )
    assert got == [
        ("street", "MH Selena St."),
        ("sitio_purok", "Sangi"),
        ("sitio_purok", "Relis"),
        ("sitio_purok", "Lower Calajoan"),
        ("sitio_purok", "Central Calajoan"),
    ]


def test_villa_prefix_is_subdivision():
    assert _rules("Portion of Liloan (Yati) including Villa Lorena 1 & 2, Villa Lorena Dos") == [
        ("subdivision", "Villa Lorena 1"),
        ("subdivision", "Villa Lorena 2"),
        ("subdivision", "Villa Lorena Dos"),
    ]


def test_suffix_after_hyphen():
    assert _rules("Portion of Cebu City (Pari-an) including Sikatuna Youth Club-Chapel") == [
        ("establishment", "Sikatuna Youth Club-Chapel")
    ]


def test_bare_portion_is_a_cue():
    assert _rules("Portion of Cebu City (Tisa) including portion along SRP Bypass Road.") == [
        ("street", "SRP Bypass Road.")
    ]


def test_unknown_is_other():
    assert _rules("Portion of Cebu City (Lahug) including Kintolimbo") == [("other", "Kintolimbo")]


def test_no_segments_no_entities():
    assert _rules("Portion of Cebu City Barangays: Tejero") == []


def _dev_rows():
    from outages.evaluate import load_split

    return load_split(DEV)


@pytest.mark.xfail(strict=False, reason="tuning aid on dev, not a gate (plan Task 6)")
@pytest.mark.parametrize("row", _dev_rows(), ids=lambda r: r["id"])
def test_dev_examples(row):
    from outages.areas.gazetteer import load

    seg = segment(row["areas_raw"])
    _, not_brgy = load().resolve(seg)
    pred = [Entity("other", n) for n in not_brgy] + RulesExtractor().extract(seg)
    gold = [x for e in row["entities"] for x in normalize_entity(Entity(e["kind"], e["name"]))]
    p, g = Counter(map(entity_key, pred)), Counter(map(entity_key, gold))
    assert p == g, {"missing": sorted((g - p).elements()), "extra": sorted((p - g).elements())}
