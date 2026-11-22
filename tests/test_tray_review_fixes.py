"""Final-review fixes for the tray branch: each test reproduces one finding."""

from __future__ import annotations

import logging
import sys
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_tray_state import GOOD, NOW, at, snap

from outages import __main__ as cli
from outages import scheduler
from outages.config import Paths
from outages.tray.client import ServeClient
from outages.tray.seen import Seen
from outages.tray.state import Event, evaluate
from outages.tray.supervisor import Supervisor

# --- I1: Run now during the start-up catch-up must be refused ----------------------------------


def test_manual_refused_during_catch_up(tmp_path, monkeypatch):
    runs = cli._Runs(Paths(tmp_path), None)
    seen: list = []

    def fake(*a, **k):
        seen.append((k.get("kind"), runs.manual()))

    monkeypatch.setattr(cli, "_scheduled_run", fake)
    runs.catch_up(do_check=True)
    assert seen == [("catch_up", False)]


def test_serve_catch_up_goes_through_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ADMIN_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(cli, "make_map_app", lambda paths, *a: object())
    monkeypatch.setattr(cli.map_server, "start_thread", lambda *a, **k: None)
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: None)
    calls: list = []
    monkeypatch.setattr(cli._Runs, "catch_up", lambda self, do_check: calls.append(do_check))
    monkeypatch.setattr(cli, "_scheduled_run", lambda *a, **k: pytest.fail("bypassed the lock"))
    assert cli.main(["serve"]) == 0
    assert calls == [True]


# --- I2: one failed probe after a minute must not turn red ------------------------------------


def test_one_failed_probe_not_red():
    s = snap(healthz_ok=False, last_healthy_at=NOW - timedelta(seconds=5))
    v = evaluate(s)
    assert v.colour != "red" and v.events == []


def test_unanswered_for_60s_red_keyed_by_last_answer():
    last = NOW - timedelta(seconds=61)
    v = evaluate(snap(healthz_ok=False, last_healthy_at=last))
    assert v.colour == "red"
    assert [e.key for e in v.events] == [f"not_answering:{last.isoformat()}"]


# --- I3: long details are clipped, and an undelivered notification is retried ------------------


def test_long_publish_detail_clipped_to_first_line():
    detail = "error: failed to push some refs\n" + "hint: " + "x" * 400
    v = evaluate(snap(status=replace(GOOD, publish_status="failed", publish_detail=detail)))
    assert v.events[0].text == "Publish failed: error: failed to push some refs"
    assert v.lines[1] == "Publish: failed: error: failed to push some refs"
    one_long = "y" * 600
    v = evaluate(snap(status=replace(GOOD, publish_status="failed", publish_detail=one_long)))
    assert len(v.events[0].text) <= 250


def test_seen_marks_only_delivered(tmp_path):
    seen = Seen(tmp_path / "tray_state.json")
    seen.load()
    e = Event("k", "t")
    assert seen.fresh([e], deliver=lambda ev: False) == []
    assert seen.fresh([e], deliver=lambda ev: True) == [e]
    assert seen.fresh([e], deliver=lambda ev: True) == []


# --- I4: the tray must not hold outages.log open -----------------------------------------------


def test_tray_does_not_log_to_outages_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pystray", types.ModuleType("pystray"))
    monkeypatch.setitem(sys.modules, "PIL", types.ModuleType("PIL"))
    files: list = []

    def run(root, port):
        files.extend(
            Path(h.baseFilename).name
            for h in logging.getLogger("outages").handlers
            if hasattr(h, "baseFilename")
        )
        return 0

    fake_app = types.ModuleType("outages.tray.app")
    fake_app.run = run
    monkeypatch.setitem(sys.modules, "outages.tray.app", fake_app)
    assert cli.main(["tray"]) == 0
    assert "outages.log" not in files


# --- I6: the install script refuses to register the tray without its extra ---------------------


def test_install_script_checks_tray_extra_first():
    script = (Path(__file__).parents[1] / "scripts" / "install-task.ps1").read_text("utf-8")
    check = script.find("import pystray")
    register = script.find("Register-OutageTask $ServeTask")
    assert 0 <= check < register
    assert 'pip install -e ".[tray]"' in script[check:register]


# --- M7: an old publish skip is not notified again by a later run -------------------------------


def test_publish_event_keyed_by_publish_time():
    morning = replace(
        GOOD,
        last_run_at=at("06:00"),
        publish_status="skipped",
        publish_detail="not on main",
        publish_at=at("06:00"),
    )
    evening = replace(morning, last_run_at=at("20:00"), last_check_ok=False)
    k1 = {e.key for e in evaluate(snap(now=at("07:00"), status=morning)).events}
    k2 = {e.key for e in evaluate(snap(status=evening)).events}
    assert f"publish_skipped:{at('06:00').isoformat()}" in k1 & k2


# --- M8: a failed spawn counts as a crash instead of stranding the tray ------------------------


def _sup(spawn, now):
    return Supervisor(spawn=spawn, lock_held=lambda: False, clock=lambda: now[0])


def test_spawn_failure_is_a_crash():
    now = [datetime(2026, 11, 23, tzinfo=UTC)]

    def spawn():
        raise OSError("no pythonw")

    s = _sup(spawn, now)
    s.start()
    assert s.state == "restarting" and s.last_crash == now[0]
    for delay in (10, 60, 300):
        now[0] += timedelta(seconds=delay)
        s.tick()
    assert s.state == "capped"


# --- M9: something else on the port is not a healthy serve -------------------------------------


def _client(tmp_path, handler):
    return ServeClient(data_dir=tmp_path, transport=httpx.MockTransport(handler))


def test_healthz_foreign_listener_false(tmp_path):
    assert (
        _client(tmp_path, lambda r: httpx.Response(200, text="<html>hi</html>")).healthz() is False
    )
    assert _client(tmp_path, lambda r: httpx.Response(404, json={})).healthz() is False


def test_healthz_real_serve_true(tmp_path):
    ok = {"ok": True, "schema": 4, "last_check_at": None}
    assert _client(tmp_path, lambda r: httpx.Response(200, json=ok)).healthz() is True
    nr = {"error": "database not ready"}
    assert _client(tmp_path, lambda r: httpx.Response(503, json=nr)).healthz() is True


# --- M12: Quit never kills a lock holder that isn't an answering serve -------------------------


def test_stop_adopted_unreachable_leaves_it():
    killed: list = []
    s = Supervisor(
        spawn=lambda: pytest.fail("spawned"),
        lock_held=lambda: True,
        clock=lambda: datetime(2026, 11, 23, tzinfo=UTC),
        sleep=lambda s: None,
        kill_pid=killed.append,
        lock_pid=lambda: 4242,
    )
    s.start()
    s.stop(lambda: False, 600)  # e.g. a hand-run backfill holds the lock
    assert killed == [] and s.state == "stopped"


# --- M14: the menu is rebuilt only when what it shows changed -----------------------------------


def test_menu_key_stable_and_changes():
    from outages.tray.state import menu_key

    v = evaluate(snap())
    a = menu_key(v, [], True, "running", None)
    assert a == menu_key(evaluate(snap()), [], True, "running", None)
    assert a != menu_key(v, [{"chat_id": 7, "name": "Juan"}], True, "running", None)
    assert a != menu_key(v, [], False, "running", None)
    assert a != menu_key(v, [], True, "running", "Stopping after the current run…")
