"""Backfill report and CLI commands over a temp data dir (spec 7)."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime, timedelta

import pytest
from conftest import read_post
from test_pipeline import JAN, NOV, NOW, Site, ref, sitemap

from outages import __main__ as cli
from outages import db, pipeline, report, scheduler
from outages.config import SITEMAP_URL, Paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Paths(tmp_path)


def _store(paths: Paths, *slugs: str, html: dict[str, str] | None = None) -> None:
    conn = db.connect(paths.db)
    for s in slugs:
        pipeline.process_post(conn, ref(s), (html or {}).get(s) or read_post(s), NOW)
    conn.close()


def test_report_rows_and_failures(home):
    broken = read_post(JAN).replace("8:00 AM to 10:00 AM (2hrs)", "8 AM till 10 AM", 1)
    _store(home, NOV, JAN, html={JAN: broken})
    conn = db.connect(home.db)
    rows = {r.slug: r for r in report.build(conn)}
    conn.close()
    nov = rows[NOV]
    assert (nov.outages, nov.cancelled, nov.additional, nov.status) == (34, 3, 0, "ok")
    assert nov.first_day == "2026-11-08" and nov.last_day == "2026-11-14"
    assert rows[JAN].status == "failed"
    assert "8 AM till 10 AM" in rows[JAN].error


def test_report_lists_cross_post_duplicates(home):
    html = read_post(JAN)
    _store(home, JAN)
    conn = db.connect(home.db)
    # The same post under a second slug (VECO re-publishing under another URL).
    pipeline.process_post(conn, ref("service-interruption-january-23-2024-1"), html, NOW)
    text = report.format_table(report.build(conn), report.duplicates(conn))
    conn.close()
    section = text.split("Cross-post duplicates")[1]
    assert section.count("service-interruption-january-23-2024-1") == 5
    assert "2024-01-23T08:00:00+08:00" in section


def test_report_prints_on_cp1252_console(home, monkeypatch):
    # The failure message quotes the post's text; "→" has no cp1252 encoding.
    html = read_post(JAN).replace("8:00 AM to 10:00 AM (2hrs)", "8:00 AM → 10:00 AM Sto. Niño", 1)
    _store(home, JAN, html={JAN: html})
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["report"]) == 0
    out.flush()
    assert "8:00 AM ? 10:00 AM Sto. Niño".encode("cp1252") in out.buffer.getvalue()
    written = list(home.reports.glob("report-*.txt"))
    assert len(written) == 1
    assert "8:00 AM → 10:00 AM Sto. Niño" in written[0].read_text(encoding="utf-8")


def test_cli_backfill_exit_codes(home, monkeypatch):
    good, bad = ref(JAN, "2024-01-22"), ref("service-interruption-bad", "2024-01-23")
    pages = {SITEMAP_URL: sitemap(good), good.url: read_post(JAN)}
    monkeypatch.setattr(cli, "make_fetcher", lambda: Site(pages).fetcher())
    assert cli.main(["backfill"]) == 0

    broken = read_post(JAN).replace("9:00 AM to 5:00 PM (8hrs)", "nine to five", 1)
    pages = {SITEMAP_URL: sitemap(good, bad), good.url: read_post(JAN), bad.url: broken}
    monkeypatch.setattr(cli, "make_fetcher", lambda: Site(pages).fetcher())
    assert cli.main(["backfill"]) == 1
    assert (home.logs / "outages.log").exists()


def test_cli_check_and_reparse(home, monkeypatch):
    nov = ref(NOV)
    pages = {SITEMAP_URL: sitemap(nov), nov.url: read_post(NOV)}
    monkeypatch.setattr(cli, "make_fetcher", lambda: Site(pages).fetcher())
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    assert cli.main(["check"]) == 0
    assert cli.main(["reparse"]) == 0


def test_serve_catch_up(home, monkeypatch):
    conn = db.connect(home.db)
    db.set_meta(conn, "last_check_at", (NOW - timedelta(hours=13)).isoformat())
    conn.commit()
    conn.close()
    calls: list[str] = []
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    monkeypatch.setattr(cli, "_run_check", lambda paths: calls.append("check"))
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    assert cli.main(["serve"]) == 0
    assert calls == ["check", "start"]


def test_serve_skips_catch_up_when_recent(home, monkeypatch):
    conn = db.connect(home.db)
    db.set_meta(conn, "last_check_at", (NOW - timedelta(hours=2)).isoformat())
    conn.commit()
    conn.close()
    calls: list[str] = []
    monkeypatch.setattr(cli, "utcnow", lambda: NOW)
    monkeypatch.setattr(cli, "_run_check", lambda paths: calls.append("check"))
    monkeypatch.setattr(scheduler.BlockingScheduler, "start", lambda self: calls.append("start"))
    assert cli.main(["serve"]) == 0
    assert calls == ["start"]


def test_scheduler_jobs_at_run_times():
    sched = scheduler.build(lambda: None)
    triggers = sorted(str(j.trigger) for j in sched.get_jobs())
    assert triggers == [
        "cron[hour='20', minute='0']",
        "cron[hour='6', minute='0']",
    ]
    assert all(str(j.trigger.timezone) == "Asia/Manila" for j in sched.get_jobs())


def test_needs_catch_up():
    now = datetime(2026, 11, 10, tzinfo=UTC)
    assert scheduler.needs_catch_up(None, now)
    assert scheduler.needs_catch_up((now - timedelta(hours=12, minutes=1)).isoformat(), now)
    assert not scheduler.needs_catch_up((now - timedelta(hours=11)).isoformat(), now)


def test_report_alerts_line(home):
    from outages.alerts import store

    conn = db.connect(home.db)
    s = store.upsert_subscriber(conn, 42, None, datetime(2026, 11, 1, tzinfo=UTC))
    store.subscribe(conn, s.id, "730600041", datetime(2026, 11, 1, tzinfo=UTC))
    conn.commit()
    _store(home, NOV)
    conn.execute("UPDATE outages SET areas_hash = NULL")
    conn.commit()
    now = datetime(2026, 11, 7, tzinfo=UTC)
    upcoming = conn.execute(
        "SELECT count(*) FROM outages WHERE status IN ('scheduled', 'additional')"
    ).fetchone()[0]
    assert report.alerts_line(conn, now) == (
        f"alerts: 1 subscribers, 0 sent; unparsed areas: {upcoming} upcoming outages"
    )
    conn.close()
