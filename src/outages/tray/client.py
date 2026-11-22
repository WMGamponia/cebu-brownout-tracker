"""The tray's client for `serve`'s `/healthz` and `/admin/*` (tray spec 5.5).

Every admin call carries the token from `data/admin.token` and never an `Origin` header. On a
403 the token is re-read once (serve restarted with a new one) and the call retried.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

import httpx

from outages import admintoken
from outages.config import MAP_PORT
from outages.map.admin import HEADER

log = logging.getLogger("outages")


class ServeClient:
    def __init__(
        self,
        base_url: str = f"http://127.0.0.1:{MAP_PORT}",
        data_dir: Path = Path("data"),
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.data_dir = data_dir
        self._http = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def healthz(self) -> bool:
        """True when *serve* answers: its JSON with a `schema`, or its 503 "not ready" (alive,
        with no database yet). Anything else on the port is not serve (port in use)."""
        try:
            r = self._http.get("/healthz")
            body = r.json()
        except (httpx.HTTPError, ValueError):
            return False
        if not isinstance(body, dict):
            return False
        if r.status_code == 200:
            return "schema" in body
        return r.status_code == 503 and "error" in body

    def _admin(self, method: str, path: str, body: dict | None = None) -> httpx.Response | None:
        for _ in range(2):
            token = admintoken.read(self.data_dir)
            if token is None:
                return None
            try:
                r = self._http.request(method, path, json=body, headers={HEADER: token})
            except httpx.HTTPError as e:
                log.warning("tray: %s %s failed: %s", method, path, e)
                return None
            if r.status_code != 403:
                return r
        return r

    def run_now(self) -> Literal["started", "busy", "error"]:
        r = self._admin("POST", "/admin/run")
        if r is not None and r.status_code == 202:
            return "started"
        if r is not None and r.status_code == 409:
            return "busy"
        return "error"

    def pending(self) -> list[dict]:
        r = self._admin("GET", "/admin/pending")
        if r is None or r.status_code != 200:
            return []
        try:
            items = r.json().get("pending")
        except ValueError:
            return []
        return items if isinstance(items, list) else []

    def decide(self, chat_id: int, approve: bool) -> Literal["ok", "not_pending", "error"]:
        r = self._admin("POST", "/admin/decide", {"chat_id": chat_id, "approve": approve})
        if r is not None and r.status_code == 200:
            return "ok"
        if r is not None and r.status_code == 404:
            return "not_pending"
        return "error"

    def stop(self) -> bool:
        r = self._admin("POST", "/admin/stop")
        return r is not None and r.status_code == 202
