"""Blog sitemap XML -> interruption post refs (spec 2: slugs are never constructed)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from outages.config import SLUG_MARKER

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True)
class PostRef:
    url: str
    slug: str
    lastmod: date | None


def parse_sitemap(xml: str) -> list[PostRef]:
    """Posts whose slug contains `interruption`, sorted by lastmod (missing first) then slug."""
    refs = []
    for url in ET.fromstring(xml).iter(f"{_NS}url"):
        loc = (url.findtext(f"{_NS}loc") or "").strip()
        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        if SLUG_MARKER not in slug:
            continue
        lm = (url.findtext(f"{_NS}lastmod") or "").strip()
        refs.append(PostRef(loc, slug, date.fromisoformat(lm[:10]) if lm else None))
    return sorted(refs, key=lambda r: (r.lastmod or date.min, r.slug))
