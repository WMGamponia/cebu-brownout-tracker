"""Subscriber store (Phase 3 spec 5.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from outages import db
from outages.alerts import store

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


def test_upsert_subscriber_reactivates(conn):
    s = store.upsert_subscriber(conn, 42, "Me", NOW)
    assert s.chat_id == 42 and s.active and s.display_name == "Me"
    store.set_active(conn, s.id, False)
    again = store.upsert_subscriber(conn, 42, "Me2", NOW)
    assert again.id == s.id and again.active and again.display_name == "Me2"
    assert conn.execute("SELECT count(*) FROM subscribers").fetchone()[0] == 1


def test_get_subscriber(conn):
    assert store.get_subscriber(conn, 42) is None
    s = store.upsert_subscriber(conn, 42, None, NOW)
    assert store.get_subscriber(conn, 42) == s


def test_subscribe_idempotent(conn):
    s = store.upsert_subscriber(conn, 42, None, NOW)
    assert store.subscribe(conn, s.id, "730600027", NOW) is True
    assert store.subscribe(conn, s.id, "730600027", NOW) is False
    assert store.subscriptions(conn, s.id) == ["730600027"]


def test_unsubscribe(conn):
    s = store.upsert_subscriber(conn, 42, None, NOW)
    store.subscribe(conn, s.id, "730600027", NOW)
    store.subscribe(conn, s.id, "730600001", NOW)
    assert store.unsubscribe(conn, s.id, "730600027") is True
    assert store.unsubscribe(conn, s.id, "730600027") is False
    assert store.subscriptions(conn, s.id) == ["730600001"]


def test_notes(conn):
    assert store.note_sent(conn, "k") is False
    store.mark_note(conn, "k", NOW)
    assert store.note_sent(conn, "k") is True
    store.mark_note(conn, "k", NOW)  # idempotent


# --- Phase 5: approve-to-join (spec 4.9) ---

CHAT = 5550123  # distinctive, so a scan for it finds only this user's traces


def _all_cells(conn):
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for row in conn.execute(f"SELECT * FROM {table}"):
            for v in row:
                yield table, v


def test_request_join_creates_pending(conn):
    assert store.request_join(conn, CHAT, "Ana", NOW)
    s = store.get_subscriber(conn, CHAT)
    assert (s.status, s.display_name, s.active) == ("pending", "Ana", True)
    assert conn.execute("SELECT requested_at FROM subscribers").fetchone()[0] == store.ts(NOW)
    assert not store.request_join(conn, CHAT, "Ana", NOW)  # already pending
    assert store.pending_count(conn) == 1
    assert [p.chat_id for p in store.pending(conn)] == [CHAT]


def test_decide_idempotent(conn):
    store.request_join(conn, CHAT, "Ana", NOW)
    s = store.decide(conn, CHAT, True, NOW)
    assert s.status == "approved"
    again = store.decide(conn, CHAT, True, NOW)
    assert again.status == "approved" and again.id == s.id
    assert store.decide(conn, 1, True, NOW) is None
    store.request_join(conn, 77, "Bo", NOW)
    d = store.decide(conn, 77, False, NOW)
    assert (d.status, d.display_name) == ("blocked", None)
    assert not store.request_join(conn, 77, "Bo", NOW)  # blocked stays blocked


def test_forget_leaves_no_row_with_chat_id(conn):
    _seed_outage(conn)
    store.request_join(conn, CHAT, "Ana", NOW)
    s = store.decide(conn, CHAT, True, NOW)
    store.subscribe(conn, s.id, "730600041", NOW)
    conn.execute(
        "INSERT INTO alerts_sent (subscriber_id, outage_id, kind, sent_at)"
        " VALUES (?, 7, 'day_of', 't')",
        (s.id,),
    )
    for key in (f"stranger:{CHAT}", f"group:{CHAT}", f"pending_reply:{CHAT}:2026-11-12"):
        store.mark_note(conn, key, NOW)
    store.mark_note(conn, f"parse_failed:{CHAT}:1", NOW)  # a post id that happens to match
    conn.commit()
    with conn:
        assert store.forget(conn, CHAT)
    left = [
        (t, v) for t, v in _all_cells(conn) if v == CHAT or (isinstance(v, str) and f"{CHAT}" in v)
    ]
    assert left == [("admin_notes_sent", f"parse_failed:{CHAT}:1")]
    assert conn.execute("SELECT count(*) FROM subscriptions").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0] == 0
    with conn:
        assert not store.forget(conn, CHAT)
    assert store.request_join(conn, CHAT, "Ana", NOW)  # may ask again


def test_revoke_keeps_blocked_row_without_name(conn):
    _seed_outage(conn)
    store.request_join(conn, CHAT, "Ana", NOW)
    s = store.decide(conn, CHAT, True, NOW)
    store.subscribe(conn, s.id, "730600041", NOW)
    conn.execute(
        "INSERT INTO alerts_sent (subscriber_id, outage_id, kind, sent_at)"
        " VALUES (?, 7, 'day_of', 't')",
        (s.id,),
    )
    assert store.revoke(conn, CHAT, NOW)
    r = store.get_subscriber(conn, CHAT)
    assert (r.status, r.display_name) == ("blocked", None)
    assert store.subscription_count(conn, s.id) == 0
    assert conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0] == 0
    assert not store.revoke(conn, 1, NOW)


def test_sync_allowlist_approves(conn):
    store.request_join(conn, CHAT, "Ana", NOW)
    store.request_join(conn, 77, "Bo", NOW)
    assert store.sync_allowlist(conn, frozenset({CHAT, 12345})) == 1
    assert store.get_subscriber(conn, CHAT).status == "approved"
    assert store.get_subscriber(conn, 77).status == "pending"
    assert store.sync_allowlist(conn, frozenset({CHAT})) == 0


def test_users_and_subscription_count(conn):
    a = store.upsert_subscriber(conn, 42, "Me", NOW)
    store.subscribe(conn, a.id, "730600041", NOW)
    store.subscribe(conn, a.id, "730600060", NOW)
    store.request_join(conn, CHAT, "Ana", NOW)
    assert store.subscription_count(conn, a.id) == 2
    got = [(s.chat_id, s.status, n) for s, n in store.users(conn)]
    assert got == [(42, "approved", 2), (CHAT, "pending", 0)]


def _seed_outage(conn):
    conn.execute("INSERT INTO posts (id, url, slug, first_seen_at) VALUES (1, 'u', 's', 't')")
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    conn.execute(
        "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
        " VALUES (7, 1, 0, 'h', 's', 'e', 'scheduled', 'p', 'a', 'k', 1, 1)"
    )
