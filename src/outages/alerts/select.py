"""Which alert items are due (Phase 3 spec 4.1-4.3).

`due_items` is pure: a `Snapshot` and `now` in, `AlertItem`s out. `load` is the thin reader that
builds the snapshot from the DB. Due times are computed in Manila time, never the machine's zone.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Literal

from outages.areas.gazetteer import Gazetteer
from outages.areas.segment import segment
from outages.timeparse import MANILA

ALERT_MATCHES = frozenset({"exact", "alias", "ambiguous"})
REMINDER_STATUSES = frozenset({"scheduled", "additional"})
DAY_BEFORE_AT = time(20, 0)
DAY_OF_AT = time(6, 0)

Kind = Literal["day_before", "day_of", "change"]


@dataclass(frozen=True)
class Sub:
    subscriber_id: int
    chat_id: int
    psgc_code: str
    created_at: datetime


@dataclass(frozen=True)
class Outage:
    id: int
    post_url: str
    start_at: datetime
    end_at: datetime
    status: str
    purpose: str
    areas_hash: str | None
    post_failed: bool


@dataclass(frozen=True)
class Row:
    psgc_code: str | None
    name_raw: str
    town_psgc: str | None
    match: str


@dataclass(frozen=True)
class Change:
    id: int
    outage_id: int
    kind: str
    old: dict | None
    new: dict | None
    detected_at: datetime
    old_codes: frozenset[str]


@dataclass(frozen=True)
class Snapshot:
    subs: list[Sub] = field(default_factory=list)
    outages: dict[int, Outage] = field(default_factory=dict)
    rows: dict[str, list[Row]] = field(default_factory=dict)
    entities: dict[str, list[str]] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    sent: frozenset[tuple[int, int, str, int]] = frozenset()
    unparsed: frozenset[str] = frozenset()  # loaded outages' hashes with no `area_parses` row


@dataclass(frozen=True)
class AlertItem:
    subscriber_id: int
    chat_id: int
    psgc_code: str
    outage_id: int
    kind: Kind
    change_id: int | None = None
    change_kind: str | None = None
    direction: Literal["in", "out"] | None = None

    @property
    def key(self) -> tuple[int, int, str, int]:
        """The `alerts_sent` identity of this item."""
        return (self.subscriber_id, self.outage_id, self.kind, self.change_id or 0)


def concerns(snap: Snapshot, outage: Outage, psgc_code: str) -> bool:
    """True when the outage's current areas name `psgc_code` with an alerting match."""
    if outage.areas_hash is None:
        return False
    return any(
        r.psgc_code == psgc_code and r.match in ALERT_MATCHES
        for r in snap.rows.get(outage.areas_hash, ())
    )


def _at(day, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=MANILA)


def reminder_kind(
    outage: Outage, now: datetime, sent: frozenset, subscriber_id: int
) -> Kind | None:
    """The reminder due now for one outage, or None.

    `day_of` (06:00 on the start date, only for starts at or after 06:00) supersedes an unsent
    `day_before` (20:00 the day before); once `day_of` is due nothing falls back to `day_before`.
    """
    if outage.status not in REMINDER_STATUSES or outage.start_at <= now:
        return None
    start = outage.start_at.astimezone(MANILA)
    day_of = (
        _at(start.date(), DAY_OF_AT) if start.timetz().replace(tzinfo=None) >= DAY_OF_AT else None
    )
    if day_of is not None and now >= day_of:
        kind: Kind = "day_of"
    elif now >= _at(start.date() - timedelta(days=1), DAY_BEFORE_AT):
        kind = "day_before"
    else:
        return None
    return None if (subscriber_id, outage.id, kind, 0) in sent else kind


def _end(c: Change, o: Outage) -> datetime:
    ends = [o.end_at]
    for side in (c.old, c.new):
        if side and side.get("end_at"):
            ends.append(datetime.fromisoformat(side["end_at"]))
    return max(ends)


def _change_item(snap: Snapshot, sub: Sub, c: Change, now: datetime) -> AlertItem | None:
    o = snap.outages.get(c.outage_id)
    if o is None or c.detected_at < sub.created_at or _end(c, o) <= now:
        return None
    if (sub.subscriber_id, o.id, "change", c.id) in snap.sent:
        return None
    now_in = concerns(snap, o, sub.psgc_code)
    direction: Literal["in", "out"] | None = None
    was_in = sub.psgc_code in c.old_codes
    if c.kind == "removed":
        # `areas` deletes a removed outage's parse rows: the old text's codes decide.
        if not (now_in or was_in):
            return None
    elif c.kind == "areas_changed":
        if o.areas_hash is None or o.areas_hash in snap.unparsed:
            return None  # new text not parsed yet: "dropped" would be a guess; wait

        if not (now_in or was_in):
            return None
        if now_in != was_in:
            direction = "in" if now_in else "out"
    elif not now_in:
        return None
    return AlertItem(
        sub.subscriber_id, sub.chat_id, sub.psgc_code, o.id, "change", c.id, c.kind, direction
    )


def due_items(snap: Snapshot, now: datetime) -> list[AlertItem]:
    """Every item due at `now`, not yet sent, deduped per subscriber, ordered by start time."""
    found: dict[tuple[int, int, str, int], AlertItem] = {}
    for sub in snap.subs:
        for o in snap.outages.values():
            if not concerns(snap, o, sub.psgc_code):
                continue
            kind = reminder_kind(o, now, snap.sent, sub.subscriber_id)
            if kind is not None:
                item = AlertItem(sub.subscriber_id, sub.chat_id, sub.psgc_code, o.id, kind)
                found.setdefault(item.key, item)
        for c in snap.changes:
            item = _change_item(snap, sub, c, now)
            if item is not None:
                found.setdefault(item.key, item)
    order = {"change": 0, "day_of": 1, "day_before": 2}
    return sorted(
        found.values(),
        key=lambda i: (
            snap.outages[i.outage_id].start_at,
            i.outage_id,
            i.subscriber_id,
            order[i.kind],
            i.change_id or 0,
        ),
    )


# --- reading the DB --------------------------------------------------------------------------


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _old_codes(gaz: Gazetteer, old: dict | None) -> frozenset[str]:
    """Barangays of an `areas_changed` change's old text, resolved now (its rows may be gone)."""
    if not old or not old.get("areas_raw"):
        return frozenset()
    matches, _ = gaz.resolve(segment(old["areas_raw"]))
    return frozenset(m.psgc_code for m in matches if m.psgc_code and m.match in ALERT_MATCHES)


def _in(n: int) -> str:
    return ",".join("?" * n)


def load_outages(conn: sqlite3.Connection, now: datetime) -> tuple[dict[int, Outage], dict, dict]:
    """Outages ending on or after `now - 1 day`, with their barangay rows and entity names."""
    since = (now - timedelta(days=1)).astimezone(MANILA).isoformat()
    outages = {
        r[0]: Outage(
            id=r[0],
            post_url=r[1],
            start_at=datetime.fromisoformat(r[2]),
            end_at=datetime.fromisoformat(r[3]),
            status=r[4],
            purpose=r[5],
            areas_hash=r[6],
            post_failed=r[7] == "failed",
        )
        for r in conn.execute(
            "SELECT o.id, p.url, o.start_at, o.end_at, o.status, o.purpose, o.areas_hash,"
            " p.parse_status FROM outages o JOIN posts p ON p.id = o.post_id"
            " WHERE o.end_at >= ? ORDER BY o.start_at, o.id",
            (since,),
        )
    }
    hashes = sorted({o.areas_hash for o in outages.values() if o.areas_hash})
    rows: dict[str, list[Row]] = {}
    entities: dict[str, list[str]] = {}
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        for r in conn.execute(
            "SELECT areas_hash, psgc_code, name_raw, town_psgc, match FROM area_barangays"
            f" WHERE areas_hash IN ({_in(len(chunk))}) ORDER BY areas_hash, seq",
            chunk,
        ):
            rows.setdefault(r[0], []).append(Row(r[1], r[2], r[3], r[4]))
        for r in conn.execute(
            "SELECT areas_hash, name FROM area_entities"
            f" WHERE areas_hash IN ({_in(len(chunk))}) ORDER BY areas_hash, seq",
            chunk,
        ):
            entities.setdefault(r[0], []).append(r[1])
    return outages, rows, entities


def load(conn: sqlite3.Connection, now: datetime, gaz: Gazetteer) -> Snapshot:
    """Everything `due_items` needs; an empty snapshot before migration 003."""
    if not _has_table(conn, "subscribers"):
        return Snapshot()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(subscribers)")}
    # Phase 5: only approved chats get alerts (a schema-3 DB has no status: all were allowed).
    approved = " AND s.status = 'approved'" if "status" in cols else ""
    subs = [
        Sub(r[0], r[1], r[2], datetime.fromisoformat(r[3]))
        for r in conn.execute(
            "SELECT s.id, s.chat_id, x.psgc_code, x.created_at FROM subscribers s"
            " JOIN subscriptions x ON x.subscriber_id = s.id WHERE s.active = 1"
            f"{approved}"
            " ORDER BY s.id, x.created_at, x.psgc_code"
        )
    ]
    outages, rows, entities = load_outages(conn, now)
    changes = []
    ids = list(outages)
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        for r in conn.execute(
            "SELECT id, outage_id, kind, old_json, new_json, detected_at FROM outage_changes"
            f" WHERE outage_id IN ({_in(len(chunk))}) ORDER BY id",
            chunk,
        ):
            old = json.loads(r[3]) if r[3] else None
            new = json.loads(r[4]) if r[4] else None
            needs_old = r[2] in ("areas_changed", "removed")
            codes = _old_codes(gaz, old) if needs_old else frozenset()
            changes.append(Change(r[0], r[1], r[2], old, new, datetime.fromisoformat(r[5]), codes))
    sent = frozenset(
        (r[0], r[1], r[2], r[3] or 0)
        for r in conn.execute("SELECT subscriber_id, outage_id, kind, change_id FROM alerts_sent")
    )
    hashes = sorted({o.areas_hash for o in outages.values() if o.areas_hash})
    parsed: set[str] = set()
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        parsed |= {
            r[0]
            for r in conn.execute(
                f"SELECT areas_hash FROM area_parses WHERE areas_hash IN ({_in(len(chunk))})",
                chunk,
            )
        }
    unparsed = frozenset(set(hashes) - parsed)
    return Snapshot(subs, outages, rows, entities, changes, sent, unparsed)
