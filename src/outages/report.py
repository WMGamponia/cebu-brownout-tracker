"""Per-post report: dates, outage counts, warnings, failures, cross-post duplicates (spec 7)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ReportRow:
    slug: str
    first_day: str | None
    last_day: str | None
    outages: int
    cancelled: int
    additional: int
    removed: int
    warnings: tuple[str, ...]  # "seq N: …" for outage warnings, plain for post-level ones
    status: str
    error: str | None


@dataclass(frozen=True)
class Duplicate:
    start_at: str
    end_at: str
    areas_key: str
    slugs: tuple[str, ...]


def build(conn: sqlite3.Connection) -> list[ReportRow]:
    """One row per post, oldest outage date first (posts with no outages last)."""
    rows = []
    for p in conn.execute("SELECT * FROM posts").fetchall():
        outs = conn.execute(
            "SELECT seq, start_at, end_at, status, warnings_json FROM outages WHERE post_id = ?"
            " ORDER BY seq",
            (p["id"],),
        ).fetchall()
        active = [o for o in outs if o["status"] != "removed"]
        warnings = list(json.loads(p["parse_warnings_json"]))
        for o in active:
            warnings += [f"seq {o['seq']}: {w}" for w in json.loads(o["warnings_json"])]
        rows.append(
            ReportRow(
                slug=p["slug"],
                first_day=min((o["start_at"][:10] for o in active), default=None),
                last_day=max((o["end_at"][:10] for o in active), default=None),
                outages=len(active),
                cancelled=sum(o["status"] == "cancelled" for o in active),
                additional=sum(o["status"] == "additional" for o in active),
                removed=len(outs) - len(active),
                warnings=tuple(warnings),
                status=p["parse_status"],
                error=p["parse_error"],
            )
        )
    return sorted(rows, key=lambda r: (r.first_day is None, r.first_day or "", r.slug))


def duplicates(conn: sqlite3.Connection) -> list[Duplicate]:
    """Outages with the same start, end and areas in more than one post (spec 6.4)."""
    rows = conn.execute(
        "SELECT o.start_at, o.end_at, o.areas_key, group_concat(DISTINCT p.slug) AS slugs"
        " FROM outages o JOIN posts p ON p.id = o.post_id WHERE o.status != 'removed'"
        " GROUP BY o.start_at, o.end_at, o.areas_key HAVING count(DISTINCT o.post_id) > 1"
        " ORDER BY o.start_at, o.areas_key"
    ).fetchall()
    return [
        Duplicate(r["start_at"], r["end_at"], r["areas_key"], tuple(sorted(r["slugs"].split(","))))
        for r in rows
    ]


def areas_line(conn: sqlite3.Connection) -> str:
    """One line on Phase 2 areas: barangay rows by match kind and the review-queue size."""
    counts = dict(
        conn.execute("SELECT match, count(*) FROM area_barangays GROUP BY match").fetchall()
    )
    queue = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT name_raw, match FROM area_barangays"
        " WHERE match IN ('unmatched', 'other_town', 'ambiguous'))"
    ).fetchone()[0]
    kinds = ("exact", "alias", "ambiguous", "other_town", "unmatched")
    parts = ", ".join(f"{k} {counts.get(k, 0)}" for k in kinds)
    return f"Areas: {sum(counts.values())} barangay rows ({parts}); review queue {queue} names"


def streets_line(conn: sqlite3.Connection) -> str:
    """One line on Phase 5 street matches by status."""
    counts = dict(
        conn.execute("SELECT status, count(*) FROM street_matches GROUP BY status").fetchall()
    )
    if not counts:
        return "streets: no matches yet (run `streets match`)"
    kinds = ("matched", "alias", "not_found", "no_osm_data")
    parts = ", ".join(f"{k} {counts.get(k, 0)}" for k in kinds)
    return f"streets: {sum(counts.values())} mentions ({parts})"


def alerts_line(conn: sqlite3.Connection, now: datetime | None = None) -> str:
    """One line on Phase 3 alerts: active subscribers, ledger rows, unparsed upcoming areas."""
    from outages.alerts.run import unparsed_upcoming

    subs = conn.execute("SELECT count(*) FROM subscribers WHERE active = 1").fetchone()[0]
    sent = conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0]
    unparsed = unparsed_upcoming(conn, now or datetime.now(UTC))
    return f"alerts: {subs} subscribers, {sent} sent; unparsed areas: {unparsed} upcoming outages"


def format_table(rows: list[ReportRow], dups: list[Duplicate], areas: str | None = None) -> str:
    """Plain-text report: totals, the per-post table, failures, warnings, duplicates."""
    w = max([len(r.slug) for r in rows] + [4])
    out = [
        f"Posts: {len(rows)}  ok: {sum(r.status == 'ok' for r in rows)}  "
        f"failed: {sum(r.status == 'failed' for r in rows)}  "
        f"pending: {sum(r.status == 'pending' for r in rows)}",
        f"Outages: {sum(r.outages for r in rows)}  cancelled: {sum(r.cancelled for r in rows)}  "
        f"additional: {sum(r.additional for r in rows)}  removed: {sum(r.removed for r in rows)}  "
        f"warnings: {sum(len(r.warnings) for r in rows)}",
        *([areas] if areas else []),
        "",
        f"{'slug':<{w}}  {'from':10}  {'to':10}  {'out':>3}  {'can':>3}  {'add':>3}  {'warn':>4}  "
        "status",
    ]
    for r in rows:
        out.append(
            f"{r.slug:<{w}}  {r.first_day or '-':10}  {r.last_day or '-':10}  {r.outages:>3}  "
            f"{r.cancelled:>3}  {r.additional:>3}  {len(r.warnings):>4}  {r.status}"
        )
    failed = [r for r in rows if r.status == "failed"]
    out += ["", f"Failures ({len(failed)})"]
    out += [f"  {r.slug}: {r.error}" for r in failed] or ["  none"]
    warned = [r for r in rows if r.warnings]
    out += ["", f"Warnings ({sum(len(r.warnings) for r in warned)})"]
    out += [f"  {r.slug}: {w_}" for r in warned for w_ in r.warnings] or ["  none"]
    out += ["", f"Cross-post duplicates ({len(dups)})"]
    out += [
        f"  {d.start_at} -> {d.end_at}  {d.areas_key[:60]}\n    in: {', '.join(d.slugs)}"
        for d in dups
    ] or ["  none"]
    return "\n".join(out) + "\n"
