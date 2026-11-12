"""Static checks on the map and history pages (Phase 6 spec 4.3, 7)."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "outages" / "map" / "static"
PAGES = ("index.html", "history.html")
OWN_JS = sorted((STATIC / "js").glob("*.js"))


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_tile_url_in_one_place():
    files = [*OWN_JS, *(STATIC / p for p in PAGES)]
    hits = [
        f.relative_to(STATIC).as_posix()
        for f in files
        if "tile.openstreetmap.org" in f.read_text(encoding="utf-8")
    ]
    assert hits == ["js/tiles.js"]


def test_no_referrer_policy():
    for page in PAGES:
        metas = re.findall(r"<meta[^>]*>", _text(page), flags=re.I)
        assert not [m for m in metas if "referrer" in m.lower()], page


def test_pages_never_read_the_clock_directly():
    for name in ("js/map.js", "js/history.js"):
        assert "Date.now(" not in _text(name), name


def test_fix_the_map_link():
    for page in PAGES:
        html = _text(page)
        assert re.search(
            r'<a [^>]*href="https://www\.openstreetmap\.org/fixthemap"[^>]*>Report a map issue</a>',
            html,
        ), page
