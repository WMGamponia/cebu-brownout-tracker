"""The subscription bot (Phase 3 spec 5.7): `/start`, `/add`, `/list`, `/remove`, allowlisted.

`handle` turns one Telegram update into replies (its only side effects are DB writes, each
committed); `poll_once` / `poll_forever` long-poll `getUpdates` and keep the offset in `meta`.
Updates the bot doesn't handle (stickers, edits, groups, membership changes) are skipped, and
the offset still moves past them.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from outages import db
from outages.alerts import store
from outages.alerts.render import e, render_upcoming
from outages.alerts.select import load
from outages.areas.gazetteer import Gazetteer, Place
from outages.telegram import TelegramClient, TelegramError

log = logging.getLogger("outages.bot")

OFFSET_KEY = "telegram_update_offset"
PRIVATE = "This is a private bot."
HELP = (
    "Alerts for VECO's scheduled power interruptions in your barangay, the evening before "
    "(~8 PM) and the morning of (~6 AM), and when VECO changes an outage.\n\n"
    "/add &lt;barangay&gt; — subscribe, e.g. /add Lahug\n"
    "/list — your barangays\n"
    "/remove — unsubscribe"
)
USAGE = "Usage: /add &lt;barangay&gt;, e.g. /add Lahug"
BACKOFF_START_S, BACKOFF_MAX_S = 5, 60


@dataclass(frozen=True)
class Reply:
    """A message to send; with `callback_id`, a callback answer (toast) instead; with
    `edit_message_id`, an edit of that message's inline keyboard to `markup`."""

    chat_id: int
    text: str
    markup: dict | None = None
    callback_id: str | None = None
    edit_message_id: int | None = None


def _label(p: Place) -> str:
    return f"{p.name} ({p.town})"


def _buttons(prefix: str, places: list[Place]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": _label(p), "callback_data": f"{prefix}:{p.code}"}] for p in places
        ]
    }


def _subscribe(conn, gaz, chat_id: int, place: Place, now: datetime, name: str | None) -> str:
    with conn:
        sub = store.upsert_subscriber(conn, chat_id, name, now)
        added = store.subscribe(conn, sub.id, place.code, now)
    if not added:
        return f"You're already subscribed to {e(_label(place))}."
    upcoming = render_upcoming(place.code, load(conn, now, gaz), gaz, now)
    return f"Subscribed to {e(_label(place))}.\n\n{upcoming}"


def _command(text: str) -> tuple[str, str]:
    head, _, rest = text.strip().partition(" ")
    return head.split("@", 1)[0].lower(), rest.strip()


def _on_message(m: dict, conn, gaz: Gazetteer, now: datetime) -> list[Reply]:
    chat_id = m["chat"]["id"]
    name = (m.get("from") or {}).get("first_name")
    cmd, arg = _command(m["text"])
    if cmd == "/start":
        with conn:
            store.upsert_subscriber(conn, chat_id, name, now)
        return [Reply(chat_id, HELP)]
    if cmd == "/add":
        if not arg:
            return [Reply(chat_id, USAGE)]
        places = gaz.search(arg)
        if len(places) == 1:
            return [Reply(chat_id, _subscribe(conn, gaz, chat_id, places[0], now, name))]
        if places:
            return [Reply(chat_id, f"Which {e(arg)}?", markup=_buttons("add", places))]
        hints = gaz.suggest(arg)
        tail = f" Did you mean: {e(', '.join(hints))}?" if hints else ""
        return [Reply(chat_id, f"No barangay named {e(arg)} in VECO's area.{tail}")]
    sub = store.get_subscriber(conn, chat_id)
    codes = store.subscriptions(conn, sub.id) if sub else []
    places = [p for p in (gaz.place(c) for c in codes) if p]
    if cmd == "/list":
        if not places:
            return [Reply(chat_id, "No subscriptions yet. " + USAGE.replace("Usage: ", "Use "))]
        return [Reply(chat_id, "Your barangays:\n" + "\n".join(e(_label(p)) for p in places))]
    if cmd == "/remove":
        if not places:
            return [Reply(chat_id, "No subscriptions to remove.")]
        return [Reply(chat_id, "Remove which barangay?", markup=_buttons("rm", places))]
    return [Reply(chat_id, HELP)]


def _on_callback(q: dict, conn, gaz: Gazetteer, now: datetime) -> list[Reply]:
    chat_id = q["message"]["chat"]["id"]
    action, _, code = (q.get("data") or "").partition(":")
    place = gaz.place(code)
    done = [Reply(chat_id, "", edit_message_id=q["message"]["message_id"])]
    if place is None or action not in ("add", "rm"):
        return [Reply(chat_id, "Unknown choice.", callback_id=q["id"]), *done]
    if action == "add":
        name = (q.get("from") or {}).get("first_name")
        text = _subscribe(conn, gaz, chat_id, place, now, name)
        return [Reply(chat_id, "Added", callback_id=q["id"]), *done, Reply(chat_id, text)]
    sub = store.get_subscriber(conn, chat_id)
    with conn:
        removed = sub is not None and store.unsubscribe(conn, sub.id, place.code)
    text = f"Removed {e(_label(place))}." if removed else f"Not subscribed to {e(_label(place))}."
    return [Reply(chat_id, "Removed", callback_id=q["id"]), *done, Reply(chat_id, text)]


def _stranger(conn, chat_id: int, now: datetime) -> list[Reply]:
    key = f"stranger:{chat_id}"
    if store.note_sent(conn, key):
        return []
    with conn:
        store.mark_note(conn, key, now)
    log.info("bot: message from chat %s (not allowed)", chat_id)
    return [Reply(chat_id, PRIVATE)]


def handle(
    update: dict, conn: sqlite3.Connection, gaz: Gazetteer, allowed: frozenset[int], now: datetime
) -> list[Reply]:
    """Replies to one update; [] for anything the bot doesn't handle."""
    if "callback_query" in update:
        q = update["callback_query"]
        msg = q.get("message") or {}
        if (msg.get("chat") or {}).get("type") != "private" or "id" not in q:
            return []
        if msg["chat"]["id"] not in allowed:
            return []
        return _on_callback(q, conn, gaz, now)
    m = update.get("message")
    if not m or (m.get("chat") or {}).get("type") != "private":
        return []
    chat_id = m["chat"]["id"]
    if chat_id not in allowed:
        return _stranger(conn, chat_id, now)
    if not isinstance(m.get("text"), str):
        return []
    return _on_message(m, conn, gaz, now)


def deliver(client: TelegramClient, replies: list[Reply]) -> None:
    for r in replies:
        if r.callback_id is not None:
            client.answer_callback_query(r.callback_id, r.text or None)
        elif r.edit_message_id is not None:
            try:
                client.edit_message_reply_markup(r.chat_id, r.edit_message_id, r.markup)
            except TelegramError as err:  # e.g. "message is not modified"; harmless
                log.info("bot: keyboard edit skipped (%s)", err.status)
        else:
            client.send_message(r.chat_id, r.text, reply_markup=r.markup)


def poll_once(
    client: TelegramClient,
    conn: sqlite3.Connection,
    gaz: Gazetteer,
    allowed: frozenset[int],
    now: datetime,
) -> int:
    """One `getUpdates` long poll; handle each update, then store the next offset."""
    raw = db.get_meta(conn, OFFSET_KEY)
    updates = client.get_updates(int(raw) if raw else None)
    for u in updates:
        try:
            deliver(client, handle(u, conn, gaz, allowed, now))
        except Exception:
            log.exception("bot: update %s failed; skipped", u.get("update_id"))
    if updates:
        with conn:
            db.set_meta(conn, OFFSET_KEY, str(max(u["update_id"] for u in updates) + 1))
    return len(updates)


def poll_forever(
    client: TelegramClient,
    db_path: Path,
    gaz: Gazetteer,
    allowed: frozenset[int],
    stop: threading.Event,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Long-poll until `stop` is set, on this thread's own connection. Errors are logged and
    backed off 5 s doubling to 60 s, reset after a good poll; they never end the loop."""
    conn = db.connect(db_path)
    delay = BACKOFF_START_S
    try:
        while not stop.is_set():
            try:
                poll_once(client, conn, gaz, allowed, datetime.now(UTC))
                delay = BACKOFF_START_S
            except TelegramError as err:
                log.warning("bot: poll failed (%s); retrying in %d s", err, delay)
                sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
            except Exception:
                log.exception("bot: poll failed; retrying in %d s", delay)
                sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)
    finally:
        conn.close()
