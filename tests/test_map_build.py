"""The map document: boundaries joined with the outages in a window (Phase 4 spec 4.3).

Golden files: set `UPDATE_GOLDEN=1` to rewrite them, then review the diff by hand.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pytest
from map_world import (
    GORORDO_LINES,
    NOW,
    P2,
    STO_NINO,
    STREETS_OSM_DATE,
    TAYUD_C,
    TAYUD_L,
    TISA,
    boundaries,
    gazetteer,
    populate,
    populate_streets,
)

from outages import db
from outages.map.build import BoundariesError, build, dumps, load_boundaries, schema_ready
from outages.map.window import Window, default_window

GOLDEN = Path(__file__).parent / "golden" / "map"


@pytest.fixture
def conn():
    c = db.connect(Path(":memory:"))
    populate(c)
    populate_streets(c)
    yield c
    c.close()


def _doc(conn, window: Window | None = None, draw: bool = True) -> dict:
    w = window or default_window(conn, NOW)
    return json.loads(dumps(build(conn, gazetteer(), boundaries(), w, NOW, draw=draw)))


def _check_golden(doc: dict, name: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    assert doc == json.loads(path.read_text(encoding="utf-8"))


def test_default_document_golden(conn):
    _check_golden(_doc(conn), "default.geojson")


def test_explicit_window_golden(conn):
    doc = _doc(conn, Window(date(2026, 11, 13), date(2026, 11, 13)))
    _check_golden(doc, "explicit.geojson")
    assert "4" in doc["outages"]  # 22:00 on the 12th to 05:00 on the 13th
    assert not {"1", "2", "3"} & set(doc["outages"])
    assert doc["meta"]["days"] == ["2026-11-13"]


def test_default_window_and_order(conn):
    doc = _doc(conn)
    assert doc["meta"]["window"] == {"from": "2026-11-12", "to": "2026-11-18"}
    assert list(doc["outages"]) == ["1", "2", "3", "4", "5", "6", "7", "8", "11"]
    assert "10" not in doc["outages"]  # 20 Nov: after the 7-day cap


def test_every_boundary_feature_present(conn):
    doc = _doc(conn)
    assert [f["id"] for f in doc["features"]] == [f["id"] for f in boundaries()["features"]]
    agus = doc["features"][-1]
    assert agus["properties"] == {
        "psgc": "731100001",
        "name": "Agus",
        "town": "Lapu-Lapu City",
        "covered": False,
        "outage_ids": [],
    }


def test_unplaced_and_shaded_split(conn):
    o = _doc(conn)["outages"]
    assert [b["name"] for b in o["6"]["barangays"]] == ["Linao"]
    assert o["6"]["unplaced"] == [
        {"name": "Poblacion", "towns": ["Minglanilla"], "why": "other_town"}
    ]
    assert o["7"]["unplaced"] == [{"name": "Lipata", "towns": [], "why": "unmatched"}]
    assert o["7"]["barangays"] == []
    assert o["8"]["unplaced"] == [{"name": None, "towns": [], "why": "not_parsed"}]


def test_parse_without_barangays_is_listed(conn):
    """A parse that names no barangay at all (e.g. only `Cebu Business Park`) must not vanish."""
    o = _doc(conn)["outages"]["11"]
    assert o["barangays"] == []
    assert o["unplaced"] == [{"name": None, "towns": ["Cebu City"], "why": "no_barangay"}]
    assert o["entities"]["establishment"] == ["Cebu Business Park"]


def test_removed_counted_not_listed(conn):
    doc = _doc(conn)
    assert doc["meta"]["removed_count"] == 1
    assert "9" not in doc["outages"]


def test_stale_flag_and_posts(conn):
    doc = _doc(conn)
    assert doc["outages"]["5"]["stale"] is True
    assert doc["outages"]["1"]["stale"] is False
    assert doc["meta"]["stale_posts"] == [{"url": P2, "title": "Nov 13"}]


def test_display_name_and_veco_name(conn):
    doc = _doc(conn)
    feat = next(f for f in doc["features"] if f["id"] == STO_NINO)
    assert feat["properties"]["name"] == "Santo Niño"
    assert feat["properties"]["outage_ids"] == [2]
    (b,) = doc["outages"]["2"]["barangays"]
    assert (b["name"], b["veco_name"], b["ambiguous_with"]) == ("Santo Niño", "Sto. Niño", [])


def test_ambiguous_with(conn):
    bs = {b["psgc"]: b for b in _doc(conn)["outages"]["5"]["barangays"]}
    assert bs[TAYUD_L]["ambiguous_with"] == ["Consolacion"]
    assert bs[TAYUD_C]["ambiguous_with"] == ["Liloan"]


def test_duplicate_barangay_rows_deduplicated(conn):
    doc = _doc(conn)
    tisa = next(f for f in doc["features"] if f["id"] == TISA)
    assert tisa["properties"]["outage_ids"] == [1, 3]
    assert [b["psgc"] for b in doc["outages"]["1"]["barangays"]].count(TISA) == 1


def test_entities_grouped_in_order(conn):
    e = _doc(conn)["outages"]["1"]["entities"]
    assert e == {
        "street": ["Gorordo Ave.", "Salinas Dr."],
        "sitio_purok": ["Sitio Mabuhay"],
        "subdivision": [],
        "establishment": [],
        "other": [],
    }


def test_markup_in_text_passes_through_raw(conn):
    doc = build(conn, gazetteer(), boundaries(), default_window(conn, NOW), NOW)
    assert doc["outages"]["7"]["purpose"] == "Line <b>work</b> & tests"
    assert "Line <b>work</b> & tests" in dumps(doc)


def test_meta_times_in_manila(conn):
    meta = _doc(conn)["meta"]
    assert meta["generated_at"] == "2026-11-12T10:00:00+08:00"
    assert meta["last_check_at"] == "2026-11-12T08:00:00+08:00"
    assert meta["attribution"].startswith(boundaries()["attribution"])


def test_never_checked_is_null(conn):
    conn.execute("DELETE FROM meta WHERE key = 'last_check_at'")
    assert _doc(conn)["meta"]["last_check_at"] is None


def test_load_boundaries_rejects_bad_file(tmp_path):
    with pytest.raises(BoundariesError):
        load_boundaries(tmp_path / "missing.geojson")
    for bad in ("{}", "not json", '{"type":"FeatureCollection","features":[{"properties":{}}]}'):
        p = tmp_path / "b.geojson"
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(BoundariesError):
            load_boundaries(p)


def test_load_boundaries_committed_file():
    assert len(load_boundaries()["features"]) == 304


def test_schema_ready(tmp_path):
    assert schema_ready(db.connect(Path(":memory:")))
    import sqlite3

    assert not schema_ready(sqlite3.connect(":memory:"))


def test_build_is_read_only(tmp_path):
    path = tmp_path / "o.db"
    c = db.connect(path)
    populate(c)
    c.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    ro = db.connect_ro(path)
    build(ro, gazetteer(), boundaries(), default_window(ro, NOW), NOW)
    ro.close()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_load_boundaries_rejects_wrong_json_types(tmp_path):
    p = tmp_path / "b.geojson"
    for bad in (
        "[]",
        '"x"',
        '{"type":"FeatureCollection","features":["x"]}',
        '{"type":"FeatureCollection","features":{}}',
    ):
        p.write_text(bad, encoding="utf-8")
        with pytest.raises(BoundariesError):
            load_boundaries(p)


ODBL = "Streets © OpenStreetMap contributors (ODbL)"


def test_streets_drawn_and_not_drawn(conn):
    o = _doc(conn)["outages"]["1"]  # hA: Gorordo drawn, Salinas not found
    assert o["streets"] == {
        "drawn": [{"name": "Gorordo Ave.", "osm_name": "Gorordo Avenue", "lines": GORORDO_LINES}],
        "not_drawn": [{"name": "Salinas Dr.", "why": "not_found"}],
    }
    assert _doc(conn)["outages"]["4"]["streets"]["drawn"][0]["osm_name"] == "Juana Osmeña Street"
    assert _doc(conn)["outages"]["2"]["streets"] == {"drawn": [], "not_drawn": []}


def test_drawing_off_moves_all_to_not_drawn(conn):
    doc = _doc(conn, draw=False)
    _check_golden(doc, "drawing_off.geojson")
    assert doc["outages"]["1"]["streets"] == {
        "drawn": [],
        "not_drawn": [
            {"name": "Gorordo Ave.", "why": "drawing_off"},
            {"name": "Salinas Dr.", "why": "drawing_off"},
        ],
    }
    assert doc["meta"]["osm"] == {"data_date": STREETS_OSM_DATE, "drawing": False}
    assert ODBL not in doc["meta"]["attribution"]


def test_entity_without_match_row_is_not_processed(conn):
    """Areas re-parsed after the last `streets match`: a new entity has no row yet."""
    with conn:
        conn.execute(
            "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
            " VALUES ('hA', 3, 'street', 'New Street', 'rules')"
        )
    nd = _doc(conn)["outages"]["1"]["streets"]["not_drawn"]
    assert nd == [
        {"name": "Salinas Dr.", "why": "not_found"},
        {"name": "New Street", "why": "not_processed"},
    ]


def test_schema3_db_streets_no_osm_data(tmp_path):
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
    doc = json.loads(
        dumps(build(ro, gazetteer(), boundaries(), default_window(ro, NOW), NOW, draw=True))
    )
    ro.close()
    assert doc["outages"]["1"]["streets"] == {
        "drawn": [],
        "not_drawn": [
            {"name": "Gorordo Ave.", "why": "no_osm_data"},
            {"name": "Salinas Dr.", "why": "no_osm_data"},
        ],
    }
    assert doc["meta"]["osm"] == {"data_date": None, "drawing": True}


def test_meta_osm_and_attribution(conn):
    doc = _doc(conn)
    assert doc["meta"]["osm"] == {"data_date": STREETS_OSM_DATE, "drawing": True}
    assert doc["meta"]["attribution"].endswith(ODBL)
    # a window with no drawn line: no ODbL line
    doc = _doc(conn, Window(date(2026, 11, 16), date(2026, 11, 16)))
    assert not any(o["streets"]["drawn"] for o in doc["outages"].values())
    assert ODBL not in doc["meta"]["attribution"]


def test_no_barangay_outage_with_lines_stays_unplaced(conn):
    o = _doc(conn)["outages"]["11"]  # hG: Cebu Business Park, no barangay
    assert o["barangays"] == []
    assert o["unplaced"] == [{"name": None, "towns": ["Cebu City"], "why": "no_barangay"}]
    assert o["streets"]["drawn"][0]["osm_name"] == "Cardinal Rosales Avenue"
    assert len(o["streets"]["drawn"][0]["lines"]) == 2
