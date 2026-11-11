"""Split `areas_raw` into towns, barangay tokens and labelled entity segments (spec 4.2). Pure.

Layouts VECO uses:
- city-first: `Portion of Cebu City (Tisa) including portions of …`
- barangay-first: `Portion of Banilad & Talamban, Cebu City, along portions of …`
- structured: `Portion of Cebu City` then `Barangays: …`, `Streets: …`, … lines, sometimes with
  no line break before a sub-label (`Jubay, CotcotStreets: …`).
Anything else is `other`, with a warning. Text is never dropped: what isn't a town or a
barangay token stays in a segment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Words a sub-label is built from, joined by `/` or `&` (`Villages / Subdivisions / Compounds`).
# VECO uses ~130 spellings of them, so labels are matched by word, not by a fixed list.
_ATOM_KIND: dict[str, str | None] = {
    "barangays": "barangay",
    "barangay": "barangay",
    "streets": "street",
    "street": "street",
    "roads": "street",
    "road": "street",
    "drives": "street",
    "sitios": "sitio_purok",
    "sitio": "sitio_purok",
    "puroks": "sitio_purok",
    "purok": "sitio_purok",
    "zones": "sitio_purok",
    "zone": "sitio_purok",
    "subdivisions": "subdivision",
    "subdivision": "subdivision",
    "villages": "subdivision",
    "village": "subdivision",
    "compounds": "subdivision",
    "compound": "subdivision",
    "residences": "subdivision",
    "residential areas": "subdivision",
    "residential": "subdivision",
    "estates": "subdivision",
    "condominiums": "subdivision",
    "establishments": "establishment",
    "establishment": "establishment",
    "institutions": "establishment",
    "institution": "establishment",
    "educational institutions": "establishment",
    "religious institutions": "establishment",
    "schools": "establishment",
    "school": "establishment",
    "learning centers": "establishment",
    "churches": "establishment",
    "church": "establishment",
    "chapels": "establishment",
    "chapel": "establishment",
    "places of worship": "establishment",
    "religious sites": "establishment",
    "hospitals": "establishment",
    "health facilities": "establishment",
    "health centers": "establishment",
    "medical centers": "establishment",
    "medical": "establishment",
    "pharmacies": "establishment",
    "government facilities": "establishment",
    "government buildings": "establishment",
    "government": "establishment",
    "public utilities": "establishment",
    "utilities": "establishment",
    "barangay facilities": "establishment",
    "barangay facility": "establishment",
    "facilities": "establishment",
    "landmarks": "establishment",
    "buildings": "establishment",
    "complexes": "establishment",
    "organizations": "establishment",
    "businesses": "establishment",
    "commercial entities": "establishment",
    "commercial buildings": "establishment",
    "homeowners associations": "establishment",
    "associations": "establishment",
    "media": "establishment",
    "telecommunications": "establishment",
    # neutral: say nothing about the kind
    "areas covered": None,
    "areas": None,
    "locations": None,
}
SUB_LABELS: tuple[str, ...] = tuple(sorted(_ATOM_KIND, key=lambda a: (-len(a), a)))

_ATOM = "|".join(
    r"\s+".join(w if w == "of" else w.capitalize() for w in a.split()) for a in SUB_LABELS
)
_LABEL_BODY = rf"(?:Other\s+)?(?:{_ATOM})(?:\s*[/&]\s*(?:{_ATOM}))*\.?"
# A known label: at the start, after whitespace, or right after a letter or `.` (no line break
# before it: `CotcotStreets:`, `Ward IVStreets:`), and always followed by `:`.
_LABEL_RE = re.compile(rf"(?<![^\s.A-Za-z])({_LABEL_BODY})\s*:")
# Any other `Words:` at the start of a line is an unknown sub-label (kept, with a warning).
_UNKNOWN_RE = re.compile(r"(?m)^[ \t]*([A-Z][^:\n]{0,40}?)\s*:")

_PREFIX_RE = re.compile(r"^(?:a\s+)?portions?\s+of\s+", re.IGNORECASE)
_CUE_RE = re.compile(r"\b(?:along|including|portions?\s+(?:of|along))\b", re.IGNORECASE)
_SPLIT_RE = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)

_TOWN_FORMS = {
    "cebu city": "Cebu City",
    "city of cebu": "Cebu City",
    "mandaue city": "Mandaue City",
    "mandaue": "Mandaue City",
    "city of mandaue": "Mandaue City",
    "talisay city": "Talisay City",
    "talisay": "Talisay City",
    "city of talisay": "Talisay City",
    "city of naga": "City of Naga",
    "naga city": "City of Naga",
    "naga": "City of Naga",
    "minglanilla": "Minglanilla",
    "consolacion": "Consolacion",
    "liloan": "Liloan",
    "lilo-an": "Liloan",
    "san fernando": "San Fernando",
    "san fernado": "San Fernando",  # posted once
}
_TOWN_ALT = "|".join(
    sorted((re.escape(f).replace(r"\ ", r"\s+") for f in _TOWN_FORMS), key=len, reverse=True)
)
_TOWN_SEARCH_RE = re.compile(rf"\b({_TOWN_ALT})\b", re.IGNORECASE)
_GLUED_TOWN_RE = re.compile(rf"\s({_TOWN_ALT})$", re.IGNORECASE)
_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿]")


@dataclass(frozen=True)
class Segment:
    label: str | None
    text: str


@dataclass(frozen=True)
class Segmented:
    layout: str
    towns: list[str]
    barangay_tokens: list[str]
    segments: list[Segment]
    warnings: list[str]


def _collapse(s: str) -> str:
    return " ".join(s.split())


def canonical_town(text: str) -> str | None:
    """A VECO town's display name (`config.VECO_TOWNS` key) for a posted spelling, else None."""
    return _TOWN_FORMS.get(_collapse(text).strip(" .,").casefold())


def sub_label_kind(label: str) -> str | None:
    """The one kind a sub-label names (`barangay` for `Barangays:`), or None if mixed/unknown."""
    body = re.sub(r"^Other\s+", "", label.strip().rstrip("."))
    kinds = set()
    for atom in re.split(r"\s*[/&]\s*", body):
        key = _collapse(atom).casefold()
        if key not in _ATOM_KIND:
            return None
        if _ATOM_KIND[key] is not None:
            kinds.add(_ATOM_KIND[key])
    return kinds.pop() if len(kinds) == 1 else None


def _split_names(s: str) -> list[str]:
    return [p.strip(" .") for p in _SPLIT_RE.split(s) if p.strip(" .")]


def _split_spans(s: str) -> list[tuple[str, int]]:
    """Like `_split_names`, with the end offset of each name in `s`."""
    out, pos = [], 0
    for sep in [*_SPLIT_RE.finditer(s), None]:
        end = sep.start() if sep else len(s)
        name = s[pos:end].strip(" .")
        if name:
            out.append((name, end))
        pos = sep.end() if sep else len(s)
    return out


def _unglue_towns(spans: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """`Taptap Cebu City` (no comma before the town) -> `Taptap`, `Cebu City`."""
    out = []
    for name, end in spans:
        m = None if canonical_town(name) else _GLUED_TOWN_RE.search(name)
        if m:
            out.append((name[: m.start()].strip(" ."), end - len(name) + m.start()))
            out.append((m.group(1), end))
        else:
            out.append((name, end))
    return out


def _dedupe(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(xs))


@dataclass
class _Free:
    layout: str
    towns: list[str]
    tokens: list[str]
    rest: str
    warnings: list[str]


def _parse_free(line: str) -> _Free:
    """Parse one (joined) line of a city-first or barangay-first text."""
    m = _PREFIX_RE.match(line)
    if m:
        after = line[m.end() :]
        cf = re.match(r"([^(]*?)\s*\(([^)]*)\)\s*(.*)$", after, re.DOTALL)
        if cf and cf.group(1):
            parts = _split_names(cf.group(1))
            if parts and all(canonical_town(p) for p in parts):
                rest = cf.group(3).strip(" ,")
                warns = ["second portion clause"] if _PREFIX_RE.match(rest) else []
                towns = _dedupe([canonical_town(p) or "" for p in parts])
                return _Free("city_first", towns, _split_names(cf.group(2)), rest, warns)
        cue = _CUE_RE.search(after)
        head = after[: cue.start()] if cue else after
        spans = _unglue_towns(_split_spans(head))
        towns = [canonical_town(p) for p, _ in spans]
        if any(towns):
            cue_rest = after[cue.start() :] if cue else ""
            lead = next(i for i, t in enumerate(towns) if not t) if not all(towns) else len(towns)
            if lead and not any(towns[lead:]):
                # Towns first, then their barangays: `Mandaue City & Consolacion, Basak, …`.
                tokens, rest = [p for p, _ in spans[lead:]], cue_rest.strip()
            else:
                # Barangays before the (last) town; anything after it is entity text.
                last = max(i for i, t in enumerate(towns) if t)
                rest = (head[spans[last][1] :] + cue_rest).strip(" ,")
                tokens = [p for (p, _), t in zip(spans[:last], towns[:last], strict=True) if not t]
            return _Free("barangay_first", _dedupe([t for t in towns if t]), tokens, rest, [])
    base = line[m.end() :] if m else line
    head = _CUE_RE.split(base.split("(", 1)[0], maxsplit=1)[0]
    towns = _dedupe([canonical_town(t.group(1)) or "" for t in _TOWN_SEARCH_RE.finditer(head)])
    return _Free("other", towns, [], line, ["other layout"])


def _labels(text: str) -> list[tuple[int, int, str, bool]]:
    """(start, end, label, known) of every sub-label, in order."""
    found = [(m.start(1), m.end(), m.group(1), True) for m in _LABEL_RE.finditer(text)]
    for m in _UNKNOWN_RE.finditer(text):
        if not any(s < m.end() and m.start(1) < e for s, e, _, _ in found):
            found.append((m.start(1), m.end(), _collapse(m.group(1)), False))
    return sorted(found)


def _finish(layout, towns, tokens, segments, warnings) -> Segmented:
    if not towns:
        warnings.append("no town found")
    if not tokens:
        warnings.append("no barangay token")
    return Segmented(layout, towns, tokens, segments, warnings)


def segment(areas_raw: str) -> Segmented:
    text = _ZERO_WIDTH_RE.sub("", areas_raw).replace("\r", "").strip()
    labels = _labels(text)
    if not labels:
        free = _parse_free(_collapse(text))
        segs = [Segment(None, free.rest)] if free.rest else []
        return _finish(free.layout, free.towns, free.tokens, segs, free.warnings)

    head = _collapse(text[: labels[0][0]])
    free = _parse_free(head) if head else _Free("other", [], [], "", [])
    towns, tokens, warnings = list(free.towns), list(free.tokens), list(free.warnings)
    warnings = [w for w in warnings if w != "other layout"]
    segments: list[Segment] = []
    head_parts = _split_names(head)
    if free.layout == "other" and head_parts and all(canonical_town(p) for p in head_parts):
        towns = _dedupe([canonical_town(p) or "" for p in head_parts])  # `Mandaue City` alone
    elif free.layout == "other" and head:
        segments.append(Segment(None, head))
        warnings.append("unparsed structured head")
    elif free.rest:
        segments.append(Segment(None, free.rest))
    for i, (_, end, label, known) in enumerate(labels):
        stop = labels[i + 1][0] if i + 1 < len(labels) else len(text)
        value = _collapse(text[end:stop])
        if not known:
            warnings.append(f"unknown sub-label: {label}")
        if known and sub_label_kind(label) == "barangay":
            cue = _CUE_RE.search(value)
            tokens += _split_names(value[: cue.start()] if cue else value)
            if cue:
                segments.append(Segment(None, value[cue.start() :]))
        elif value:
            segments.append(Segment(label.rstrip() if known else label, value))
    return _finish("structured", towns, tokens, segments, warnings)
