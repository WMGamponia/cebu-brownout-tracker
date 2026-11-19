"""The snapshot command as a function (live-site spec 5.3): the CLI prints its result, `serve`'s
publisher acts on it. Offline and read-only (Phase 6 spec 4.1): builds no fetcher, Overpass or
Telegram client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from outages import db, snapshot
from outages.areas.gazetteer import load as load_gazetteer
from outages.config import OSM_DIR_NAME, STALE_CHECK_H, Paths, dotenv_get, telegram_settings
from outages.map.app import NOT_MIGRATED, NOT_READY
from outages.map.build import load_boundaries, schema_ready
from outages.osm import extract as streets_index
from outages.timeparse import MANILA

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Taken:
    """What `take` did. `code`: 0 ok; 1 build failure or leak; 2 old data, bad folder or DB not
    ready. `error` is the message after "error: " (None when ok)."""

    code: int
    error: str | None
    taken_at: datetime | None = None
    last_check_at: str | None = None
    written: int = 0
    removed: int = 0
    weeks: int = 0
    ranges: tuple[str, ...] = ()
    size: int = 0


def secrets_for_scan(conn) -> tuple[list[int], str | None]:
    """Every chat ID we know (subscribers, the allowlist, the admin) and the bot token, to scan the
    snapshot's bytes for. Read before the guard is installed, on a connection of their own."""
    ids = [r[0] for r in conn.execute("SELECT chat_id FROM subscribers")]
    settings = telegram_settings()  # ValueError on a malformed chat ID list
    if settings is not None:
        ids += list(settings.allowed)
        if settings.admin_chat_id is not None:
            ids.append(settings.admin_chat_id)
    return ids, dotenv_get("TELEGRAM_BOT_TOKEN")


def take(paths: Paths, out: Path, now: datetime, allow_old: bool = False) -> Taken:
    """Build the snapshot at `now` and write it into `out`; nothing is written unless every guard
    passes (fresh data, a snapshot folder, no leak)."""
    if not paths.db.is_file():
        return Taken(2, NOT_READY)
    conn = db.connect_ro(paths.db)
    try:
        if not schema_ready(conn):
            return Taken(2, NOT_READY)
        if not db.is_current(conn):
            return Taken(2, NOT_MIGRATED)
        last = db.get_meta(conn, "last_check_at")
        try:
            chat_ids, token = secrets_for_scan(conn)
        except ValueError as e:
            return Taken(1, str(e))
    finally:
        conn.close()
    taken = now.astimezone(MANILA).replace(microsecond=0)
    age_h = None if last is None else (taken - datetime.fromisoformat(last)).total_seconds() / 3600
    if not allow_old and (age_h is None or age_h > STALE_CHECK_H):
        what = "data has never been checked" if age_h is None else f"data is {int(age_h)} h old"
        return Taken(2, f"{what}; run check first or pass --allow-old")
    problem = snapshot.check_out_dir(out)
    if problem:
        return Taken(2, problem)
    conn = db.connect_ro(paths.db)
    try:
        conn.execute("PRAGMA busy_timeout=30000")  # wait out a scheduled run's write
        snapshot.guard(conn)
        files = snapshot.build_files(
            conn,
            load_gazetteer(),
            load_boundaries(),
            taken,
            last,
            osm_base=streets_index.read_osm_base(paths.data / OSM_DIR_NAME),
        )
    except Exception as e:
        log.exception("snapshot: build failed")
        return Taken(1, f"snapshot not written: {e}")
    finally:
        conn.close()
    leak = snapshot.find_leak(files, chat_ids, token)
    if leak:
        return Taken(1, f"snapshot not written: the output contains a {leak}")
    written, removed = snapshot.write(out, files)
    parts = [k.split("/") for k in files]
    return Taken(
        0,
        None,
        taken_at=taken,
        last_check_at=last,
        written=written,
        removed=removed,
        weeks=sum(1 for p in parts if p[0] == "data" and p[2] == "weeks"),
        ranges=tuple(
            p[3].removesuffix(".json") for p in parts if p[0] == "data" and p[2] == "stats"
        ),
        size=sum(len(b) for b in files.values()),
    )
