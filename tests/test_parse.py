"""Rule-based post parser (spec 5.4) with golden files written from reading the posts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES, read_post

from outages.extract import ExtractedPost, Line, extract_post
from outages.parse import areas_key, parse_post

EXPECTED = FIXTURES / "expected"
GOLDEN = sorted(EXPECTED.glob("*.json"))


def _slug(p: Path) -> str:
    return p.name.removesuffix(".json").removesuffix(".partial")


def _parse(slug: str):
    return parse_post(extract_post(read_post(slug)))


def _post(*lines: str | Line) -> ExtractedPost:
    ls = tuple(ln if isinstance(ln, Line) else Line(ln, False, ()) for ln in lines)
    return ExtractedPost("T", ls, "", "h")


BLOCK = ("Time:", "9:00 AM to 12:00 PM (3hrs)", "Purpose:", "X", "Areas Affected:", "Lahug")


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_golden(path):
    expected = json.loads(path.read_text(encoding="utf-8"))
    result = _parse(_slug(path))
    assert result.error is None
    got = [o.to_json() for o in result.outages]
    if path.name.endswith(".partial.json"):
        got = got[: len(expected)]
    assert got == expected


def test_every_fixture_has_a_golden_file():
    slugs = {_slug(p) for p in GOLDEN}
    assert slugs == {p.stem for p in (FIXTURES / "posts").glob("*.html")}


def test_nov_2026_counts():
    r = _parse("service-interruption-november-8-14-2026")
    assert len(r.outages) == 34
    assert sum(o.status == "cancelled" for o in r.outages) == 3
    assert [o.seq for o in r.outages] == list(range(34))


def test_additional_status():
    r = _parse("service-interruption-november-30-december-6-2025")
    assert sum(o.status == "additional" for o in r.outages) == 3


def test_map_image_per_outage():
    r = _parse("service-interruption-september-27-october-3-2026")
    assert len(r.outages) == 30
    assert all(o.map_image_url.startswith("https://static.wixstatic.com/media/") for o in r.outages)


def test_structured_areas_kept_as_lines():
    o = _parse("interruption-june-22-28-2025").outages[0]
    assert o.areas_raw == (
        "Portion of Cebu City\n"
        "Barangays: Camputhaw\n"
        "Streets: Queens Road, M. Gotianuy Street, Mango Avenue\n"
        "Establishments / Institutions: C&E Publishing Inc., Tango Plaza Building, "
        "Mister Donut Kitchen."
    )


def test_wrapped_areas_joined():
    r = _parse("service-interruption-september-21-27-2025")
    wrapped = [o for o in r.outages if "Lataban" in o.areas_raw]
    assert len(wrapped) == 1
    assert wrapped[0].areas_raw.endswith("along diversion road\ngoing to Lataban road.")


def test_label_and_value_on_one_line():
    r = parse_post(
        _post(
            "November 9, 2026 (Monday)",
            "Time: 9:00 AM to 12:00 PM (3hrs)",
            "Purpose: X",
            "Areas Affected: Portion of Lahug, Cebu City",
        )
    )
    assert r.error is None
    assert len(r.outages) == 1
    assert r.outages[0].areas_raw == "Portion of Lahug, Cebu City"
    assert r.outages[0].purpose == "X"


def test_weekday_mismatch_warns():
    r = parse_post(_post("November 9, 2026 (Sunday)", *BLOCK))
    assert any("weekday" in w for w in r.outages[0].warnings)


def test_struck_block_warns_not_cancels():
    r = parse_post(
        _post(
            "November 9, 2026 (Monday)",
            "Time:",
            Line("9:00 AM to 12:00 PM (3hrs)", True, ()),
            *BLOCK[2:],
        )
    )
    o = r.outages[0]
    assert o.status == "scheduled"
    assert any("strikethrough" in w for w in o.warnings)


def test_missing_purpose_warns():
    r = parse_post(_post("November 9, 2026 (Monday)", *BLOCK[:2], *BLOCK[4:]))
    assert r.error is None
    assert any("purpose" in w for w in r.outages[0].warnings)


def test_trailing_lines_end_parsing():
    r = parse_post(_post("November 9, 2026 (Monday)", *BLOCK, "Related posts", "See all"))
    assert r.error is None
    assert len(r.outages) == 1


def test_footer_cell_not_absorbed_into_areas():
    r = parse_post(
        _post(
            "November 9, 2026 (Monday)",
            *(Line(s, False, (), c) for s, c in zip(BLOCK, (0, 1, 2, 3, 4, 5), strict=True)),
            Line("For inquiries call 230-8326", False, (), 6),
        )
    )
    assert r.outages[0].areas_raw == "Lahug"


@pytest.mark.parametrize(
    ("lines", "msg"),
    [
        (("Time:", "9:00 AM to 12:00 PM (3hrs)"), "heading"),
        (("November 9, 2026 (Monday)", "Time:", "9 AM - 12 PM", *BLOCK[2:]), "time"),
        (("November 9, 2026 (Monday)", *BLOCK[:4]), "areas"),
        (("November 9, 2026 (Monday)", "Purpose:", "X", "Areas Affected:", "Y"), "time"),
        (("November 9, 2026 (Monday)",), "no outages"),
        (("November 9, 2026 (Monday)", *BLOCK, "Nov. 10, 2026 (Tuesday)", *BLOCK), "heading"),
    ],
    ids=["no-heading", "bad-time", "no-areas", "no-time", "zero", "unknown-heading"],
)
def test_hard_failures(lines, msg):
    r = parse_post(_post(*lines))
    assert r.outages == ()
    assert r.error is not None
    assert msg in r.error.lower()


def test_areas_key():
    assert (
        areas_key("Portion of Banilad & Talamban, Cebu City, along Banilad Road.")
        == "portion of banilad talamban cebu city along banilad road"
    )


def test_areas_key_ignores_line_breaks():
    assert areas_key("Portion of Cebu City\nBarangays: Camputhaw") == areas_key(
        "Portion of Cebu City Barangays: Camputhaw"
    )
