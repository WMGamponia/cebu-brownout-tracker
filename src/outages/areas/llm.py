"""LLM entity extractors (spec 4.4): Qwen through local Ollama, Haiku through the Anthropic API
(eval only). Both read the same prompt and schema, cache every valid answer in `llm_cache`, and
fall back to the rules after two invalid answers. The API key is never logged or shown."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from outages.areas.entities import KINDS, Entity, normalize_entity
from outages.areas.entities_rules import RulesExtractor
from outages.areas.segment import Segmented, sub_label_kind
from outages.config import HAIKU_MODEL, LLM_TIMEOUT_S, OLLAMA_URL, QWEN_MODEL

log = logging.getLogger("outages.llm")

PROMPT_VERSION = "2"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
TOOL_NAME = "record_entities"

ENTITY_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "name": {"type": "string"},
                },
                "required": ["kind", "name"],
            },
        }
    },
    "required": ["entities"],
}

SYSTEM_PROMPT = """\
You extract named places from a power-interruption notice for Cebu, Philippines. The town and \
barangay names were already removed; what is left is the text that names streets, sitios and \
other places. Some lines start with a label such as "Streets:" or "Sitios:".

Return every named place, in order, as {"entities": [{"kind": ..., "name": ...}]}.

Kinds:
- street: streets, roads, avenues, highways, boulevards, drives, extensions, bypasses
- sitio_purok: sitios, puroks, zones, and named parts of a barangay such as "Upper Tingub", \
"Lower Calajoan", "Proper"
- subdivision: subdivisions, villages, compounds, homes, residences, townhouses, residential \
hills, condominiums
- establishment: companies, institutions, schools, churches and chapels, halls, offices, \
stations, plants, markets, stores, associations
- other: any other named place, or a name whose kind the text does not show

How to decide the kind:
0. A name ending in Street, St., Road, Rd., Avenue, Ave., Highway, Boulevard, Blvd, Drive or \
Extension is a street, wherever it appears.
1. A label that names one kind decides it for every value under it ("Streets:" -> street, \
"Sitios:"/"Puroks:" -> sitio_purok, "Subdivisions:"/"Villages:"/"Compounds:" -> subdivision, \
"Establishments:"/"Schools:"/"Religious Sites:"/"Government Facilities:" -> establishment). \
Under a label that mixes kinds, give each name its own kind.
2. Otherwise read it from the name ("... St." is a street, "... Inc." an establishment) and from \
its list: a run introduced by "Sitios"/"Puroks" stays sitio_purok until a name of another kind \
appears; a run of residential names stays subdivision.
3. If neither tells, use other.

Names:
- Copy each name exactly as written: keep typos, abbreviations, "Properties", "Ext.", "Inc.", \
and a parenthetical that qualifies the name. Never shorten, merge or translate a name. \
"Cebu South Road Properties" stays whole.
- Leave out cue words: "along", "including", "portion(s) of", "as well as", "specifically at", \
"road going to", a leading "and", "corner"/"cor.".
- Drop "Sitio"/"Purok" before a name ("Sitio Kawayan" -> "Kawayan"), but keep it before a \
number ("Purok 3"). "Zone" is always kept.
- Expand number lists: "Zone 3,4,5" -> "Zone 3", "Zone 4", "Zone 5".
- A company suffix after a comma belongs to the name ("Wilcon Depot, Inc." is one name). "&" \
inside a company name stays; "&" between names splits them.
- Return every name in a list, including the last one after "&" or "and", and names that are \
not streets inside a list of streets. Do not skip any.
- A name listed twice is returned twice.
- Return an empty list if the text names no place."""


class SchemaError(ValueError):
    """The model's answer is not `{"entities": [{"kind", "name"}]}`."""


class LLMUnavailable(RuntimeError):
    """The model can't be reached; the run stops rather than silently degrading."""


def validate(obj: object) -> list[Entity]:
    """The hand-written schema check (no dependency)."""
    if not isinstance(obj, dict) or not isinstance(obj.get("entities"), list):
        raise SchemaError("expected an object with an `entities` list")
    out: list[Entity] = []
    for item in obj["entities"]:
        if not isinstance(item, dict):
            raise SchemaError("entity is not an object")
        kind, name = item.get("kind"), item.get("name")
        if kind not in KINDS:
            raise SchemaError(f"unknown kind {kind!r}")
        if not isinstance(name, str) or not name.strip():
            raise SchemaError("empty name")
        out.append(Entity(kind, name.strip()))
    return out


def render_input(seg: Segmented) -> str:
    """The text the model reads: entity segments with their labels; never towns or barangays."""
    lines = []
    for s in seg.segments:
        text = " ".join(s.text.split())
        if not any(ch.isalnum() for ch in text):
            continue
        show = s.label and sub_label_kind(s.label) != "barangay"
        lines.append(f"{s.label}: {text}" if show else text)
    return "\n".join(lines)


def cache_key(model: str, text: str) -> str:
    return hashlib.sha256((model + PROMPT_VERSION + text).encode("utf-8")).hexdigest()


class _Invalid(Exception):
    pass


class _CachedLLM:
    """Shared cache, retry and fallback; subclasses implement `_call(text) -> object`."""

    name = "llm"
    model = ""

    def __init__(self, conn: sqlite3.Connection, client: httpx.Client | None) -> None:
        self.conn = conn
        self.client = client or httpx.Client(timeout=LLM_TIMEOUT_S)
        self.last_extractor = self.name
        self._rules = RulesExtractor()

    def _call(self, text: str) -> object:
        raise NotImplementedError

    def _unavailable(self, why: str) -> LLMUnavailable:
        raise NotImplementedError

    def _cached(self, key: str) -> list[Entity] | None:
        row = self.conn.execute("SELECT response_json FROM llm_cache WHERE key = ?", (key,))
        hit = row.fetchone()
        return validate(json.loads(hit[0])) if hit else None

    def _store(self, key: str, obj: object, seconds: float) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, model, prompt_version, response_json,"
                " seconds, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    key,
                    self.model,
                    PROMPT_VERSION,
                    json.dumps(obj, ensure_ascii=False),
                    seconds,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )

    def _ask(self, text: str) -> list[Entity] | None:
        """Up to two calls; None after two invalid answers. Raises LLMUnavailable."""
        timed_out = False
        for attempt in (1, 2):
            start = time.perf_counter()
            try:
                obj = self._call(text)
                ents = validate(obj)
            except httpx.TimeoutException:
                log.warning("%s: timed out (attempt %d)", self.name, attempt)
                timed_out = True
                continue
            except (SchemaError, _Invalid) as e:
                log.warning("%s: invalid answer (attempt %d): %s", self.name, attempt, e)
                timed_out = False
                continue
            self._store(cache_key(self.model, text), obj, time.perf_counter() - start)
            return ents
        if timed_out:
            raise self._unavailable(f"timed out twice after {LLM_TIMEOUT_S} s")
        return None

    def extract(self, seg: Segmented) -> list[Entity]:
        text = render_input(seg)
        self.last_extractor = self.name
        if not text:
            return []
        raw = self._cached(cache_key(self.model, text))
        if raw is None:
            raw = self._ask(text)
        if raw is None:
            self.last_extractor = "rules_fallback"
            return self._rules.extract(seg)
        return [x for e in raw for x in normalize_entity(e)]


class OllamaExtractor(_CachedLLM):
    name = "qwen"

    def __init__(
        self, conn: sqlite3.Connection, client: httpx.Client | None = None, model: str = QWEN_MODEL
    ) -> None:
        self.model = model
        super().__init__(conn, client)

    def _unavailable(self, why: str) -> LLMUnavailable:
        return LLMUnavailable(
            f"Ollama not reachable at {OLLAMA_URL} ({why}); start it or set ENTITY_EXTRACTOR=rules"
        )

    def _call(self, text: str) -> object:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "think": False,
            "format": ENTITY_SCHEMA,
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


class AnthropicExtractor(_CachedLLM):
    name = "haiku"

    def __init__(
        self,
        conn: sqlite3.Connection,
        api_key: str,
        client: httpx.Client | None = None,
        model: str = HAIKU_MODEL,
    ) -> None:
        self.model = model
        self.api_key = api_key
        super().__init__(conn, client)

    def __repr__(self) -> str:
        return f"AnthropicExtractor(model={self.model!r})"

    def _unavailable(self, why: str) -> LLMUnavailable:
        return LLMUnavailable(f"Anthropic API failed ({why}); the haiku arm is eval only")

    def _call(self, text: str) -> object:
        body = {
            "model": self.model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": text}],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Record the named places found in the text.",
                    "input_schema": ENTITY_SCHEMA,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            r = self.client.post(ANTHROPIC_URL, json=body, headers=headers, timeout=LLM_TIMEOUT_S)
        except httpx.TimeoutException:
            raise
        except httpx.TransportError as e:
            raise self._unavailable(type(e).__name__) from None
        if r.status_code != 200:
            raise self._unavailable(f"HTTP {r.status_code}")
        for block in r.json().get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
                return block.get("input")
        raise _Invalid("no tool_use block")


def _dotenv_key(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and k.strip() == "ANTHROPIC_API_KEY":
            return v.strip().strip("'\"") or None
    return None


def anthropic_from_env(conn: sqlite3.Connection) -> AnthropicExtractor | None:
    """The Haiku arm, or None (the eval writes `haiku: skipped`) when no key is set."""
    key = os.environ.get("ANTHROPIC_API_KEY") or _dotenv_key(Path(".env"))
    return AnthropicExtractor(conn, key) if key else None
