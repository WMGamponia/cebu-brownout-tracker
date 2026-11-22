"""Final-review fixes for tray feedback: each test reproduces one finding."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from test_tray_state import GOOD, NOW, snap

from outages import ollama
from outages.tray import feedback
from outages.tray.state import evaluate

CLICKED = NOW - timedelta(minutes=2)
MANUAL = replace(GOOD, last_run_kind="manual", last_run_at=NOW - timedelta(minutes=1))


# --- I1: no "Run finished" while serve can't say whether the run is still going ---------------


def test_no_finish_when_status_unknown():
    assert feedback.should_announce_finish(CLICKED, MANUAL, running=False, known=False) is False
    assert feedback.should_announce_finish(CLICKED, MANUAL, running=False, known=True) is True


# --- M2: "Run finished" never reports the previous run's publish or Ollama result -------------


def test_finished_omits_stale_publish_and_ollama():
    s = replace(
        MANUAL,
        last_check_ok=False,
        publish_at=CLICKED - timedelta(hours=10),
        suggest_status="used",
        suggest_at=CLICKED - timedelta(hours=10),
    )
    assert feedback.run_finished(s, since=CLICKED) == "Run finished: check failed"


def test_finished_keeps_this_runs_parts():
    s = replace(
        MANUAL,
        publish_at=NOW - timedelta(seconds=30),
        suggest_status="unreachable",
        suggest_at=NOW - timedelta(seconds=10),
    )
    assert feedback.run_finished(s, since=CLICKED) == (
        "Run finished: OK · published abc1234 · Ollama not running"
    )


# --- M3: never stop an Ollama this run didn't actually start -----------------------------------


class _Proc:
    pid = 1

    def __init__(self, code=None):
        self.code, self.killed = code, False

    def poll(self):
        return self.code


def test_spawned_exited_but_other_ollama_answers_returns_none():
    proc = _Proc(code=1)  # "address in use": someone else's Ollama took the port
    answers = iter([False, True])
    r = ollama.ensure_running(
        60,
        reachable=lambda: next(answers, True),
        find_exe=lambda: Path("C:/x/ollama.exe"),
        spawn=lambda exe: ollama.Started(proc),
        sleep=lambda s: None,
        clock=lambda: 0.0,
    )
    assert r is None


def test_stop_skips_an_exited_process(monkeypatch):
    killed: list = []
    monkeypatch.setattr(ollama, "kill_pid_tree", killed.append)
    proc = _Proc(code=0)
    proc.pid = 4242
    ollama.Started(proc).stop()
    assert killed == []
    live = _Proc(code=None)
    live.pid = 99
    ollama.Started(live).stop()
    assert killed == [99]


# --- M5: the menu says why Ollama wasn't used --------------------------------------------------


def test_unreachable_line_has_reason():
    s = replace(GOOD, suggest_status="unreachable", suggest_detail="Ollama not found")
    assert evaluate(snap(status=s)).lines[2] == (
        "Ollama: not running · street suggestions skipped · Ollama not found"
    )


def test_not_needed_line_keeps_started_note():
    s = replace(
        GOOD, suggest_status="not_needed", suggest_detail="(started for this run, then closed)"
    )
    assert evaluate(snap(status=s)).lines[2] == (
        "Ollama: not needed this run · (started for this run, then closed)"
    )
