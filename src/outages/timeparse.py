"""Date headings and time ranges -> aware datetimes in Asia/Manila (spec 5.3).

Input lines are already normalized (`normalize_line`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

MANILA = ZoneInfo("Asia/Manila")

_MONTH_NAMES = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
MONTHS = {m: i for i, m in enumerate(_MONTH_NAMES, start=1)}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_MONTH = r"(?:" + "|".join(MONTHS) + r")"

_HEADING = re.compile(
    rf"^(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})(?:,\s*(?P<y1>\d{{4}}))?"
    rf"(?:\s*-\s*(?:(?P<m2>{_MONTH})\s+)?(?P<d2>\d{{1,2}}))?"
    rf",\s*(?P<y2>\d{{4}})"
    r"(?:\s*\((?P<wd>[^)]*)\))?$",
    re.IGNORECASE,
)

_CLOCK = r"\d{1,2}:\d{2}\s*[AP]M"
_TIME = re.compile(
    rf"^(?P<t1>{_CLOCK})(?:\s+of\s+(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}}))?"
    rf"\s+to\s+(?P<t2>{_CLOCK})(?:\s+of\s+(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}}))?"
    r"(?:\s*\((?P<label>(?P<hours>\d+(?:\.\d+)?)\s*hrs?)\))?"
    r"(?:\s*-?\s*(?P<status>cancelled|additional))?$",
    re.IGNORECASE,
)

Status = Literal["scheduled", "cancelled", "additional"]


@dataclass(frozen=True)
class DateRange:
    """A date heading: first and last day (equal for a one-day heading)."""

    start: date
    end: date
    weekday_text: str | None


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    duration_label: str | None
    status: Status
    warnings: tuple[str, ...]


def _date(y: int, m: str, d: str) -> date | None:
    try:
        return date(y, MONTHS[m.lower()], int(d))
    except ValueError:
        return None


def parse_heading(line: str) -> DateRange | None:
    """`November 11-12, 2026 (Wednesday-Thursday)` and the other spec 5.3 forms, else None."""
    m = _HEADING.match(line)
    if not m:
        return None
    y2 = int(m["y2"])
    if m["d2"] is None:
        start = _date(y2, m["m1"], m["d1"])
        end = start
    else:
        end = _date(y2, m["m2"] or m["m1"], m["d2"])
        y1 = int(m["y1"]) if m["y1"] else y2
        start = _date(y1, m["m1"], m["d1"])
        if start and end and start > end and not m["y1"]:
            start = _date(y2 - 1, m["m1"], m["d1"])  # `December 31 - January 1, 2026`
    if start is None or end is None or end < start:
        return None
    wd = m["wd"].strip() if m["wd"] else None
    return DateRange(start, end, wd or None)


def heading_warnings(h: DateRange) -> tuple[str, ...]:
    """Warn when the heading's weekday text doesn't match its dates."""
    if not h.weekday_text:
        return ()
    names = [w.strip().lower() for w in h.weekday_text.split("-")]
    expected = [WEEKDAYS[h.start.weekday()]]
    if h.end != h.start:
        expected.append(WEEKDAYS[h.end.weekday()])
    if names != expected:
        return (f"weekday {h.weekday_text!r} does not match {h.start}..{h.end}",)
    return ()


def _clock(s: str) -> time | None:
    hm, ampm = s[:-2].strip(), s[-2:].upper()
    h, mi = (int(x) for x in hm.split(":"))
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    # 12:00 AM is midnight at the start of the day; 12:00 PM is noon.
    return time(h % 12 + (12 if ampm == "PM" else 0), mi)


def _explicit(m: str, d: str, h: DateRange) -> date | None:
    """`of November 12`: the heading's start year, +1 when the month wraps past December."""
    year = h.start.year + (1 if MONTHS[m.lower()] < h.start.month else 0)
    return _date(year, m, d)


def parse_time(line: str, heading: DateRange) -> TimeRange | None:
    """A time line under `heading`; None when it matches no known form (a hard failure)."""
    m = _TIME.match(line)
    if not m:
        return None
    t1, t2 = _clock(m["t1"]), _clock(m["t2"])
    if t1 is None or t2 is None:
        return None
    warnings: list[str] = []

    d1 = _explicit(m["m1"], m["d1"], heading) if m["m1"] else heading.start
    d2 = _explicit(m["m2"], m["d2"], heading) if m["m2"] else None
    if d1 is None or (m["m2"] and d2 is None):
        return None
    start = datetime.combine(d1, t1, MANILA)
    if d2 is None:
        end = datetime.combine(d1, t2, MANILA)
        if end <= start:
            end += timedelta(days=1)
    else:
        end = datetime.combine(d2, t2, MANILA)
        if end <= start:
            warnings.append(f"end {end.isoformat()} is not after start {start.isoformat()}")

    for d in {d for d in (d1, d2) if d is not None and (m["m1"] or m["m2"])}:
        if not heading.start <= d <= heading.end:
            warnings.append(f"explicit date {d} outside heading {heading.start}..{heading.end}")

    if m["hours"] is not None:
        computed = (end - start).total_seconds() / 3600
        if abs(computed - float(m["hours"])) > 0.01:
            warnings.append(f"duration label {m['label']!r} but times span {computed:g}h")

    status: Status = m["status"].lower() if m["status"] else "scheduled"  # type: ignore[assignment]
    return TimeRange(start, end, m["label"], status, tuple(sorted(warnings)))
