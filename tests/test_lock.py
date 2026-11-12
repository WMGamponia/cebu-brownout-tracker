"""Single-instance lock (Phase 6 spec 4.4)."""

from __future__ import annotations

import os

import pytest
from outages.lock import LockHeld, acquire


def test_acquire_writes_pid(tmp_path):
    p = tmp_path / "serve.lock"
    lk = acquire(p)
    try:
        assert p.read_text().strip() == str(os.getpid())
    finally:
        lk.release()
    with acquire(p):
        pass


def test_held_lock_reports_pid(tmp_path, held_lock):
    p = tmp_path / "serve.lock"
    h = held_lock(p)
    with pytest.raises(LockHeld) as exc:
        acquire(p)
    assert exc.value.pid == str(h.pid)
    # a failed attempt never wipes the holder's PID
    assert p.read_text().strip() == str(h.pid)


def test_lock_free_after_holder_exits(tmp_path, held_lock):
    p = tmp_path / "serve.lock"
    held_lock(p).close()
    with acquire(p):
        assert p.read_text().strip() == str(os.getpid())


def test_stale_lock_file_does_not_block(tmp_path):
    p = tmp_path / "serve.lock"
    p.write_text("99999999\n")
    with acquire(p):
        assert p.read_text().strip() == str(os.getpid())


def test_no_lock_module_warns_and_runs(tmp_path, monkeypatch, caplog):
    from outages import lock

    monkeypatch.setattr(lock, "_lock_fd", None)
    with caplog.at_level("WARNING", logger="outages"), acquire(tmp_path / "serve.lock"):
        pass
    assert "no OS lock" in caplog.text
