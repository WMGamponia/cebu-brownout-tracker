"""Notification texts for the tray's actions (tray feedback spec). Pure."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from test_tray_state import GOOD, NOW

from outages.tray import feedback


def test_run_started():
    assert feedback.run_started() == "Run started"


def test_run_busy():
    assert feedback.run_busy("check", 42) == "A run is already in progress (check, 0:42)"
    assert feedback.run_busy(None, 125) == "A run is already in progress (starting, 2:05)"


def test_run_finished_ok():
    s = replace(GOOD, suggest_status="used", suggest_detail="3 new")
    assert feedback.run_finished(s) == "Run finished: OK · published abc1234 · Ollama used"


def test_run_finished_problems():
    s = replace(
        GOOD,
        last_check_ok=False,
        publish_status="skipped",
        publish_detail="not on main",
        suggest_status="unreachable",
    )
    assert feedback.run_finished(s) == (
        "Run finished: check failed · publish skipped: not on main · Ollama not running"
    )


def test_run_finished_ollama_words():
    for status, word in [
        ("not_needed", "Ollama not needed"),
        ("skipped", "Ollama not used"),
        ("failed", "Ollama failed"),
    ]:
        assert feedback.run_finished(replace(GOOD, suggest_status=status)).endswith(word)
    assert feedback.run_finished(GOOD) == "Run finished: OK · published abc1234"


def test_decided():
    assert feedback.decided("Juan", True, "ok") == "Juan approved"
    assert feedback.decided("Juan", False, "ok") == "Juan denied"
    assert feedback.decided("Juan", True, "not_pending") == "Juan: already decided"
    assert (
        feedback.decided("Juan", True, "error") == "Couldn't reach serve to decide Juan's request"
    )


def test_restart_texts():
    assert feedback.restarting() == "Restarting serve…"
    assert feedback.restarted() == "serve restarted"
    assert feedback.cant_restart(2356) == (
        "Can't restart: this serve wasn't started by the tray and doesn't answer (PID 2356). "
        "End it in Task Manager; the tray then starts a new one."
    )
    assert "(PID unknown)" in feedback.cant_restart(None)


def test_toggle_texts():
    assert feedback.autostart(True) == "Start at logon: on"
    assert feedback.autostart(False) == "Start at logon: off"
    assert feedback.ollama_setting("scheduled", True) == "Start Ollama for scheduled runs: on"
    assert feedback.ollama_setting("Run now", False) == "Start Ollama for Run now runs: off"


def test_finished_only_for_awaited_manual():
    clicked = NOW - timedelta(minutes=2)
    manual = replace(GOOD, last_run_kind="manual", last_run_at=NOW - timedelta(minutes=1))
    assert feedback.should_announce_finish(None, manual, running=False) is False
    assert feedback.should_announce_finish(clicked, manual, running=True) is False
    scheduled = replace(manual, last_run_kind="scheduled")
    assert feedback.should_announce_finish(clicked, scheduled, running=False) is False
    old = replace(manual, last_run_at=clicked - timedelta(minutes=5))
    assert feedback.should_announce_finish(clicked, old, running=False) is False
    assert feedback.should_announce_finish(clicked, manual, running=False) is True
