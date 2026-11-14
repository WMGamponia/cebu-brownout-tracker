"""Street matcher on the fixture world (spec 4.5)."""

from __future__ import annotations

import pytest
from map_world import LAHUG, LINAO, STO_NINO, TAYUD_C, TAYUD_L, TISA, boundaries, populate
from osm_world import CEBU, CONSOLACION, LILOAN, MINGLANILLA, OSM_BASE, write

from outages import db
from outages.osm.extract import StreetIndex
from outages.streets.match import load_aliases, match_street, scope_for
from outages.streets.review import review

SQ = 0.01  # one square, in degrees


@pytest.fixture(scope="module")
def bnd():
    return boundaries()


@pytest.fixture
def index(tmp_path):
    return StreetIndex.load(write(tmp_path / "osm"))


def _m(name, brgys, towns, index, bnd, aliases=None):
    return match_street(name, scope_for(brgys, towns, bnd), towns, index, aliases or {})


def test_index_load(index, tmp_path):
    assert index.osm_base == OSM_BASE
    assert [w.id for w in index.ways_named(CEBU, "gorordo avenue")] == [102, 103, 104]
    assert [w.id for w in index.ways_named(CEBU, "escario street")] == [105]
    assert index.ways_named(LILOAN, "gorordo avenue") == []
    assert StreetIndex.load(tmp_path / "missing") is None


def test_long_road_clipped_to_listed_barangays(index, bnd):
    m = _m("Cebu South Road.", [LAHUG, TISA], [CEBU], index, bnd)
    assert m.status == "matched" and m.osm_name == "Cebu South Road"
    xs = [p[0] for line in m.lines for p in line]
    # Sto. Niño (square 0) is not listed: only its node 109 m from Lahug's edge survives
    assert min(xs) == pytest.approx(123.8 + 0.9 * SQ)
    assert max(xs) == pytest.approx(123.8 + 2.9 * SQ)
    assert len(m.lines) == 1


def test_alias_applies_town_scoped(index, bnd):
    aliases = {(CONSOLACION, "cansaga bay road"): "Tayud Road"}
    m = _m("Cansaga Bay Rd.", [TAYUD_C], [CONSOLACION], index, bnd, aliases)
    assert m.status == "alias" and m.osm_name == "Tayud Road" and m.lines
    m = _m("Cansaga Bay Rd.", [TAYUD_L], [LILOAN], index, bnd, aliases)
    assert m.status == "not_found" and m.lines == []


def test_typo_not_found(index, bnd):
    m = _m("Gorordo Avnue", [LAHUG], [CEBU], index, bnd)
    assert (m.status, m.osm_name, m.lines) == ("not_found", None, [])


def test_no_barangay_uses_towns(index, bnd):
    m = _m("Linao Rd.", [], [MINGLANILLA], index, bnd)
    assert m.status == "matched" and m.osm_name == "Linao Road"


def test_no_scope_is_not_found(index, bnd):
    assert scope_for([], [], bnd) is None
    assert match_street("Linao Road", None, [], index, {}).status == "not_found"


def test_no_index_is_no_osm_data(bnd):
    m = _m("Cebu South Road", [LAHUG], [CEBU], None, bnd)
    assert (m.status, m.lines) == ("no_osm_data", [])


def test_generic_names_never_drawn(index, bnd):
    m = _m("Brgy. Road", [TISA], [CEBU], index, bnd)
    assert m.status == "not_found"
    aliases = {(CEBU, "barangay road"): "Barangay Road"}
    m = _m("Brgy. Road", [TISA], [CEBU], index, bnd, aliases)
    assert m.status == "alias" and m.osm_name == "Barangay Road" and m.lines


def test_any_name_tag_matches(index, bnd):
    m = _m("Escario St.", [LAHUG], [CEBU], index, bnd)
    assert m.status == "matched" and m.osm_name == "Juana Osmeña Street"


def test_way_with_one_node_inside_not_matched(index, bnd):
    assert _m("Salinas Dr.", [TISA], [CEBU], index, bnd).status == "not_found"


def test_osm_name_most_common(index, bnd):
    m = _m("Gorordo Ave", [LAHUG], [CEBU], index, bnd)
    assert m.status == "matched" and m.osm_name == "Gorordo Avenue"
    assert len(m.lines) == 3  # the Extension is a different road


def test_way_outside_scope_not_matched(index, bnd):
    assert _m("Gorordo Avenue", [STO_NINO], [CEBU], index, bnd).status == "not_found"


def test_scope_for_skips_unknown_psgc(bnd):
    assert scope_for(["999"], [], bnd) is None
    assert scope_for([LINAO, "999"], [], bnd) is not None


def test_load_aliases(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "town_psgc,veco_key,osm_name\n702219000,Cansaga Bay Rd.,Tayud Road\n", encoding="utf-8"
    )
    assert load_aliases(p) == {("702219000", "cansaga bay road"): "Tayud Road"}
    assert isinstance(load_aliases(), dict)  # the committed file loads


def test_review_suggests_close_names(tmp_path, index, bnd):
    conn = db.connect(tmp_path / "o.db")
    populate(conn)
    with conn:
        conn.execute(
            "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
            " VALUES ('hA', 3, 'street', 'Gorordo Avnue', 'rules')"
        )
        conn.executemany(
            "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, ?, ?)",
            [("hA", 3, "not_found"), ("hA", 2, "not_found"), ("hA", 0, "matched")],
        )
    items = review(conn, index, bnd)
    by_name = {i.name: i for i in items}
    assert set(by_name) == {"Gorordo Avnue", "Salinas Dr."}
    assert by_name["Gorordo Avnue"].count == 1
    assert by_name["Gorordo Avnue"].suggestions[0] == "Gorordo Avenue"
    assert len(by_name["Gorordo Avnue"].suggestions) <= 3
    conn.close()


def test_primary_name_fallback():
    from outages.osm.extract import PRIMARY_ORDER, Way, primary_name

    assert PRIMARY_ORDER == ("name", "official_name", "alt_name", "old_name", "short_name")
    assert primary_name(Way(1, {"alt_name": "Cebu South Road"}, [], CEBU)) == "Cebu South Road"
    assert primary_name(Way(1, {"name": "A", "alt_name": "B"}, [], CEBU)) == "A"
    assert primary_name(Way(1, {"old_name": "O", "official_name": "F"}, [], CEBU)) == "F"
    assert primary_name(Way(1, {}, [], CEBU)) is None


def test_osm_name_uses_primary_name(bnd):
    from outages.osm.extract import Way

    coords = [(123.8 + (1.1 + k * 0.2) / 100, 10.305) for k in range(5)]
    index = StreetIndex(OSM_BASE, [Way(901, {"alt_name": "Cebu South Road"}, coords, CEBU)])
    m = _m("Cebu South Road", [LAHUG], [CEBU], index, bnd)
    assert m.status == "matched" and m.osm_name == "Cebu South Road"
