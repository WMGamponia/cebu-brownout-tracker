"""The map document: every barangay boundary plus the outages in a window (Phase 4 spec 4.3).

One self-contained GeoJSON FeatureCollection with top-level `outages` and `meta` members. Only
reads the DB, one query per concern. An outage the map can't place is never dropped: it carries
`unplaced` items, which the page lists under "Not on the map".
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from outages.areas.gazetteer import Gazetteer
from outages.config import STREETS_DRAW
from outages.gazetteer_build import BOUNDARIES_PATH
from outages.map.window import Window, iso
from outages.timeparse import MANILA

SCHEMA = 3
SHADED = ("exact", "alias", "ambiguous")
DRAWN = ("matched", "alias")
ODBL = "Streets © OpenStreetMap contributors (ODbL)"
ENTITY_KINDS = ("street", "sitio_purok", "subdivision", "establishment", "other")


class BoundariesError(Exception):
    pass


def load_boundaries(path: Path = BOUNDARIES_PATH) -> dict:
    """The committed boundaries; raises `BoundariesError` unless it is a usable collection."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BoundariesError(f"can't read boundaries {path}: {e}") from None
    if not isinstance(doc, dict) or doc.get("type") != "FeatureCollection":
        raise BoundariesError(f"{path} is not a FeatureCollection")
    feats = doc.get("features")
    if not isinstance(feats, list) or not feats:
        raise BoundariesError(f"{path} has no features")
    if not all(
        isinstance(f, dict)
        and isinstance(f.get("properties"), dict)
        and f["properties"].get("psgc")
        for f in feats
    ):
        raise BoundariesError(f"{path} has a feature without properties.psgc")
    return doc


def schema_ready(conn: sqlite3.Connection) -> bool:
    """True when the DB has the Phase 3 schema (or later); works read-only."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return (
        bool(has)
        and (conn.execute("SELECT max(version) FROM schema_version").fetchone()[0] or 0) >= SCHEMA
    )


def dumps(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


def _manila(utc_iso: str | None) -> str | None:
    return datetime.fromisoformat(utc_iso).astimezone(MANILA).isoformat() if utc_iso else None


def _in(n: int) -> str:
    return ",".join("?" * n)


def _outage_rows(conn: sqlite3.Connection, w: Window) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT o.id, o.start_at, o.end_at, o.status, o.purpose, o.areas_raw, o.areas_hash,"
        " p.url, p.title, p.parse_status FROM outages o JOIN posts p ON p.id = o.post_id"
        " WHERE o.status != 'removed' AND o.start_at < ? AND o.end_at > ?"
        " ORDER BY o.start_at, o.id",
        (iso(w.end), iso(w.start)),
    ).fetchall()


def _parses(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, list[str]]:
    rows = conn.execute(
        f"SELECT areas_hash, towns_json FROM area_parses WHERE areas_hash IN ({_in(len(hashes))})",
        hashes,
    ).fetchall()
    return {r[0]: json.loads(r[1]) for r in rows}


def _barangay_rows(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, list[sqlite3.Row]]:
    out: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        "SELECT areas_hash, seq, name_raw, psgc_code, match FROM area_barangays"
        f" WHERE areas_hash IN ({_in(len(hashes))}) ORDER BY areas_hash, seq",
        hashes,
    ):
        out.setdefault(r[0], []).append(r)
    return out


def _entities(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    for h, kind, name in conn.execute(
        "SELECT areas_hash, kind, name FROM area_entities"
        f" WHERE areas_hash IN ({_in(len(hashes))}) ORDER BY areas_hash, seq",
        hashes,
    ):
        out.setdefault(h, {k: [] for k in ENTITY_KINDS})[kind].append(name)
    return out


def _street_rows(
    conn: sqlite3.Connection, hashes: list[str]
) -> dict[tuple[str, int], sqlite3.Row] | None:
    """street_matches rows of these texts; None on a DB without the table (schema 3)."""
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='street_matches'"
    ).fetchone()
    if not has:
        return None
    return {
        (r["areas_hash"], r["seq"]): r
        for r in conn.execute(
            "SELECT areas_hash, seq, status, osm_name, geometry_json, osm_date FROM street_matches"
            f" WHERE areas_hash IN ({_in(len(hashes))})",
            hashes,
        )
    }


def _street_entities(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for h, seq, name in conn.execute(
        "SELECT areas_hash, seq, name FROM area_entities WHERE kind = 'street'"
        f" AND areas_hash IN ({_in(len(hashes))}) ORDER BY areas_hash, seq",
        hashes,
    ):
        out.setdefault(h, []).append((seq, name))
    return out


def _streets(
    h: str | None,
    ents: dict[str, list[tuple]],
    matches: dict[tuple[str, int], sqlite3.Row] | None,
    draw: bool,
) -> dict:
    """An outage's streets (Phase 5 spec 4.6), in VECO's order."""
    drawn: list[dict] = []
    not_drawn: list[dict] = []
    for seq, name in ents.get(h, []) if h else []:
        if not draw:
            not_drawn.append({"name": name, "why": "drawing_off"})
            continue
        if matches is None:
            not_drawn.append({"name": name, "why": "no_osm_data"})
            continue
        m = matches.get((h, seq))
        if m is None:
            not_drawn.append({"name": name, "why": "not_processed"})
        elif m["status"] in DRAWN and m["geometry_json"]:
            lines = json.loads(m["geometry_json"])
            drawn.append({"name": name, "osm_name": m["osm_name"], "lines": lines})
        else:
            why = m["status"] if m["status"] in ("not_found", "no_osm_data") else "not_found"
            not_drawn.append({"name": name, "why": why})
    return {"drawn": drawn, "not_drawn": not_drawn}


def _areas(rows: list[sqlite3.Row], towns: list[str], gaz: Gazetteer) -> tuple[list, list]:
    """(shaded barangays sorted by psgc, unplaced items in VECO's order) for one parse."""
    shaded: dict[str, dict] = {}
    unplaced: dict[tuple[str, str], dict] = {}
    for r in rows:
        place = gaz.place(r["psgc_code"]) if r["psgc_code"] else None
        if r["match"] in SHADED and place is not None:
            if place.code in shaded:
                continue
            others = sorted(
                {
                    p.town
                    for x in rows
                    if x["match"] == "ambiguous" and x["name_raw"] == r["name_raw"]
                    for p in [gaz.place(x["psgc_code"])]
                    if p is not None and p.town != place.town
                }
            )
            shaded[place.code] = {
                "psgc": place.code,
                "name": place.name,
                "town": place.town,
                "veco_name": r["name_raw"],
                "ambiguous_with": others if r["match"] == "ambiguous" else [],
            }
        else:
            why = "other_town" if r["match"] == "other_town" else "unmatched"
            unplaced.setdefault(
                (r["name_raw"], why), {"name": r["name_raw"], "towns": towns, "why": why}
            )
    items = list(unplaced.values())
    if not shaded and not items:
        items = [{"name": None, "towns": towns, "why": "no_barangay"}]
    return [shaded[k] for k in sorted(shaded)], items


def build(
    conn: sqlite3.Connection,
    gaz: Gazetteer,
    boundaries: dict,
    window: Window,
    now: datetime,
    draw: bool = STREETS_DRAW,
) -> dict:
    rows = _outage_rows(conn, window)
    hashes = sorted({r["areas_hash"] for r in rows if r["areas_hash"]})
    parses = _parses(conn, hashes)
    brgy_rows = _barangay_rows(conn, hashes)
    entities = _entities(conn, hashes)
    street_ents = _street_entities(conn, hashes)
    matches = _street_rows(conn, hashes)
    any_drawn = False

    outages: dict[str, dict] = {}
    by_psgc: dict[str, set[int]] = {}
    stale_posts: dict[str, dict] = {}
    for r in rows:
        h = r["areas_hash"]
        if h in parses:
            barangays, unplaced = _areas(brgy_rows.get(h, []), parses[h], gaz)
        else:
            barangays, unplaced = [], [{"name": None, "towns": [], "why": "not_parsed"}]
        for b in barangays:
            by_psgc.setdefault(b["psgc"], set()).add(r["id"])
        post = {"url": r["url"], "title": r["title"]}
        stale = r["parse_status"] == "failed"
        if stale:
            stale_posts.setdefault(r["url"], post)
        outages[str(r["id"])] = {
            "start": r["start_at"],
            "end": r["end_at"],
            "status": r["status"],
            "purpose": r["purpose"],
            "areas_raw": r["areas_raw"],
            "post": post,
            "stale": stale,
            "barangays": barangays,
            "entities": entities.get(h) or {k: [] for k in ENTITY_KINDS},
            "unplaced": unplaced,
            "streets": _streets(h, street_ents, matches, draw),
        }
        any_drawn = any_drawn or bool(outages[str(r["id"])]["streets"]["drawn"])

    features = []
    for f in boundaries["features"]:
        p = f["properties"]
        place = gaz.place(p["psgc"])
        props = {
            "psgc": p["psgc"],
            "name": place.name if place else p["name"],
            "town": p["town"],
            "covered": p["covered"],
            "outage_ids": sorted(by_psgc.get(p["psgc"], ())),
        }
        features.append(
            {"type": "Feature", "id": f["id"], "geometry": f["geometry"], "properties": props}
        )

    dates = [m["osm_date"] for m in (matches or {}).values() if m["osm_date"]]
    osm_date = max(dates) if dates else None
    removed = conn.execute(
        "SELECT count(*) FROM outages WHERE status = 'removed' AND start_at < ? AND end_at > ?",
        (iso(window.end), iso(window.start)),
    ).fetchone()[0]
    last = conn.execute("SELECT value FROM meta WHERE key = 'last_check_at'").fetchone()
    meta = {
        "generated_at": now.astimezone(MANILA).isoformat(timespec="seconds"),
        "last_check_at": _manila(last[0] if last else None),
        "window": {"from": window.from_date.isoformat(), "to": window.to_date.isoformat()},
        "days": [d.isoformat() for d in window.days()],
        "removed_count": removed,
        "stale_posts": [stale_posts[u] for u in sorted(stale_posts)],
        "attribution": boundaries.get("attribution", "") + (f"; {ODBL}" if any_drawn else ""),
        "osm": {"data_date": osm_date, "drawing": draw},
    }
    return {"type": "FeatureCollection", "features": features, "outages": outages, "meta": meta}
