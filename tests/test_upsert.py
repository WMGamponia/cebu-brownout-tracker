"""Matching a re-parse against stored outages, and applying the changes (spec 6.3)."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from outages import db
from outages.db import StoredOutage
from outages.extract import ExtractedPost, Line
from outages.parse import ParsedOutage, areas_key
from outages.timeparse import MANILA
from outages.upsert import Change, match


def P(seq, start="09:00", end="12:00", areas="Lahug", status="scheduled", day=9):
    def at(hm):
        h, m = map(int, hm.split(":"))
        return datetime(2026, 11, day, h, m, tzinfo=MANILA)

    return ParsedOutage(
        seq=seq,
        heading_text=f"November {day}, 2026",
        start_at=at(start),
        end_at=at(end),
        duration_label=None,
        status=status,
        purpose="p",
        areas_raw=areas,
        areas_key=areas_key(areas),
        map_image_url=None,
        warnings=(),
    )


def S(id_, *args, **kw):
    p = P(*args, **kw)
    j = p.to_json()
    return StoredOutage(
        id=id_,
        post_id=1,
        seq=p.seq,
        heading_text=p.heading_text,
        start_at=j["start_at"],
        end_at=j["end_at"],
        duration_label=None,
        status=p.status,
        purpose="p",
        areas_raw=p.areas_raw,
        areas_key=p.areas_key,
        map_image_url=None,
        warnings=(),
    )


def kinds(changes: list[Change]) -> list[tuple[str, int | None, int | None]]:
    return [(c.kind, c.old.id if c.old else None, c.new.seq if c.new else None) for c in changes]


def test_identical_lists_unchanged():
    old = [S(1, 0), S(2, 1, areas="Tisa")]
    new = [P(0), P(1, areas="Tisa")]
    assert kinds(match(old, new)) == [("unchanged", 1, 0), ("unchanged", 2, 1)]


def test_cancelled():
    assert kinds(match([S(1, 0)], [P(0, status="cancelled")])) == [("cancelled", 1, 0)]


def test_uncancelled_is_status_changed():
    assert kinds(match([S(1, 0, status="cancelled")], [P(0)])) == [("status_changed", 1, 0)]


def test_time_changed_not_removed_and_added():
    assert kinds(match([S(1, 0)], [P(0, start="10:00")])) == [("time_changed", 1, 0)]


def test_areas_changed():
    old = [S(1, 0, areas="Portion of Lahug, Cebu Cty")]
    new = [P(0, areas="Portion of Lahug, Cebu City")]
    assert kinds(match(old, new)) == [("areas_changed", 1, 0)]


def test_removed_and_added():
    old = [S(1, 0), S(2, 1, areas="Tisa")]
    new = [P(0), P(1, areas="Mabolo", start="13:00", end="15:00")]
    assert kinds(match(old, new)) == [
        ("unchanged", 1, 0),
        ("removed", 2, None),
        ("added", None, 1),
    ]


def test_different_day_is_not_a_match():
    assert kinds(match([S(1, 0)], [P(0, day=10)])) == [("removed", 1, None), ("added", None, 0)]


def test_tie_pairs_in_page_order():
    old = [S(1, 0, start="09:00", end="12:00"), S(2, 1, start="13:00", end="16:00")]
    new = [P(0, start="10:00", end="12:00"), P(1, start="14:00", end="16:00")]
    assert kinds(match(old, new)) == [("time_changed", 1, 0), ("time_changed", 2, 1)]


def test_pass_one_wins_over_pass_two():
    # Old 2 matches new 0 exactly; old 1 must not grab new 0 by areas in pass 2.
    old = [S(1, 0, start="08:00", end="10:00"), S(2, 1, start="09:00", end="12:00")]
    new = [P(0, start="09:00", end="12:00")]
    assert kinds(match(old, new)) == [("unchanged", 2, 0), ("removed", 1, None)]


# --- apply_changes ------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


def _version(conn, pid, h):
    ex = ExtractedPost("T", (Line("x", False, ()),), "<s/>", h)
    return db.store_version(conn, pid, ex, "2026-11-10T00:00:00+00:00")


def test_first_parse_logs_nothing(conn):
    pid = db.upsert_post(conn, "u", "s", None)
    v = _version(conn, pid, "h1")
    db.apply_changes(conn, pid, v, match([], [P(0), P(1, areas="Tisa")]), False, "t")
    assert len(db.active_outages(conn, pid)) == 2
    assert conn.execute("SELECT count(*) FROM outage_changes").fetchone()[0] == 0


def test_reparse_logs_each_change(conn):
    pid = db.upsert_post(conn, "u", "s", None)
    v1 = _version(conn, pid, "h1")
    db.apply_changes(conn, pid, v1, match([], [P(0), P(1, areas="Tisa")]), False, "t1")
    v2 = _version(conn, pid, "h2")
    old = db.active_outages(conn, pid)
    changes = match(old, [P(0, start="10:00"), P(1, areas="Mabolo", day=10)])
    db.apply_changes(conn, pid, v2, changes, True, "t2")

    rows = conn.execute(
        "SELECT kind, old_json, new_json, version_id, detected_at FROM outage_changes ORDER BY id"
    ).fetchall()
    assert [r["kind"] for r in rows] == ["time_changed", "removed", "added"]
    tc = rows[0]
    assert json.loads(tc["old_json"])["start_at"] == "2026-11-09T09:00:00+08:00"
    assert json.loads(tc["new_json"])["start_at"] == "2026-11-09T10:00:00+08:00"
    assert (tc["version_id"], tc["detected_at"]) == (v2, "t2")
    assert json.loads(rows[1]["new_json"] or "null") is None

    statuses = conn.execute("SELECT areas_raw, status FROM outages ORDER BY id").fetchall()
    assert [tuple(r) for r in statuses] == [
        ("Lahug", "scheduled"),
        ("Tisa", "removed"),  # kept, not deleted
        ("Mabolo", "scheduled"),
    ]
    first = conn.execute("SELECT start_at, last_seen_version_id FROM outages WHERE id=1").fetchone()
    assert tuple(first) == ("2026-11-09T10:00:00+08:00", v2)
    assert [o.areas_raw for o in db.active_outages(conn, pid)] == ["Lahug", "Mabolo"]
