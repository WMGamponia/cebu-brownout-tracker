"""One alert run: select, render, send, record; then admin notes (Phase 3 spec 4.3, 5.6).

Items are written to `alerts_sent` only after Telegram accepted every part of the message, so
a failed send is retried by the next run (a crash between send and record can duplicate one
message, never drop one). `alert_run` never fetches from VECO.
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from outages import db
from outages.alerts import store
from outages.alerts.render import e, render, when
from outages.alerts.select import (
    REMINDER_STATUSES,
    AlertItem,
    Outage,
    Snapshot,
    due_items,
    load,
)
from outages.areas.gazetteer import Gazetteer
from outages.notify import Notifier, PrintNotifier, SendError
from outages.timeparse import MANILA

log = logging.getLogger("outages.alerts")

PARSE_FAILED_WINDOW = timedelta(days=2)


@dataclass(frozen=True)
class RunSummary:
    subscribers: int  # active subscribers with at least one subscription
    sent: int  # subscribers whose message went out in full
    items: int  # ledger rows written
    failed: int  # subscribers whose send failed (their items stay unrecorded)
    notes: int  # admin notes delivered


@dataclass(frozen=True)
class Note:
    """One admin message; `keys` go to `admin_notes_sent` once it is delivered."""

    keys: tuple[str, ...]
    text: str


def _hour(now: datetime) -> str:
    return f"{now.astimezone(MANILA):%Y-%m-%d %H}"


def _in(n: int) -> str:
    return ",".join("?" * n)


def _upcoming(snap: Snapshot, now: datetime) -> list[Outage]:
    return [o for o in snap.outages.values() if o.status in REMINDER_STATUSES and o.start_at > now]


def _parse_failed_notes(conn: sqlite3.Connection, snap: Snapshot, now: datetime) -> list[Note]:
    soon = [
        o
        for o in snap.outages.values()
        if o.post_failed
        and o.status in REMINDER_STATUSES
        and o.end_at > now
        and o.start_at <= now + PARSE_FAILED_WINDOW
    ]
    urls = sorted({o.post_url for o in soon})
    notes = []
    for url in urls:
        row = conn.execute(
            "SELECT p.id, p.slug, p.parse_error, max(v.id) FROM posts p"
            " JOIN post_versions v ON v.post_id = p.id WHERE p.url = ? GROUP BY p.id",
            (url,),
        ).fetchone()
        if row is None:
            continue
        n = sum(o.post_url == url for o in soon)
        notes.append(
            Note(
                (f"parse_failed:{row[0]}:{row[3]}",),
                f"Post {e(row[1])} failed to parse ({e(row[2] or 'no error text')}); "
                f"{n} outage(s) in the next 2 days alert from the earlier version with a caveat. "
                f"{e(url)}",
            )
        )
    return notes


def _unparsed_note(conn: sqlite3.Connection, snap: Snapshot, now: datetime) -> list[Note]:
    ups = _upcoming(snap, now)
    hashes = sorted({o.areas_hash for o in ups if o.areas_hash})
    parsed: set[str] = set()
    if hashes:
        parsed = {
            r[0]
            for r in conn.execute(
                f"SELECT areas_hash FROM area_parses WHERE areas_hash IN ({_in(len(hashes))})",
                hashes,
            )
        }
    missing = [o for o in ups if not o.areas_hash or o.areas_hash not in parsed]
    if not missing:
        return []
    keys = tuple(sorted({f"unparsed:{o.areas_hash or f'outage-{o.id}'}" for o in missing}))
    return [
        Note(
            keys,
            f"{len(missing)} upcoming outages have unparsed areas; run `python -m outages areas`",
        )
    ]


def _town_list(towns: Iterable[str]) -> str:
    ts = list(dict.fromkeys(towns))
    return ", ".join(ts) if ts else "no town"


def _match_notes(conn: sqlite3.Connection, snap: Snapshot, now: datetime, gaz) -> list[Note]:
    first: dict[str, Outage] = {}
    for o in sorted(_upcoming(snap, now), key=lambda o: o.start_at):
        if o.areas_hash and o.areas_hash not in first:
            first[o.areas_hash] = o
    if not first:
        return []
    hashes = sorted(first)
    rows = conn.execute(
        "SELECT b.areas_hash, b.seq, b.name_raw, b.psgc_code, b.match, p.towns_json"
        " FROM area_barangays b JOIN area_parses p ON p.areas_hash = b.areas_hash"
        f" WHERE b.areas_hash IN ({_in(len(hashes))}) AND b.match IN ('other_town', 'unmatched')"
        " ORDER BY b.areas_hash, b.seq",
        hashes,
    ).fetchall()
    groups: dict[tuple[str, str, str], list] = {}
    for r in rows:
        groups.setdefault((r[0], r[2], r[4]), []).append(r)
    notes = []
    for (h, name, match), rs in groups.items():
        o = first[h]
        listed = _town_list(json.loads(rs[0][5]))
        key = f"{match}:{h}:{rs[0][1]}"
        where = f"in the outage on {when(o.start_at, o.end_at)}"
        if match == "other_town":
            towns = _town_list(
                p.town for p in (gaz.place(r[3]) for r in rs if r[3]) if p is not None
            )
            text = (
                f"`{e(name)}` {where} is listed under {e(listed)} but only exists in {e(towns)}; "
                "no one is alerted for it. Fix: a town-scoped alias in `aliases.csv`, then "
                "`python -m outages areas --rebuild`."
            )
        else:
            text = (
                f"`{e(name)}` {where} (listed under {e(listed)}) matched no barangay; no one is "
                "alerted for it. Fix: an alias in `aliases.csv`, then "
                "`python -m outages areas --rebuild`."
            )
        notes.append(Note((key,), text + f" {e(o.post_url)}"))
    return notes


def admin_notes(
    conn: sqlite3.Connection,
    snap: Snapshot,
    now: datetime,
    check_ok: bool,
    gaz: Gazetteer | None = None,
) -> list[Note]:
    """Operational notes for the admin chat whose keys were never sent (spec 5.6)."""
    from outages.areas.gazetteer import load as load_gazetteer

    gaz = gaz or load_gazetteer()
    notes: list[Note] = []
    if not check_ok:
        last = db.get_meta(conn, "last_check_at") or "never"
        notes.append(
            Note(
                (f"check_failed:{_hour(now)}",),
                f"VECO not reachable; alerts from data as of {last}",
            )
        )
    notes += _parse_failed_notes(conn, snap, now)
    notes += _unparsed_note(conn, snap, now)
    notes += _match_notes(conn, snap, now, gaz)
    return [n for n in notes if not all(store.note_sent(conn, k) for k in n.keys)]


def _send_all(notifier: Notifier, chat_id: int, parts: list[str]) -> str:
    """Send every part; 'plain' if any part fell back to plain text. Raises SendError."""
    how = "html"
    for p in parts:
        if notifier.send(chat_id, p) == "plain":
            how = "plain"
    return how


def _record(conn: sqlite3.Connection, items: list[AlertItem], now: datetime) -> None:
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO alerts_sent (subscriber_id, outage_id, kind, change_id,"
            " sent_at) VALUES (?, ?, ?, ?, ?)",
            [(i.subscriber_id, i.outage_id, i.kind, i.change_id, store.ts(now)) for i in items],
        )


def _by_subscriber(items: list[AlertItem]) -> dict[tuple[int, int], list[AlertItem]]:
    out: dict[tuple[int, int], list[AlertItem]] = {}
    for i in items:
        out.setdefault((i.subscriber_id, i.chat_id), []).append(i)
    return out


def alert_run(
    conn: sqlite3.Connection,
    notifier: Notifier,
    now: datetime,
    gaz: Gazetteer,
    admin_chat_id: int | None,
    check_ok: bool,
) -> RunSummary:
    """Send each active subscriber one message with what is due; then the admin notes."""
    snap = load(conn, now, gaz)
    groups = _by_subscriber(due_items(snap, now))
    sent = recorded = failed = 0
    run_notes: list[Note] = []
    for (sid, chat_id), items in groups.items():
        parts = render(items, snap, gaz, now)
        try:
            how = _send_all(notifier, chat_id, parts)
        except SendError as err:
            failed += 1
            log.warning("alerts: send to subscriber %s failed (%s)", sid, err.kind)
            if err.kind == "blocked":
                with conn:
                    store.set_active(conn, sid, False)
            elif err.kind == "bad_request":
                run_notes.append(
                    Note(
                        (f"bad_request:{sid}:{_hour(now)}",),
                        f"Telegram rejected the alert for subscriber {sid} even as plain text; "
                        "its items stay unsent.",
                    )
                )
            continue
        _record(conn, items, now)
        sent += 1
        recorded += len(items)
        if how == "plain":
            run_notes.append(
                Note(
                    (f"bad_request:{sid}:{_hour(now)}",),
                    f"Telegram rejected the HTML alert for subscriber {sid}; "
                    "it was resent as plain text.",
                )
            )
    notes = _deliver_notes(
        conn, notifier, admin_chat_id, run_notes + admin_notes(conn, snap, now, check_ok, gaz), now
    )
    subscribers = len({s.subscriber_id for s in snap.subs})
    log.info(
        "alerts: %d subscribers, sent %d, items %d, failed %d, notes %d",
        subscribers,
        sent,
        recorded,
        failed,
        notes,
    )
    return RunSummary(subscribers, sent, recorded, failed, notes)


def _deliver_notes(
    conn: sqlite3.Connection,
    notifier: Notifier,
    admin_chat_id: int | None,
    notes: list[Note],
    now: datetime,
) -> int:
    done = 0
    for n in notes:
        if admin_chat_id is None:
            log.warning("admin note (no TELEGRAM_ADMIN_CHAT_ID): %s", n.text)
            continue
        try:
            notifier.send(admin_chat_id, n.text)
        except SendError as err:
            log.warning("admin note not delivered (%s): %s", err.kind, n.text)
            continue
        with conn:
            for k in n.keys:
                store.mark_note(conn, k, now)
        done += 1
    return done


def dry_run(conn: sqlite3.Connection, now: datetime, gaz: Gazetteer) -> str:
    """What a run would send now, per subscriber, and the admin notes. Reads only."""
    snap = load(conn, now, gaz)
    has = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscribers'"
    ).fetchone()
    if not has:
        return "no subscribers (migration 003 not applied)\n"
    out = io.StringIO()
    printer = PrintNotifier(out)
    groups = _by_subscriber(due_items(snap, now))
    messages = items = 0
    for (_, chat_id), its in groups.items():
        parts = render(its, snap, gaz, now)
        for p in parts:
            printer.send(chat_id, p)
        messages += len(parts)
        items += len(its)
    notes = admin_notes(conn, snap, now, True, gaz)
    out.write(f"--- admin notes ({len(notes)}) ---\n")
    for n in notes:
        out.write(f"{n.text}\n")
    subscribers = len({s.subscriber_id for s in snap.subs})
    out.write(
        f"\nwould send {messages} message(s) with {items} item(s) to {len(groups)} of "
        f"{subscribers} subscriber(s); admin notes: {len(notes)}\n"
    )
    return out.getvalue()


def unparsed_upcoming(conn: sqlite3.Connection, now: datetime) -> int:
    """Upcoming scheduled/additional outages with no parsed areas (for `report`)."""
    return conn.execute(
        "SELECT count(*) FROM outages o WHERE o.status IN ('scheduled', 'additional')"
        " AND o.start_at > ? AND (o.areas_hash IS NULL OR NOT EXISTS"
        " (SELECT 1 FROM area_parses p WHERE p.areas_hash = o.areas_hash))",
        (now.astimezone(MANILA).isoformat(),),
    ).fetchone()[0]
