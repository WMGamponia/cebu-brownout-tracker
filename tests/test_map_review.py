"""Street review routes: loopback only, token + same Origin, writes only the queue (spec 7.9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from map_world import LAHUG, NOW, STO_NINO, boundaries, gazetteer, populate
from osm_world import CEBU, write

from outages import db
from outages.map.app import create_app
from outages.map.review import ReviewConfig
from outages.osm.extract import StreetIndex
from outages.streets import queue
from outages.streets.match import match_street, scope_for, search_towns
from outages.streets.store import scope_inputs

TOKEN = "t" * 43
ORIGIN = "http://127.0.0.1:8765"
STREETS = [("hA", 5, "Gorordo Avnue"), ("hC", 5, "Gorordo Avnue"), ("hA", 6, "Escarrio St")]


def _row(key, mentions=1, decision="", status="ok", **kw) -> dict:
    r = {c: "" for c in queue.COLUMNS}
    r.update(town_psgc=CEBU, town="Cebu City", veco_key=key, veco_name=key.title())
    r.update(mentions=str(mentions), status=status, decision=decision, **kw)
    return r


ROWS = [
    _row(
        "gorordo avnue",
        mentions=2,
        pick="Gorordo Avenue",
        pick_source="qwen",
        other_pick="Gorordo Avenue Extension",
        candidates="Gorordo Avenue|Gorordo Avenue Extension|Juana Osmeña Street",
        reason="typo",
    ),
    _row("escarrio street", pick="", candidates="Juana Osmeña Street", status="ok"),
    _row("done street", decision="n", pick="X"),
    _row("nothing street", status="no_candidates"),
]


@pytest.fixture
def world(tmp_path) -> dict:
    path = tmp_path / "outages.db"
    c = db.connect(path)
    populate(c)
    with c:
        for h, seq, name in STREETS:
            c.execute(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, ?, 'street', ?, 'rules')",
                (h, seq, name),
            )
            c.execute(
                "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, ?, 'not_found')",
                (h, seq),
            )
    c.close()
    osm = write(tmp_path / "osm")
    q = tmp_path / "streets" / "alias_queue.csv"
    queue.write(q, ROWS)
    return {"db": path, "osm": osm, "queue": q}


def _client(world, review=True) -> TestClient:
    cfg = ReviewConfig(world["queue"], world["osm"], TOKEN) if review else None
    app = create_app(world["db"], boundaries(), gazetteer(), clock=lambda: NOW, review=cfg)
    return TestClient(app, base_url=ORIGIN)


def _post(c: TestClient, body: dict, origin: str | None = ORIGIN):
    headers = {"Origin": origin} if origin else {}
    return c.post("/streets/review/decide", json=body, headers=headers)


def _body(**kw) -> dict:
    return {"town": CEBU, "key": "gorordo avnue", "decision": "y", "token": TOKEN, **kw}


def test_review_absent_on_lan_bind(world):
    c = _client(world, review=False)
    for path in ("/streets/review", "/streets/review/items", "/streets/review/item"):
        assert c.get(path).status_code == 404
    assert _post(c, _body()).status_code in (404, 405)
    assert c.get("/streets/review/static/review.js").status_code == 404


def test_page_carries_token(world):
    r = _client(world).get("/streets/review")
    assert r.status_code == 200
    assert f'<meta name="review-token" content="{TOKEN}">' in r.text


def test_post_needs_token(world):
    c = _client(world)
    assert _post(c, _body(token="nope")).status_code == 403
    assert _post(c, {k: v for k, v in _body().items() if k != "token"}).status_code == 403
    assert queue.read(world["queue"])[0]["decision"] == ""


def test_post_needs_same_origin(world):
    c = _client(world)
    assert _post(c, _body(), origin="http://evil.example").status_code == 403
    assert _post(c, _body(), origin=None).status_code == 403
    assert queue.read(world["queue"])[0]["decision"] == ""


def test_decide_writes_queue(world):
    before = {r["veco_key"]: r for r in queue.read(world["queue"])}
    assert _post(_client(world), _body()).status_code == 204
    after = {r["veco_key"]: r for r in queue.read(world["queue"])}
    assert after["gorordo avnue"]["decision"] == "y"
    for k in ("escarrio street", "done street", "nothing street"):
        assert after[k] == before[k]


def test_decide_candidate_name_and_skip(world):
    c = _client(world)
    assert _post(c, _body(decision="skip")).status_code == 204
    assert queue.read(world["queue"])[0]["decision"] == ""
    assert _post(c, _body(decision="Juana Osmeña Street")).status_code == 204
    assert queue.read(world["queue"])[0]["decision"] == "Juana Osmeña Street"


def test_decide_bad_value_422(world):
    c = _client(world)
    assert _post(c, _body(decision="Some Other Street")).status_code == 422
    assert _post(c, _body(key="escarrio street", decision="y")).status_code == 422  # no pick
    assert _post(c, {"token": TOKEN, "town": CEBU}).status_code == 422


def test_decide_twice_409(world):
    c = _client(world)
    assert _post(c, _body()).status_code == 204
    assert _post(c, _body(decision="n")).status_code == 409
    assert _post(c, _body(key="gone street")).status_code == 409


def test_decide_queue_busy_409(world, monkeypatch):
    def held(*a):
        raise PermissionError("in use")

    monkeypatch.setattr(queue.os, "replace", held)
    r = _post(_client(world), _body())
    assert r.status_code == 409 and r.json() == {"error": "queue busy"}


def test_items_excludes_decided(world):
    items = _client(world).get("/streets/review/items").json()
    assert [i["veco_key"] for i in items] == ["gorordo avnue", "escarrio street"]
    assert set(items[0]) == {
        "town_psgc",
        "town",
        "veco_key",
        "veco_name",
        "mentions",
        "status",
        "has_pick",
    }
    assert items[0]["mentions"] == 2


def test_items_empty_without_queue(world):
    world["queue"].unlink()
    assert _client(world).get("/streets/review/items").json() == []


def test_item_lines_equal_match_street(world):
    r = _client(world).get("/streets/review/item", params={"town": CEBU, "key": "gorordo avnue"})
    assert r.status_code == 200
    doc = r.json()
    assert (doc["veco_name"], doc["mentions"], doc["buffer_m"]) == ("Gorordo Avnue", 2, 150)
    assert doc["pick"] == "Gorordo Avenue" and doc["reason"] == "typo"
    assert doc["areas"] and len(doc["areas"]) <= 2
    ro = db.connect_ro(world["db"])
    brgys, towns = scope_inputs(ro, ["hA"])["hA"]  # hA is the group's first mention
    ro.close()
    assert LAHUG in brgys
    assert [f["properties"]["psgc"] for f in doc["scope"]["features"]] == brgys
    names = [c["name"] for c in doc["candidates"]]
    assert names[0] == "Gorordo Avenue" and doc["candidates"][0]["is_pick"]
    assert "Juana Osmeña Street" in names and len(names) == len(set(names))
    index = StreetIndex.load(world["osm"])
    scope = scope_for(brgys, towns, boundaries())
    search = search_towns(brgys, towns, boundaries())
    want = match_street("Gorordo Avenue", scope, search, index, {}).lines
    got = doc["candidates"][0]["lines"]
    assert got == [[list(p) for p in line] for line in want]
    assert got  # the pick is really drawn


def test_item_scope_is_union_of_mentions(world):
    """Candidates come from every mention's scope (up to 20), so the item draws that union:
    a group whose first mention is in Sto. Niño still shows the Lahug candidate's line."""
    c = db.connect(world["db"])
    with c:
        for h in ("hB", "hC"):
            c.execute(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, 7, 'street', 'Gorrordo Avenue', 'rules')",
                (h,),
            )
            c.execute(
                "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, 7, 'not_found')",
                (h,),
            )
    c.close()
    queue.write(world["queue"], [*ROWS, _row("gorrordo avenue", mentions=2, pick="Gorordo Avenue")])
    doc = (
        _client(world)
        .get("/streets/review/item", params={"town": CEBU, "key": "gorrordo avenue"})
        .json()
    )
    assert {f["properties"]["psgc"] for f in doc["scope"]["features"]} == {STO_NINO, LAHUG}
    index = StreetIndex.load(world["osm"])
    scope = scope_for([STO_NINO, LAHUG], [CEBU], boundaries())
    search = search_towns([STO_NINO, LAHUG], [CEBU], boundaries())
    want = match_street("Gorordo Avenue", scope, search, index, {}).lines
    assert doc["candidates"][0]["lines"] == [[list(p) for p in line] for line in want]
    assert want


def test_item_unknown_409(world):
    r = _client(world).get("/streets/review/item", params={"town": CEBU, "key": "gone street"})
    assert r.status_code == 409


def test_item_without_extract(world, tmp_path):
    world["osm"] = tmp_path / "no-osm"
    r = _client(world).get("/streets/review/item", params={"town": CEBU, "key": "gorordo avnue"})
    doc = r.json()
    assert r.status_code == 200 and doc["candidates"] == [] and "extract" in doc["message"]


def test_review_failure_leaves_map_routes(world):
    world["queue"].write_text("not,a,queue\n", encoding="utf-8")
    c = _client(world)
    assert c.get("/streets/review/items").status_code == 500
    assert c.get("/outages.geojson").status_code == 200


def test_snapshot_never_copies_review():
    from outages import snapshot

    files = snapshot._static_files()
    assert files and not any("review" in p for p in files)
    assert not (Path(snapshot.STATIC) / "review.html").exists()


def test_make_map_app_review_only_on_loopback(monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages.config import Paths

    seen = {}

    def fake(db_path, b, g, clock, review=None):
        seen["review"] = review
        return object()

    monkeypatch.setattr(cli, "create_app", fake)
    monkeypatch.setattr(cli, "load_boundaries", lambda: {})
    monkeypatch.setattr(cli, "load_gazetteer", lambda: None)
    paths = Paths(tmp_path)
    cli.make_map_app(paths, "127.0.0.1")
    assert seen["review"] is not None and len(seen["review"].token) >= 40
    cli.make_map_app(paths, "0.0.0.0")
    assert seen["review"] is None


def test_items_picks_first_by_mentions(world):
    rows = [
        _row("a street", mentions=9, pick="", candidates="A Street"),
        _row("b street", mentions=2, pick="B Street", candidates="B Street"),
        _row("c street", mentions=5, pick="C Street", candidates="C Street"),
        _row("d street", mentions=3, pick="", status="invalid", candidates="D Street"),
    ]
    queue.write(world["queue"], rows)
    items = _client(world).get("/streets/review/items").json()
    assert [i["veco_key"] for i in items] == ["c street", "b street", "a street", "d street"]
    assert [i["has_pick"] for i in items] == [True, True, False, False]


def _add_streets(world, streets):
    c = db.connect(world["db"])
    with c:
        for h, seq, name in streets:
            c.execute(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, ?, 'street', ?, 'rules')",
                (h, seq, name),
            )
            c.execute(
                "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, ?, 'not_found')",
                (h, seq),
            )
    c.close()


def test_item_draws_a_generic_pick_where_the_alias_would(world):
    """Review #3: lines come from the alias path that runs after --apply, so a generic OSM
    name (blocked on the plain path) is shown where it would really be drawn."""
    rows = [{**ROWS[0], "pick": "Barangay Road", "candidates": "Barangay Road|Gorordo Avenue"}]
    queue.write(world["queue"], rows)
    doc = _client(world).get("/streets/review/item", params={"town": CEBU, "key": "gorordo avnue"})
    lines = {c["name"]: c["lines"] for c in doc.json()["candidates"]}
    assert lines["Barangay Road"]  # drawn, as the alias would draw it
    assert doc.json()["candidates"][0]["generic"] is True


def test_item_scope_per_mention_for_mixed_groups(world):
    """Review #3: one mention placed (Sto. Niño), one town-only (hG): the town-only mention
    reaches every Cebu City barangay, so Lahug's Gorordo Avenue is drawn."""
    _add_streets(world, [("hB", 8, "Gorrordo Avenue"), ("hG", 8, "Gorrordo Avenue")])
    queue.write(world["queue"], [_row("gorrordo avenue", mentions=2, pick="Gorordo Avenue")])
    doc = (
        _client(world)
        .get("/streets/review/item", params={"town": CEBU, "key": "gorrordo avenue"})
        .json()
    )
    psgcs = {f["properties"]["psgc"] for f in doc["scope"]["features"]}
    assert {STO_NINO, LAHUG} <= psgcs
    assert doc["candidates"][0]["lines"]


def test_review_refuses_foreign_host(world):
    """Review #5: DNS rebinding sends Host: evil.example with a matching Origin; the review
    routes answer only to a loopback host name."""
    c = _client(world)
    evil = {"Host": "evil.example:8765"}
    assert c.get("/streets/review", headers=evil).status_code == 403
    assert c.get("/streets/review/items", headers=evil).status_code == 403
    r = c.post(
        "/streets/review/decide",
        json=_body(),
        headers={**evil, "Origin": "http://evil.example:8765"},
    )
    assert r.status_code == 403
    assert queue.read(world["queue"])[0]["decision"] == ""
    assert c.get("/streets/review", headers={"Host": "localhost:8765"}).status_code == 200
