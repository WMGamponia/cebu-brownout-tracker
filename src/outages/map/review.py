"""Street alias review routes (Streets v2 spec 7.9): a local page for deciding the queue.

Registered only on a loopback bind. Reads the DB read-only; writes only the queue file. POSTs
need the per-process token from the page and an `Origin` equal to the server's own. A failure
here is a 500 on the review route and never touches the map's routes."""

from __future__ import annotations

import logging
import secrets
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from outages.config import STREET_BUFFER_M
from outages.osm.extract import StreetIndex
from outages.streets import queue
from outages.streets.match import match_street, scope_for, search_towns
from outages.streets.store import scope_inputs
from outages.streets.suggest import groups

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "review_static"
REVIEWABLE = ("ok", "invalid")
TOKEN_META = "{{REVIEW_TOKEN}}"


@dataclass
class ReviewConfig:
    queue_path: Path
    osm_dir: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _mentions(r: dict) -> int:
    try:
        return int(r["mentions"] or 0)
    except ValueError:
        return 0


def build_router(
    cfg: ReviewConfig,
    ro: Callable[[], AbstractContextManager],
    boundaries: dict,
) -> APIRouter:
    router = APIRouter()
    features = {f["properties"]["psgc"]: f for f in boundaries["features"]}
    index_lock = threading.Lock()
    loaded: dict[str, StreetIndex | None] = {}

    def index() -> StreetIndex | None:
        with index_lock:  # loaded once, on the first item request
            if "i" not in loaded:
                loaded["i"] = StreetIndex.load(cfg.osm_dir)
            return loaded["i"]

    @router.get("/streets/review", include_in_schema=False)
    def page() -> HTMLResponse:
        html = (STATIC / "review.html").read_text(encoding="utf-8")
        return HTMLResponse(
            html.replace(TOKEN_META, cfg.token), headers={"Cache-Control": "no-store"}
        )

    @router.get("/streets/review/items")
    def items() -> Response:
        try:
            rows = queue.read(cfg.queue_path)
        except Exception:
            log.exception("review: /streets/review/items failed")
            return _error(500, "queue unreadable")
        out = [
            {
                "town_psgc": r["town_psgc"],
                "town": r["town"],
                "veco_key": r["veco_key"],
                "veco_name": r["veco_name"],
                "mentions": _mentions(r),
                "status": r["status"],
            }
            for r in rows
            if not r["decision"] and r["status"] in REVIEWABLE
        ]
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @router.get("/streets/review/item")
    def item(town: str = "", key: str = "") -> Response:
        try:
            return _item(town, key)
        except Exception:
            log.exception("review: /streets/review/item failed")
            return _error(500, "internal error")

    def _item(town: str, key: str) -> Response:
        rows = queue.read(cfg.queue_path)
        row = next((r for r in rows if (r["town_psgc"], r["veco_key"]) == (town, key)), None)
        if row is None or row["decision"]:
            return _error(409, "no such undecided row")
        with ro() as conn:
            g = next(
                (x for x in groups(conn, set(), set()) if (x.town_psgc, x.key) == (town, key)), None
            )
            if g is None:
                return _error(409, "no not_found mentions left for this name")
            h = sorted(g.mentions)[0][0]
            brgys, towns = scope_inputs(conn, [h]).get(h, ([], []))
        codes = brgys or [
            p for p, f in features.items() if f["properties"].get("town_psgc") in towns
        ]
        doc = {
            "veco_name": g.veco_name,
            "mentions": len(g.mentions),
            "areas": g.areas_examples[:2],
            "reason": row["reason"],
            "pick": row["pick"],
            "pick_source": row["pick_source"],
            "other_pick": row["other_pick"],
            "status": row["status"],
            "scope": {
                "type": "FeatureCollection",
                "features": [features[c] for c in codes if c in features],
            },
            "buffer_m": STREET_BUFFER_M,
            "candidates": [],
            "message": "",
        }
        idx = index()
        if idx is None:
            doc["message"] = "no OSM extract: run `streets fetch`; no street lines to show"
            return JSONResponse(doc)
        scope = scope_for(brgys, towns, boundaries)
        search = search_towns(brgys, towns, boundaries)
        for name in queue.choices(row):
            m = match_street(name, scope, search, idx, {})
            doc["candidates"].append(
                {
                    "name": name,
                    "is_pick": name == row["pick"],
                    "lines": [[list(p) for p in line] for line in m.lines],
                }
            )
        return JSONResponse(doc, headers={"Cache-Control": "no-store"})

    @router.post("/streets/review/decide")
    async def decide(request: Request) -> Response:
        if request.headers.get("origin") != f"http://{request.url.netloc}":
            return _error(403, "forbidden")
        try:
            body = await request.json()
        except ValueError:
            return _error(422, "expected JSON")
        if not isinstance(body, dict):
            return _error(422, "expected an object")
        token = body.get("token")
        if not isinstance(token, str) or not secrets.compare_digest(token, cfg.token):
            return _error(403, "forbidden")
        town, key, decision = body.get("town"), body.get("key"), body.get("decision")
        if not all(isinstance(x, str) and x for x in (town, key, decision)):
            return _error(422, "town, key and decision are required")
        try:
            queue.set_decision(cfg.queue_path, town, key, decision)
        except KeyError:
            return _error(409, "no such row")
        except queue.QueueConflict:
            return _error(409, "already decided")
        except queue.QueueBusy:
            return _error(409, "queue busy")
        except queue.QueueError:
            log.exception("review: queue unreadable")
            return _error(500, "queue unreadable")
        except ValueError as e:
            return _error(422, str(e))
        return Response(status_code=204)

    return router
