"""The alias suggestion queue: `data/streets/alias_queue.csv` (Streets v2 spec 7.6).

UTF-8 with a BOM so a spreadsheet opens it cleanly; read back whether or not the spreadsheet
kept the BOM or wrote CRLF. Every write goes to a per-writer temp file and replaces the queue.
Read-modify-write goes through `update`, which holds a thread lock and a lock file shared by
every process (`streets suggest`, `serve`'s hook and review page, `--apply`) and re-reads the
queue inside them, so no writer can put back an older copy over a newer decision."""

from __future__ import annotations

import contextlib
import csv
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

COLUMNS = (
    "town_psgc",
    "town",
    "veco_key",
    "veco_name",
    "mentions",
    "pick",
    "pick_source",
    "other_pick",
    "candidates",
    "reason",
    "status",
    "decision",
)
LOCK = threading.RLock()
SEP = "|"
DECISIONS = ("y", "n", "skip")


class QueueError(ValueError):
    """The queue file can't be used (a column missing)."""


class QueueBusy(RuntimeError):
    """The queue file is open elsewhere (a spreadsheet holds it on Windows)."""


class QueueConflict(RuntimeError):
    """The row was already decided."""


def _key(r: dict) -> tuple[str, str]:
    return (r["town_psgc"], r["veco_key"])


def _sorted(rows: list[dict]) -> list[dict]:
    def mentions(r: dict) -> int:
        try:
            return int(r.get("mentions") or 0)
        except ValueError:
            return 0

    return sorted(rows, key=lambda r: (-mentions(r), r["town_psgc"], r["veco_key"]))


def read(path: Path) -> list[dict]:
    """The queue's rows ([] when there is no queue); raises QueueError on a missing column."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise QueueError(f"{path}: missing column(s) {', '.join(missing)}")
        return [{c: (r.get(c) or "").strip() for c in COLUMNS} for r in reader]


def write(path: Path, rows: list[dict]) -> None:
    """Sorted by mentions, atomically (temp + replace). Raises QueueBusy if the file is held."""
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        with tmp.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
            w.writeheader()
            w.writerows(_sorted(rows))
        try:
            os.replace(tmp, path)
        except PermissionError as e:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise QueueBusy(f"{path} is open in another program; close it and retry") from e


if os.name == "nt":
    import msvcrt

    def _try_lock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


LOCK_WAIT_S = 30.0


@contextlib.contextmanager
def locked(path: Path, wait_s: float = LOCK_WAIT_S) -> Iterator[None]:
    """This process's lock plus `<queue>.lock`, shared by every process that writes the queue.
    Raises QueueBusy after `wait_s`."""
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path.with_name(path.name + ".lock"), "a+b") as f:
            deadline = time.monotonic() + wait_s
            while True:
                try:
                    _try_lock(f)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise QueueBusy(f"{path} is being written by another process") from None
                    time.sleep(0.1)
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    _unlock(f)


def update(path: Path, change: Callable[[list[dict]], list[dict]]) -> list[dict]:
    """Read the queue fresh under `locked`, apply `change`, write the result; returns it."""
    with locked(path):
        rows = change(read(path))
        write(path, rows)
        return rows


def merge(existing: list[dict], new: list[dict]) -> list[dict]:
    """Existing rows (and their decisions) kept as they are; new (town, key) rows appended."""
    seen = {_key(r) for r in existing}
    return _sorted([*existing, *(r for r in new if _key(r) not in seen)])


def choices(row: dict) -> list[str]:
    """The names a decision may name: the pick, the other arm's pick and the top candidates."""
    out = [row["pick"], row["other_pick"], *row["candidates"].split(SEP)]
    return [n for i, n in enumerate(out) if n and n not in out[:i]]


def set_decision(path: Path, town: str, key: str, decision: str) -> None:
    """Record `y`, `n`, a candidate's exact name, or nothing for `skip`.

    KeyError: no such row. QueueConflict: already decided. ValueError: not a valid decision."""

    def change(rows: list[dict]) -> list[dict]:
        row = next((r for r in rows if _key(r) == (town, key)), None)
        if row is None:
            raise KeyError((town, key))
        if row["decision"]:
            raise QueueConflict(f"{town} {key} already decided")
        if decision == "y" and not row["pick"]:
            raise ValueError("no pick to accept")
        if decision not in ("y", "n", "skip") and decision not in choices(row):
            raise ValueError(f"{decision!r} is not y, n, skip or one of the candidates")
        if decision != "skip":
            row["decision"] = decision
        return rows

    update(path, change)
