"""Keeping a timer-woken PC awake through a scheduled run (Phase 6 review I1)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from outages import power

ROOT = Path(__file__).resolve().parents[1]


def _record(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(power, "_set_state", calls.append)
    return calls


def test_keep_awake_sets_then_clears(monkeypatch):
    calls = _record(monkeypatch)
    with power.keep_awake():
        assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]
    assert calls[-1] == power.ES_CONTINUOUS


def test_keep_awake_clears_after_an_error(monkeypatch):
    calls = _record(monkeypatch)
    with contextlib.suppress(RuntimeError), power.keep_awake():
        raise RuntimeError("run failed")
    assert calls[-1] == power.ES_CONTINUOUS


def test_keep_awake_is_a_no_op_without_windows(monkeypatch):
    monkeypatch.setattr(power, "_set_state", None)
    with power.keep_awake():
        pass


def test_hold_keeps_awake_for_the_given_time(monkeypatch):
    calls = _record(monkeypatch)
    slept = []
    monkeypatch.setattr(power.time, "sleep", slept.append)
    power.hold(900)
    assert slept == [900]
    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED, power.ES_CONTINUOUS]


def test_scheduled_run_keeps_the_pc_awake(monkeypatch, tmp_path):
    from outages import __main__ as cli

    events = []

    @contextlib.contextmanager
    def awake():
        events.append("on")
        yield
        events.append("off")

    monkeypatch.setattr(cli.power, "keep_awake", awake)
    monkeypatch.setattr(cli, "_check", lambda paths: events.append("check") or True)
    cli._scheduled_run(cli.Paths(tmp_path), None)
    assert events == ["on", "check", "off"]


def test_wake_task_holds_the_pc_awake():
    script = (ROOT / "scripts" / "install-task.ps1").read_text(encoding="utf-8")
    assert "outages.power" in script and "hold(" in script
