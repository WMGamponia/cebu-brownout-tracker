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
