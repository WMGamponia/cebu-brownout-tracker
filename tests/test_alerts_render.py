"""Alert messages as Telegram HTML (Phase 3 spec 4.4, 5.5)."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from outages.alerts.render import MAX_LEN, render, render_upcoming
from outages.alerts.select import AlertItem, Change, Outage, Row, Snapshot, Sub
from outages.areas.gazetteer import load as load_gazetteer
from outages.timeparse import MANILA

GOLDEN = Path(__file__).parent / "golden" / "alerts"
LAHUG, CC = "730600041", "730600000"
TAYUD_CO, TAYUD_LI = "702219018", "702227013"
GAZ = load_gazetteer()


def m(day: int, hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 11, day, hh, mm, tzinfo=MANILA)


NOW = m(13, 6)  # Fri 13 Nov 2026, 06:00


def outage(
    oid, start, end, purpose="Line maintenance", *, h=None, failed=False, status="scheduled"
):
    return Outage(oid, f"https://example.invalid/{oid}", start, end, status, purpose, h, failed)


def item(oid, kind, *, code=LAHUG, change_id=None, change_kind=None, direction=None):
    return AlertItem(1, 42, code, oid, kind, change_id, change_kind, direction)


def make_snap(outages, *, rows=None, entities=None, changes=()):
    rows = rows or {o.areas_hash: [Row(LAHUG, "Lahug", CC, "exact")] for o in outages}
    return Snapshot(
        subs=[Sub(1, 42, LAHUG, m(1, 0))],
        outages={o.id: o for o in outages},
        rows=rows,
        entities=entities or {},
        changes=list(changes),
        sent=frozenset(),
    )


def test_render_golden_mixed():
    a = Outage(
        1,
        "https://example.invalid/a",
        m(15, 8),
        m(15, 12),
        "scheduled",
        "Pole relocation",
        "ha",
        False,
    )
    b = Outage(
        2,
        "https://example.invalid/b",
        m(13, 9),
        m(13, 17),
        "scheduled",
        "Line maintenance & tree trimming",
        "hb",
        False,
    )
    c = Outage(
        3,
        "https://example.invalid/c",
        m(14, 22),
        m(15, 2),
        "scheduled",
        "Substation upgrade",
        "hc",
        False,
    )
    ch = Change(
        9,
        1,
        "time_changed",
        {"start_at": m(14, 8).isoformat(), "end_at": m(14, 12).isoformat()},
        {},
        m(13, 3),
        frozenset(),
    )
    snap = make_snap(
        [a, b, c],
        entities={"ha": ["Gorordo Ave."], "hb": ["Gorordo Ave.", "Salinas Drive", "Sitio Kamatis"]},
        changes=[ch],
    )
    items = [
        item(2, "day_of"),
        item(3, "day_before"),
        item(1, "change", change_id=9, change_kind="time_changed"),
    ]
    parts = render(items, snap, GAZ, NOW)
    golden = (GOLDEN / "mixed.html").read_text(encoding="utf-8").rstrip("\n")
    assert parts == [golden]


def test_render_ambiguous_note():
    o = outage(1, m(13, 9), m(13, 17), h="h")
    rows = {
        "h": [
            Row(TAYUD_CO, "Tayud", "702219000", "ambiguous"),
            Row(TAYUD_LI, "Tayud", "702227000", "ambiguous"),
        ]
    }
    (text,) = render([item(1, "day_of", code=TAYUD_LI)], make_snap([o], rows=rows), GAZ, NOW)
    assert "Portion of Tayud (Liloan)" in text
    assert "VECO lists Tayud under Consolacion and Liloan." in text


def test_render_failed_parse_caveat():
    o = outage(1, m(13, 9), m(13, 17), h="h", failed=True)
    (text,) = render([item(1, "day_of")], make_snap([o]), GAZ, NOW)
    assert (
        "VECO updated this post and we couldn't read the update; this is from the earlier version."
        in text
    )
    assert '<a href="https://example.invalid/1">VECO post</a>' in text


def test_render_removed_wording():
    o = outage(1, m(14, 9), m(14, 17), h="h", status="removed")
    ch = Change(4, 1, "removed", {}, None, m(13, 3), frozenset())
    (text,) = render(
        [item(1, "change", change_id=4, change_kind="removed")],
        make_snap([o], changes=[ch]),
        GAZ,
        NOW,
    )
    assert "no longer listed in VECO's post" in text
    assert "cancel" not in text.lower()


@pytest.mark.parametrize(
    ("kind", "direction", "old", "expected"),
    [
        ("added", None, None, "Added to VECO's post."),
        ("cancelled", None, {}, "Cancelled by VECO."),
        ("status_changed", None, {"status": "scheduled"}, "Status changed; was scheduled."),
        ("areas_changed", "in", {}, "Lahug was added to this outage."),
        ("areas_changed", "out", {}, "Lahug was dropped from this outage."),
        ("areas_changed", None, {}, "The listed areas changed."),
    ],
)
def test_change_wording(kind, direction, old, expected):
    o = outage(1, m(14, 9), m(14, 17), h="h")
    ch = Change(4, 1, kind, old, {}, m(13, 3), frozenset())
    it = item(1, "change", change_id=4, change_kind=kind, direction=direction)
    (text,) = render([it], make_snap([o], changes=[ch]), GAZ, NOW)
    assert expected in text


def test_change_and_reminder_one_line():
    o = outage(1, m(13, 9), m(13, 17), h="h")
    ch = Change(
        4,
        1,
        "time_changed",
        {"start_at": m(13, 8).isoformat(), "end_at": m(13, 17).isoformat()},
        {},
        m(13, 3),
        frozenset(),
    )
    items = [item(1, "day_of"), item(1, "change", change_id=4, change_kind="time_changed")]
    (text,) = render(items, make_snap([o], changes=[ch]), GAZ, NOW)
    assert text.count("Fri 13 Nov, 9:00 AM–5:00 PM") == 1
    assert "<b>Changes</b>" in text and "<b>Today</b>" not in text


def test_render_plus_more():
    o = outage(1, m(13, 9), m(13, 17), h="h")
    ents = {"h": [f"Street {i}" for i in range(1, 9)]}
    (text,) = render([item(1, "day_of")], make_snap([o], entities=ents), GAZ, NOW)
    assert "Streets/areas: Street 1, Street 2, Street 3, Street 4, Street 5 +3 more" in text


def test_render_escapes():
    o = outage(1, m(13, 9), m(13, 17), "Line <upgrade> & repair (phase 2)", h="h")
    ents = {"h": ["A&B <Compound>"]}
    (text,) = render([item(1, "day_of")], make_snap([o], entities=ents), GAZ, NOW)
    assert "Purpose: Line &lt;upgrade&gt; &amp; repair (phase 2)" in text
    assert "A&amp;B &lt;Compound&gt;" in text


def test_render_sections_by_date():
    o = outage(1, m(13, 9), m(13, 17), h="h")
    (text,) = render([item(1, "day_before")], make_snap([o]), GAZ, m(13, 1))
    assert "<b>Today</b>" in text and "Tomorrow" not in text


def _balanced(html: str) -> bool:
    stack = []
    for close, tag in re.findall(r"<(/?)(\w+)[^>]*>", html):
        if close:
            if not stack or stack.pop() != tag:
                return False
        else:
            stack.append(tag)
    return not stack


def test_render_splits_under_4096():
    outs = [
        outage(i, m(13, 9) + timedelta(minutes=i), m(13, 17), "Purpose " + "x" * 80, h="h")
        for i in range(1, 61)
    ]
    ents = {"h": [f"Very long street name number {i}" for i in range(8)]}
    items = [item(o.id, "day_of") for o in outs]
    parts = render(items, make_snap(outs, entities=ents), GAZ, NOW)
    assert len(parts) > 1
    assert all(len(p) <= MAX_LEN and _balanced(p) for p in parts)
    joined = "".join(parts)
    for o in outs:
        assert joined.count(f'href="https://example.invalid/{o.id}"') == 1
    assert all(p.startswith("<b>Scheduled outages that may affect you</b>") for p in parts)


def test_render_nothing_for_no_items():
    assert render([], make_snap([]), GAZ, NOW) == []


def test_render_upcoming():
    a = outage(1, m(14, 9), m(14, 17), h="h")
    late = outage(2, m(25, 9), m(25, 17), h="h")  # beyond 7 days
    text = render_upcoming(LAHUG, make_snap([a, late]), GAZ, NOW)
    assert "Lahug (Cebu City)" in text
    assert "Sat 14 Nov, 9:00 AM–5:00 PM" in text and "25 Nov" not in text
    assert len(text) <= MAX_LEN and _balanced(text)


def test_render_upcoming_none():
    text = render_upcoming(LAHUG, make_snap([]), GAZ, NOW)
    assert "No scheduled outages in the next 7 days." in text
