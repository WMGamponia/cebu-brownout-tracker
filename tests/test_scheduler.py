"""`serve`: runs at 06:00 and 20:00, check then alerts, catch-up, bot thread (Phase 3 spec 4.5,
5.9). Nothing here reaches VECO or Telegram."""

from __future__ import annotations

import contextlib
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
    jobs = sorted((j for j in sched.get_jobs() if j.id != "watchdog"), key=lambda j: j.id)
    assert [j.id for j in jobs] == ["run-06:00", "run-20:00"]
    assert sorted(str(j.trigger) for j in jobs) == [
        "cron[hour='20', minute='0']",
        "cron[hour='6', minute='0']",
    ]
    assert all(str(j.trigger.timezone) == "Asia/Manila" for j in jobs)
    assert all(j.misfire_grace_time == 21600 and j.coalesce and j.max_instances == 1 for j in jobs)


def test_scheduler_watchdog_caps_the_wait():
    """Windows wait timeouts don't count time asleep (2026-11-14: the 06:00 run never came).
    A short interval job keeps every wait short, so a due run is seen soon after waking."""
    sched = scheduler.build(lambda: None, watchdog=lambda: None)
    dog = sched.get_job("watchdog")
    assert dog.trigger.interval == timedelta(minutes=5)
    assert dog.coalesce and dog.max_instances == 1 and dog.misfire_grace_time is None


MANILA_NOV14 = datetime.fromisoformat("2026-11-14T00:00:00+08:00")


def _at(hm: str) -> datetime:
    h, m = hm.split(":")
    return MANILA_NOV14 + timedelta(hours=int(h), minutes=int(m))


@pytest.mark.parametrize(
    ("now", "slot"),
    [
        ("06:00", "06:00"),
        ("11:24", "06:00"),
        ("19:59", "06:00"),
        ("20:00", "20:00"),
        ("05:59", "-04:00"),
    ],
)
def test_latest_slot(now, slot):
    assert scheduler.latest_slot(_at(now)) == _at(slot)


def test_missed_slot_the_2026_11_14_case():
    """Last check 13 Nov 20:01, PC asleep past the 6 h grace: at 11:24 the 06:00 run is missed."""
    assert scheduler.missed_slot("2026-11-13T12:01:55+00:00", _at("11:24")) == _at("06:00")


@pytest.mark.parametrize(
    ("last", "now"),
    [
        ("06:01", "18:00"),  # checked this slot; 20:00 not due yet (no spurious 18:00 run)
        ("2026-11-13T20:01", "05:00"),  # checked the previous slot
        ("2026-11-13T20:01", "06:05"),  # 06:00 just passed: leave it to the cron job
    ],
)
def test_missed_slot_none(last, now):
    last_at = (datetime.fromisoformat(last + "+08:00") if "T" in last else _at(last)).isoformat()
    assert scheduler.missed_slot(last_at, _at(now)) is None


def test_missed_slot_never_checked():
    assert scheduler.missed_slot(None, _at("11:24")) == _at("06:00")


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


# --- 2026-11-14: a run slept through must not be dropped ---------------------------------------


def _runs(home, monkeypatch, now: datetime) -> tuple[cli._Runs, list]:
    monkeypatch.setattr(cli, "utcnow", lambda: now)
    calls: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda p, s, do_check=True, **k: calls.append(do_check)
    )
    return cli._Runs(home, None), calls


def test_watchdog_runs_the_missed_06_00_once(home, monkeypatch, caplog):
    conn = db.connect(home.db)
    db.set_meta(conn, "last_check_at", "2026-11-13T12:01:55+00:00")  # 20:01 Manila
    conn.commit()
    conn.close()
    runs, calls = _runs(home, monkeypatch, _at("11:24"))
    with caplog.at_level(logging.WARNING, logger="outages"):
        runs.watchdog()
        runs.watchdog()  # once per slot, even if the check failed
    assert calls == [True]
    assert "missed the 06:00 run" in caplog.text


def test_watchdog_quiet_when_checked(home, monkeypatch):
    conn = db.connect(home.db)
    db.set_meta(conn, "last_check_at", _at("06:01").isoformat())
    conn.commit()
    conn.close()
    runs, calls = _runs(home, monkeypatch, _at("18:00"))
    runs.watchdog()
    assert calls == []


def test_cron_run_then_watchdog_does_not_repeat(home, monkeypatch):
    runs, calls = _runs(home, monkeypatch, _at("06:20"))  # cron fired late, check failed
    runs.scheduled()
    runs.watchdog()
    assert calls == [True]


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
    from outages.streets.suggest import Outcome

    def fake(paths, now, **kw):
        order.append("suggest")
        if summary is None:
            return Outcome("skipped", "no OSM extract", None)
        return Outcome("used", f"{summary.new} new street suggestions", summary)

    monkeypatch.setattr(cli.street_suggest, "suggest_outcome", fake)
    monkeypatch.setattr(cli.street_suggest, "inert_reason", lambda paths: None)


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
        cli,
        "_scheduled_run",
        lambda p, s, do_check=True, suggest=True, publisher=None, kind=None, progress=None: (
            seen.append(suggest)
        ),
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

    monkeypatch.setattr(cli.street_suggest, "suggest_outcome", boom)
    monkeypatch.setattr(cli.street_suggest, "inert_reason", lambda paths: None)
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


def test_watchdog_run_respects_no_suggest(home, monkeypatch):
    """Merge of the watchdog (PR #4) and the suggest hook: a caught-up run keeps `--no-suggest`."""
    monkeypatch.setattr(cli, "utcnow", lambda: _at("11:24"))
    seen: list = []
    monkeypatch.setattr(
        cli,
        "_scheduled_run",
        lambda p, s, do_check=True, suggest=True, publisher=None, kind=None, progress=None: (
            seen.append(suggest)
        ),
    )
    cli._Runs(home, None, suggest=False).scheduled()
    cli._Runs(home, None).scheduled()
    assert seen == [False, True]


# --- live site: publish after a successful check (live-site spec 5.1) --------------------------


class _Publisher:
    def __init__(self, order, outcome=None, error=None):
        self.order, self.outcome, self.error = order, outcome, error

    def publish(self, now):
        self.order.append("publish")
        if self.error:
            raise self.error
        from outages.publish import Outcome

        return self.outcome or Outcome("published", "abc1234 snapshot", None)


def _ordered_run(home, monkeypatch, check=None):
    order: list[str] = []
    monkeypatch.setattr(cli, "make_fetcher", _Fetcher)
    monkeypatch.setattr(
        pipeline,
        "check",
        check or (lambda *a, **k: order.append("check") or pipeline.CheckSummary()),
    )
    mock_telegram(monkeypatch)
    monkeypatch.setattr(cli.alerts_run, "alert_run", lambda *a, **k: order.append("alerts"))
    monkeypatch.setattr(cli, "_suggest_hook", lambda *a, **k: order.append("suggest"))
    return order


def test_publish_after_alerts_before_suggestions(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order))
    assert order == ["check", "alerts", "publish", "suggest"]


def test_no_publish_without_check(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, do_check=False, publisher=_Publisher(order))
    assert order == ["alerts", "suggest"]


def test_no_publish_after_failed_check(home, monkeypatch):
    order = _ordered_run(home, monkeypatch, check=lambda *a, **k: pipeline.CheckSummary(errors=1))
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order))
    assert order == ["alerts", "suggest"]


def test_publish_note_sent_once(home, monkeypatch):
    from outages.alerts.run import Note
    from outages.publish import Outcome

    order = _ordered_run(home, monkeypatch)
    sent: list[str] = []

    def handler(req):
        sent.append(req.url.path)
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr(
        cli, "make_telegram", lambda t: TelegramClient(t, transport=httpx.MockTransport(handler))
    )
    note = Note(("publish:fail:2026-11-12",), "Site publish failed: boom")
    pub = _Publisher(order, Outcome("failed", "boom", note))
    cli._scheduled_run(home, SETTINGS, publisher=pub)
    cli._scheduled_run(home, SETTINGS, publisher=pub)
    assert order.count("publish") == 2
    assert len([p for p in sent if p.endswith("/sendMessage")]) == 1


def test_publish_error_does_not_stop_suggestions(home, monkeypatch, caplog):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order, error=RuntimeError("boom")))
    assert order == ["check", "alerts", "publish", "suggest"]
    assert "publish failed" in caplog.text


def test_runs_pass_the_publisher(home, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_scheduled_run", lambda *a, **k: seen.append(k.get("publisher")))
    pub = object()
    runs = cli._Runs(home, SETTINGS, publisher=pub)
    runs.scheduled()
    assert seen == [pub]


# --- tray: each run's outcome is recorded in meta (tray spec 5.2) ------------------------------


def _status(home):
    from outages import runstatus

    conn = db.connect(home.db)
    try:
        return runstatus.read(conn)
    finally:
        conn.close()


def test_run_once_records_run_and_publish(home, monkeypatch):
    from outages.publish import Outcome

    order = _ordered_run(home, monkeypatch)
    pub = _Publisher(order, Outcome("skipped", "not on main", None))
    cli._scheduled_run(home, SETTINGS, publisher=pub)
    s = _status(home)
    assert (s.last_run_at, s.last_run_kind, s.last_check_ok) == (NOW, "scheduled", True)
    assert (s.publish_status, s.publish_detail, s.publish_at) == ("skipped", "not on main", NOW)


def test_failed_check_keeps_publish_status(home, monkeypatch):
    from outages import runstatus

    t0 = NOW - timedelta(hours=12)
    conn = db.connect(home.db)
    runstatus.record_publish(conn, "published", "abc1234 x", t0)
    conn.close()
    order = _ordered_run(home, monkeypatch, check=lambda *a, **k: pipeline.CheckSummary(errors=1))
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order))
    s = _status(home)
    assert s.last_check_ok is False
    assert (s.publish_status, s.publish_at) == ("published", t0)


def test_alerts_only_run_writes_nothing(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, do_check=False, publisher=_Publisher(order))
    assert _status(home).last_run_at is None


def test_no_publisher_records_off(home, monkeypatch):
    _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, publisher=None)
    assert _status(home).publish_status == "off"


def test_publish_error_records_failed(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order, error=RuntimeError("boom")))
    s = _status(home)
    assert (s.publish_status, s.publish_detail) == ("failed", "boom")


def test_status_write_failure_never_breaks_the_run(home, monkeypatch, caplog):
    from outages import runstatus

    order = _ordered_run(home, monkeypatch)

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(runstatus, "record_run", broken)
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order))
    assert order == ["check", "alerts", "publish", "suggest"]
    assert "could not record" in caplog.text


def _kinds(monkeypatch) -> list:
    kinds: list = []
    monkeypatch.setattr(
        cli, "_scheduled_run", lambda *a, **k: kinds.append(k.get("kind", "scheduled"))
    )
    return kinds


def test_kind_scheduled_and_watchdog(home, monkeypatch):
    kinds = _kinds(monkeypatch)
    monkeypatch.setattr(cli, "utcnow", lambda: _at("11:24"))
    conn = db.connect(home.db)
    db.set_meta(conn, "last_check_at", "2026-11-13T12:01:55+00:00")
    conn.commit()
    conn.close()
    cli._Runs(home, None).watchdog()
    monkeypatch.setattr(cli, "utcnow", lambda: _at("20:00"))
    cli._Runs(home, None).scheduled()
    assert kinds == ["watchdog", "scheduled"]


def test_kind_catch_up_on_start(home, monkeypatch):
    kinds = _kinds(monkeypatch)
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: None)
    assert cli.main(["serve"]) == 0
    assert kinds == ["catch_up"]


# --- tray: manual runs, admin control, clean stop (tray spec 5.5, 5.7) -------------------------


def test_manual_busy_returns_false(home, monkeypatch):
    calls: list = []
    monkeypatch.setattr(cli, "_scheduled_run", lambda *a, **k: calls.append(k))
    runs = cli._Runs(home, None)
    with runs._lock:
        assert runs.manual() is False
    assert calls == []


def test_manual_runs_kind_manual(home, monkeypatch):
    done = threading.Event()
    seen: list = []

    def fake(*a, **k):
        seen.append(k.get("kind"))
        done.set()

    monkeypatch.setattr(cli, "_scheduled_run", fake)
    runs = cli._Runs(home, None)
    assert runs.manual() is True
    assert done.wait(5)
    assert seen == ["manual"]
    deadline = datetime.now() + timedelta(seconds=5)
    while not runs._lock.acquire(blocking=False):  # released after the run
        assert datetime.now() < deadline
    runs._lock.release()


def test_manual_keeps_done_slot(home, monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(cli, "_scheduled_run", lambda *a, **k: done.set())
    runs = cli._Runs(home, None)
    runs.manual()
    assert done.wait(5)
    assert runs._done is None


def test_manual_run_error_releases_lock(home, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "_scheduled_run", boom)
    runs = cli._Runs(home, None)
    assert runs.manual() is True
    deadline = datetime.now() + timedelta(seconds=5)
    while not runs._lock.acquire(blocking=False):
        assert datetime.now() < deadline
    runs._lock.release()


def test_serve_writes_admin_token_and_passes_admin(home, monkeypatch):
    from outages import admintoken

    _last_check(home, 2)
    _quiet_serve(monkeypatch)
    seen: dict = {}

    def fake_app(paths, host, admin=None):
        seen["admin"] = admin
        return object()

    monkeypatch.setattr(cli, "make_map_app", fake_app)
    assert cli.main(["serve"]) == 0
    token = admintoken.read(home.data)
    assert token and seen["admin"].token == token


def test_serve_lan_bind_gets_no_admin(home, monkeypatch):
    _last_check(home, 2)
    _quiet_serve(monkeypatch)
    seen: dict = {}

    def fake_app(paths, host, admin=None):
        seen["admin"] = admin
        return object()

    monkeypatch.setattr(cli, "make_map_app", fake_app)
    assert cli.main(["serve", "--map-host", "0.0.0.0"]) == 0
    assert seen["admin"] is None


def test_serve_stop_event_ends_scheduler(home, monkeypatch):
    _last_check(home, 2)
    monkeypatch.setattr(cli, "_scheduled_run", lambda *a, **k: None)
    monkeypatch.setattr(cli.bot, "poll_forever", lambda *a, **k: None)
    box: dict = {}

    def fake_app(paths, host, admin=None):
        box["admin"] = admin
        return object()

    monkeypatch.setattr(cli, "make_map_app", fake_app)
    monkeypatch.setattr(cli, "STOP_POLL_S", 0.05)
    result: list = []
    t = threading.Thread(target=lambda: result.append(cli.main(["serve"])), daemon=True)
    t.start()
    deadline = datetime.now() + timedelta(seconds=5)
    while "admin" not in box:
        assert datetime.now() < deadline
    box["admin"].control.stop()
    t.join(10)
    assert not t.is_alive() and result == [0]


def test_control_pending_and_decide(home, monkeypatch):
    from outages.alerts import store as astore

    conn = db.connect(home.db)
    astore.request_join(conn, 555, "Ana", NOW)
    conn.commit()
    conn.close()
    sent = _recording_telegram(monkeypatch)
    ctl = cli.ServeControl(home, SETTINGS, cli._Runs(home, SETTINGS), threading.Event())
    assert [p["chat_id"] for p in ctl.pending()] == [555]
    assert ctl.decide(555, True) is True
    assert ctl.decide(555, True) is False
    assert len([r for r in sent if r.url.path.endswith("/sendMessage")]) == 1


def test_control_decide_without_telegram(home, caplog):
    from outages.alerts import store as astore

    conn = db.connect(home.db)
    astore.request_join(conn, 556, "Bo", NOW)
    conn.commit()
    conn.close()
    ctl = cli.ServeControl(home, None, cli._Runs(home, None), threading.Event())
    with caplog.at_level(logging.WARNING, logger="outages"):
        assert ctl.decide(556, False) is True
    assert "no message sent" in caplog.text


# --- tray feedback: run progress and settings (tray feedback spec) -----------------------------


def test_progress_steps_full_run(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    steps: list = []
    cli._scheduled_run(home, SETTINGS, publisher=_Publisher(order), progress=steps.append)
    assert steps == ["check", "alerts", "publish", "suggest"]


def test_progress_steps_alerts_only(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    steps: list = []
    cli._scheduled_run(
        home, SETTINGS, do_check=False, publisher=_Publisher(order), progress=steps.append
    )
    assert steps == ["alerts", "suggest"]


def test_runs_current_during_and_after(home, monkeypatch):
    runs = cli._Runs(home, None)
    seen: list = []

    def fake(*a, **k):
        k["progress"]("check")
        seen.append(runs.status())

    monkeypatch.setattr(cli, "_scheduled_run", fake)
    monkeypatch.setattr(cli, "utcnow", lambda: _at("20:00"))
    runs.scheduled()
    assert seen[0]["running"] is True
    assert (seen[0]["kind"], seen[0]["step"]) == ("scheduled", "check")
    assert seen[0]["started_at"] == _at("20:00").astimezone(UTC).isoformat(timespec="seconds")
    assert runs.status() == {"running": False, "kind": None, "started_at": None, "step": None}


def test_manual_run_current_kind_manual(home, monkeypatch):
    runs = cli._Runs(home, None)
    seen: list = []
    done = threading.Event()

    def fake(*a, **k):
        seen.append(runs.status()["kind"])
        done.set()

    monkeypatch.setattr(cli, "_scheduled_run", fake)
    assert runs.manual() is True
    assert done.wait(5)
    assert seen == ["manual"]


def test_control_settings_roundtrip(home):
    from outages import runstatus

    ctl = cli.ServeControl(home, None, cli._Runs(home, None), threading.Event())
    assert ctl.get_settings() == {"ollama_scheduled": False, "ollama_manual": False}
    assert ctl.set_settings({"ollama_manual": True}) == {
        "ollama_scheduled": False,
        "ollama_manual": True,
    }
    conn = db.connect(home.db)
    assert runstatus.read_settings(conn) == runstatus.Settings(False, True)
    conn.close()


# --- tray feedback: start and close Ollama around street suggestions ---------------------------


def _set(home, scheduled=False, manual=False):
    from outages import runstatus

    conn = db.connect(home.db)
    runstatus.write_settings(conn, runstatus.Settings(scheduled, manual))
    conn.close()


def _fake_ollama(monkeypatch, result="started"):
    calls: dict = {"ensure": 0, "stopped": 0}

    class Handle:
        pid = 77

        def stop(self):
            calls["stopped"] += 1

    def ensure(*a, **k):
        calls["ensure"] += 1
        return Handle() if result == "started" else result

    monkeypatch.setattr(cli.ollama, "ensure_running", ensure)
    return calls


def _fake_outcome(monkeypatch, outcome=None, error=None):
    from outages.streets.suggest import Outcome

    def fake(paths, now, **k):
        if error:
            raise error
        return outcome or Outcome("used", "3 new street suggestions", None)

    monkeypatch.setattr(cli.street_suggest, "suggest_outcome", fake)
    monkeypatch.setattr(cli.street_suggest, "inert_reason", lambda paths: None)


def test_suggest_records_outcome(home, monkeypatch):
    _fake_outcome(monkeypatch)
    cli._suggest_hook(home, None, kind="scheduled")
    s = _status(home)
    assert (s.suggest_status, s.suggest_detail) == ("used", "3 new street suggestions")


def test_manual_uses_manual_setting(home, monkeypatch):
    _set(home, scheduled=False, manual=True)
    calls = _fake_ollama(monkeypatch)
    _fake_outcome(monkeypatch)
    cli._suggest_hook(home, None, kind="scheduled")
    assert calls["ensure"] == 0
    cli._suggest_hook(home, None, kind="manual")
    assert calls == {"ensure": 1, "stopped": 1}


def test_catch_up_uses_scheduled_setting(home, monkeypatch):
    _set(home, scheduled=True, manual=False)
    calls = _fake_ollama(monkeypatch)
    _fake_outcome(monkeypatch)
    for kind in ("catch_up", "watchdog", "scheduled"):
        cli._suggest_hook(home, None, kind=kind)
    cli._suggest_hook(home, None, kind="manual")
    assert calls == {"ensure": 3, "stopped": 3}


def test_started_ollama_stopped_on_error(home, monkeypatch):
    _set(home, scheduled=True)
    calls = _fake_ollama(monkeypatch)
    _fake_outcome(monkeypatch, error=RuntimeError("boom"))
    with contextlib.suppress(RuntimeError):
        cli._suggest_hook(home, None, kind="scheduled")
    assert calls["stopped"] == 1


def test_already_running_ollama_not_stopped(home, monkeypatch):
    _set(home, scheduled=True)
    calls = _fake_ollama(monkeypatch, result=None)
    _fake_outcome(monkeypatch)
    cli._suggest_hook(home, None, kind="scheduled")
    assert calls == {"ensure": 1, "stopped": 0}
    assert _status(home).suggest_detail == "3 new street suggestions"


def test_missing_exe_is_unreachable(home, monkeypatch):
    _set(home, scheduled=True)
    _fake_ollama(monkeypatch, result="Ollama not found")
    _fake_outcome(monkeypatch, error=AssertionError("must not suggest"))
    cli._suggest_hook(home, None, kind="scheduled")
    s = _status(home)
    assert (s.suggest_status, s.suggest_detail) == ("unreachable", "Ollama not found")


def test_closed_note_in_detail(home, monkeypatch):
    _set(home, scheduled=True)
    _fake_ollama(monkeypatch)
    _fake_outcome(monkeypatch)
    cli._suggest_hook(home, None, kind="scheduled")
    assert _status(home).suggest_detail == (
        "3 new street suggestions (started for this run, then closed)"
    )


def test_no_suggest_records_skipped(home, monkeypatch):
    order = _ordered_run(home, monkeypatch)
    cli._scheduled_run(home, SETTINGS, suggest=False, publisher=_Publisher(order))
    s = _status(home)
    assert (s.suggest_status, s.suggest_detail) == (
        "skipped",
        "street suggestions off (--no-suggest)",
    )


def test_inert_step_never_starts_ollama(home, monkeypatch):
    """Final review M6: no Ollama start when suggestions have nothing to do (e.g. no extract)."""
    _set(home, scheduled=True)
    calls = _fake_ollama(monkeypatch)
    monkeypatch.setattr(cli.street_suggest, "inert_reason", lambda paths: "no OSM extract")
    cli._suggest_hook(home, None, kind="catch_up")
    assert calls["ensure"] == 0
    s = _status(home)
    assert (s.suggest_status, s.suggest_detail) == ("skipped", "no OSM extract")
