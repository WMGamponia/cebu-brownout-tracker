"""The committed VECO post fixtures are trimmed to what the parser reads (Phase 6 spec 4.7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POSTS = sorted((ROOT / "tests" / "fixtures" / "posts").glob("*.html"))


def _trim():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from trim_fixture import trim
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return trim


def test_twelve_fixtures():
    assert len(POSTS) == 12


@pytest.mark.parametrize("path", POSTS, ids=lambda p: p.stem)
def test_fixtures_trimmed(path):
    data = path.read_bytes()
    assert len(data) < 200_000
    low = data.lower()
    assert b"<script" not in low and b"<style" not in low


@pytest.mark.parametrize("path", POSTS, ids=lambda p: p.stem)
def test_trim_is_idempotent(path):
    trim = _trim()
    html = path.read_text(encoding="utf-8")
    assert trim(html) == html


def test_trim_keeps_title_and_content():
    trim = _trim()
    page = (
        "<html><head><title>T &amp; U</title><script>x()</script><style>p{}</style></head>"
        '<body><div><article><h1 data-hook="post-title">Heading</h1><p>byline</p>'
        '<section data-hook="post-description" class="c"><p style="a" id="i">Body &amp; more</p>'
        '<p><span style="text-decoration: line-through">gone</span><img class="x" src="s.png">'
        "</p></section></article></div><script>y()</script></body></html>"
    )
    out = trim(page)
    assert out == (
        '<!doctype html>\n<html><head><meta charset="utf-8"><title>T &amp; U</title></head>'
        '<body><article><h1 data-hook="post-title">Heading</h1>'
        '<section data-hook="post-description"><p>Body &amp; more</p>'
        '<p><span style="text-decoration:line-through">gone</span><img src="s.png"></p>'
        "</section></article></body></html>\n"
    )
    assert trim(out) == out


def test_trim_without_content_raises():
    with pytest.raises(ValueError):
        _trim()("<html><body><p>no post here</p></body></html>")
