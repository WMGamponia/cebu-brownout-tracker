"""Alias suggester: group `not_found` street names by (town, key), list the OSM names in
scope, ask a picker for the closest, queue the pick for the owner (Streets v2 spec 7)."""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import logging
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from outages import config, db
from outages.areas.llm import LLMUnavailable
from outages.config import (
    LLM_TIMEOUT_S,
    OLLAMA_URL,
    OSM_DIR_NAME,
    QWEN_MODEL,
    SUGGEST_BUDGET_GROUPS,
    SUGGEST_BUDGET_S,
    VECO_TOWNS,
    Paths,
)
from outages.map.build import load_boundaries
from outages.osm.extract import StreetIndex
from outages.streets import queue
from outages.streets.evaluate import EVAL_DIR, INPUT_LOG_NAME, TEST_ROWS, street_rows
from outages.streets.match import REJECTS_PATH, load_rejects, scope_for, search_towns
from outages.streets.normalize import GENERIC_STREET_KEYS, street_key
from outages.streets.store import scope_inputs

log = logging.getLogger("outages.streets.suggest")

SCOPE_MENTIONS = 20  # candidates come from the first this-many mentions' scopes
EXAMPLES = 3


@dataclass(frozen=True)
class Group:
    town_psgc: str
    key: str
    veco_name: str
    mentions: list[tuple[str, int]]
    areas_examples: list[str]


def groups(
    conn: sqlite3.Connection, test_ids: set[tuple[str, int]], skip: set[tuple[str, str]]
) -> list[Group]:
    """`not_found` street mentions grouped by (town, key), test rows and `skip` pairs left
    out, most mentions first, then key."""
    sql = "SELECT areas_hash, seq FROM street_matches WHERE status = 'not_found'"
    not_found = {(h, s) for h, s in conn.execute(sql)}
    by: dict[tuple[str, str], list] = {}
    for r in street_rows(conn):
        if (r.areas_hash, r.seq) not in not_found or (r.areas_hash, r.seq) in test_ids:
            continue
        key = street_key(r.name)
        if not key or key in GENERIC_STREET_KEYS or r.town_psgc is None:
            continue
        if (r.town_psgc, key) in skip:
            continue
        by.setdefault((r.town_psgc, key), []).append(r)
    out = []
    for (town, key), rows in by.items():
        rows.sort(key=lambda r: (r.areas_hash, r.seq))
        spellings = Counter(r.name for r in rows)
        veco = min(spellings, key=lambda n: (-spellings[n], n))
        examples: list[str] = []
        for r in rows:
            if r.areas_raw not in examples and len(examples) < EXAMPLES:
                examples.append(r.areas_raw)
        out.append(Group(town, key, veco, [(r.areas_hash, r.seq) for r in rows], examples))
    return sorted(out, key=lambda g: (-len(g.mentions), g.key, g.town_psgc))


def candidates(
    group: Group,
    index: StreetIndex,
    boundaries: dict,
    inputs: dict[str, tuple[list[str], list[str]]],
    k: int = 12,
) -> list[str]:
    """The k in-scope OSM names closest to the group's key (its own key excluded)."""
    by_key: dict[str, str] = {}
    for h, _ in sorted(group.mentions)[:SCOPE_MENTIONS]:
        if h not in inputs:
            continue
        brgys, towns = inputs[h]
        scope = scope_for(brgys, towns, boundaries)
        if scope is None:
            continue
        for n in index.names_in(search_towns(brgys, towns, boundaries), scope):
            nk = street_key(n)
            # never the group's own name (it would still not draw) nor a generic one ("Barangay
            # Road"): as an alias it would draw every way of that name in scope
            if nk and nk != group.key and nk not in GENERIC_STREET_KEYS:
                by_key.setdefault(nk, n)

    def rank(item: tuple[str, str]) -> tuple[float, str]:
        return (-SequenceMatcher(None, group.key, item[0]).ratio(), item[1])

    return [n for _, n in sorted(by_key.items(), key=rank)[:k]]


def log_inputs(path: Path, group: Group, now: datetime) -> None:
    """Append one line per mention shown to the picker (checked by the test-run guard)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for h, seq in group.mentions:
            row = {
                "areas_hash": h,
                "seq": seq,
                "town_psgc": group.town_psgc,
                "key": group.key,
                "at": now.isoformat(),
            }
            f.write(json.dumps(row) + "\n")


def load_test_ids(eval_dir: Path) -> set[tuple[str, int]]:
    path = eval_dir / TEST_ROWS
    if not path.is_file():
        return set()
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {(r["areas_hash"], r["seq"]) for r in rows}


# --- pickers (spec 7.3-7.4) -----------------------------------------------------------------

STREET_PROMPT_VERSION = "2"
REASON_WORDS = 15
DIFFLIB_CUTOFF = 0.75
CACHE_PATH = Path("data") / "streets" / "llm_cache.db"

PICK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "pick": {"type": ["integer", "null"], "minimum": 1, "maximum": 12},
        "reason": {"type": "string"},
    },
    "required": ["pick", "reason"],
}

STREET_SYSTEM = """\
You match a street name from a Cebu, Philippines power-interruption notice to the street names \
in OpenStreetMap for the same area. The notice was typed by hand and may have typos, \
abbreviations, missing or extra words, or numbers written as words.

You get the name as the notice wrote it, one or two notice texts it appears in, and a numbered \
list of OpenStreetMap street names in that area.

Answer {"pick": n, "reason": "..."} where n is the number of the listed name that is plainly \
the same road, or {"pick": null, "reason": "..."} if none is.
- Pick only when it is plainly the same road: a typo ("Gorordo Avnue" -> "Gorordo Avenue"), an \
abbreviation ("M.J. Cuenco" -> "Mariano Jesus Cuenco Avenue"), a missing or extra word, a \
numeral written as a word.
- Never pick because of a shared generic word alone ("Street", "Road", "Avenue", "Barangay").
- The distinctive word of the notice's name (e.g. "Ilaya", "Sangi", "Mary Ellen") must appear \
in the listed name, spelled the same or with a small typo. A main road that merely runs through \
the area ("Cebu South Road") is not a match for a different name.
- A name that adds or drops a qualifier is a different road: "... Extension", "Dumpsite Road", \
"Barangay ... Road", a road from one place to another, or a person's name with different \
initials or first name ("Pepito Drive" is not "Pedro Pepito Street").
- If two listed names fit equally well, answer null.
- If you are unsure, answer null. A wrong pick draws the wrong street on a public map; null \
costs nothing.
- The reason is at most 15 words."""


@dataclass(frozen=True)
class Pick:
    status: str  # ok | invalid | no_candidates
    pick: str | None
    reason: str


NO_CANDIDATES = Pick("no_candidates", None, "")


def render_prompt(group: Group, cands: list[str]) -> str:
    lines = [f'Name in the notice: "{group.veco_name}"', "", "Notice texts:"]
    lines += [f"- {' '.join(t.split())[:400]}" for t in group.areas_examples[:2]]
    lines += ["", "OpenStreetMap streets in this area:"]
    lines += [f"{i}. {n}" for i, n in enumerate(cands, 1)]
    return "\n".join(lines)


def difflib_pick(group: Group, cands: list[str]) -> Pick:
    """`get_close_matches` over the candidates' keys, cutoff 0.75 (spec 7.5)."""
    if not cands:
        return NO_CANDIDATES
    by_key: dict[str, str] = {}
    for n in cands:
        by_key.setdefault(street_key(n), n)
    close = difflib.get_close_matches(group.key, list(by_key), n=1, cutoff=DIFFLIB_CUTOFF)
    return Pick("ok", by_key[close[0]] if close else None, "difflib" if close else "")


def open_cache(path: Path = CACHE_PATH) -> sqlite3.Connection:
    """The suggester's own `llm_cache` (Phase 2's table), kept out of the outages DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, model TEXT NOT NULL,"
        " prompt_version TEXT NOT NULL, response_json TEXT NOT NULL, seconds REAL NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    conn.commit()
    return conn


class _Invalid(Exception):
    pass


def _parse(obj: object, cands: list[str]) -> Pick:
    if not isinstance(obj, dict) or "pick" not in obj or not isinstance(obj.get("reason"), str):
        raise _Invalid("expected {pick, reason}")
    n = obj["pick"]
    reason = " ".join(obj["reason"].split()[:REASON_WORDS])
    if n is None:
        return Pick("ok", None, reason)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(cands):
        raise _Invalid(f"pick {n!r} outside 1..{len(cands)}")
    return Pick("ok", cands[n - 1], reason)


class QwenPicker:
    name = "qwen"

    def __init__(
        self, conn: sqlite3.Connection, client: httpx.Client | None = None, model: str = QWEN_MODEL
    ) -> None:
        self.conn = conn
        self.client = client or httpx.Client(timeout=LLM_TIMEOUT_S)
        self.model = model

    def _key(self, prompt: str) -> str:
        raw = self.model + STREET_PROMPT_VERSION + prompt
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _unavailable(self, why: str) -> LLMUnavailable:
        return LLMUnavailable(f"Ollama not reachable at {OLLAMA_URL} ({why}); start it and rerun")

    def _call(self, prompt: str) -> object:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": STREET_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "think": False,
            "format": PICK_SCHEMA,
            "options": {"temperature": 0},
        }
        try:
            r = self.client.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=LLM_TIMEOUT_S)
        except httpx.TimeoutException:
            raise
        except httpx.TransportError as e:
            raise self._unavailable(type(e).__name__) from None
        if r.status_code != 200:
            raise self._unavailable(f"HTTP {r.status_code}")
        try:
            return json.loads(r.json()["message"]["content"])
        except (ValueError, KeyError, TypeError) as e:
            raise _Invalid(f"unparsable content: {type(e).__name__}") from None

    def _store(self, key: str, obj: object, seconds: float) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, model, prompt_version, response_json,"
                " seconds, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    self.model,
                    STREET_PROMPT_VERSION,
                    json.dumps(obj, ensure_ascii=False),
                    seconds,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def pick(self, group: Group, cands: list[str]) -> Pick:
        """Up to two calls; `invalid` after two bad answers. Raises LLMUnavailable."""
        if not cands:
            return NO_CANDIDATES
        prompt = render_prompt(group, cands)
        key = self._key(prompt)
        row = self.conn.execute("SELECT response_json FROM llm_cache WHERE key = ?", (key,))
        hit = row.fetchone()
        if hit:
            return _parse(json.loads(hit[0]), cands)
        timed_out = False
        for attempt in (1, 2):
            start = time.perf_counter()
            try:
                obj = self._call(prompt)
                p = _parse(obj, cands)
            except httpx.TimeoutException:
                log.warning("street picker: timed out (attempt %d)", attempt)
                timed_out = True
                continue
            except _Invalid as e:
                log.warning("street picker: invalid answer (attempt %d): %s", attempt, e)
                timed_out = False
                continue
            self._store(key, obj, time.perf_counter() - start)
            return p
        if timed_out:
            raise self._unavailable(f"timed out twice after {LLM_TIMEOUT_S} s")
        return Pick("invalid", None, "")

    def check(self) -> None:
        """Raise LLMUnavailable unless Ollama answers, before a run writes anything."""
        try:
            r = self.client.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        except httpx.HTTPError as e:
            raise self._unavailable(type(e).__name__) from None
        if r.status_code != 200:
            raise self._unavailable(f"HTTP {r.status_code}")


# --- run and apply (spec 7.1, 7.6-7.7) ------------------------------------------------------

QUEUE_NAME = "alias_queue.csv"
ALIAS_COLUMNS = (
    "town_psgc",
    "veco_key",
    "osm_name",
    "source",
    "model",
    "prompt_version",
    "decided",
)
REJECT_COLUMNS = ("town_psgc", "veco_key", "decided")
TOP_CANDIDATES = 5


@dataclass(frozen=True)
class RunSummary:
    new: int
    picked: int
    invalid: int
    no_candidates: int


@dataclass(frozen=True)
class ApplySummary:
    accepted_by_source: dict[str, int]
    rejected: int
    refused: list[str]
    decided_by_source: dict[str, int]  # by the queue row's pick_source: the acceptance-rate base
    took_pick_by_source: dict[str, int]  # `y` or the pick's own name


def queue_path(data_dir: Path) -> Path:
    return data_dir / "streets" / QUEUE_NAME


def _town_names() -> dict[str, str]:
    return {v: k for k, v in VECO_TOWNS.items()}


def run(
    conn: sqlite3.Connection,
    index: StreetIndex,
    boundaries: dict,
    picker,
    eval_dir: Path,
    data_dir: Path,
    now: datetime,
    limit: int | None = None,
    budget_s: float | None = None,
    primary: str | None = None,
    rejects_path: Path = REJECTS_PATH,
) -> RunSummary:
    """New groups only (not in the queue, not rejected, test rows removed); both arms pick,
    the primary fills `pick`. Ollama down raises before anything is written."""
    primary = primary or config.STREET_SUGGEST_PRIMARY
    picker.check()
    qpath = queue_path(data_dir)
    existing = queue.read(qpath)
    skip = {(r["town_psgc"], r["veco_key"]) for r in existing} | load_rejects(rejects_path)
    todo = groups(conn, load_test_ids(eval_dir), skip)
    if limit is not None:
        todo = todo[:limit]
    hashes = sorted({h for g in todo for h, _ in sorted(g.mentions)[:SCOPE_MENTIONS]})
    inputs = scope_inputs(conn, hashes)
    towns = _town_names()
    start = time.monotonic()
    rows: list[dict] = []
    log_path = data_dir / "streets" / INPUT_LOG_NAME
    for g in todo:
        if budget_s is not None and time.monotonic() - start >= budget_s:
            break
        cands = candidates(g, index, boundaries, inputs)
        if cands:  # logged before the model sees it, so a crash can't hide a mention
            log_inputs(log_path, g, now)
        q, d = picker.pick(g, cands), difflib_pick(g, cands)
        first, other = (q, d) if primary == "qwen" else (d, q)
        rows.append(
            {
                "town_psgc": g.town_psgc,
                "town": towns.get(g.town_psgc, g.town_psgc),
                "veco_key": g.key,
                "veco_name": g.veco_name,
                "mentions": str(len(g.mentions)),
                "pick": first.pick or "",
                "pick_source": primary if first.pick else "",
                "other_pick": other.pick or "",
                "candidates": queue.SEP.join(cands[:TOP_CANDIDATES]),
                "reason": q.reason if q.status == "ok" else "",
                "status": first.status if cands else "no_candidates",
                "decision": "",
            }
        )
    if rows:  # merged into a fresh read: decisions made while picking are kept
        queue.update(qpath, lambda fresh: queue.merge(fresh, rows))
    return RunSummary(
        new=len(rows),
        picked=sum(bool(r["pick"]) for r in rows),
        invalid=sum(r["status"] == "invalid" for r in rows),
        no_candidates=sum(r["status"] == "no_candidates" for r in rows),
    )


def _read_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows({c: r.get(c) or "" for c in columns} for r in rows)
    os.replace(tmp, path)


def apply(
    queue_file: Path,
    aliases_path: Path,
    rejects_path: Path,
    index: StreetIndex,
    boundaries: dict,
    conn: sqlite3.Connection,
    today: date,
) -> ApplySummary:
    """Decided queue rows → `street_aliases.csv` (with provenance) or the rejects file.
    A decision that isn't `y`/`n`/an in-scope name, or whose group is gone, is refused and
    stays in the queue. Raises queue.QueueError on an unreadable queue (nothing applied).

    Idempotent: a (town, key) already in the aliases or rejects file is not appended again, so
    a retry after QueueBusy (the queue held open) adds no duplicates."""
    with queue.locked(queue_file):
        return _apply(queue_file, aliases_path, rejects_path, index, boundaries, conn, today)


def _apply(
    queue_file: Path,
    aliases_path: Path,
    rejects_path: Path,
    index: StreetIndex,
    boundaries: dict,
    conn: sqlite3.Connection,
    today: date,
) -> ApplySummary:
    rows = queue.read(queue_file)
    by_group = {(g.town_psgc, g.key): g for g in groups(conn, set(), set())}
    hashes = sorted({h for g in by_group.values() for h, _ in sorted(g.mentions)[:SCOPE_MENTIONS]})
    inputs = scope_inputs(conn, hashes)
    aliases = _read_csv(aliases_path)
    for a in aliases:
        a["source"] = a.get("source") or "manual"
    rejects = _read_csv(rejects_path)
    done = {(a["town_psgc"], street_key(a["veco_key"])) for a in [*aliases, *rejects]}
    accepted: Counter[str] = Counter()
    decided: Counter[str] = Counter()
    took: Counter[str] = Counter()
    refused: list[str] = []
    keep: list[dict] = []
    n_rejected = 0
    stamp = today.isoformat()
    for r in rows:
        d = r["decision"]
        label = f"{r['town'] or r['town_psgc']} {r['veco_key']!r}"
        if not d:
            keep.append(r)
            continue
        if (r["town_psgc"], street_key(r["veco_key"])) in done:
            continue  # applied by an earlier --apply whose queue write failed: just drop it
        src = r["pick_source"] or "none"
        if d == "n":
            rejects.append(
                {"town_psgc": r["town_psgc"], "veco_key": r["veco_key"], "decided": stamp}
            )
            decided[src] += 1
            n_rejected += 1
            continue
        name = r["pick"] if d == "y" else d
        g = by_group.get((r["town_psgc"], r["veco_key"]))
        if not name:
            refused.append(f"{label}: `y` but nothing was picked")
        elif g is None:
            refused.append(f"{label}: no not_found mentions left for this name")
        else:
            in_scope = {street_key(n): n for n in candidates(g, index, boundaries, inputs, k=10**6)}
            if street_key(name) not in in_scope or (d != "y" and d not in in_scope.values()):
                refused.append(f"{label}: {d!r} is not y, n or a street name in its scope")
                g = None
        if g is None or not name:
            keep.append(r)
            continue
        is_pick = name == r["pick"]
        source = r["pick_source"] if is_pick and r["pick_source"] else "owner"
        aliases.append(
            {
                "town_psgc": r["town_psgc"],
                "veco_key": r["veco_key"],
                "osm_name": name,
                "source": source,
                "model": QWEN_MODEL if source == "qwen" else "",
                "prompt_version": STREET_PROMPT_VERSION if source == "qwen" else "",
                "decided": stamp,
            }
        )
        accepted[source] += 1
        decided[src] += 1
        took[src] += is_pick
    _write_csv(aliases_path, ALIAS_COLUMNS, aliases)
    _write_csv(rejects_path, REJECT_COLUMNS, rejects)
    queue.write(queue_file, keep)
    return ApplySummary(dict(accepted), n_rejected, refused, dict(decided), dict(took))


# --- serve hook (spec 7.8) ------------------------------------------------------------------


def suggest_new(
    paths: Paths,
    now: datetime,
    picker=None,
    boundaries: dict | None = None,
    budget_groups: int = SUGGEST_BUDGET_GROUPS,
    budget_s: float = SUGGEST_BUDGET_S,
) -> RunSummary | None:
    """New groups to the queue after a serve run, at most `budget_groups` or `budget_s`.

    None (one log line) when inert: the test split isn't frozen, no extract, no
    `street_matches`, or Ollama is down. Never raises."""
    try:
        if not (paths.root / EVAL_DIR / "FROZEN").is_file():
            log.info("street suggestions: inert until %s/FROZEN exists", EVAL_DIR.as_posix())
            return None
        index = StreetIndex.load(paths.data / OSM_DIR_NAME)
        if index is None:
            log.info("street suggestions: no OSM extract; skipped")
            return None
        if not paths.db.is_file():
            log.info("street suggestions: no database; skipped")
            return None
        conn = db.connect_ro(paths.db)
        try:
            has = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='street_matches'"
            ).fetchone()
            if not has:
                log.info("street suggestions: no street_matches; skipped")
                return None
            if picker is None:
                picker = QwenPicker(open_cache(paths.data / "streets" / "llm_cache.db"))
            if boundaries is None:
                boundaries = load_boundaries()
            s = run(
                conn,
                index,
                boundaries,
                picker,
                paths.root / EVAL_DIR,
                paths.data,
                now,
                limit=budget_groups,
                budget_s=budget_s,
            )
        finally:
            conn.close()
    except LLMUnavailable as e:
        log.info("street suggestions: skipped (%s)", e)
        return None
    except Exception:
        log.exception("street suggestions: failed; alerts are unaffected")
        return None
    log.info(
        "street suggestions: new %d, picked %d, invalid %d, no candidates %d",
        s.new,
        s.picked,
        s.invalid,
        s.no_candidates,
    )
    return s
