"""Pure-Python geometry for clipping OSM ways to barangay polygons (spec 4.4).

Metres come from an equirectangular projection at 10.3° N (error < 0.1 % over Cebu). Points
are (lon, lat), as in GeoJSON. Polygons are lists of rings, outer ring first; holes are
ignored (the medres boundaries have none)."""

from __future__ import annotations

import math

from outages.config import STREET_BUFFER_M

Point = tuple[float, float]
Ring = list[Point]
Polygon = list[Ring]

_KX = 111_320 * math.cos(math.radians(10.3))  # metres per degree of longitude
_KY = 110_574  # metres per degree of latitude


def _xy(p: Point) -> tuple[float, float]:
    return p[0] * _KX, p[1] * _KY


def distance_m(a: Point, b: Point) -> float:
    (ax, ay), (bx, by) = _xy(a), _xy(b)
    return math.hypot(bx - ax, by - ay)


def point_segment_m(p: Point, a: Point, b: Point) -> float:
    """Distance from `p` to the segment a–b, in metres."""
    (px, py), (ax, ay), (bx, by) = _xy(p), _xy(a), _xy(b)
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def in_polygon(p: Point, ring: Ring) -> bool:
    """Ray casting against one ring."""
    x, y = p
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _bbox(ring: Ring, pad_lon: float, pad_lat: float) -> tuple[float, float, float, float]:
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs) - pad_lon, min(ys) - pad_lat, max(xs) + pad_lon, max(ys) + pad_lat


class Scope:
    """Where a street may be drawn: inside any polygon, or within `buffer_m` of an edge."""

    def __init__(self, polygons: list[Polygon], buffer_m: float = STREET_BUFFER_M) -> None:
        self.buffer_m = buffer_m
        pad_lon, pad_lat = buffer_m / _KX, buffer_m / _KY
        self._rings = [(poly[0], _bbox(poly[0], pad_lon, pad_lat)) for poly in polygons if poly]
        boxes = [b for _, b in self._rings]
        self.bbox: tuple[float, float, float, float] = (
            (
                min(b[0] for b in boxes),
                min(b[1] for b in boxes),
                max(b[2] for b in boxes),
                max(b[3] for b in boxes),
            )
            if boxes
            else (0.0, 0.0, 0.0, 0.0)
        )

    def contains(self, p: Point) -> bool:
        x, y = p
        for ring, (w, s, e, n) in self._rings:
            if not (w <= x <= e and s <= y <= n):
                continue
            if in_polygon(p, ring):
                return True
            for i in range(len(ring) - 1):
                if point_segment_m(p, ring[i], ring[i + 1]) <= self.buffer_m:
                    return True
        return False


def clip(coords: list[Point], scope: Scope) -> list[list[Point]]:
    """Maximal runs of consecutive nodes inside the scope; runs of one node are dropped."""
    runs: list[list[Point]] = []
    run: list[Point] = []
    for p in coords:
        if scope.contains(p):
            run.append(p)
            continue
        if len(run) >= 2:
            runs.append(run)
        run = []
    if len(run) >= 2:
        runs.append(run)
    return runs


def round_lines(lines: list[list[Point]], ndigits: int = 5) -> list[list[Point]]:
    return [[(round(x, ndigits), round(y, ndigits)) for x, y in line] for line in lines]


def polygons_of(feature: dict) -> list[Polygon]:
    """The polygons of a GeoJSON Polygon or MultiPolygon feature; [] for anything else."""
    geom = feature.get("geometry") or {}
    kind = geom.get("type")
    if kind == "Polygon":
        parts = [geom["coordinates"]]
    elif kind == "MultiPolygon":
        parts = geom["coordinates"]
    else:
        return []
    return [[[(float(x), float(y)) for x, y, *_ in ring] for ring in poly] for poly in parts]
