"""Shared test helpers: fixture paths."""

from __future__ import annotations

from pathlib import Path

import pytest

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
