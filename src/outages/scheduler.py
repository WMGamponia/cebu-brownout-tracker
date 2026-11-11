"""Runs at 06:00 and 20:00 Asia/Manila for `serve` (Phase 3 spec 5.9): each is a check, then an
alert run. Only module importing APScheduler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from outages.config import CATCH_UP_AFTER_H, MISFIRE_GRACE_S, RUN_TIMES
from outages.timeparse import MANILA


def build(run: Callable[[], None]) -> BlockingScheduler:
    """A scheduler with one cron job per RUN_TIMES entry, in Manila time."""
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
    return sched


def needs_catch_up(last_check_at: str | None, now: datetime) -> bool:
    """True when there was no check yet or the last one is older than CATCH_UP_AFTER_H."""
    if last_check_at is None:
        return True
    return now - datetime.fromisoformat(last_check_at) > timedelta(hours=CATCH_UP_AFTER_H)
