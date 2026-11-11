"""Telegram Bot API client over httpx (Phase 3 spec 5.2): four calls, nothing else.

The token is part of every request URL (`/bot<token>/<method>`), so it must never reach a log,
an exception or the console: errors are rebuilt without the URL, and `install_redaction` quiets
httpx's request logging and scrubs the token from the `outages` log handlers.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

API = "https://api.telegram.org"
REDACTED = "<token>"


class TelegramError(Exception):
    """A Bot API failure. `status` is the HTTP status, 0 for a network error."""

    def __init__(self, status: int, description: str, retry_after: int | None = None) -> None:
        super().__init__(f"Telegram {status}: {description}")
        self.status = status
        self.description = description
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return f"TelegramError({self.status}, {self.description!r}, {self.retry_after!r})"


class TelegramClient:
    def __init__(
        self, token: str, transport: httpx.BaseTransport | None = None, timeout_s: float = 60
    ) -> None:
        self._token = token
        self._http = httpx.Client(transport=transport, timeout=timeout_s)

    def __repr__(self) -> str:
        return "TelegramClient(<token>)"

    def _scrub(self, text: str) -> str:
        return text.replace(self._token, REDACTED)

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{API}/bot{self._token}/{method}"
        try:
            r = self._http.post(url, json=payload)
        except httpx.HTTPError as e:
            # `from None`: the httpx exception and its request carry the URL with the token.
            raise TelegramError(0, f"{type(e).__name__}: {self._scrub(str(e))}") from None
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(r.status_code, f"non-JSON response ({r.status_code})") from None
        if r.status_code == 200 and body.get("ok"):
            return body.get("result")
        params = body.get("parameters") or {}
        desc = self._scrub(str(body.get("description") or "unknown error"))
        raise TelegramError(r.status_code, desc, params.get("retry_after"))

    def get_updates(self, offset: int | None, timeout_s: int = 50) -> list[dict]:
        payload: dict[str, Any] = {
            "timeout": timeout_s,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload)

    def send_message(
        self, chat_id: int, text: str, html: bool = True, reply_markup: dict | None = None
    ) -> dict:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if html:
            payload["parse_mode"] = "HTML"
        payload["disable_web_page_preview"] = True
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def answer_callback_query(self, callback_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        self._call("answerCallbackQuery", payload)

    def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: dict | None
    ) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("editMessageReplyMarkup", payload)

    def close(self) -> None:
        self._http.close()


class _Redact(logging.Filter):
    """Replaces the token in a record's message and traceback before any handler writes it."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if self.token in msg:
            record.msg, record.args = msg.replace(self.token, REDACTED), ()
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text and self.token in record.exc_text:
            record.exc_text = record.exc_text.replace(self.token, REDACTED)
        return True


def install_redaction(token: str) -> None:
    """Quiet httpx/httpcore (they log request URLs at INFO) and scrub the token from every
    handler on the `outages` logger. Idempotent; call after logging is set up."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for h in logging.getLogger("outages").handlers:
        if not any(isinstance(f, _Redact) and f.token == token for f in h.filters):
            h.addFilter(_Redact(token))
