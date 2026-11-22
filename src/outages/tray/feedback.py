"""Notification texts for the tray's actions (tray feedback spec): every click answers. Pure."""

from __future__ import annotations

from datetime import datetime

from outages.runstatus import RunStatus
from outages.tray.state import publish_short

OLLAMA_WORDS = {
    "used": "Ollama used",
    "not_needed": "Ollama not needed",
    "unreachable": "Ollama not running",
    "skipped": "Ollama not used",
    "failed": "Ollama failed",
}


def mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def run_started() -> str:
    return "Run started"


def run_busy(step: str | None, elapsed_s: int) -> str:
    return f"A run is already in progress ({step or 'starting'}, {mmss(elapsed_s)})"


def run_finished(s: RunStatus) -> str:
    parts = ["OK" if s.last_check_ok else "check failed", publish_short(s)]
    if s.suggest_status in OLLAMA_WORDS:
        parts.append(OLLAMA_WORDS[s.suggest_status])
    return "Run finished: " + " · ".join(parts)


def decided(name: str, approve: bool, result: str) -> str:
    if result == "ok":
        return f"{name} {'approved' if approve else 'denied'}"
    if result == "not_pending":
        return f"{name}: already decided"
    return f"Couldn't reach serve to decide {name}'s request"


def restarting() -> str:
    return "Restarting serve…"


def restarted() -> str:
    return "serve restarted"


def cant_restart(pid: int | None) -> str:
    return (
        "Can't restart: this serve wasn't started by the tray and doesn't answer "
        f"(PID {pid if pid is not None else 'unknown'}). "
        "End it in Task Manager; the tray then starts a new one."
    )


def autostart(on: bool) -> str:
    return f"Start at logon: {'on' if on else 'off'}"


def ollama_setting(which: str, on: bool) -> str:
    return f"Start Ollama for {which} runs: {'on' if on else 'off'}"


def should_announce_finish(
    awaited_since: datetime | None, status: RunStatus, running: bool
) -> bool:
    """True once the Run now the owner clicked has finished (and only for that run)."""
    return (
        awaited_since is not None
        and not running
        and status.last_run_kind == "manual"
        and status.last_run_at is not None
        and status.last_run_at >= awaited_since
    )
