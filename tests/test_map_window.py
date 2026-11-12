"""The map's time window (Phase 4 spec 4.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from outages import db
from outages.map.window import Window, WindowError, default_window, parse_window
from outages.timeparse import MANILA

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=MANILA)


@pytest.fixture
def conn():
    c = db.connect(Path(":memory:"))
    c.execute(
        "INSERT INTO posts (id, url, slug, first_seen_at)"
        " VALUES (1, 'https://x.invalid/p', 's', 't')"
    )
    c.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    yield c
    c.close()


def _outage(conn, oid: int, start: str, end: str, status: str = "scheduled") -> None:
    conn.execute(
        "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
        " VALUES (?, 1, ?, 'h', ?, ?, ?, 'p', 'a', 'k', 1, 1)",
        (oid, oid, start, end, status),
    )


def test_default_to_last_end(conn):
    _outage(conn, 1, "2026-11-13T08:00:00+08:00", "2026-11-13T17:00:00+08:00")
    _outage(conn, 2, "2026-11-14T08:00:00+08:00", "2026-11-14T17:00:00+08:00")
    _outage(conn, 3, "2026-11-01T08:00:00+08:00", "2026-11-01T17:00:00+08:00")
    assert default_window(conn, NOW) == Window(date(2026, 11, 12), date(2026, 11, 14))


def test_default_capped_at_7_days(conn):
    _outage(conn, 1, "2026-11-24T08:00:00+08:00", "2026-11-25T17:00:00+08:00")
    assert default_window(conn, NOW).to_date == date(2026, 11, 18)


def test_default_empty_db_is_today(conn):
    assert default_window(conn, NOW) == Window(date(2026, 11, 12), date(2026, 11, 12))


def test_end_at_midnight_counts_previous_day(conn):
    _outage(conn, 1, "2026-11-13T22:00:00+08:00", "2026-11-14T00:00:00+08:00")
    assert default_window(conn, NOW).to_date == date(2026, 11, 13)


def test_ended_at_window_start_ignored(conn):
    _outage(conn, 1, "2026-11-11T22:00:00+08:00", "2026-11-12T00:00:00+08:00")
    assert default_window(conn, NOW) == Window(date(2026, 11, 12), date(2026, 11, 12))


def test_removed_ignored_for_default(conn):
    _outage(conn, 1, "2026-11-16T08:00:00+08:00", "2026-11-16T17:00:00+08:00", "removed")
    assert default_window(conn, NOW) == Window(date(2026, 11, 12), date(2026, 11, 12))


def test_explicit_from_days(conn):
    assert parse_window(conn, NOW, "2026-10-01", "3") == Window(
        date(2026, 10, 1), date(2026, 10, 3)
    )
    assert parse_window(conn, NOW, None, "2") == Window(date(2026, 11, 12), date(2026, 11, 13))
    assert parse_window(conn, NOW, "2026-10-01", None) == Window(
        date(2026, 10, 1), date(2026, 10, 7)
    )


def test_no_query_is_default(conn):
    _outage(conn, 1, "2026-11-13T08:00:00+08:00", "2026-11-13T17:00:00+08:00")
    assert parse_window(conn, NOW, None, None) == default_window(conn, NOW)


@pytest.mark.parametrize(
    ("from_", "days", "param"),
    [
        (None, "0", "days"),
        (None, "15", "days"),
        (None, "x", "days"),
        ("2026-02-30", None, "from"),
        ("12/11/2026", None, "from"),
    ],
)
def test_bad_query(conn, from_, days, param):
    with pytest.raises(WindowError, match=param):
        parse_window(conn, NOW, from_, days)


def test_now_in_utc_uses_manila_date(conn):
    now = datetime(2026, 11, 12, 17, 30, tzinfo=UTC)  # 01:30 on 13 Nov in Manila
    assert default_window(conn, now).from_date == date(2026, 11, 13)


def test_window_bounds_and_days():
    w = Window(date(2026, 11, 12), date(2026, 11, 14))
    assert w.start == datetime(2026, 11, 12, tzinfo=MANILA)
    assert w.end == datetime(2026, 11, 15, tzinfo=MANILA)
    assert w.days() == [date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 14)]
