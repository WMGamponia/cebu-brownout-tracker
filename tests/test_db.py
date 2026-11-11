"""SQLite schema, migrations and post versions (spec 6.1, 6.2)."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import date

import pytest

from outages import db
from outages.extract import ExtractedPost, Line

TABLES = {"schema_version", "meta", "posts", "post_versions", "outages", "outage_changes"}


def _ex(h: str = "h1", html: str = "<section>Niño</section>") -> ExtractedPost:
    return ExtractedPost("T", (Line("x", False, ()),), html, h)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "sub" / "o.db")
    yield c
    c.close()


def test_fresh_db_has_schema(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= TABLES
    assert [r[0] for r in conn.execute("SELECT version FROM schema_version")] == [1]


def test_connect_twice_does_not_reapply(tmp_path):
    p = tmp_path / "o.db"
    db.connect(p).close()
    c = db.connect(p)
    assert c.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 1
    c.close()


def test_upsert_post_is_idempotent(conn):
    a = db.upsert_post(conn, "https://x/post/a", "a", date(2026, 11, 10))
    b = db.upsert_post(conn, "https://x/post/a", "a", date(2026, 11, 11))
    assert a == b
    row = conn.execute("SELECT sitemap_lastmod, parse_status FROM posts").fetchone()
    assert tuple(row) == ("2026-11-11", "pending")


def test_store_version_dedupes_by_hash(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    v1 = db.store_version(conn, pid, _ex("h1"), "2026-11-10T00:00:00+00:00")
    assert v1 is not None
    assert db.store_version(conn, pid, _ex("h1"), "2026-11-10T01:00:00+00:00") is None
    v2 = db.store_version(conn, pid, _ex("h2"), "2026-11-10T02:00:00+00:00")
    assert v2 not in (None, v1)
    row = conn.execute("SELECT last_fetched_at, text_hash, title FROM posts").fetchone()
    assert tuple(row) == ("2026-11-10T02:00:00+00:00", "h2", "T")
    assert conn.execute("SELECT count(*) FROM post_versions").fetchone()[0] == 2


def test_unchanged_fetch_updates_last_fetched(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    db.store_version(conn, pid, _ex("h1"), "2026-11-10T00:00:00+00:00")
    db.store_version(conn, pid, _ex("h1"), "2026-11-10T05:00:00+00:00")
    assert (
        conn.execute("SELECT last_fetched_at FROM posts").fetchone()[0].startswith("2026-11-10T05")
    )


def test_content_gz_round_trips(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    ex = _ex()
    vid = db.store_version(conn, pid, ex, "2026-11-10T00:00:00+00:00")
    blob = conn.execute("SELECT content_gz FROM post_versions WHERE id=?", (vid,)).fetchone()[0]
    assert gzip.decompress(blob).decode("utf-8") == ex.content_html


def test_status_check_constraint(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    vid = db.store_version(conn, pid, _ex(), "2026-11-10T00:00:00+00:00")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO outages (post_id, seq, heading_text, start_at, end_at, status, purpose,"
            " areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
            " VALUES (?, 0, 'h', 's', 'e', 'postponed', 'p', 'a', 'a', ?, ?)",
            (pid, vid, vid),
        )


def test_parse_status_and_meta(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    db.set_parse_status(conn, pid, "failed", "no heading")
    row = conn.execute("SELECT parse_status, parse_error FROM posts").fetchone()
    assert tuple(row) == ("failed", "no heading")
    assert db.get_meta(conn, "last_check_at") is None
    db.set_meta(conn, "last_check_at", "2026-11-10T00:00:00+00:00")
    db.set_meta(conn, "last_check_at", "2026-11-11T00:00:00+00:00")
    assert db.get_meta(conn, "last_check_at") == "2026-11-11T00:00:00+00:00"


def test_active_outages_empty(conn):
    pid = db.upsert_post(conn, "https://x/post/a", "a", None)
    assert db.active_outages(conn, pid) == []
