"""CLI argument handling."""

from __future__ import annotations

import pytest

from outages.__main__ import main


def test_help_lists_commands(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for cmd in ("backfill", "check", "reparse", "report", "serve"):
        assert cmd in out


def test_gazetteer_command_prints_counts(capsys, monkeypatch, tmp_path):
    from outages import gazetteer_build

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gazetteer_build, "run", lambda d: {"Cebu City": 80, "Liloan": 14})
    assert main(["gazetteer"]) == 0
    out = capsys.readouterr().out
    assert "Cebu City 80" in out and "total 94" in out


def test_eval_sample_refuses_when_frozen(capsys, monkeypatch, tmp_path):
    from outages import db

    monkeypatch.chdir(tmp_path)
    db.connect(tmp_path / "data" / "outages.db").close()
    frozen = tmp_path / "evals" / "areas" / "FROZEN"
    frozen.parent.mkdir(parents=True)
    frozen.write_text("x\n", encoding="utf-8")
    assert main(["eval", "sample", "--seed", "20261111"]) == 1
    assert "frozen" in capsys.readouterr().err


def test_unknown_command_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["frobnicate"])
    assert exc.value.code == 2


def test_extractor_from_config_qwen(monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages import db
    from outages.areas.llm import OllamaExtractor

    conn = db.connect(tmp_path / "t.db")
    monkeypatch.setattr(cli, "ENTITY_EXTRACTOR", "qwen")
    assert isinstance(cli.extractor_from_config(conn), OllamaExtractor)
    monkeypatch.setattr(cli, "ENTITY_EXTRACTOR", "rules")
    assert cli.extractor_from_config(conn).name == "rules"
    conn.close()


def test_areas_stops_when_ollama_unreachable(capsys, monkeypatch, tmp_path):
    from outages import __main__ as cli
    from outages import db
    from outages.areas import llm

    monkeypatch.chdir(tmp_path)
    db.connect(tmp_path / "data" / "outages.db").close()

    class Down:
        name = "qwen"

        def extract(self, seg):
            raise llm.LLMUnavailable(
                "Ollama not reachable at x; start it or set ENTITY_EXTRACTOR=rules"
            )

    monkeypatch.setattr(cli, "extractor_from_config", lambda conn: Down())
    monkeypatch.setattr(
        cli.areas_store,
        "pending_hashes",
        lambda conn, rebuild: [("h", "Portion of Cebu City (Lahug) along A St.")],
    )
    assert main(["areas"]) == 1
    assert "Ollama not reachable" in capsys.readouterr().err


def no_network(monkeypatch):
    """Fail if a command that must stay offline builds the VECO fetcher."""
    from outages import __main__ as cli

    def boom():
        raise AssertionError("this command must not fetch from VECO")

    monkeypatch.setattr(cli, "make_fetcher", boom)


def test_eval_run_refuses_unfrozen_test(capsys, monkeypatch, tmp_path):
    from outages import db

    no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    db.connect(tmp_path / "data" / "outages.db").close()
    d = tmp_path / "evals" / "areas"
    d.mkdir(parents=True)
    (d / "test.jsonl").write_text("{}\n", encoding="utf-8")
    assert main(["eval", "run", "--split", "test", "--arms", "rules"]) == 1
    assert "not frozen" in capsys.readouterr().err


def test_only_check_and_backfill_fetch(monkeypatch, tmp_path):
    from outages import __main__ as cli

    no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)
    args = cli._parser().parse_args(["eval", "run"])
    args.action = "frobnicate"
    with pytest.raises(SystemExit):
        cli._run(args, cli.Paths.default())


def test_suite_blocks_real_network():
    """conftest's autouse guard: no real HTTP from tests (localhost and `live` tests excepted)."""
    import httpx

    with pytest.raises(RuntimeError, match="network blocked in tests"):
        httpx.get("http://veco-guard-check.invalid/", timeout=2)
