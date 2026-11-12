"""History stats: union hours per barangay, months, tops, left-out (Phase 5 spec 4.8).

Golden: set `UPDATE_GOLDEN=1` to rewrite tests/golden/history/default.json, then review it.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

import pytest
from map_world import LAHUG, LINAO, NOW, STO_NINO, TAYUD_C, TAYUD_L, TISA, gazetteer, populate

from outages import db
from outages.history.load import load_rows
from outages.history.stats import (
    Coverage,
    OutageRow,
    Range,
    compute,
    format_text,
    parse_range,
    union_hours,
)
from outages.map.window import WindowError
from outages.timeparse import MANILA

GOLDEN = Path(__file__).parent / "golden" / "history"
COV = Coverage(first_outage="2024-01-23", posts=2, posts_parsed=1)
NOV = Range(date(2026, 11, 1), date(2026, 11, 30))


def t(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=MANILA)


def row(i, start, end, status="scheduled", placed=((LAHUG, False),), stale=False, reason=None):
    return OutageRow(i, t(start), t(end), status, stale, list(placed), reason)


def doc(rows, rng=NOV):
    return compute(rows, rng, gazetteer(), COV, NOW)


def test_union_overlapping_intervals():
    iv = [
        (t("2026-11-02T08:00"), t("2026-11-02T12:00")),
        (t("2026-11-02T10:00"), t("2026-11-02T14:00")),
    ]
    assert union_hours(iv) == 6
    assert union_hours([]) == 0
    assert union_hours(iv + [(t("2026-11-03T08:00"), t("2026-11-03T09:30"))]) == 7.5


def test_barangay_hours_are_union_not_sum():
    d = doc(
        [
            row(1, "2026-11-02T08:00", "2026-11-02T12:00"),
            row(2, "2026-11-02T10:00", "2026-11-02T14:00"),
        ]
    )
    assert d["barangays"][LAHUG]["hours"] == 6.0
    assert d["barangays"][LAHUG]["outages"] == 2
    assert d["barangays"][LAHUG]["last"] == {"start": "2026-11-02T10:00:00+08:00", "id": 2}
    # towns and months sum outage-hours (each outage once)
    assert d["towns"] == [{"town": "Cebu City", "hours": 8.0, "outages": 2}]


def test_range_clips_interval():
    d = doc([row(1, "2026-10-31T20:00", "2026-11-01T04:00")])
    assert d["barangays"][LAHUG]["hours"] == 4.0
    assert d["duration"] == {"mean_h": 8.0, "median_h": 8.0, "n": 1}  # full scheduled length


def test_month_split_at_manila_midnight():
    rng = Range(date(2026, 10, 1), date(2026, 11, 30))
    d = doc([row(1, "2026-10-31T22:00", "2026-11-01T04:00")], rng)
    assert d["months"] == [
        {"month": "2026-10", "hours": 2.0, "outages": 1},
        {"month": "2026-11", "hours": 4.0, "outages": 1},
    ]


def test_ambiguous_counted_each_town_with_ambiguous_hours():
    d = doc(
        [row(1, "2026-11-02T08:00", "2026-11-02T11:00", placed=[(TAYUD_C, True), (TAYUD_L, True)])]
    )
    for p in (TAYUD_C, TAYUD_L):
        assert d["barangays"][p]["hours"] == 3.0
        assert d["barangays"][p]["ambiguous_hours"] == 3.0
    assert {x["town"] for x in d["towns"]} == {"Consolacion", "Liloan"}
    d = doc([row(1, "2026-11-02T08:00", "2026-11-02T11:00")])
    assert d["barangays"][LAHUG]["ambiguous_hours"] == 0


def test_cancelled_removed_counted_not_in_hours():
    d = doc(
        [
            row(1, "2026-11-02T08:00", "2026-11-02T12:00", status="cancelled"),
            row(2, "2026-11-02T08:00", "2026-11-02T12:00", status="removed"),
            row(3, "2026-11-03T08:00", "2026-11-03T09:00", status="additional"),
        ]
    )
    assert d["barangays"][LAHUG]["hours"] == 1.0
    assert d["counts"] == {"counted": 1, "cancelled": 1, "removed": 1, "stale": 0}


def test_stale_counted_and_flagged():
    d = doc([row(1, "2026-11-02T08:00", "2026-11-02T12:00", stale=True)])
    assert d["barangays"][LAHUG]["hours"] == 4.0
    assert d["counts"]["stale"] == 1 and d["counts"]["counted"] == 1


def test_left_out_reasons():
    rows = [
        row(1, "2026-11-02T08:00", "2026-11-02T10:00", placed=[], reason="other_town"),
        row(2, "2026-11-02T08:00", "2026-11-02T11:00", placed=[], reason="unmatched"),
        row(3, "2026-11-02T08:00", "2026-11-02T09:00", placed=[], reason="not_parsed"),
        row(4, "2026-11-02T08:00", "2026-11-02T09:30", placed=[], reason="no_barangay"),
        row(
            5,
            "2026-11-02T08:00",
            "2026-11-02T09:30",
            placed=[],
            reason="no_barangay",
            status="cancelled",
        ),
    ]
    d = doc(rows)
    assert d["left_out"] == {
        "count": 4,
        "hours": 7.5,
        "by_reason": {"other_town": 1, "unmatched": 1, "not_parsed": 1, "no_barangay": 1},
    }
    assert d["barangays"] == {}
    assert d["counts"]["counted"] == 0 and d["counts"]["cancelled"] == 1
    assert d["duration"]["n"] == 0 and d["duration"]["mean_h"] is None


def test_months_zero_filled():
    rng = Range(date(2026, 8, 15), date(2026, 11, 2))
    d = doc([row(1, "2026-11-02T08:00", "2026-11-02T10:00")], rng)
    assert [m["month"] for m in d["months"]] == ["2026-08", "2026-09", "2026-10", "2026-11"]
    assert [m["hours"] for m in d["months"]] == [0, 0, 0, 2.0]
    assert d["worst_months"] == ["2026-11"]


def test_worst_months_top_three_by_hours():
    rng = Range(date(2026, 1, 1), date(2026, 12, 31))
    rows = [
        row(i, f"2026-{m:02d}-10T08:00", f"2026-{m:02d}-10T{8 + h:02d}:00")
        for i, (m, h) in enumerate([(1, 2), (2, 5), (3, 1), (4, 5), (5, 3)], 1)
    ]
    assert doc(rows, rng)["worst_months"] == ["2026-02", "2026-04", "2026-05"]


def test_top_ten_tie_by_name():
    d = doc(
        [
            row(
                1,
                "2026-11-02T08:00",
                "2026-11-02T10:00",
                placed=[(TISA, False), (LAHUG, False), (STO_NINO, False)],
            ),
            row(2, "2026-11-03T08:00", "2026-11-03T12:00", placed=[(LINAO, False)]),
        ]
    )
    assert [x["name"] for x in d["top"]] == ["Linao", "Lahug", "Santo Niño", "Tisa"]
    assert d["top"][0] == {
        "psgc": LINAO,
        "name": "Linao",
        "town": "Minglanilla",
        "hours": 4.0,
        "outages": 1,
    }


def test_median_and_mean_duration():
    d = doc(
        [
            row(1, "2026-11-02T08:00", "2026-11-02T10:00"),
            row(2, "2026-11-03T08:00", "2026-11-03T12:00"),
            row(3, "2026-11-04T08:00", "2026-11-04T17:00"),
            row(4, "2026-11-05T08:00", "2026-11-05T17:00"),
        ]
    )
    assert d["duration"] == {"mean_h": 6.0, "median_h": 6.5, "n": 4}


def test_parse_range():
    assert parse_range(None, None, NOW) == Range(date(2025, 11, 13), date(2026, 11, 12))
    assert parse_range("2026-01-01", "2026-01-31", NOW) == Range(
        date(2026, 1, 1), date(2026, 1, 31)
    )
    assert parse_range(None, "2026-06-30", NOW).from_date == date(2025, 7, 1)
    assert parse_range("2026-11-12", "2026-11-12", NOW).end == t("2026-11-13T00:00")
    for f, to in (
        ("2026-02-30", None),
        ("x", None),
        (None, "2026-1-1"),
        ("2026-11-12", "2026-11-11"),
    ):
        with pytest.raises(WindowError):
            parse_range(f, to, NOW)


@pytest.fixture
def conn():
    c = db.connect(Path(":memory:"))
    populate(c)
    yield c
    c.close()


def test_load_rows_world(conn):
    rng = Range(date(2026, 11, 12), date(2026, 11, 20))
    rows, cov = load_rows(conn, rng)
    by_id = {r.id: r for r in rows}
    assert set(by_id) == set(range(1, 12))  # 9 removed is in range too
    assert by_id[1].placed == [(LAHUG, False), (TISA, False)]
    assert by_id[5].placed == [(TAYUD_C, True), (TAYUD_L, True)] and by_id[5].stale
    assert by_id[6].placed == [(LINAO, False)]  # other_town row ignored when one is placed
    assert (by_id[7].placed, by_id[7].left_out_reason) == ([], "unmatched")
    assert by_id[8].left_out_reason == "not_parsed"
    assert by_id[11].left_out_reason == "no_barangay"
    assert cov == Coverage(first_outage="2026-11-12", posts=2, posts_parsed=1)
    outside, _ = load_rows(conn, Range(date(2026, 11, 21), date(2026, 11, 30)))
    assert outside == []


def test_left_out_and_counts_add_up(conn):
    rng = Range(date(2026, 11, 1), date(2026, 11, 30))
    rows, cov = load_rows(conn, rng)
    d = compute(rows, rng, gazetteer(), cov, NOW)
    c = d["counts"]
    assert d["left_out"]["count"] + c["counted"] + c["cancelled"] + c["removed"] == len(rows)


def test_golden_default(conn):
    rng = parse_range(None, None, NOW)
    rows, cov = load_rows(conn, rng)
    d = compute(rows, rng, gazetteer(), cov, NOW)
    path = GOLDEN / "default.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(d, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
        )
    assert d == json.loads(path.read_text(encoding="utf-8"))
    assert d["range"] == {"from": "2025-11-13", "to": "2026-11-12"}
    assert d["generated_at"] == "2026-11-12T10:00:00+08:00"


def test_format_text(conn):
    rng = Range(date(2026, 11, 1), date(2026, 11, 30))
    rows, cov = load_rows(conn, rng)
    text = format_text(compute(rows, rng, gazetteer(), cov, NOW))
    assert "VECO's scheduled interruptions, not measured outages" in text
    assert "Lahug" in text and "left out" in text
