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
    return parse_post(extract_post(read_post(slug)), slug=slug)


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


@pytest.mark.parametrize(
    "line",
    [
        "November 20, 024 (Wednesday)",  # real, 2024-11-16 post: was skipped as a stray line
        "November 20 (Wednesday)",
    ],
)
def test_heading_like_line_with_bad_year_fails(line):
    """Skipping it would file its outages under the previous date."""
    r = parse_post(_post("November 19, 2024 (Tuesday)", *BLOCK, line, *BLOCK))
    assert r.outages == ()
    assert "heading" in r.error


@pytest.mark.parametrize(
    "line",
    ["May 2026 schedule", "Mandaue City (portion)"],
)
def test_month_words_that_are_not_headings_only_warn(line):
    r = parse_post(_post("November 19, 2024 (Tuesday)", *BLOCK, line, *BLOCK))
    assert r.error is None
    assert len(r.outages) == 2


# --- variants seen in the 2026-11-11 backfill (lines from real posts) ------------------


def test_two_ranges_on_one_line_become_two_outages():
    r = parse_post(
        _post(
            "August 4, 2024 (Sunday)",
            "Time:",
            "9:00 AM to 10:00 AM (1hr); 4:00 PM to 5:00 PM (1hr)",
            *BLOCK[2:],
            *BLOCK,
        )
    )
    assert r.error is None
    a, b, c = r.outages
    assert [(o.start_at.hour, o.end_at.hour) for o in (a, b)] == [(9, 10), (16, 17)]
    assert (a.purpose, a.areas_raw) == (b.purpose, b.areas_raw) == ("X", "Lahug")
    assert [o.seq for o in r.outages] == [0, 1, 2]
    assert all(any("2 time ranges" in w for w in o.warnings) for o in (a, b))
    assert not any("time ranges" in w for w in c.warnings)


def test_two_ranges_without_space_after_semicolon():
    r = parse_post(
        _post(
            "September 28, 2024 (Saturday)",
            "Time:",
            "8:00 AM to 9:00 AM (1hr);4:00 PM to 5:00 PM (1hr)",
            *BLOCK[2:],
        )
    )
    assert len(r.outages) == 2


def test_cancelled_heading_cancels_its_outages():
    r = parse_post(
        _post(
            "December 17, 2024 (Tuesday) - CANCELLED",
            *BLOCK,
            *BLOCK,
            "December 18, 2024 (Wednesday)",
            *BLOCK,
        )
    )
    assert [o.status for o in r.outages] == ["cancelled", "cancelled", "scheduled"]
    assert any("heading" in w for w in r.outages[0].warnings)


def test_ime_label_typo_read_as_time():
    r = parse_post(
        _post(
            "October 30, 2026 (Friday)",
            "ime:",
            "8:00 AM to 5:00 PM (9hrs) REVISED",
            *BLOCK[2:],
        )
    )
    assert r.error is None
    assert r.outages[0].start_at.hour == 8
    assert any("ime:" in w for w in r.warnings)


def test_time_without_label_after_heading():
    r = parse_post(
        _post(
            "August 11, 2026 (Tuesday)",
            "9:00 AM to 5:00 PM (8hrs)",
            *BLOCK[2:],
            *BLOCK,
        )
    )
    assert r.error is None
    assert len(r.outages) == 2
    assert r.outages[0].end_at.hour == 17
    assert any("without" in w for w in r.warnings)


def test_wrong_month_corrected_from_slug_week():
    r = parse_post(
        _post("November 7, 2024 (Saturday)", *BLOCK),
        slug="service-interruption-december-7-11-2024",
    )
    o = r.outages[0]
    assert o.start_at.date().isoformat() == "2024-12-07"
    assert o.heading_text == "November 7, 2024 (Saturday)"
    assert any("corrected" in w for w in o.warnings)
    assert not any("weekday" in w for w in o.warnings)


def test_heading_outside_week_unresolvable_fails():
    r = parse_post(
        _post("November 7, 2024 (Friday)", *BLOCK),
        slug="service-interruption-december-7-11-2024",
    )
    assert r.error is not None
    assert "outside" in r.error


def test_am_pm_slip_fails_the_post():
    r = parse_post(_post("May 13, 2024 (Monday)", "Time:", "9:00 PM to 5:00 PM (8hrs)", *BLOCK[2:]))
    assert r.outages == ()
    assert "AM/PM" in r.error


def test_areas_key():
    assert (
        areas_key("Portion of Banilad & Talamban, Cebu City, along Banilad Road.")
        == "portion of banilad talamban cebu city along banilad road"
    )


def test_areas_key_ignores_line_breaks():
    assert areas_key("Portion of Cebu City\nBarangays: Camputhaw") == areas_key(
        "Portion of Cebu City Barangays: Camputhaw"
    )
