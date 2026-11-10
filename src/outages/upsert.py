"""Match a post's new parse against its stored outages (spec 6.3). Pure: no I/O.

Passes, each outage matched at most once, ties paired in page order:
1. same start date, start/end and areas_key -> unchanged (or cancelled / status_changed)
2. same start date and areas_key            -> time_changed
3. same start date and start/end            -> areas_changed
4. leftovers: stored -> removed, new -> added
So a time change is recorded as a change, not as a delete plus an add.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from outages.db import StoredOutage
from outages.parse import ParsedOutage

Kind = Literal[
    "unchanged", "added", "cancelled", "status_changed", "time_changed", "areas_changed", "removed"
]


@dataclass(frozen=True)
class Change:
    kind: Kind
    old: StoredOutage | None
    new: ParsedOutage | None


def _day(o: StoredOutage | ParsedOutage) -> str:
    s = o.start_at if isinstance(o.start_at, str) else o.start_at.isoformat()
    return s[:10]  # Manila date: times are stored with +08:00


def _times(o: StoredOutage | ParsedOutage) -> tuple[str, str]:
    if isinstance(o, StoredOutage):
        return o.start_at, o.end_at
    return o.start_at.isoformat(), o.end_at.isoformat()


def _same_time(a, b) -> bool:
    return _day(a) == _day(b) and _times(a) == _times(b)


def _same_areas(a, b) -> bool:
    return _day(a) == _day(b) and a.areas_key == b.areas_key


def _status_kind(old: StoredOutage, new: ParsedOutage) -> Kind:
    if old.status == new.status:
        return "unchanged"
    return "cancelled" if new.status == "cancelled" else "status_changed"


def match(old: list[StoredOutage], new: list[ParsedOutage]) -> list[Change]:
    """Changes in order: matched pairs by new page order, then removed, then added."""
    olds = sorted(old, key=lambda o: (o.seq, o.id))
    news = sorted(new, key=lambda n: n.seq)
    free_old = list(range(len(olds)))
    pairs: dict[int, tuple[int, Kind]] = {}  # new index -> (old index, kind)

    passes: list[tuple[Callable[[StoredOutage, ParsedOutage], bool], Kind | None]] = [
        (lambda o, n: _same_time(o, n) and _same_areas(o, n), None),
        (_same_areas, "time_changed"),
        (_same_time, "areas_changed"),
    ]
    for pred, kind in passes:
        for ni, n in enumerate(news):
            if ni in pairs:
                continue
            for oi in free_old:
                if pred(olds[oi], n):
                    pairs[ni] = (oi, kind or _status_kind(olds[oi], n))
                    free_old.remove(oi)
                    break

    changes = [
        Change(pairs[ni][1], olds[pairs[ni][0]], news[ni]) for ni in range(len(news)) if ni in pairs
    ]
    changes += [Change("removed", olds[oi], None) for oi in free_old]
    changes += [Change("added", None, n) for ni, n in enumerate(news) if ni not in pairs]
    return changes
