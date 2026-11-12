"""The subscription bot: commands, allowlist, callbacks and polling (Phase 3 spec 5.7)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta

import httpx
import pytest

from outages import db
from outages.alerts import store
from outages.areas.gazetteer import load as load_gazetteer
from outages.bot import Reply, deliver, handle, poll_forever, poll_once
from outages.telegram import TelegramClient
from outages.timeparse import MANILA

GAZ = load_gazetteer()
ME, STRANGER = 42, 666
ALLOWED = frozenset({ME})
NOW = datetime(2026, 11, 13, 6, 0, tzinfo=MANILA)
LAHUG = "730600041"
TOKEN = "123456:SECRET-abcdefghijklmnop"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


_uid = iter(range(1, 10_000))


def msg(text: str, chat_id: int = ME, chat_type: str = "private") -> dict:
    return {
        "update_id": next(_uid),
        "message": {
            "message_id": 1,
            "from": {"id": chat_id, "first_name": "Me"},
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def cb(data: str, chat_id: int = ME, message_id: int = 77) -> dict:
    return {
        "update_id": next(_uid),
        "callback_query": {
            "id": "cb1",
            "from": {"id": chat_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
            "data": data,
        },
    }


def say(conn, update, now=NOW) -> list[Reply]:
    return handle(update, conn, GAZ, ALLOWED, now)


def texts(replies: list[Reply]) -> str:
    return "\n".join(r.text for r in replies)


def subs(conn, chat_id=ME) -> list[str]:
    s = store.get_subscriber(conn, chat_id)
    return store.subscriptions(conn, s.id) if s else []


def test_stranger_without_start_gets_one_hint(conn, caplog):
    """Phase 5: strangers may /start to ask to join (tests/test_bot_join.py); anything else
    gets one hint, then silence."""
    caplog.set_level(logging.INFO, logger="outages")
    first = say(conn, msg("/add Lahug", STRANGER))
    second = say(conn, msg("/list", STRANGER))
    assert [r.text for r in first] == ["Send /start to ask to join."] and second == []
    assert store.get_subscriber(conn, STRANGER) is None
    lines = [r.getMessage() for r in caplog.records if "unknown chat" in r.getMessage()]
    assert lines == [f"bot: message from unknown chat {STRANGER}"]


def test_start_creates_subscriber(conn):
    replies = say(conn, msg("/start"))
    assert store.get_subscriber(conn, ME).active
    assert "/add" in texts(replies) and "/list" in texts(replies)


def test_start_reactivates(conn):
    s = store.upsert_subscriber(conn, ME, None, NOW)
    store.set_active(conn, s.id, False)
    conn.commit()
    say(conn, msg("/start"))
    assert store.get_subscriber(conn, ME).active


def _outage_in_lahug(conn, start):
    from outages.areas import store as areas_store
    from outages.areas.entities_rules import RulesExtractor

    raw = "Portion of Cebu City (Lahug) along Gorordo Ave."
    h = areas_store.areas_hash(raw)
    conn.execute("INSERT INTO posts (id, url, slug, first_seen_at) VALUES (1, 'u', 's', 't')")
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    conn.execute(
        "INSERT INTO outages (post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id, areas_hash)"
        " VALUES (1, 0, 'h', ?, ?, 'scheduled', 'Maint', ?, 'k', 1, 1, ?)",
        (start.isoformat(), (start + timedelta(hours=4)).isoformat(), raw, h),
    )
    areas_store.write_parse(conn, h, areas_store.parse_one(raw, GAZ, RulesExtractor()), "r", NOW)
    conn.commit()


def test_add_single_match_subscribes_and_lists_upcoming(conn):
    _outage_in_lahug(conn, NOW + timedelta(days=1, hours=3))
    replies = say(conn, msg("/add lahug"))
    assert subs(conn) == [LAHUG]
    t = texts(replies)
    assert "Subscribed to Lahug (Cebu City)" in t and "Sat 14 Nov, 9:00 AM–1:00 PM" in t
    assert conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0] == 0  # info only


def test_add_already_subscribed(conn):
    say(conn, msg("/add Lahug"))
    assert "already" in texts(say(conn, msg("/add Lahug")))


def test_add_two_towns_gives_buttons(conn):
    (r,) = say(conn, msg("/add San Roque"))
    buttons = [b for row in r.markup["inline_keyboard"] for b in row]
    towns = {b["text"].split("(")[1].rstrip(")") for b in buttons}
    assert len(buttons) >= 2 and len(towns) == len(buttons)
    assert all(b["callback_data"].startswith("add:") for b in buttons)
    assert subs(conn) == []


def test_callback_add_subscribes_and_answers(conn):
    replies = say(conn, cb("add:702219018"))
    assert subs(conn) == ["702219018"]
    assert any(r.callback_id == "cb1" for r in replies)
    assert any(r.edit_message_id == 77 and r.markup is None for r in replies)
    assert "Subscribed to Tayud (Consolacion)" in texts(replies)


def test_callback_unknown_code_or_stranger(conn):
    replies = say(conn, cb("add:999999999"))
    assert subs(conn) == [] and any(r.callback_id == "cb1" for r in replies)
    assert say(conn, cb("add:702219018", STRANGER)) == []
    assert store.get_subscriber(conn, STRANGER) is None


def test_add_unknown_suggests(conn):
    t = texts(say(conn, msg("/add Lahugg")))
    assert "No barangay named" in t and "Lahug (Cebu City)" in t


def test_add_without_name_shows_usage(conn):
    assert "Usage: /add" in texts(say(conn, msg("/add")))
    assert "Usage: /add" in texts(say(conn, msg("/add@MyBot   ")))


def test_list(conn):
    assert "No subscriptions" in texts(say(conn, msg("/list")))
    say(conn, msg("/add Lahug"))
    say(conn, cb("add:702219018"))
    t = texts(say(conn, msg("/list")))
    assert "Lahug (Cebu City)" in t and "Tayud (Consolacion)" in t


def test_remove_buttons_and_callback(conn):
    assert "No subscriptions" in texts(say(conn, msg("/remove")))
    say(conn, msg("/add Lahug"))
    (r,) = say(conn, msg("/remove"))
    (button,) = [b for row in r.markup["inline_keyboard"] for b in row]
    assert button["callback_data"] == f"rm:{LAHUG}"
    replies = say(conn, cb(button["callback_data"]))
    assert subs(conn) == [] and "Removed Lahug (Cebu City)" in texts(replies)


def test_unknown_command_and_text_show_help(conn):
    assert "/add" in texts(say(conn, msg("/frobnicate")))
    assert "/add" in texts(say(conn, msg("hello")))


# --- delivery and polling over MockTransport ---------------------------------------------------


def mock_client(updates: list[dict] | Exception, seen: list):
    def handler(req):
        method = req.url.path.rsplit("/", 1)[1]
        seen.append((method, json.loads(req.content)))
        if method == "getUpdates":
            if isinstance(updates, Exception):
                raise updates
            return httpx.Response(200, json={"ok": True, "result": updates})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return TelegramClient(TOKEN, transport=httpx.MockTransport(handler))


def test_deliver_routes_reply_kinds():
    seen: list = []
    c = mock_client([], seen)
    deliver(
        c,
        [
            Reply(ME, "hi", markup={"inline_keyboard": []}),
            Reply(ME, "Added", callback_id="cb1"),
            Reply(ME, "", edit_message_id=77),
        ],
    )
    assert [m for m, _ in seen] == ["sendMessage", "answerCallbackQuery", "editMessageReplyMarkup"]
    assert seen[0][1]["parse_mode"] == "HTML"


def test_poll_once_handles_and_stores_offset(conn):
    seen: list = []
    c = mock_client([msg("/start") | {"update_id": 10}], seen)
    assert poll_once(c, conn, GAZ, ALLOWED, NOW) == 1
    assert db.get_meta(conn, "telegram_update_offset") == "11"
    assert ("sendMessage", ME) in [(m, b.get("chat_id")) for m, b in seen]
    poll_once(c, conn, GAZ, ALLOWED, NOW)
    assert [b.get("offset") for m, b in seen if m == "getUpdates"] == [None, 11]


def test_unhandled_updates_ignored_offset_advances(conn):
    updates = [
        {
            "update_id": 20,
            "message": {
                "message_id": 1,
                "chat": {"id": ME, "type": "private"},
                "from": {"id": ME},
                "sticker": {"file_id": "x"},
            },
        },
        {"update_id": 21, "edited_message": {"message_id": 1, "chat": {"id": ME}, "text": "/x"}},
        msg("/start", -100123, "group") | {"update_id": 22},
        {"update_id": 23, "my_chat_member": {"chat": {"id": ME}}},
        {
            "update_id": 24,
            "message": {
                "message_id": 2,
                "chat": {"id": ME, "type": "private"},
                "from": {"id": ME},
                "photo": [{}],
            },
        },
    ]
    seen: list = []
    assert poll_once(mock_client(updates, seen), conn, GAZ, ALLOWED, NOW) == 5
    # Phase 5: a group chat gets one "private chats only"; the rest stay unanswered
    assert [(m, b.get("chat_id")) for m, b in seen] == [
        ("getUpdates", None),
        ("sendMessage", -100123),
    ]
    assert db.get_meta(conn, "telegram_update_offset") == "25"


def test_handler_error_skips_update(conn, monkeypatch):
    from outages import bot

    def boom(*a, **k):
        raise RuntimeError("bad update")

    monkeypatch.setattr(bot, "handle", boom)
    seen: list = []
    assert (
        poll_once(mock_client([msg("/start") | {"update_id": 30}], seen), conn, GAZ, ALLOWED, NOW)
        == 1
    )
    assert db.get_meta(conn, "telegram_update_offset") == "31"


def test_poll_forever_backs_off_and_stops(tmp_path, caplog):
    db.connect(tmp_path / "o.db").close()
    stop = threading.Event()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 3:
            stop.set()

    seen: list = []
    c = mock_client(httpx.ConnectError("down"), seen)
    poll_forever(c, tmp_path / "o.db", GAZ, ALLOWED, stop, sleep=sleep)
    assert slept == [5, 10, 20]
    assert "SECRET" not in caplog.text


def test_poll_forever_caps_backoff(tmp_path):
    db.connect(tmp_path / "o.db").close()
    stop = threading.Event()
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        if len(slept) == 6:
            stop.set()

    poll_forever(
        mock_client(httpx.ConnectError("down"), []),
        tmp_path / "o.db",
        GAZ,
        ALLOWED,
        stop,
        sleep=sleep,
    )
    assert slept == [5, 10, 20, 40, 60, 60]


def test_poll_forever_resets_after_good_poll(tmp_path):
    db.connect(tmp_path / "o.db").close()
    stop = threading.Event()
    slept: list[float] = []
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise httpx.ConnectError("down")
        if calls["n"] >= 4:
            stop.set()
        return httpx.Response(200, json={"ok": True, "result": []})

    c = TelegramClient(TOKEN, transport=httpx.MockTransport(handler))
    poll_forever(c, tmp_path / "o.db", GAZ, ALLOWED, stop, sleep=slept.append)
    assert slept == [5, 5]
