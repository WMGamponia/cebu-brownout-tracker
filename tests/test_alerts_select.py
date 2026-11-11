"""Due alert items (Phase 3 spec 4.1, 4.2, 4.3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from outages import db
from outages.alerts import store
from outages.alerts.select import (
    Change,
    Outage,
    Row,
    Snapshot,
    Sub,
    due_items,
    load,
)
from outages.areas.gazetteer import load as load_gazetteer
from outages.timeparse import MANILA

LAHUG, CC = "730600041", "730600000"
TAYUD_CO, TAYUD_LI = "702219018", "702227013"
SUBBED = datetime(2026, 11, 1, tzinfo=UTC)


def m(day: int, hh: int, mm: int = 0) -> datetime:
    """A Manila time in November 2026."""
    return datetime(2026, 11, day, hh, mm, tzinfo=MANILA)


def outage(start: datetime, *, status: str = "scheduled", oid: int = 1, hours: int = 8) -> Outage:
    return Outage(
        id=oid,
        post_url="https://example.invalid/p",
        start_at=start,
        end_at=start + timedelta(hours=hours),
        status=status,
        purpose="Line maintenance",
        areas_hash="h1",
        post_failed=False,
    )


def snap(
    outages: list[Outage],
    *,
    match: str = "exact",
    rows: list[Row] | None = None,
    subs: list[Sub] | None = None,
    changes: list[Change] | None = None,
    sent: set[tuple[int, int, str, int]] | None = None,
) -> Snapshot:
    return Snapshot(
        subs=subs or [Sub(1, 42, LAHUG, SUBBED)],
        outages={o.id: o for o in outages},
        rows={"h1": rows if rows is not None else [Row(LAHUG, "Lahug", CC, match)]},
        entities={},
        changes=changes or [],
        sent=frozenset(sent or ()),
    )


def kinds(s: Snapshot, now: datetime) -> list[str]:
    return [i.kind for i in due_items(s, now)]


NINE = outage(m(13, 9))


@pytest.mark.parametrize(
    ("now", "sent", "expected"),
    [
        (m(12, 19, 59), set(), []),
        (m(12, 20), set(), ["day_before"]),
        (m(13, 5, 59), set(), ["day_before"]),
        (m(13, 6), set(), ["day_of"]),  # day_before dropped when day_of is due
        (m(13, 6), {(1, 1, "day_before", 0)}, ["day_of"]),
        (m(13, 7), {(1, 1, "day_of", 0)}, []),  # never falls back to day_before
        (m(12, 21), {(1, 1, "day_before", 0)}, []),
        (m(13, 9), set(), []),  # started
    ],
)
def test_due_nine_am(now, sent, expected):
    assert kinds(snap([NINE], sent=sent), now) == expected


def test_five_am_start_has_day_before_only():
    five = outage(m(13, 5))
    assert kinds(snap([five]), m(12, 20)) == ["day_before"]
    assert kinds(snap([five]), m(13, 1)) == ["day_before"]  # woke after midnight
    assert kinds(snap([five]), m(13, 6)) == []


def test_ten_pm_start_day_of_still_due_at_20():
    late = outage(m(13, 22))
    assert kinds(snap([late]), m(13, 20)) == ["day_of"]


@pytest.mark.parametrize("status", ["cancelled", "removed"])
def test_no_reminder_for_cancelled_or_removed(status):
    assert kinds(snap([outage(m(13, 9), status=status)]), m(13, 6)) == []


def test_additional_gets_reminders():
    assert kinds(snap([outage(m(13, 9), status="additional")]), m(13, 6)) == ["day_of"]


@pytest.mark.parametrize(
    ("match", "expected"),
    [
        ("exact", ["day_of"]),
        ("alias", ["day_of"]),
        ("ambiguous", ["day_of"]),
        ("other_town", []),
        ("unmatched", []),
    ],
)
def test_match_policy(match, expected):
    assert kinds(snap([NINE], match=match), m(13, 6)) == expected


def test_other_barangay_not_alerted():
    rows = [Row("730600001", "Adlaon", CC, "exact")]
    assert kinds(snap([NINE], rows=rows), m(13, 6)) == []


def test_unparsed_areas_not_alerted():
    o = Outage(1, "u", m(13, 9), m(13, 17), "scheduled", "p", None, False)
    assert kinds(snap([o]), m(13, 6)) == []


def test_post_failed_still_alerts():
    o = Outage(1, "u", m(13, 9), m(13, 17), "scheduled", "p", "h1", True)
    assert kinds(snap([o]), m(13, 6)) == ["day_of"]


def _change(kind: str, detected: datetime, *, old=None, new=None, old_codes=frozenset(), cid=5):
    return Change(cid, 1, kind, old, new, detected, frozenset(old_codes))


def test_change_items():
    o = outage(m(14, 9))
    ch = _change("time_changed", m(12, 3), old={"end_at": m(13, 17).isoformat()})
    items = due_items(snap([o], changes=[ch]), m(12, 6))
    assert [(i.kind, i.change_id, i.change_kind) for i in items] == [("change", 5, "time_changed")]
    assert kinds(snap([o], changes=[ch], sent={(1, 1, "change", 5)}), m(12, 6)) == []


def test_change_before_subscription_not_sent():
    o = outage(m(14, 9))
    ch = _change("time_changed", SUBBED - timedelta(seconds=1))
    assert kinds(snap([o], changes=[ch]), m(12, 6)) == []


def test_change_to_ended_outage_not_sent():
    o = outage(m(12, 1), hours=2)  # ended 03:00
    old = {"end_at": m(12, 4).isoformat()}
    ch = _change("time_changed", m(12, 2), old=old, new={"end_at": m(12, 3).isoformat()})
    assert kinds(snap([o], changes=[ch]), m(12, 5)) == []


def test_change_to_started_but_not_ended_is_sent():
    o = outage(m(12, 1), hours=8)  # ends 09:00
    ch = _change("time_changed", m(12, 2))
    assert kinds(snap([o], changes=[ch]), m(12, 6)) == ["change"]


def test_cancelled_change_is_sent_without_reminder():
    o = outage(m(14, 9), status="cancelled")
    ch = _change("cancelled", m(13, 3))
    assert kinds(snap([o], changes=[ch]), m(13, 20)) == ["change"]


def test_areas_changed_direction():
    o = outage(m(14, 9))
    out = _change("areas_changed", m(12, 3), old_codes={LAHUG})
    rows_without = [Row("730600001", "Adlaon", CC, "exact")]
    items = due_items(snap([o], rows=rows_without, changes=[out]), m(12, 6))
    assert [(i.kind, i.direction) for i in items] == [("change", "out")]

    into = _change("areas_changed", m(12, 3), old_codes={"730600001"})
    items = due_items(snap([o], changes=[into]), m(12, 6))
    assert [(i.kind, i.direction) for i in items] == [("change", "in")]

    neither = _change("areas_changed", m(12, 3), old_codes={"730600001"})
    assert due_items(snap([o], rows=rows_without, changes=[neither]), m(12, 6)) == []

    both = _change("areas_changed", m(12, 3), old_codes={LAHUG})
    items = due_items(snap([o], changes=[both]), m(12, 6))
    assert [(i.kind, i.direction) for i in items] == [("change", None)]


def test_two_subscriptions_one_item():
    rows = [
        Row(TAYUD_CO, "Tayud", "702219000", "ambiguous"),
        Row(TAYUD_LI, "Tayud", "702227000", "ambiguous"),
    ]
    subs = [Sub(1, 42, TAYUD_CO, SUBBED), Sub(1, 42, TAYUD_LI, SUBBED)]
    items = due_items(snap([NINE], rows=rows, subs=subs), m(13, 6))
    assert [(i.subscriber_id, i.outage_id, i.kind) for i in items] == [(1, 1, "day_of")]


def test_two_subscribers_each_get_item():
    subs = [Sub(1, 42, LAHUG, SUBBED), Sub(2, 43, LAHUG, SUBBED)]
    items = due_items(snap([NINE], subs=subs), m(13, 6))
    assert sorted((i.subscriber_id, i.chat_id) for i in items) == [(1, 42), (2, 43)]


def test_items_ordered_by_start():
    a, b = outage(m(13, 13), oid=1), outage(m(13, 9), oid=2)
    assert [i.outage_id for i in due_items(snap([a, b]), m(13, 6))] == [2, 1]


def test_due_times_use_manila(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    now = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # 20:00 Manila
    assert kinds(snap([NINE]), now) == ["day_before"]
    assert kinds(snap([NINE]), now - timedelta(minutes=1)) == []


# --- load() on a tmp DB ---------------------------------------------------------------------

NOW = m(12, 20)


def _seed(conn: sqlite3.Connection) -> None:
    """One post, one outage on 13 Nov in Lahug (exact), one areas_changed from Adlaon."""
    gaz = load_gazetteer()
    from outages.areas import store as areas_store
    from outages.areas.entities_rules import RulesExtractor

    raw = "Portion of Cebu City (Lahug) along Gorordo Ave."
    old_raw = "Portion of Cebu City (Adlaon) along Gorordo Ave."
    h = areas_store.areas_hash(raw)
    conn.execute(
        "INSERT INTO posts (id, url, slug, first_seen_at, parse_status)"
        " VALUES (1, 'https://example.invalid/p', 's', 't', 'failed')"
    )
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    for oid, start, end in [
        (1, m(13, 9), m(13, 17)),
        (2, m(1, 9), m(1, 17)),  # long ended: not loaded
    ]:
        conn.execute(
            "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status,"
            " purpose, areas_raw, areas_key, first_seen_version_id, last_seen_version_id,"
            " areas_hash) VALUES (?, 1, ?, 'h', ?, ?, 'scheduled', 'Maint', ?, 'k', 1, 1, ?)",
            (oid, oid, start.isoformat(), end.isoformat(), raw, h),
        )
    areas_store.write_parse(
        conn, h, areas_store.parse_one(raw, gaz, RulesExtractor()), "rules", NOW
    )
    conn.execute(
        "INSERT INTO outage_changes (id, outage_id, version_id, kind, old_json, new_json,"
        " detected_at) VALUES (5, 1, 1, 'areas_changed', ?, ?, ?)",
        (
            json.dumps({"areas_raw": old_raw, "end_at": m(13, 17).isoformat()}),
            json.dumps({"areas_raw": raw, "end_at": m(13, 17).isoformat()}),
            "2026-11-12T03:00:00+00:00",
        ),
    )
    sub = store.upsert_subscriber(conn, 42, "me", SUBBED)
    store.subscribe(conn, sub.id, LAHUG, SUBBED)
    off = store.upsert_subscriber(conn, 43, "off", SUBBED)
    store.subscribe(conn, off.id, LAHUG, SUBBED)
    store.set_active(conn, off.id, False)
    conn.execute(
        "INSERT INTO alerts_sent (subscriber_id, outage_id, kind, change_id, sent_at)"
        " VALUES (?, 1, 'change', 5, 't')",
        (sub.id,),
    )
    conn.commit()


def test_load_snapshot(tmp_path):
    conn = db.connect(tmp_path / "o.db")
    _seed(conn)
    s = load(conn, NOW, load_gazetteer())
    assert [(x.chat_id, x.psgc_code) for x in s.subs] == [(42, LAHUG)]  # inactive skipped
    assert list(s.outages) == [1]
    o = s.outages[1]
    assert o.start_at == m(13, 9) and o.post_failed and o.purpose == "Maint"
    assert [r.psgc_code for r in s.rows[o.areas_hash]] == [LAHUG]
    assert "Gorordo Ave." in s.entities[o.areas_hash]
    assert s.sent == frozenset({(1, 1, "change", 5)})
    conn.close()


def test_load_resolves_old_areas(tmp_path):
    conn = db.connect(tmp_path / "o.db")
    _seed(conn)
    s = load(conn, NOW, load_gazetteer())
    (ch,) = s.changes
    assert ch.kind == "areas_changed" and ch.old_codes == frozenset({"730600001"})
    assert ch.detected_at == datetime(2026, 11, 12, 3, tzinfo=UTC)
    conn.close()


def test_load_empty_without_migration(tmp_path):
    conn = sqlite3.connect(tmp_path / "v2.db")
    for n, sql in db._migrations():
        if n <= 2:
            conn.executescript(sql)
    s = load(conn, NOW, load_gazetteer())
    assert s.subs == [] and s.outages == {} and s.changes == []
    conn.close()


# --- review fixes ---------------------------------------------------------------------------


def test_removed_change_alerts_via_old_codes():
    """`areas` deletes a removed outage's parse rows, so `removed` uses the old text's codes."""
    o = outage(m(14, 9), status="removed")
    ch = _change("removed", m(12, 3), old_codes={LAHUG})
    items = due_items(snap([o], rows=[], changes=[ch]), m(12, 6))
    assert [(i.kind, i.change_kind) for i in items] == [("change", "removed")]
    other = _change("removed", m(12, 3), old_codes={"730600001"})
    assert due_items(snap([o], rows=[], changes=[other]), m(12, 6)) == []


def test_areas_changed_waits_for_unparsed_new_text():
    """A failed areas step must not tell a subscriber their barangay was dropped."""
    o = outage(m(14, 9))
    ch = _change("areas_changed", m(12, 3), old_codes={LAHUG})
    s = snap([o], rows=[], changes=[ch])
    s = Snapshot(s.subs, s.outages, {}, {}, s.changes, s.sent, unparsed=frozenset({"h1"}))
    assert due_items(s, m(12, 6)) == []
