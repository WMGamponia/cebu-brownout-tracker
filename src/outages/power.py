"""Keep a sleeping PC awake through a scheduled run (Windows; a no-op elsewhere).

A PC woken by a timer with no user input goes back to sleep after the "unattended sleep
timeout" (about 2 minutes by default), which can cut a 06:00 run short. `keep_awake()` marks
the run's thread as needing the system; `hold(seconds)` does the same for the wake task, so the
PC is still awake when `serve`'s job starts.

`SetThreadExecutionState` is per thread: call it on the thread that does the work.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable, Iterator

log = logging.getLogger("outages")

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_set_state: Callable[[int], object] | None
try:
    import ctypes

    _set_state = ctypes.windll.kernel32.SetThreadExecutionState  # type: ignore[attr-defined]
except (ImportError, AttributeError):  # not Windows
    _set_state = None


@contextlib.contextmanager
def keep_awake() -> Iterator[None]:
    """While inside, the system may not sleep for being idle; restored on exit."""
    if _set_state is None:
        yield
        return
    _set_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _set_state(ES_CONTINUOUS)


def hold(seconds: float) -> None:
    """Keep the system awake for `seconds` (the wake task: `pythonw -c ... hold(900)`)."""
    with keep_awake():
        time.sleep(seconds)
