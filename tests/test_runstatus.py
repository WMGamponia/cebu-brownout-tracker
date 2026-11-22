"""The last run's and publish's outcome in `meta`, read by the tray (tray spec 5.2)."""

from __future__ import annotations

from datetime import UTC, datetime

from outages import db, runstatus
from outages.runstatus import RunStatus

NOW = datetime(2026, 11, 22, 12, 0, 5, tzinfo=UTC)


def _conn(tmp_path):
    return db.connect(tmp_path / "outages.db")


def test_read_empty(tmp_path):
    conn = _conn(tmp_path)
    assert runstatus.read(conn) == RunStatus(None, None, None, None, None, None, None)


def test_record_run_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    runstatus.record_run(conn, "manual", False, NOW)
    s = runstatus.read(conn)
    assert (s.last_run_at, s.last_run_kind, s.last_check_ok) == (NOW, "manual", False)


def test_record_publish_roundtrip(tmp_path):
    conn = _conn(tmp_path)
    runstatus.record_publish(conn, "skipped", "not on main", NOW)
    s = runstatus.read(conn)
    assert (s.publish_status, s.publish_detail, s.publish_at) == ("skipped", "not on main", NOW)


def test_read_last_check_at(tmp_path):
    conn = _conn(tmp_path)
    db.set_meta(conn, "last_check_at", NOW.isoformat())
    conn.commit()
    assert runstatus.read(conn).last_check_at == NOW
