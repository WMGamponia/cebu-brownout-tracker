"""Paths, User-Agent, schedule and delays (spec 5, 7); Phase 2 gazetteer and LLM settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/OutageTracker)"
SITEMAP_URL = "https://www.visayanelectric.com/blog-posts-sitemap.xml"
SLUG_MARKER = "interruption"
CHECK_TIMES = ("07:00", "17:00")  # Asia/Manila
BACKFILL_DELAY_S = (3, 8)
CATCH_UP_AFTER_H = 12

# Phase 2: pinned PSGC gazetteer (faeldon/philippines-json-maps v1.1.1). The hash is ours, not
# the release's SHA256SUMS, which comes from the same place as the zip.
GAZETTEER_URL = (
    "https://github.com/faeldon/philippines-json-maps/releases/download/v1.1.1/geojson-medres.zip"
)
GAZETTEER_SHA256 = "ecf499258fd0fb03660af60332a0025aec985fc71076617ea78259715517b974"
# Display name -> town PSGC code (as the release writes it), for the 8 VECO franchise towns.
VECO_TOWNS: dict[str, str] = {
    "Cebu City": "730600000",
    "Mandaue City": "731300000",
    "Talisay City": "702250000",
    "City of Naga": "702234000",
    "Minglanilla": "702232000",
    "Consolacion": "702219000",
    "Liloan": "702227000",
    "San Fernando": "702241000",
}

OLLAMA_URL = "http://localhost:11434"
QWEN_MODEL = "qwen3:14b"
HAIKU_MODEL = "claude-haiku-5-5"
LLM_TIMEOUT_S = 120
# Set by the pre-registered eval decision rule (spec 2); env OUTAGES_ENTITY_EXTRACTOR overrides.
ENTITY_EXTRACTOR = os.environ.get("OUTAGES_ENTITY_EXTRACTOR", "rules")


@dataclass(frozen=True)
class Paths:
    """Where local data lives; everything under `data/` is gitignored."""

    root: Path

    @classmethod
    def default(cls) -> Paths:
        return cls(Path.cwd())

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def db(self) -> Path:
        return self.data / "outages.db"

    @property
    def logs(self) -> Path:
        return self.data / "logs"

    @property
    def reports(self) -> Path:
        return self.data / "reports"

    @property
    def gazetteer(self) -> Path:
        return self.data / "gazetteer"
