"""Running the map: loopback check, bind failure handled in the thread (Phase 4 spec 4.5)."""

from __future__ import annotations

import socket

import pytest
from fastapi import FastAPI

from outages.map import server


@pytest.mark.parametrize(
    ("host", "loop"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("127.0.0.2", True),
        ("0.0.0.0", False),
        ("192.168.1.5", False),
        ("::", False),
    ],
)
def test_is_loopback(host, loop):
    assert server.is_loopback(host) is loop


@pytest.fixture
def taken_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    yield s.getsockname()[1]
    s.close()


def test_serve_forever_port_in_use_returns_false(taken_port):
    assert server.serve_forever(FastAPI(), "127.0.0.1", taken_port) is False


def test_start_thread_reports_failure_once(taken_port):
    errors: list[str] = []
    t = server.start_thread(FastAPI(), "127.0.0.1", taken_port, errors.append)
    assert t.daemon and t.name == "map-server"
    t.join(10)
    assert not t.is_alive()
    assert len(errors) == 1
    assert str(taken_port) in errors[0]


def test_start_thread_reports_unexpected_error(monkeypatch):
    def boom(app, host, port):
        raise RuntimeError("bad app")

    monkeypatch.setattr(server, "serve_forever", boom)
    errors: list[str] = []
    server.start_thread(FastAPI(), "127.0.0.1", 1, errors.append).join(5)
    assert len(errors) == 1 and "bad app" in errors[0]
