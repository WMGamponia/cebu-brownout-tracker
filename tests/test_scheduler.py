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
        cli, "_scheduled_run", lambda p, s, do_check=True: calls.append((s, do_check))
    )
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    monkeypatch.setattr(cli.bot, "poll_forever", lambda *a, **k: pytest.fail("bot started"))
    assert cli.main(["serve"]) == 0
    assert calls == [(None, True), "start"]
    assert "alerts disabled" in caplog.text


def test_serve_without_token_recent_check_does_nothing_on_start(home, monkeypatch):
    _last_check(home, 2)
    calls: list = []
    monkeypatch.setattr(cli, "_scheduled_run", lambda p, s, do_check=True: calls.append(do_check))
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

    def fake_poll(client, db_path, gaz, allowed, stop, sleep=None):
        box["stop"], box["allowed"] = stop, allowed
        box["thread"] = threading.current_thread()
        started.set()
        stop.wait(5)

    calls: list = []
    monkeypatch.setattr(cli.bot, "poll_forever", fake_poll)
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True: calls.append((s is not None, do_check))
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
    monkeypatch.setattr(cli, "_scheduled_run", lambda p, s, do_check=True: calls.append(do_check))
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    assert cli.main(["serve"]) == 0
    assert calls == [False, "start"]
