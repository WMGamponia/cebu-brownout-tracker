"""Where alert messages go (Phase 3 spec 5.3): Telegram, or stdout for a dry run.

`send` returns how the message went out: `html`, or `plain` after Telegram rejected the HTML
and the text was resent without markup (the alert run turns that into an admin note). Failures
raise `SendError` with a kind the run acts on: `retry` (leave the items unrecorded), `blocked`
(deactivate the subscriber) or `bad_request`.
"""

from __future__ import annotations

import html
import re
import sys
import time
from collections.abc import Callable
from typing import Literal, Protocol, TextIO

from outages.telegram import TelegramClient, TelegramError

SendKind = Literal["retry", "blocked", "bad_request"]
Delivery = Literal["html", "plain"]


class SendError(Exception):
    def __init__(self, kind: SendKind, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail


class Notifier(Protocol):
    def send(self, chat_id: int, html: str) -> Delivery: ...


_LINK = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.S)
_TAG = re.compile(r"<[^>]+>")


def to_plain(text: str) -> str:
    """Telegram HTML -> plain text: links become `label (url)`, tags dropped, entities decoded."""
    text = _LINK.sub(lambda m: f"{m.group(2)} ({m.group(1)})", text)
    return html.unescape(_TAG.sub("", text))


class TelegramNotifier:
    def __init__(self, client: TelegramClient, sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.sleep = sleep

    def send(self, chat_id: int, html: str) -> Delivery:
        try:
            self._send(chat_id, html, True, retry_429=True)
            return "html"
        except TelegramError as e:
            if e.status != 400:
                raise self._error(e) from None
        try:
            self._send(chat_id, to_plain(html), False, retry_429=True)
        except TelegramError as e:
            raise (
                SendError("bad_request", str(e)) if e.status == 400 else self._error(e)
            ) from None
        return "plain"

    def _send(self, chat_id: int, text: str, as_html: bool, retry_429: bool) -> None:
        try:
            self.client.send_message(chat_id, text, html=as_html)
        except TelegramError as e:
            if e.status == 429 and retry_429:
                self.sleep(e.retry_after or 1)
                self._send(chat_id, text, as_html, retry_429=False)
            else:
                raise

    @staticmethod
    def _error(e: TelegramError) -> SendError:
        if e.status == 403:
            return SendError("blocked", str(e))
        return SendError("retry", str(e))  # 429 again, 5xx, network, 401/404


class PrintNotifier:
    """Writes each message to a stream instead of sending it (`alerts --dry-run`)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream

    def send(self, chat_id: int, html: str) -> Delivery:
        out = self.stream or sys.stdout
        out.write(f"--- to chat {chat_id} ---\n{html}\n\n")
        return "html"
