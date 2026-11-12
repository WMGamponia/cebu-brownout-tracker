"""`street_key`: one normalisation shared by VECO names and every OSM name tag (spec 4.3).

The word map is a module dict so dev-split tuning edits one place."""

from __future__ import annotations

import re
import unicodedata

_PREFIX = re.compile(r"^(?:portions?\s+of|part\s+of|along)\s+")
_PUNCT = re.compile(r"[.,\-'’]")

WORD_MAP: dict[str, str] = {
    "st": "street",
    "str": "street",
    "ave": "avenue",
    "av": "avenue",
    "rd": "road",
    "brgy": "barangay",
    "baranggay": "barangay",
    "bgy": "barangay",
    "ext": "extension",
    "hwy": "highway",
    "gen": "general",
    "gov": "governor",
    "sto": "santo",
    "sta": "santa",
}

# Never drawn without an alias: an OSM way of this name in scope is usually a different road
# (spec 4.5 step 6).
GENERIC_STREET_KEYS: frozenset[str] = frozenset(
    {
        "road",
        "main road",
        "barangay road",
        "national road",
        "national highway",
        "highway",
        "provincial road",
        "city road",
        "access road",
        "diversion road",
    }
)


def street_key(s: str) -> str:
    """Comparable key: no accents, casefolded, no "portion of", abbreviations expanded."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().strip()
    s = _PREFIX.sub("", s)
    s = _PUNCT.sub(" ", s)
    return " ".join(WORD_MAP.get(w, w) for w in s.split())
