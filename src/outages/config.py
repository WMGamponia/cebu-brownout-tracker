"""Paths, User-Agent, schedule and delays (spec 5, 7)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/OutageTracker)"
SITEMAP_URL = "https://www.visayanelectric.com/blog-posts-sitemap.xml"
SLUG_MARKER = "interruption"
CHECK_TIMES = ("07:00", "17:00")  # Asia/Manila
BACKFILL_DELAY_S = (3, 8)
CATCH_UP_AFTER_H = 12


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
