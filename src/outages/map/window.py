"""The map's time window: inclusive Manila dates (Phase 4 spec 4.2).

A window covers [from 00:00, to+1 00:00) Manila. By default it runs from today to the date of
the last stored end, capped at 7 days; `?from=YYYY-MM-DD&days=N` picks another range.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from outages.timeparse import MANILA

DEFAULT_DAYS = 7
MAX_DAYS = 14


class WindowError(ValueError):
    """A bad `from` or `days` query parameter; the message names it."""


@dataclass(frozen=True)
class Window:
    from_date: date
    to_date: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.from_date, time(0), MANILA)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.to_date + timedelta(days=1), time(0), MANILA)

    def days(self) -> list[date]:
        n = (self.to_date - self.from_date).days + 1
        return [self.from_date + timedelta(days=i) for i in range(n)]


def iso(dt: datetime) -> str:
    """The stored outage-time format (`2026-11-12T00:00:00+08:00`), comparable as text."""
    return dt.astimezone(MANILA).isoformat(timespec="seconds")


def _today(now: datetime) -> date:
    return now.astimezone(MANILA).date()


def default_window(conn: sqlite3.Connection, now: datetime) -> Window:
    """From today to the Manila date of the latest non-removed end after today 00:00 (an end at
    exactly 00:00 belongs to the day before), capped at 7 days."""
    start = _today(now)
    row = conn.execute(
        "SELECT max(end_at) FROM outages WHERE status != 'removed' AND end_at > ?",
        (iso(datetime.combine(start, time(0), MANILA)),),
    ).fetchone()
    if row[0] is None:
        return Window(start, start)
    last = datetime.fromisoformat(row[0]).astimezone(MANILA) - timedelta(microseconds=1)
    return Window(start, min(last.date(), start + timedelta(days=DEFAULT_DAYS - 1)))


def parse_window(
    conn: sqlite3.Connection, now: datetime, from_: str | None, days: str | None
) -> Window:
    """The window for a query; no parameters → `default_window`. Raises `WindowError`."""
    if from_ is None and days is None:
        return default_window(conn, now)
    if from_ is None:
        start = _today(now)
    else:
        try:
            if len(from_) != 10:
                raise ValueError
            start = date.fromisoformat(from_)
        except ValueError:
            raise WindowError(f"from must be a date as YYYY-MM-DD, got {from_!r}") from None
    n = DEFAULT_DAYS
    if days is not None:
        if not days.isdigit() or not 1 <= int(days) <= MAX_DAYS:
            raise WindowError(f"days must be a whole number from 1 to {MAX_DAYS}, got {days!r}")
        n = int(days)
    return Window(start, start + timedelta(days=n - 1))
