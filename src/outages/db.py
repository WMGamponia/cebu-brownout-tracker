"""SQLite connection, numbered migrations and queries (spec 6).

Functions here never commit: the caller wraps one post's work in `with conn:` so a version,
its outages and its change log land together or not at all (an interrupted backfill must not
leave a stored hash with no outages behind it).
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from outages.extract import ExtractedPost

if TYPE_CHECKING:
    from outages.parse import ParsedOutage
    from outages.upsert import Change


@dataclass(frozen=True)
class StoredOutage:
    id: int
    post_id: int
    seq: int
    heading_text: str
    start_at: str
    end_at: str
    duration_label: str | None
    status: str
    purpose: str
    areas_raw: str
    areas_key: str
    map_image_url: str | None
    warnings: tuple[str, ...]

    def to_json(self) -> dict:
        """The outage as stored, for `outage_changes.old_json`."""
        return {
            "seq": self.seq,
            "heading_text": self.heading_text,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "duration_label": self.duration_label,
            "status": self.status,
            "purpose": self.purpose,
            "areas_raw": self.areas_raw,
            "areas_key": self.areas_key,
            "map_image_url": self.map_image_url,
            "warnings": list(self.warnings),
        }


def _migrations() -> list[tuple[int, str]]:
    files = resources.files("outages") / "migrations"
    out = []
    for f in files.iterdir():
        if f.name.endswith(".sql"):
            out.append((int(f.name.split("_", 1)[0]), f.read_text(encoding="utf-8")))
    return sorted(out)


def _version(conn: sqlite3.Connection) -> int:
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if not has:
        return 0
    row = conn.execute("SELECT max(version) FROM schema_version").fetchone()
    return row[0] or 0


def is_current(conn: sqlite3.Connection) -> bool:
    """True when every migration is applied (works on a read-only connection)."""
    return _version(conn) >= max(n for n, _ in _migrations())


def connect(path: Path) -> sqlite3.Connection:
    """Open (creating dirs), enable foreign keys, apply pending migrations, each atomically.

    `busy_timeout` makes the bot thread and a scheduled run wait for each other's writes
    instead of failing with `database is locked`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    current = _version(conn)
    for n, sql in _migrations():
        if n > current:
            conn.executescript(
                f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({n});\nCOMMIT;"
            )
    return conn


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open an existing DB read-only: no migrations, no writes (dry runs and the eval sampler)."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def upsert_post(conn: sqlite3.Connection, url: str, slug: str, lastmod: date | None) -> int:
    """Insert the post if new (first_seen_at = now, UTC), refresh its lastmod; return its id."""
    lm = lastmod.isoformat() if lastmod else None
    conn.execute(
        "INSERT INTO posts (url, slug, sitemap_lastmod, first_seen_at)"
        " VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'))"
        " ON CONFLICT(url) DO UPDATE SET sitemap_lastmod = coalesce(excluded.sitemap_lastmod,"
        " sitemap_lastmod)",
        (url, slug, lm),
    )
    return conn.execute("SELECT id FROM posts WHERE url = ?", (url,)).fetchone()[0]


def store_version(
    conn: sqlite3.Connection, post_id: int, ex: ExtractedPost, fetched_at: str
) -> int | None:
    """Store a new version and return its id, or None when the text hash is unchanged.

    Always records `last_fetched_at` (spec 6.2).
    """
    current = conn.execute("SELECT text_hash FROM posts WHERE id = ?", (post_id,)).fetchone()[0]
    conn.execute("UPDATE posts SET last_fetched_at = ? WHERE id = ?", (fetched_at, post_id))
    if current == ex.text_hash:
        return None
    cur = conn.execute(
        "INSERT INTO post_versions (post_id, fetched_at, text_hash, content_gz)"
        " VALUES (?, ?, ?, ?)",
        (post_id, fetched_at, ex.text_hash, gzip.compress(ex.content_html.encode("utf-8"))),
    )
    conn.execute(
        "UPDATE posts SET text_hash = ?, title = ? WHERE id = ?", (ex.text_hash, ex.title, post_id)
    )
    return cur.lastrowid


def _stored(row: sqlite3.Row) -> StoredOutage:
    return StoredOutage(
        id=row["id"],
        post_id=row["post_id"],
        seq=row["seq"],
        heading_text=row["heading_text"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        duration_label=row["duration_label"],
        status=row["status"],
        purpose=row["purpose"],
        areas_raw=row["areas_raw"],
        areas_key=row["areas_key"],
        map_image_url=row["map_image_url"],
        warnings=tuple(json.loads(row["warnings_json"])),
    )


def active_outages(conn: sqlite3.Connection, post_id: int) -> list[StoredOutage]:
    """A post's outages that aren't `removed`, in page order."""
    rows = conn.execute(
        "SELECT * FROM outages WHERE post_id = ? AND status != 'removed' ORDER BY seq, id",
        (post_id,),
    )
    return [_stored(r) for r in rows]


def set_parse_status(
    conn: sqlite3.Connection,
    post_id: int,
    status: str,
    error: str | None,
    warnings: tuple[str, ...] | None = None,
) -> None:
    """Record a parse outcome; `warnings` (post-level) replace the stored ones when given."""
    conn.execute(
        "UPDATE posts SET parse_status = ?, parse_error = ? WHERE id = ?", (status, error, post_id)
    )
    if warnings is not None:
        conn.execute(
            "UPDATE posts SET parse_warnings_json = ? WHERE id = ?",
            (json.dumps(list(warnings), ensure_ascii=False), post_id),
        )


def latest_version(conn: sqlite3.Connection, post_id: int) -> tuple[int, str] | None:
    """(version id, content-node HTML) of the post's newest stored version."""
    row = conn.execute(
        "SELECT id, content_gz FROM post_versions WHERE post_id = ? ORDER BY id DESC LIMIT 1",
        (post_id,),
    ).fetchone()
    return (row[0], gzip.decompress(row[1]).decode("utf-8")) if row else None


def _outage_values(n: ParsedOutage) -> tuple:
    return (
        n.seq,
        n.heading_text,
        n.start_at.isoformat(),
        n.end_at.isoformat(),
        n.duration_label,
        n.status,
        n.purpose,
        n.areas_raw,
        n.areas_key,
        n.map_image_url,
        json.dumps(list(n.warnings), ensure_ascii=False),
    )


_COLS = (
    "seq, heading_text, start_at, end_at, duration_label, status, purpose, areas_raw, areas_key,"
    " map_image_url, warnings_json"
)


def apply_changes(
    conn: sqlite3.Connection,
    post_id: int,
    version_id: int,
    changes: list[Change],
    log: bool,
    detected_at: str,
) -> None:
    """Write `upsert.match` results. Matched rows take the new values; `removed` rows are kept
    with that status. `log=False` (a post's first parse) writes no `outage_changes` (spec 6.1)."""
    set_cols = ", ".join(f"{c.strip()} = ?" for c in _COLS.split(","))
    for ch in changes:
        if ch.kind == "added":
            assert ch.new is not None
            cur = conn.execute(
                f"INSERT INTO outages (post_id, {_COLS}, first_seen_version_id,"
                " last_seen_version_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (post_id, *_outage_values(ch.new), version_id, version_id),
            )
            outage_id = cur.lastrowid
        elif ch.kind == "removed":
            assert ch.old is not None
            conn.execute("UPDATE outages SET status = 'removed' WHERE id = ?", (ch.old.id,))
            outage_id = ch.old.id
        else:
            assert ch.old is not None and ch.new is not None
            conn.execute(
                f"UPDATE outages SET {set_cols}, last_seen_version_id = ? WHERE id = ?",
                (*_outage_values(ch.new), version_id, ch.old.id),
            )
            outage_id = ch.old.id
        if log and ch.kind != "unchanged":
            conn.execute(
                "INSERT INTO outage_changes (outage_id, version_id, kind, old_json, new_json,"
                " detected_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    outage_id,
                    version_id,
                    ch.kind,
                    json.dumps(ch.old.to_json(), ensure_ascii=False) if ch.old else None,
                    json.dumps(ch.new.to_json(), ensure_ascii=False) if ch.new else None,
                    detected_at,
                ),
            )


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
