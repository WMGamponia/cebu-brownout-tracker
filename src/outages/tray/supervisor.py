"""Start, restart, adopt and stop `serve` (tray spec 5.1, 5.7).

The tray runs `serve` as a hidden child. An unexpected exit restarts it after 10 s, 60 s, then
5 min; a 4th crash within 30 min stops restarting ("capped"). Exit code 3 means another
`serve` holds the lock: the tray adopts that one instead, and starts its own once the lock is
free. Not thread-safe: the tray calls it from one worker thread.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from outages import lock
from outages.config import MAP_PORT
from outages.tray.state import ProcState

log = logging.getLogger("outages")

LOCK_HELD_EXIT = 3  # `python -m outages` returns 3 when another instance holds the lock
LOCK_NAME = "serve.lock"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


class Proc(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def kill_tree(self) -> None: ...


def kill_pid_tree(pid: int) -> None:
    """Kill `pid` and its children, hidden; errors ignored (it may be gone already)."""
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.kill(pid, 9)
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=15,
            creationflags=NO_WINDOW,
        )


class _Popen:
    def __init__(self, p: subprocess.Popen) -> None:
        self._p = p
        self.pid = p.pid

    def poll(self) -> int | None:
        return self._p.poll()

    def kill_tree(self) -> None:
        kill_pid_tree(self.pid)
        try:
            self._p.kill()
            self._p.wait(timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def _pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if exe.name.lower() == "python.exe" and w.is_file() else exe)


def serve_command(port: int = MAP_PORT) -> list[str]:
    cmd = [_pythonw(), "-m", "outages", "serve"]
    return cmd if port == MAP_PORT else [*cmd, "--map-port", str(port)]


def spawn_serve(root: Path, port: int = MAP_PORT) -> Proc:
    """`pythonw -m outages serve` in `root`, with no console window."""
    p = subprocess.Popen(
        serve_command(port),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW | NEW_GROUP,
    )
    return _Popen(p)


def lock_held(data_dir: Path) -> bool:
    """True when a `serve` (or another locked command) holds `data/serve.lock`."""
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        held = lock.acquire(data_dir / LOCK_NAME)
    except lock.LockHeld:
        return True
    held.release()
    return False


def lock_pid(data_dir: Path) -> int | None:
    try:
        text = (data_dir / LOCK_NAME).read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


class Supervisor:
    def __init__(
        self,
        spawn: Callable[[], Proc],
        lock_held: Callable[[], bool],
        clock: Callable[[], datetime],
        sleep: Callable[[float], None] = time.sleep,
        kill_pid: Callable[[int], None] = kill_pid_tree,
        lock_pid: Callable[[], int | None] = lambda: None,
        backoff: tuple[int, ...] = (10, 60, 300),
        cap_window: timedelta = timedelta(minutes=30),
        cap_count: int = 4,
    ) -> None:
        self._spawn, self._lock_held, self._clock, self._sleep = spawn, lock_held, clock, sleep
        self._kill_pid, self._lock_pid = kill_pid, lock_pid
        self._backoff, self._cap_window, self._cap_count = backoff, cap_window, cap_count
        self._proc: Proc | None = None
        self._crashes: list[datetime] = []
        self._restart_at: datetime | None = None
        self.state: ProcState = "stopped"
        self.since: datetime = clock()
        self.last_crash: datetime | None = None

    def _set(self, state: ProcState) -> None:
        self.state, self.since = state, self._clock()

    def _launch(self) -> None:
        try:
            self._proc = self._spawn()
        except OSError as e:  # counts as a crash: backs off, then caps, never strands the tray
            log.error("tray: could not start serve: %s", e)
            self._proc = None
            self._crashed(self._clock())
            return
        self._set("starting")

    def _crashed(self, now: datetime) -> None:
        self._crashes = [c for c in self._crashes if now - c < self._cap_window] + [now]
        self.last_crash = now
        if len(self._crashes) >= self._cap_count:
            self._set("capped")
            return
        delay = self._backoff[min(len(self._crashes), len(self._backoff)) - 1]
        self._restart_at = now + timedelta(seconds=delay)
        self._set("restarting")

    def start(self) -> None:
        if self._lock_held():
            self._proc = None
            self._set("adopted")
        else:
            self._launch()

    def mark_healthy(self) -> None:
        if self.state == "starting":
            self._set("running")

    def tick(self) -> None:
        now = self._clock()
        if self.state in ("starting", "running") and self._proc is not None:
            code = self._proc.poll()
            if code is None:
                return
            self._proc = None
            if code == LOCK_HELD_EXIT:
                self._set("adopted")
                return
            self._crashed(now)
        elif self.state == "restarting":
            if self._restart_at is not None and now >= self._restart_at:
                self._launch()
        elif self.state == "adopted" and not self._lock_held():
            self._launch()

    def stop(self, request_stop: Callable[[], bool], timeout_s: float) -> None:
        """Ask serve to stop (it finishes a run in progress), wait up to `timeout_s`, then kill
        it as a last resort. Our own child is killed at once if the stop request can't reach it.
        An adopted lock holder is killed only if it answered the stop request (it is a serve):
        one that didn't may be a hand-run `check` or `backfill`, and is left alone."""
        deadline = self._clock() + timedelta(seconds=timeout_s)
        if self.state == "adopted":
            if request_stop():
                while self._lock_held() and self._clock() < deadline:
                    self._sleep(1)
                if self._lock_held():
                    pid = self._lock_pid()
                    if pid is not None:
                        self._kill_pid(pid)
        elif self._proc is not None:
            proc = self._proc
            if request_stop():
                while proc.poll() is None and self._clock() < deadline:
                    self._sleep(1)
            if proc.poll() is None:
                proc.kill_tree()
        self._proc = None
        self._restart_at = None
        self._set("stopped")

    def restart_now(self, request_stop: Callable[[], bool], timeout_s: float) -> None:
        self._crashes = []
        self.last_crash = None
        self.stop(request_stop, timeout_s)
        self.start()
