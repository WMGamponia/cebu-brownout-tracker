"""Street eval: sampler and scorer (spec 5)."""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

import pytest

from outages import db
from outages.__main__ import _parser, main
from outages.config import VECO_TOWNS
from outages.streets.evaluate import StreetRow, sample_streets, score, street_rows
from outages.streets.normalize import street_key

TOWNS = list(VECO_TOWNS.values())


def _rows() -> list[StreetRow]:
    """Per town: 20 'one', 10 'several', 5 'none' rows, each with its own street name."""
    out = []
    for t_i, town in enumerate(TOWNS):
        for kind, n in (("one", 20), ("several", 10), ("none", 5)):
            for i in range(n):
                out.append(
                    StreetRow(
                        areas_hash=f"h{t_i}-{kind}-{i}",
                        seq=0,
                        name=f"Street {t_i} {kind} {i}",
                        areas_raw=f"raw {t_i} {kind} {i}",
                        towns=["Cebu City"],
                        barangays=[]
                        if kind == "none"
                        else ["730600001"] * (1 if kind == "one" else 2),
                        scope_kind=kind,
                        town_psgc=town,
                    )
                )
    # a duplicate key in another town must never be drawn twice
    out.append(
        StreetRow(
            "dup", 0, "Street 0 one 0.", "raw dup", ["Liloan"], ["702227001"], "one", TOWNS[6]
        )
    )
    return out


def test_sample_deterministic():
    a = sample_streets(_rows(), seed=20261112)
    b = sample_streets(_rows(), seed=20261112)
    assert a == b
    c = sample_streets(_rows(), seed=1)
    assert [r["areas_hash"] for r in a[0]] != [r["areas_hash"] for r in c[0]]


def test_sample_stratified():
    dev, test = sample_streets(_rows())
    assert len(dev) == 30 and len(test) == 50
    assert [r["id"] for r in dev] == [f"dev-{i:02d}" for i in range(1, 31)]
    assert [r["id"] for r in test] == [f"test-{i:02d}" for i in range(1, 51)]
    keys = [street_key(r["name"]) for r in dev + test]
    assert len(set(keys)) == 80
    by_town = Counter(r["areas_hash"].split("-")[0] for r in dev + test)
    assert set(by_town.values()) == {10}
    dev_town = Counter(r["areas_hash"].split("-")[0] for r in dev)
    assert sorted(dev_town.values()) == [3, 3, 4, 4, 4, 4, 4, 4]
    # proportional scope kinds per town: 20/10/5 of 35 -> 6/3/1 of 10 (largest remainder)
    kinds = Counter(r["areas_hash"].split("-")[1] for r in dev + test)
    assert kinds == {"one": 48, "several": 24, "none": 8}
    assert set(dev[0]) == {
        "id",
        "areas_hash",
        "seq",
        "name",
        "areas_raw",
        "towns",
        "barangays",
        "gold",
    }
    assert all(r["gold"] is None for r in dev + test)


def _m(status, osm_name=None):
    return SimpleNamespace(status=status, osm_name=osm_name)


def test_score_precision_coverage():
    rows = [
        {"areas_hash": "a", "seq": 0, "gold": "Gorordo Avenue"},
        {"areas_hash": "b", "seq": 0, "gold": "Cebu South Road"},
        {"areas_hash": "c", "seq": 0, "gold": "Banilad Road"},
        {"areas_hash": "d", "seq": 0, "gold": "Juan Luna Avenue"},
        {"areas_hash": "e", "seq": 0, "gold": "Osmeña Boulevard"},
        {"areas_hash": "f", "seq": 0, "gold": None},
    ]
    matches = {
        ("a", 0): _m("matched", "Gorordo Avenue"),
        ("b", 0): _m("alias", "Cebu South Road"),
        ("c", 0): _m("matched", "Banilad Rd."),  # same key
        ("d", 0): _m("matched", "Juan Luna Avenue Extension"),  # wrong road
        ("e", 0): _m("not_found"),
        ("f", 0): _m("not_found"),
    }
    s = score(rows, matches)
    assert s["drawn"] == 4 and s["correct"] == 3
    assert s["precision"] == pytest.approx(0.75)
    assert s["coverage"] == pytest.approx(0.6)
    assert s["by_status"] == {"matched": 3, "alias": 1, "not_found": 2}


def test_null_gold_drawn_counts_against_precision():
    rows = [
        {"areas_hash": "a", "seq": 0, "gold": None},
        {"areas_hash": "b", "seq": 1, "gold": "X Street"},
    ]
    s = score(rows, {("a", 0): _m("matched", "Barangay Road"), ("b", 1): _m("matched", "X St.")})
    assert s["drawn"] == 2 and s["correct"] == 1
    assert s["precision"] == 0.5
    assert s["coverage"] == 1.0


def test_score_nothing_drawn():
    s = score([{"areas_hash": "a", "seq": 0, "gold": "X"}], {})
    assert s["precision"] is None and s["coverage"] == 0.0
    assert s["by_status"] == {"missing": 1}


def test_street_rows_from_db(tmp_path):
    conn = db.connect(tmp_path / "o.db")
    conn.execute("INSERT INTO posts (id, url, slug, first_seen_at) VALUES (1, 'u', 's', 't')")
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (1, 1, 't', 'h', x'00')"
    )
    for oid, h, status in ((1, "h1", "scheduled"), (2, "h2", "scheduled"), (3, "h3", "removed")):
        conn.execute(
            "INSERT INTO outages (id, post_id, seq, heading_text, start_at, end_at, status,"
            " purpose, areas_raw, areas_key, areas_hash, first_seen_version_id,"
            " last_seen_version_id) VALUES (?, 1, ?, 'h', 's', 'e', ?, 'p', ?, ?, ?, 1, 1)",
            (oid, oid, status, f"raw {h}", f"k{oid}", h),
        )
    for h, towns in (
        ("h1", '["Cebu City", "Mandaue City"]'),
        ("h2", '["Liloan"]'),
        ("h3", '["Cebu City"]'),
    ):
        conn.execute(
            "INSERT INTO area_parses (areas_hash, layout, towns_json, extractor, parsed_at)"
            " VALUES (?, 'city_first', ?, 'rules', 't')",
            (h, towns),
        )
        conn.execute(
            "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
            " VALUES (?, 0, 'street', 'Gorordo Ave.', 'rules')",
            (h,),
        )
    conn.execute(
        "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
        " VALUES ('h1', 1, 'sitio_purok', 'Sitio X', 'rules')"
    )
    for seq, psgc, town, match in (
        (0, "731300003", "731300000", "ambiguous"),
        (1, "730600007", "730600000", "ambiguous"),
        (2, None, "730600000", "unmatched"),
    ):
        conn.execute(
            "INSERT INTO area_barangays (areas_hash, seq, name_raw, town_psgc, psgc_code, match)"
            " VALUES ('h1', ?, 'n', ?, ?, ?)",
            (seq, town, psgc, match),
        )
    conn.commit()
    rows = {r.areas_hash: r for r in street_rows(conn)}
    assert set(rows) == {"h1", "h2"}  # removed-only text left out; streets only
    h1 = rows["h1"]
    assert (h1.seq, h1.name, h1.areas_raw) == (0, "Gorordo Ave.", "raw h1")
    assert h1.towns == ["Cebu City", "Mandaue City"]
    assert h1.barangays == ["731300003", "730600007"]
    assert h1.scope_kind == "several" and h1.town_psgc == "731300000"
    h2 = rows["h2"]
    assert h2.barangays == [] and h2.scope_kind == "none" and h2.town_psgc == "702227000"
    conn.close()


def test_eval_suite_default_is_areas():
    assert _parser().parse_args(["eval", "sample"]).suite == "areas"
    assert _parser().parse_args(["eval", "run", "--suite", "streets"]).suite == "streets"


def test_eval_streets_offline(capsys, monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages.osm import overpass

    def boom(*a, **kw):
        raise AssertionError("offline command built a network client")

    monkeypatch.setattr(cli, "make_fetcher", boom)
    monkeypatch.setattr(cli, "make_telegram", boom)
    monkeypatch.setattr(overpass, "make_client", boom)
    monkeypatch.chdir(tmp_path)
    db.connect(tmp_path / "data" / "outages.db").close()
    assert main(["eval", "sample", "--suite", "streets"]) == 0
    assert (tmp_path / "evals" / "streets" / "dev.jsonl").exists()
    assert (tmp_path / "evals" / "streets" / "test.jsonl").exists()
    (tmp_path / "evals" / "streets" / "FROZEN").write_text("x\n", encoding="utf-8")
    assert main(["eval", "sample", "--suite", "streets"]) == 1
    assert "frozen" in capsys.readouterr().err


def _write_split(d, name, rows):
    import json

    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_run_streets_scores_and_writes(tmp_path):
    from datetime import UTC, datetime

    from map_world import LAHUG, TISA, boundaries
    from osm_world import write

    from outages.streets.evaluate import run_streets

    osm = write(tmp_path / "osm")
    rows = [
        {
            "id": "dev-01",
            "areas_hash": "a",
            "seq": 0,
            "name": "Cebu South Rd.",
            "towns": ["Cebu City"],
            "barangays": [LAHUG, TISA],
            "gold": "Cebu South Road",
        },
        {
            "id": "dev-02",
            "areas_hash": "b",
            "seq": 0,
            "name": "Gorordo Avnue",
            "towns": ["Cebu City"],
            "barangays": [LAHUG],
            "gold": "Gorordo Avenue",
        },
        {
            "id": "dev-03",
            "areas_hash": "c",
            "seq": 0,
            "name": "Brgy. Road",
            "towns": ["Cebu City"],
            "barangays": [TISA],
            "gold": None,
        },
    ]
    d = tmp_path / "evals"
    _write_split(d, "dev.jsonl", rows)
    out, s = run_streets("dev", d, osm, boundaries(), datetime(2026, 11, 12, tzinfo=UTC))
    assert s["drawn"] == 1 and s["correct"] == 1 and s["precision"] == 1.0
    assert s["coverage"] == 0.5
    text = out.read_text(encoding="utf-8")
    assert out.name == "2026-11-12-dev.md"
    assert "precision" in text and "dev-02" in text and "not_found" in text


def test_run_streets_test_split_must_be_frozen(tmp_path):
    from datetime import UTC, datetime

    from map_world import boundaries
    from osm_world import write

    from outages.evaluate import EvalError, freeze
    from outages.streets.evaluate import run_streets

    d = tmp_path / "evals"
    _write_split(d, "test.jsonl", [])
    now = datetime(2026, 11, 12, tzinfo=UTC)
    with pytest.raises(EvalError, match="not frozen"):
        run_streets("test", d, write(tmp_path / "osm"), boundaries(), now)
    freeze(d / "test.jsonl", d / "FROZEN")
    run_streets("test", d, write(tmp_path / "osm"), boundaries(), now)


def test_run_streets_without_extract_errors(tmp_path):
    from datetime import UTC, datetime

    from map_world import boundaries

    from outages.evaluate import EvalError
    from outages.streets.evaluate import run_streets

    d = tmp_path / "evals"
    _write_split(d, "dev.jsonl", [])
    with pytest.raises(EvalError, match="streets fetch"):
        run_streets("dev", d, tmp_path / "none", boundaries(), datetime(2026, 11, 12, tzinfo=UTC))


def test_eval_run_streets_cli_offline(capsys, monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages.osm import overpass

    def boom(*a, **kw):
        raise AssertionError("offline command built a network client")

    monkeypatch.setattr(cli, "make_fetcher", boom)
    monkeypatch.setattr(cli, "make_telegram", boom)
    monkeypatch.setattr(overpass, "make_client", boom)
    monkeypatch.chdir(tmp_path)
    _write_split(tmp_path / "evals" / "streets", "dev.jsonl", [])
    assert main(["eval", "run", "--suite", "streets"]) == 1  # no extract yet
    assert "streets fetch" in capsys.readouterr().err


def test_score_list_gold():
    rows = [{"areas_hash": "a", "seq": 0, "gold": ["Natalio Bacalso Avenue", "Cebu South Road"]}]
    s = score(rows, {("a", 0): _m("matched", "Cebu South Road")})
    assert s["drawn"] == 1 and s["correct"] == 1
    assert s["coverage"] == 1.0


def test_gold_keys():
    from outages.streets.evaluate import gold_keys

    assert gold_keys(None) == set()
    assert gold_keys("Gorordo Ave.") == {"gorordo avenue"}
    assert gold_keys(["A St.", "B Road"]) == {"a street", "b road"}


def test_score_breakdowns():
    rows = [
        {"areas_hash": "a", "seq": 0, "gold": "X Street", "town_psgc": "730600000"},
        {"areas_hash": "b", "seq": 0, "gold": "Y Street", "town_psgc": "730600000"},
        {"areas_hash": "c", "seq": 0, "gold": "Z Street", "town_psgc": "702227000"},
        {"areas_hash": "d", "seq": 0, "gold": None},
    ]
    matches = {
        ("a", 0): _m("matched", "X Street"),
        ("b", 0): _m("matched", "Y St."),
        ("c", 0): _m("alias", "W Street"),
        ("d", 0): _m("not_found"),
    }
    s = score(rows, matches)
    assert s["by_drawn_status"] == {
        "matched": {"drawn": 2, "correct": 2},
        "alias": {"drawn": 1, "correct": 0},
    }
    assert s["by_town"] == {
        "730600000": {"drawn": 2, "correct": 2},
        "702227000": {"drawn": 1, "correct": 0},
        "?": {"drawn": 0, "correct": 0},
    }
    lo, hi = s["interval"]
    assert 0 < lo < 2 / 3 < hi < 1


def test_wilson():
    from outages.streets.evaluate import wilson

    lo, hi = wilson(57, 60)
    assert lo == pytest.approx(0.862, abs=1e-3)
    assert hi == pytest.approx(0.983, abs=1e-3)
    assert wilson(0, 0) is None


def test_run_refuses_osm_base_mismatch(tmp_path):
    from datetime import UTC, datetime

    from map_world import LAHUG, boundaries
    from osm_world import OSM_BASE, write

    from outages.evaluate import EvalError
    from outages.streets.evaluate import run_streets

    row = {
        "id": "dev-01",
        "areas_hash": "a",
        "seq": 0,
        "name": "Gorordo Avenue",
        "towns": ["Cebu City"],
        "barangays": [LAHUG],
        "gold": "Gorordo Avenue",
        "osm_base": "2026-01-01T00:00:00Z",
    }
    d = tmp_path / "evals"
    _write_split(d, "dev.jsonl", [row])
    with pytest.raises(EvalError) as e:
        run_streets(
            "dev", d, write(tmp_path / "osm"), boundaries(), datetime(2026, 11, 12, tzinfo=UTC)
        )
    assert "2026-01-01T00:00:00Z" in str(e.value) and OSM_BASE in str(e.value)
