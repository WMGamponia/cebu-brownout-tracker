"""Starting and closing Ollama for a run (tray feedback spec). No real process or network."""

from __future__ import annotations

from pathlib import Path

from outages import ollama


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _ensure(answers, exe=Path("C:/x/ollama.exe"), wait_s=60):
    clock = Clock()
    seq = iter(answers)
    spawned: list = []
    stopped: list = []

    class Handle:
        pid = 77

        def exited(self):
            return False

        def stop(self):
            stopped.append(self.pid)

    def spawn(path):
        spawned.append(path)
        return Handle()

    r = ollama.ensure_running(
        wait_s,
        reachable=lambda: next(seq, False),
        find_exe=lambda: exe,
        spawn=spawn,
        sleep=clock.sleep,
        clock=clock,
    )
    return r, spawned, stopped, clock


def test_already_running_returns_none():
    r, spawned, _, _ = _ensure([True])
    assert r is None and spawned == []


def test_starts_and_waits():
    r, spawned, stopped, _ = _ensure([False, False, False, True])
    assert r is not None and not isinstance(r, str) and r.pid == 77
    assert spawned == [Path("C:/x/ollama.exe")] and stopped == []


def test_timeout_stops_and_reports():
    r, spawned, stopped, clock = _ensure([False] * 1000)
    assert r == "Ollama didn't start within 60 s"
    assert stopped == [77] and clock.t >= 60


def test_missing_exe_is_unreachable_message():
    r, spawned, _, _ = _ensure([False], exe=None)
    assert r == "Ollama not found" and spawned == []


def test_find_exe_env_override(monkeypatch, tmp_path):
    exe = tmp_path / "my-ollama.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(
        ollama, "dotenv_get", lambda name: str(exe) if name == "OLLAMA_EXE" else None
    )
    assert ollama.find_exe() == exe


def test_find_exe_none(monkeypatch, tmp_path):
    monkeypatch.setattr(ollama, "dotenv_get", lambda name: None)
    monkeypatch.setattr(ollama.shutil, "which", lambda name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert ollama.find_exe() is None
