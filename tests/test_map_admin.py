"""Admin routes for the tray: loopback app only, token header, never a browser (tray spec 5.5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from map_world import NOW, boundaries, gazetteer, populate

from outages import db
from outages.map.admin import HEADER, AdminConfig
from outages.map.app import create_app

TOKEN = "a" * 43
AUTH = {HEADER: TOKEN}


class FakeControl:
    def __init__(self, busy=False, pending_chats=(7,)):
        self.busy, self.pending_chats = busy, set(pending_chats)
        self.runs = 0
        self.decisions: list = []
        self.stops = 0

    def run_now(self) -> bool:
        if self.busy:
            return False
        self.runs += 1
        return True

    def pending(self) -> list[dict]:
        return [
            {"chat_id": c, "name": "Juan", "requested_at": "2026-11-22T01:00:00+00:00"}
            for c in sorted(self.pending_chats)
        ]

    def decide(self, chat_id: int, approve: bool) -> bool:
        if chat_id not in self.pending_chats:
            return False
        self.decisions.append((chat_id, approve))
        self.pending_chats.discard(chat_id)
        return True

    def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def world(tmp_path) -> dict:
    path = tmp_path / "outages.db"
    c = db.connect(path)
    populate(c)
    c.close()
    return {"db": path}


def _client(world, control=None, admin=True) -> tuple[TestClient, FakeControl]:
    control = control or FakeControl()
    cfg = AdminConfig(TOKEN, control) if admin else None
    app = create_app(world["db"], boundaries(), gazetteer(), clock=lambda: NOW, admin=cfg)
    return TestClient(app), control


def test_run_started(world):
    c, ctl = _client(world)
    r = c.post("/admin/run", headers=AUTH)
    assert r.status_code == 202 and r.json() == {"started": True}
    assert ctl.runs == 1


def test_run_busy_409(world):
    c, _ = _client(world, FakeControl(busy=True))
    r = c.post("/admin/run", headers=AUTH)
    assert r.status_code == 409 and r.json() == {"error": "already running"}


def test_missing_token_403(world):
    c, ctl = _client(world)
    assert c.post("/admin/run").status_code == 403
    assert ctl.runs == 0


def test_wrong_token_403(world):
    c, ctl = _client(world)
    assert c.post("/admin/run", headers={HEADER: "b" * 43}).status_code == 403
    assert ctl.runs == 0


def test_origin_present_403(world):
    c, ctl = _client(world)
    r = c.post("/admin/run", headers={**AUTH, "Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 403 and r.json() == {"error": "forbidden"}
    assert ctl.runs == 0


def test_pending_lists(world):
    c, _ = _client(world)
    r = c.get("/admin/pending", headers=AUTH)
    assert r.status_code == 200
    assert [p["chat_id"] for p in r.json()["pending"]] == [7]


def test_pending_needs_token(world):
    c, _ = _client(world)
    assert c.get("/admin/pending").status_code == 403


def test_decide_ok(world):
    c, ctl = _client(world)
    r = c.post("/admin/decide", headers=AUTH, json={"chat_id": 7, "approve": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ctl.decisions == [(7, True)]


def test_decide_not_pending_404(world):
    c, ctl = _client(world)
    r = c.post("/admin/decide", headers=AUTH, json={"chat_id": 8, "approve": False})
    assert r.status_code == 404 and r.json() == {"error": "not pending"}
    assert ctl.decisions == []


@pytest.mark.parametrize(
    "body", [{}, {"chat_id": "7", "approve": True}, {"chat_id": 7, "approve": "yes"}, [1]]
)
def test_decide_bad_body_400(world, body):
    c, ctl = _client(world)
    r = c.post("/admin/decide", headers=AUTH, json=body)
    assert r.status_code == 400
    assert ctl.decisions == []


def test_decide_bool_is_not_a_chat_id(world):
    c, _ = _client(world)
    r = c.post("/admin/decide", headers=AUTH, json={"chat_id": True, "approve": True})
    assert r.status_code == 400


def test_stop_202(world):
    c, ctl = _client(world)
    assert c.post("/admin/stop", headers=AUTH).status_code == 202
    assert ctl.stops == 1


def test_routes_absent_without_admin(world):
    c, _ = _client(world, admin=False)
    assert c.post("/admin/run", headers=AUTH).status_code == 404
