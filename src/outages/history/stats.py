"""Union-hours stats for a date range (Phase 5 spec 4.8). Pure: rows in, document out.

These are VECO's *scheduled* interruptions, not measured outages. A barangay's hours are the
length of the union of the counted intervals placing it, clipped to the range, so two
overlapping outages over the same barangay are not counted twice. Nothing is dropped silently:
cancelled, removed, stale and left-out outages are counted with their reason.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from outages.areas.gazetteer import Gazetteer
from outages.map.window import WindowError
from outages.timeparse import MANILA

DEFAULT_DAYS = 365
COUNTED = ("scheduled", "additional")
REASONS = ("other_town", "unmatched", "not_parsed", "no_barangay")
TOP_N = 10
WORST_N = 3
BANNER = "These are VECO's scheduled interruptions, not measured outages."

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class Range:
    from_date: date
    to_date: date

    @property
    def start(self) -> datetime:
        return datetime.combine(self.from_date, time(0), MANILA)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.to_date + timedelta(days=1), time(0), MANILA)


@dataclass(frozen=True)
class Coverage:
    first_outage: str | None
    posts: int
    posts_parsed: int


@dataclass(frozen=True)
class OutageRow:
    id: int
    start: datetime
    end: datetime
    status: str
    stale: bool
    placed: list[tuple[str, bool]]  # (psgc, ambiguous), distinct, in parse order
    left_out_reason: str | None  # set when nothing is placed


def _date(s: str | None, name: str) -> date | None:
    if s is None:
        return None
    try:
        if len(s) != 10:
            raise ValueError
        return date.fromisoformat(s)
    except ValueError:
        raise WindowError(f"{name} must be a date as YYYY-MM-DD, got {s!r}") from None


def parse_range(from_: str | None, to: str | None, now: datetime) -> Range:
    """Inclusive Manila dates; default to = today, from = to − 364 days. Raises WindowError."""
    to_d = _date(to, "to") or now.astimezone(MANILA).date()
    from_d = _date(from_, "from") or to_d - timedelta(days=DEFAULT_DAYS - 1)
    if from_d > to_d:
        raise WindowError(f"from ({from_d}) must not be after to ({to_d})")
    return Range(from_d, to_d)


def _hours(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600


def union_hours(intervals: list[Interval]) -> float:
    total = 0.0
    cur: Interval | None = None
    for a, b in sorted(intervals):
        if cur is None or a > cur[1]:
            if cur is not None:
                total += _hours(*cur)
            cur = (a, b)
        elif b > cur[1]:
            cur = (cur[0], b)
    if cur is not None:
        total += _hours(*cur)
    return total


def _clip(r: OutageRow, rng: Range) -> Interval | None:
    a, b = max(r.start, rng.start), min(r.end, rng.end)
    return (a, b) if b > a else None


def _month_starts(rng: Range) -> list[date]:
    d = rng.from_date.replace(day=1)
    out = []
    while d <= rng.to_date:
        out.append(d)
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def _split_months(iv: Interval) -> list[tuple[str, float]]:
    """(YYYY-MM, hours) of an interval, split at Manila month starts."""
    out = []
    a, b = iv
    while a < b:
        m = a.astimezone(MANILA)
        nxt_d = (m.date().replace(day=1) + timedelta(days=32)).replace(day=1)
        nxt = datetime.combine(nxt_d, time(0), MANILA)
        cut = min(b, nxt)
        out.append((f"{m:%Y-%m}", _hours(a, cut)))
        a = cut
    return out


def _r(x: float) -> float:
    return round(x, 1)


def compute(
    rows: list[OutageRow], rng: Range, gaz: Gazetteer, coverage: Coverage, now: datetime
) -> dict:
    counts = {"counted": 0, "cancelled": 0, "removed": 0, "stale": 0}
    left = {"count": 0, "hours": 0.0, "by_reason": dict.fromkeys(REASONS, 0)}
    per: dict[str, dict] = {}
    towns: dict[str, dict] = {}
    months = {f"{m:%Y-%m}": {"hours": 0.0, "outages": set()} for m in _month_starts(rng)}
    durations: list[float] = []
    for r in rows:
        iv = _clip(r, rng)
        if iv is None:
            continue
        if r.status == "removed":
            counts["removed"] += 1
            continue
        if r.status not in COUNTED:
            counts["cancelled"] += 1
            continue
        if not r.placed:
            left["count"] += 1
            left["hours"] += _hours(*iv)
            left["by_reason"][r.left_out_reason or "no_barangay"] += 1
            continue
        counts["counted"] += 1
        counts["stale"] += r.stale
        durations.append(_hours(r.start, r.end))
        for month, h in _split_months(iv):
            if month in months:
                months[month]["hours"] += h
                months[month]["outages"].add(r.id)
        seen_towns: set[str] = set()
        for psgc, ambiguous in r.placed:
            b = per.setdefault(psgc, {"iv": [], "amb": [], "ids": set(), "last": None})
            b["iv"].append(iv)
            if ambiguous:
                b["amb"].append(iv)
            b["ids"].add(r.id)
            if b["last"] is None or (r.start, r.id) > b["last"]:
                b["last"] = (r.start, r.id)
            place = gaz.place(psgc)
            town = place.town if place else "?"
            if town not in seen_towns:
                seen_towns.add(town)
                t = towns.setdefault(town, {"hours": 0.0, "outages": 0})
                t["hours"] += _hours(*iv)
                t["outages"] += 1

    barangays = {}
    for psgc in sorted(per):
        b = per[psgc]
        hours = union_hours(b["iv"])
        if hours <= 0:
            continue
        start, oid = b["last"]
        barangays[psgc] = {
            "hours": _r(hours),
            "outages": len(b["ids"]),
            "ambiguous_hours": _r(union_hours(b["amb"])),
            "last": {"start": start.astimezone(MANILA).isoformat(), "id": oid},
        }

    def name_of(psgc: str) -> tuple[str, str]:
        p = gaz.place(psgc)
        return (p.name, p.town) if p else (psgc, "?")

    top = sorted(barangays, key=lambda p: (-barangays[p]["hours"], name_of(p)[0], p))[:TOP_N]
    month_list = [
        {"month": m, "hours": _r(v["hours"]), "outages": len(v["outages"])}
        for m, v in months.items()
    ]
    worst = sorted(
        (m for m in month_list if m["hours"] > 0), key=lambda m: (-m["hours"], m["month"])
    )
    return {
        "range": {"from": rng.from_date.isoformat(), "to": rng.to_date.isoformat()},
        "coverage": {
            "first_outage": coverage.first_outage,
            "posts": coverage.posts,
            "posts_parsed": coverage.posts_parsed,
        },
        "barangays": barangays,
        "top": [
            {
                "psgc": p,
                "name": name_of(p)[0],
                "town": name_of(p)[1],
                "hours": barangays[p]["hours"],
                "outages": barangays[p]["outages"],
            }
            for p in top
        ],
        "duration": {
            "mean_h": _r(statistics.fmean(durations)) if durations else None,
            "median_h": _r(statistics.median(durations)) if durations else None,
            "n": len(durations),
        },
        "months": month_list,
        "worst_months": [m["month"] for m in worst[:WORST_N]],
        "towns": [
            {"town": t, "hours": _r(v["hours"]), "outages": v["outages"]}
            for t, v in sorted(towns.items(), key=lambda kv: (-kv[1]["hours"], kv[0]))
        ],
        "counts": counts,
        "left_out": {
            "count": left["count"],
            "hours": _r(left["hours"]),
            "by_reason": left["by_reason"],
        },
        "generated_at": now.astimezone(MANILA).isoformat(timespec="seconds"),
    }


def format_text(doc: dict) -> str:
    """The `stats` command's output: the same numbers as `/stats.json`, as text."""
    rng, cov = doc["range"], doc["coverage"]
    lines = [
        f"History {rng['from']} to {rng['to']}",
        BANNER,
        f"Posts from {cov['first_outage']}; {cov['posts_parsed']} of {cov['posts']} posts read.",
        "",
        "Most affected barangays (scheduled hours, union of intervals):",
    ]
    for i, b in enumerate(doc["top"], 1):
        lines.append(
            f"  {i:>2}. {b['name']}, {b['town']}: {b['hours']} h in {b['outages']} outages"
        )
    if not doc["top"]:
        lines.append("  (none)")
    d = doc["duration"]
    lines += [
        "",
        f"Duration: mean {d['mean_h']} h, median {d['median_h']} h over {d['n']} outages",
        "",
        "Towns (outage-hours, each outage once per town):",
        *(f"  {t['town']}: {t['hours']} h, {t['outages']} outages" for t in doc["towns"]),
        "",
        "Months (outage-hours):",
        *(f"  {m['month']}: {m['hours']} h, {m['outages']} outages" for m in doc["months"]),
        f"Worst months: {', '.join(doc['worst_months']) or '-'}",
        "",
    ]
    c, lo = doc["counts"], doc["left_out"]
    reasons = ", ".join(f"{k} {v}" for k, v in lo["by_reason"].items())
    lines += [
        f"Counted {c['counted']} (stale {c['stale']}); cancelled {c['cancelled']}; "
        f"removed {c['removed']}",
        f"left out {lo['count']} outages, {lo['hours']} h: {reasons}",
        f"generated {doc['generated_at']}",
    ]
    return "\n".join(lines) + "\n"
