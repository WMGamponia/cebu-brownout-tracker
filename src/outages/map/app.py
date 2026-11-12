"""The map's web app: GET routes over a per-request read-only connection (spec 4.4).

The surface is exactly `/`, `/history`, `/static/…`, `/outages.geojson`, `/stats.json`,
`/boundaries.geojson` and `/healthz`: no docs UI, no CORS, no writes. A missing or old DB is a
503 and is never created or migrated.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from outages import db
from outages.areas.gazetteer import Gazetteer
from outages.history.load import load_rows
from outages.history.stats import compute, parse_range
from outages.map.build import build, dumps, schema_ready
from outages.map.window import WindowError, parse_window
from outages.timeparse import MANILA

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
NOT_READY = "database not ready: run python -m outages areas"
NOT_MIGRATED = "database needs migrations: run python -m outages streets match"


class _NotReady(Exception):
    pass


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(
    db_path: Path, boundaries: dict, gaz: Gazetteer, clock: Callable[[], datetime]
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @contextmanager
    def ro() -> Iterator[sqlite3.Connection]:
        if not db_path.is_file():  # connect_ro must never create the file
            raise _NotReady
        conn = db.connect_ro(db_path)
        conn.execute("PRAGMA busy_timeout=30000")  # wait out a scheduled run's write
        try:
            if not schema_ready(conn):
                raise _NotReady
            yield conn
        finally:
            conn.close()

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/outages.geojson")
    def outages(from_: str | None = Query(None, alias="from"), days: str | None = None) -> Response:
        try:
            with ro() as conn:
                now = clock()
                window = parse_window(conn, now, from_, days)
                doc = build(conn, gaz, boundaries, window, now)
        except _NotReady:
            return _error(503, NOT_READY)
        except WindowError as e:
            return _error(422, str(e))
        except Exception:
            log.exception("map: /outages.geojson failed")
            return _error(500, "internal error")
        return Response(
            dumps(doc),
            media_type="application/geo+json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/stats.json")
    def stats(from_: str | None = Query(None, alias="from"), to: str | None = None) -> Response:
        try:
            with ro() as conn:
                if not db.is_current(conn):
                    return _error(503, NOT_MIGRATED)
                now = clock()
                rng = parse_range(from_, to, now)
                rows, coverage = load_rows(conn, rng)
                doc = compute(rows, rng, gaz, coverage, now)
        except _NotReady:
            return _error(503, NOT_READY)
        except WindowError as e:
            return _error(422, str(e))
        except Exception:
            log.exception("map: /stats.json failed")
            return _error(500, "internal error")
        return Response(
            dumps(doc),
            media_type="application/json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    boundaries_body = dumps(boundaries)

    @app.get("/boundaries.geojson")
    def boundaries_geojson() -> Response:
        return Response(
            boundaries_body,
            media_type="application/geo+json; charset=utf-8",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        try:
            with ro() as conn:
                version = conn.execute("SELECT max(version) FROM schema_version").fetchone()[0]
                row = conn.execute("SELECT value FROM meta WHERE key = 'last_check_at'").fetchone()
        except _NotReady:
            return _error(503, NOT_READY)
        last = datetime.fromisoformat(row[0]).astimezone(MANILA).isoformat() if row else None
        return JSONResponse({"ok": True, "schema": version, "last_check_at": last})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
