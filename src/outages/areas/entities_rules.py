"""Rule-based typed entity extractor (spec 4.4). Pure. Suffix and prefix lists are tuned on
`evals/areas/dev.jsonl` only."""

from __future__ import annotations

import re

from outages.areas.entities import Entity, normalize_entity
from outages.areas.segment import Segment, Segmented, sub_label_kind

_W = r"(?:^|[\s-])"
_STREET = re.compile(
    _W + r"(?:st|street|rd|road|ave|avenue|highway|hwy|blvd|boulevard|ext|extn|extension"
    r"|drive|dr|bypass)\.?$",
    re.IGNORECASE,
)
_SUBDIVISION = re.compile(
    _W + r"(?:subd|subdivision|village|vill|ville|homes|townhomes|townhouses?|compound|compd"
    r"|estates?|residences?|heights|hills|condominium)\.?$",
    re.IGNORECASE,
)
_ESTABLISHMENT = re.compile(
    _W + r"(?:inc|incorporated|corp|corporation|co|company|school|elem|college|academy"
    r"|chapel|church|parish|hospital|center|centre|bank|office|building|hall|station|market"
    r"|coop|cooperative|association|foundation|enterprises|services|industries|pharmacy)\.?$",
    re.IGNORECASE,
)
# A company suffix that VECO writes after a comma (`Wilcon Depot, Inc.`) belongs to the name.
_COMPANY_TAIL = re.compile(r"^(?:inc|incorporated|corp|co)\b\.?", re.IGNORECASE)
_VILLA = re.compile(r"^villa\s+\S", re.IGNORECASE)
_STICKY = re.compile(r"^(?:sitios?|puroks?|zones?)\b", re.IGNORECASE)
_PART_OF = re.compile(r"^(?:upper|lower|central)\s+\S", re.IGNORECASE)
_CUE_SPLIT = re.compile(
    r"\s+(?=(?:along|including|portions?\s+(?:of|along)|near)\b)", re.IGNORECASE
)
_COMMA_SPLIT = re.compile(r"\s*[,;]\s*")
_AND_SPLIT = re.compile(r"\s*(?:&|\band\b)\s*", re.IGNORECASE)


def _suffix_kind(name: str) -> str | None:
    for kind, pat in (
        ("street", _STREET),
        ("subdivision", _SUBDIVISION),
        ("establishment", _ESTABLISHMENT),
    ):
        if pat.search(name.strip()):
            return kind
    if _VILLA.match(name):
        return "subdivision"
    return None


def _join_company_tails(pieces: list[str]) -> list[str]:
    out: list[str] = []
    for p in pieces:
        if out and _COMPANY_TAIL.match(p):
            out[-1] = f"{out[-1]}, {p}"
        else:
            out.append(p)
    return out


def _split_and(piece: str) -> list[str]:
    """Split on `&`/`and`, except inside a company or subdivision name
    (`Alfonso & Sons Development Corporation`): left part without a suffix, right with one."""
    parts = _AND_SPLIT.split(piece)
    out: list[str] = []
    for p in parts:
        if (
            out
            and _suffix_kind(p) in ("establishment", "subdivision")
            and not _suffix_kind(out[-1])
        ):
            sep = re.search(
                re.escape(out[-1]) + r"(\s*(?:&|\band\b)\s*)" + re.escape(p), piece, re.IGNORECASE
            )
            out[-1] = f"{out[-1]}{sep.group(1) if sep else ' & '}{p}"
        else:
            out.append(p)
    return out


def _fix_fragments(items: list[str]) -> list[str]:
    """`Zone 3`, `4` -> `Zone 3`, `Zone 4`; `Lower`, `Central Calajoan` -> `Lower Calajoan`, …"""
    out: list[str] = []
    for i, it in enumerate(items):
        if it.isdigit() and out and re.search(r"\d+$", out[-1]):
            out.append(re.sub(r"\d+$", it, out[-1]))
        elif it.casefold() in ("upper", "lower", "central") and i + 1 < len(items):
            nxt = items[i + 1].split(maxsplit=1)
            out.append(f"{it} {nxt[1]}" if len(nxt) == 2 else it)
        else:
            out.append(it)
    return out


def _items(text: str, split_and: bool) -> list[str]:
    pieces: list[str] = []
    for chunk in _CUE_SPLIT.split(text):
        pieces += [p for p in _COMMA_SPLIT.split(chunk) if p.strip()]
    pieces = _join_company_tails(pieces)
    if split_and:
        pieces = [x for p in pieces for x in _split_and(p)]
    pieces = [p.strip() for p in pieces if normalize_entity(Entity("other", p))]
    return _fix_fragments(pieces)


def _free_kinds(items: list[str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    sticky = False
    for it in items:
        bare = normalize_entity(Entity("other", it))
        name = bare[0].name if bare else it
        kind = _suffix_kind(name)
        if kind:
            sticky = False
        elif (
            _STICKY.match(re.sub(r"^\W+", "", it.split(" of ")[-1]))
            or bare
            and bare[0].kind == "sitio_purok"
        ):
            sticky, kind = True, "sitio_purok"
        elif sticky or _PART_OF.match(name):
            kind = "sitio_purok"
        else:
            kind = "other"
        out.append((kind, it))
    return out


class RulesExtractor:
    name = "rules"

    def _segment(self, s: Segment) -> list[Entity]:
        fixed = sub_label_kind(s.label) if s.label else None
        if fixed == "barangay":
            fixed = None
        items = _items(s.text, split_and=fixed != "establishment")
        pairs = [(fixed, it) for it in items] if fixed else _free_kinds(items)
        return [x for kind, it in pairs for x in normalize_entity(Entity(kind, it))]

    def extract(self, seg: Segmented) -> list[Entity]:
        return [e for s in seg.segments for e in self._segment(s)]
