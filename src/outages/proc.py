"""Small process helpers shared by the tray and `serve` (Windows-first, no console windows)."""

from __future__ import annotations

import contextlib
import os
import subprocess

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


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
