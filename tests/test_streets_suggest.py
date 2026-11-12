"""Alias suggester: groups, in-scope candidates, input log (Streets v2 spec 7.1-7.2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from map_world import LAHUG, TAYUD_C, TISA, boundaries, populate
from osm_world import CEBU, CONSOLACION, OSM_BASE

from outages import db
from outages.osm.extract import StreetIndex, Way
from outages.streets.suggest import Group, candidates, groups, load_test_ids, log_inputs

NOW = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def bnd():
    return boundaries()


def _conn(tmp_path, streets: list[tuple[str, int, str, str]]):
    """The map world plus street mentions (areas_hash, seq, name, status)."""
    conn = db.connect(tmp_path / "o.db")
    populate(conn)
    with conn:
        for h, seq, name, status in streets:
            conn.execute(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, ?, 'street', ?, 'rules')",
                (h, seq, name),
            )
            conn.execute(
                "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, ?, ?)",
                (h, seq, status),
            )
    return conn


def test_groups_by_town_and_key(tmp_path):
    conn = _conn(
        tmp_path,
        [
            ("hA", 5, "Gorordo Avnue", "not_found"),
            ("hD", 5, "Gorordo Avnue", "not_found"),
            ("hC", 5, "Gorordo Avnue.", "not_found"),
            ("hC", 6, "Escario St.", "matched"),
        ],
    )
    gs = groups(conn, set(), set())
    by = {(g.town_psgc, g.key): g for g in gs}
    assert set(by) == {(CEBU, "gorordo avnue"), (CONSOLACION, "gorordo avnue")}
    cebu = by[(CEBU, "gorordo avnue")]
    assert cebu.mentions == [("hA", 5), ("hC", 5)]
    assert gs[0] is cebu  # most mentions first
    assert cebu.areas_examples == ["Areas of 1", "Areas of 4"]
    conn.close()


def test_groups_drop_test_rows(tmp_path):
    conn = _conn(
        tmp_path,
        [
            ("hA", 5, "Gorordo Avnue", "not_found"),
            ("hC", 5, "Gorordo Avnue", "not_found"),
            ("hD", 5, "Gorordo Avnue", "not_found"),
        ],
    )
    gs = groups(conn, {("hD", 5), ("hA", 5)}, set())
    assert [(g.town_psgc, g.mentions) for g in gs] == [(CEBU, [("hC", 5)])]
    conn.close()


def test_groups_skip_generic_and_known(tmp_path):
    conn = _conn(
        tmp_path,
        [
            ("hA", 5, "Brgy. Road", "not_found"),
            ("hA", 6, "Gorordo Avnue", "not_found"),
            ("hD", 6, "Gorordo Avnue", "not_found"),
        ],
    )
    gs = groups(conn, set(), {(CEBU, "gorordo avnue")})
    assert [(g.town_psgc, g.key) for g in gs] == [(CONSOLACION, "gorordo avnue")]
    conn.close()


def test_veco_name_most_common_spelling(tmp_path):
    conn = _conn(
        tmp_path,
        [
            ("hA", 5, "Gorordo Avnue.", "not_found"),
            ("hA", 6, "Gorordo Avnue", "not_found"),
            ("hC", 5, "Gorordo Avnue", "not_found"),
        ],
    )
    (g,) = groups(conn, set(), set())
    assert g.veco_name == "Gorordo Avnue"
    conn.close()


def _lahug_way(wid: int, name: str, row: int = 0, nodes: int = 3) -> Way:
    """A way inside Lahug (square 1: x 123.81-123.82, y 10.30-10.31)."""
    y = 10.3005 + row * 0.0006
    return Way(wid, {"name": name}, [(123.811 + k * 0.002, y) for k in range(nodes)], CEBU)


def _group(key="bugos street", mentions=(("hX", 0),)):
    return Group(CEBU, key, "Bugos Street", list(mentions), [])


INPUTS = {"hX": ([LAHUG], [CEBU])}


def test_candidates_only_in_scope(bnd):
    far = Way(2, {"name": "Bugos Street"}, [(123.835, 10.305), (123.838, 10.305)], CEBU)
    index = StreetIndex(OSM_BASE, [_lahug_way(1, "Burgos Street"), far])
    assert candidates(_group(), index, bnd, INPUTS) == ["Burgos Street"]


def test_candidates_need_two_nodes_in_scope(bnd):
    one = Way(3, {"name": "Burgos Street"}, [(123.815, 10.305), (123.835, 10.305)], CEBU)
    index = StreetIndex(OSM_BASE, [one, _lahug_way(1, "Bonifacio Street")])
    assert candidates(_group(), index, bnd, INPUTS) == ["Bonifacio Street"]


def test_candidates_top_k_by_ratio(bnd):
    names = ["Burgos Street", "Burgos St."] + [f"Zeta {c} Avenue" for c in "ABCDEFGHIJK"]
    names += ["Bugos Street", "Bugos Road"]  # own key: never a candidate; Road is close
    ways = [_lahug_way(i, n, row=i) for i, n in enumerate(names)]
    cands = candidates(_group(), StreetIndex(OSM_BASE, ways), bnd, INPUTS)
    assert len(cands) == 12
    # one name per key (names_in order: most ways, then name), closest key first
    assert cands[:2] == ["Burgos St.", "Bugos Road"]
    assert "Burgos Street" not in cands and "Bugos Street" not in cands


def test_candidates_union_over_first_20_mentions(bnd):
    tisa = Way(5, {"name": "Burgos Street"}, [(123.821, 10.305), (123.825, 10.305)], CEBU)
    index = StreetIndex(OSM_BASE, [_lahug_way(1, "Bonifacio Street"), tisa])
    inputs = {"hX": ([LAHUG], [CEBU]), "hY": ([TISA], [CEBU])}
    g = _group(mentions=[("hX", 0), ("hY", 0)])
    assert set(candidates(g, index, bnd, inputs)) == {"Bonifacio Street", "Burgos Street"}
    g21 = _group(mentions=[(f"hX{i:02d}", 0) for i in range(20)] + [("hY", 0)])
    inputs21 = {**{f"hX{i:02d}": ([LAHUG], [CEBU]) for i in range(20)}, "hY": ([TISA], [CEBU])}
    assert candidates(g21, index, bnd, inputs21) == ["Bonifacio Street"]


def test_log_inputs_appends_jsonl(tmp_path):
    log = tmp_path / "streets" / "suggest_inputs.jsonl"
    g = Group(CONSOLACION, "x road", "X Rd.", [("h1", 0), ("h2", 3)], [])
    log_inputs(log, g, NOW)
    log_inputs(log, g, NOW)
    rows = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 4
    assert rows[1] == {
        "areas_hash": "h2",
        "seq": 3,
        "town_psgc": CONSOLACION,
        "key": "x road",
        "at": NOW.isoformat(),
    }


def test_load_test_ids(tmp_path):
    assert load_test_ids(tmp_path) == set()
    (tmp_path / "test.rows.jsonl").write_text(
        json.dumps({"id": "t2-001", "areas_hash": "h1", "seq": 2}) + "\n", encoding="utf-8"
    )
    assert load_test_ids(tmp_path) == {("h1", 2)}


def test_group_town_is_first_placed_barangays(tmp_path):
    conn = _conn(tmp_path, [("hD", 5, "Gorordo Avnue", "not_found")])
    (g,) = groups(conn, set(), set())
    assert g.town_psgc == CONSOLACION  # TAYUD_C is hD's first placed barangay
    assert TAYUD_C.startswith(g.town_psgc[:6])
    conn.close()
