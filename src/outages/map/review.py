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

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from outages.config import STREET_BUFFER_M
from outages.osm.extract import StreetIndex
from outages.streets import queue
from outages.streets.match import match_street, scope_for, search_towns
from outages.streets.normalize import GENERIC_STREET_KEYS, street_key
from outages.streets.store import scope_inputs
from outages.streets.suggest import SCOPE_MENTIONS, candidates, groups

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "review_static"
REVIEWABLE = ("ok", "invalid")
TOKEN_META = "{{REVIEW_TOKEN}}"


@dataclass
class ReviewConfig:
    queue_path: Path
    osm_dir: Path
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


@dataclass
class _Context:
    """One undecided row's group and the scope its candidates come from."""

    row: dict
    group: object  # suggest.Group
    inputs: dict
    codes: list[str]  # barangays shown (union of the mentions' scopes)
    search: list[str]  # towns searched
    scope: object  # geo.Scope of `codes`, as the lines are clipped


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _loopback_host(request: Request) -> None:
    """Refuse any Host that isn't a loopback name: a DNS-rebinding page (evil.example → 127.0.0.1)
    would otherwise be same-origin, read the token and pass the Origin check (review #5)."""
    if (request.url.hostname or "").lower() not in LOOPBACK_NAMES:
        raise HTTPException(status_code=403, detail="forbidden")


def build_router(
    cfg: ReviewConfig,
    ro: Callable[[], AbstractContextManager],
    boundaries: dict,
) -> APIRouter:
    router = APIRouter(dependencies=[Depends(_loopback_host)])
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
        try:
            # Live, not the CSV's count (frozen when the row was queued): a name VECO keeps
            # using climbs past the page's filter, and a name with nothing left to draw drops.
            with ro() as conn:
                live = {(g.town_psgc, g.key): len(g.mentions) for g in groups(conn, set(), set())}
        except Exception:
            log.exception("review: /streets/review/items failed")
            return _error(500, "internal error")
        out = [
            {
                "town_psgc": r["town_psgc"],
                "town": r["town"],
                "veco_key": r["veco_key"],
                "veco_name": r["veco_name"],
                "mentions": live[(r["town_psgc"], r["veco_key"])],
                "status": r["status"],
                "has_pick": bool(r["pick"]),
            }
            for r in rows
            if not r["decision"]
            and r["status"] in REVIEWABLE
            and (r["town_psgc"], r["veco_key"]) in live
        ]
        # Owner's order (2026-11-13): rows with a pick first, each part by mentions.
        out.sort(key=lambda i: (not i["has_pick"], -i["mentions"]))
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @router.get("/streets/review/item")
    def item(town: str = "", key: str = "") -> Response:
        try:
            return _item(town, key)
        except Exception:
            log.exception("review: /streets/review/item failed")
            return _error(500, "internal error")

    def _context(town: str, key: str) -> _Context | JSONResponse:
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
            # The same mentions the candidates came from (suggest.candidates), so every
            # candidate can be drawn: their placed barangays, else their towns.
            hashes = [h for h, _ in sorted(g.mentions)[:SCOPE_MENTIONS]]
            inputs = scope_inputs(conn, sorted(set(hashes)))
        # Per mention, as the matcher sees it: its placed barangays, else every barangay of its
        # listed towns; the item shows the union of those scopes.
        codes: list[str] = []
        search: list[str] = []
        for h in dict.fromkeys(hashes):
            b, t = inputs.get(h, ([], []))
            own = b or [p for p, f in features.items() if f["properties"].get("town_psgc") in t]
            codes += [c for c in own if c not in codes]
            search += [c for c in search_towns(b, t, boundaries) if c not in search]
        return _Context(row, g, inputs, codes, search, scope_for(codes, [], boundaries))

    def _area_names(ctx: _Context) -> list[str]:
        """Every street name in the group's area: exactly what `--apply` accepts as a named
        decision (suggest.candidates; own key and generic names left out)."""
        idx = index()
        if idx is None:
            return []
        names = candidates(ctx.group, idx, boundaries, ctx.inputs, k=10**6)
        return sorted(names, key=str.casefold)

    def _lines(ctx: _Context, name: str, idx: StreetIndex) -> list[list[list[float]]]:
        # Through the alias path that runs after --apply, so the page shows exactly where
        # this name would be drawn for VECO's spelling (generic names included).
        alias = {(t, ctx.group.key): name for t in ctx.search}
        m = match_street(ctx.group.veco_name, ctx.scope, ctx.search, idx, alias)
        return [[list(p) for p in line] for line in m.lines]

    def _item(town: str, key: str) -> Response:
        ctx = _context(town, key)
        if isinstance(ctx, JSONResponse):
            return ctx
        row, g, codes = ctx.row, ctx.group, ctx.codes
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
            "area_names": [],
            "message": "",
        }
        idx = index()
        if idx is None:
            doc["message"] = "no OSM extract: run `streets fetch`; no street lines to show"
            return JSONResponse(doc)
        for name in queue.choices(row):
            doc["candidates"].append(
                {
                    "name": name,
                    "is_pick": name == row["pick"],
                    "generic": street_key(name) in GENERIC_STREET_KEYS,
                    "lines": _lines(ctx, name, idx),
                }
            )
        doc["area_names"] = _area_names(ctx)
        return JSONResponse(doc, headers={"Cache-Control": "no-store"})

    @router.get("/streets/review/lines")
    def lines(town: str = "", key: str = "", name: str = "") -> Response:
        if not (town and key and name):
            return _error(422, "town, key and name are required")
        try:
            ctx = _context(town, key)
            if isinstance(ctx, JSONResponse):
                return ctx
            idx = index()
            if idx is None or name not in _area_names(ctx):
                return _error(404, "not a street in this area")
            doc = {"name": name, "lines": _lines(ctx, name, idx)}
            return JSONResponse(doc, headers={"Cache-Control": "no-store"})
        except Exception:
            log.exception("review: /streets/review/lines failed")
            return _error(500, "internal error")

    def _extra_names(town: str, key: str, decision: str) -> list[str]:
        """The area's names when `decision` names a street outside the row's 5 choices."""
        if decision in ("y", "n", "skip"):
            return []
        row = next(
            (
                r
                for r in queue.read(cfg.queue_path)
                if (r["town_psgc"], r["veco_key"]) == (town, key)
            ),
            None,
        )
        if row is None or decision in queue.choices(row):
            return []
        ctx = _context(town, key)
        return [] if isinstance(ctx, JSONResponse) else _area_names(ctx)

    async def _checked_body(request: Request) -> dict | JSONResponse:
        """The JSON body of a same-origin POST carrying the page's token, or the error."""
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
        return body

    @router.post("/streets/review/decide")
    async def decide(request: Request) -> Response:
        body = await _checked_body(request)
        if isinstance(body, JSONResponse):
            return body
        town, key, decision = body.get("town"), body.get("key"), body.get("decision")
        if not all(isinstance(x, str) and x for x in (town, key, decision)):
            return _error(422, "town, key and decision are required")
        try:
            extra = _extra_names(town, key, decision)
            queue.set_decision(cfg.queue_path, town, key, decision, extra=extra)
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

    @router.post("/streets/review/undo")
    async def undo(request: Request) -> Response:
        """Back: clear a decision that `--apply` hasn't taken yet."""
        body = await _checked_body(request)
        if isinstance(body, JSONResponse):
            return body
        town, key = body.get("town"), body.get("key")
        if not all(isinstance(x, str) and x for x in (town, key)):
            return _error(422, "town and key are required")
        try:
            queue.clear_decision(cfg.queue_path, town, key)
        except KeyError:
            return _error(409, "no such row (already applied?)")
        except queue.QueueBusy:
            return _error(409, "queue busy")
        except queue.QueueError:
            log.exception("review: queue unreadable")
            return _error(500, "queue unreadable")
        return Response(status_code=204)

    return router
