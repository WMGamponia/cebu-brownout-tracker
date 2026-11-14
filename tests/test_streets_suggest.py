"""Alias suggester: groups, in-scope candidates, input log (Streets v2 spec 7.1-7.2)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest
from map_world import LAHUG, TAYUD_C, TISA, boundaries, populate
from osm_world import CEBU, CONSOLACION, OSM_BASE

from outages import db
from outages.__main__ import main
from outages.areas.llm import LLMUnavailable
from outages.osm.extract import StreetIndex, Way
from outages.streets.suggest import (
    STREET_PROMPT_VERSION,
    Group,
    Pick,
    QwenPicker,
    candidates,
    difflib_pick,
    groups,
    load_test_ids,
    log_inputs,
    open_cache,
    render_prompt,
)

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


# --- pickers (Task 8, spec 7.3-7.5) ---------------------------------------------------------

CANDS = [f"Cand {i} Street" for i in range(1, 13)]
GROUP = Group(CEBU, "bugos street", "Bugos St.", [("h1", 0)], ["Areas A", "Areas B", "Areas C"])


def _ollama(answers: list, calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        a = answers[min(len(calls), len(answers)) - 1]
        if isinstance(a, Exception):
            raise a
        return httpx.Response(200, json={"message": {"content": json.dumps(a)}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_prompt_numbers_candidates():
    p = render_prompt(GROUP, CANDS)
    assert "Bugos St." in p
    assert all(f"{i}. Cand {i} Street" in p for i in range(1, 13))
    assert "Areas A" in p and "Areas B" in p and "Areas C" not in p


def test_pick_valid(tmp_path):
    calls: list = []
    qp = QwenPicker(open_cache(tmp_path / "c.db"), _ollama([{"pick": 3, "reason": "typo"}], calls))
    assert qp.pick(GROUP, CANDS) == Pick("ok", "Cand 3 Street", "typo")
    body = calls[0]
    assert body["think"] is False and body["options"]["temperature"] == 0
    assert body["format"]["required"] == ["pick", "reason"]


def test_pick_none_and_reason_truncated(tmp_path):
    long = " ".join(f"w{i}" for i in range(30))
    qp = QwenPicker(open_cache(tmp_path / "c.db"), _ollama([{"pick": None, "reason": long}], []))
    p = qp.pick(GROUP, CANDS)
    assert (p.status, p.pick) == ("ok", None)
    assert len(p.reason.split()) == 15


def test_pick_out_of_range_invalid_twice(tmp_path):
    calls: list = []
    qp = QwenPicker(open_cache(tmp_path / "c.db"), _ollama([{"pick": 13, "reason": "x"}], calls))
    assert qp.pick(GROUP, CANDS).status == "invalid"
    assert len(calls) == 2


def test_pick_cached(tmp_path):
    conn = open_cache(tmp_path / "c.db")
    calls: list = []
    qp = QwenPicker(conn, _ollama([{"pick": 1, "reason": "same"}], calls), model="m")
    qp.pick(GROUP, CANDS)
    assert qp.pick(GROUP, CANDS).pick == "Cand 1 Street"
    assert len(calls) == 1
    key = hashlib.sha256(
        ("m" + STREET_PROMPT_VERSION + render_prompt(GROUP, CANDS)).encode("utf-8")
    ).hexdigest()
    assert conn.execute("SELECT model FROM llm_cache WHERE key = ?", (key,)).fetchone()[0] == "m"


def test_pick_ollama_down_raises(tmp_path):
    qp = QwenPicker(open_cache(tmp_path / "c.db"), _ollama([httpx.ConnectError("down")], []))
    with pytest.raises(LLMUnavailable):
        qp.pick(GROUP, CANDS)


def test_no_candidates_no_call(tmp_path):
    calls: list = []
    qp = QwenPicker(open_cache(tmp_path / "c.db"), _ollama([{"pick": 1, "reason": ""}], calls))
    assert qp.pick(GROUP, []) == Pick("no_candidates", None, "")
    assert difflib_pick(GROUP, []).status == "no_candidates"
    assert calls == []


def test_difflib_pick_cutoff():
    assert difflib_pick(GROUP, ["Burgos Street", "Bonifacio Street"]).pick == "Burgos Street"
    p = difflib_pick(GROUP, ["Mango Avenue", "Escario Street"])
    assert (p.status, p.pick) == ("ok", None)


def test_suggest_dev_metrics(tmp_path, bnd):
    from outages.streets.evaluate import run_suggest_dev

    ways = [
        _lahug_way(1, "Burgos Street"),
        _lahug_way(2, "Bonifacio Street", row=1),
        _lahug_way(3, "Mango Avenue", row=2),
    ]
    index = StreetIndex(OSM_BASE, ways)
    base = {"areas_raw": "raw", "towns": ["Cebu City"], "barangays": [LAHUG], "town_psgc": CEBU}
    golds = [
        ("Bugos St.", "Burgos Street"),
        ("Bonfacio Street", "Bonifacio Street"),
        ("Zzz Road", None),
        ("Qqq Lane", "Hidden Street"),  # not in scope: a prefilter miss
        ("Burgos Street", "Burgos Street"),  # matched by the rules: not a suggester row
    ]
    rows = [
        {
            "id": f"dev-{i}",
            "areas_hash": f"h{i}",
            "seq": 0,
            "name": n,
            **base,
            "gold": g,
            "osm_base": OSM_BASE,
        }
        for i, (n, g) in enumerate(golds)
    ]
    (tmp_path / "dev.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    picks = {"bugos street": "Mango Avenue", "bonfacio street": "Bonifacio Street"}

    class Fake:
        def pick(self, g, cands):
            return Pick("ok", picks.get(g.key), "fake")

    aliases = tmp_path / "a.csv"
    aliases.write_text("town_psgc,veco_key,osm_name\n", encoding="utf-8")
    out, m = run_suggest_dev(tmp_path, index, bnd, NOW, Fake(), aliases_path=aliases)
    assert (m["n_nonnull"], m["n_null"]) == (3, 1)
    assert m["prefilter_recall"] == pytest.approx(2 / 3)
    assert m["difflib_acc"] == pytest.approx(3 / 4)
    assert m["qwen_acc"] == pytest.approx(2 / 4)
    text = out.read_text(encoding="utf-8")
    assert "directional" in text and f"prompt v{STREET_PROMPT_VERSION}" in text


def test_suggest_dev_cli(capsys, monkeypatch, tmp_path):
    from osm_world import write

    from outages import __main__ as cli

    monkeypatch.setattr(cli, "load_boundaries", lambda: boundaries())
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "data" / "osm")
    d = tmp_path / "evals" / "streets"
    d.mkdir(parents=True)
    row = {
        "id": "dev-1",
        "areas_hash": "h1",
        "seq": 0,
        "name": "Zzz Road",
        "areas_raw": "raw",
        "towns": ["Cebu City"],
        "barangays": [LAHUG],
        "town_psgc": CEBU,
        "gold": None,
        "osm_base": OSM_BASE,
    }
    (d / "dev.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    class Fake:
        name, model = "qwen", "fake"

        def pick(self, g, cands):
            return Pick("ok", None, "")

    monkeypatch.setattr(cli, "make_street_picker", lambda paths: Fake())
    argv = ["eval", "run", "--suite", "streets-suggest"]
    assert main([*argv, "--split", "test"]) == 2
    assert "dev only" in capsys.readouterr().err
    assert main([*argv, "--split", "dev"]) == 0
    assert "qwen accuracy" in capsys.readouterr().out

    def down(paths):
        raise LLMUnavailable("Ollama not reachable")

    monkeypatch.setattr(cli, "make_street_picker", down)
    assert main([*argv, "--split", "dev"]) == 2
