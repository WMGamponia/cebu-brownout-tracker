"""Areas segmenter: layouts, towns, barangay tokens, labelled segments (spec 4.2)."""

from __future__ import annotations

import json
import re

import pytest
from conftest import FIXTURES

from outages.areas.segment import Segment, canonical_town, segment, sub_label_kind


def test_city_first():
    s = segment("Portion of Cebu City (Tisa) including portions along Buhisan Road.")
    assert s.layout == "city_first"
    assert s.towns == ["Cebu City"]
    assert s.barangay_tokens == ["Tisa"]
    assert s.segments == [Segment(None, "including portions along Buhisan Road.")]
    assert s.warnings == []


def test_city_first_two_towns():
    s = segment(
        "Portion of Consolacion & Liloan (Lamac & Yati) along Cebu North Road, Libo St., "
        "and Pagutlan Road."
    )
    assert s.layout == "city_first"
    assert s.towns == ["Consolacion", "Liloan"]
    assert s.barangay_tokens == ["Lamac", "Yati"]


def test_city_first_tokens_trimmed_and_split_on_and():
    s = segment("Portion of Cebu City (Day-as , Kamagayan , Pari-an and T. Padilla) along X St.")
    assert s.barangay_tokens == ["Day-as", "Kamagayan", "Pari-an", "T. Padilla"]


def test_city_first_a_portion_and_city_of_naga():
    s = segment("A portion of City of Naga (Cogon) including portions of Purok Katikan.")
    assert (s.layout, s.towns, s.barangay_tokens) == ("city_first", ["City of Naga"], ["Cogon"])


def test_barangay_first():
    s = segment("Portion of Jaclupan, Talisay City, along portions of Cebu-Toledo Wharf Rd.")
    assert s.layout == "barangay_first"
    assert s.towns == ["Talisay City"]
    assert s.barangay_tokens == ["Jaclupan"]
    assert s.segments == [Segment(None, "along portions of Cebu-Toledo Wharf Rd.")]


def test_barangay_first_no_comma_before_cue():
    s = segment("Portion of Cabancalan, Mandaue City along Russelia St.")
    assert (s.towns, s.barangay_tokens) == (["Mandaue City"], ["Cabancalan"])
    assert s.segments == [Segment(None, "along Russelia St.")]


def test_barangay_first_double_comma_and_portions_along():
    s = segment("Portion of San Roque & Sto. Niño, Cebu City portions along Vicente Gullas Street.")
    assert (s.layout, s.barangay_tokens) == ("barangay_first", ["San Roque", "Sto. Niño"])
    s = segment("Portion of Camputhaw,, Lahug & San Roque, Cebu City, along Gorordo Avenue.")
    assert s.barangay_tokens == ["Camputhaw", "Lahug", "San Roque"]


def test_barangay_first_text_after_town_is_segment():
    s = segment("Portion of Poblacion, Liloan, corner F. Ramas Ext and Central Nautical Hwy.")
    assert (s.towns, s.barangay_tokens) == (["Liloan"], ["Poblacion"])
    assert s.segments == [Segment(None, "corner F. Ramas Ext and Central Nautical Hwy.")]


def test_barangay_first_town_tokens_in_list():
    s = segment(
        "Portion of Mandaue City & Consolacion, Basak, Casili, Jagobiao, Poblacion Orriental, "
        "along Cebu North Road."
    )
    assert s.layout == "barangay_first"
    assert s.towns == ["Mandaue City", "Consolacion"]
    assert s.barangay_tokens == ["Basak", "Casili", "Jagobiao", "Poblacion Orriental"]


def test_structured_lines():
    s = segment(
        "Portion of Talisay City\nBarangay: Jaclupan\nStreet: along Cebu Toledo Wharf Rd.\n"
        "Puroks: Avocado, Banana\nSitios: Ipilipil, Kawayan\nEstablishments: MCWD-Jaclupan"
    )
    assert s.layout == "structured"
    assert s.towns == ["Talisay City"]
    assert s.barangay_tokens == ["Jaclupan"]
    assert s.segments == [
        Segment("Street", "along Cebu Toledo Wharf Rd."),
        Segment("Puroks", "Avocado, Banana"),
        Segment("Sitios", "Ipilipil, Kawayan"),
        Segment("Establishments", "MCWD-Jaclupan"),
    ]


def test_structured_two_towns_and_slash_labels():
    s = segment(
        "Portion of Consolacion & Liloan\nBarangays: Lamac, Yati\n"
        "Villages / Subdivisions / Compounds: Perez Compound\nStreets.: along Pitogo St."
    )
    assert s.towns == ["Consolacion", "Liloan"]
    assert s.segments == [
        Segment("Villages / Subdivisions / Compounds", "Perez Compound"),
        Segment("Streets.", "along Pitogo St."),
    ]


def test_structured_inline_labels():
    s = segment(
        "Portion of Liloan\nBarangays: Jubay, CotcotStreets: Jasmin Street, Mulberry Drive"
        "Sitios: Tulay"
    )
    assert s.barangay_tokens == ["Jubay", "Cotcot"]
    assert s.segments == [
        Segment("Streets", "Jasmin Street, Mulberry Drive"),
        Segment("Sitios", "Tulay"),
    ]


def test_inline_label_after_period():
    s = segment("Portion of Liloan\nBarangays: Catarman, Tayud.Street: Kapaz Road")
    assert s.barangay_tokens == ["Catarman", "Tayud"]
    assert s.segments == [Segment("Street", "Kapaz Road")]


def test_inline_label_after_capital():
    s = segment("Portion of Minglanilla\nBarangays: Vito, Ward IVStreets: Cebu South Rd.")
    assert s.barangay_tokens == ["Vito", "Ward IV"]
    assert s.segments == [Segment("Streets", "Cebu South Rd.")]


def test_town_glued_to_last_barangay():
    s = segment("Portion of Adlaon, Tagba-o & Taptap Cebu City, along Sister Cities Drive.")
    assert (s.layout, s.towns) == ("barangay_first", ["Cebu City"])
    assert s.barangay_tokens == ["Adlaon", "Tagba-o", "Taptap"]


def test_zero_width_spaces_removed():
    s = segment("Portion of ​Jubay​, Liloan​, along Fatima Rd.")
    assert (s.layout, s.towns, s.barangay_tokens) == ("barangay_first", ["Liloan"], ["Jubay"])


def test_town_typo_san_fernado():
    s = segment("Portion of Pitalo, San Fernado, along portions of Cebu South Rd.")
    assert (s.towns, s.barangay_tokens) == (["San Fernando"], ["Pitalo"])


def test_cue_inside_barangays_value():
    s = segment(
        "Portion of San Fernando Barangays: Bugho including portions of Purok 1\nSitios: Proper"
    )
    assert s.barangay_tokens == ["Bugho"]
    assert s.segments == [
        Segment(None, "including portions of Purok 1"),
        Segment("Sitios", "Proper"),
    ]


def test_structured_head_without_portion_of():
    s = segment("Mandaue City\nBarangays: Tawason\nStreets: Kinabalu")
    assert (s.towns, s.barangay_tokens, s.warnings) == (["Mandaue City"], ["Tawason"], [])
    assert s.segments == [Segment("Streets", "Kinabalu")]


def test_city_first_comma_after_paren_dropped():
    s = segment("Portion of City of Naga & Minglanilla (Colon & Tungkop), along Cebu South Road")
    assert s.segments == [Segment(None, "along Cebu South Road")]


def test_inline_label_on_first_line():
    s = segment("Portion of Cebu City Barangays: Tejero")
    assert (s.layout, s.towns, s.barangay_tokens, s.segments) == (
        "structured",
        ["Cebu City"],
        ["Tejero"],
        [],
    )


def test_wrapped_value_joined():
    s = segment(
        "Portion of Cebu City\nBarangays: Zapatera\nEstablishments / Institutions:\n"
        "Bluspur Development Corporation,\nIglesia Ni Cristo"
    )
    assert s.segments == [
        Segment(
            "Establishments / Institutions", "Bluspur Development Corporation, Iglesia Ni Cristo"
        )
    ]


def test_wrapped_line_in_free_text_joined():
    s = segment("Portion of Cebu City (Lahug) along Gorordo Avenue,\nincluding Sitio Kawayan.")
    assert s.segments == [Segment(None, "along Gorordo Avenue, including Sitio Kawayan.")]


def test_testing_is_other_with_warning():
    s = segment("testing")
    assert s.layout == "other"
    assert s.segments == [Segment(None, "testing")]
    assert "other layout" in s.warnings
    assert "no town found" in s.warnings


def test_no_town_warns():
    s = segment("Portion of Mandaue City DCA Brokerage Corp.")
    assert s.layout == "other"
    assert s.towns == ["Mandaue City"]
    assert "no barangay token" in s.warnings
    assert s.segments == [Segment(None, "Portion of Mandaue City DCA Brokerage Corp.")]


def test_unknown_sub_label_kept():
    s = segment("Portion of Cebu City\nBarangays: Lahug\nNotes Here: Gorordo side")
    assert s.segments == [Segment("Notes Here", "Gorordo side")]
    assert "unknown sub-label: Notes Here" in s.warnings


def test_label_word_inside_value_not_split():
    s = segment(
        "Portion of San Fernando\nBarangays: Tonggo\n"
        "Establishments: Barangay Tonggo Water Works System, Streetlight Office\n"
        "Sitios: Streetlight, Barangay Proper"
    )
    assert s.barangay_tokens == ["Tonggo"]
    assert s.segments == [
        Segment("Establishments", "Barangay Tonggo Water Works System, Streetlight Office"),
        Segment("Sitios", "Streetlight, Barangay Proper"),
    ]


def test_second_portion_clause():
    s = segment("Portion of Cebu City (Poblacion Pardo) Portion of Sunrise Village, Bulacao.")
    assert s.barangay_tokens == ["Poblacion Pardo"]
    assert s.segments == [Segment(None, "Portion of Sunrise Village, Bulacao.")]
    assert "second portion clause" in s.warnings


@pytest.mark.parametrize(
    ("text", "town"),
    [
        ("Cebu City", "Cebu City"),
        ("Mandaue", "Mandaue City"),
        ("mandaue city", "Mandaue City"),
        ("Talisay", "Talisay City"),
        ("Naga City", "City of Naga"),
        ("Naga", "City of Naga"),
        ("City of Talisay", "Talisay City"),
        ("San  Fernando", "San Fernando"),
        ("Lahug", None),
    ],
)
def test_canonical_town(text, town):
    assert canonical_town(text) == town


@pytest.mark.parametrize(
    ("label", "kind"),
    [
        ("Streets/Roads", "street"),
        ("Puroks / Sitios", "sitio_purok"),
        ("Villages / Subdivisions / Compounds", "subdivision"),
        ("Establishments / Institutions", "establishment"),
        ("Religious Sites", "establishment"),
        ("Sitios / Establishments", None),
        ("Notes Here", None),
    ],
)
def test_sub_label_kind(label, kind):
    assert sub_label_kind(label) == kind


_DROPPABLE = re.compile(
    r"^(a|portion|portions|of|city|barangay|barangays|streets?|roads?|sitios?|puroks?"
    r"|subdivisions?|villages?|compounds?|establishments?|institutions?)$"
)


def _words(s: str) -> list[str]:
    """Words, also split where VECO left out the line break (`CotcotStreets`)."""
    return re.findall(r"[^\W_]+", re.sub(r"(?<=[A-Za-z.])(?=[A-Z][a-z])", " ", s).casefold())


def test_no_text_dropped():
    """Every word of every fixture's areas_raw survives in towns, tokens or segments."""
    texts = []
    for p in sorted((FIXTURES / "expected").glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        texts += [o["areas_raw"] for o in data]
    assert len(texts) > 50
    for text in texts:
        s = segment(text)
        out = " ".join(
            [
                *s.towns,
                *s.barangay_tokens,
                *(x.text for x in s.segments),
                *(x.label or "" for x in s.segments),
            ]
        )
        have = set(_words(out))
        lost = [w for w in _words(text) if w not in have and not _DROPPABLE.match(w)]
        assert not lost, (text, lost)
