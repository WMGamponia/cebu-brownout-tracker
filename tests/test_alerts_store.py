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
