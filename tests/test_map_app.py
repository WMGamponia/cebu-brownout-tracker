"""The map's HTTP surface (Phase 4 spec 4.4, 6)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from map_world import NOW, boundaries, gazetteer, populate

from outages import db
from outages.map import app as map_app
from outages.map.app import create_app
from outages.map.build import build, dumps
from outages.map.window import default_window


@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "outages.db"
    c = db.connect(path)
    populate(c)
    c.close()
    return path


def _client(path: Path) -> TestClient:
    return TestClient(create_app(path, boundaries(), gazetteer(), clock=lambda: NOW))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_geojson_ok(db_path):
    r = _client(db_path).get("/outages.geojson")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/geo+json")
    assert r.headers["cache-control"] == "no-store"
    ro = db.connect_ro(db_path)
    expected = dumps(build(ro, gazetteer(), boundaries(), default_window(ro, NOW), NOW))
    ro.close()
    assert r.content.decode("utf-8") == expected


def test_query_params_forwarded(db_path):
    r = _client(db_path).get("/outages.geojson?from=2026-11-13&days=1")
    assert r.json()["meta"]["window"] == {"from": "2026-11-13", "to": "2026-11-13"}


@pytest.mark.parametrize(("query", "param"), [("days=15", "days"), ("from=2026-02-30", "from")])
def test_bad_query_422(db_path, query, param):
    r = _client(db_path).get(f"/outages.geojson?{query}")
    assert r.status_code == 422
    assert param in r.json()["error"]


def test_index_and_static(db_path):
    c = _client(db_path)
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert c.get("/static/map.css").status_code == 200


def test_healthz(db_path):
    r = _client(db_path).get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "schema": 3, "last_check_at": "2026-11-12T08:00:00+08:00"}


def test_no_docs_routes(db_path):
    c = _client(db_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(path).status_code == 404


def test_only_get(db_path):
    assert _client(db_path).post("/outages.geojson").status_code == 405


def test_missing_db_503_creates_nothing(tmp_path):
    path = tmp_path / "nope" / "outages.db"
    c = _client(path)
    for route in ("/outages.geojson", "/healthz"):
        r = c.get(route)
        assert r.status_code == 503
        assert r.json() == {"error": "database not ready: run python -m outages areas"}
    assert not path.exists()
    assert not path.parent.exists()


def test_old_schema_503(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript(
        "CREATE TABLE schema_version (version INTEGER); INSERT INTO schema_version VALUES (2);"
    )
    c.close()
    assert _client(path).get("/outages.geojson").status_code == 503


def test_requests_leave_db_unchanged(db_path):
    before = _sha(db_path)
    c = _client(db_path)
    for route in ("/outages.geojson", "/outages.geojson?from=2026-11-13&days=2", "/healthz"):
        assert c.get(route).status_code == 200
    assert _sha(db_path) == before


def test_unexpected_error_500_generic(db_path, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(map_app, "build", boom)
    with caplog.at_level(logging.ERROR):
        r = TestClient(
            create_app(db_path, boundaries(), gazetteer(), clock=lambda: NOW),
            raise_server_exceptions=False,
        ).get("/outages.geojson")
    assert r.status_code == 500
    assert r.json() == {"error": "internal error"}
    assert "secret detail" not in r.text
    assert "secret detail" in caplog.text


def test_body_is_utf8_json(db_path):
    doc = json.loads(_client(db_path).get("/outages.geojson").content.decode("utf-8"))
    assert any(f["properties"]["name"] == "Santo Niño" for f in doc["features"])
