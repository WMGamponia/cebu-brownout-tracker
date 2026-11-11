"""Command line: `python -m outages <command>` (Phase 1 spec 7, Phase 2 spec 6)."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from outages import bot, db, evaluate, gazetteer_build, pipeline, report, scheduler
from outages.alerts import run as alerts_run
from outages.areas import store as areas_store
from outages.areas.entities import Extractor
from outages.areas.entities_rules import RulesExtractor
from outages.areas.gazetteer import load as load_gazetteer
from outages.areas.llm import LLMUnavailable, OllamaExtractor
from outages.config import BACKFILL_DELAY_S, ENTITY_EXTRACTOR, Paths, telegram_settings
from outages.fetch import Fetcher
from outages.notify import TelegramNotifier
from outages.telegram import TelegramClient, install_redaction

COMMANDS = {
    "backfill": "fetch and store every interruption post not stored yet (resumable)",
    "check": "fetch the sitemap and any new, edited or current-week posts",
    "reparse": "re-run parse + upsert over stored versions (no network)",
    "report": "print per-post outage counts, warnings, failures and duplicates",
    "serve": "run checks at 07:00 and 17:00 Asia/Manila (catch-up on start)",
    "gazetteer": "download the pinned PSGC gazetteer and write barangays.csv (GitHub only)",
    "areas": "parse area texts into towns, barangays and entities (--dry-run, --review)",
    "eval": "area eval: `sample` the label set, or `run` the arms on a split",
    "alerts": "alert run on stored data: --dry-run prints it, --send sends it (no VECO fetch)",
    "bot": "run the Telegram bot (/start, /add, /list, /remove) until Ctrl+C (no VECO fetch)",
}

log = logging.getLogger("outages")


def utcnow() -> datetime:
    return datetime.now(UTC)


def extractor_from_config(conn) -> Extractor:
    """The default entity extractor, set by the eval decision rule (spec 4.5).

    `conn` holds the LLM cache, so it must be writable for `qwen`."""
    if ENTITY_EXTRACTOR == "rules":
        return RulesExtractor()
    if ENTITY_EXTRACTOR == "qwen":
        return OllamaExtractor(conn)
    raise SystemExit(f"error: unknown ENTITY_EXTRACTOR {ENTITY_EXTRACTOR!r}")


def _areas(paths: Paths, args: argparse.Namespace) -> int:
    gaz = load_gazetteer()
    conn = db.connect_ro(paths.db) if args.dry_run else db.connect(paths.db)
    # A dry run writes no area rows, but an LLM's answers still go to llm_cache (a separate
    # writable connection), so a slow full-corpus dry run is not paid for twice. On a DB that
    # isn't migrated yet the cache is in memory: a dry run never migrates.
    cache = conn
    if args.dry_run and ENTITY_EXTRACTOR != "rules":
        cache = db.connect(paths.db if db.is_current(conn) else Path(":memory:"))
    try:
        if args.review:
            items = areas_store.review_queue(conn, gaz, dry_run=args.dry_run)
            text = areas_store.format_review(items)
            paths.reports.mkdir(parents=True, exist_ok=True)
            out = paths.reports / f"areas-review-{utcnow():%Y-%m-%d}.md"
            out.write_text(text, encoding="utf-8", newline="\n")
            print(text, end="")
            print(f"written: {out}")
            return 0
        try:
            s = areas_store.run_areas(
                conn,
                extractor_from_config(cache),
                gaz,
                utcnow(),
                dry_run=args.dry_run,
                rebuild=args.rebuild,
            )
        except LLMUnavailable as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(areas_store.format_summary(s, args.dry_run), end="")
        return 0
    finally:
        if cache is not conn:
            cache.close()
        conn.close()


def make_fetcher() -> Fetcher:
    return Fetcher(delay_s=BACKFILL_DELAY_S)


def make_telegram(token: str) -> TelegramClient:
    return TelegramClient(token)


def _settings_or_exit():
    """Telegram settings, or None after printing why (never the token)."""
    try:
        settings = telegram_settings()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return None
    if settings is None:
        print("error: TELEGRAM_BOT_TOKEN not set (environment or .env)", file=sys.stderr)
        return None
    install_redaction(settings.token)
    return settings


def _alerts(paths: Paths, args: argparse.Namespace) -> int:
    """`alerts --dry-run` (read-only, no network) or `alerts --send` (Telegram only)."""
    if args.dry_run:
        if not paths.db.is_file():
            print(f"error: no database at {paths.db}", file=sys.stderr)
            return 1
        conn = db.connect_ro(paths.db)
        try:
            print(alerts_run.dry_run(conn, utcnow(), load_gazetteer()), end="")
        finally:
            conn.close()
        return 0
    settings = _settings_or_exit()
    if settings is None:
        return 1
    client = make_telegram(settings.token)
    conn = db.connect(paths.db)
    try:
        s = alerts_run.alert_run(
            conn,
            TelegramNotifier(client),
            utcnow(),
            load_gazetteer(),
            settings.admin_chat_id,
            check_ok=True,
        )
    finally:
        conn.close()
        client.close()
    print(
        f"alerts: {s.subscribers} subscribers, sent {s.sent} (items {s.items}), "
        f"failed {s.failed}, admin notes {s.notes}"
    )
    return 1 if s.failed else 0


def _bot(paths: Paths) -> int:
    """Long-poll the bot in the foreground until Ctrl+C (Telegram only)."""
    settings = _settings_or_exit()
    if settings is None:
        return 1
    client = make_telegram(settings.token)
    log.info("bot: polling for %d allowed chat(s); Ctrl+C to stop", len(settings.allowed))
    try:
        bot.poll_forever(client, paths.db, load_gazetteer(), settings.allowed, threading.Event())
    except KeyboardInterrupt:
        print("bot: stopped")
    finally:
        client.close()
    return 0


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
    a = parsers["areas"]
    a.add_argument("--rebuild", action="store_true", help="re-parse every text")
    a.add_argument("--dry-run", action="store_true", help="parse in memory, write nothing")
    a.add_argument("--review", action="store_true", help="print and save the review queue")
    e = parsers["eval"]
    e.add_argument("action", choices=("sample", "run"))
    e.add_argument("--seed", type=int, default=20261111)
    e.add_argument("--split", choices=("dev", "test"), default="dev")
    e.add_argument("--arms", default="rules")
    al = parsers["alerts"].add_mutually_exclusive_group(required=True)
    al.add_argument("--dry-run", action="store_true", help="print what would be sent; no writes")
    al.add_argument("--send", action="store_true", help="send through Telegram and record")


def _eval_sample(paths: Paths, seed: int) -> int:
    conn = db.connect_ro(paths.db)
    try:
        rows = evaluate.corpus_rows(conn)
    finally:
        conn.close()
    dev, test = evaluate.sample(rows, seed=seed)
    try:
        evaluate.write_sample(evaluate.EVAL_DIR, dev, test)
    except evaluate.EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(evaluate.format_counts(dev, test), end="")
    return 0


def _eval_run(paths: Paths, args: argparse.Namespace) -> int:
    """Score the arms on a split; the LLM cache lives in data/outages.db. No VECO requests."""
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    conn = db.connect(paths.db)
    try:
        out = evaluate.run_eval(args.split, arms, conn, utcnow())
    except (evaluate.EvalError, LLMUnavailable) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(out.read_text(encoding="utf-8"), end="")
    print(f"written: {out}")
    return 0


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
        _summary("check", pipeline.check(conn, fetcher, utcnow(), extractor_from_config(conn)))
    except Exception:
        log.exception("check failed")
    finally:
        fetcher.close()
        conn.close()


def _report(paths: Paths, conn) -> int:
    lines = "\n".join((report.areas_line(conn), report.alerts_line(conn, utcnow())))
    text = report.format_table(report.build(conn), report.duplicates(conn), areas=lines)
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"report-{utcnow():%Y-%m-%d}.txt"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    print(f"written: {out}")
    return 0


def _run(args: argparse.Namespace, paths: Paths) -> int:
    command = args.command
    if command == "gazetteer":
        return _gazetteer(paths)
    if command == "areas":
        return _areas(paths, args)
    if command == "alerts":
        return _alerts(paths, args)
    if command == "bot":
        return _bot(paths)
    if command == "eval":
        if args.action == "sample":
            return _eval_sample(paths, args.seed)
        if args.action == "run":
            return _eval_run(paths, args)
        raise SystemExit(f"error: unknown eval action {args.action!r}")
    if command not in ("report", "reparse", "serve", "check", "backfill"):
        raise SystemExit(f"error: unhandled command {command!r}")  # never fall through to fetch
    conn = db.connect(paths.db)
    try:
        if command == "report":
            return _report(paths, conn)
        if command == "reparse":
            return _summary(
                "reparse", pipeline.reparse(conn, utcnow(), extractor_from_config(conn))
            )
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
            if command == "backfill":
                return _summary(command, pipeline.backfill(conn, fetcher, utcnow()))
            assert command == "check"
            return _summary(
                command, pipeline.check(conn, fetcher, utcnow(), extractor_from_config(conn))
            )
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
        return _run(args, paths)
    except KeyboardInterrupt:
        print("error: interrupted; rerun the same command to resume", file=sys.stderr)
        return 1
    finally:
        for h in handlers:
            log.removeHandler(h)
            h.close()


if __name__ == "__main__":
    sys.exit(main())
