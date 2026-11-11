"""Notifiers: Telegram error mapping and the dry-run printer (Phase 3 spec 5.3)."""

from __future__ import annotations

import io
import json

import httpx
import pytest

from outages.notify import PrintNotifier, SendError, TelegramNotifier
from outages.telegram import TelegramClient

TOKEN = "123456:SECRET-abcdefghijklmnop"


def notifier(responses):
    """A TelegramNotifier whose transport replays `responses` (Response or Exception)."""
    seen: list[dict] = []
    slept: list[float] = []
    queue = list(responses)

    def handler(req):
        seen.append(json.loads(req.content))
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(handler))
    return TelegramNotifier(client, sleep=slept.append), seen, slept


def ok():
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def err(status, desc="x", retry_after=None):
    body = {"ok": False, "description": desc}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return httpx.Response(status, json=body)


def test_ok_returns_html():
    n, seen, _ = notifier([ok()])
    assert n.send(42, "<b>a</b>") == "html"
    assert seen[0]["parse_mode"] == "HTML"


def test_notifier_429_waits_and_retries():
    n, seen, slept = notifier([err(429, retry_after=7), ok()])
    assert n.send(42, "a") == "html"
    assert slept == [7] and len(seen) == 2


def test_notifier_429_twice_is_retry():
    n, _, slept = notifier([err(429, retry_after=2), err(429, retry_after=2)])
    with pytest.raises(SendError) as exc:
        n.send(42, "a")
    assert exc.value.kind == "retry" and slept == [2]


def test_notifier_403_blocked():
    n, _, _ = notifier([err(403, "Forbidden: bot was blocked by the user")])
    with pytest.raises(SendError) as exc:
        n.send(42, "a")
    assert exc.value.kind == "blocked"


def test_notifier_400_resends_plain():
    n, seen, _ = notifier([err(400, "Bad Request: can't parse entities"), ok()])
    assert n.send(42, '<b>Purpose:</b> A &amp; B <a href="u">VECO post</a>') == "plain"
    assert "parse_mode" not in seen[1]
    assert seen[1]["text"] == "Purpose: A & B VECO post (u)"


def test_notifier_400_twice_is_bad_request():
    n, _, _ = notifier([err(400), err(400)])
    with pytest.raises(SendError) as exc:
        n.send(42, "<b>a</b>")
    assert exc.value.kind == "bad_request"


@pytest.mark.parametrize(
    "first",
    [err(500), err(502), httpx.ConnectError("down"), httpx.ReadTimeout("slow")],
)
def test_notifier_5xx_and_network_retry(first):
    n, _, _ = notifier([first])
    with pytest.raises(SendError) as exc:
        n.send(42, "a")
    assert exc.value.kind == "retry"
    assert "SECRET" not in str(exc.value)


def test_print_notifier():
    out = io.StringIO()
    assert PrintNotifier(out).send(42, "<b>hi</b>") == "html"
    assert out.getvalue() == "--- to chat 42 ---\n<b>hi</b>\n\n"
