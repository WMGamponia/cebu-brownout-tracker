"""Start Ollama for a run and close it afterwards (tray feedback spec).

Only an Ollama this module started is ever stopped: one already running (the Ollama app at
logon, or one the owner opened) is left alone.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from outages.config import OLLAMA_URL, dotenv_get
from outages.proc import NO_WINDOW, kill_pid_tree

log = logging.getLogger("outages")

WAIT_S = 60


class Started:
    """An Ollama this run started."""

    def __init__(self, pid: int) -> None:
        self.pid = pid

    def stop(self) -> None:
        log.info("ollama: closing the one started for this run (pid %d)", self.pid)
        kill_pid_tree(self.pid)


def reachable(url: str = OLLAMA_URL, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(f"{url}/api/version", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def find_exe() -> Path | None:
    """`OLLAMA_EXE` (env or `.env`), else `ollama` on PATH, else the per-user install."""
    override = dotenv_get("OLLAMA_EXE")
    if override and Path(override).is_file():
        return Path(override)
    on_path = shutil.which("ollama")
    if on_path:
        return Path(on_path)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        default = Path(local) / "Programs" / "Ollama" / "ollama.exe"
        if default.is_file():
            return default
    return None


def _spawn(exe: Path) -> Started:
    p = subprocess.Popen(
        [str(exe), "serve"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )
    return Started(p.pid)


def ensure_running(
    wait_s: float = WAIT_S,
    *,
    reachable: Callable[[], bool] = reachable,
    find_exe: Callable[[], Path | None] = find_exe,
    spawn: Callable[[Path], Started] = _spawn,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Started | None | str:
    """None when Ollama already answers; `Started` when this call started it and it answers;
    otherwise a short reason (and anything started is stopped again)."""
    if reachable():
        return None
    exe = find_exe()
    if exe is None:
        return "Ollama not found"
    log.info("ollama: starting %s for this run", exe)
    try:
        started = spawn(exe)
    except OSError as e:
        return f"Ollama couldn't start: {e}"
    deadline = clock() + wait_s
    while clock() < deadline:
        if reachable():
            return started
        sleep(1)
    started.stop()
    return f"Ollama didn't start within {wait_s:g} s"
