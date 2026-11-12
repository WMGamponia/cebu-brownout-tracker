"""The four guards on the single Streets v2 test run (spec 6.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from map_world import LAHUG, boundaries
from osm_world import OSM_BASE, write

from outages.__main__ import main
from outages.evaluate import freeze
from outages.streets.evaluate import (
    ALIASES_FROZEN,
    GuardError,
    run_streets,
    test_guards,
)

NOW = datetime(2026, 11, 13, tzinfo=UTC)


def _dump(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _world(tmp_path) -> tuple[Path, Path, Path]:
    """A complete, guarded test split: rows + ROWS_FROZEN, test + FROZEN, aliases + frozen."""
    d = tmp_path / "evals" / "streets"
    d.mkdir(parents=True)
    rows = [
        {
            "id": f"t2-{i:03d}",
            "areas_hash": f"h{i}",
            "seq": i,
            "name": "Gorordo Ave",
            "areas_raw": "raw",
            "towns": ["Cebu City"],
            "barangays": [LAHUG],
            "town_psgc": "730600000",
        }
        for i in (1, 2)
    ]
    _dump(d / "test.rows.jsonl", rows)
    freeze(d / "test.rows.jsonl", d / "ROWS_FROZEN")
    _dump(d / "test.jsonl", [{**r, "gold": "Gorordo Avenue", "osm_base": OSM_BASE} for r in rows])
    freeze(d / "test.jsonl", d / "FROZEN")
    aliases = tmp_path / "street_aliases.csv"
    aliases.write_text("town_psgc,veco_key,osm_name\n", encoding="utf-8")
    freeze(aliases, d / ALIASES_FROZEN)
    data = tmp_path / "data"
    (data / "streets").mkdir(parents=True)
    return d, data, aliases


def test_guards_pass_and_report(tmp_path):
    d, data, aliases = _world(tmp_path)
    lines = test_guards(d, data, aliases, OSM_BASE)
    text = "\n".join(lines)
    assert len(lines) == 4
    assert "FROZEN" in text and "ROWS_FROZEN" in text and "ALIASES_FROZEN" in text
    assert OSM_BASE in text and "no suggester inputs" in text


def test_guard_test_hash(tmp_path):
    d, data, aliases = _world(tmp_path)
    with (d / "test.jsonl").open("a", encoding="utf-8") as f:
        f.write("\n")
    with pytest.raises(GuardError, match="test.jsonl changed"):
        test_guards(d, data, aliases, OSM_BASE)
    (d / "FROZEN").unlink()
    with pytest.raises(GuardError, match="not frozen"):
        test_guards(d, data, aliases, OSM_BASE)


def test_guard_rows_hash(tmp_path):
    d, data, aliases = _world(tmp_path)
    rows = (d / "test.rows.jsonl").read_text(encoding="utf-8")
    (d / "test.rows.jsonl").write_text(rows.replace("t2-002", "t2-009"), encoding="utf-8")
    with pytest.raises(GuardError, match="ROWS_FROZEN"):
        test_guards(d, data, aliases, OSM_BASE)
    # rows re-frozen with other ids: the labelled file no longer fits
    freeze(d / "test.rows.jsonl", d / "ROWS_FROZEN")
    with pytest.raises(GuardError, match="t2-009"):
        test_guards(d, data, aliases, OSM_BASE)


def test_guard_aliases_hash(tmp_path):
    d, data, aliases = _world(tmp_path)
    with aliases.open("a", encoding="utf-8") as f:
        f.write("730600000,X St.,X Street\n")
    with pytest.raises(GuardError, match="street_aliases.csv changed"):
        test_guards(d, data, aliases, OSM_BASE)
    (d / ALIASES_FROZEN).unlink()
    with pytest.raises(GuardError, match="ALIASES_FROZEN"):
        test_guards(d, data, aliases, OSM_BASE)


def test_guard_osm_base(tmp_path):
    d, data, aliases = _world(tmp_path)
    with pytest.raises(GuardError, match="2030-01-01T00:00:00Z"):
        test_guards(d, data, aliases, "2030-01-01T00:00:00Z")


def test_guard_input_log(tmp_path):
    d, data, aliases = _world(tmp_path)
    log = data / "streets" / "suggest_inputs.jsonl"
    _dump(log, [{"areas_hash": "other", "seq": 0}, {"areas_hash": "h1", "seq": 9}])
    assert "2 suggester inputs" in "\n".join(test_guards(d, data, aliases, OSM_BASE))
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"areas_hash": "h2", "seq": 2}) + "\n")
    with pytest.raises(GuardError, match="t2-002"):
        test_guards(d, data, aliases, OSM_BASE)


def test_run_streets_test_prints_guards(tmp_path):
    d, data, aliases = _world(tmp_path)
    osm = write(tmp_path / "osm")
    out, s = run_streets("test", d, osm, boundaries(), NOW, data_dir=data, aliases_path=aliases)
    text = out.read_text(encoding="utf-8")
    assert out.name == "2026-11-13-test.md"
    head = text.split("| id |")[0]
    assert "ROWS_FROZEN" in head and "ALIASES_FROZEN" in head and "suggester" in head
    assert s["drawn"] == 2


def test_second_run_same_day_needs_again(tmp_path):
    d, data, aliases = _world(tmp_path)
    osm = write(tmp_path / "osm")
    kw = {"data_dir": data, "aliases_path": aliases}
    run_streets("test", d, osm, boundaries(), NOW, **kw)
    with pytest.raises(GuardError, match="--again"):
        run_streets("test", d, osm, boundaries(), NOW, **kw)
    out, _ = run_streets("test", d, osm, boundaries(), NOW, again=True, **kw)
    assert out.name == "2026-11-13-test-2.md"
    assert "--again" in out.read_text(encoding="utf-8")


def test_eval_run_test_guard_cli_exit_2(capsys, monkeypatch, tmp_path):
    from outages import __main__ as cli

    monkeypatch.setattr(cli, "load_boundaries", lambda: boundaries())
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "data" / "osm")
    d = tmp_path / "evals" / "streets"
    d.mkdir(parents=True)
    _dump(d / "test.jsonl", [])
    assert main(["eval", "run", "--suite", "streets", "--split", "test"]) == 2
    assert "not frozen" in capsys.readouterr().err


def test_freeze_aliases_cli(capsys, monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages.evaluate import _file_sha

    aliases = tmp_path / "a.csv"
    aliases.write_text("town_psgc,veco_key,osm_name\n", encoding="utf-8")
    monkeypatch.setattr(cli, "ALIASES_PATH", aliases)
    monkeypatch.chdir(tmp_path)
    assert main(["eval", "freeze-aliases"]) == 0
    frozen = tmp_path / "evals" / "streets" / ALIASES_FROZEN
    assert frozen.read_text(encoding="utf-8").strip() == _file_sha(aliases)
