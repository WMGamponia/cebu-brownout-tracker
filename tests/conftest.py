"""Shared test helpers: fixture paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from lock_holder import Holder

FIXTURES = Path(__file__).parent / "fixtures"
POSTS = FIXTURES / "posts"


def read_post(slug: str) -> str:
    """Return a saved post's HTML."""
    return (POSTS / f"{slug}.html").read_text(encoding="utf-8")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@pytest.fixture(autouse=True)
def _block_real_network(request, monkeypatch):
    """No test may reach the network (VECO above all); mocks use MockTransport and are
    unaffected. `live` tests opt out; localhost (Ollama, for `llm` tests) stays reachable."""
    if request.node.get_closest_marker("live"):
        return
    import httpx

    real = httpx.HTTPTransport.handle_request

    def guard(self, req):
        if req.url.host in _LOCAL_HOSTS:
            return real(self, req)
        raise RuntimeError(f"network blocked in tests: {req.method} {req.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", guard)


@pytest.fixture
def held_lock():
    """Start processes that hold a lock file until the test ends."""
    holders = []

    def hold(path: Path) -> Holder:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = Holder(path)
        holders.append(h)
        return h

    yield hold
    for h in holders:
        h.close()
