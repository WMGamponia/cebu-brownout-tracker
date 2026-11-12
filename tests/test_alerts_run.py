"""Alert run: send, record, retry, deactivate, admin notes (Phase 3 spec 4.3, 5.6)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from outages import db
from outages.alerts import store
from outages.alerts.run import RunSummary, admin_notes, alert_run, dry_run
from outages.alerts.select import load
from outages.areas import store as areas_store
from outages.areas.entities_rules import RulesExtractor
from outages.areas.gazetteer import load as load_gazetteer
from outages.notify import SendError
from outages.timeparse import MANILA

GAZ = load_gazetteer()
LAHUG = "730600041"
ADMIN = 999
SUBBED = datetime(2026, 11, 1, tzinfo=UTC)


def m(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=MANILA)


NOW = m(13, 6)


class RecordingNotifier:
    """Records every message; `fail` maps a 1-based call number to the SendError kind raised."""

    def __init__(self, fail: dict[int, str] | None = None, plain: set[int] | None = None):
        self.sent: list[tuple[int, str]] = []
        self.calls = 0
        self.fail = fail or {}
        self.plain = plain or set()

    def send(self, chat_id: int, html: str):
        self.calls += 1
        if self.calls in self.fail:
            raise SendError(self.fail[self.calls])  # type: ignore[arg-type]
        self.sent.append((chat_id, html))
        return "plain" if self.calls in self.plain else "html"

    def to(self, chat_id: int) -> list[str]:
        return [h for c, h in self.sent if c == chat_id]


_ids = {"post": 0, "outage": 0}


def add_outage(
    conn: sqlite3.Connection,
    start: datetime,
    raw: str = "Portion of Cebu City (Lahug) along Gorordo Ave.",
    *,
    post_status: str = "ok",
    parse: bool = True,
    purpose: str = "Line maintenance",
    hours: int = 8,
) -> int:
    """A post with one version and one outage; areas parsed unless `parse=False`."""
    pid = conn.execute("SELECT coalesce(max(id), 0) + 1 FROM posts").fetchone()[0]
    conn.execute(
        "INSERT INTO posts (id, url, slug, first_seen_at, parse_status, parse_error)"
        " VALUES (?, ?, ?, 't', ?, ?)",
        (
            pid,
            f"https://example.invalid/p{pid}",
            f"post-{pid}",
            post_status,
            "bad time" if post_status == "failed" else None,
        ),
    )
    conn.execute(
        "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
        " VALUES (?, ?, 't', ?, x'00')",
        (pid, pid, f"h{pid}"),
    )
    h = areas_store.areas_hash(raw)
    cur = conn.execute(
        "INSERT INTO outages (post_id, seq, heading_text, start_at, end_at, status, purpose,"
        " areas_raw, areas_key, first_seen_version_id, last_seen_version_id, areas_hash)"
        " VALUES (?, 0, 'h', ?, ?, 'scheduled', ?, ?, 'k', ?, ?, ?)",
        (
            pid,
            start.isoformat(),
            (start + timedelta(hours=hours)).isoformat(),
            purpose,
            raw,
            pid,
            pid,
            h,
        ),
    )
    if parse:
        p = areas_store.parse_one(raw, GAZ, RulesExtractor())
        areas_store.write_parse(conn, h, p, "rules", NOW)
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


def subscribe(conn, chat_id: int = 42, code: str = LAHUG) -> int:
    s = store.upsert_subscriber(conn, chat_id, "me", SUBBED)
    store.subscribe(conn, s.id, code, SUBBED)
    conn.commit()
    return s.id


def n_sent(conn) -> int:
    return conn.execute("SELECT count(*) FROM alerts_sent").fetchone()[0]


def run(conn, notifier, now=NOW, check_ok=True, admin=ADMIN) -> RunSummary:
    return alert_run(conn, notifier, now, GAZ, admin, check_ok)


def test_second_run_sends_nothing(conn):
    subscribe(conn)
    add_outage(conn, m(13, 9))
    n = RecordingNotifier()
    s = run(conn, n)
    assert (s.subscribers, s.sent, s.items, s.failed) == (1, 1, 1, 0)
    assert "Gorordo Ave." in n.to(42)[0]
    assert n_sent(conn) == 1
    n2 = RecordingNotifier()
    s2 = run(conn, n2)
    assert (s2.sent, s2.items) == (0, 0) and n2.to(42) == []


def test_retry_failure_unrecorded_then_sent(conn):
    subscribe(conn)
    add_outage(conn, m(13, 9))
    s = run(conn, RecordingNotifier(fail={1: "retry"}))
    assert (s.sent, s.failed) == (0, 1) and n_sent(conn) == 0
    n = RecordingNotifier()
    assert run(conn, n).sent == 1 and len(n.to(42)) == 1 and n_sent(conn) == 1


def test_partial_send_records_nothing(conn):
    subscribe(conn)
    for i in range(60):
        add_outage(conn, m(13, 9) + timedelta(minutes=i), purpose="P " + "x" * 120)
    s = run(conn, RecordingNotifier(fail={2: "retry"}), admin=None)
    assert s.failed == 1 and n_sent(conn) == 0
    n = RecordingNotifier()
    s = run(conn, n, admin=None)
    assert len(n.to(42)) >= 2 and s.items == 60 and n_sent(conn) == 60


def test_blocked_deactivates(conn):
    sid = subscribe(conn)
    add_outage(conn, m(13, 9))
    s = run(conn, RecordingNotifier(fail={1: "blocked"}))
    assert s.failed == 1 and n_sent(conn) == 0
    assert conn.execute("SELECT active FROM subscribers WHERE id = ?", (sid,)).fetchone()[0] == 0
    assert run(conn, RecordingNotifier()).subscribers == 0


def test_admin_notes_once_per_key(conn):
    subscribe(conn)
    add_outage(conn, m(13, 9), post_status="failed")
    add_outage(conn, m(13, 10), "Portion of Cebu City (Kamputhaw) along X St.", parse=False)
    add_outage(conn, m(14, 9), "Portion of Minglanilla (Poblacion) along Y St.")
    n = RecordingNotifier()
    s = run(conn, n)
    notes = "\n".join(n.to(ADMIN))
    assert s.notes == 3
    assert "post-1" in notes and "bad time" in notes
    assert "1 upcoming outages have unparsed areas; run `python -m outages areas`" in notes
    assert "Poblacion" in notes and "alias" in notes
    # the failed post's outage still alerted, with the caveat
    assert "couldn't read the update" in n.to(42)[0]
    n2 = RecordingNotifier()
    assert run(conn, n2).notes == 0 and n2.to(ADMIN) == []


def test_admin_notes_not_marked_without_admin_chat(conn):
    add_outage(conn, m(13, 9), post_status="failed")
    assert run(conn, RecordingNotifier(), admin=None).notes == 0
    n = RecordingNotifier()
    assert run(conn, n).notes == 1 and len(n.to(ADMIN)) == 1


def test_admin_note_send_failure_retried(conn):
    add_outage(conn, m(13, 9), post_status="failed")
    assert run(conn, RecordingNotifier(fail={1: "retry"})).notes == 0
    assert run(conn, RecordingNotifier()).notes == 1


def test_check_failed_note(conn):
    db.set_meta(conn, "last_check_at", "2026-11-12T12:00:00+00:00")
    conn.commit()
    n = RecordingNotifier()
    assert run(conn, n, check_ok=False).notes == 1
    (note,) = n.to(ADMIN)
    assert "VECO not reachable; alerts from data as of 2026-11-12T12:00:00+00:00" in note
    assert run(conn, RecordingNotifier(), check_ok=False).notes == 0  # same hour


def test_plain_resend_note(conn):
    sid = subscribe(conn)
    add_outage(conn, m(13, 9))
    n = RecordingNotifier(plain={1})
    s = run(conn, n)
    assert s.sent == 1 and n_sent(conn) == 1
    assert any(f"subscriber {sid}" in x and "plain text" in x for x in n.to(ADMIN))


def test_admin_notes_skip_started_and_cancelled(conn):
    add_outage(conn, m(13, 1), post_status="failed", hours=2)  # ended
    assert admin_notes(conn, load(conn, NOW, GAZ), NOW, True) == []


def test_run_never_builds_fetcher(conn, monkeypatch):
    from outages import __main__ as cli

    monkeypatch.setattr(cli, "make_fetcher", lambda: pytest.fail("alert_run fetched"))
    subscribe(conn)
    add_outage(conn, m(13, 9))
    assert run(conn, RecordingNotifier()).sent == 1


def test_dry_run_prints_and_writes_nothing(conn, tmp_path):
    subscribe(conn)
    add_outage(conn, m(13, 9), post_status="failed")
    conn.close()
    path = tmp_path / "o.db"
    before = path.read_bytes()
    ro = db.connect_ro(path)
    out = dry_run(ro, NOW, GAZ)
    ro.close()
    assert path.read_bytes() == before
    assert "--- to chat 42 ---" in out and "Gorordo Ave." in out
    assert "admin notes" in out and "post-1" in out
    assert "would send 1 message(s) with 1 item(s) to 1 of 1 subscriber(s)" in out


def test_dry_run_on_unmigrated_db(tmp_path):
    path = tmp_path / "v2.db"
    c = sqlite3.connect(path)
    for n, sql in db._migrations():
        if n <= 2:
            c.executescript(f"BEGIN;\n{sql}\nINSERT INTO schema_version VALUES ({n});\nCOMMIT;")
    c.close()
    before = path.read_bytes()
    ro = db.connect_ro(path)
    assert dry_run(ro, NOW, GAZ) == "no subscribers (migration 003 not applied)\n"
    ro.close()
    assert path.read_bytes() == before


def test_removed_outage_alerts_after_parse_rows_deleted(conn):
    """End to end: `removed` change, its parse rows gone (as `areas` leaves them)."""
    import json

    subscribe(conn)
    oid = add_outage(conn, m(14, 9))
    raw, start = conn.execute(
        "SELECT areas_raw, start_at FROM outages WHERE id = ?", (oid,)
    ).fetchone()
    conn.execute("UPDATE outages SET status = 'removed' WHERE id = ?", (oid,))
    conn.execute(
        "INSERT INTO outage_changes (outage_id, version_id, kind, old_json, new_json, detected_at)"
        " VALUES (?, 1, 'removed', ?, NULL, '2026-11-12T03:00:00+00:00')",
        (oid, json.dumps({"areas_raw": raw, "start_at": start, "end_at": start})),
    )
    areas_store.delete_orphans(conn)
    conn.commit()
    assert conn.execute("SELECT count(*) FROM area_barangays").fetchone()[0] == 0
    n = RecordingNotifier()
    assert run(conn, n).items == 1
    assert "no longer listed in VECO's post" in n.to(42)[0]


def test_unparsed_outage_listed_in_snapshot(conn):
    oid = add_outage(conn, m(14, 9), parse=False)
    h = conn.execute("SELECT areas_hash FROM outages WHERE id = ?", (oid,)).fetchone()[0]
    assert load(conn, NOW, GAZ).unparsed == frozenset({h})


# --- Phase 5 (spec 4.9) ---


def test_only_approved_alerted(conn):
    subscribe(conn, 42)
    for chat, status in ((43, "pending"), (44, "blocked")):
        sid = subscribe(conn, chat)
        conn.execute("UPDATE subscribers SET status = ? WHERE id = ?", (status, sid))
    conn.commit()
    add_outage(conn, m(13, 9))
    n = RecordingNotifier()
    s = run(conn, n)
    assert [c for c, _ in n.sent] == [42]
    assert s.subscribers == 1


def test_record_after_forget_is_skipped(conn):
    """/forget lands (bot thread) while this run is sending to that user."""
    sid = subscribe(conn, 42)
    add_outage(conn, m(13, 9))

    class ForgetDuringSend(RecordingNotifier):
        def send(self, chat_id, html):
            out = super().send(chat_id, html)
            with conn:
                store.forget(conn, chat_id)
            return out

    s = run(conn, ForgetDuringSend())
    assert s.sent == 1
    assert n_sent(conn) == 0
    assert conn.execute("SELECT count(*) FROM subscribers WHERE id = ?", (sid,)).fetchone()[0] == 0


def test_send_spacing(conn):
    subscribe(conn, 42)
    subscribe(conn, 43)
    add_outage(conn, m(13, 9))
    slept = []
    alert_run(conn, RecordingNotifier(), NOW, GAZ, ADMIN, True, sleep=slept.append)
    assert slept and all(x >= 0.05 for x in slept)
    assert len(slept) >= 1
