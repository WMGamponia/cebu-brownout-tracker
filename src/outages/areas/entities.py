"""Typed entities and the naming conventions every arm and every label goes through (spec 4.4,
`evals/areas/GUIDE.md`). Pure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from outages.areas.segment import Segmented

KINDS = ("street", "sitio_purok", "subdivision", "establishment", "other")


@dataclass(frozen=True)
class Entity:
    kind: str
    name: str


class Extractor(Protocol):
    name: str

    def extract(self, seg: Segmented) -> list[Entity]: ...


_CUE_RE = re.compile(
    r"^(?:portions?(?:\s+(?:of|along))?|along|including|near|as\s+well\s+as|specifically\s+at"
    r"|road\s+going\s+to|and|cor\.?|corner)(?:\s+|$)",
    re.IGNORECASE,
)
_PREFIX_RE = re.compile(r"^(?:sitios?|puroks?)\b\.?\s+(.+)$", re.IGNORECASE)
_ZONE_RE = re.compile(r"^zones?\s*\d", re.IGNORECASE)
_CORNER_RE = re.compile(r"\s+(?:corner|cor\.?)\s+", re.IGNORECASE)
_SHARED_HEAD_RE = re.compile(
    r"^(upper|lower|central)\s*(?:&|and)\s*(upper|lower|central)\s+(.+)$", re.IGNORECASE
)
_NUMBER_LIST_RE = re.compile(r"^(.*?)(\d+)((?:\s*(?:,|&|and)\s*\d+)+)$")


def _strip(name: str) -> str:
    name = " ".join(name.split()).strip(" ,;")
    while True:
        m = _CUE_RE.match(name)
        if not m:
            return name
        name = name[m.end() :].strip(" ,;")


def normalize_entity(e: Entity) -> list[Entity]:
    """Apply GUIDE.md's conventions; may return several entities (or none for empty text)."""
    out: list[Entity] = []
    for part in _CORNER_RE.split(_strip(e.name)):
        name, kind = _strip(part), e.kind
        m = _PREFIX_RE.match(name)
        if m:
            kind = "sitio_purok"
            if not m.group(1)[0].isdigit():
                name = _strip(m.group(1))
        if _ZONE_RE.match(name):
            kind = "sitio_purok"
        if not name:
            continue
        sh = _SHARED_HEAD_RE.match(name)
        if sh:
            out += [
                Entity(kind, f"{sh.group(1)} {sh.group(3)}"),
                Entity(kind, f"{sh.group(2)} {sh.group(3)}"),
            ]
            continue
        nl = _NUMBER_LIST_RE.match(name)
        if nl:
            head = nl.group(1)
            nums = [nl.group(2), *re.findall(r"\d+", nl.group(3))]
            out += [Entity(kind, f"{head}{n}".strip()) for n in nums]
            continue
        out.append(Entity(kind, name))
    return out


def entity_key(e: Entity) -> tuple[str, str]:
    """Scoring key: kind and the name casefolded, whitespace collapsed, no trailing `.`."""
    return e.kind, " ".join(e.name.casefold().split()).rstrip(".").strip()
