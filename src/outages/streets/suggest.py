"""Alias suggester: group `not_found` street names by (town, key), list the OSM names in
scope, ask a picker for the closest, queue the pick for the owner (Streets v2 spec 7)."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from outages.areas.llm import LLMUnavailable
from outages.config import LLM_TIMEOUT_S, OLLAMA_URL, QWEN_MODEL
from outages.osm.extract import StreetIndex
from outages.streets.evaluate import TEST_ROWS, street_rows
from outages.streets.match import scope_for, search_towns
from outages.streets.normalize import GENERIC_STREET_KEYS, street_key

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
            if nk and nk != group.key:
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
