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


LATEST = max(n for n, _ in db._migrations())
AREA_TABLES = {"area_parses", "area_barangays", "area_entities", "llm_cache"}


def test_fresh_db_has_schema(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= TABLES | AREA_TABLES
    assert [r[0] for r in conn.execute("SELECT version FROM schema_version")] == list(
        range(1, LATEST + 1)
    )


def test_connect_twice_does_not_reapply(tmp_path):
    p = tmp_path / "o.db"
    db.connect(p).close()
    c = db.connect(p)
    assert c.execute("SELECT count(*) FROM schema_version").fetchone()[0] == LATEST
    c.close()


def _phase1_db(path):
    """A DB at schema version 1 with one post, version and outage (as Phase 1 left it)."""
    sql = (db.resources.files("outages") / "migrations" / "001_init.sql").read_text("utf-8")
    c = sqlite3.connect(path)
    c.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES (1);\nCOMMIT;")
    c.execute("INSERT INTO posts (id, url, slug, first_seen_at) VALUES (1, 'u', 's', 't')")
    c.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    c.execute(
        "INSERT INTO outages (post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
        " VALUES (1, 0, 'h', 's', 'e', 'scheduled', 'p', 'Portion of Cebu City (Tisa)', 'k', 1, 1)"
    )
    c.commit()
    c.close()


def test_migration_002_on_phase1_db(tmp_path):
    p = tmp_path / "o.db"
    _phase1_db(p)
    c = db.connect(p)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= AREA_TABLES
    assert c.execute("SELECT max(version) FROM schema_version").fetchone()[0] == LATEST
    assert c.execute("SELECT areas_hash, areas_raw FROM outages").fetchone()[0] is None
    c.close()


def test_connect_ro_never_migrates_or_writes(tmp_path):
    p = tmp_path / "o.db"
    _phase1_db(p)
    c = db.connect_ro(p)
    assert c.execute("SELECT max(version) FROM schema_version").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        c.execute("DELETE FROM outages")
    c.close()
    with pytest.raises(sqlite3.OperationalError):
        db.connect_ro(tmp_path / "missing.db")


def test_area_rows_cascade(conn):
    conn.execute(
        "INSERT INTO area_parses (areas_hash, layout, towns_json, extractor, parsed_at)"
        " VALUES ('h', 'city_first', '[]', 'rules', 't')"
    )
    conn.execute(
        "INSERT INTO area_barangays (areas_hash, seq, name_raw, match)"
        " VALUES ('h', 0, 'Tisa', 'unmatched')"
    )
    conn.execute(
        "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
        " VALUES ('h', 0, 'street', 'Katipunan St.', 'rules')"
    )
    conn.execute("DELETE FROM area_parses WHERE areas_hash = 'h'")
    assert conn.execute("SELECT count(*) FROM area_barangays").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM area_entities").fetchone()[0] == 0


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


ALERT_TABLES = {"subscribers", "subscriptions", "alerts_sent", "admin_notes_sent"}


def test_migration_003_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert names >= ALERT_TABLES
    assert conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] == LATEST


def _seed_alert_refs(conn):
    conn.execute("INSERT INTO posts (id, url, slug, first_seen_at) VALUES (1, 'u', 's', 't')")
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    conn.execute(
        "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
        " VALUES (7, 1, 0, 'h', 's', 'e', 'scheduled', 'p', 'a', 'k', 1, 1)"
    )
    conn.execute(
        "INSERT INTO outage_changes (id, outage_id, version_id, kind, detected_at)"
        " VALUES (5, 7, 1, 'time_changed', 't')"
    )
    conn.execute("INSERT INTO subscribers (id, chat_id, created_at) VALUES (1, 42, 't')")


def test_alerts_sent_unique_item(conn):
    _seed_alert_refs(conn)
    ins = (
        "INSERT INTO alerts_sent (subscriber_id, outage_id, kind, change_id, sent_at)"
        " VALUES (?, ?, ?, ?, 't')"
    )
    conn.execute(ins, (1, 7, "change", 5))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (1, 7, "change", 5))
    conn.execute(ins, (1, 7, "day_of", None))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, (1, 7, "day_of", None))


def test_connect_sets_busy_timeout(conn):
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_migration_004_tables(conn):
    assert _cols(conn, "street_matches") == {
        "areas_hash",
        "seq",
        "status",
        "osm_name",
        "geometry_json",
        "osm_date",
    }
    assert _cols(conn, "subscribers") >= {"status", "requested_at", "decided_at"}
    assert db._version(conn) == 4


def test_migration_004_keeps_subscribers_approved(tmp_path):
    p = tmp_path / "o.db"
    c = sqlite3.connect(p)
    for n, sql in db._migrations():
        if n <= 3:
            c.executescript(
                f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({n});\nCOMMIT;"
            )
    c.execute("INSERT INTO subscribers (chat_id, created_at) VALUES (42, 't')")
    c.commit()
    c.close()
    c = db.connect(p)
    assert c.execute("SELECT status FROM subscribers WHERE chat_id = 42").fetchone()[0] == (
        "approved"
    )
    c.close()
