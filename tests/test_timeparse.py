"""Date headings and time ranges (spec 5.3)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from outages.timeparse import MANILA, DateRange, parse_heading, parse_time


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
