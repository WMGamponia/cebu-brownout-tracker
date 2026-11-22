"""Admin routes for the tray (tray spec 5.5): run now, join requests, stop.

Mounted only on a loopback bind. Every request must carry the token in `HEADER` and no `Origin`
header, so only a program holding the token can call them, never a web page.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

HEADER = "X-Outages-Admin"


class AdminControl(Protocol):
    def run_now(self) -> bool:
        """Start a manual run; False when a run is already in progress. Returns at once."""

    def pending(self) -> list[dict]:
        """Pending join requests: `{"chat_id", "name", "requested_at"}`."""

    def decide(self, chat_id: int, approve: bool) -> bool:
        """Approve or deny; False when the chat isn't pending."""

    def stop(self) -> None:
        """Ask serve to finish any run in progress, then exit."""


@dataclass
class AdminConfig:
    token: str
    control: AdminControl


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def build_router(cfg: AdminConfig) -> APIRouter:
    router = APIRouter()

    def allowed(request: Request) -> bool:
        if "origin" in request.headers:
            return False
        token = request.headers.get(HEADER)
        return token is not None and secrets.compare_digest(token, cfg.token)

    @router.post("/admin/run", include_in_schema=False)
    def run(request: Request) -> JSONResponse:
        if not allowed(request):
            return _error(403, "forbidden")
        if not cfg.control.run_now():
            return _error(409, "already running")
        return JSONResponse({"started": True}, status_code=202)

    @router.get("/admin/pending", include_in_schema=False)
    def pending(request: Request) -> JSONResponse:
        if not allowed(request):
            return _error(403, "forbidden")
        return JSONResponse({"pending": cfg.control.pending()})

    @router.post("/admin/decide", include_in_schema=False)
    async def decide(request: Request) -> JSONResponse:
        if not allowed(request):
            return _error(403, "forbidden")
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "bad body")
        chat_id = body.get("chat_id") if isinstance(body, dict) else None
        approve = body.get("approve") if isinstance(body, dict) else None
        if type(chat_id) is not int or not isinstance(approve, bool):
            return _error(400, "bad body")
        if not await run_in_threadpool(cfg.control.decide, chat_id, approve):
            return _error(404, "not pending")
        return JSONResponse({"ok": True})

    @router.post("/admin/stop", include_in_schema=False)
    def stop(request: Request) -> JSONResponse:
        if not allowed(request):
            return _error(403, "forbidden")
        cfg.control.stop()
        return JSONResponse({"stopping": True}, status_code=202)

    return router
