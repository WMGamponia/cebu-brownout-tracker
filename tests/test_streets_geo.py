"""Pure geometry for street clipping (spec 4.4)."""

from __future__ import annotations

import math

import pytest

from outages.streets.geo import (
    Scope,
    clip,
    distance_m,
    in_polygon,
    point_segment_m,
    polygons_of,
    round_lines,
)

W, S, E, N = 123.90, 10.30, 123.91, 10.31
SQUARE = [(W, S), (E, S), (E, N), (W, N), (W, S)]
M_PER_DEG_LON = 111_320 * math.cos(math.radians(10.3))


def _east(m: float) -> float:
    """Longitude `m` metres east of the square's east edge."""
    return E + m / M_PER_DEG_LON


def test_distance_lat():
    assert distance_m((123.9, 10.3), (123.9, 10.31)) == pytest.approx(1105.74, abs=1)


def test_distance_lon():
    assert distance_m((123.9, 10.3), (123.91, 10.3)) == pytest.approx(1095.3, abs=1)


def test_point_segment_perpendicular_and_endpoint():
    a, b = (E, S), (E, N)
    assert point_segment_m((_east(100), 10.305), a, b) == pytest.approx(100, abs=0.5)
    # beyond the segment's end: distance to the endpoint
    assert point_segment_m((E, N + 0.001), a, b) == pytest.approx(110.57, abs=0.5)


def test_in_polygon():
    assert in_polygon((123.905, 10.305), SQUARE)
    assert not in_polygon((123.92, 10.305), SQUARE)
    assert not in_polygon((123.905, 10.32), SQUARE)
    assert in_polygon((W + 1e-6, S + 1e-6), SQUARE)
    assert not in_polygon((W - 1e-6, S + 0.005), SQUARE)


def test_scope_buffer():
    sc = Scope([[SQUARE]], buffer_m=150)
    assert sc.contains((123.905, 10.305))
    assert sc.contains((_east(100), 10.305))
    assert not sc.contains((_east(200), 10.305))
    w, s, e, n = sc.bbox
    assert w < W and e > E and s < S and n > N
    assert e - E == pytest.approx(150 / M_PER_DEG_LON, rel=1e-6)


def test_scope_several_polygons():
    other = [(x + 0.02, y) for x, y in SQUARE]
    sc = Scope([[SQUARE], [other]])
    assert sc.contains((123.925, 10.305))
    assert not sc.contains((123.915, 10.305 + 0.02))


def test_clip_crossing_gives_one_run():
    way = [
        (123.89, 10.305),
        (123.902, 10.305),
        (123.905, 10.305),
        (123.908, 10.305),
        (123.93, 10.305),
    ]
    assert clip(way, Scope([[SQUARE]])) == [
        [(123.902, 10.305), (123.905, 10.305), (123.908, 10.305)]
    ]


def test_clip_leave_and_reenter_gives_two_runs():
    way = [
        (123.902, 10.305),
        (123.904, 10.305),
        (123.904, 10.33),
        (123.906, 10.33),
        (123.906, 10.305),
        (123.908, 10.305),
    ]
    runs = clip(way, Scope([[SQUARE]]))
    assert runs == [[(123.902, 10.305), (123.904, 10.305)], [(123.906, 10.305), (123.908, 10.305)]]


def test_clip_single_node_run_dropped():
    way = [(123.89, 10.305), (123.905, 10.305), (123.93, 10.305)]
    assert clip(way, Scope([[SQUARE]])) == []


def test_clip_outside_is_empty():
    assert clip([(123.95, 10.4), (123.96, 10.41)], Scope([[SQUARE]])) == []


def test_round_lines():
    assert round_lines([[(123.9000049, 10.1234567), (1.0, 2.0)]]) == [
        [(123.9, 10.12346), (1.0, 2.0)]
    ]


def test_polygons_of_polygon_and_multipolygon():
    ring = [[W, S], [E, S], [E, N], [W, N], [W, S]]
    poly = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}}
    assert polygons_of(poly) == [[SQUARE]]
    multi = {
        "type": "Feature",
        "geometry": {"type": "MultiPolygon", "coordinates": [[ring], [ring]]},
    }
    assert polygons_of(multi) == [[SQUARE], [SQUARE]]
    assert polygons_of({"type": "Feature", "geometry": None}) == []
