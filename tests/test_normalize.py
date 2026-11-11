"""Text normalization (spec 5.2)."""

from __future__ import annotations

import pytest

from outages.normalize import normalize_line


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Barangays: \xa0Camputhaw", "Barangays: Camputhaw"),
        ("Portion of Cebu City (Kinasang‑an Pardo)", "Portion of Cebu City (Kinasang-an Pardo)"),
        ("Cebu – Mandaue — Talisay", "Cebu - Mandaue - Talisay"),
        ("Time :", "Time:"),
        ("Purpose :", "Purpose:"),
        ("Barangays : Tunghaan.", "Barangays: Tunghaan."),
        ("January 18, 2025 ( Saturday)", "January 18, 2025 (Saturday)"),
        ("June 29,  2024  (Saturday)", "June 29, 2024 (Saturday)"),
        ("9:00 AM to 5:00 PM (8hrs)- **CANCELLED**", "9:00 AM to 5:00 PM (8hrs)- CANCELLED"),
        ("  padded\t line \n", "padded line"),
        # Unicode mathematical bold (sep-27-oct-3-2026) folds to ASCII under NFKC.
        ("(8hrs)   \U0001d402\U0001d400\U0001d40d", "(8hrs) CAN"),
    ],
)
def test_normalize_line(raw, expected):
    assert normalize_line(raw) == expected


def test_keeps_non_ascii_letters():
    assert normalize_line("Sitios Sto. Niño") == "Sitios Sto. Niño"
