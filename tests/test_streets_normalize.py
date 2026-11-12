"""street_key: one normalisation for VECO and OSM street names (spec 4.3)."""

from __future__ import annotations

import pytest

from outages.streets.normalize import GENERIC_STREET_KEYS, street_key


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("A. Seno Extension", "A. Seno Ext."),
        ("Gov. M. Cuenco Ave.", "Governor M. Cuenco Avenue"),
        ("Cebu South Road.", "Cebu South Road"),
        ("N. Bacalso Ave", "N. Bacalso Avenue"),
        ("Gen. Maxilom Ave.", "General Maxilom Avenue"),
        ("Sta. Cruz St", "Santa Cruz Street"),
        ("Natalio B. Bacalso Hwy", "Natalio B. Bacalso Highway"),
        ("M. J. Cuenco Rd.", "M J Cuenco Road"),
        ("Bgy. Road", "Barangay Road"),
    ],
)
def test_same_key(a, b):
    assert street_key(a) == street_key(b)


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("portion of Cebu South Coastal Road.", "cebu south coastal road"),
        ("Portions of Cebu South Road", "cebu south road"),
        ("part of Gorordo Avenue", "gorordo avenue"),
        ("along Banilad Road", "banilad road"),
        ("Tiltilon Baranggay Road", "tiltilon barangay road"),
        ("Sto. Niño St.", "santo nino street"),
        ("Brgy. Lanas Road.", "barangay lanas road"),
        ("M.L. Quezon St.", "m l quezon street"),
        ("  D'Jakarta   St  ", "d jakarta street"),
        ("Hernan Cortes-Ext.", "hernan cortes extension"),
        ("Str. A", "street a"),
        ("Av. Rizal", "avenue rizal"),
    ],
)
def test_key_table(raw, key):
    assert street_key(raw) == key


def test_word_map_only_whole_words():
    assert street_key("Stella Road") == "stella road"
    assert street_key("Avenida") == "avenida"


def test_generic_keys():
    assert "barangay road" in GENERIC_STREET_KEYS
    for k in ("road", "main road", "national highway", "highway", "diversion road"):
        assert k in GENERIC_STREET_KEYS
    assert street_key("Brgy. Road") in GENERIC_STREET_KEYS
    assert "cebu south road" not in GENERIC_STREET_KEYS
