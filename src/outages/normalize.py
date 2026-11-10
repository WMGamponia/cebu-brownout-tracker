"""Text normalization for post lines (spec 5.2)."""

from __future__ import annotations

import re
import unicodedata

# U+2010 is what NFKC makes of U+2011 (`Kinasang‑an`); en/em dashes and the minus sign too.
_DASHES = str.maketrans({"‐": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_SPACE_BEFORE_COLON = re.compile(r"\s+:")
_PAREN_INNER_SPACE_L = re.compile(r"\(\s+")
_PAREN_INNER_SPACE_R = re.compile(r"\s+\)")
_WS = re.compile(r"\s+")


def normalize_line(s: str) -> str:
    """NFKC, ASCII spaces and dashes, no `**`, `Label :` → `Label:`, `( x)` → `(x)`, one space."""
    s = unicodedata.normalize("NFKC", s).translate(_DASHES)
    s = s.replace("**", "")
    s = _SPACE_BEFORE_COLON.sub(":", s)
    s = _PAREN_INNER_SPACE_L.sub("(", s)
    s = _PAREN_INNER_SPACE_R.sub(")", s)
    return _WS.sub(" ", s).strip()
