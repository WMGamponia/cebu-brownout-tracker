"""Start at logon (tray spec 5.8): enable or disable the logon task `install-task.ps1` registers.

No second startup method: if Windows refuses (the task was registered from an elevated shell),
the tray says how to change it instead.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import Literal

TASK_NAME = "OutageTracker serve"

Runner = Callable[[str], tuple[int, str, str]]


def powershell(command: str) -> tuple[int, str, str]:
    """Run one PowerShell command with no window; (exit code, stdout, stderr)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return r.returncode, r.stdout, r.stderr


def _quoted() -> str:
    return "'" + TASK_NAME.replace("'", "''") + "'"


def is_enabled(run: Runner = powershell) -> bool | None:
    """True/False for the logon task's state; None when there is no such task (or no answer)."""
    code, out, _ = run(
        f"(Get-ScheduledTask -TaskName {_quoted()} -ErrorAction Stop).State.ToString()"
    )
    if code != 0:
        return None
    return out.strip().lower() != "disabled"


def set_enabled(on: bool, run: Runner = powershell) -> Literal["ok", "denied", "missing", "error"]:
    verb = "Enable" if on else "Disable"
    code, _, err = run(f"{verb}-ScheduledTask -TaskName {_quoted()} -ErrorAction Stop | Out-Null")
    if code == 0:
        return "ok"
    if "Access is denied" in err or "0x80070005" in err:
        return "denied"
    if "No MSFT_ScheduledTask objects found" in err:
        return "missing"
    return "error"
