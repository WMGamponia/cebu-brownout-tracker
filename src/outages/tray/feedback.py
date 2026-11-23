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


def _this_run(at: datetime | None, since: datetime | None) -> bool:
    """True when an outcome was recorded by the run since `since` (always, without `since`)."""
    return since is None or (at is not None and at >= since)


def run_finished(s: RunStatus, since: datetime | None = None) -> str:
    """The run's result; parts the run didn't record itself (a failed check publishes nothing)
    are left out rather than showing the previous run's."""
    parts = ["OK" if s.last_check_ok else "check failed"]
    if s.publish_status is not None and _this_run(s.publish_at, since):
        parts.append(publish_short(s))
    if s.suggest_status in OLLAMA_WORDS and _this_run(s.suggest_at, since):
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
    awaited_since: datetime | None, status: RunStatus, running: bool, known: bool = True
) -> bool:
    """True once the Run now the owner clicked has finished (and only for that run). `known`:
    serve said whether a run is in progress; without that (an old serve, a timeout) the run
    may still be going, so nothing is announced."""
    return (
        known
        and awaited_since is not None
        and not running
        and status.last_run_kind == "manual"
        and status.last_run_at is not None
        and status.last_run_at >= awaited_since
    )
