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

from outages import (
    bot,
    db,
    evaluate,
    gazetteer_build,
    lock,
    pipeline,
    report,
    scheduler,
    snapshot,
)
from outages.alerts import run as alerts_run
from outages.alerts import store as alerts_store
from outages.areas import store as areas_store
from outages.areas.entities import Extractor
from outages.areas.entities_rules import RulesExtractor
from outages.areas.gazetteer import load as load_gazetteer
from outages.areas.llm import LLMUnavailable, OllamaExtractor
from outages.config import (
    BACKFILL_DELAY_S,
    ENTITY_EXTRACTOR,
    MAP_HOST,
    MAP_PORT,
    OSM_DIR_NAME,
    OVERPASS_SPACING_S,
    OVERPASS_URL,
    RUN_TIMES,
    STALE_CHECK_H,
    VECO_TOWNS,
    Paths,
    TelegramSettings,
    dotenv_get,
    telegram_settings,
)
from outages.fetch import Fetcher
from outages.history import load as history_load
from outages.history import stats as history_stats
from outages.map import server as map_server
from outages.map.app import NOT_MIGRATED, NOT_READY, create_app
from outages.map.build import BoundariesError, load_boundaries, schema_ready
from outages.map.window import WindowError
from outages.notify import TelegramNotifier
from outages.osm import extract as streets_index
from outages.osm import overpass
from outages.streets import evaluate as streets_eval
from outages.streets import review as streets_review
from outages.streets import store as streets_store
from outages.streets.match import ALIASES_PATH
from outages.telegram import TelegramClient, install_redaction
from outages.timeparse import MANILA

COMMANDS = {
    "backfill": "fetch and store every interruption post not stored yet (resumable)",
    "check": "fetch the sitemap and any new, edited or current-week posts",
    "reparse": "re-run parse + upsert over stored versions (no network)",
    "report": "print per-post outage counts, warnings, failures and duplicates",
    "serve": "run check + alerts at 06:00 and 20:00 Asia/Manila (catch-up on start)",
    "gazetteer": "download the pinned PSGC gazetteer and write barangays.csv (GitHub only)",
    "areas": "parse area texts into towns, barangays and entities (--dry-run, --review)",
    "eval": "area eval: `sample` the label set, or `run` the arms on a split",
    "alerts": "alert run on stored data: --dry-run prints it, --send sends it (no VECO fetch)",
    "bot": "run the Telegram bot (/start, /add, /list, /remove) until Ctrl+C (no VECO fetch)",
    "map": "serve the outage map (read-only; no VECO fetch) until Ctrl+C",
    "stats": "history stats for a date range (read-only; no VECO fetch): --from, --to",
    "streets": "street lines: `fetch` the OSM extract (Overpass, --yes), `match`, --review",
    "snapshot": "static demo snapshot for GitHub Pages (offline, read-only)",
}
LAN_WARNING = "map is reachable from your network; there is no login"
# Commands that write the DB on a schedule or talk to Telegram take the single-instance lock
# (Phase 6 spec 4.4); `alerts` only with --send.
LOCKED_COMMANDS = ("serve", "bot", "check", "backfill")
LOCK_NAME = "serve.lock"

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
        bot.poll_forever(
            client,
            paths.db,
            load_gazetteer(),
            settings.allowed,
            threading.Event(),
            admin_chat_id=settings.admin_chat_id,
        )
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
    e.add_argument("--suite", choices=("areas", "streets"), default="areas")
    e.add_argument("--seed", type=int, default=None, help="areas 20261111, streets 20261112")
    e.add_argument("--split", choices=("dev", "test"), default="dev")
    e.add_argument("--arms", default="rules")
    al = parsers["alerts"].add_mutually_exclusive_group(required=True)
    al.add_argument("--dry-run", action="store_true", help="print what would be sent; no writes")
    al.add_argument("--send", action="store_true", help="send through Telegram and record")
    m = parsers["map"]
    m.add_argument("--host", default=MAP_HOST, help=f"default {MAP_HOST}; 0.0.0.0 = your LAN")
    m.add_argument("--port", type=int, default=MAP_PORT)
    sp = parsers["stats"]
    sp.add_argument("--from", dest="from_", help="YYYY-MM-DD (default: to − 364 days)")
    sp.add_argument("--to", help="YYYY-MM-DD (default: today, Manila)")
    st = parsers["streets"]
    st.add_argument("action", nargs="?", choices=("fetch", "match"))
    st.add_argument("--yes", action="store_true", help="fetch: make the Overpass requests")
    st.add_argument("--towns", help="fetch: only these towns (comma-separated names)")
    st.add_argument("--rebuild", action="store_true", help="match: redo every street")
    st.add_argument("--dry-run", action="store_true", help="match: count only, write nothing")
    st.add_argument("--review", action="store_true", help="unmatched names with suggestions")
    sn = parsers["snapshot"]
    sn.add_argument("--out", required=True, type=Path, help="output folder (e.g. site)")
    sn.add_argument("--allow-old", action="store_true", help="publish data older than 15 h")
    s = parsers["serve"]
    s.add_argument("--no-map", action="store_true", help="don't serve the map")
    s.add_argument("--map-host", default=MAP_HOST, help=f"default {MAP_HOST}; 0.0.0.0 = your LAN")
    s.add_argument("--map-port", type=int, default=MAP_PORT)


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


def _eval_sample_streets(paths: Paths, seed: int) -> int:
    conn = db.connect_ro(paths.db)
    try:
        rows = streets_eval.street_rows(conn)
    finally:
        conn.close()
    dev, test = streets_eval.sample_streets(rows, seed=seed)
    try:
        evaluate.write_sample(streets_eval.EVAL_DIR, dev, test)
    except evaluate.EvalError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"{len(rows)} street mentions; sampled dev {len(dev)}, test {len(test)}")
    print(f"written: {streets_eval.EVAL_DIR}")
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


def _streets_fetch(paths: Paths, args: argparse.Namespace) -> int:
    """Without --yes: print the 8 requests and exit 2, building no client (spec 4.2)."""
    names = list(VECO_TOWNS)
    if args.towns:
        names = [t.strip() for t in args.towns.split(",") if t.strip()]
        unknown = [t for t in names if t not in VECO_TOWNS]
        if unknown:
            print(
                f"error: unknown town {', '.join(unknown)}; one of {', '.join(VECO_TOWNS)}",
                file=sys.stderr,
            )
            return 2
    try:
        boundaries = load_boundaries()
        plan = [
            (name, psgc, overpass.town_bbox(boundaries, psgc))
            for name, psgc in VECO_TOWNS.items()
            if name in names
        ]
    except (BoundariesError, overpass.FetchError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if not args.yes:
        print(f"streets fetch will make {len(plan)} requests to {OVERPASS_URL},")
        print(f"one at a time, {OVERPASS_SPACING_S} s apart (bbox S,W,N,E):")
        for name, psgc, bbox in plan:
            print(f"  {name:<14} {psgc}  {','.join(str(x) for x in bbox)}")
        print("Rerun with --yes to fetch. The extract is ODbL and stays in data/osm/.")
        return 2
    out_dir = paths.data / OSM_DIR_NAME
    client = overpass.make_client()
    try:
        summary = overpass.fetch_all(
            client, boundaries, out_dir, utcnow(), towns=[psgc for _, psgc, _ in plan]
        )
    finally:
        client.close()
    manifest = overpass.load_manifest(out_dir) or {"towns": {}}
    for name, psgc, _ in plan:
        if psgc in summary.ok:
            f = overpass.town_file(out_dir, psgc)
            ways = manifest["towns"][psgc]["ways"]
            print(f"  {name:<14} {ways} ways, {f.stat().st_size:,} bytes")
        else:
            print(f"  {name:<14} FAILED: {summary.failed.get(psgc, '?')}")
    print(f"osm_base {manifest.get('osm_base')}; written: {out_dir}")
    if summary.stopped:
        print("error: stopped after Overpass back-off; rerun later", file=sys.stderr)
    return 0 if not summary.failed else 1


def _streets_match(paths: Paths, args: argparse.Namespace) -> int:
    """Offline. A dry run opens the DB read-only (works before migration 004)."""
    index = streets_index.StreetIndex.load(paths.data / OSM_DIR_NAME)
    if index is None:
        print("warning: no OSM extract (run `streets fetch`); every street is no_osm_data")
    boundaries = load_boundaries()
    conn = db.connect_ro(paths.db) if args.dry_run else db.connect(paths.db)
    try:
        s = streets_store.run_match(
            conn, index, boundaries, rebuild=args.rebuild, dry_run=args.dry_run
        )
    finally:
        conn.close()
    parts = ", ".join(f"{k} {v}" for k, v in s.by_status.items()) or "nothing to match"
    if args.dry_run:
        print(f"dry run (nothing written): {parts}")
    else:
        print(f"written {s.written}: {parts}")
    return 0


def _streets_review(paths: Paths) -> int:
    conn = db.connect_ro(paths.db)
    try:
        if not db.is_current(conn):
            print("error: run `streets match` first (DB not migrated)", file=sys.stderr)
            return 1
        items = streets_review.review(
            conn, streets_index.StreetIndex.load(paths.data / OSM_DIR_NAME), load_boundaries()
        )
    finally:
        conn.close()
    for i in items:
        sugg = "; ".join(i.suggestions) if i.suggestions else "-"
        print(f"{i.count:>4}  {i.name}  ->  {sugg}")
    print(f"{len(items)} not_found names; accepted suggestions go in {ALIASES_PATH} by hand")
    return 0


def _stats(paths: Paths, args: argparse.Namespace) -> int:
    """Read-only; prints the same numbers as /stats.json."""
    now = utcnow()
    try:
        rng = history_stats.parse_range(args.from_, args.to, now)
    except WindowError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    conn = db.connect_ro(paths.db)
    try:
        if not db.is_current(conn):
            print("error: run `streets match` first (DB not migrated)", file=sys.stderr)
            return 1
        rows, coverage = history_load.load_rows(conn, rng)
    finally:
        conn.close()
    doc = history_stats.compute(rows, rng, load_gazetteer(), coverage, now)
    print(history_stats.format_text(doc), end="")
    return 0


def _secrets_for_scan(conn) -> tuple[list[int], str | None]:
    """Every chat ID we know (subscribers, the allowlist, the admin) and the bot token, to scan the
    snapshot's bytes for. Read before the guard is installed, on a connection of their own."""
    ids = [r[0] for r in conn.execute("SELECT chat_id FROM subscribers")]
    settings = telegram_settings()  # ValueError on a malformed chat ID list
    if settings is not None:
        ids += list(settings.allowed)
        if settings.admin_chat_id is not None:
            ids.append(settings.admin_chat_id)
    return ids, dotenv_get("TELEGRAM_BOT_TOKEN")


def _snapshot(paths: Paths, args: argparse.Namespace) -> int:
    """Offline and read-only (Phase 6 spec 4.1); builds no fetcher, Overpass or Telegram client.
    Exit 0 ok; 1 build failure or leak; 2 old data, bad folder or DB not ready."""
    if not paths.db.is_file():
        print(f"error: {NOT_READY}", file=sys.stderr)
        return 2
    conn = db.connect_ro(paths.db)
    try:
        if not schema_ready(conn):
            print(f"error: {NOT_READY}", file=sys.stderr)
            return 2
        if not db.is_current(conn):
            print(f"error: {NOT_MIGRATED}", file=sys.stderr)
            return 2
        last = db.get_meta(conn, "last_check_at")
        try:
            chat_ids, token = _secrets_for_scan(conn)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
    taken = utcnow().astimezone(MANILA).replace(microsecond=0)
    age_h = None if last is None else (taken - datetime.fromisoformat(last)).total_seconds() / 3600
    if not args.allow_old and (age_h is None or age_h > STALE_CHECK_H):
        what = "data has never been checked" if age_h is None else f"data is {int(age_h)} h old"
        print(f"error: {what}; run check first or pass --allow-old", file=sys.stderr)
        return 2
    problem = snapshot.check_out_dir(args.out)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    conn = db.connect_ro(paths.db)
    try:
        conn.execute("PRAGMA busy_timeout=30000")  # wait out a scheduled run's write
        snapshot.guard(conn)
        files = snapshot.build_files(conn, load_gazetteer(), load_boundaries(), taken, last)
    except Exception as e:
        log.exception("snapshot: build failed")
        print(f"error: snapshot not written: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    leak = snapshot.find_leak(files, chat_ids, token)
    if leak:
        print(f"error: snapshot not written: the output contains a {leak}", file=sys.stderr)
        return 1
    written, removed = snapshot.write(args.out, files)
    weeks = sum(1 for k in files if k.startswith("weeks/"))
    ranges = [
        k.removeprefix("stats/").removesuffix(".json") for k in files if k.startswith("stats/")
    ]
    size = sum(len(b) for b in files.values())
    print(f"taken_at {taken.isoformat()}; last_check_at {snapshot.manila_iso(last) or 'never'}")
    print(f"{weeks} weeks written, {removed} old file(s) removed; ranges: {', '.join(ranges)}")
    print(f"{written} files, {size / 1_000_000:.1f} MB; written: {args.out}")
    return 0


def _streets(paths: Paths, args: argparse.Namespace) -> int:
    if args.review:
        return _streets_review(paths)
    if args.action == "fetch":
        return _streets_fetch(paths, args)
    if args.action == "match":
        return _streets_match(paths, args)
    raise SystemExit("error: usage: streets fetch [--yes] | streets match | streets --review")


def _gazetteer(paths: Paths) -> int:
    counts = gazetteer_build.run(paths.gazetteer)
    for town, n in counts.items():
        print(f"{town} {n}")
    print(f"total {sum(counts.values())}; written: {gazetteer_build.CSV_PATH}")
    n = gazetteer_build.boundary_count()
    print(f"boundaries {n}; written: {gazetteer_build.BOUNDARIES_PATH}")
    return 0


def _setup_logging(paths: Paths) -> list[logging.Handler]:
    paths.logs.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = RotatingFileHandler(
        paths.logs / "outages.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file.setFormatter(fmt)
    handlers: list[logging.Handler] = [file]
    if sys.stderr is not None:  # None under pythonw (no console): file log only
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        handlers.insert(0, console)
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


def _check(paths: Paths) -> bool:
    """One check on its own connection; True when VECO answered every request."""
    conn = db.connect(paths.db)
    fetcher = make_fetcher()
    try:
        s = pipeline.check(
            conn, fetcher, utcnow(), extractor_from_config(conn), paths.data / OSM_DIR_NAME
        )
        _summary("check", s)
        return s.errors == 0
    except Exception:
        log.exception("check failed")
        return False
    finally:
        fetcher.close()
        conn.close()


def _scheduled_run(paths: Paths, settings: TelegramSettings | None, do_check: bool = True) -> None:
    """A check, then (with Telegram settings) an alert run on whatever is stored, even after a
    failed check. Opens its own connections: APScheduler runs jobs on a worker thread. Never
    raises into the scheduler."""
    check_ok = _check(paths) if do_check else True
    if settings is None:
        return
    conn = db.connect(paths.db)
    client = make_telegram(settings.token)
    try:
        alerts_run.alert_run(
            conn,
            TelegramNotifier(client),
            utcnow(),
            load_gazetteer(),
            settings.admin_chat_id,
            check_ok,
        )
    except Exception:
        log.exception("alert run failed")
    finally:
        client.close()
        conn.close()


def make_map_app(paths: Paths):
    """The map app over the real DB; raises `BoundariesError` if the boundaries won't load."""
    return create_app(paths.db, load_boundaries(), load_gazetteer(), lambda: utcnow())


def _warn_if_lan(host: str) -> None:
    if not map_server.is_loopback(host):
        log.warning(LAN_WARNING)


def _map(paths: Paths, args: argparse.Namespace) -> int:
    """The map alone, in the foreground. Builds no fetcher and no Telegram client."""
    _warn_if_lan(args.host)
    try:
        app = make_map_app(paths)
    except BoundariesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    try:
        ok = map_server.serve_forever(app, args.host, args.port)
    except KeyboardInterrupt:  # uvicorn re-raises Ctrl+C after its own clean shutdown
        print("map: stopped")
        return 0
    if not ok:
        print(
            f"error: map could not listen on {args.host}:{args.port} (port in use?)",
            file=sys.stderr,
        )
        return 1
    return 0


def _map_failed(paths: Paths, settings: TelegramSettings | None, message: str) -> None:
    """Log a map start failure and tell the admin chat once per Manila day. Never raises."""
    log.error(message)
    if settings is None:
        return
    now = utcnow()
    key = f"map_failed:{now.astimezone(MANILA):%Y-%m-%d}"
    try:
        conn = db.connect(paths.db)
        try:
            if alerts_store.note_sent(conn, key):
                return
            client = make_telegram(settings.token)
            try:
                note = alerts_run.Note((key,), f"{message}; alerts keep running.")
                alerts_run.deliver_notes(
                    conn, TelegramNotifier(client), settings.admin_chat_id, [note], now
                )
            finally:
                client.close()
        finally:
            conn.close()
    except Exception:
        log.exception("map: could not report the failure")


def _start_map(paths: Paths, args: argparse.Namespace, settings: TelegramSettings | None) -> None:
    """The map thread inside `serve`; a failure is reported and never stops the alert runs."""
    if args.no_map:
        return
    _warn_if_lan(args.map_host)
    try:
        app = make_map_app(paths)
    except Exception as e:  # spec 6: nothing about the map may stop the alert runs
        _map_failed(paths, settings, f"map: not started: {e}")
        return
    map_server.start_thread(
        app, args.map_host, args.map_port, lambda msg: _map_failed(paths, settings, msg)
    )


def _serve(paths: Paths, last_check_at: str | None, args: argparse.Namespace) -> int:
    """Catch-up, the bot and map threads, then runs at RUN_TIMES until Ctrl+C."""
    try:
        settings = telegram_settings()
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    stop = threading.Event()
    if settings is None:
        log.info("serve: alerts disabled (no TELEGRAM_BOT_TOKEN); checks only")
    else:
        install_redaction(settings.token)
        threading.Thread(
            target=bot.poll_forever,
            args=(
                make_telegram(settings.token),
                paths.db,
                load_gazetteer(),
                settings.allowed,
                stop,
            ),
            kwargs={"admin_chat_id": settings.admin_chat_id},
            name="telegram-bot",
            daemon=True,
        ).start()
    _start_map(paths, args, settings)
    log.info("serve: check + alerts at %s Asia/Manila; Ctrl+C to stop", " and ".join(RUN_TIMES))
    catch_up = scheduler.needs_catch_up(last_check_at, utcnow())
    if catch_up or settings is not None:
        _scheduled_run(paths, settings, do_check=catch_up)
    try:
        with contextlib.suppress(KeyboardInterrupt, SystemExit):  # Ctrl+C stops serve
            scheduler.build(lambda: _scheduled_run(paths, settings)).start()
    finally:
        stop.set()
    return 0


def _report(paths: Paths, conn) -> int:
    lines = "\n".join(
        (
            report.areas_line(conn),
            report.streets_line(conn),
            report.alerts_line(conn, utcnow()),
        )
    )
    text = report.format_table(report.build(conn), report.duplicates(conn), areas=lines)
    paths.reports.mkdir(parents=True, exist_ok=True)
    out = paths.reports / f"report-{utcnow():%Y-%m-%d}.txt"
    out.write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    print(f"written: {out}")
    return 0


def needs_lock(args: argparse.Namespace) -> bool:
    return args.command in LOCKED_COMMANDS or (
        args.command == "alerts" and getattr(args, "send", False)
    )


def _run(args: argparse.Namespace, paths: Paths) -> int:
    if not needs_lock(args):
        return _dispatch(args, paths)
    paths.data.mkdir(parents=True, exist_ok=True)
    try:
        held = lock.acquire(paths.data / LOCK_NAME)
    except lock.LockHeld as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    with held:
        return _dispatch(args, paths)


def _dispatch(args: argparse.Namespace, paths: Paths) -> int:
    command = args.command
    if command == "gazetteer":
        return _gazetteer(paths)
    if command == "areas":
        return _areas(paths, args)
    if command == "alerts":
        return _alerts(paths, args)
    if command == "bot":
        return _bot(paths)
    if command == "map":
        return _map(paths, args)
    if command == "streets":
        return _streets(paths, args)
    if command == "stats":
        return _stats(paths, args)
    if command == "snapshot":
        return _snapshot(paths, args)
    if command == "eval":
        if args.suite == "streets":
            if args.action == "sample":
                return _eval_sample_streets(paths, args.seed or streets_eval.SEED)
            assert args.action == "run"
            try:
                out, _ = streets_eval.run_streets(
                    args.split,
                    streets_eval.EVAL_DIR,
                    paths.data / OSM_DIR_NAME,
                    load_boundaries(),
                    utcnow(),
                )
            except (evaluate.EvalError, BoundariesError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 1
            print(out.read_text(encoding="utf-8"), end="")
            print(f"written: {out}")
            return 0
        if args.action == "sample":
            return _eval_sample(paths, args.seed or 20261111)
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
                "reparse",
                pipeline.reparse(
                    conn, utcnow(), extractor_from_config(conn), paths.data / OSM_DIR_NAME
                ),
            )
        if command == "serve":
            last = db.get_meta(conn, "last_check_at")
            conn.close()
            return _serve(paths, last, args)
        fetcher = make_fetcher()
        try:
            if command == "backfill":
                return _summary(command, pipeline.backfill(conn, fetcher, utcnow()))
            assert command == "check"
            return _summary(
                command,
                pipeline.check(
                    conn, fetcher, utcnow(), extractor_from_config(conn), paths.data / OSM_DIR_NAME
                ),
            )
        finally:
            fetcher.close()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """Run one command; return 0 ok, 1 failures, 2 usage, 3 another instance holds the lock."""
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
