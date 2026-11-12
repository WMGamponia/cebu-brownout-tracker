"""Approve-to-join, admin commands, /privacy and /forget (Phase 5 spec 4.9)."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

import pytest

from outages import db
from outages.alerts import store
from outages.areas.gazetteer import load as load_gazetteer
from outages.bot import RateLimiter, handle, poll_forever
from outages.timeparse import MANILA

GAZ = load_gazetteer()
ADMIN = 42  # the owner: allowlisted and admin
ALLOWED = frozenset({ADMIN})
ANA = 5550123
NOW = datetime(2026, 11, 13, 6, 0, tzinfo=MANILA)

_uid = iter(range(1, 100_000))


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


def msg(text, chat_id=ANA, chat_type="private", first="Ana", username="ana_c"):
    frm = {"id": chat_id, "first_name": first}
    if username:
        frm["username"] = username
    return {
        "update_id": next(_uid),
        "message": {
            "message_id": 1,
            "from": frm,
            "chat": {"id": chat_id, "type": chat_type},
            "text": text,
        },
    }


def cb(data, chat_id=ADMIN, message_id=77):
    return {
        "update_id": next(_uid),
        "callback_query": {
            "id": "cb1",
            "from": {"id": chat_id},
            "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
            "data": data,
        },
    }


class Bot:
    def __init__(self, conn, admin=ADMIN, now=NOW):
        self.conn, self.admin, self.now = conn, admin, now
        self.limiter = RateLimiter()

    def __call__(self, update, now=None):
        return handle(update, self.conn, GAZ, ALLOWED, now or self.now, self.admin, self.limiter)


def to(replies, chat_id):
    return [r for r in replies if r.chat_id == chat_id and r.callback_id is None and r.text]


def test_stranger_start_pending_and_admin_request(conn):
    bot = Bot(conn)
    replies = bot(msg("/start"))
    s = store.get_subscriber(conn, ANA)
    assert (s.status, s.display_name) == ("pending", "Ana")
    welcome = to(replies, ANA)[0].text
    for words in ("one person's computer", "late or missed", "chat ID", "/privacy", "/forget"):
        assert words in welcome
    req = to(replies, ADMIN)
    assert len(req) == 1
    assert req[0].text == f"Join request: Ana (@ana_c, chat {ANA})"
    buttons = [b["callback_data"] for row in req[0].markup["inline_keyboard"] for b in row]
    assert buttons == [f"ap:{ANA}", f"dn:{ANA}"]


def test_join_request_escapes_name(conn):
    replies = Bot(conn)(msg("/start", first="<b>Eve</b>", username=None))
    assert (
        to(replies, ADMIN)[0].text
        == f"Join request: &lt;b&gt;Eve&lt;/b&gt; (no username, chat {ANA})"
    )


def test_repeat_start_while_pending_sends_one_request(conn):
    bot = Bot(conn)
    admin_msgs = []
    for _ in range(5):
        admin_msgs += to(bot(msg("/start")), ADMIN)
    assert len(admin_msgs) == 1
    assert (
        conn.execute("SELECT count(*) FROM subscribers WHERE chat_id = ?", (ANA,)).fetchone()[0]
        == 1
    )


def test_approve_callback_from_admin_only(conn, caplog):
    bot = Bot(conn)
    bot(msg("/start"))
    caplog.set_level(logging.INFO, logger="outages")
    assert bot(cb(f"ap:{ANA}", chat_id=ANA)) == []  # the stranger approving themself
    assert store.get_subscriber(conn, ANA).status == "pending"
    assert "ignored" in caplog.text
    replies = bot(cb(f"ap:{ANA}"))
    assert store.get_subscriber(conn, ANA).status == "approved"
    assert [r.text for r in to(replies, ANA)] == ["You're in. Try /add &lt;barangay&gt;."]
    assert any(r.callback_id == "cb1" for r in replies)
    assert any(r.edit_message_id == 77 for r in replies)
    again = bot(cb(f"ap:{ANA}"))  # a second tap: no second message to the user
    assert to(again, ANA) == []
    assert "Lahug" in " ".join(r.text for r in to(bot(msg("/add Lahug")), ANA))


def test_deny_blocks_and_replies_once(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    replies = bot(cb(f"dn:{ANA}"))
    s = store.get_subscriber(conn, ANA)
    assert (s.status, s.display_name) == ("blocked", None)
    assert [r.text for r in to(replies, ANA)] == ["Not approved."]
    assert to(bot(cb(f"dn:{ANA}")), ANA) == []
    assert bot(msg("/start")) == [] and bot(msg("/add Lahug")) == []  # blocked: silence
    assert bot(msg("/forget")) == []


def test_pending_reply_once_per_day(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    first = bot(msg("/add Lahug"))
    assert [r.text for r in first] == ["Waiting for approval"]
    assert bot(msg("hello")) == []
    later = bot(msg("hello"), now=NOW + timedelta(days=1))
    assert [r.text for r in later] == ["Waiting for approval"]
    assert store.subscription_count(conn, store.get_subscriber(conn, ANA).id) == 0


def test_pending_cap(conn):
    bot = Bot(conn)
    for i in range(20):
        store.request_join(conn, 1000 + i, "x", NOW)
    conn.commit()
    replies = bot(msg("/start"))
    assert [r.text for r in to(replies, ANA)] == ["Not taking new requests right now"]
    assert store.get_subscriber(conn, ANA) is None
    notes = to(replies, ADMIN)
    assert len(notes) == 1 and "20" in notes[0].text
    replies = bot(msg("/start", chat_id=777, first="Bo"))
    assert to(replies, ADMIN) == []  # the admin note once per day


def test_subscription_cap(conn):
    bot = Bot(conn)
    s = store.upsert_subscriber(conn, ADMIN, "Me", NOW)
    names = ("Tisa", "Mabolo", "Kasambagan", "Apas", "Talamban", "Guadalupe", "Labangon")
    names += ("Pardo", "Lorega", "Kamagayan")
    codes = [GAZ.search(n)[0].code for n in names]
    assert len(set(codes)) == 10
    for c in codes:
        store.subscribe(conn, s.id, c, NOW)
    conn.commit()
    replies = bot(msg("/add Lahug", chat_id=ADMIN))
    text = " ".join(r.text for r in replies)
    assert "10" in text and "/remove" in text
    assert store.subscription_count(conn, s.id) == 10
    # re-adding one you have is still answered, not refused
    again = " ".join(r.text for r in bot(msg("/add Tisa", chat_id=ADMIN)))
    assert "already subscribed" in again


def test_rate_limit_mutes_ten_minutes(conn, caplog):
    caplog.set_level(logging.INFO, logger="outages")
    bot = Bot(conn)
    s = store.upsert_subscriber(conn, ADMIN, "Me", NOW)
    conn.commit()
    got = [bot(msg("/list", chat_id=ADMIN), now=NOW + timedelta(seconds=i)) for i in range(21)]
    assert all(got[:20]) and got[20] == []
    assert bot(msg("/list", chat_id=ADMIN), now=NOW + timedelta(minutes=9)) == []
    assert bot(msg("/list", chat_id=ADMIN), now=NOW + timedelta(minutes=11))
    muted = [r for r in caplog.records if "rate limit" in r.getMessage()]
    assert len(muted) == 1 and str(ADMIN) in muted[0].getMessage()
    assert s


def test_rate_limiter_window():
    lim = RateLimiter(per_min=2, mute_s=60)
    assert lim.allow(1, NOW) and lim.allow(1, NOW + timedelta(seconds=30))
    assert lim.allow(1, NOW + timedelta(seconds=61))  # the first has left the window
    assert lim.allow(2, NOW)  # per chat


def test_group_chat_refused_once(conn):
    bot = Bot(conn)
    first = bot(msg("/start", chat_id=-100123, chat_type="group"))
    assert [r.text for r in first] == ["This bot works in private chats only."]
    assert bot(msg("/add Lahug", chat_id=-100123, chat_type="supergroup")) == []
    assert store.get_subscriber(conn, -100123) is None


def test_privacy_text(conn):
    bot = Bot(conn)
    for chat in (ANA, ADMIN):
        text = " ".join(r.text for r in bot(msg("/privacy", chat_id=chat)))
        for words in ("chat ID", "first name", "barangays", "/forget"):
            assert words in text
    assert store.get_subscriber(conn, ANA) is None  # /privacy creates nothing


def test_forget_command(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    bot(cb(f"ap:{ANA}"))
    bot(msg("/add Lahug"))
    replies = bot(msg("/forget"))
    assert [r.text for r in replies] == ["Deleted. /start to ask again."]
    assert store.get_subscriber(conn, ANA) is None
    rows = [
        (t, v)
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for row in conn.execute(f"SELECT * FROM {t}")
        for v in row
        if v == ANA or (isinstance(v, str) and str(ANA) in v)
    ]
    assert rows == []
    assert to(bot(msg("/start")), ADMIN)  # may ask again: a new request
    # the allowlisted owner can forget too
    store.upsert_subscriber(conn, ADMIN, "Me", NOW)
    conn.commit()
    assert [r.text for r in bot(msg("/forget", chat_id=ADMIN))] == ["Deleted. /start to ask again."]
    assert store.get_subscriber(conn, ADMIN) is None


def test_forget_while_pending(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    assert [r.text for r in bot(msg("/forget"))] == ["Deleted. /start to ask again."]
    assert store.get_subscriber(conn, ANA) is None


def test_admin_users_lists_no_barangays(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    bot(cb(f"ap:{ANA}"))
    bot(msg("/add Lahug"))
    text = " ".join(r.text for r in bot(msg("/users", chat_id=ADMIN)))
    assert "Ana" in text and "approved" in text and "1 barangay" in text and str(ANA) in text
    assert "Lahug" not in text


def test_admin_pending_lists_with_buttons(conn):
    bot = Bot(conn)
    assert [r.text for r in bot(msg("/pending", chat_id=ADMIN))] == ["No pending requests."]
    bot(msg("/start"))
    bot(msg("/start", chat_id=777, first="Bo", username=None))
    replies = bot(msg("/pending", chat_id=ADMIN))
    assert len(replies) == 2
    assert [b["callback_data"] for b in replies[1].markup["inline_keyboard"][0]] == [
        "ap:777",
        "dn:777",
    ]


def test_admin_commands_ignored_outside_admin_chat(conn):
    bot = Bot(conn, admin=999)  # the owner is allowlisted but not the admin chat here
    bot(msg("/start"))
    for cmd in ("/users", "/pending", f"/revoke {ANA}"):
        text = " ".join(r.text for r in bot(msg(cmd, chat_id=ADMIN)))
        assert "/add" in text  # plain help, no admin output
    assert store.get_subscriber(conn, ANA).status == "pending"


def test_admin_revoke(conn):
    bot = Bot(conn)
    bot(msg("/start"))
    bot(cb(f"ap:{ANA}"))
    bot(msg("/add Lahug"))
    replies = bot(msg(f"/revoke {ANA}", chat_id=ADMIN))
    assert [r.text for r in replies] == [f"Revoked chat {ANA}."]
    s = store.get_subscriber(conn, ANA)
    assert (s.status, s.display_name) == ("blocked", None)
    assert store.subscription_count(conn, s.id) == 0
    assert [r.text for r in bot(msg("/revoke 1", chat_id=ADMIN))] == ["No such chat."]
    assert "Usage" in bot(msg("/revoke abc", chat_id=ADMIN))[0].text


def test_no_admin_chat_refuses_requests(conn, caplog):
    caplog.set_level(logging.WARNING, logger="outages")
    bot = Bot(conn, admin=None)
    replies = bot(msg("/start"))
    assert [r.text for r in replies] == ["Not taking new requests right now"]
    assert store.get_subscriber(conn, ANA) is None
    assert "TELEGRAM_ADMIN_CHAT_ID" in caplog.text


def test_allowlisted_chat_works_without_approval(conn):
    bot = Bot(conn)
    assert "/add" in " ".join(r.text for r in bot(msg("/start", chat_id=ADMIN)))
    assert store.get_subscriber(conn, ADMIN).status == "approved"
    assert "Lahug" in " ".join(r.text for r in bot(msg("/add Lahug", chat_id=ADMIN)))


def test_unknown_chat_other_text_gets_one_hint(conn):
    bot = Bot(conn)
    assert [r.text for r in bot(msg("/add Lahug"))] == ["Send /start to ask to join."]
    assert bot(msg("hello")) == []
    assert store.get_subscriber(conn, ANA) is None


def test_logs_have_chat_ids_not_text(conn, caplog):
    caplog.set_level(logging.DEBUG, logger="outages")
    Bot(conn)(msg("/start secret words here"))
    assert "secret words" not in caplog.text


def test_poll_forever_syncs_allowlist(tmp_path):
    p = tmp_path / "o.db"
    c = db.connect(p)
    store.request_join(c, ADMIN, "Me", NOW)
    c.commit()
    c.close()
    stop = threading.Event()
    stop.set()
    poll_forever(None, p, GAZ, ALLOWED, stop, admin_chat_id=ADMIN)
    c = db.connect(p)
    assert store.get_subscriber(c, ADMIN).status == "approved"
    c.close()


def _cells_with(conn, chat_id):
    return [
        (t, v)
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for row in conn.execute(f"SELECT * FROM {t}")
        for v in row
        if v == chat_id or (isinstance(v, str) and str(chat_id) in v)
    ]


def test_second_forget_leaves_no_trace(conn):
    """Live test, 2026-11-12: a second /forget right after the first was treated as a
    stranger's message and stored stranger:<chat_id>."""
    bot = Bot(conn)
    bot(msg("/start"))
    bot(cb(f"ap:{ANA}"))
    assert [r.text for r in bot(msg("/forget"))] == ["Deleted. /start to ask again."]
    assert [r.text for r in bot(msg("/forget"))] == ["Deleted. /start to ask again."]
    assert _cells_with(conn, ANA) == []


def test_forget_from_unknown_chat_clears_its_notes(conn):
    bot = Bot(conn)
    bot(msg("hello"))  # the one hint stores stranger:<id>
    assert _cells_with(conn, ANA)
    assert [r.text for r in bot(msg("/forget"))] == ["Deleted. /start to ask again."]
    assert _cells_with(conn, ANA) == []


def test_join_request_reaches_admin_when_welcome_fails(conn):
    """Review #2: the user blocked the bot (or the network dropped) right after /start."""
    from outages.bot import deliver
    from outages.telegram import TelegramError

    class Client:
        def __init__(self):
            self.sent = []

        def send_message(self, chat_id, text, reply_markup=None):
            if chat_id == ANA:
                raise TelegramError(403, "Forbidden: bot was blocked by the user")
            self.sent.append(chat_id)

    replies = Bot(conn)(msg("/start"))
    assert replies[0].chat_id == ADMIN  # the admin's request goes first
    c = Client()
    deliver(c, replies, sleep=lambda s: None)
    assert c.sent == [ADMIN]
