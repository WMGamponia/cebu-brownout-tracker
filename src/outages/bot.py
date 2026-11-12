"""The subscription bot (Phase 3 spec 5.7; Phase 5 spec 4.9: approve to join).

`handle` turns one Telegram update into replies (its only side effects are DB writes, each
committed); `poll_once` / `poll_forever` long-poll `getUpdates` and keep the offset in `meta`.
Updates the bot doesn't handle (stickers, edits, membership changes) are skipped, and the offset
still moves past them.

Who may do what: chats in `TELEGRAM_ALLOWED_CHAT_IDS` are always approved. Anyone else sends
/start and becomes `pending`; the admin chat approves (`approved`) or denies (`blocked`) with a
button. Pending chats get "Waiting for approval" at most once a day; blocked chats get silence.
Group chats get one refusal. Logs carry chat IDs, never message text.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from outages import db
from outages.alerts import store
from outages.alerts.render import e, render_upcoming
from outages.alerts.select import load
from outages.areas.gazetteer import Gazetteer, Place
from outages.config import (
    PENDING_CAP,
    RATE_LIMIT_MUTE_S,
    RATE_LIMIT_PER_MIN,
    SEND_SPACING_S,
    SUBSCRIPTION_CAP,
)
from outages.telegram import TelegramClient, TelegramError
from outages.timeparse import MANILA

log = logging.getLogger("outages.bot")

OFFSET_KEY = "telegram_update_offset"
HELP = (
    "Alerts for VECO's scheduled power interruptions in your barangay, the evening before "
    "(~8 PM) and the morning of (~6 AM), and when VECO changes an outage.\n\n"
    "/add &lt;barangay&gt; — subscribe, e.g. /add Lahug\n"
    "/list — your barangays\n"
    "/remove — unsubscribe\n"
    "/privacy — what this bot stores\n"
    "/forget — delete everything about you"
)
USAGE = "Usage: /add &lt;barangay&gt;, e.g. /add Lahug"
WELCOME = (
    "Hi! This bot sends alerts for VECO's scheduled power interruptions in the barangays you "
    "pick.\n\n"
    "It runs on one person's computer, so alerts can be late or missed: always check VECO's "
    "own posts too.\n\n"
    "It stores your chat ID, your first name and the barangays you add, only to send these "
    "alerts. /privacy has the details; /forget deletes everything about you.\n\n"
    "Your request has gone to the admin. You'll get a message here when it's approved."
)
PRIVACY = (
    "What this bot stores: your Telegram chat ID, your first name, the barangays you add, and "
    "which alerts it has sent you (so it doesn't send them twice).\n\n"
    "Why: only to send you VECO's scheduled interruptions for those barangays. Nothing is "
    "shared or sold. It runs on one person's computer.\n\n"
    "How to delete: send /forget. It deletes your chat ID, name, barangays and alert history "
    "at once. If a request was declined, the chat ID is kept so the bot stays silent."
)
APPROVED = "You're in. Try /add &lt;barangay&gt;."
DENIED = "Not approved."
PENDING = "Waiting for approval"
CAPPED = "Not taking new requests right now"
GROUPS = "This bot works in private chats only."
FORGOTTEN = "Deleted. /start to ask again."
HINT = "Send /start to ask to join."
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


class RateLimiter:
    """More than `per_min` updates in a minute from one chat → that chat is ignored for
    `mute_s` seconds (in memory; logged once per mute)."""

    def __init__(self, per_min: int = RATE_LIMIT_PER_MIN, mute_s: int = RATE_LIMIT_MUTE_S):
        self.per_min, self.mute_s = per_min, mute_s
        self._seen: dict[int, deque[datetime]] = {}
        self._muted: dict[int, datetime] = {}

    def allow(self, chat_id: int, now: datetime) -> bool:
        until = self._muted.get(chat_id)
        if until is not None:
            if now < until:
                return False
            del self._muted[chat_id]
        q = self._seen.setdefault(chat_id, deque())
        while q and now - q[0] >= timedelta(minutes=1):
            q.popleft()
        q.append(now)
        if len(q) > self.per_min:
            self._muted[chat_id] = now + timedelta(seconds=self.mute_s)
            q.clear()
            log.warning("bot: rate limit: chat %s muted for %d s", chat_id, self.mute_s)
            return False
        return True


def _label(p: Place) -> str:
    return f"{p.name} ({p.town})"


def _buttons(prefix: str, places: list[Place]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": _label(p), "callback_data": f"{prefix}:{p.code}"}] for p in places
        ]
    }


def _decision_buttons(chat_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Approve", "callback_data": f"ap:{chat_id}"},
                {"text": "Deny", "callback_data": f"dn:{chat_id}"},
            ]
        ]
    }


def _subscribe(conn, gaz, chat_id: int, place: Place, now: datetime, name: str | None) -> str:
    sub = store.get_subscriber(conn, chat_id)
    if (
        sub is not None
        and place.code not in store.subscriptions(conn, sub.id)
        and store.subscription_count(conn, sub.id) >= SUBSCRIPTION_CAP
    ):
        return (
            f"You have {SUBSCRIPTION_CAP} barangays, the most one person can have. "
            "Use /remove to drop one first."
        )
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


def _first_name(m: dict) -> str | None:
    return (m.get("from") or {}).get("first_name")


def _on_message(m: dict, conn, gaz: Gazetteer, now: datetime) -> list[Reply]:
    """An approved chat's commands (Phase 3)."""
    chat_id = m["chat"]["id"]
    name = _first_name(m)
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


def _once(conn, key: str, now: datetime) -> bool:
    """True the first time `key` is seen (and marks it)."""
    if store.note_sent(conn, key):
        return False
    with conn:
        store.mark_note(conn, key, now)
    return True


def _day(now: datetime) -> str:
    return now.astimezone(MANILA).date().isoformat()


# --- admin -------------------------------------------------------------------------------------


def _who(s: store.Subscriber) -> str:
    return e(s.display_name) if s.display_name else "—"


def _on_decision(q: dict, conn, now: datetime, admin_chat_id: int) -> list[Reply]:
    action, _, raw = (q.get("data") or "").partition(":")
    msg = q["message"]
    done = [Reply(admin_chat_id, "", edit_message_id=msg["message_id"])]
    try:
        target = int(raw)
    except ValueError:
        return [Reply(admin_chat_id, "Unknown choice.", callback_id=q["id"]), *done]
    before = store.get_subscriber(conn, target)
    if before is None:
        return [Reply(admin_chat_id, "No such request.", callback_id=q["id"]), *done]
    approve = action == "ap"
    with conn:
        after = store.decide(conn, target, approve, now)
    changed = after is not None and after.status != before.status
    toast = "Approved" if approve else "Denied"
    out = [Reply(admin_chat_id, toast, callback_id=q["id"]), *done]
    if changed:
        log.info("bot: chat %s %s", target, after.status)
        out.append(Reply(target, APPROVED if approve else DENIED))
    return out


def _on_admin(cmd: str, arg: str, conn, admin_chat_id: int, now: datetime) -> list[Reply]:
    if cmd == "/pending":
        subs = store.pending(conn)
        if not subs:
            return [Reply(admin_chat_id, "No pending requests.")]
        return [
            Reply(
                admin_chat_id,
                f"Pending: {_who(s)} (chat {s.chat_id})",
                markup=_decision_buttons(s.chat_id),
            )
            for s in subs
        ]
    if cmd == "/users":
        rows = store.users(conn)
        if not rows:
            return [Reply(admin_chat_id, "No users.")]
        lines = [
            f"{_who(s)} · {s.status} · {n} barangay{'s' if n != 1 else ''} · chat {s.chat_id}"
            for s, n in rows
        ]
        return [Reply(admin_chat_id, "\n".join(lines))]
    assert cmd == "/revoke"
    try:
        target = int(arg)
    except ValueError:
        return [Reply(admin_chat_id, "Usage: /revoke &lt;chat id&gt; (see /users)")]
    with conn:
        ok = store.revoke(conn, target, now)
    if ok:
        log.info("bot: chat %s revoked", target)
    return [Reply(admin_chat_id, f"Revoked chat {target}." if ok else "No such chat.")]


# --- strangers, pending, everyone -----------------------------------------------------------------


def _join(m: dict, conn, now: datetime, admin_chat_id: int | None) -> list[Reply]:
    chat_id = m["chat"]["id"]
    if admin_chat_id is None:
        log.warning(
            "bot: join request from chat %s refused: TELEGRAM_ADMIN_CHAT_ID not set", chat_id
        )
        return [Reply(chat_id, CAPPED)]
    if store.pending_count(conn) >= PENDING_CAP:
        out = [Reply(chat_id, CAPPED)]
        if _once(conn, f"pending_cap:{_day(now)}", now):
            log.warning("bot: %d join requests pending; refusing new ones", PENDING_CAP)
            out.append(
                Reply(
                    admin_chat_id,
                    f"{PENDING_CAP} join requests are waiting; new ones are refused until you "
                    "answer some (/pending).",
                )
            )
        return out
    frm = m.get("from") or {}
    name = frm.get("first_name")
    with conn:
        created = store.request_join(conn, chat_id, name, now)
    if not created:
        return []
    log.info("bot: join request from chat %s", chat_id)
    username = frm.get("username")
    handle_ = f"@{e(username)}" if username else "no username"
    request = f"Join request: {e(name or '?')} ({handle_}, chat {chat_id})"
    return [
        Reply(chat_id, WELCOME),
        Reply(admin_chat_id, request, markup=_decision_buttons(chat_id)),
    ]


def _forget(conn, chat_id: int) -> list[Reply]:
    with conn:
        store.forget(conn, chat_id)
    log.info("bot: chat %s forgotten", chat_id)
    return [Reply(chat_id, FORGOTTEN)]


def _status(conn, chat_id: int, allowed: frozenset[int]) -> str | None:
    """approved | pending | blocked, or None for a chat the bot doesn't know."""
    if chat_id in allowed:
        return "approved"
    sub = store.get_subscriber(conn, chat_id)
    return sub.status if sub else None


def handle(
    update: dict,
    conn: sqlite3.Connection,
    gaz: Gazetteer,
    allowed: frozenset[int],
    now: datetime,
    admin_chat_id: int | None = None,
    limiter: RateLimiter | None = None,
) -> list[Reply]:
    """Replies to one update; [] for anything the bot doesn't handle."""
    if "callback_query" in update:
        q = update["callback_query"]
        msg = q.get("message") or {}
        if (msg.get("chat") or {}).get("type") != "private" or "id" not in q:
            return []
        chat_id = msg["chat"]["id"]
        if limiter is not None and not limiter.allow(chat_id, now):
            return []
        data = q.get("data") or ""
        if data.startswith(("ap:", "dn:")):
            if admin_chat_id is None or chat_id != admin_chat_id:
                log.info("bot: decision button from chat %s ignored (not the admin chat)", chat_id)
                return []
            return _on_decision(q, conn, now, admin_chat_id)
        if _status(conn, chat_id, allowed) != "approved":
            return []
        return _on_callback(q, conn, gaz, now)
    m = update.get("message")
    if not m:
        return []
    chat = m.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return []
    if limiter is not None and not limiter.allow(chat_id, now):
        return []
    if chat.get("type") != "private":
        if chat.get("type") in ("group", "supergroup") and _once(conn, f"group:{chat_id}", now):
            log.info("bot: message from group chat %s refused", chat_id)
            return [Reply(chat_id, GROUPS)]
        return []
    if not isinstance(m.get("text"), str):
        return []
    cmd, arg = _command(m["text"])
    status = _status(conn, chat_id, allowed)
    if status == "blocked":
        return []
    if cmd == "/privacy":
        return [Reply(chat_id, PRIVACY)]
    if cmd == "/forget":  # any chat but a blocked one; never leaves a trace (live test fix)
        return _forget(conn, chat_id)
    if chat_id == admin_chat_id and cmd in ("/pending", "/users", "/revoke"):
        return _on_admin(cmd, arg, conn, admin_chat_id, now)
    if status == "approved":
        return _on_message(m, conn, gaz, now)
    if status == "pending":
        if _once(conn, f"pending_reply:{chat_id}:{_day(now)}", now):
            return [Reply(chat_id, PENDING)]
        return []
    if cmd == "/start":
        return _join(m, conn, now, admin_chat_id)
    if _once(conn, f"stranger:{chat_id}", now):
        log.info("bot: message from unknown chat %s", chat_id)
        return [Reply(chat_id, HINT)]
    return []


def deliver(
    client: TelegramClient, replies: list[Reply], sleep: Callable[[float], None] = time.sleep
) -> None:
    """Send in order, messages at least `SEND_SPACING_S` apart."""
    sent = False
    for r in replies:
        if r.callback_id is not None:
            client.answer_callback_query(r.callback_id, r.text or None)
        elif r.edit_message_id is not None:
            try:
                client.edit_message_reply_markup(r.chat_id, r.edit_message_id, r.markup)
            except TelegramError as err:  # e.g. "message is not modified"; harmless
                log.info("bot: keyboard edit skipped (%s)", err.status)
        else:
            if sent:
                sleep(SEND_SPACING_S)
            client.send_message(r.chat_id, r.text, reply_markup=r.markup)
            sent = True


def poll_once(
    client: TelegramClient,
    conn: sqlite3.Connection,
    gaz: Gazetteer,
    allowed: frozenset[int],
    now: datetime,
    admin_chat_id: int | None = None,
    limiter: RateLimiter | None = None,
) -> int:
    """One `getUpdates` long poll; handle each update, then store the next offset."""
    raw = db.get_meta(conn, OFFSET_KEY)
    updates = client.get_updates(int(raw) if raw else None)
    for u in updates:
        try:
            deliver(client, handle(u, conn, gaz, allowed, now, admin_chat_id, limiter))
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
    admin_chat_id: int | None = None,
) -> None:
    """Long-poll until `stop` is set, on this thread's own connection. Errors are logged and
    backed off 5 s doubling to 60 s, reset after a good poll; they never end the loop.

    On start, allowlisted chats' rows are marked approved, so alert runs read only the DB."""
    conn = db.connect(db_path)
    with conn:
        n = store.sync_allowlist(conn, allowed)
    if n:
        log.info("bot: %d allowlisted chat(s) marked approved", n)
    if admin_chat_id is None:
        log.warning("bot: TELEGRAM_ADMIN_CHAT_ID not set: join requests can't be approved")
    limiter = RateLimiter()
    delay = BACKOFF_START_S
    try:
        while not stop.is_set():
            try:
                poll_once(client, conn, gaz, allowed, datetime.now(UTC), admin_chat_id, limiter)
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
