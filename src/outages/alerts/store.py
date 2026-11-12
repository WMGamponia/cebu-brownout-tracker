"""Subscribers, subscriptions and admin-note keys (Phase 3 spec 5.1; Phase 5 spec 4.9: the
approve-to-join status, /forget and /revoke). Callers commit."""

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
    status: str = "approved"  # pending | approved | blocked


# Admin-note kinds whose key holds a chat ID ("<kind>:<chat_id>" or "<kind>:<chat_id>:..."):
# forget() deletes exactly these, never another note whose id happens to match.
CHAT_NOTE_KINDS = ("stranger", "group", "pending_reply")
_COLS = ("id", "chat_id", "display_name", "active", "status")


def _sub(row) -> Subscriber:
    return Subscriber(row[0], row[1], row[2], bool(row[3]), row[4])


def ts(now: datetime) -> str:
    """Bookkeeping timestamp: UTC ISO 8601, seconds."""
    return now.astimezone(UTC).isoformat(timespec="seconds")


def get_subscriber(conn: sqlite3.Connection, chat_id: int) -> Subscriber | None:
    row = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM subscribers WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    return _sub(row) if row else None


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


def subscription_count(conn: sqlite3.Connection, subscriber_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM subscriptions WHERE subscriber_id = ?", (subscriber_id,)
    ).fetchone()[0]


def request_join(
    conn: sqlite3.Connection, chat_id: int, first_name: str | None, now: datetime
) -> bool:
    """A pending row for a chat with none (new, or one that used /forget); False otherwise."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO subscribers (chat_id, display_name, created_at, active, status,"
        " requested_at) VALUES (?, ?, ?, 1, 'pending', ?)",
        (chat_id, first_name, ts(now), ts(now)),
    )
    return cur.rowcount == 1


def decide(
    conn: sqlite3.Connection, chat_id: int, approve: bool, now: datetime
) -> Subscriber | None:
    """Approve, or block (a block clears the name). Idempotent; None when there is no row."""
    if approve:
        conn.execute(
            "UPDATE subscribers SET status = 'approved', decided_at = ? WHERE chat_id = ?",
            (ts(now), chat_id),
        )
    else:
        conn.execute(
            "UPDATE subscribers SET status = 'blocked', display_name = NULL, decided_at = ?"
            " WHERE chat_id = ?",
            (ts(now), chat_id),
        )
    return get_subscriber(conn, chat_id)


def pending(conn: sqlite3.Connection) -> list[Subscriber]:
    rows = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM subscribers WHERE status = 'pending'"
        " ORDER BY requested_at, id"
    )
    return [_sub(r) for r in rows]


def pending_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT count(*) FROM subscribers WHERE status = 'pending'").fetchone()[0]


def users(conn: sqlite3.Connection) -> list[tuple[Subscriber, int]]:
    """Every subscriber with its number of subscriptions (never the barangays)."""
    rows = conn.execute(
        f"SELECT {', '.join('s.' + c for c in _COLS)},"
        " (SELECT count(*) FROM subscriptions x WHERE x.subscriber_id = s.id)"
        " FROM subscribers s ORDER BY s.id"
    )
    return [(_sub(r), r[5]) for r in rows]


def _delete_chat_notes(conn: sqlite3.Connection, chat_id: int) -> int:
    n = 0
    for kind in CHAT_NOTE_KINDS:
        n += conn.execute(
            "DELETE FROM admin_notes_sent WHERE key = ? OR key LIKE ?",
            (f"{kind}:{chat_id}", f"{kind}:{chat_id}:%"),
        ).rowcount
    return n


def forget(conn: sqlite3.Connection, chat_id: int) -> bool:
    """Delete every row holding this chat ID: its subscriptions, alert ledger, chat-keyed admin
    notes and the subscriber row. The caller wraps it in one transaction."""
    sub = get_subscriber(conn, chat_id)
    n = _delete_chat_notes(conn, chat_id)
    if sub is not None:
        conn.execute("DELETE FROM alerts_sent WHERE subscriber_id = ?", (sub.id,))
        conn.execute("DELETE FROM subscriptions WHERE subscriber_id = ?", (sub.id,))
        n += conn.execute("DELETE FROM subscribers WHERE id = ?", (sub.id,)).rowcount
    return n > 0


def revoke(conn: sqlite3.Connection, chat_id: int, now: datetime) -> bool:
    """Admin: drop the subscriptions and ledger, clear the name, block. The row stays so the bot
    keeps ignoring that chat."""
    sub = get_subscriber(conn, chat_id)
    if sub is None:
        return False
    conn.execute("DELETE FROM alerts_sent WHERE subscriber_id = ?", (sub.id,))
    conn.execute("DELETE FROM subscriptions WHERE subscriber_id = ?", (sub.id,))
    decide(conn, chat_id, False, now)
    return True


def sync_allowlist(conn: sqlite3.Connection, allowed: frozenset[int]) -> int:
    """Approve existing rows of allowlisted chats, so alert runs need only the DB status."""
    if not allowed:
        return 0
    ids = sorted(allowed)
    marks = ",".join("?" * len(ids))
    return conn.execute(
        f"UPDATE subscribers SET status = 'approved' WHERE chat_id IN ({marks})"
        " AND status != 'approved'",
        ids,
    ).rowcount
