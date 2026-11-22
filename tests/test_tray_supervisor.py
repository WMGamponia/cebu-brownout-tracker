"""The tray starts, restarts, adopts and stops `serve` (tray spec 5.1, 5.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from outages.tray.supervisor import Supervisor

T0 = datetime(2026, 11, 23, 0, 0, tzinfo=UTC)


class FakeProc:
    def __init__(self, pid: int, exits_with: int | None = None):
        self.pid, self.code, self.killed = pid, exits_with, False
        self.stop_after_request = True

    def poll(self):
        return self.code

    def kill_tree(self):
        self.killed = True
        self.code = 1


class World:
    def __init__(self, locked=False):
        self.now = T0
        self.locked = locked
        self.spawned: list[FakeProc] = []
        self.killed_pids: list[int] = []
        self.next_exit: int | None = None

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += timedelta(seconds=s)

    def spawn(self):
        p = FakeProc(100 + len(self.spawned), self.next_exit)
        self.next_exit = None
        self.spawned.append(p)
        return p

    def sup(self) -> Supervisor:
        return Supervisor(
            spawn=self.spawn,
            lock_held=lambda: self.locked,
            clock=self.clock,
            sleep=self.sleep,
            kill_pid=self.killed_pids.append,
            lock_pid=lambda: 4242,
        )

    def tick(self, sup, seconds=0):
        self.now += timedelta(seconds=seconds)
        sup.tick()


def crash(sup, world):
    sup._proc.code = 1
    world.tick(sup)


def test_start_spawns():
    w = World()
    s = w.sup()
    s.start()
    assert s.state == "starting" and len(w.spawned) == 1 and s.since == T0
    s.mark_healthy()
    assert s.state == "running"


def test_start_adopts_when_locked():
    w = World(locked=True)
    s = w.sup()
    s.start()
    assert s.state == "adopted" and w.spawned == []


def test_exit_3_adopts_not_crash():
    w = World()
    w.next_exit = 3
    s = w.sup()
    s.start()
    w.locked = True  # an orphaned serve holds the lock
    w.tick(s)
    assert s.state == "adopted"
    assert s.last_crash is None


def test_crash_backoff_10_60_300():
    w = World()
    s = w.sup()
    s.start()
    for delay in (10, 60, 300):
        crash(s, w)
        assert s.state == "restarting" and s.last_crash == w.now
        n = len(w.spawned)
        w.tick(s, delay - 1)
        assert len(w.spawned) == n
        w.tick(s, 1)
        assert len(w.spawned) == n + 1 and s.state == "starting"


def test_cap_after_4_in_30_min():
    w = World()
    s = w.sup()
    s.start()
    for delay in (10, 60, 300):
        crash(s, w)
        w.tick(s, delay)
    crash(s, w)
    assert s.state == "capped"
    n = len(w.spawned)
    w.tick(s, 3600)
    assert len(w.spawned) == n


def test_crashes_outside_window_dont_cap():
    w = World()
    s = w.sup()
    s.start()
    for _ in range(5):
        crash(s, w)
        w.tick(s, 10)
        w.tick(s, 31 * 60)  # each crash in its own half hour
    assert s.state == "starting"


def test_adopted_lock_freed_spawns():
    w = World(locked=True)
    s = w.sup()
    s.start()
    w.locked = False
    w.tick(s, 30)
    assert s.state == "starting" and len(w.spawned) == 1


def test_stop_graceful():
    w = World()
    s = w.sup()
    s.start()
    p = w.spawned[0]

    def request_stop():
        p.code = 0
        return True

    s.stop(request_stop, 600)
    assert s.state == "stopped" and not p.killed


def test_stop_timeout_kills():
    w = World()
    s = w.sup()
    s.start()
    p = w.spawned[0]
    s.stop(lambda: True, 600)
    assert p.killed and s.state == "stopped"
    assert w.now - T0 >= timedelta(seconds=600)


def test_stop_unreachable_kills_at_once():
    w = World()
    s = w.sup()
    s.start()
    p = w.spawned[0]
    s.stop(lambda: False, 600)  # /admin/stop couldn't be reached
    assert p.killed and w.now - T0 < timedelta(seconds=5)


def test_stop_adopted_waits_for_lock():
    w = World(locked=True)
    s = w.sup()
    s.start()

    def request_stop():
        w.locked = False
        return True

    s.stop(request_stop, 600)
    assert s.state == "stopped" and w.killed_pids == []


def test_stop_adopted_timeout_kills_lock_pid():
    w = World(locked=True)
    s = w.sup()
    s.start()
    s.stop(lambda: True, 600)
    assert w.killed_pids == [4242] and s.state == "stopped"


def test_stopped_stays_stopped():
    w = World()
    s = w.sup()
    s.start()
    w.spawned[0].code = 0
    s.stop(lambda: True, 600)
    w.tick(s, 3600)
    assert s.state == "stopped" and len(w.spawned) == 1


def test_lock_held_and_pid_real_lock(tmp_path, held_lock):
    from outages.tray.supervisor import lock_held, lock_pid

    assert lock_held(tmp_path) is False
    h = held_lock(tmp_path / "serve.lock")
    assert lock_held(tmp_path) is True
    assert lock_pid(tmp_path) == h.pid  # the probe never overwrote the holder's PID
    h.close()
    assert lock_held(tmp_path) is False


def test_serve_command_port():
    from outages.tray.supervisor import serve_command

    assert serve_command(8765)[-2:] == ["outages", "serve"]
    assert serve_command(8799)[-4:] == ["outages", "serve", "--map-port", "8799"]


def test_restart_now_clears_cap():
    w = World()
    s = w.sup()
    s.start()
    for delay in (10, 60, 300):
        crash(s, w)
        w.tick(s, delay)
    crash(s, w)
    assert s.state == "capped"
    s.restart_now(lambda: True, 600)
    assert s.state == "starting"
    crash(s, w)
    assert s.state == "restarting"
