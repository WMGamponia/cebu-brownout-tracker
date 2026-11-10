"""discover -> fetch -> store version -> extract -> parse -> upsert (spec 6.2, 7).

Each post is one transaction: its version, outages, change log and parse status are committed
together, so an interrupted backfill resumes cleanly (posts without a version are refetched).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from outages import db
from outages.config import SITEMAP_URL
from outages.discover import PostRef, parse_sitemap
from outages.extract import ExtractedPost, ExtractError, extract_post
from outages.fetch import Fetcher
from outages.parse import parse_post
from outages.timeparse import MANILA
from outages.upsert import match

log = logging.getLogger(__name__)

Outcome = Literal["new", "changed", "unchanged", "failed"]


@dataclass(frozen=True)
class CheckSummary:
    fetched: int = 0
    new: int = 0
    changed: int = 0
    failed: int = 0
    errors: int = 0

    def add(self, outcome: Outcome | None) -> CheckSummary:
        """Count one post: an outcome after a fetch, or None for a fetch error."""
        if outcome is None:
            return CheckSummary(self.fetched, self.new, self.changed, self.failed, self.errors + 1)
        return CheckSummary(
            self.fetched + 1,
            self.new + (outcome == "new"),
            self.changed + (outcome == "changed"),
            self.failed + (outcome == "failed"),
            self.errors,
        )


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def _parse_and_apply(
    conn: sqlite3.Connection,
    post_id: int,
    version_id: int,
    ex: ExtractedPost,
    now: datetime,
    log_changes: bool,
) -> bool:
    """Parse and upsert one stored version; False when parsing failed, which leaves the
    stored outages untouched (spec 6.2). A post's first outages are never logged (6.1)."""
    result = parse_post(ex)
    if result.error:
        log.warning("post %s: parse failed: %s", post_id, result.error)
        db.set_parse_status(conn, post_id, "failed", result.error, result.warnings)
        return False
    old = db.active_outages(conn, post_id)
    changes = match(old, list(result.outages))
    db.apply_changes(conn, post_id, version_id, changes, log_changes and bool(old), _utc(now))
    db.set_parse_status(conn, post_id, "ok", None, result.warnings)
    return True


def process_post(conn: sqlite3.Connection, ref: PostRef, html: str, now: datetime) -> Outcome:
    """Store a fetched post and, if its text changed, parse and upsert it."""
    with conn:
        post_id = db.upsert_post(conn, ref.url, ref.slug, ref.lastmod)
        try:
            ex = extract_post(html)
        except ExtractError as e:
            log.warning("%s: %s", ref.slug, e)
            db.set_parse_status(conn, post_id, "failed", str(e))
            return "failed"
        had_version = db.latest_version(conn, post_id) is not None
        version_id = db.store_version(conn, post_id, ex, _utc(now))
        if version_id is None:
            return "unchanged"
        if not _parse_and_apply(conn, post_id, version_id, ex, now, log_changes=True):
            return "failed"
        return "changed" if had_version else "new"


def _fetch_and_process(
    conn: sqlite3.Connection, fetcher: Fetcher, refs: list[PostRef], now: datetime
) -> CheckSummary:
    summary = CheckSummary()
    for i, ref in enumerate(refs, 1):
        html = fetcher.get(ref.url)
        if html is None:
            log.error("%s: fetch failed; left as is", ref.slug)
            summary = summary.add(None)
            continue
        outcome = process_post(conn, ref, html, now)
        log.info("[%d/%d] %s: %s", i, len(refs), ref.slug, outcome)
        summary = summary.add(outcome)
    return summary


def _sitemap(fetcher: Fetcher) -> list[PostRef] | None:
    xml = fetcher.get(SITEMAP_URL)
    if xml is None:
        log.error("sitemap fetch failed")
        return None
    return parse_sitemap(xml)


def backfill(conn: sqlite3.Connection, fetcher: Fetcher, now: datetime) -> CheckSummary:
    """Fetch every interruption post in the sitemap that has no stored version (resumable)."""
    refs = _sitemap(fetcher)
    if refs is None:
        return CheckSummary(errors=1)
    stored = {
        r[0]
        for r in conn.execute(
            "SELECT p.url FROM posts p WHERE EXISTS"
            " (SELECT 1 FROM post_versions v WHERE v.post_id = p.id)"
        )
    }
    todo = [r for r in refs if r.url not in stored]
    log.info("backfill: %d posts in sitemap, %d to fetch", len(refs), len(todo))
    return _fetch_and_process(conn, fetcher, todo, now)


def check(conn: sqlite3.Connection, fetcher: Fetcher, now: datetime) -> CheckSummary:
    """Fetch posts that are new, have a newer lastmod, or have outages ending today or later."""
    refs = _sitemap(fetcher)
    if refs is None:
        return CheckSummary(errors=1)
    today = now.astimezone(MANILA).date().isoformat()
    known = {
        r["url"]: r["sitemap_lastmod"]
        for r in conn.execute(
            "SELECT url, sitemap_lastmod FROM posts p WHERE EXISTS"
            " (SELECT 1 FROM post_versions v WHERE v.post_id = p.id)"
        )
    }
    current = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT p.url FROM outages o JOIN posts p ON p.id = o.post_id"
            " WHERE o.status != 'removed' AND substr(o.end_at, 1, 10) >= ?",
            (today,),
        )
    }
    todo = [
        r
        for r in refs
        if r.url not in known
        or (r.lastmod and (known[r.url] is None or r.lastmod.isoformat() > known[r.url]))
        or r.url in current
    ]
    log.info("check: %d posts to fetch", len(todo))
    summary = _fetch_and_process(conn, fetcher, todo, now)
    with conn:
        db.set_meta(conn, "last_check_at", _utc(now))
    return summary


def _page(title: str | None, content_html: str) -> str:
    """Rebuild a page `extract_post` accepts from a stored content node (no network)."""
    t = f'<h1 data-hook="post-title">{title}</h1>' if title else ""
    return f"<html><body><article>{t}{content_html}</article></body></html>"


def reparse(conn: sqlite3.Connection, now: datetime) -> CheckSummary:
    """Re-run parse + upsert on each post's latest stored version.

    Changes are logged only when that version is newer than the one the outages came from (a
    version that failed before and parses now): a parser fix on the same version is not news.
    """
    summary = CheckSummary()
    for row in conn.execute("SELECT id, slug, title FROM posts ORDER BY id").fetchall():
        post_id = row["id"]
        with conn:
            latest = db.latest_version(conn, post_id)
            if latest is None:
                continue
            version_id, content_html = latest
            ex = extract_post(_page(row["title"], content_html))
            seen = conn.execute(
                "SELECT max(last_seen_version_id) FROM outages WHERE post_id = ?", (post_id,)
            ).fetchone()[0]
            newer = seen is not None and seen < version_id
            before = [o.to_json() for o in db.active_outages(conn, post_id)]
            ok = _parse_and_apply(conn, post_id, version_id, ex, now, log_changes=newer)
            after = [o.to_json() for o in db.active_outages(conn, post_id)]
        summary = CheckSummary(
            summary.fetched,
            summary.new,
            summary.changed + (ok and before != after),
            summary.failed + (not ok),
        )
        log.info("reparse %s: %s", row["slug"], "ok" if ok else "failed")
    return summary
