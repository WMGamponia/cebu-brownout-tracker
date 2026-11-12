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


def no_telegram(monkeypatch):
    """Fail if a command that must not talk to Telegram builds the client."""
    from outages import __main__ as cli

    def boom(token):
        raise AssertionError("this command must not build a Telegram client")

    monkeypatch.setattr(cli, "make_telegram", boom)


TOKEN = "123456:SECRET-abcdefghijklmnop"


@pytest.fixture
def no_tg_env(monkeypatch, tmp_path):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)


def test_alerts_dry_run_offline(capsys, monkeypatch, tmp_path, no_tg_env):
    import hashlib

    from outages import db

    no_network(monkeypatch)
    no_telegram(monkeypatch)
    path = tmp_path / "data" / "outages.db"
    db.connect(path).close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(["alerts", "--dry-run"]) == 0
    assert "would send 0 message(s)" in capsys.readouterr().out
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_alerts_dry_run_without_db(capsys, monkeypatch, no_tg_env):
    no_network(monkeypatch)
    no_telegram(monkeypatch)
    assert main(["alerts", "--dry-run"]) == 1
    assert "no database" in capsys.readouterr().err


def test_alerts_needs_a_mode(capsys, no_tg_env):
    with pytest.raises(SystemExit) as exc:
        main(["alerts"])
    assert exc.value.code == 2


def test_alerts_send_without_token_exits_1(capsys, monkeypatch, no_tg_env):
    no_network(monkeypatch)
    no_telegram(monkeypatch)
    assert main(["alerts", "--send"]) == 1
    out = capsys.readouterr()
    assert "error: TELEGRAM_BOT_TOKEN not set" in out.err
    assert "SECRET" not in out.out + out.err


def test_alerts_send_bad_chat_id_exits_1(capsys, monkeypatch, no_tg_env):
    no_network(monkeypatch)
    no_telegram(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "me")
    assert main(["alerts", "--send"]) == 1
    out = capsys.readouterr()
    assert "TELEGRAM_ADMIN_CHAT_ID" in out.err and "SECRET" not in out.out + out.err


def test_alerts_send_runs_offline_toward_veco(capsys, monkeypatch, tmp_path, no_tg_env):
    import httpx

    from outages import __main__ as cli
    from outages.telegram import TelegramClient

    no_network(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(
        cli, "make_telegram", lambda t: TelegramClient(t, transport=httpx.MockTransport(handler))
    )
    assert main(["alerts", "--send"]) == 0
    out = capsys.readouterr()
    assert "alerts: 0 subscribers, sent 0" in out.out
    assert calls == [] and "SECRET" not in out.out + out.err
    log = (tmp_path / "data" / "logs" / "outages.log").read_text(encoding="utf-8")
    assert "SECRET" not in log


def test_report_offline_builds_no_telegram(capsys, monkeypatch, no_tg_env):
    no_network(monkeypatch)
    no_telegram(monkeypatch)
    assert main(["report"]) == 0
    assert "alerts: 0 subscribers, 0 sent; unparsed areas: 0 upcoming outages" in (
        capsys.readouterr().out
    )


def test_bot_command_offline(capsys, monkeypatch, tmp_path, no_tg_env):
    import httpx

    from outages import __main__ as cli
    from outages.telegram import TelegramClient

    no_network(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    polls = {"n": 0}

    def handler(req):
        method = req.url.path.rsplit("/", 1)[1]
        if method == "getUpdates":
            polls["n"] += 1
            if polls["n"] > 1:
                raise KeyboardInterrupt  # Ctrl+C during the second long poll
            update = {
                "update_id": 1,
                "message": {
                    "message_id": 1,
                    "chat": {"id": 7, "type": "private"},
                    "from": {"id": 7},
                    "text": "/start",
                },
            }
            return httpx.Response(200, json={"ok": True, "result": [update]})
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(
        cli, "make_telegram", lambda t: TelegramClient(t, transport=httpx.MockTransport(handler))
    )
    assert main(["bot"]) == 0
    out = capsys.readouterr()
    assert "SECRET" not in out.out + out.err
    log = (tmp_path / "data" / "logs" / "outages.log").read_text(encoding="utf-8")
    assert "bot: message from chat 7 (not allowed)" in log and "SECRET" not in log


def test_bot_without_token_exits_1(capsys, monkeypatch, no_tg_env):
    no_telegram(monkeypatch)
    assert main(["bot"]) == 1
    assert "TELEGRAM_BOT_TOKEN not set" in capsys.readouterr().err


# --- Phase 4: the map ------------------------------------------------------------------------


def _record_serving(monkeypatch, ok: bool = True) -> list:
    from outages import __main__ as cli

    seen: list = []

    def fake(app, host, port):
        seen.append((host, port))
        return ok

    monkeypatch.setattr(cli.map_server, "serve_forever", fake)
    return seen


def test_map_command_offline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    no_network(monkeypatch)
    no_telegram(monkeypatch)
    seen = _record_serving(monkeypatch)
    assert main(["map"]) == 0
    assert seen == [("127.0.0.1", 8765)]
    assert not (tmp_path / "data" / "outages.db").exists()  # map never creates the DB


def test_map_command_host_port(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    seen = _record_serving(monkeypatch)
    assert main(["map", "--port", "9001"]) == 0
    assert seen == [("127.0.0.1", 9001)]


def test_map_command_port_failure_exits_1(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    _record_serving(monkeypatch, ok=False)
    assert main(["map"]) == 1
    assert "8765" in capsys.readouterr().err


def test_map_command_bad_boundaries_exits_1(monkeypatch, tmp_path, capsys):
    from outages import __main__ as cli
    from outages.map.build import BoundariesError

    monkeypatch.chdir(tmp_path)
    seen = _record_serving(monkeypatch)

    def bad():
        raise BoundariesError("boundaries.geojson is broken")

    monkeypatch.setattr(cli, "load_boundaries", bad)
    assert main(["map"]) == 1
    assert "boundaries.geojson is broken" in capsys.readouterr().err
    assert seen == []


def test_non_loopback_warns(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.chdir(tmp_path)
    _record_serving(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="outages"):
        assert main(["map", "--host", "0.0.0.0"]) == 0
    assert "map is reachable from your network; there is no login" in caplog.text


def test_loopback_does_not_warn(monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(tmp_path)
    _record_serving(monkeypatch)
    assert main(["map"]) == 0
    assert "reachable from your network" not in caplog.text


def test_map_command_ctrl_c_is_a_clean_stop(monkeypatch, tmp_path, capsys):
    """uvicorn re-raises SIGINT after its own clean shutdown; Ctrl+C is how `map` stops."""
    from outages import __main__ as cli

    monkeypatch.chdir(tmp_path)

    def interrupted(app, host, port):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.map_server, "serve_forever", interrupted)
    assert main(["map"]) == 0
    err = capsys.readouterr().err
    assert "resume" not in err and "error" not in err


def no_overpass(monkeypatch):
    """Fail if a command builds the Overpass client."""
    from outages.osm import overpass

    def boom(**kw):
        raise AssertionError("this command must not build an Overpass client")

    monkeypatch.setattr(overpass, "make_client", boom)


def test_streets_fetch_without_yes_builds_no_client(capsys, monkeypatch, tmp_path):
    from outages.config import VECO_TOWNS

    no_network(monkeypatch)
    no_overpass(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert main(["streets", "fetch"]) == 2
    out = capsys.readouterr().out
    for town in VECO_TOWNS:
        assert town in out
    assert "--yes" in out
    assert "overpass-api.de" in out
    assert not (tmp_path / "data" / "osm").exists()


def test_streets_fetch_yes_reports(capsys, monkeypatch, tmp_path):
    from outages.osm import overpass

    no_network(monkeypatch)
    monkeypatch.chdir(tmp_path)

    class FakeClient:
        def close(self):
            pass

    monkeypatch.setattr(overpass, "make_client", lambda **kw: FakeClient())

    def fake_fetch_all(client, boundaries, out_dir, now, sleep=None):
        out_dir.mkdir(parents=True)
        (out_dir / "manifest.json").write_text(
            '{"osm_base": "2026-11-12T03:00:00Z", "towns": {"730600000": {"ways": 5}}}',
            encoding="utf-8",
        )
        (out_dir / "streets-730600000.json").write_text("{}", encoding="utf-8")
        return overpass.FetchSummary(ok=["730600000"], failed={"731300000": "0 ways"})

    monkeypatch.setattr(overpass, "fetch_all", fake_fetch_all)
    assert main(["streets", "fetch", "--yes"]) == 1  # one town failed
    out = capsys.readouterr().out
    assert "Cebu City" in out and "5 ways" in out
    assert "Mandaue City" in out and "0 ways" in out
    assert "2026-11-12T03:00:00Z" in out
