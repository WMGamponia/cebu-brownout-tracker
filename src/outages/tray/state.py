"""The tray's rules (tray spec 5.3, 5.4): a status snapshot -> icon colour, menu lines, tooltip
and notification events. Pure: no I/O, no clock of its own.

Worst wins: red (broken) > grey (not running) > amber (attention) > green. An event's key is
one (reason, run) pair, so a new failure notifies even while the icon is already red, and the
same unchanged problem never does twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from outages import scheduler
from outages.config import RUN_TIMES, STALE_CHECK_H
from outages.runstatus import RunStatus
from outages.timeparse import MANILA

ProcState = Literal["starting", "running", "restarting", "stopped", "capped", "adopted"]
Colour = Literal["green", "amber", "red", "grey", "blue"]

HEALTHZ_GRACE = timedelta(seconds=60)
TOOLTIP_MAX = 127  # Windows' limit for a tray tooltip
DETAIL_MAX = 200  # keeps "Publish failed: <detail>" under the 255-char notification limit


@dataclass(frozen=True)
class Snapshot:
    now: datetime
    proc: ProcState
    proc_since: datetime
    healthz_ok: bool
    status: RunStatus
    pending: list[dict]
    crashed_at: datetime | None
    last_healthy_at: datetime | None = None  # the last time /healthz answered
    run: dict | None = None  # /admin/status while a run is in progress


@dataclass(frozen=True)
class Event:
    key: str
    text: str


@dataclass(frozen=True)
class View:
    colour: Colour
    headline: str
    lines: list[str]
    tooltip: str
    events: list[Event]
    can_act: bool
    can_run: bool = False  # Run now: serve answers and no run is in progress


def _iso(t: datetime) -> str:
    return t.isoformat()


def _day_hm(t: datetime) -> str:
    return t.astimezone(MANILA).strftime("%a %d %b %H:%M")


def _hm(t: datetime) -> str:
    return t.astimezone(MANILA).strftime("%H:%M")


def next_run(now: datetime) -> datetime:
    """The first RUN_TIMES time after `now`, in Manila time."""
    local = now.astimezone(MANILA)
    slots = []
    for days in (0, 1):
        day = local + timedelta(days=days)
        for hm in RUN_TIMES:
            h, m = hm.split(":")
            slots.append(day.replace(hour=int(h), minute=int(m), second=0, microsecond=0))
    return min(s for s in slots if s > local)


def _detail(text: str | None) -> str:
    """The first non-empty line of a publish detail (git's stderr can be long), clipped."""
    for line in (text or "").splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= DETAIL_MAX else line[: DETAIL_MAX - 1] + "…"
    return ""


def _publish_line(s: RunStatus) -> str:
    if s.publish_status is None:
        return "Publish: none yet"
    if s.publish_status == "off":
        return "Publish: off"
    if s.publish_status == "published":
        return f"Publish: published {_detail(s.publish_detail)}".rstrip()
    return f"Publish: {s.publish_status}: {_detail(s.publish_detail)}".rstrip(": ")


def publish_short(s: RunStatus) -> str:
    if s.publish_status is None:
        return "no publish yet"
    if s.publish_status == "off":
        return "publishing off"
    if s.publish_status == "published":
        return f"published {_detail(s.publish_detail).split(' ')[0]}".rstrip()
    return f"publish {s.publish_status}: {_detail(s.publish_detail)}".rstrip(": ")


OLLAMA_LINES = {
    "used": "Ollama: used · {detail}",
    "not_needed": "Ollama: not needed this run",
    "unreachable": "Ollama: not running · street suggestions skipped",
    "skipped": "Ollama: not used · {detail}",
    "failed": "Ollama: street suggestions failed · {detail}",
}


def _ollama_line(s: RunStatus) -> str:
    template = OLLAMA_LINES.get(s.suggest_status or "")
    if template is None:
        return "Ollama: no run yet"
    return template.format(detail=_detail(s.suggest_detail)).rstrip(" ·")


def _running_headline(run: dict, now: datetime) -> str:
    try:
        started = datetime.fromisoformat(run["started_at"])
        secs = int((now - started).total_seconds())
    except (KeyError, TypeError, ValueError):
        secs = 0
    secs = max(0, secs)
    return f"Running: {run.get('step') or 'starting'} ({secs // 60}:{secs % 60:02d})"


def _red(s: Snapshot, run_key: str, alive: bool) -> list[Event]:
    st = s.status
    red: list[Event] = []
    if s.proc == "capped":
        red.append(
            Event(f"crash_cap:{_iso(s.proc_since)}", "serve keeps crashing; restarts stopped")
        )
    if st.last_check_ok is False:
        red.append(Event(f"check_failed:{run_key}", "Check failed"))
    if st.publish_status in ("failed", "skipped"):
        label = "Publish skipped" if st.publish_status == "skipped" else "Publish failed"
        when = _iso(st.publish_at) if st.publish_at else run_key
        red.append(
            Event(f"publish_{st.publish_status}:{when}", f"{label}: {_detail(st.publish_detail)}")
        )
    answered = max(s.proc_since, s.last_healthy_at or s.proc_since)
    if alive and not s.healthz_ok and s.now - answered >= HEALTHZ_GRACE:
        red.append(
            Event(
                f"not_answering:{_iso(answered)}",
                "serve is running but not answering (map port in use?)",
            )
        )
    return red


def _amber(s: Snapshot, starting: bool) -> list[Event]:
    st = s.status
    amber: list[Event] = []
    if s.proc == "stopped":
        return amber
    if st.last_run_at is not None:
        slot = scheduler.missed_slot(_iso(st.last_run_at), s.now)
        if slot is not None:
            amber.append(Event(f"missed:{_iso(slot)}", f"Missed the {_hm(slot)} run"))
    stale_at = s.now - timedelta(hours=STALE_CHECK_H)
    if (st.last_check_at is not None and st.last_check_at < stale_at) or (
        st.last_check_at is None and not starting
    ):
        amber.append(Event("stale", "Data is old"))
    if s.proc == "restarting":
        amber.append(Event("restarting", "Restarting serve"))
    return amber


def evaluate(s: Snapshot) -> View:
    st = s.status
    alive = s.proc in ("starting", "running", "adopted")
    starting = alive and s.proc != "adopted" and s.now - s.proc_since < HEALTHZ_GRACE
    run_key = _iso(st.last_run_at) if st.last_run_at else "never"

    red = _red(s, run_key, alive)
    amber = _amber(s, starting)
    events = list(red)
    if alive:  # a stopped or restarting serve doesn't nag about runs it couldn't do
        events += [e for e in amber if e.key.startswith("missed:")]
    if s.crashed_at is not None:
        events.append(Event(f"crash:{_iso(s.crashed_at)}", "serve crashed and was restarted"))
    for p in s.pending:
        who = p.get("name") or f"chat {p['chat_id']}"
        events.append(
            Event(f"join:{p['chat_id']}", f"Join request from {who}: right-click to approve")
        )

    running = bool(s.run and s.run.get("running"))
    if red:
        colour: Colour = "red"
        headline = red[0].text
    elif running and s.healthz_ok:
        colour, headline = "blue", _running_headline(s.run, s.now)
    elif s.proc == "stopped" or starting:
        colour, headline = "grey", ("Starting serve" if starting else "serve is stopped")
    elif amber:
        colour, headline = "amber", amber[0].text
    else:
        colour, headline = "green", "Running fine"

    if st.last_run_at is None:
        run_line, run_short = "Last run: never", "Last run never"
    else:
        ok = "OK" if st.last_check_ok else "check failed"
        run_line = f"Last run: {_day_hm(st.last_run_at)} · {ok}"
        run_short = f"Last run {_hm(st.last_run_at)} {ok}"
    nxt = next_run(s.now)
    lines = [run_line, _publish_line(st), _ollama_line(st), f"Next run: {_day_hm(nxt)}"]
    if s.proc == "adopted":
        lines.append("serve: started elsewhere")
    tooltip = f"{run_short} · {publish_short(st)} · next {_hm(nxt)}"
    if len(tooltip) > TOOLTIP_MAX:
        tooltip = tooltip[: TOOLTIP_MAX - 1] + "…"
    return View(
        colour,
        headline,
        lines,
        tooltip,
        events,
        can_act=s.healthz_ok,
        can_run=s.healthz_ok and not running,
    )


def menu_key(
    view: View,
    pending: list[dict],
    autostart: bool | None,
    proc: str,
    busy: str | None,
    settings: dict | None = None,
) -> tuple:
    """Everything the menu shows: rebuild it only when this changes, so a menu the owner has
    open isn't destroyed under them by every 30 s refresh."""
    people = tuple((p.get("chat_id"), p.get("name")) for p in pending)
    opts = tuple(sorted((settings or {}).items()))
    return (
        view.headline,
        tuple(view.lines),
        view.can_act,
        view.can_run,
        people,
        autostart,
        proc,
        busy,
        opts,
    )
