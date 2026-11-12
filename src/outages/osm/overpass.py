"""Overpass client: one named-highway extract per VECO town, fetched by hand (spec 4.2, 7).

Polite: the project's User-Agent, one request at a time, `OVERPASS_SPACING_S` apart, back-off on
429/504 and then stop. A town whose answer is unusable (bad JSON, no `elements`, a runtime-error
`remark`, 0 ways) fails on its own and keeps its old file: a partial answer is never saved as a
smaller "complete" extract. Only `streets fetch --yes` builds this client.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from outages.config import (
    BBOX_PAD_M,
    OVERPASS_BACKOFF_S,
    OVERPASS_SPACING_S,
    OVERPASS_URL,
    USER_AGENT,
    VECO_TOWNS,
)
from outages.streets.geo import Scope, polygons_of

log = logging.getLogger(__name__)

NAME_TAGS = ("name", "alt_name", "old_name", "official_name", "short_name", "highway")
MANIFEST = "manifest.json"

Sleep = Callable[[float], None]


class FetchError(Exception):
    pass


class RateLimited(FetchError):
    """429/504 after every back-off: stop the whole fetch (spec 7)."""


@dataclass
class FetchSummary:
    ok: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    stopped: bool = False


def town_file(out_dir: Path, town_psgc: str) -> Path:
    return out_dir / f"streets-{town_psgc}.json"


def town_bbox(
    boundaries: dict, town_psgc: str, pad_m: float = BBOX_PAD_M
) -> tuple[float, float, float, float]:
    """(S, W, N, E) of the town's barangay polygons, padded by `pad_m`, for Overpass."""
    polys = [
        p
        for f in boundaries["features"]
        if f["properties"].get("town_psgc") == town_psgc
        for p in polygons_of(f)
    ]
    if not polys:
        raise FetchError(f"no boundaries for town {town_psgc}")
    w, s, e, n = Scope(polys, buffer_m=pad_m).bbox
    return round(s, 5), round(w, 5), round(n, 5), round(e, 5)


def query_for(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    return f'[out:json][timeout:90];way["highway"]["name"]({s},{w},{n},{e});out tags geom;'


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, transport=transport)


def _slim(el: dict) -> dict | None:
    geom = [
        [round(g["lon"], 6), round(g["lat"], 6)]
        for g in el.get("geometry") or []
        if isinstance(g, dict) and "lat" in g and "lon" in g
    ]
    tags = {k: v for k, v in (el.get("tags") or {}).items() if k in NAME_TAGS}
    if len(geom) < 2 or not tags.get("name"):
        return None
    return {"id": el["id"], "tags": tags, "geometry": geom}


def fetch_town(
    client: httpx.Client,
    town_psgc: str,
    bbox: tuple[float, float, float, float],
    sleep: Sleep = time.sleep,
) -> dict:
    """{"ways": [...], "osm_base": str} for one town; raises FetchError (RateLimited to stop)."""
    query = query_for(bbox)
    backoffs = list(OVERPASS_BACKOFF_S)
    while True:
        try:
            r = client.post(OVERPASS_URL, data={"data": query})
        except httpx.HTTPError as e:
            raise FetchError(f"{town_psgc}: {type(e).__name__}") from None
        if r.status_code in (429, 504):
            if not backoffs:
                raise RateLimited(f"{town_psgc}: HTTP {r.status_code} after back-off")
            wait = backoffs.pop(0)
            log.warning("overpass HTTP %s for %s; waiting %ss", r.status_code, town_psgc, wait)
            sleep(wait)
            continue
        if r.status_code != 200:
            raise FetchError(f"{town_psgc}: HTTP {r.status_code}")
        break
    try:
        data = r.json()
    except ValueError:
        raise FetchError(f"{town_psgc}: response is not JSON") from None
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise FetchError(f"{town_psgc}: response has no elements")
    remark = str(data.get("remark") or "")
    if "error" in remark.lower() or "timed out" in remark.lower():
        raise FetchError(f"{town_psgc}: {remark[:120]}")
    ways = [w for el in data["elements"] if el.get("type") == "way" and (w := _slim(el))]
    if not ways:
        raise FetchError(f"{town_psgc}: 0 ways")
    return {"ways": ways, "osm_base": (data.get("osm3s") or {}).get("timestamp_osm_base")}


def _write_atomic(path: Path, text: str) -> bytes:
    raw = text.encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    return raw


def load_manifest(out_dir: Path) -> dict | None:
    p = out_dir / MANIFEST
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def fetch_all(
    client: httpx.Client,
    boundaries: dict,
    out_dir: Path,
    now: datetime,
    sleep: Sleep = time.sleep,
) -> FetchSummary:
    """Fetch every VECO town in order; write each file atomically and update the manifest."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_dir) or {"towns": {}}
    summary = FetchSummary()
    towns = list(VECO_TOWNS.values())
    for i, psgc in enumerate(towns):
        if i:
            sleep(OVERPASS_SPACING_S)
        try:
            bbox = town_bbox(boundaries, psgc)
            got = fetch_town(client, psgc, bbox, sleep=sleep)
        except RateLimited as e:
            summary.stopped = True
            for rest in towns[i:]:
                summary.failed[rest] = str(e) if rest == psgc else "not fetched (stopped)"
            log.error("overpass: %s; stopping", e)
            break
        except FetchError as e:
            summary.failed[psgc] = str(e)
            log.error("overpass: %s", e)
            continue
        doc = {"town_psgc": psgc, "osm_base": got["osm_base"], "ways": got["ways"]}
        raw = _write_atomic(town_file(out_dir, psgc), json.dumps(doc, ensure_ascii=False))
        manifest["towns"][psgc] = {
            "fetched_at": now.isoformat(),
            "query": query_for(bbox),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "ways": len(got["ways"]),
            "osm_base": got["osm_base"],
        }
        summary.ok.append(psgc)
    if summary.ok:
        # The extract is as old as its oldest town (a failed town keeps its old file).
        old = manifest.get("osm_base")
        bases = [t.get("osm_base") or old for t in manifest["towns"].values()]
        manifest["osm_base"] = min((b for b in bases if b), default=None)
        _write_atomic(out_dir / MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return summary
