"""Streets v2 eval: sampler, label check, review sample, freeze (spec 6.1-6.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outages import db
from outages.__main__ import main
from outages.config import VECO_TOWNS
from outages.streets.evaluate import StreetRow
from outages.streets.normalize import street_key

TOWNS = list(VECO_TOWNS.values())


def _sr(h, name, town, seq=0):
    return StreetRow(h, seq, name, f"raw {h}", ["Cebu City"], [], "none", town)


def _pool() -> list[StreetRow]:
    return [
        _sr(f"p{t}-{i}", f"Road {i % 30}", town) for t, town in enumerate(TOWNS) for i in range(40)
    ]


def test_sample_v2_deterministic():
    from outages.streets.evaluate import sample_v2

    a = sample_v2(_pool(), set(), n=150, seed=20261113)
    b = sample_v2(_pool(), set(), n=150, seed=20261113)
    assert a == b
    assert [r["id"] for r in a[:3]] == ["t2-001", "t2-002", "t2-003"]
    assert set(a[0]) == {
        "id",
        "areas_hash",
        "seq",
        "name",
        "areas_raw",
        "towns",
        "barangays",
        "town_psgc",
    }
    c = sample_v2(_pool(), set(), n=150, seed=1)
    assert [r["areas_hash"] for r in a] != [r["areas_hash"] for r in c]


def test_sample_v2_one_per_town_key():
    from outages.streets.evaluate import sample_v2

    rows = sample_v2(_pool(), set(), n=150)
    pairs = [(r["town_psgc"], street_key(r["name"])) for r in rows]
    assert len(rows) == 150 and len(set(pairs)) == 150
    # a pool with 30 distinct pairs gives what it has
    assert len(sample_v2(_pool()[:40], set(), n=150)) == 30


def test_sample_v2_excludes_old_keys():
    from outages.streets.evaluate import sample_v2

    rows = sample_v2(_pool(), {"road 1", "road 2"}, n=150)
    assert not {street_key(r["name"]) for r in rows} & {"road 1", "road 2"}


def test_sample_v2_mention_weighted():
    from outages.streets.evaluate import sample_v2

    pool = [_sr(f"a{i}", "Key A", TOWNS[0]) for i in range(90)]
    pool += [_sr(f"b{i}", f"Key B{i}", TOWNS[0]) for i in range(10)]
    picks = [sample_v2(pool, set(), n=1, seed=s)[0]["name"] for s in range(1000)]
    hits = sum(street_key(p) == "key a" for p in picks)
    assert 850 <= hits <= 950


def _rows3() -> list[dict]:
    return [
        {
            "id": f"t2-{i:03d}",
            "areas_hash": f"h{i}",
            "seq": 0,
            "name": "Gorordo Ave",
            "areas_raw": "raw",
            "towns": ["Cebu City"],
            "barangays": ["730600040"],
            "town_psgc": "730600000",
        }
        for i in range(1, 4)
    ]


def _dump(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _v2_files(tmp_path, gold="SECRETGOLD"):
    """A frozen rows file and a labelled test file under tmp/evals/streets."""
    from osm_world import OSM_BASE

    from outages.evaluate import freeze

    d = tmp_path / "evals" / "streets"
    d.mkdir(parents=True, exist_ok=True)
    rows = _rows3()
    _dump(d / "test.rows.jsonl", rows)
    freeze(d / "test.rows.jsonl", d / "ROWS_FROZEN")
    golds = [[gold, "Other"], gold, None]
    labelled = [{**r, "gold": g, "osm_base": OSM_BASE} for r, g in zip(rows, golds, strict=True)]
    labelled[2]["note"] = "two roads"
    _dump(d / "test.jsonl", labelled)
    return d


def _lines(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / "test.jsonl").read_text(encoding="utf-8").splitlines()]


def test_check_labels_counts(tmp_path):
    from osm_world import OSM_BASE

    from outages.streets.evaluate import check_labels

    d = _v2_files(tmp_path)
    c = check_labels(d / "test.rows.jsonl", d / "test.jsonl", OSM_BASE)
    assert c == {"rows": 3, "null": 1, "non_null": 2, "lists": 1, "notes": 1}


def test_check_labels_mismatch(tmp_path):
    from osm_world import OSM_BASE

    from outages.streets.evaluate import GuardError, check_labels

    d = _v2_files(tmp_path)
    rows_p, test_p = d / "test.rows.jsonl", d / "test.jsonl"
    good = _lines(d)
    _dump(test_p, good[:2])  # a dropped row
    with pytest.raises(GuardError, match="t2-003"):
        check_labels(rows_p, test_p, OSM_BASE)
    _dump(test_p, [good[0], {**good[1], "seq": 9}, good[2]])  # a changed seq
    with pytest.raises(GuardError, match="t2-002"):
        check_labels(rows_p, test_p, OSM_BASE)
    no_gold = {k: v for k, v in good[1].items() if k != "gold"}
    _dump(test_p, [good[0], no_gold, good[2]])
    with pytest.raises(GuardError, match="t2-002.*gold"):
        check_labels(rows_p, test_p, OSM_BASE)
    _dump(test_p, [good[0], {**good[1], "gold": 7}, good[2]])
    with pytest.raises(GuardError, match="t2-002"):
        check_labels(rows_p, test_p, OSM_BASE)
    _dump(test_p, good)
    with pytest.raises(GuardError, match="osm_base"):
        check_labels(rows_p, test_p, "2030-01-01T00:00:00Z")


def test_check_labels_rows_changed_after_freeze(tmp_path):
    from osm_world import OSM_BASE

    from outages.streets.evaluate import GuardError, check_labels

    d = _v2_files(tmp_path)
    with (d / "test.rows.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(GuardError, match="ROWS_FROZEN"):
        check_labels(d / "test.rows.jsonl", d / "test.jsonl", OSM_BASE)


def test_review_sample_stratified():
    from outages.streets.evaluate import review_sample

    rows = [
        {"id": f"t2-{t}{i}", "town_psgc": town, "gold": None if i % 2 else f"G{t}-{i}"}
        for t, town in enumerate(TOWNS)
        for i in range(10)
    ]
    rows[3]["gold"] = ["A", "B"]
    rows[5]["note"] = "two roads"
    picked = review_sample(rows, n=25, seed=1)
    assert len(picked) == 25 and len({r["id"] for r in picked}) == 25
    ids = {r["id"] for r in picked}
    assert rows[3]["id"] in ids and rows[5]["id"] in ids
    assert {r["town_psgc"] for r in picked} == set(TOWNS)
    assert any(r["gold"] is None for r in picked) and any(r["gold"] for r in picked)
    assert picked == sorted(picked, key=lambda r: r["id"])
    assert review_sample(rows, n=25, seed=1) == picked


@pytest.fixture
def offline_cli(monkeypatch, tmp_path):
    from map_world import boundaries
    from osm_world import write

    from outages import __main__ as cli
    from outages.osm import overpass

    def boom(*a, **kw):
        raise AssertionError("offline command built a network client")

    monkeypatch.setattr(cli, "make_fetcher", boom)
    monkeypatch.setattr(cli, "make_telegram", boom)
    monkeypatch.setattr(overpass, "make_client", boom)
    monkeypatch.setattr(cli, "load_boundaries", lambda: boundaries())
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "data" / "osm")
    return tmp_path


def test_sample_v2_cli_writes_and_refuses_overwrite(capsys, offline_cli):
    from map_world import populate

    tmp_path = offline_cli
    c = db.connect(tmp_path / "data" / "outages.db")
    populate(c)
    c.close()
    d = tmp_path / "evals" / "streets"
    d.mkdir(parents=True)
    _dump(d / "dev.jsonl", [{"name": "Salinas Dr."}])
    assert main(["eval", "sample", "--suite", "streets-v2"]) == 0
    text = (d / "test.rows.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(x) for x in text.splitlines()]
    assert rows and all("gold" not in r for r in rows)
    assert "salinas drive" not in {street_key(r["name"]) for r in rows}
    assert (d / "ROWS_FROZEN").is_file()
    capsys.readouterr()
    assert main(["eval", "sample", "--suite", "streets-v2"]) == 2
    assert "exists" in capsys.readouterr().err
    (d / "test.rows.jsonl").unlink()
    assert main(["eval", "sample", "--suite", "streets-v2"]) == 2  # ROWS_FROZEN alone


def test_check_labels_cli_counts_only(capsys, offline_cli):
    _v2_files(offline_cli)
    assert main(["eval", "check", "--suite", "streets-v2"]) == 0
    out = capsys.readouterr().out
    assert "rows 3" in out and "null 1" in out
    assert "SECRETGOLD" not in out and "Gorordo" not in out


def test_check_labels_cli_mismatch_exit_2(capsys, offline_cli):
    d = _v2_files(offline_cli)
    _dump(d / "test.jsonl", _lines(d)[:2])
    assert main(["eval", "check", "--suite", "streets-v2"]) == 2
    err = capsys.readouterr().err
    assert "t2-003" in err and "SECRETGOLD" not in err


def test_review_sample_writes_data_file(capsys, offline_cli):
    _v2_files(offline_cli)
    assert main(["eval", "review-sample", "--suite", "streets-v2", "--n", "25"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == str(Path("data") / "streets" / "label-review.md")
    text = (offline_cli / "data" / "streets" / "label-review.md").read_text(encoding="utf-8")
    assert "SECRETGOLD" in text and "t2-001" in text and "Gorordo Avenue" in text


def test_freeze_v2_cli(offline_cli):
    d = _v2_files(offline_cli)
    assert main(["eval", "freeze", "--suite", "streets-v2"]) == 0
    assert (d / "FROZEN").is_file()
    assert main(["eval", "freeze", "--suite", "streets-v2"]) == 2  # already frozen
    (d / "FROZEN").unlink()
    (d / "test.jsonl").write_text("", encoding="utf-8")
    assert main(["eval", "freeze", "--suite", "streets-v2"]) == 2  # the check fails first
    assert not (d / "FROZEN").exists()
