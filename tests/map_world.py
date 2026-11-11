"""A tiny world for the map tests: gazetteer, boundaries and DB rows covering Phase 4 spec 7."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from outages.areas.gazetteer import Gazetteer
from outages.timeparse import MANILA

NOW = datetime(2026, 11, 12, 10, 0, tzinfo=MANILA)
P1 = "https://www.visayanelectric.com/blog-posts/p1-interruption"
P2 = "https://www.visayanelectric.com/blog-posts/p2-interruption"

LAHUG, TISA, STO_NINO = "730600040", "730600060", "730600022"
TAYUD_C, TAYUD_L, POBLACION_L = "702219020", "702227030", "702227020"
LINAO, AGUS = "702232010", "731100001"

_BRGYS = [
    (STO_NINO, "Central", "Cebu City", "730600000"),
    (LAHUG, "Lahug", "Cebu City", "730600000"),
    (TISA, "Tisa", "Cebu City", "730600000"),
    (TAYUD_C, "Tayud", "Consolacion", "702219000"),
    (POBLACION_L, "Poblacion", "Liloan", "702227000"),
    (TAYUD_L, "Tayud", "Liloan", "702227000"),
    (LINAO, "Linao", "Minglanilla", "702232000"),
]


def gazetteer() -> Gazetteer:
    return Gazetteer.from_rows(
        [{"psgc_code": c, "name": n, "town": t, "town_psgc": tp} for c, n, t, tp in _BRGYS],
        [{"town": "Cebu City", "alias": "Sto. Niño", "target": STO_NINO}],
        [{"psgc_code": STO_NINO, "name": "Santo Niño"}],
    )


def _square(i: int) -> dict:
    x, y = 123.8 + i / 100, 10.3
    ring = [[x, y], [x + 0.01, y], [x + 0.01, y + 0.01], [x, y + 0.01], [x, y]]
    return {"type": "Polygon", "coordinates": [ring]}


def boundaries() -> dict:
    rows = [(*b, True) for b in _BRGYS] + [(AGUS, "Agus", "Lapu-Lapu City", "731100000", False)]
    feats = [
        {
            "type": "Feature",
            "id": c,
            "properties": {"psgc": c, "name": n, "town": t, "town_psgc": tp, "covered": cov},
            "geometry": _square(i),
        }
        for i, (c, n, t, tp, cov) in enumerate(rows)
    ]
    return {
        "type": "FeatureCollection",
        "attribution": "Boundaries: faeldon/philippines-json-maps v1.1.1 (MIT), PSGC 3Q 2026",
        "features": feats,
    }


# areas_hash -> (towns_json, [(name_raw, town_psgc, psgc_code, match)], [(kind, name)])
PARSES = {
    "hA": (
        ["Cebu City"],
        [
            ("Lahug", "730600000", LAHUG, "exact"),
            ("Tisa", "730600000", TISA, "exact"),
            ("Tisa", "730600000", TISA, "exact"),
        ],
        [("street", "Gorordo Ave."), ("sitio_purok", "Sitio Mabuhay"), ("street", "Salinas Dr.")],
    ),
    "hB": (["Cebu City"], [("Sto. Niño", "730600000", STO_NINO, "alias")], []),
    "hC": (["Cebu City"], [("Lahug", "730600000", LAHUG, "exact")], [("street", "Escario St.")]),
    "hD": (
        ["Consolacion", "Liloan"],
        [
            ("Tayud", "702219000", TAYUD_C, "ambiguous"),
            ("Tayud", "702227000", TAYUD_L, "ambiguous"),
        ],
        [],
    ),
    "hE": (
        ["Minglanilla"],
        [
            ("Linao", "702232000", LINAO, "exact"),
            ("Poblacion", "702227000", POBLACION_L, "other_town"),
        ],
        [("establishment", "Minglanilla Town Hall")],
    ),
    "hF": ([], [("Lipata", None, None, "unmatched")], []),
    "hG": (["Cebu City"], [], [("establishment", "Cebu Business Park")]),
}

# id, post, start, end, status, purpose, areas_hash
OUTAGES = [
    (1, 1, "2026-11-12T08:00", "2026-11-12T17:00", "scheduled", "Line maintenance", "hA"),
    (2, 1, "2026-11-12T08:30", "2026-11-12T12:00", "additional", "Pole transfer", "hB"),
    (3, 1, "2026-11-12T13:00", "2026-11-12T17:00", "cancelled", "Tree trimming", "hA"),
    (4, 1, "2026-11-12T22:00", "2026-11-13T05:00", "scheduled", "Overnight works", "hC"),
    (5, 2, "2026-11-13T08:00", "2026-11-13T17:00", "scheduled", "Substation work", "hD"),
    (6, 1, "2026-11-13T09:00", "2026-11-13T15:00", "scheduled", "Rerouting", "hE"),
    (7, 1, "2026-11-14T08:00", "2026-11-14T17:00", "scheduled", "Line <b>work</b> & tests", "hF"),
    (8, 1, "2026-11-14T10:00", "2026-11-14T14:00", "scheduled", "Not parsed yet", None),
    (9, 1, "2026-11-14T08:00", "2026-11-14T12:00", "removed", "Gone", None),
    (10, 1, "2026-11-20T08:00", "2026-11-20T17:00", "scheduled", "Next week", "hA"),
    (11, 1, "2026-11-14T13:00", "2026-11-14T16:00", "scheduled", "Business park", "hG"),
]


def populate(conn: sqlite3.Connection) -> None:
    """Insert the world's rows into a migrated DB and commit."""
    with conn:
        conn.executemany(
            "INSERT INTO posts (id, url, slug, title, first_seen_at, parse_status)"
            " VALUES (?, ?, ?, ?, 't', ?)",
            [(1, P1, "p1", "Nov 12-20 interruptions", "ok"), (2, P2, "p2", "Nov 13", "failed")],
        )
        conn.executemany(
            "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
            " VALUES (?, ?, 't', 'h', x'00')",
            [(1, 1), (2, 2)],
        )
        conn.executemany(
            "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status,"
            " purpose, areas_raw, areas_key, first_seen_version_id, last_seen_version_id,"
            " areas_hash) VALUES (?, ?, ?, 'h', ?, ?, ?, ?, ?, 'k', ?, ?, ?)",
            [
                (i, p, i, f"{s}:00+08:00", f"{e}:00+08:00", st, pu, f"Areas of {i}", p, p, h)
                for i, p, s, e, st, pu, h in OUTAGES
            ],
        )
        for h, (towns, brgys, ents) in PARSES.items():
            conn.execute(
                "INSERT INTO area_parses (areas_hash, layout, towns_json, extractor, parsed_at)"
                " VALUES (?, 'city_first', ?, 'rules', 't')",
                (h, json.dumps(towns)),
            )
            conn.executemany(
                "INSERT INTO area_barangays (areas_hash, seq, name_raw, town_psgc, psgc_code,"
                " match) VALUES (?, ?, ?, ?, ?, ?)",
                [(h, i, *b) for i, b in enumerate(brgys)],
            )
            conn.executemany(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, ?, ?, ?, 'rules')",
                [(h, i, *e) for i, e in enumerate(ents)],
            )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('last_check_at', ?)",
            ("2026-11-12T00:00:00+00:00",),
        )
