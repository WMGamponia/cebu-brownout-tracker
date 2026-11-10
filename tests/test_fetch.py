"""Polite fetcher (spec 7) over httpx.MockTransport: no network."""

from __future__ import annotations

import httpx

from outages.config import USER_AGENT
from outages.fetch import Fetcher


def _fetcher(handler, delay=(0, 0)):
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client=client, sleep=sleeps.append, delay_s=delay), sleeps


def test_sends_user_agent_and_returns_text():
    seen = []

    def handler(req):
        seen.append(req.headers["user-agent"])
        return httpx.Response(200, text="Niño")

    f, _ = _fetcher(handler)
    assert f.get("https://x/a") == "Niño"
    assert seen == [USER_AGENT]


def test_retries_once_after_503():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, text="ok")

    f, sleeps = _fetcher(handler)
    assert f.get("https://x/a") == "ok"
    assert len(calls) == 2
    assert sleeps == [30]


def test_retries_once_after_network_error_then_gives_up():
    calls = []

    def handler(req):
        calls.append(1)
        raise httpx.ConnectError("down", request=req)

    f, sleeps = _fetcher(handler)
    assert f.get("https://x/a") is None
    assert len(calls) == 2
    assert sleeps == [30]


def test_404_returns_none_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(404)

    f, sleeps = _fetcher(handler)
    assert f.get("https://x/a") is None
    assert len(calls) == 1
    assert sleeps == []


def test_delay_between_requests_not_before_first():
    f, sleeps = _fetcher(lambda req: httpx.Response(200, text="x"), delay=(3, 8))
    f.get("https://x/a")
    assert sleeps == []
    f.get("https://x/b")
    f.get("https://x/c")
    assert len(sleeps) == 2
    assert all(3 <= s <= 8 for s in sleeps)


def test_follows_redirects():
    def handler(req):
        if req.url.path == "/a":
            return httpx.Response(301, headers={"location": "https://x/b"})
        return httpx.Response(200, text="moved")

    f, _ = _fetcher(handler)
    assert f.get("https://x/a") == "moved"
