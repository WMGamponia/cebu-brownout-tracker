"""Paths, User-Agent, schedule and delays (spec 5, 7); Phase 2 gazetteer and LLM settings;
Phase 3 run times, `.env` lookup and Telegram settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

USER_AGENT = "CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/OutageTracker)"
SITEMAP_URL = "https://www.visayanelectric.com/blog-posts-sitemap.xml"
SLUG_MARKER = "interruption"
# Phase 3: each run is a check, then an alert run (Asia/Manila). Replaces 07:00 / 17:00.
RUN_TIMES = ("06:00", "20:00")
MISFIRE_GRACE_S = 6 * 3600  # a PC that slept through a run still runs it once on waking
BACKFILL_DELAY_S = (3, 8)
CATCH_UP_AFTER_H = 12
# 2026-11-14: Windows wait timeouts don't count time asleep, so one long scheduler wait can end
# hours late. A short watchdog job keeps waits short and runs a slot slept past the grace once.
WATCHDOG_MIN = 5
WATCHDOG_SLACK_MIN = 10  # leave a just-due slot to its cron job

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


# Phase 4: the map. Lapu-Lapu and Cordova are MECO's, drawn grey as "not covered".
MECO_TOWNS: dict[str, str] = {"Lapu-Lapu City": "731100000", "Cordova": "702223000"}
MAP_HOST = "127.0.0.1"
MAP_PORT = 8765
STALE_CHECK_H = 15  # runs are 14 h apart at most; older data means a run was missed

# Phase 6: the static demo snapshot links back to these.
PUBLIC_REPO_URL = "https://github.com/WMGamponia/cebu-brownout-tracker"
VECO_HOME = "https://www.visayanelectric.com/"

OLLAMA_URL = "http://localhost:11434"
QWEN_MODEL = "qwen3:14b"
HAIKU_MODEL = "claude-haiku-5-5"
LLM_TIMEOUT_S = 120
# Set by the pre-registered eval decision rule (spec 2); env OUTAGES_ENTITY_EXTRACTOR overrides.
ENTITY_EXTRACTOR = os.environ.get("OUTAGES_ENTITY_EXTRACTOR", "rules")
# Streets v2 spec 7.5: the arm with the higher dev accuracy fills the queue's `pick`;
# a tie → difflib.
STREET_SUGGEST_PRIMARY = "qwen"
# Spec 7.8: after each serve run, new street groups go to the queue, bounded per run.
STREETS_SUGGEST_AUTO = True
SUGGEST_BUDGET_GROUPS = 20
SUGGEST_BUDGET_S = 300


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


def dotenv_get(name: str, path: Path = Path(".env")) -> str | None:
    """`name` from the environment, else from a `KEY=value` line in `.env`; None when unset."""
    value = os.environ.get(name)
    if value:
        return value
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        k, sep, v = line.strip().partition("=")
        if sep and k.strip() == name:
            return v.strip().strip("'\"") or None
    return None


@dataclass(frozen=True)
class TelegramSettings:
    token: str = field(repr=False)  # never in logs or reprs
    allowed: frozenset[int]
    admin_chat_id: int | None


def _chat_ids(key: str) -> list[int]:
    raw = dotenv_get(key) or ""
    try:
        return [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise ValueError(f"{key} must be comma-separated integer chat IDs") from None


def telegram_settings() -> TelegramSettings | None:
    """Telegram settings from the environment or `.env`; None without a bot token."""
    token = dotenv_get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    admin = _chat_ids("TELEGRAM_ADMIN_CHAT_ID")
    if len(admin) > 1:
        raise ValueError("TELEGRAM_ADMIN_CHAT_ID must be one integer chat ID")
    allowed = frozenset(_chat_ids("TELEGRAM_ALLOWED_CHAT_IDS"))
    return TelegramSettings(token, allowed, admin[0] if admin else None)


# Phase 5: streets from one hand-refreshed OSM extract per town (spec 4.2-4.5).
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_SPACING_S = 10
OVERPASS_BACKOFF_S = (30, 120)
OSM_DIR_NAME = "osm"  # under data/; ODbL, never committed
STREET_BUFFER_M = 150
BBOX_PAD_M = 500
# Set by the pre-registered street eval gate (spec 5.4): test precision >= 0.95, >= 20 drawn.
# Phase 5 test split 2026-11-12: precision 0.923 (12 of 13 drawn), coverage 0.600 -> off.
# Streets v2 test split (evals/streets/results/2026-11-12-test.md): precision 1.000
# (68 of 68 drawn, 95 % Wilson [0.947, 1.000]), coverage 0.654 -> on.
STREETS_DRAW = True

# Phase 5: approve-to-join bot (spec 4.9).
PENDING_CAP = 20
SUBSCRIPTION_CAP = 10
RATE_LIMIT_PER_MIN = 20
RATE_LIMIT_MUTE_S = 600
SEND_SPACING_S = 0.05
PENDING_REPLY_EVERY_H = 24
