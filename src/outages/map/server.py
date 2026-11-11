"""Running the map: in the foreground for `map`, on a daemon thread for `serve` (spec 4.5).

uvicorn calls `sys.exit(1)` when it can't bind, so `serve_forever` catches `SystemExit` as well
as `OSError` and reports by return value. Signal handlers stay with the main thread (uvicorn only
installs them there), so the map thread never takes Ctrl+C away from the scheduler.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import threading
from collections.abc import Callable

import uvicorn

log = logging.getLogger(__name__)


def is_loopback(host: str) -> bool:
    """True when only this PC can reach `host`."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Server(uvicorn.Server):
    async def startup(self, sockets=None) -> None:
        await super().startup(sockets)
        if self.started:
            log.info("map: http://%s:%s", self.config.host, self.config.port)


def serve_forever(app, host: str, port: int) -> bool:
    """Serve until shut down; False when the server never started (port in use, bad host)."""
    server = _Server(uvicorn.Config(app, host=host, port=port, log_config=None))
    with contextlib.suppress(SystemExit, OSError):
        server.run()
    return server.started


def start_thread(app, host: str, port: int, on_error: Callable[[str], None]) -> threading.Thread:
    """The map on a daemon thread; `on_error(message)` once if it fails to start or crashes."""

    def target() -> None:
        try:
            ok = serve_forever(app, host, port)
        except Exception as e:  # the thread must never take serve down with it
            log.exception("map: server thread failed")
            on_error(f"map: server failed on {host}:{port}: {e}")
            return
        if not ok:
            on_error(f"map: could not listen on {host}:{port} (port in use?)")

    t = threading.Thread(target=target, name="map-server", daemon=True)
    t.start()
    return t
