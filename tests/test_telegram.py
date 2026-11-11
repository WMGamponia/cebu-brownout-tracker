"""Bot API client over httpx (Phase 3 spec 5.2). Every request goes to a MockTransport."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from outages.telegram import API, TelegramClient, TelegramError, install_redaction

TOKEN = "123456:SECRET-abcdefghijklmnop"


def client_with(handler) -> tuple[TelegramClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    return TelegramClient(TOKEN, transport=httpx.MockTransport(record)), seen


def ok(result) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def test_send_message_posts_html():
    c, seen = client_with(lambda r: ok({"message_id": 7}))
    assert c.send_message(42, "<b>hi</b>") == {"message_id": 7}
    (req,) = seen
    assert str(req.url) == f"{API}/bot{TOKEN}/sendMessage"
    body = json.loads(req.content)
    assert body == {
        "chat_id": 42,
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }


def test_send_message_plain_and_markup():
    c, seen = client_with(lambda r: ok({"message_id": 7}))
    markup = {"inline_keyboard": [[{"text": "A", "callback_data": "add:1"}]]}
    c.send_message(42, "hi", html=False, reply_markup=markup)
    body = json.loads(seen[0].content)
    assert "parse_mode" not in body and body["reply_markup"] == markup


def test_get_updates_offset_and_timeout():
    c, seen = client_with(lambda r: ok([{"update_id": 5}]))
    assert c.get_updates(5, timeout_s=50) == [{"update_id": 5}]
    body = json.loads(seen[0].content)
    assert body["offset"] == 5 and body["timeout"] == 50
    c.get_updates(None)
    assert "offset" not in json.loads(seen[1].content)


def test_callback_and_edit_markup():
    c, seen = client_with(lambda r: ok(True))
    c.answer_callback_query("cb1", "Added")
    c.edit_message_reply_markup(42, 9, None)
    assert [r.url.path.rsplit("/", 1)[1] for r in seen] == [
        "answerCallbackQuery",
        "editMessageReplyMarkup",
    ]
    assert json.loads(seen[0].content) == {"callback_query_id": "cb1", "text": "Added"}
    assert json.loads(seen[1].content) == {"chat_id": 42, "message_id": 9}


def test_error_fields():
    def h(r):
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 3},
            },
        )

    c, _ = client_with(h)
    with pytest.raises(TelegramError) as exc:
        c.send_message(42, "x")
    assert (exc.value.status, exc.value.retry_after) == (429, 3)
    assert "Too Many Requests" in exc.value.description


def test_error_has_no_token():
    c, _ = client_with(
        lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
    )
    with pytest.raises(TelegramError) as exc:
        c.send_message(42, "x")
    assert exc.value.status == 401
    assert "SECRET" not in str(exc.value) and "SECRET" not in repr(exc.value)


def test_network_error_has_no_token():
    def h(r):
        raise httpx.ConnectError(f"cannot reach {r.url}", request=r)

    c, _ = client_with(h)
    with pytest.raises(TelegramError) as exc:
        c.get_updates(None)
    assert exc.value.status == 0
    assert "SECRET" not in str(exc.value) and "SECRET" not in repr(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_non_json_error_response():
    c, _ = client_with(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    with pytest.raises(TelegramError) as exc:
        c.send_message(42, "x")
    assert exc.value.status == 502


@pytest.fixture
def outages_file_log(tmp_path):
    log = logging.getLogger("outages")
    path = tmp_path / "outages.log"
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    log.addHandler(handler)
    old = log.level
    log.setLevel(logging.INFO)
    yield path, handler
    log.removeHandler(handler)
    handler.close()
    log.setLevel(old)


def test_token_never_logged(outages_file_log):
    path, handler = outages_file_log
    install_redaction(TOKEN)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING

    def h(r):
        raise httpx.ConnectError(f"cannot reach {r.url}", request=r)

    c, _ = client_with(h)
    log = logging.getLogger("outages.test")
    log.info("url was %s", f"{API}/bot{TOKEN}/getMe")
    try:
        c.send_message(42, "x")
    except TelegramError:
        log.exception("send failed")
    try:
        raise RuntimeError(f"wrapped {TOKEN}")
    except RuntimeError:
        log.exception("boom")
    handler.flush()
    text = path.read_text(encoding="utf-8")
    assert "SECRET" not in text
    assert "<token>" in text and "send failed" in text and "boom" in text


def test_install_redaction_idempotent(outages_file_log):
    _, handler = outages_file_log
    install_redaction(TOKEN)
    install_redaction(TOKEN)
    assert sum(type(f).__name__ == "_Redact" for f in handler.filters) == 1
