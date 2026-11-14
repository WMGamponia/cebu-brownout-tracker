"""Runs at 06:00 and 20:00 Asia/Manila for `serve` (Phase 3 spec 5.9): each is a check, then an
alert run. Only module importing APScheduler.

A watchdog job runs every WATCHDOG_MIN minutes. On Windows a wait's timeout doesn't count time
the PC spent asleep, so a single wait from 20:00 to 06:00 ended hours late after a night of
sleeps and the 06:00 run fell outside the misfire grace (2026-11-14). Short waits mean a due run
is seen within minutes of waking; `missed_slot` covers a run slept past the grace."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from outages.config import (
    CATCH_UP_AFTER_H,
    MISFIRE_GRACE_S,
    RUN_TIMES,
    WATCHDOG_MIN,
    WATCHDOG_SLACK_MIN,
)
from outages.timeparse import MANILA


def build(run: Callable[[], None], watchdog: Callable[[], None] | None = None) -> BlockingScheduler:
    """A scheduler with one cron job per RUN_TIMES entry, in Manila time, and the watchdog."""
    sched = BlockingScheduler(timezone=MANILA)
    for hm in RUN_TIMES:
        h, m = hm.split(":")
        sched.add_job(
            run,
            CronTrigger(hour=int(h), minute=int(m), timezone=MANILA),
            id=f"run-{hm}",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=MISFIRE_GRACE_S,
        )
    sched.add_job(
        watchdog or (lambda: None),
        IntervalTrigger(minutes=WATCHDOG_MIN, timezone=MANILA),
        id="watchdog",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    return sched


def needs_catch_up(last_check_at: str | None, now: datetime) -> bool:
    """True when there was no check yet or the last one is older than CATCH_UP_AFTER_H."""
    if last_check_at is None:
        return True
    return now - datetime.fromisoformat(last_check_at) > timedelta(hours=CATCH_UP_AFTER_H)


def latest_slot(now: datetime) -> datetime:
    """The most recent RUN_TIMES time at or before `now`, in Manila time."""
    local = now.astimezone(MANILA)
    slots = []
    for days in (0, 1):
        day = local - timedelta(days=days)
        for hm in RUN_TIMES:
            h, m = hm.split(":")
            slots.append(day.replace(hour=int(h), minute=int(m), second=0, microsecond=0))
    return max(s for s in slots if s <= local)


def missed_slot(last_check_at: str | None, now: datetime) -> datetime | None:
    """The latest slot when no check has happened since it, once WATCHDOG_SLACK_MIN has passed
    (before that its cron job should run it); else None."""
    slot = latest_slot(now)
    if now - slot < timedelta(minutes=WATCHDOG_SLACK_MIN):
        return None
    if last_check_at is not None and datetime.fromisoformat(last_check_at) >= slot:
        return None
    return slot
