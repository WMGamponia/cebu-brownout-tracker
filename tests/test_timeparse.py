"""Date headings and time ranges (spec 5.3)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from outages.timeparse import (
    MANILA,
    AmPmSlip,
    DateRange,
    HeadingOutsideWeek,
    fix_heading,
    heading_warnings,
    parse_heading,
    parse_time,
    slug_week,
)


def dt(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=MANILA)


NOV9 = DateRange(date(2026, 11, 9), date(2026, 11, 9), "Monday")
NOV11_12 = DateRange(date(2026, 11, 11), date(2026, 11, 12), "Wednesday-Thursday")


# --- headings ---------------------------------------------------------------


def test_heading_single_day():
    assert parse_heading("November 8, 2026 (Sunday)") == DateRange(
        date(2026, 11, 8), date(2026, 11, 8), "Sunday"
    )


def test_heading_multi_day_same_month():
    assert parse_heading("November 11-12, 2026 (Wednesday-Thursday)") == NOV11_12


def test_heading_two_full_dates():
    assert parse_heading("February 29, 2024 - March 01, 2024 (Thursday-Friday)") == DateRange(
        date(2024, 2, 29), date(2024, 3, 1), "Thursday-Friday"
    )


def test_heading_cross_month():
    assert parse_heading("November 30 - December 1, 2025 (Sunday-Monday)") == DateRange(
        date(2025, 11, 30), date(2025, 12, 1), "Sunday-Monday"
    )


def test_heading_cross_year():
    h = parse_heading("December 28, 2025 - January 3, 2026")
    assert h == DateRange(date(2025, 12, 28), date(2026, 1, 3), None)


def test_heading_cross_year_without_first_year():
    h = parse_heading("December 31 - January 1, 2026 (Wednesday-Thursday)")
    assert h == DateRange(date(2025, 12, 31), date(2026, 1, 1), "Wednesday-Thursday")


@pytest.mark.parametrize(
    "line",
    [
        "Time:",
        "Service Interruption: November 8-14, 2026",
        "9:00 AM to 5:00 PM (8hrs)",
        "Portion of Lahug, Cebu City",
        "February 30, 2026 (Monday)",
    ],
)
def test_not_a_heading(line):
    assert parse_heading(line) is None


# --- times ------------------------------------------------------------------


def test_simple_range():
    t = parse_time("9:00 AM to 12:00 PM (3hrs)", NOV9)
    assert (t.start, t.end) == (dt(2026, 11, 9, 9), dt(2026, 11, 9, 12))
    assert t.duration_label == "3hrs"
    assert t.status == "scheduled"
    assert t.warnings == ()


def test_overnight_without_dates():
    t = parse_time("9:00 PM to 7:00 AM (10hrs)", NOV9)
    assert (t.start, t.end) == (dt(2026, 11, 9, 21), dt(2026, 11, 10, 7))
    assert t.warnings == ()


def test_explicit_dates():
    t = parse_time("10:00 PM of November 11 to 5:00 AM of November 12 (7hrs)", NOV11_12)
    assert (t.start, t.end) == (dt(2026, 11, 11, 22), dt(2026, 11, 12, 5))
    assert t.warnings == ()


def test_explicit_dates_roll_year():
    h = DateRange(date(2025, 12, 31), date(2026, 1, 1), None)
    t = parse_time("10:00 PM of December 31 to 5:00 AM of January 1 (7hrs)", h)
    assert t.start == dt(2025, 12, 31, 22)
    assert t.end.isoformat() == "2026-01-01T05:00:00+08:00"


def test_midnight_start():
    t = parse_time("12:00 AM to 6:00 AM (6hrs)", NOV9)
    assert (t.start, t.end) == (dt(2026, 11, 9, 0), dt(2026, 11, 9, 6))


def test_noon_end():
    t = parse_time("9:00 AM to 12:00 PM", NOV9)
    assert t.end == dt(2026, 11, 9, 12)
    assert t.duration_label is None


def test_one_hour_label():
    t = parse_time("10:00 PM to 11:00 PM (1hr)", NOV9)
    assert t.duration_label == "1hr"
    assert t.warnings == ()


def test_half_hours_and_decimal_label():
    t = parse_time("1:00 PM to 3:30 PM (2.5hrs) ADDITIONAL", NOV9)
    assert (t.start, t.end) == (dt(2026, 11, 9, 13), dt(2026, 11, 9, 15, 30))
    assert t.duration_label == "2.5hrs"
    assert t.status == "additional"
    assert t.warnings == ()


def test_label_with_space():
    t = parse_time("10:00 PM of November 11 to 6:00 AM of November 12 (8 hrs)", NOV11_12)
    assert t.duration_label == "8 hrs"
    assert t.warnings == ()


@pytest.mark.parametrize(
    ("line", "status"),
    [
        ("9:00 AM to 5:00 PM (8hrs)- CANCELLED", "cancelled"),
        ("9:00 AM to 5:00 PM (8hrs)-CANCELLED", "cancelled"),
        ("9:00 AM to 5:00 PM (8hrs) - cancelled", "cancelled"),
        ("9:00 AM to 5:00 PM (8hrs) CANCELLED", "cancelled"),
        ("9:00 AM to 11:00 AM (2hrs)- ADDITIONAL", "additional"),
    ],
)
def test_status_suffix(line, status):
    assert parse_time(line, NOV9).status == status


def test_duration_mismatch_warns():
    t = parse_time("8:00 AM to 5:00 PM (8hrs)", NOV9)
    assert t is not None
    assert any("duration" in w for w in t.warnings)


def test_explicit_date_outside_heading_warns():
    t = parse_time("10:00 PM of November 14 to 5:00 AM of November 15 (7hrs)", NOV11_12)
    assert t.start == dt(2026, 11, 14, 22)
    assert any("outside heading" in w for w in t.warnings)


@pytest.mark.parametrize(
    "line",
    [
        "9 AM - 5 PM",
        "9:00 AM to 5:00 PM (8hrs) maybe",
        "Purpose:",
        "13:00 PM to 5:00 PM",
    ],
)
def test_unparseable_time(line):
    assert parse_time(line, NOV9) is None


# --- variants seen in the 2026-11-11 backfill (each line is from a real post) ---------


@pytest.mark.parametrize(
    ("line", "start", "end", "wd"),
    [
        ("April 18-19 2026 (Saturday-Sunday)", (2026, 4, 18), (2026, 4, 19), "Saturday-Sunday"),
        (
            "July 8- July 9 , 2026 (Wednesday- Thursday)",
            (2026, 7, 8),
            (2026, 7, 9),
            "Wednesday- Thursday",
        ),
        (
            "August 29, 2024 (Thursday)-August 30, 2024 (Friday)",
            (2024, 8, 29),
            (2024, 8, 30),
            "Thursday-Friday",
        ),
        (
            "June 16, 2026 (Tuesday) - June 17, 2026 (Wednesday)",
            (2026, 6, 16),
            (2026, 6, 17),
            "Tuesday-Wednesday",
        ),
        (
            "March 31, 2026 (Tuesday) -April 1, 2026 (Wednesday",
            (2026, 3, 31),
            (2026, 4, 1),
            "Tuesday-Wednesday",
        ),
    ],
)
def test_heading_variants(line, start, end, wd):
    h = parse_heading(line)
    assert h == DateRange(date(*start), date(*end), wd)
    assert heading_warnings(h) == ()


@pytest.mark.parametrize(
    "line",
    [
        "December 17, 2024 (Tuesday) - CANCELLED",
        "November 20-21, 2024 (Wednesday-Thursday)- CANCELLED",
        "November 27, 2024 (Wednesday)- CANCELLED",
    ],
)
def test_heading_cancelled(line):
    assert parse_heading(line).status == "cancelled"


def test_heading_status_defaults_to_none():
    assert parse_heading("November 9, 2026 (Monday)").status is None


@pytest.mark.parametrize(
    ("line", "start", "end", "label"),
    [
        ("9:00 AM to 5:00 PM (8hrs", 9, 17, "8hrs"),
        ("9:00 AM to 5:0 0 PM (8hrs)", 9, 17, "8hrs"),
        ("8:00 A.M. to 5:00 P.M. (9hrs)", 8, 17, "9hrs"),
        ("9:00 AM to 12:00 NN (3hrs)", 9, 12, "3hrs"),
        ("9:00 AM to 1:00 PM (4hrs))", 9, 13, "4hrs"),
        ("Time: 8:00 AM to 4:00 PM (8hrs)", 8, 16, "8hrs"),
    ],
)
def test_time_variants(line, start, end, label):
    t = parse_time(line, NOV9)
    assert (t.start, t.end) == (dt(2026, 11, 9, start), dt(2026, 11, 9, end))
    assert t.duration_label == label
    assert t.warnings == ()


def test_hours_and_minutes_label():
    t = parse_time("3:30 PM to 5:00 PM (1hr 30mins)", NOV9)
    assert t.duration_label == "1hr 30mins"
    assert t.warnings == ()


def test_noon_marker_only_for_twelve():
    assert parse_time("9:00 AM to 11:00 NN (2hrs)", NOV9) is None


@pytest.mark.parametrize(
    ("line", "status", "warn"),
    [
        (
            "10:00 PM of November 9 to 6:00 AM of November 10 (8hrs) (ADDITIONAL)",
            "additional",
            None,
        ),
        ("5:00 AM to 6:00 AM (1hr)- CANCELLEDD", "cancelled", None),
        ("9:00 AM to 5:00 PM (8hrs)- ADDITIONAL-CANCELLED", "cancelled", "additional"),
        ("8:00 AM to 5:00 PM (9hrs) REVISED", "scheduled", "revised"),
        (
            "10:00 PM of November 9 to 12:00 AM of November 10 (2hrs)-ADDITIONAL OUTAGE",
            "additional",
            None,
        ),
    ],
)
def test_status_variants(line, status, warn):
    t = parse_time(line, NOV9)
    assert t.status == status
    if warn is None:
        assert t.warnings == ()
    else:
        assert any(warn in w.lower() for w in t.warnings)


def test_overnight_end_the_day_after_heading_does_not_warn():
    t = parse_time("10:00 PM of November 12 to 6:00 AM of November 13 (8hrs)", NOV11_12)
    assert t.warnings == ()


@pytest.mark.parametrize(
    "line",
    ["9:00 PM to 5:00 PM (8hrs)", "8:00 AM to 9:00 PM (1hr)"],
)
def test_am_pm_slip_is_an_error(line):
    """Flipping either AM/PM shifts the span by the same 12 h, so the label can't say which
    one is wrong: the post fails rather than storing a guess (owner, 2026-11-11)."""
    with pytest.raises(AmPmSlip, match="AM/PM"):
        parse_time(line, NOV9)


def test_small_duration_mismatch_still_only_warns():
    t = parse_time("9:00 AM to 4:00 PM (9hrs)", NOV9)
    assert any("duration" in w for w in t.warnings)


# --- the post's own week, from its slug -------------------------------------


@pytest.mark.parametrize(
    ("slug", "start", "end"),
    [
        ("service-interruption-december-7-11-2024", (2024, 12, 7), (2024, 12, 11)),
        ("service-interruption-august-30-september-5-2026", (2026, 8, 30), (2026, 9, 5)),
        ("service-interruption-july-5-july-11-2026", (2026, 7, 5), (2026, 7, 11)),
        ("service-interruption-october-18-2026-october-24-2026", (2026, 10, 18), (2026, 10, 24)),
        ("service-interruption-oct-5-11-2025", (2025, 10, 5), (2025, 10, 11)),
        ("service-interruption-november-03-08-2026", (2026, 11, 3), (2026, 11, 8)),
        ("interruption-june-22-28-2025", (2025, 6, 22), (2025, 6, 28)),
        ("copy-of-service-interruption-july-6-12-2025", (2025, 7, 6), (2025, 7, 12)),
        ("service-interruption-december-28-2024-january-3-2025", (2024, 12, 28), (2025, 1, 3)),
    ],
)
def test_slug_week(slug, start, end):
    assert slug_week(slug) == (date(*start), date(*end))


@pytest.mark.parametrize(
    "slug",
    [
        "some-news-post",
        "service-interruption-advisory",
        None,
        # One-date slugs are publish dates, not weeks: the april-6-2024 post runs to April 9.
        "service-interruption-april-6-2024",
        "service-interruption-january-24-2024-1",
    ],
)
def test_slug_week_unknown(slug):
    assert slug_week(slug) is None


DEC7_11 = (date(2024, 12, 7), date(2024, 12, 11))


def test_fix_heading_inside_week_untouched():
    h = parse_heading("December 8, 2024 (Sunday)")
    assert fix_heading(h, DEC7_11) == (h, ())


def test_fix_heading_overnight_end_after_week_untouched():
    h = parse_heading("December 11-12, 2024 (Wednesday-Thursday)")
    assert fix_heading(h, DEC7_11) == (h, ())


@pytest.mark.parametrize(
    ("line", "week"),
    [
        # Slugs are sometimes a day short: the April 6, 2024 post also lists April 7, and
        # `…may-31-june-5-2026` lists Saturday June 6.
        ("April 7, 2024 (Sunday)", (date(2024, 4, 6), date(2024, 4, 6))),
        ("June 6, 2026 (Saturday)", (date(2026, 5, 31), date(2026, 6, 5))),
        ("December 5, 2024 (Thursday)", DEC7_11),
    ],
)
def test_fix_heading_two_days_either_side_untouched(line, week):
    h = parse_heading(line)
    assert fix_heading(h, week) == (h, ())


def test_fix_heading_wrong_month_pinned_by_weekday():
    h = parse_heading("November 7, 2024 (Saturday)")
    fixed, warnings = fix_heading(h, DEC7_11)
    assert (fixed.start, fixed.end) == (date(2024, 12, 7), date(2024, 12, 7))
    assert any("corrected" in w for w in warnings)


def test_fix_heading_wrong_range_end_pinned_by_weekday():
    h = parse_heading("January 15-26, 2026 (Thursday-Friday)")
    fixed, _ = fix_heading(h, (date(2026, 1, 11), date(2026, 1, 16)))
    assert (fixed.start, fixed.end) == (date(2026, 1, 15), date(2026, 1, 16))


def test_fix_heading_unresolvable_raises():
    h = parse_heading("November 7, 2024 (Saturdy)")
    with pytest.raises(HeadingOutsideWeek, match="outside"):
        fix_heading(h, DEC7_11)


def test_fix_heading_without_week_untouched():
    h = parse_heading("November 7, 2024 (Saturday)")
    assert fix_heading(h, None) == (h, ())
