"""Subscribers, subscriptions and admin-note keys (Phase 3 spec 5.1). Callers commit."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Subscriber:
    id: int
    chat_id: int
    display_name: str | None
    active: bool


def ts(now: datetime) -> str:
    """Bookkeeping timestamp: UTC ISO 8601, seconds."""
    return now.astimezone(UTC).isoformat(timespec="seconds")


def get_subscriber(conn: sqlite3.Connection, chat_id: int) -> Subscriber | None:
    row = conn.execute(
        "SELECT id, chat_id, display_name, active FROM subscribers WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return Subscriber(row[0], row[1], row[2], bool(row[3])) if row else None


def upsert_subscriber(
    conn: sqlite3.Connection, chat_id: int, display_name: str | None, now: datetime
) -> Subscriber:
    """Create the subscriber, or reactivate it and refresh its display name."""
    conn.execute(
        "INSERT INTO subscribers (chat_id, display_name, created_at, active) VALUES (?, ?, ?, 1)"
        " ON CONFLICT(chat_id) DO UPDATE SET active = 1,"
        " display_name = coalesce(excluded.display_name, display_name)",
        (chat_id, display_name, ts(now)),
    )
    sub = get_subscriber(conn, chat_id)
    assert sub is not None
    return sub


def set_active(conn: sqlite3.Connection, subscriber_id: int, active: bool) -> None:
    conn.execute("UPDATE subscribers SET active = ? WHERE id = ?", (int(active), subscriber_id))


def subscribe(conn: sqlite3.Connection, subscriber_id: int, psgc_code: str, now: datetime) -> bool:
    """Add a subscription; False when it already exists (its created_at is kept)."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO subscriptions (subscriber_id, psgc_code, created_at)"
        " VALUES (?, ?, ?)",
        (subscriber_id, psgc_code, ts(now)),
    )
    return cur.rowcount == 1


def unsubscribe(conn: sqlite3.Connection, subscriber_id: int, psgc_code: str) -> bool:
    cur = conn.execute(
        "DELETE FROM subscriptions WHERE subscriber_id = ? AND psgc_code = ?",
        (subscriber_id, psgc_code),
    )
    return cur.rowcount == 1


def subscriptions(conn: sqlite3.Connection, subscriber_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT psgc_code FROM subscriptions WHERE subscriber_id = ?"
        " ORDER BY created_at, psgc_code",
        (subscriber_id,),
    )
    return [r[0] for r in rows]


def note_sent(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT 1 FROM admin_notes_sent WHERE key = ?", (key,)).fetchone()
    return row is not None


def mark_note(conn: sqlite3.Connection, key: str, now: datetime) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO admin_notes_sent (key, sent_at) VALUES (?, ?)", (key, ts(now))
    )
