"""The last run's and publish's outcome, kept in `meta` for the tray (tray spec 5.2).

`last_run_*` and `last_check_ok` are written only by runs that do a check; `last_publish_*` only
when a publish was tried (or `off` when there is no publisher), so a restart or a failed check
never hides the last real outcome. `last_check_at` (the last *successful* check) is pipeline's.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from outages import db

RunKind = Literal["scheduled", "catch_up", "watchdog", "manual"]
PublishStatus = Literal["published", "skipped", "failed", "off"]


@dataclass(frozen=True)
class RunStatus:
    last_run_at: datetime | None
    last_run_kind: str | None
    last_check_ok: bool | None
    last_check_at: datetime | None
    publish_status: str | None
    publish_detail: str | None
    publish_at: datetime | None


def _ts(now: datetime) -> str:
    return now.astimezone(UTC).isoformat(timespec="seconds")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def record_run(conn: sqlite3.Connection, kind: RunKind, check_ok: bool, now: datetime) -> None:
    with conn:
        db.set_meta(conn, "last_run_at", _ts(now))
        db.set_meta(conn, "last_run_kind", kind)
        db.set_meta(conn, "last_check_ok", "1" if check_ok else "0")


def record_publish(
    conn: sqlite3.Connection, status: PublishStatus, detail: str, now: datetime
) -> None:
    with conn:
        db.set_meta(conn, "last_publish_status", status)
        db.set_meta(conn, "last_publish_detail", detail)
        db.set_meta(conn, "last_publish_at", _ts(now))


def read(conn: sqlite3.Connection) -> RunStatus:
    def get(key: str) -> str | None:
        return db.get_meta(conn, key)

    ok = get("last_check_ok")
    return RunStatus(
        last_run_at=_dt(get("last_run_at")),
        last_run_kind=get("last_run_kind"),
        last_check_ok=None if ok is None else ok == "1",
        last_check_at=_dt(get("last_check_at")),
        publish_status=get("last_publish_status"),
        publish_detail=get("last_publish_detail"),
        publish_at=_dt(get("last_publish_at")),
    )
