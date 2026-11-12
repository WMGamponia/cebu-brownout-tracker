"""`serve`: runs at 06:00 and 20:00, check then alerts, catch-up, bot thread (Phase 3 spec 4.5,
5.9). Nothing here reaches VECO or Telegram."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from outages import __main__ as cli
from outages import db, pipeline, scheduler
from outages.config import Paths, TelegramSettings
from outages.telegram import TelegramClient

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
TOKEN = "123456:SECRET-abcdefghijklmnop"
SETTINGS = TelegramSettings(TOKEN, frozenset({42}), 999)


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    # serve starts the map thread by default; never open a real port in these tests
    monkeypatch.setattr(cli, "make_map_app", lambda paths, *a: object())
    monkeypatch.setattr(cli.map_server, "start_thread", lambda *a, **k: None)
    return Paths(tmp_path)


def mock_telegram(monkeypatch):
    monkeypatch.setattr(
        cli,
        "make_telegram",
        lambda t: TelegramClient(
            t, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))
        ),
    )


class _Fetcher:
    def close(self):
        pass


def test_scheduler_jobs_at_run_times():
    sched = scheduler.build(lambda: None)
    jobs = sorted(sched.get_jobs(), key=lambda j: j.id)
    assert [j.id for j in jobs] == ["run-06:00", "run-20:00"]
    assert sorted(str(j.trigger) for j in jobs) == [
        "cron[hour='20', minute='0']",
        "cron[hour='6', minute='0']",
    ]
    assert all(str(j.trigger.timezone) == "Asia/Manila" for j in jobs)
    assert all(j.misfire_grace_time == 21600 and j.coalesce and j.max_instances == 1 for j in jobs)


def _record_alert_runs(monkeypatch) -> list[bool]:
    seen: list[bool] = []

    def fake(conn, notifier, now, gaz, admin_chat_id, check_ok):
        seen.append(check_ok)

    monkeypatch.setattr(cli.alerts_run, "alert_run", fake)
    return seen


def test_scheduled_run_alerts_after_failed_check(home, monkeypatch):
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)

    def down(*a, **k):
        raise httpx.ConnectError("VECO down")

    monkeypatch.setattr(pipeline, "check", down)
    mock_telegram(monkeypatch)
    seen = _record_alert_runs(monkeypatch)
    cli._scheduled_run(home, SETTINGS)
    assert seen == [False]


def test_scheduled_run_fetch_errors_mean_check_not_ok(home, monkeypatch):
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)
    monkeypatch.setattr(pipeline, "check", lambda *a, **k: pipeline.CheckSummary(errors=1))
    mock_telegram(monkeypatch)
    seen = _record_alert_runs(monkeypatch)
    cli._scheduled_run(home, SETTINGS)
    assert seen == [False]


def test_scheduled_run_ok_check_and_skip_check(home, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)
    monkeypatch.setattr(
        pipeline, "check", lambda *a, **k: calls.append("check") or pipeline.CheckSummary()
    )
    mock_telegram(monkeypatch)
    seen = _record_alert_runs(monkeypatch)
    cli._scheduled_run(home, SETTINGS)
    cli._scheduled_run(home, SETTINGS, do_check=False)
    assert seen == [True, True] and calls == ["check"]


def test_scheduled_run_alert_error_is_logged(home, monkeypatch, caplog):
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)
    monkeypatch.setattr(pipeline, "check", lambda *a, **k: pipeline.CheckSummary())
    mock_telegram(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("alert run broke")

    monkeypatch.setattr(cli.alerts_run, "alert_run", boom)
    cli._scheduled_run(home, SETTINGS)  # never raises into the scheduler
    assert "alert run failed" in caplog.text


def test_scheduled_run_without_settings_checks_only(home, monkeypatch):
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)
    monkeypatch.setattr(pipeline, "check", lambda *a, **k: pipeline.CheckSummary())
    monkeypatch.setattr(cli, "make_telegram", lambda t: pytest.fail("built Telegram"))
    seen = _record_alert_runs(monkeypatch)
    cli._scheduled_run(home, None)
    assert seen == []


def _last_check(paths: Paths, hours_ago: int) -> None:
    conn = db.connect(paths.db)
    db.set_meta(conn, "last_check_at", (NOW - timedelta(hours=hours_ago)).isoformat())
    conn.commit()
    conn.close()


def test_serve_without_token_checks_only(home, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="outages")
    _last_check(home, 13)
    calls: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True, **k: calls.append((s, do_check))
    )
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(cli.bot, "poll_forever", lambda *a, **k: pytest.fail("bot started"))
    assert cli.main(["serve"]) == 0
    assert calls == [(None, True), "start"]
    assert "alerts disabled" in caplog.text


def test_serve_without_token_recent_check_does_nothing_on_start(home, monkeypatch):
    _last_check(home, 2)
    calls: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True, **k: calls.append(do_check)
    )
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    assert cli.main(["serve"]) == 0
    assert calls == ["start"]


def test_serve_starts_bot_thread_and_catch_up(home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    _last_check(home, 13)
    mock_telegram(monkeypatch)
    started = threading.Event()
    box: dict = {}

    def fake_poll(client, db_path, gaz, allowed, stop, sleep=None, admin_chat_id=None):
        box["stop"], box["allowed"] = stop, allowed
        box["thread"] = threading.current_thread()
        started.set()
        stop.wait(5)

    calls: list = []
    monkeypatch.setattr(cli.bot, "poll_forever", fake_poll)
    monkeypatch.setattr(
        cli,
        "_scheduled_run",
        lambda p, s, do_check=True, **k: calls.append((s is not None, do_check)),
    )

    def start(self):
        assert started.wait(2)
        calls.append("start")

    monkeypatch.setattr(scheduler.BlockingScheduler, "start", start)
    assert cli.main(["serve"]) == 0
    assert calls == [(True, True), "start"]
    assert box["thread"].daemon and box["allowed"] == frozenset({42})
    assert box["stop"].is_set()


def test_serve_recent_check_still_runs_alerts(home, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    _last_check(home, 2)
    mock_telegram(monkeypatch)
    monkeypatch.setattr(cli.bot, "poll_forever", lambda *a, **k: None)
    calls: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True, **k: calls.append(do_check)
    )
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    assert cli.main(["serve"]) == 0
    assert calls == [False, "start"]


# --- Phase 4: the map thread in serve ----------------------------------------------------------


def _quiet_serve(monkeypatch) -> list:
    calls: list = []
    monkeypatch.setattr(cli, "_scheduled_run", lambda p, s, do_check=True, **k: calls.append("run"))
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(cli.bot, "poll_forever", lambda *a, **k: None)
    return calls


def test_serve_starts_map_thread(home, monkeypatch):
    _last_check(home, 2)
    _quiet_serve(monkeypatch)
    started: list = []
    monkeypatch.setattr(
        cli.map_server,
        "start_thread",
        lambda app, host, port, on_error: started.append((host, port)),
    )
    assert cli.main(["serve", "--map-port", "9002"]) == 0
    assert started == [("127.0.0.1", 9002)]


def test_serve_no_map_starts_no_thread(home, monkeypatch):
    _last_check(home, 2)
    calls = _quiet_serve(monkeypatch)
    monkeypatch.setattr(cli, "make_map_app", lambda paths, *a: pytest.fail("built the map app"))
    monkeypatch.setattr(
        cli.map_server, "start_thread", lambda *a, **k: pytest.fail("started the map")
    )
    assert cli.main(["serve", "--no-map"]) == 0
    assert calls == ["start"]


def _recording_telegram(monkeypatch) -> list:
    sent: list = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(
        cli, "make_telegram", lambda t: TelegramClient(t, transport=httpx.MockTransport(handler))
    )
    return sent


def _failing_map(monkeypatch) -> None:
    def start_thread(app, host, port, on_error):
        on_error(f"map: could not listen on {host}:{port}")

    monkeypatch.setattr(cli.map_server, "start_thread", start_thread)


def test_map_thread_bind_failure_reports_and_scheduler_runs(home, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "999")
    _last_check(home, 2)
    sent = _recording_telegram(monkeypatch)
    calls = _quiet_serve(monkeypatch)
    _failing_map(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="outages"):
        assert cli.main(["serve"]) == 0
    assert "start" in calls  # the 06:00/20:00 runs are still scheduled
    assert "could not listen on 127.0.0.1:8765" in caplog.text
    notes = [r for r in sent if r.url.path.endswith("/sendMessage")]
    assert len(notes) == 1 and b"8765" in notes[0].content
    conn = db.connect(home.db)
    assert conn.execute("SELECT key FROM admin_notes_sent").fetchall()[0][0] == (
        "map_failed:2026-11-12"
    )
    conn.close()
    # a second failure the same Manila day sends nothing
    assert cli.main(["serve"]) == 0
    assert len([r for r in sent if r.url.path.endswith("/sendMessage")]) == 1


def test_map_bad_boundaries_in_serve_keeps_alerts(home, monkeypatch, caplog):
    from outages.map.build import BoundariesError

    _last_check(home, 2)
    calls = _quiet_serve(monkeypatch)

    def bad(paths, *a):
        raise BoundariesError("boundaries missing")

    monkeypatch.setattr(cli, "make_map_app", bad)
    with caplog.at_level(logging.ERROR, logger="outages"):
        assert cli.main(["serve"]) == 0
    assert calls == ["start"]
    assert "boundaries missing" in caplog.text


def test_serve_map_host_non_loopback_warns(home, monkeypatch, caplog):
    _last_check(home, 2)
    _quiet_serve(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="outages"):
        assert cli.main(["serve", "--map-host", "0.0.0.0"]) == 0
    assert "map is reachable from your network; there is no login" in caplog.text


def test_map_unexpected_startup_error_keeps_alerts(home, monkeypatch, caplog):
    """Spec 6: whatever stops the map from starting, serve keeps the alert runs."""
    _last_check(home, 2)
    calls = _quiet_serve(monkeypatch)

    def broken(paths, *a):
        raise AttributeError("'list' object has no attribute 'get'")

    monkeypatch.setattr(cli, "make_map_app", broken)
    with caplog.at_level(logging.ERROR, logger="outages"):
        assert cli.main(["serve"]) == 0
    assert calls == ["start"]
    assert "has no attribute" in caplog.text


# --- Streets v2: automatic suggestions after each run (spec 7.8) -------------------------------


def _record_suggest(monkeypatch, order: list, summary=None):
    def fake(paths, now, **kw):
        order.append("suggest")
        return summary

    monkeypatch.setattr(cli.street_suggest, "suggest_new", fake)


def test_hook_runs_after_alerts(home, monkeypatch):
    order: list = []
    mock_telegram(monkeypatch)
    monkeypatch.setattr(cli.alerts_run, "alert_run", lambda *a, **k: order.append("alerts"))
    _record_suggest(monkeypatch, order)
    cli._scheduled_run(home, SETTINGS, do_check=False)
    assert order == ["alerts", "suggest"]


def test_hook_runs_after_failed_alerts_and_without_settings(home, monkeypatch):
    order: list = []
    mock_telegram(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("alert run broke")

    monkeypatch.setattr(cli.alerts_run, "alert_run", boom)
    _record_suggest(monkeypatch, order)
    cli._scheduled_run(home, SETTINGS, do_check=False)
    cli._scheduled_run(home, None, do_check=False)
    assert order == ["suggest", "suggest"]


def test_no_suggest_flag_and_switch(home, monkeypatch):
    order: list = []
    mock_telegram(monkeypatch)
    monkeypatch.setattr(cli.alerts_run, "alert_run", lambda *a, **k: None)
    _record_suggest(monkeypatch, order)
    cli._scheduled_run(home, SETTINGS, do_check=False, suggest=False)
    assert order == []
    seen: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True, suggest=True: seen.append(suggest)
    )
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: None)
    _last_check(home, 20)  # catch-up run on start
    assert cli.main(["serve", "--no-map", "--no-suggest"]) == 0
    monkeypatch.setattr(cli, "STREETS_SUGGEST_AUTO", False)
    assert cli.main(["serve", "--no-map"]) == 0
    monkeypatch.setattr(cli, "STREETS_SUGGEST_AUTO", True)
    assert cli.main(["serve", "--no-map"]) == 0
    assert seen == [False, False, True]


def test_hook_swallows_exceptions(home, monkeypatch, caplog):
    def boom(paths, now, **kw):
        raise RuntimeError("suggester broke")

    monkeypatch.setattr(cli.street_suggest, "suggest_new", boom)
    cli._scheduled_run(home, None, do_check=False)  # never raises into the scheduler
    assert "suggester broke" in caplog.text


def test_hook_admin_note_once(home, monkeypatch):
    from outages.streets.suggest import RunSummary

    sent: list = []

    def handler(request):
        sent.append(request.content.decode())
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr(
        cli, "make_telegram", lambda t: TelegramClient(t, transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(cli.alerts_run, "alert_run", lambda *a, **k: None)
    _record_suggest(monkeypatch, [], RunSummary(new=3, picked=2, invalid=0, no_candidates=1))
    cli._scheduled_run(home, SETTINGS, do_check=False)
    cli._scheduled_run(home, SETTINGS, do_check=False)
    assert len(sent) == 1
    assert "3 new street suggestions waiting (data/streets/alias_queue.csv)" in sent[0]
    conn = db.connect(home.db)
    from outages.alerts import store as alerts_store

    assert alerts_store.note_sent(conn, f"suggest:{NOW:%Y-%m-%d}:3")
    conn.close()


def test_hook_no_note_when_nothing_new(home, monkeypatch):
    from outages.streets.suggest import RunSummary

    mock_telegram(monkeypatch)
    monkeypatch.setattr(cli.alerts_run, "alert_run", lambda *a, **k: None)
    _record_suggest(monkeypatch, [], RunSummary(new=0, picked=0, invalid=0, no_candidates=0))
    monkeypatch.setattr(cli.alerts_run, "deliver_notes", lambda *a, **k: pytest.fail("noted"))
    cli._scheduled_run(home, SETTINGS, do_check=False)
