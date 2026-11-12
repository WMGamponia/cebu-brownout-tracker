"""Single-instance lock for the commands that write or send (Phase 6 spec 4.4).

The OS holds the lock, so it goes away when the process exits or crashes; the file only
carries the holder's PID for the message. The locked byte sits at `LOCK_OFFSET`, away from
the PID, because Windows locks are mandatory: a byte-range lock over the PID would make it
unreadable to the second process.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("outages")

LOCK_OFFSET = 1024

_lock_fd: Callable[[int], None] | None
_unlock_fd: Callable[[int], None] | None

try:
    import msvcrt

    def _lock_fd(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

except ImportError:  # not Windows
    try:
        import fcntl

        def _lock_fd(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def _unlock_fd(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_UN)

    except ImportError:
        _lock_fd = _unlock_fd = None


class LockHeld(Exception):
    """Another process holds the lock; `pid` is its PID as written, or "unknown"."""

    def __init__(self, pid: str):
        super().__init__(f"serve is already running (pid {pid}); stop it first")
        self.pid = pid


class Lock:
    """A held lock; `release()` or leave the `with` block to let it go."""

    def __init__(self, fd: int | None):
        self._fd = fd

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            if _unlock_fd is not None:
                _unlock_fd(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self) -> Lock:
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _read_pid(path: Path) -> str:
    try:
        pid = path.read_text(encoding="ascii", errors="replace").strip()
    except OSError:
        return "unknown"
    return pid if pid.isdigit() else "unknown"


def acquire(path: Path) -> Lock:
    """Take the lock at `path` or raise `LockHeld`. Lock first, then write the PID, so a
    failed attempt never wipes the holder's PID. With no OS lock available: warn and run."""
    if _lock_fd is None:
        log.warning("no OS lock available; running without the single-instance guard")
        return Lock(None)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        _lock_fd(fd)
    except OSError:
        os.close(fd)
        raise LockHeld(_read_pid(path)) from None
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    os.fsync(fd)
    return Lock(fd)
