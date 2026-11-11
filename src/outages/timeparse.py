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

# `CANCELLED` after a heading cancels every outage under it.
_HEADING_STATUS = r"(?:\s*-\s*(?P<status>cancelled))?$"
# The year may follow without a comma (`April 18-19 2026`); the closing `)` may be missing.
_HEADING = re.compile(
    rf"^(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})(?:\s*,\s*(?P<y1>\d{{4}}))?"
    rf"(?:\s*-\s*(?:(?P<m2>{_MONTH})\s+)?(?P<d2>\d{{1,2}}))?"
    rf"(?:\s*,\s*|\s+)(?P<y2>\d{{4}}|0\d{{2}})"  # `024`: a dropped digit, checked by fix_heading
    r"(?:\s*\((?P<wd>[^)]*)\)?)?" + _HEADING_STATUS,
    re.IGNORECASE,
)
# Two full dates, each with its weekday: `August 29, 2024 (Thursday)-August 30, 2024 (Friday)`.
_HEADING_PAIR = re.compile(
    rf"^(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}})\s*,\s*(?P<y1>\d{{4}})\s*\((?P<wd1>[^)]*)\)"
    rf"\s*-\s*(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}})\s*,\s*(?P<y2>\d{{4}})"
    r"(?:\s*\((?P<wd2>[^)]*)\)?)?" + _HEADING_STATUS,
    re.IGNORECASE,
)

_CLOCK = r"\d{1,2}:\d{2}\s*(?:[AP]M|NN)"
_TIME = re.compile(
    rf"^(?P<t1>{_CLOCK})(?:\s+of\s+(?P<m1>{_MONTH})\s+(?P<d1>\d{{1,2}}))?"
    rf"\s+to\s+(?P<t2>{_CLOCK})(?:\s+of\s+(?P<m2>{_MONTH})\s+(?P<d2>\d{{1,2}}))?"
    r"(?:\s*\((?P<label>(?P<hours>\d+(?:\.\d+)?)\s*hrs?(?:\s*(?P<mins>\d+)\s*mins?)?)\))?"
    r"(?:\s*-?\s*\(?(?P<status>additional\s*-\s*cancel+ed+|cancel+ed+|additional(?:\s+outage)?|revised)\)?)?$",
    re.IGNORECASE,
)
# Mechanical typo repairs applied before matching (2026-11-11 backfill).
_REPAIRS = (
    (re.compile(r"^time:\s*", re.IGNORECASE), ""),  # `Time:` repeated inside the value
    (re.compile(r"\b([AP])\.\s*M\.?", re.IGNORECASE), r"\1M"),  # `A.M.`
    (re.compile(r"\b(\d{1,2}):(\d) (\d)\b"), r"\1:\2\3"),  # `5:0 0 PM`
    (re.compile(r"\)\)+"), ")"),  # `(4hrs))`
    (re.compile(r"(\(\d+(?:\.\d+)?\s*hrs?)\s*$", re.IGNORECASE), r"\1)"),  # unclosed `(8hrs`
)

Status = Literal["scheduled", "cancelled", "additional"]


class AmPmSlip(ValueError):
    """The duration label is 12 h off the times: one AM/PM is wrong, but not which one."""


class HeadingOutsideWeek(ValueError):
    """A heading date outside the post's own week that its weekday can't pin down."""


@dataclass(frozen=True)
class DateRange:
    """A date heading: first and last day (equal for a one-day heading)."""

    start: date
    end: date
    weekday_text: str | None
    status: Literal["cancelled"] | None = None
    year_typo: str | None = None  # `024` read as 2024; fix_heading needs the week to agree


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
    year_typo = None
    if m:
        y2 = int(m["y2"])
        if len(m["y2"]) == 3:
            year_typo, y2 = m["y2"], 2000 + y2
        if m["d2"] is None:
            start = _date(y2, m["m1"], m["d1"])
            end = start
        else:
            end = _date(y2, m["m2"] or m["m1"], m["d2"])
            y1 = int(m["y1"]) if m["y1"] else y2
            start = _date(y1, m["m1"], m["d1"])
            if start and end and start > end and not m["y1"]:
                start = _date(y2 - 1, m["m1"], m["d1"])  # `December 31 - January 1, 2026`
        wd = m["wd"].strip() if m["wd"] else None
    else:
        m = _HEADING_PAIR.match(line)
        if not m:
            return None
        start = _date(int(m["y1"]), m["m1"], m["d1"])
        end = _date(int(m["y2"]), m["m2"], m["d2"])
        wd = "-".join(w.strip() for w in (m["wd1"], m["wd2"]) if w and w.strip())
    if start is None or end is None or end < start:
        return None
    return DateRange(start, end, wd or None, "cancelled" if m["status"] else None, year_typo)


_SLUG = re.compile(
    r"interruption-(?P<m1>[a-z]+)-(?P<d1>\d{1,2})(?:-(?P<y1>20\d\d))?"
    r"(?:-(?:(?P<m2>[a-z]+)-)?(?P<d2>\d{1,2}))?-(?P<y2>20\d\d)(?:-\d+)?$"
)


def _slug_month(s: str) -> str | None:
    """`oct` and `october` -> `october`."""
    return next((n for n in MONTHS if len(s) >= 3 and n.startswith(s)), None)


def slug_week(slug: str | None) -> tuple[date, date] | None:
    """The days a post covers, from its slug (`…-interruption-december-7-11-2024`), else None.

    A one-date slug gives None: in 2024 those are publish dates (`april-6-2024` runs to April 9).
    """
    m = _SLUG.search(slug or "")
    if not m or m["d2"] is None:
        return None
    m1 = _slug_month(m["m1"])
    m2 = _slug_month(m["m2"]) if m["m2"] else m1
    if m1 is None or m2 is None:
        return None
    y2 = int(m["y2"])
    end = _date(y2, m2, m["d2"] or m["d1"])
    start = _date(int(m["y1"]) if m["y1"] else y2, m1, m["d1"])
    if start and end and start > end and not m["y1"]:
        start = _date(y2 - 1, m1, m["d1"])
    if start is None or end is None or start > end:
        return None
    return start, end


SLUG_SLACK = timedelta(days=2)


def fix_heading(h: DateRange, week: tuple[date, date] | None) -> tuple[DateRange, tuple[str, ...]]:
    """Correct a heading date that falls outside the post's own week (VECO typos such as
    `November 7, 2024 (Saturday)` in the December 7-11 post).

    A date within SLUG_SLACK days of the week is kept: slugs are sometimes a day short (the
    `april-6-2024` post also lists April 7), and overnight work ends the day after. The typos
    seen are 7 to 30 days off. Further out, the weekday must name exactly one day of the week;
    that day replaces the date, with a warning. Otherwise HeadingOutsideWeek: the post fails
    rather than storing a guess. When the weekday names two days (an 8-day week), the one with
    the heading's day of the month wins: a month typo keeps the day.

    A three-digit year (`024`) is kept only when the dates fall within the week.
    """
    if week is None:
        if h.year_typo:
            raise HeadingOutsideWeek(
                f"year {h.year_typo!r} in a heading and no post week to check it"
            )
        return h, ()
    ws, we = week
    lo, hi = ws - SLUG_SLACK, we + SLUG_SLACK
    if h.year_typo:
        if not (lo <= h.start and h.end <= hi):
            raise HeadingOutsideWeek(
                f"year {h.year_typo!r} read as {h.start.year} puts the heading outside "
                f"the post's week {ws}..{we}"
            )
        return h, (f"year {h.year_typo!r} read as {h.start.year} (post week {ws}..{we})",)
    names = [w.strip().lower() for w in (h.weekday_text or "").split("-")]
    days = [ws + timedelta(days=i) for i in range((we - ws).days + 1)]
    warnings: list[str] = []

    def pin(d: date, name: str) -> date:
        if lo <= d <= hi:
            return d
        hits = [x for x in days if WEEKDAYS[x.weekday()] == name]
        if len(hits) > 1:
            hits = [x for x in hits if x.day == d.day] or hits
        if len(hits) != 1:
            raise HeadingOutsideWeek(
                f"heading date {d} is outside the post's week {ws}..{we} and its weekday "
                f"{name!r} doesn't pin one day"
            )
        warnings.append(f"heading date {d} corrected to {hits[0]} (post week {ws}..{we})")
        return hits[0]

    start = pin(h.start, names[0])
    end = start if h.end == h.start else pin(h.end, names[-1])
    if end < start:
        raise HeadingOutsideWeek(f"corrected heading {start}..{end} runs backwards")
    if (start, end) == (h.start, h.end):
        return h, ()
    return DateRange(start, end, h.weekday_text, h.status), tuple(warnings)


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
    hm, suffix = s[:-2].strip(), s[-2:].upper()
    h, mi = (int(x) for x in hm.split(":"))
    if not (1 <= h <= 12 and 0 <= mi <= 59):
        return None
    if suffix == "NN":  # `12:00 NN` is noon; NN on any other hour is nonsense
        return time(12, mi) if h == 12 else None
    # 12:00 AM is midnight at the start of the day; 12:00 PM is noon.
    return time(h % 12 + (12 if suffix == "PM" else 0), mi)


def _flip(s: str) -> str:
    return s[:-2] + {"AM": "PM", "PM": "AM"}.get(s[-2:].upper(), s[-2:])


def _repair(line: str) -> str:
    for rx, repl in _REPAIRS:
        line = rx.sub(repl, line)
    return line.strip()


def _explicit(m: str, d: str, h: DateRange) -> date | None:
    """`of November 12`: the heading's start year, +1 when the month wraps past December."""
    year = h.start.year + (1 if MONTHS[m.lower()] < h.start.month else 0)
    return _date(year, m, d)


def _am_pm_fix(m: re.Match, day: date, hours: float) -> tuple[datetime, datetime, str] | None:
    """The one AM/PM flip whose span matches the label and stays on one day, else None.

    Flipping either side moves a plain span by the same 12 h, so both flips match the label.
    VECO always writes overnight work with explicit dates (`10:00 PM of April 12 to …`; none
    without in the 2026-11-11 corpus), so a reading that crosses midnight is dropped
    (owner, 2026-11-11). `8:00 AM to 9:00 PM (1hr)` keeps two readings and stays an error.
    """
    found = []
    for a, b in ((_flip(m["t1"]), m["t2"]), (m["t1"], _flip(m["t2"]))):
        ta, tb = _clock(a), _clock(b)
        if ta is None or tb is None or tb <= ta:
            continue
        s, e = datetime.combine(day, ta, MANILA), datetime.combine(day, tb, MANILA)
        if abs((e - s).total_seconds() / 3600 - hours) < 0.01:
            found.append((s, e, f"{a} to {b}"))
    return found[0] if len(found) == 1 else None


def parse_time(line: str, heading: DateRange) -> TimeRange | None:
    """A time line under `heading`; None when it matches no known form (a hard failure).

    Raises AmPmSlip when the duration label is exactly 12 h off the times and _am_pm_fix
    finds no single reading.
    """
    m = _TIME.match(_repair(line))
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

    # An end on the day after the heading is overnight work, not a stray date.
    outside = {
        d
        for d, explicit, last in (
            (d1, m["m1"], heading.end),
            (d2, m["m2"], heading.end + timedelta(days=1)),
        )
        if explicit and d is not None and not heading.start <= d <= last
    }
    for d in sorted(outside):
        warnings.append(f"explicit date {d} outside heading {heading.start}..{heading.end}")

    if m["hours"] is not None:
        computed = (end - start).total_seconds() / 3600
        labelled = float(m["hours"]) + int(m["mins"] or 0) / 60
        off = abs(computed - labelled)
        if abs(off - 12) < 0.01:
            fixed = _am_pm_fix(m, d1, labelled) if not (m["m1"] or m["m2"]) else None
            if fixed is None:
                raise AmPmSlip(
                    f"AM/PM slip in {line!r}: label {m['label']!r} but times span "
                    f"{computed:g}h; could be {_flip(m['t1'])!r} or {_flip(m['t2'])!r}"
                )
            start, end, reading = fixed
            warnings.append(f"AM/PM slip: {line!r} read as {reading!r} to match the label")
        elif off > 0.01:
            warnings.append(f"duration label {m['label']!r} but times span {computed:g}h")

    word = (m["status"] or "").lower()
    status: Status = "scheduled"
    if "cancel" in word:
        status = "cancelled"
        if "additional" in word:
            warnings.append(f"{m['status']!r} read as cancelled")
    elif word.startswith("additional"):
        status = "additional"
    elif word == "revised":
        warnings.append("marked REVISED; status left as scheduled")
    return TimeRange(start, end, m["label"], status, tuple(sorted(warnings)))
