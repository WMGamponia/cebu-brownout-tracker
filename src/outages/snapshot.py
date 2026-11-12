"""The static demo snapshot for GitHub Pages (Phase 6 spec 4.1, 4.2).

Offline and read-only: every document is built in memory with the server's own `build()`,
`compute()` and `dumps()` over a connection whose authorizer refuses the subscriber and alert
tables, the bytes are scanned for chat IDs and the bot token, and only then is anything written.
Time is pinned to `taken_at` for every window, range and label.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from outages.areas.gazetteer import Gazetteer
from outages.config import PUBLIC_REPO_URL, VECO_HOME
from outages.history.load import load_rows
from outages.history.stats import Range, compute
from outages.map.app import STATIC
from outages.map.build import build, dumps
from outages.map.window import Window, default_window
from outages.timeparse import MANILA

FORMAT = 1
PAST_WEEKS = 52
FIRST_YEAR = 2024  # the archive's start
DEFAULT_RANGE = "last-12-months"

# Every table of a fully migrated DB is in exactly one set (a test fails on a new one). The guard
# refuses reads of the private ones, and of any table in neither set.
PRIVATE_TABLES: frozenset[str] = frozenset(
    {"subscribers", "subscriptions", "alerts_sent", "admin_notes_sent"}
)
PUBLIC_TABLES: frozenset[str] = frozenset(
    {
        "schema_version",
        "meta",
        "posts",
        "post_versions",
        "outages",
        "outage_changes",
        "area_parses",
        "area_barangays",
        "area_entities",
        "llm_cache",
        "street_matches",
    }
)

PAGES = ("index.html", "history.html")
TOP_LEVEL = frozenset(
    {
        ".git",
        ".nojekyll",
        "index.html",
        "history.html",
        "snapshot.json",
        "boundaries.geojson",
        "LICENSES.md",
        "static",
        "weeks",
        "stats",
    }
)
# What a previous snapshot may hold inside the folders a refresh prunes.
PRUNED = {
    "weeks": re.compile(r"\d{4}-\d{2}-\d{2}\.geojson"),
    "stats": re.compile(r"[a-z0-9-]+\.json"),
}

LICENSES = """\
# Licences and data notes

This folder is a static snapshot of the Cebu Brownout Tracker, a portfolio demo. It is not
live and not affiliated with VECO (Visayan Electric Company).

- **Outage data:** from VECO's public scheduled power interruption advisories; every outage
  links to its post. Only parsed fields are published (times, status, purpose, the affected
  areas as VECO wrote them, barangays), not the posts themselves.
- **Barangay boundaries:** faeldon/philippines-json-maps v1.1.1 (MIT), PSGC 3Q 2026.
- **Map library:** Leaflet 1.9.4, BSD-2-Clause (in `static/vendor/leaflet-1.9.4/`).
- **Map tiles:** © OpenStreetMap contributors, used under the OSMF tile usage policy. Your
  browser fetches the tiles from OpenStreetMap's servers, which sees their IP address.
- **Street lines:** none while street drawing is off. If drawing is ever on, street lines are
  derived from OpenStreetMap data, © OpenStreetMap contributors, available under the ODbL; the
  derived lines are in `weeks/*.geojson` (the `streets.drawn[].lines` fields).
- **Code:** MIT; see the repository.
"""


class SnapshotError(Exception):
    """The snapshot can't be built or written; the message says why."""


@dataclass(frozen=True)
class RangeSpec:
    id: str
    label: str
    from_d: date
    to_d: date


def guard(conn: sqlite3.Connection) -> None:
    """Refuse reads of the private tables (and of any unclassified table) on `conn`."""

    def authorize(action, arg1, arg2, dbname, source):
        private = arg1 in PRIVATE_TABLES or arg1 not in PUBLIC_TABLES
        if action == sqlite3.SQLITE_READ and arg1 and not arg1.startswith("sqlite_") and private:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    conn.set_authorizer(authorize)


def taken_date(taken_at: datetime) -> date:
    return taken_at.astimezone(MANILA).date()


def windows(conn: sqlite3.Connection, taken_at: datetime) -> list[Window]:
    """53 windows, oldest first: 52 seven-day weeks back from the taken date, then the snapshot
    week (the map's default window at `taken_at`: today to the last stored end, ≤ 7 days)."""
    d = taken_date(taken_at)
    past = [
        Window(d - timedelta(days=7 * k), d - timedelta(days=7 * k - 6))
        for k in range(PAST_WEEKS, 0, -1)
    ]
    return [*past, default_window(conn, taken_at)]


def ranges(d: date) -> list[RangeSpec]:
    """The last 12 months, then every calendar year from 2024; the taken year to date."""
    out = [RangeSpec(DEFAULT_RANGE, "Last 12 months", d - timedelta(days=364), d)]
    for y in range(FIRST_YEAR, d.year + 1):
        if y == d.year:
            out.append(RangeSpec(str(y), f"{y} to date", date(y, 1, 1), d))
        else:
            out.append(RangeSpec(str(y), str(y), date(y, 1, 1), date(y, 12, 31)))
    return out


def manila_iso(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(MANILA).isoformat(timespec="seconds")


def _page(name: str) -> bytes:
    html = (STATIC / name).read_text(encoding="utf-8")
    tag = '<html lang="en">'
    if html.count(tag) != 1:
        raise SnapshotError(f"{name}: expected one {tag}")
    html = html.replace(tag, '<html lang="en" data-mode="snapshot">')
    # A static host has no /history route: link the file itself.
    html = html.replace('href="history"', 'href="history.html"')
    return html.encode("utf-8")


def _static_files() -> dict[str, bytes]:
    out = {}
    for p in sorted(STATIC.rglob("*")):
        rel = p.relative_to(STATIC).as_posix()
        if p.is_file() and rel not in PAGES:
            out[f"static/{rel}"] = p.read_bytes()
    return out


def build_files(
    conn: sqlite3.Connection,
    gaz: Gazetteer,
    boundaries: dict,
    taken_at: datetime,
    last_check_at: str | None,
) -> dict[str, bytes]:
    """Every output file (relative POSIX path → bytes), built in memory."""
    taken_at = taken_at.astimezone(MANILA).replace(microsecond=0)
    d = taken_date(taken_at)
    files: dict[str, bytes] = {name: _page(name) for name in PAGES}
    files.update(_static_files())
    ws = windows(conn, taken_at)
    for w in ws:
        doc = build(conn, gaz, boundaries, w, taken_at)
        files[f"weeks/{w.from_date.isoformat()}.geojson"] = dumps(doc).encode("utf-8")
    rs = ranges(d)
    for r in rs:
        rng = Range(r.from_d, r.to_d)
        rows, coverage = load_rows(conn, rng)
        doc = compute(rows, rng, gaz, coverage, taken_at)
        files[f"stats/{r.id}.json"] = dumps(doc).encode("utf-8")
    files["boundaries.geojson"] = dumps(boundaries).encode("utf-8")
    snap = {
        "format": FORMAT,
        "taken_at": taken_at.isoformat(timespec="seconds"),
        "last_check_at": manila_iso(last_check_at),
        "snapshot_week": ws[-1].from_date.isoformat(),
        "weeks": [w.from_date.isoformat() for w in ws],
        "ranges": [
            {"id": r.id, "label": r.label, "from": r.from_d.isoformat(), "to": r.to_d.isoformat()}
            for r in rs
        ],
        "default_range": DEFAULT_RANGE,
        "veco_url": VECO_HOME,
        "code_url": PUBLIC_REPO_URL,
    }
    files["snapshot.json"] = dumps(snap).encode("utf-8")
    files["LICENSES.md"] = LICENSES.encode("utf-8")
    files[".nojekyll"] = b""
    return files


def find_leak(files: dict[str, bytes], chat_ids: list[int], token: str | None) -> str | None:
    """The kind of secret found in the output ("chat ID", "bot token"), or None. A chat ID
    matches only as a whole run of digits (a group's -100… ID also without its minus sign)."""
    pats = [re.compile(rb"(?<!\d)" + str(abs(i)).encode() + rb"(?!\d)") for i in set(chat_ids)]
    secret = token.encode("utf-8") if token else None
    for data in files.values():
        if secret and secret in data:
            return "bot token"
        if any(p.search(data) for p in pats):
            return "chat ID"
    return None


def check_out_dir(out: Path) -> str | None:
    """Why `out` can't take a snapshot, or None: it must be missing, empty (a `.git` alone counts
    as empty: the gh-pages worktree link), or hold a previous snapshot and nothing else."""
    if not out.exists():
        return None
    if not out.is_dir():
        return f"{out} is not a folder"
    names = {p.name for p in out.iterdir()}
    if names <= {".git"}:
        return None
    if "snapshot.json" not in names:
        shown = ", ".join(sorted(names)[:5])
        return f"{out} holds files and no previous snapshot ({shown}); pick an empty folder"
    foreign = sorted(names - TOP_LEVEL)
    for sub, pattern in PRUNED.items():
        d = out / sub
        if d.is_dir():
            foreign += [
                f"{sub}/{p.name}"
                for p in d.iterdir()
                if not (p.is_file() and pattern.fullmatch(p.name))
            ]
        elif d.exists():
            foreign.append(sub)
    if foreign:
        return f"{out} holds files that aren't part of a snapshot: {', '.join(foreign[:5])}"
    return None


def write(out: Path, files: dict[str, bytes]) -> tuple[int, int]:
    """Write every file, then delete week and stats files a previous snapshot left that aren't
    in `files`. `.git` and anything else are never touched. Returns (written, removed)."""
    for rel, data in files.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    removed = 0
    for sub in PRUNED:
        d = out / sub
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and f"{sub}/{p.name}" not in files:
                p.unlink()
                removed += 1
    return len(files), removed
