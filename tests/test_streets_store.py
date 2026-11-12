"""street_matches store and run_match (spec 4.5)."""

from __future__ import annotations

import hashlib
import json

import pytest
from map_world import boundaries, populate
from osm_world import OSM_BASE, write

from outages import db
from outages.osm.extract import StreetIndex
from outages.streets.store import run_match


@pytest.fixture
def world(tmp_path):
    conn = db.connect(tmp_path / "o.db")
    populate(conn)
    index = StreetIndex.load(write(tmp_path / "osm"))
    yield conn, index, boundaries(), tmp_path / "o.db"
    conn.close()


def _rows(conn):
    return {
        (r[0], r[1]): (r[2], r[3], r[4], r[5])
        for r in conn.execute(
            "SELECT areas_hash, seq, status, osm_name, geometry_json, osm_date FROM street_matches"
        )
    }


def test_run_match_writes_rows_and_geometry(world):
    conn, index, bnd, _ = world
    s = run_match(conn, index, bnd)
    assert s.written == 3
    assert s.by_status == {"matched": 2, "not_found": 1}
    rows = _rows(conn)
    assert set(rows) == {("hA", 0), ("hA", 2), ("hC", 0)}
    status, osm_name, geom, date = rows[("hA", 0)]
    assert (status, osm_name, date) == ("matched", "Gorordo Avenue", OSM_BASE)
    lines = json.loads(geom)
    assert len(lines) == 3
    for line in lines:
        for x, y in line:
            assert round(x, 5) == x and round(y, 5) == y
    assert rows[("hA", 2)] == ("not_found", None, None, OSM_BASE)
    assert rows[("hC", 0)][:2] == ("matched", "Juana Osmeña Street")


def test_idempotent_second_run_writes_none(world):
    conn, index, bnd, _ = world
    run_match(conn, index, bnd)
    assert run_match(conn, index, bnd).written == 0


def test_rebuild_rewrites_all(world):
    conn, index, bnd, _ = world
    run_match(conn, index, bnd)
    assert run_match(conn, index, bnd, rebuild=True).written == 3


def test_new_extract_redoes_rows(world, tmp_path):
    conn, _, bnd, _ = world
    run_match(conn, None, bnd)
    assert {v[0] for v in _rows(conn).values()} == {"no_osm_data"}
    index = StreetIndex.load(tmp_path / "osm")
    assert run_match(conn, index, bnd).written == 3
    assert _rows(conn)[("hA", 0)][0] == "matched"


def test_dry_run_writes_nothing(world):
    conn, index, bnd, path = world
    conn.commit()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ro = db.connect_ro(path)
    s = run_match(ro, index, bnd, dry_run=True)
    ro.close()
    assert s.written == 0 and s.by_status == {"matched": 2, "not_found": 1}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_dry_run_on_schema3_db(tmp_path):
    """The real DB is at schema 3 before the migration: a dry run must still count."""
    import sqlite3

    p = tmp_path / "o.db"
    c = sqlite3.connect(p)
    for n, sql in db._migrations():
        if n <= 3:
            c.executescript(
                f"BEGIN;\n{sql}\nINSERT INTO schema_version (version) VALUES ({n});\nCOMMIT;"
            )
    populate(c)
    c.close()
    ro = db.connect_ro(p)
    s = run_match(ro, StreetIndex.load(write(tmp_path / "osm")), boundaries(), dry_run=True)
    ro.close()
    assert s.written == 0 and s.by_status == {"matched": 2, "not_found": 1}


def test_orphan_rows_deleted(world):
    conn, index, bnd, _ = world
    run_match(conn, index, bnd)
    with conn:
        conn.execute("DELETE FROM area_entities WHERE areas_hash = 'hC'")
        conn.execute(
            "INSERT INTO street_matches (areas_hash, seq, status) VALUES ('gone', 0, 'matched')"
        )
    run_match(conn, index, bnd)
    assert set(_rows(conn)) == {("hA", 0), ("hA", 2)}


def test_entity_kind_change_drops_row(world):
    conn, index, bnd, _ = world
    run_match(conn, index, bnd)
    with conn:
        conn.execute("UPDATE area_entities SET kind = 'other' WHERE areas_hash = 'hA' AND seq = 2")
    run_match(conn, index, bnd)
    assert ("hA", 2) not in _rows(conn)


def test_partial_refetch_redoes_rows(world, tmp_path):
    """Review #1: `streets fetch --towns X` for a town that isn't the oldest leaves osm_base
    unchanged; the rows must still be redone (the extract's version is its newest fetch)."""
    conn, _, bnd, _ = world
    osm = tmp_path / "osm"
    man_path = osm / "manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    for t in man["towns"].values():
        t["fetched_at"] = "2026-11-12T03:00:00+00:00"
    man_path.write_text(json.dumps(man), encoding="utf-8")
    run_match(conn, StreetIndex.load(osm), bnd)
    assert run_match(conn, StreetIndex.load(osm), bnd).written == 0
    man["towns"]["702232000"]["fetched_at"] = "2026-11-12T05:00:00+00:00"  # refetched one town
    man_path.write_text(json.dumps(man), encoding="utf-8")
    index = StreetIndex.load(osm)
    assert index.osm_base == OSM_BASE  # unchanged: the oldest town still dates the data
    assert run_match(conn, index, bnd).written == 3
    assert {r[3] for r in _rows(conn).values()} == {"2026-11-12T05:00:00+00:00"}
