"""Polite HTTP client for visayanelectric.com (spec 7). The only module that imports httpx.

One request at a time, a named User-Agent, a 30 s timeout, one retry after 30 s on a network
error or 5xx, and a random pause between consecutive requests during backfill.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable

import httpx

from outages.config import USER_AGENT

log = logging.getLogger(__name__)

TIMEOUT_S = 30
RETRY_AFTER_S = 30


class Fetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        delay_s: tuple[float, float] = (0, 0),
    ) -> None:
        self._client = client or httpx.Client()
        self._sleep = sleep
        self._delay = delay_s
        self._first = True

    def _once(self, url: str) -> httpx.Response | None:
        try:
            return self._client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_S,
                follow_redirects=True,
            )
        except httpx.TransportError as e:
            log.warning("fetch %s: %s", url, e)
            return None

    def get(self, url: str) -> str | None:
        """The body as text, or None on a non-200 (after one retry for network errors/5xx)."""
        if not self._first and self._delay[1] > 0:
            self._sleep(random.uniform(*self._delay))
        self._first = False
        r = self._once(url)
        if r is None or r.status_code >= 500:
            log.info("retrying %s in %ss", url, RETRY_AFTER_S)
            self._sleep(RETRY_AFTER_S)
            r = self._once(url)
        if r is None:
            return None
        if r.status_code != 200:
            log.warning("fetch %s: HTTP %s", url, r.status_code)
            return None
        return r.text

    def close(self) -> None:
        self._client.close()
