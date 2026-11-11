"""Command line: `python -m outages <command>` (Phase 1 spec 7, Phase 2 spec 6)."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from outages import db, gazetteer_build, pipeline, report, scheduler
from outages.config import BACKFILL_DELAY_S, Paths
from outages.fetch import Fetcher

COMMANDS = {
    "backfill": "fetch and store every interruption post not stored yet (resumable)",
    "check": "fetch the sitemap and any new, edited or current-week posts",
    "reparse": "re-run parse + upsert over stored versions (no network)",
    "report": "print per-post outage counts, warnings, failures and duplicates",
    "serve": "run checks at 07:00 and 17:00 Asia/Manila (catch-up on start)",
    "gazetteer": "download the pinned PSGC gazetteer and write barangays.csv (GitHub only)",
}

log = logging.getLogger("outages")


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_fetcher() -> Fetcher:
    return Fetcher(delay_s=BACKFILL_DELAY_S)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="outages", description="Cebu Brownout Tracker")
    sub = p.add_subparsers(dest="command", required=True, metavar="command")
    parsers = {
        name: sub.add_parser(name, help=help_text, description=help_text)
        for name, help_text in COMMANDS.items()
    }
    _add_args(parsers)
    return p


def _add_args(parsers: dict[str, argparse.ArgumentParser]) -> None:
    """Per-command options (Phase 2)."""


def _gazetteer(paths: Paths) -> int:
    counts = gazetteer_build.run(paths.gazetteer)
    for town, n in counts.items():
        print(f"{town} {n}")
    print(f"total {sum(counts.values())}; written: {gazetteer_build.CSV_PATH}")
    return 0


def _setup_logging(paths: Paths) -> list[logging.Handler]:
    paths.logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    file = RotatingFileHandler(
        paths.logs / "outages.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file.setFormatter(fmt)
    handlers: list[logging.Handler] = [console, file]
    for h in handlers:
        log.addHandler(h)
    log.setLevel(logging.INFO)
    return handlers


def _summary(name: str, s: pipeline.CheckSummary) -> int:
    print(
        f"{name}: fetched {s.fetched}, new {s.new}, changed {s.changed}, "
        f"failed {s.failed}, fetch errors {s.errors}"
    )
    return 1 if s.failed or s.errors else 0


def _run_check(paths: Paths) -> None:
    """One scheduled check; opens its own connection (APScheduler runs jobs on a worker thread)."""
    conn = db.connect(paths.db)
    fetcher = make_fetcher()
    try:
        _summary("check", pipeline.check(conn, fetcher, utcnow()))
    except Exception:
        log.exception("check failed")
    finally:
        fetcher.close()
        conn.close()


def _report(paths: Paths, conn) -> int:
    text = report.format_table(report.build(conn), report.duplicates(conn))
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"report-{utcnow():%Y-%m-%d}.txt"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    print(f"written: {out}")
    return 0


def _run(command: str, paths: Paths) -> int:
    if command == "gazetteer":
        return _gazetteer(paths)
    conn = db.connect(paths.db)
    try:
        if command == "report":
            return _report(paths, conn)
        if command == "reparse":
            return _summary("reparse", pipeline.reparse(conn, utcnow()))
        if command == "serve":
            last = db.get_meta(conn, "last_check_at")
            conn.close()
            log.info("serve: checks at 07:00 and 17:00 Asia/Manila; Ctrl+C to stop")
            if scheduler.needs_catch_up(last, utcnow()):
                _run_check(paths)
            with contextlib.suppress(KeyboardInterrupt, SystemExit):  # Ctrl+C stops serve
                scheduler.build(lambda: _run_check(paths)).start()
            return 0
        fetcher = make_fetcher()
        try:
            run = pipeline.backfill if command == "backfill" else pipeline.check
            return _summary(command, run(conn, fetcher, utcnow()))
        finally:
            fetcher.close()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Run one command; return 0 ok, 1 failures, 2 usage."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = _parser().parse_args(argv)
    paths = Paths.default()
    handlers = _setup_logging(paths)
    try:
        return _run(args.command, paths)
    except KeyboardInterrupt:
        print("error: interrupted; rerun the same command to resume", file=sys.stderr)
        return 1
    finally:
        for h in handlers:
            log.removeHandler(h)
            h.close()


if __name__ == "__main__":
    sys.exit(main())
