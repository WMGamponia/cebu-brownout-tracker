"""The tray's client for `/healthz` and `/admin/*` (tray spec 5.5)."""

from __future__ import annotations

import json

import httpx

from outages import admintoken
from outages.map.admin import HEADER
from outages.tray.client import ServeClient


def client(tmp_path, handler) -> ServeClient:
    return ServeClient(data_dir=tmp_path, transport=httpx.MockTransport(handler))


def test_run_now_started_and_busy(tmp_path):
    token = admintoken.write_new(tmp_path)
    codes = iter([202, 409])

    def handler(req):
        assert req.headers[HEADER] == token
        return httpx.Response(next(codes), json={})

    c = client(tmp_path, handler)
    assert c.run_now() == "started"
    assert c.run_now() == "busy"


def test_client_rereads_token_on_403(tmp_path):
    old = admintoken.write_new(tmp_path)
    seen: list[str] = []
    box: dict = {}

    def handler(req):
        seen.append(req.headers[HEADER])
        if req.headers[HEADER] == old:
            box["new"] = admintoken.write_new(tmp_path)  # serve restarted meanwhile
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(202, json={"started": True})

    c = client(tmp_path, handler)
    assert c.run_now() == "started"
    assert seen == [old, box["new"]]


def test_403_twice_is_error(tmp_path):
    admintoken.write_new(tmp_path)
    calls: list = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403, json={})

    assert client(tmp_path, handler).run_now() == "error"
    assert len(calls) == 2


def test_never_sends_origin(tmp_path):
    admintoken.write_new(tmp_path)

    def handler(req):
        assert "origin" not in req.headers
        return httpx.Response(200, json={"pending": [], "ok": True})

    c = client(tmp_path, handler)
    c.pending()
    c.decide(7, True)
    c.run_now()
    c.stop()


def test_no_token_is_error_without_request(tmp_path):
    calls: list = []

    def handler(req):
        calls.append(1)
        return httpx.Response(202, json={})

    assert client(tmp_path, handler).run_now() == "error"
    assert calls == []


def test_healthz_false_on_connect_error(tmp_path):
    def handler(req):
        raise httpx.ConnectError("refused")

    assert client(tmp_path, handler).healthz() is False


def test_healthz_true_when_answering_even_503(tmp_path):
    def handler(req):
        return httpx.Response(503, json={"error": "not ready"})

    assert client(tmp_path, handler).healthz() is True


def test_decide_results(tmp_path):
    admintoken.write_new(tmp_path)
    codes = iter([200, 404, 500])

    def handler(req):
        assert json.loads(req.content) == {"chat_id": 7, "approve": False}
        return httpx.Response(next(codes), json={})

    c = client(tmp_path, handler)
    assert c.decide(7, False) == "ok"
    assert c.decide(7, False) == "not_pending"
    assert c.decide(7, False) == "error"


def test_pending_empty_on_error(tmp_path):
    admintoken.write_new(tmp_path)

    def handler(req):
        raise httpx.ReadTimeout("slow")

    assert client(tmp_path, handler).pending() == []


def test_pending_parses(tmp_path):
    admintoken.write_new(tmp_path)
    body = {"pending": [{"chat_id": 7, "name": "Juan", "requested_at": "x"}]}

    def handler(req):
        return httpx.Response(200, json=body)

    assert client(tmp_path, handler).pending() == body["pending"]


def test_stop(tmp_path):
    admintoken.write_new(tmp_path)

    def handler(req):
        assert req.url.path == "/admin/stop"
        return httpx.Response(202, json={})

    assert client(tmp_path, handler).stop() is True
