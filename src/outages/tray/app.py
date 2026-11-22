"""The tray icon itself (tray spec 5.3, 5.4, 5.6): the only module importing pystray and Pillow.

One worker thread owns the supervisor and does all the work: every 30 s, and right after a menu
action, it ticks the supervisor, asks serve's /healthz, reads the run status from the database,
fetches join requests, evaluates the rules, sends the new notifications and redraws the icon.
Menu clicks only queue actions for that thread, so the supervisor is never shared.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from outages import db, runstatus
from outages.config import MAP_PORT
from outages.runstatus import RunStatus
from outages.tray import autostart, feedback
from outages.tray.client import ServeClient
from outages.tray.seen import Seen
from outages.tray.state import Snapshot, View, evaluate, menu_key
from outages.tray.supervisor import Supervisor, lock_held, lock_pid, spawn_serve

log = logging.getLogger("outages")

TITLE = "Outage Tracker"
POLL_S = 30
POLL_FAST_S = 5  # while a run is in progress, or a Run now / restart is awaited
NOTIFY_MAX = 250
QUIT_WAIT_S = 600
COLOURS = {
    "green": "#2e9e44",
    "amber": "#e0a100",
    "red": "#d0342c",
    "grey": "#8a8a8a",
    "blue": "#2f6fd6",
}
NO_STATUS = RunStatus(None, None, None, None, None, None, None)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _image(colour: str) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((6, 6, 58, 58), fill=COLOURS[colour])
    return img


def _read_status(db_path: Path) -> RunStatus:
    if not db_path.is_file():
        return NO_STATUS
    try:
        conn = db.connect_ro(db_path)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            return runstatus.read(conn)
        finally:
            conn.close()
    except Exception:
        log.exception("tray: could not read the run status")
        return NO_STATUS


def _setup_logging(logs: Path) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(logs / "tray.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


class TrayApp:
    def __init__(self, root: Path, port: int = MAP_PORT) -> None:
        self.root, self.port = root, port
        self.data = root / "data"
        self.client = ServeClient(base_url=f"http://127.0.0.1:{port}", data_dir=self.data)
        self.sup = Supervisor(
            spawn=lambda: spawn_serve(root, port),
            lock_held=lambda: lock_held(self.data),
            clock=utcnow,
            lock_pid=lambda: lock_pid(self.data),
        )
        self.seen = Seen(self.data / "tray_state.json")
        self.actions: queue.Queue[Callable[[], None]] = queue.Queue()
        self.wake = threading.Event()
        self.quitting = threading.Event()
        self.view: View | None = None
        self.pending: list[dict] = []
        self.autostart: bool | None = None
        self.busy_text: str | None = None  # "Stopping after the current run…"
        self.last_healthy_at: datetime | None = None
        self._menu_key: tuple | None = None
        self.run_state: dict | None = None  # /admin/status while a run is in progress
        self.settings: dict | None = None  # the two "Start Ollama for … runs" switches
        self.awaited_since: datetime | None = None  # a Run now we're waiting to finish
        self.awaiting_restart = False
        self.icon = pystray.Icon("outage-tracker", _image("grey"), TITLE, menu=self._menu())

    # --- worker thread -------------------------------------------------------------------------

    def _queue(self, fn: Callable[[], None]) -> Callable[..., None]:
        def action(*_args) -> None:
            self.actions.put(fn)
            self.wake.set()

        return action

    def _notify(self, text: str) -> bool:
        """Show a Windows notification; False when it couldn't be shown (it is retried)."""
        if len(text) > NOTIFY_MAX:  # Windows' limit is 255 characters
            text = text[: NOTIFY_MAX - 1] + "…"
        try:
            self.icon.notify(text, TITLE)
        except Exception:
            log.exception("tray: notification failed")
            return False
        return True

    def _redraw_menu(self) -> None:
        key = menu_key(
            self.view,
            self.pending,
            self.autostart,
            self.sup.state,
            self.busy_text,
            self.settings,
        )
        if key != self._menu_key:  # never destroy an open menu for nothing
            self._menu_key = key
            self.icon.update_menu()

    def _refresh(self) -> None:
        self.sup.tick()
        healthy = self.client.healthz()
        if healthy:
            self.sup.mark_healthy()
            self.last_healthy_at = utcnow()
        status = _read_status(self.data / "outages.db")
        self.pending = self.client.pending() if healthy else []
        st = self.client.status() if healthy else None
        self.run_state = st if st and st.get("running") else None
        if healthy:
            self.settings = self.client.settings() or self.settings
        crashed = self.sup.last_crash if self.sup.state != "capped" else None
        snap = Snapshot(
            now=utcnow(),
            proc=self.sup.state,
            proc_since=self.sup.since,
            healthz_ok=healthy,
            status=status,
            pending=self.pending,
            crashed_at=crashed,
            last_healthy_at=self.last_healthy_at,
            run=self.run_state,
        )
        self.view = evaluate(snap)
        self.seen.fresh(self.view.events, deliver=lambda e: self._notify(e.text))
        running = self.run_state is not None
        known = st is not None  # serve said whether a run is in progress
        finished = feedback.should_announce_finish(self.awaited_since, status, running, known)
        if finished and self._notify(feedback.run_finished(status, since=self.awaited_since)):
            self.awaited_since = None
        back = self.awaiting_restart and healthy and self.sup.state in ("running", "adopted")
        if back and self._notify(feedback.restarted()):
            self.awaiting_restart = False
        self.icon.icon = _image(self.view.colour)
        self.icon.title = self.view.tooltip
        self._redraw_menu()

    def _start(self) -> None:
        try:
            self.seen.load()
            self.autostart = autostart.is_enabled()
        except Exception:
            log.exception("tray: start-up step failed")
        self.sup.start()  # a failed spawn counts as a crash inside the supervisor

    def _work(self) -> None:
        try:
            self._start()
        except Exception:  # never leave the icon stuck at "Starting…" with no worker
            log.exception("tray: could not start")
            self._notify("The tray couldn't start serve; see data\\logs\\tray.log")
        while not self.quitting.is_set():
            try:
                while not self.actions.empty():
                    self.actions.get_nowait()()
                if self.quitting.is_set():
                    break
                self._refresh()
            except Exception:
                log.exception("tray: refresh failed")
            fast = self.run_state or self.awaited_since or self.awaiting_restart
            self.wake.wait(POLL_FAST_S if fast else POLL_S)
            self.wake.clear()

    # --- actions (run on the worker thread) ----------------------------------------------------

    def _run_now(self) -> None:
        clicked = utcnow()
        result = self.client.run_now()
        if result == "started":
            self.awaited_since = clicked
            self._notify(feedback.run_started())
        elif result == "busy":
            st = self.client.status() or {}
            try:
                started = datetime.fromisoformat(st["started_at"])
                elapsed = int((utcnow() - started).total_seconds())
            except (KeyError, TypeError, ValueError):
                elapsed = 0
            self._notify(feedback.run_busy(st.get("step"), elapsed))
        else:
            self._notify("Couldn't reach serve to start a run")

    def _decide(self, chat_id: int, approve: bool, name: str) -> None:
        self._notify(feedback.decided(name, approve, self.client.decide(chat_id, approve)))

    def _toggle_ollama(self, key: str, which: str) -> None:
        want = not bool((self.settings or {}).get(key))
        new = self.client.set_settings({key: want})
        if new is None:
            self._notify("Couldn't reach serve to change that setting")
            return
        self.settings = new
        self._notify(feedback.ollama_setting(which, bool(new.get(key))))

    def _toggle_autostart(self) -> None:
        want = not bool(self.autostart)
        result = autostart.set_enabled(want)
        if result == "denied":
            self._notify("Run scripts\\install-task.ps1 from an elevated PowerShell to change this")
        elif result == "missing":
            self._notify("No logon task yet: run scripts\\install-task.ps1 (elevated) first")
        elif result == "error":
            self._notify("Couldn't change the logon task")
        self.autostart = autostart.is_enabled()
        if result == "ok" and self.autostart is not None:
            self._notify(feedback.autostart(self.autostart))

    def _stopping(self, fn: Callable[[], None]) -> None:
        self.busy_text = "Stopping after the current run…"
        self._redraw_menu()
        try:
            fn()
        finally:
            self.busy_text = None

    def _start_or_restart(self) -> None:
        self._notify(feedback.restarting())
        box: list = []
        if self.sup.state in ("stopped", "capped"):
            box.append(self.sup.restart_now(lambda: False, 0))
        else:
            self._stopping(lambda: box.append(self.sup.restart_now(self.client.stop, QUIT_WAIT_S)))
        if box and box[0] == "left":
            self._notify(feedback.cant_restart(lock_pid(self.data)))
        else:
            self.awaiting_restart = True

    def _quit(self) -> None:
        self._stopping(lambda: self.sup.stop(self.client.stop, QUIT_WAIT_S))
        self.quitting.set()
        self.icon.stop()

    # --- menu (built on pystray's thread from the last view) -----------------------------------

    def _menu(self) -> pystray.Menu:
        return pystray.Menu(lambda: self._items())

    def _items(self):
        item, sep = pystray.MenuItem, pystray.Menu.SEPARATOR
        v = self.view
        headline = self.busy_text or (v.headline if v else "Starting…")
        yield item(f"● {headline}", None, enabled=False)
        for line in v.lines if v else []:
            yield item(line, None, enabled=False)
        yield sep
        can_act = bool(v and v.can_act) and self.busy_text is None
        can_run = bool(v and v.can_run) and self.busy_text is None
        yield item("Run now", self._queue(self._run_now), enabled=can_run)
        people = []
        for p in self.pending:
            cid = p["chat_id"]
            who = p.get("name") or f"chat {cid}"
            people.append(
                item(
                    who,
                    pystray.Menu(
                        item(
                            "Approve",
                            self._queue(lambda c=cid, n=who: self._decide(c, True, n)),
                        ),
                        item(
                            "Deny",
                            self._queue(lambda c=cid, n=who: self._decide(c, False, n)),
                        ),
                    ),
                )
            )
        yield item(
            f"Join requests ({len(people)})",
            pystray.Menu(*people) if people else pystray.Menu(item("None", None, enabled=False)),
            enabled=can_act and bool(people),
        )
        yield sep
        yield item("Open log", lambda *_: self._open(self.data / "logs" / "outages.log"))
        yield item("Open map", lambda *_: webbrowser.open(f"http://127.0.0.1:{self.port}/"))
        yield item(
            "Start at logon",
            self._queue(self._toggle_autostart),
            checked=lambda _item: self.autostart is True,
            enabled=self.autostart is not None,
        )
        opts = self.settings or {}
        yield item(
            "Start Ollama for scheduled runs",
            self._queue(lambda: self._toggle_ollama("ollama_scheduled", "scheduled")),
            checked=lambda _item: bool((self.settings or {}).get("ollama_scheduled")),
            enabled=can_act and "ollama_scheduled" in opts,
        )
        yield item(
            "Start Ollama for Run now",
            self._queue(lambda: self._toggle_ollama("ollama_manual", "Run now")),
            checked=lambda _item: bool((self.settings or {}).get("ollama_manual")),
            enabled=can_act and "ollama_manual" in opts,
        )
        yield sep
        stopped = self.sup.state in ("stopped", "capped")
        yield item(
            "Start serve" if stopped else "Restart serve",
            self._queue(self._start_or_restart),
            enabled=self.busy_text is None,
        )
        yield item("Quit (stops serve)", self._queue(self._quit), enabled=self.busy_text is None)

    @staticmethod
    def _open(path: Path) -> None:
        try:
            os.startfile(path)  # type: ignore[attr-defined]  # Windows only
        except OSError:
            log.exception("tray: could not open %s", path)

    # --- main ----------------------------------------------------------------------------------

    def run(self) -> int:
        def setup(icon: pystray.Icon) -> None:
            icon.visible = True
            threading.Thread(target=self._work, name="tray-worker", daemon=True).start()

        self.icon.run(setup=setup)
        self.client.close()
        return 0


def run(root: Path, port: int = MAP_PORT) -> int:
    _setup_logging(root / "data" / "logs")
    log.info("tray: starting in %s (map port %d)", root, port)
    return TrayApp(root, port).run()
