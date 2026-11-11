"""Alert items -> Telegram HTML messages (Phase 3 spec 4.4, 5.5). Pure.

One subscriber's items become one message in sections `Changes`, `Today`, `Tomorrow`; an outage
appears once (under `Changes` when it has a change item). Text goes through `html.escape`; a
message longer than Telegram's 4,096 characters is split between outage blocks.
"""

from __future__ import annotations

import html
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from outages.alerts.select import (
    REMINDER_STATUSES,
    AlertItem,
    Change,
    Outage,
    Snapshot,
    concerns,
)
from outages.areas.gazetteer import Gazetteer
from outages.timeparse import MANILA

MAX_LEN = 4096
HEADER = "<b>Scheduled outages that may affect you</b>"
MAX_ENTITIES = 5
MAX_PURPOSE = 300
UPCOMING_DAYS = 7
CAVEAT = "VECO updated this post and we couldn't read the update; this is from the earlier version."


def e(text: str) -> str:
    """Escape text-node content (`&`, `<`, `>`); apostrophes stay readable."""
    return html.escape(text, quote=False)


def _clock(dt: datetime) -> str:
    return f"{dt:%I}".lstrip("0") + f":{dt:%M %p}"


def _day(dt: datetime) -> str:
    return f"{dt:%a} {dt.day} {dt:%b}"


def when(start: datetime, end: datetime) -> str:
    """`Fri 13 Nov, 9:00 AM–5:00 PM`, or both days when the outage crosses midnight."""
    s, t = start.astimezone(MANILA), end.astimezone(MANILA)
    if s.date() == t.date():
        return f"{_day(s)}, {_clock(s)}–{_clock(t)}"
    return f"{_day(s)}, {_clock(s)} – {_day(t)}, {_clock(t)}"


def _place_label(gaz: Gazetteer, code: str) -> tuple[str, str]:
    p = gaz.place(code)
    return (p.name, f"{p.name} ({p.town})") if p else (code, code)


def _ambiguous_note(snap: Snapshot, o: Outage, code: str, gaz: Gazetteer) -> str | None:
    rows = snap.rows.get(o.areas_hash or "", [])
    names = {r.name_raw for r in rows if r.psgc_code == code and r.match == "ambiguous"}
    if not names:
        return None
    name = sorted(names)[0]
    towns = []
    for r in rows:
        if r.name_raw == name and r.match == "ambiguous" and r.psgc_code:
            p = gaz.place(r.psgc_code)
            if p and p.town not in towns:
                towns.append(p.town)
    if len(towns) < 2:
        return None
    listed = ", ".join(towns[:-1]) + f" and {towns[-1]}"
    return f"VECO lists {e(name)} under {e(listed)}."


def _old_when(c: Change) -> str | None:
    old = c.old or {}
    if old.get("start_at") and old.get("end_at"):
        return when(datetime.fromisoformat(old["start_at"]), datetime.fromisoformat(old["end_at"]))
    return None


def _change_line(it: AlertItem, c: Change | None, name: str) -> str:
    kind = it.change_kind or (c.kind if c else "")
    if kind == "added":
        return "Added to VECO's post."
    if kind == "cancelled":
        return "Cancelled by VECO."
    if kind == "removed":
        return "Removed: no longer listed in VECO's post."
    if kind == "time_changed":
        was = _old_when(c) if c else None
        return f"Time changed; was {was}." if was else "Time changed."
    if kind == "status_changed":
        was = (c.old or {}).get("status") if c else None
        return f"Status changed; was {e(was)}." if was else "Status changed."
    if kind == "areas_changed":
        if it.direction == "in":
            return f"{e(name)} was added to this outage."
        if it.direction == "out":
            return f"{e(name)} was dropped from this outage."
        return "The listed areas changed."
    return "Changed."


def _block(
    o: Outage,
    code: str,
    snap: Snapshot,
    gaz: Gazetteer,
    changes: Iterable[AlertItem] = (),
) -> str:
    name, label = _place_label(gaz, code)
    lines = [f"<b>{when(o.start_at, o.end_at)}</b>", f"Portion of {e(label)}"]
    note = _ambiguous_note(snap, o, code, gaz)
    if note:
        lines.append(note)
    by_id = {c.id: c for c in snap.changes}
    for it in changes:
        lines.append(_change_line(it, by_id.get(it.change_id or -1), name))
    ents = snap.entities.get(o.areas_hash or "", [])
    if ents:
        shown = ", ".join(e(x) for x in ents[:MAX_ENTITIES])
        more = f" +{len(ents) - MAX_ENTITIES} more" if len(ents) > MAX_ENTITIES else ""
        lines.append(f"Streets/areas: {shown}{more}")
    purpose = o.purpose if len(o.purpose) <= MAX_PURPOSE else o.purpose[: MAX_PURPOSE - 1] + "…"
    lines.append(f"Purpose: {e(purpose)}")
    if o.post_failed:
        lines.append(f"<i>{e(CAVEAT)}</i>")
    lines.append(f'<a href="{html.escape(o.post_url, quote=True)}">VECO post</a>')
    return "\n".join(lines)


def _section(o: Outage, today: date) -> str:
    d = o.start_at.astimezone(MANILA).date()
    if d <= today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return "Later"


def _pack(sections: list[tuple[str, list[str]]], header: str) -> list[str]:
    """Join sections into messages <= MAX_LEN, splitting only between blocks; a section's
    title is repeated at the top of a continued message."""
    parts: list[str] = []
    cur = header
    for title, blocks in sections:
        head = f"<b>{title}</b>"
        opened = False  # this section's title is already in `cur`
        for b in blocks:
            add = f"\n\n{b}" if opened else f"\n\n{head}\n{b}"
            if len(cur) + len(add) > MAX_LEN and cur != header:
                parts.append(cur)
                cur = header
                add = f"\n\n{head}\n{b}"
            cur += add
            opened = True
    if cur != header:
        parts.append(cur)
    return parts


def render(items: list[AlertItem], snap: Snapshot, gaz: Gazetteer, now: datetime) -> list[str]:
    """One subscriber's items -> HTML messages, each <= MAX_LEN; [] when there are none."""
    today = now.astimezone(MANILA).date()
    order: list[int] = []
    by_outage: dict[int, list[AlertItem]] = {}
    for it in items:
        if it.outage_id not in by_outage:
            order.append(it.outage_id)
        by_outage.setdefault(it.outage_id, []).append(it)
    sections: dict[str, list[str]] = {"Changes": [], "Today": [], "Tomorrow": [], "Later": []}
    keyed = sorted(order, key=lambda oid: (snap.outages[oid].start_at, oid))
    for oid in keyed:
        o = snap.outages[oid]
        its = by_outage[oid]
        changes = [i for i in its if i.kind == "change"]
        title = "Changes" if changes else _section(o, today)
        sections[title].append(_block(o, its[0].psgc_code, snap, gaz, changes))
    return _pack([(t, bs) for t, bs in sections.items() if bs], HEADER)


def render_upcoming(psgc_code: str, snap: Snapshot, gaz: Gazetteer, now: datetime) -> str:
    """The `/add` reply: this barangay's outages in the next 7 days (informational only)."""
    _, label = _place_label(gaz, psgc_code)
    head = f"<b>Upcoming outages for {e(label)}</b>"
    until = now + timedelta(days=UPCOMING_DAYS)
    outs = sorted(
        (
            o
            for o in snap.outages.values()
            if o.status in REMINDER_STATUSES
            and o.end_at > now
            and o.start_at <= until
            and concerns(snap, o, psgc_code)
        ),
        key=lambda o: (o.start_at, o.id),
    )
    if not outs:
        return f"{head}\nNo scheduled outages in the next 7 days."
    text = head
    for i, o in enumerate(outs):
        add = "\n\n" + _block(o, psgc_code, snap, gaz)
        tail = f"\n\n+{len(outs) - i} more"
        if len(text) + len(add) + len(tail) > MAX_LEN:
            return text + tail
        text += add
    return text
