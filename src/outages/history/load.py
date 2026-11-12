"""Read-only SQL for history stats: the outages overlapping a range, with their placements."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from outages.history.stats import Coverage, OutageRow, Range
from outages.map.window import iso
from outages.timeparse import MANILA

PLACED = ("exact", "alias", "ambiguous")


def load_rows(conn: sqlite3.Connection, rng: Range) -> tuple[list[OutageRow], Coverage]:
    outages = conn.execute(
        "SELECT o.id, o.start_at, o.end_at, o.status, o.areas_hash, p.parse_status"
        " FROM outages o JOIN posts p ON p.id = o.post_id"
        " WHERE o.start_at < ? AND o.end_at > ? ORDER BY o.start_at, o.id",
        (iso(rng.end), iso(rng.start)),
    ).fetchall()
    hashes = sorted({r["areas_hash"] for r in outages if r["areas_hash"]})
    parsed: set[str] = set()
    brgys: dict[str, list[tuple[str, str | None]]] = {}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        marks = ",".join("?" * len(chunk))
        parsed |= {
            r[0]
            for r in conn.execute(
                f"SELECT areas_hash FROM area_parses WHERE areas_hash IN ({marks})", chunk
            )
        }
        for h, match, psgc in conn.execute(
            f"SELECT areas_hash, match, psgc_code FROM area_barangays WHERE areas_hash IN"
            f" ({marks}) ORDER BY areas_hash, seq",
            chunk,
        ):
            brgys.setdefault(h, []).append((match, psgc))
    rows = []
    for r in outages:
        h = r["areas_hash"]
        placed: list[tuple[str, bool]] = []
        reason = None
        if h is None or h not in parsed:
            reason = "not_parsed"
        else:
            items = brgys.get(h, [])
            for match, psgc in items:
                if match in PLACED and psgc and psgc not in [p for p, _ in placed]:
                    placed.append((psgc, match == "ambiguous"))
            if not placed:
                matches = {m for m, _ in items}
                reason = next(
                    (x for x in ("other_town", "unmatched") if x in matches), "no_barangay"
                )
        rows.append(
            OutageRow(
                id=r["id"],
                start=datetime.fromisoformat(r["start_at"]),
                end=datetime.fromisoformat(r["end_at"]),
                status=r["status"],
                stale=r["parse_status"] == "failed",
                placed=placed,
                left_out_reason=None if placed else reason,
            )
        )
    first = conn.execute("SELECT min(start_at) FROM outages WHERE status != 'removed'").fetchone()[
        0
    ]
    posts, ok = conn.execute(
        "SELECT count(*), coalesce(sum(parse_status = 'ok'), 0) FROM posts"
    ).fetchone()
    first_day = (
        datetime.fromisoformat(first).astimezone(MANILA).date().isoformat() if first else None
    )
    return rows, Coverage(first_day, posts, ok)
