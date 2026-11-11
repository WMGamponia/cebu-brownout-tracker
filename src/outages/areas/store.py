"""The `areas` step: parse each distinct `areas_raw` once, keyed by its SHA-256, into
`area_parses`, `area_barangays` and `area_entities` (spec 5, 6).

Every hash is its own transaction, so a run stopped halfway (Ctrl+C, a crashed model, a PC that
went to sleep) resumes where it stopped. A dry run reads `outages` only and writes nothing, so
it works on a read-only connection to a database that hasn't been migrated yet.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from outages.areas.entities import Entity, Extractor
from outages.areas.gazetteer import BarangayMatch, Gazetteer
from outages.areas.segment import Segmented, segment

REVIEW_MATCHES = ("unmatched", "other_town", "ambiguous")


def areas_hash(areas_raw: str) -> str:
    return hashlib.sha256(areas_raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Parsed:
    seg: Segmented
    matches: list[BarangayMatch]
    entities: list[tuple[Entity, str]]  # (entity, extractor that produced it)


def parse_one(areas_raw: str, gaz: Gazetteer, extractor: Extractor) -> Parsed:
    """Segment, resolve barangays (rules always), extract entities with `extractor`.

    Names an alias marks `not_barangay` become `other` entities tagged `gazetteer`."""
    seg = segment(areas_raw)
    matches, not_brgy = gaz.resolve(seg)
    ents = [(Entity("other", n), "gazetteer") for n in not_brgy]
    found = extractor.extract(seg)
    tag = getattr(extractor, "last_extractor", None) or extractor.name
    return Parsed(seg, matches, ents + [(e, tag) for e in found])


@dataclass
class AreasSummary:
    parsed: int = 0
    deleted: int = 0
    hashes: int = 0
    layouts: dict[str, int] = field(default_factory=dict)
    matches: dict[str, int] = field(default_factory=dict)
    extractors: dict[str, int] = field(default_factory=dict)
    warnings: dict[str, int] = field(default_factory=dict)


def _utc(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def refresh_hashes(conn: sqlite3.Connection) -> int:
    """Set `outages.areas_hash` for every non-`removed` outage; return how many changed."""
    rows = conn.execute(
        "SELECT id, areas_raw, areas_hash FROM outages WHERE status != 'removed'"
    ).fetchall()
    todo = [(areas_hash(r[1]), r[0]) for r in rows if areas_hash(r[1]) != r[2]]
    conn.executemany("UPDATE outages SET areas_hash = ? WHERE id = ?", todo)
    return len(todo)


def pending_hashes(conn: sqlite3.Connection, rebuild: bool) -> list[tuple[str, str]]:
    """(hash, areas_raw) to parse: hashes with no parse row, or all with `rebuild`."""
    where = (
        ""
        if rebuild
        else (" AND NOT EXISTS (SELECT 1 FROM area_parses a WHERE a.areas_hash = o.areas_hash)")
    )
    return [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT o.areas_hash, min(o.areas_raw) FROM outages o"
            " WHERE o.status != 'removed' AND o.areas_hash IS NOT NULL"
            + where
            + " GROUP BY o.areas_hash ORDER BY min(o.id)"
        )
    ]


def write_parse(conn: sqlite3.Connection, h: str, p: Parsed, extractor: str, now: datetime) -> None:
    """Replace one hash's rows. The caller wraps it in a transaction."""
    seg = p.seg
    conn.execute("DELETE FROM area_parses WHERE areas_hash = ?", (h,))
    conn.execute(
        "INSERT INTO area_parses (areas_hash, layout, towns_json, warnings_json, extractor,"
        " parsed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            h,
            seg.layout,
            json.dumps(seg.towns, ensure_ascii=False),
            json.dumps(seg.warnings, ensure_ascii=False),
            extractor,
            _utc(now),
        ),
    )
    conn.executemany(
        "INSERT INTO area_barangays (areas_hash, seq, name_raw, town_psgc, psgc_code, match)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [(h, i, m.name_raw, m.town_psgc, m.psgc_code, m.match) for i, m in enumerate(p.matches)],
    )
    conn.executemany(
        "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor) VALUES (?, ?, ?, ?, ?)",
        [(h, i, e.kind, e.name, tag) for i, (e, tag) in enumerate(p.entities)],
    )


def delete_orphans(conn: sqlite3.Connection) -> int:
    """Delete parses no non-`removed` outage uses any more (rows cascade)."""
    return conn.execute(
        "DELETE FROM area_parses WHERE areas_hash NOT IN (SELECT areas_hash FROM outages"
        " WHERE status != 'removed' AND areas_hash IS NOT NULL)"
    ).rowcount


def _count(rows) -> dict[str, int]:
    return dict(Counter(dict((r[0], r[1]) for r in rows)))


def _stored_summary(conn: sqlite3.Connection, s: AreasSummary) -> AreasSummary:
    s.hashes = conn.execute("SELECT count(*) FROM area_parses").fetchone()[0]
    s.layouts = _count(conn.execute("SELECT layout, count(*) FROM area_parses GROUP BY 1"))
    s.matches = _count(conn.execute("SELECT match, count(*) FROM area_barangays GROUP BY 1"))
    s.extractors = _count(conn.execute("SELECT extractor, count(*) FROM area_entities GROUP BY 1"))
    warn: Counter[str] = Counter()
    for (w,) in conn.execute("SELECT warnings_json FROM area_parses"):
        warn.update(_warning_kind(x) for x in json.loads(w))
    s.warnings = dict(warn)
    return s


def _warning_kind(w: str) -> str:
    return w.split(":", 1)[0]


def _dry_run(conn: sqlite3.Connection, extractor: Extractor, gaz: Gazetteer) -> AreasSummary:
    texts = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT areas_raw FROM outages WHERE status != 'removed' ORDER BY 1"
        )
    ]
    lay: Counter[str] = Counter()
    mat: Counter[str] = Counter()
    ext: Counter[str] = Counter()
    warn: Counter[str] = Counter()
    for t in texts:
        p = parse_one(t, gaz, extractor)
        lay[p.seg.layout] += 1
        mat.update(m.match for m in p.matches)
        ext.update(tag for _, tag in p.entities)
        warn.update(_warning_kind(w) for w in p.seg.warnings)
    return AreasSummary(len(texts), 0, len(texts), dict(lay), dict(mat), dict(ext), dict(warn))


def run_areas(
    conn: sqlite3.Connection,
    extractor: Extractor,
    gaz: Gazetteer,
    now: datetime,
    dry_run: bool = False,
    rebuild: bool = False,
) -> AreasSummary:
    """Refresh hashes, parse pending hashes (all with `rebuild`), delete orphans."""
    if dry_run:
        return _dry_run(conn, extractor, gaz)
    with conn:
        refresh_hashes(conn)
    summary = AreasSummary()
    for h, raw in pending_hashes(conn, rebuild):
        p = parse_one(raw, gaz, extractor)
        with conn:
            write_parse(conn, h, p, extractor.name, now)
        summary.parsed += 1
    with conn:
        summary.deleted = delete_orphans(conn)
    return _stored_summary(conn, summary)


def format_summary(s: AreasSummary, dry_run: bool) -> str:
    def line(name: str, d: dict[str, int]) -> str:
        parts = ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))
        return f"  {name}: {parts or 'none'}"

    head = (
        f"areas (dry run, nothing written): {s.parsed} distinct texts parsed"
        if dry_run
        else f"areas: parsed {s.parsed}, deleted {s.deleted} orphans, {s.hashes} stored"
    )
    out = [
        head,
        line("layouts", s.layouts),
        line("barangay rows", s.matches),
        line("entities by extractor", s.extractors),
        line("warnings", s.warnings),
    ]
    return "\n".join(out) + "\n"


@dataclass(frozen=True)
class ReviewItem:
    name_raw: str
    match: str  # unmatched | other_town | ambiguous | layout other | no town
    count: int  # non-removed outages using it
    example_slug: str
    example_seq: int
    suggestions: tuple[str, ...]


def _occurrences(conn: sqlite3.Connection) -> dict[str, tuple[int, str, int, str]]:
    """hash -> (outage count, example slug, example seq, areas_raw)."""
    occ: dict[str, tuple[int, str, int, str]] = {}
    for raw, slug, seq in conn.execute(
        "SELECT o.areas_raw, p.slug, o.seq FROM outages o JOIN posts p ON p.id = o.post_id"
        " WHERE o.status != 'removed' ORDER BY o.id"
    ):
        h = areas_hash(raw)
        n, s, q, _ = occ.get(h, (0, slug, seq, raw))
        occ[h] = (n + 1, s, q, raw)
    return occ


def review_queue(
    conn: sqlite3.Connection, gaz: Gazetteer, dry_run: bool = False
) -> list[ReviewItem]:
    """Unresolved barangay names, `other` layouts and town-less texts, most frequent first.

    From the stored rows, or (`dry_run`) from an in-memory parse that writes nothing."""
    occ = _occurrences(conn)
    records: list[tuple[str, str, list[str], list[tuple[str, str]]]] = []
    if dry_run:
        for h, (_, _, _, raw) in occ.items():
            seg = segment(raw)
            matches, _ = gaz.resolve(seg)
            records.append((h, seg.layout, seg.warnings, [(m.name_raw, m.match) for m in matches]))
    else:
        brgys: dict[str, list[tuple[str, str]]] = {}
        for h, name, match in conn.execute(
            "SELECT areas_hash, name_raw, match FROM area_barangays ORDER BY areas_hash, seq"
        ):
            brgys.setdefault(h, []).append((name, match))
        for h, layout, warns in conn.execute(
            "SELECT areas_hash, layout, warnings_json FROM area_parses"
        ):
            if h in occ:
                records.append((h, layout, json.loads(warns), brgys.get(h, [])))
    agg: dict[tuple[str, str], list] = {}
    for h, layout, warns, matches in records:
        n, slug, seq, raw = occ[h]
        keys = {(name, m) for name, m in matches if m in REVIEW_MATCHES}
        if layout == "other":
            keys.add((" ".join(raw.split())[:100], "layout other"))
        if "no town found" in warns:
            keys.add((" ".join(raw.split())[:100], "no town"))
        for k in keys:
            cur = agg.setdefault(k, [0, slug, seq])
            cur[0] += n
    order = {m: i for i, m in enumerate((*REVIEW_MATCHES, "layout other", "no town"))}
    items = [
        ReviewItem(
            name,
            m,
            v[0],
            v[1],
            v[2],
            tuple(gaz.suggest(name)) if m in ("unmatched", "other_town") else (),
        )
        for (name, m), v in agg.items()
    ]
    return sorted(items, key=lambda i: (order[i.match], -i.count, i.name_raw))


def format_review(items: list[ReviewItem]) -> str:
    out = [f"# Areas review queue ({len(items)})", ""]
    if not items:
        out.append("Empty.")
    for i in items:
        sug = f" -> {', '.join(i.suggestions)}" if i.suggestions else ""
        out.append(
            f"- **{i.name_raw}** ({i.match}, {i.count} outage{'s' * (i.count != 1)};"
            f" e.g. {i.example_slug} #{i.example_seq}){sug}"
        )
    return "\n".join(out) + "\n"
