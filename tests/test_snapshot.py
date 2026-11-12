"""The static demo snapshot (Phase 6 spec 4.1, 4.2, 7)."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

import pytest
from map_world import boundaries, gazetteer, populate, populate_streets

from outages import db
from outages.config import PUBLIC_REPO_URL, VECO_HOME
from outages.history.load import load_rows
from outages.history.stats import Range, compute
from outages.map.build import build, dumps
from outages.map.window import Window, default_window
from outages.snapshot import (
    PRIVATE_TABLES,
    PUBLIC_TABLES,
    build_files,
    check_out_dir,
    find_leak,
    guard,
    ranges,
    windows,
    write,
)
from outages.timeparse import MANILA

TAKEN = datetime(2026, 11, 12, 20, 5, 11, tzinfo=MANILA)
LAST_CHECK = "2026-11-12T00:00:00+00:00"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "outages.db")
    populate(c)
    populate_streets(c)
    yield c
    c.close()


@pytest.fixture(scope="module")
def world():
    return gazetteer(), boundaries()


def _files(conn, world):
    gaz, b = world
    return build_files(conn, gaz, b, TAKEN, LAST_CHECK)


def test_tables_classified(tmp_path):
    c = db.connect(tmp_path / "fresh.db")
    names = {
        r[0]
        for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        if not r[0].startswith("sqlite_")
    }
    c.close()
    assert not PUBLIC_TABLES & PRIVATE_TABLES
    assert names == PUBLIC_TABLES | PRIVATE_TABLES
    assert {"subscribers", "subscriptions", "alerts_sent", "admin_notes_sent"} <= PRIVATE_TABLES


def test_guard_denies_private_tables(conn):
    guard(conn)
    for t in sorted(PRIVATE_TABLES):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"SELECT * FROM {t}").fetchall()
    assert conn.execute("SELECT count(*) FROM outages").fetchone()[0] == 11


def test_guard_denies_unclassified_tables(conn):
    conn.execute("CREATE TABLE new_bot_state (chat_id INTEGER)")
    guard(conn)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("SELECT * FROM new_bot_state").fetchall()


def test_windows_tile(conn):
    ws = windows(conn, TAKEN)
    assert len(ws) == 53
    assert ws[-1] == default_window(conn, TAKEN)
    assert ws[-1].from_date == date(2026, 11, 12)
    assert ws[-2] == Window(date(2026, 11, 5), date(2026, 11, 11))
    assert ws[0].from_date == date(2025, 11, 13)
    for a, b in zip(ws, ws[1:], strict=False):
        assert (a.to_date - a.from_date).days == 6
        assert a.to_date + timedelta(days=1) == b.from_date


def test_windows_tile_with_short_snapshot_week(conn):
    conn.execute("UPDATE outages SET status = 'removed' WHERE end_at > '2026-11-14'")
    ws = windows(conn, TAKEN)
    assert ws[-1] == Window(date(2026, 11, 12), date(2026, 11, 13))
    assert ws[-2] == Window(date(2026, 11, 5), date(2026, 11, 11))


def test_windows_with_no_future_outage(conn):
    conn.execute("UPDATE outages SET status = 'removed'")
    ws = windows(conn, TAKEN)
    assert ws[-1] == Window(date(2026, 11, 12), date(2026, 11, 12))
    assert len(ws) == 53


def test_ranges():
    rs = ranges(date(2026, 11, 12))
    assert [r.id for r in rs] == ["last-12-months", "2024", "2025", "2026"]
    assert (rs[0].label, rs[0].from_d, rs[0].to_d) == (
        "Last 12 months",
        date(2025, 11, 13),
        date(2026, 11, 12),
    )
    assert (rs[1].label, rs[1].from_d, rs[1].to_d) == ("2024", date(2024, 1, 1), date(2024, 12, 31))
    assert (rs[3].label, rs[3].from_d, rs[3].to_d) == (
        "2026 to date",
        date(2026, 1, 1),
        date(2026, 11, 12),
    )
    assert [r.id for r in ranges(date(2027, 1, 3))][1:] == ["2024", "2025", "2026", "2027"]


def test_build_files_layout(conn, world):
    files = _files(conn, world)
    for key in (
        "index.html",
        "history.html",
        "snapshot.json",
        "boundaries.geojson",
        "LICENSES.md",
        ".nojekyll",
        "static/js/map.js",
        "static/js/source.js",
        "static/map.css",
        "static/vendor/leaflet-1.9.4/leaflet.js",
    ):
        assert key in files, key
    assert len([k for k in files if k.startswith("weeks/")]) == 53
    assert sorted(k for k in files if k.startswith("stats/")) == [
        "stats/2024.json",
        "stats/2025.json",
        "stats/2026.json",
        "stats/last-12-months.json",
    ]
    assert not [k for k in files if "\\" in k or k.startswith("/")]
    for page in ("index.html", "history.html"):
        html = files[page].decode("utf-8")
        assert '<html lang="en" data-mode="snapshot">' in html
    assert 'href="history.html"' in files["index.html"].decode("utf-8")
    assert files["boundaries.geojson"] == dumps(world[1]).encode("utf-8")


def test_week_files_equal_build(conn, world):
    gaz, b = world
    files = _files(conn, world)
    w = Window(date(2026, 11, 5), date(2026, 11, 11))
    assert files["weeks/2026-11-05.geojson"] == dumps(build(conn, gaz, b, w, TAKEN)).encode()
    snap = default_window(conn, TAKEN)
    assert files["weeks/2026-11-12.geojson"] == dumps(build(conn, gaz, b, snap, TAKEN)).encode()


def test_stats_files_equal_compute(conn, world):
    gaz, _ = world
    files = _files(conn, world)
    rng = Range(date(2025, 1, 1), date(2025, 12, 31))
    rows, cov = load_rows(conn, rng)
    assert files["stats/2025.json"] == dumps(compute(rows, rng, gaz, cov, TAKEN)).encode()
    rng = Range(date(2025, 11, 13), date(2026, 11, 12))
    rows, cov = load_rows(conn, rng)
    assert files["stats/last-12-months.json"] == dumps(compute(rows, rng, gaz, cov, TAKEN)).encode()


def test_snapshot_json_fields(conn, world):
    s = json.loads(_files(conn, world)["snapshot.json"])
    assert s["format"] == 1
    assert s["taken_at"] == "2026-11-12T20:05:11+08:00"
    assert s["last_check_at"] == "2026-11-12T08:00:00+08:00"
    assert s["snapshot_week"] == "2026-11-12"
    assert len(s["weeks"]) == 53 and s["weeks"] == sorted(s["weeks"])
    assert s["weeks"][0] == "2025-11-13" and s["weeks"][-1] == "2026-11-12"
    assert s["default_range"] == "last-12-months"
    assert s["ranges"][0] == {
        "id": "last-12-months",
        "label": "Last 12 months",
        "from": "2025-11-13",
        "to": "2026-11-12",
    }
    assert s["veco_url"] == VECO_HOME == "https://www.visayanelectric.com/"
    assert s["code_url"] == PUBLIC_REPO_URL


def test_build_is_deterministic(conn, world):
    assert _files(conn, world) == _files(conn, world)


def test_licenses_text(conn, world):
    text = _files(conn, world)["LICENSES.md"].decode("utf-8")
    for needle in (
        "VECO",
        "faeldon/philippines-json-maps",
        "Leaflet",
        "BSD-2-Clause",
        "© OpenStreetMap contributors",
        "which see your IP address",
        "ODbL",
    ):
        assert needle in text, needle


def test_leak_scan_matches_whole_numbers_only():
    assert find_leak({"a": b'"x":123456789'}, [12345678], None) is None
    assert find_leak({"a": b'"x":1234567890'}, [12345678], None) is None
    assert find_leak({"a": b'"chat":12345678,'}, [12345678], None) == "chat ID"
    assert find_leak({"a": b"id 12345678"}, [12345678], None) == "chat ID"
    assert find_leak({"a": b"group 1001234567"}, [-1001234567], None) == "chat ID"
    assert find_leak({"a": b"nothing"}, [12345678], "123:abc-token") is None
    assert find_leak({"a": b"x123:abc-tokeny"}, [], "123:abc-token") == "bot token"


def _make_snapshot_dir(out, weeks=("2025-11-06",)):
    out.mkdir(parents=True, exist_ok=True)
    (out / "snapshot.json").write_text("{}", encoding="utf-8")
    (out / "weeks").mkdir(exist_ok=True)
    for w in weeks:
        (out / "weeks" / f"{w}.geojson").write_text("{}", encoding="utf-8")
    (out / "static").mkdir(exist_ok=True)
    (out / "index.html").write_text("x", encoding="utf-8")


def test_check_out_dir(tmp_path):
    assert check_out_dir(tmp_path / "missing") is None
    empty = tmp_path / "empty"
    empty.mkdir()
    assert check_out_dir(empty) is None
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: E:/x/.git/worktrees/site\n", encoding="utf-8")
    assert check_out_dir(wt) is None
    prev = tmp_path / "prev"
    _make_snapshot_dir(prev)
    assert check_out_dir(prev) is None
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "notes.txt").write_text("x", encoding="utf-8")
    assert "notes.txt" in check_out_dir(notes)
    weeks_only = tmp_path / "weeks-only"
    (weeks_only / "weeks").mkdir(parents=True)
    assert check_out_dir(weeks_only)
    a_file = tmp_path / "a-file"
    a_file.write_text("x", encoding="utf-8")
    assert check_out_dir(a_file)


def test_foreign_file_refused(tmp_path):
    out = tmp_path / "site"
    _make_snapshot_dir(out)
    (out / "extra").mkdir()
    (out / "extra" / "thing.txt").write_text("x", encoding="utf-8")
    assert "extra" in check_out_dir(out)


def test_foreign_file_inside_weeks_refused(tmp_path):
    out = tmp_path / "site"
    _make_snapshot_dir(out)
    (out / "weeks" / "my-notes.txt").write_text("x", encoding="utf-8")
    assert "my-notes.txt" in check_out_dir(out)


def test_refresh_removes_dropped_week_keeps_git_file(tmp_path):
    out = tmp_path / "site"
    _make_snapshot_dir(out, weeks=("2025-11-06", "2025-11-13"))
    (out / "stats").mkdir()
    (out / "stats" / "2023.json").write_text("{}", encoding="utf-8")
    git = out / ".git"
    git.write_text("gitdir: E:/x/.git/worktrees/site\n", encoding="utf-8")
    files = {
        "snapshot.json": b"{}",
        "weeks/2025-11-13.geojson": b"{}",
        "stats/2024.json": b"{}",
        "static/js/a.js": b"x",
    }
    assert write(out, files) == (4, 2)
    assert not (out / "weeks" / "2025-11-06.geojson").exists()
    assert not (out / "stats" / "2023.json").exists()
    assert (out / "weeks" / "2025-11-13.geojson").read_bytes() == b"{}"
    assert (out / "static" / "js" / "a.js").read_bytes() == b"x"
    assert git.read_text(encoding="utf-8") == "gitdir: E:/x/.git/worktrees/site\n"


def test_write_is_byte_stable(tmp_path, conn, world):
    files = _files(conn, world)
    out = tmp_path / "site"
    write(out, files)
    first = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert check_out_dir(out) is None
    write(out, _files(conn, world))
    second = {p.relative_to(out).as_posix(): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert first == second == files


# --- Streets v2: the street paragraph follows STREETS_DRAW (spec 8, pass step 4) ----------------


def test_licenses_drawing_off():
    from outages.snapshot import licenses_text

    text = licenses_text(False, "2026-11-12T02:14:58Z")
    assert "none while street drawing is off" in text
    assert "2026-11-12T02:14:58Z" not in text


def test_licenses_drawing_on():
    from outages.snapshot import licenses_text

    text = licenses_text(True, "2026-11-12T02:14:58Z")
    assert "derived from OpenStreetMap data, © OpenStreetMap contributors" in text
    assert "ODbL" in text and "2026-11-12T02:14:58Z" in text
    assert "weeks/*.geojson" in text and "streets.drawn[].lines" in text
    assert "none while" not in text
    assert "Leaflet" in text and "faeldon/philippines-json-maps" in text  # the rest stays


def test_build_files_licence_follows_draw(conn, world):
    from outages.snapshot import build_files

    on = build_files(conn, world[0], world[1], TAKEN, LAST_CHECK, draw=True, osm_base="B1")
    assert "B1" in on["LICENSES.md"].decode("utf-8")
    off = build_files(conn, world[0], world[1], TAKEN, LAST_CHECK)
    assert "none while street drawing is off" in off["LICENSES.md"].decode("utf-8")


def test_read_osm_base(tmp_path):
    from osm_world import OSM_BASE, write

    from outages.osm.extract import read_osm_base

    assert read_osm_base(tmp_path / "none") is None
    assert read_osm_base(write(tmp_path / "osm")) == OSM_BASE
