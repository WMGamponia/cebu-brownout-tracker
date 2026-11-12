"""Overpass client and per-town extract (spec 4.2, 7). Every request goes to a MockTransport."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from outages.config import USER_AGENT, VECO_TOWNS
from outages.map.build import load_boundaries
from outages.osm import overpass
from outages.osm.overpass import FetchError, fetch_all, fetch_town, query_for, town_bbox
from outages.streets.geo import polygons_of

OSM = Path(__file__).parent / "fixtures" / "osm"
NOW = datetime(2026, 11, 12, 3, 30, tzinfo=UTC)
BBOX = (10.2, 123.8, 10.3, 123.9)


def _body(name: str) -> bytes:
    return (OSM / name).read_bytes()


def _client(handler) -> httpx.Client:
    return overpass.make_client(transport=httpx.MockTransport(handler))


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, s: float) -> None:
        self.calls.append(s)


@pytest.fixture(scope="module")
def boundaries():
    return load_boundaries()


def test_query_shape():
    q = query_for((10.2, 123.8, 10.3, 123.9))
    assert q.startswith("[out:json][timeout:90];")
    assert 'way["highway"]["name"](10.2,123.8,10.3,123.9);' in q
    assert q.endswith("out tags geom;")


def test_town_bbox_padded(boundaries):
    town = VECO_TOWNS["Liloan"]
    s, w, n, e = town_bbox(boundaries, town)
    pts = [
        p
        for f in boundaries["features"]
        if f["properties"]["town_psgc"] == town
        for poly in polygons_of(f)
        for p in poly[0]
    ]
    lats = [p[1] for p in pts]
    lons = [p[0] for p in pts]
    assert min(lats) - s == pytest.approx(500 / 110_574, rel=0.01)
    assert n - max(lats) == pytest.approx(500 / 110_574, rel=0.01)
    assert min(lons) - w == pytest.approx(500 / 109_526, rel=0.01)
    assert e - max(lons) == pytest.approx(500 / 109_526, rel=0.01)


def test_unknown_town_bbox_raises(boundaries):
    with pytest.raises(FetchError):
        town_bbox(boundaries, "999999999")


def test_fetch_town_slims_ways():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, content=_body("ok_small.json"))

    got = fetch_town(_client(handler), "730600000", BBOX, sleep=Sleeps())
    assert got["osm_base"] == "2026-11-12T03:00:00Z"
    w = got["ways"][0]
    assert w == {
        "id": 101,
        "tags": {"highway": "primary", "name": "Cebu South Road"},
        "geometry": [[123.840123, 10.260123], [123.841123, 10.261123], [123.842123, 10.262123]],
    }
    assert got["ways"][1]["tags"]["old_name"] == "Gorordo Ave"
    assert seen[0].method == "POST"
    assert str(seen[0].url) == overpass.OVERPASS_URL
    assert parse_qs(seen[0].content.decode())["data"] == [query_for(BBOX)]


def test_user_agent_sent():
    agents = []

    def handler(req):
        agents.append(req.headers["user-agent"])
        return httpx.Response(200, content=_body("ok_small.json"))

    fetch_town(_client(handler), "730600000", BBOX, sleep=Sleeps())
    assert agents == [USER_AGENT]


def test_429_then_ok_retries_with_backoff():
    responses = iter([httpx.Response(429), httpx.Response(200, content=_body("ok_small.json"))])
    sl = Sleeps()
    got = fetch_town(_client(lambda r: next(responses)), "730600000", BBOX, sleep=sl)
    assert len(got["ways"]) == 2
    assert sl.calls == [30]


def test_504_three_times_is_rate_limited():
    sl = Sleeps()
    with pytest.raises(overpass.RateLimited):
        fetch_town(_client(lambda r: httpx.Response(504)), "730600000", BBOX, sleep=sl)
    assert sl.calls == [30, 120]


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (httpx.Response(200, content=_body("remark_timeout.json")), "runtime error"),
        (httpx.Response(200, content=_body("empty.json")), "0 ways"),
        (httpx.Response(200, content=b"<html>busy</html>"), "JSON"),
        (httpx.Response(200, content=b'{"osm3s": {}}'), "elements"),
        (httpx.Response(400, content=b"bad query"), "400"),
    ],
)
def test_fetch_town_failures(response, match):
    with pytest.raises(FetchError, match=match):
        fetch_town(_client(lambda r: response), "730600000", BBOX, sleep=Sleeps())


def test_transport_error_is_fetch_error():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(FetchError):
        fetch_town(_client(handler), "730600000", BBOX, sleep=Sleeps())


def test_fetch_all_writes_files_and_manifest(tmp_path, boundaries):
    sl = Sleeps()
    out = tmp_path / "osm"
    s = fetch_all(
        _client(lambda r: httpx.Response(200, content=_body("ok_small.json"))),
        boundaries,
        out,
        NOW,
        sleep=sl,
    )
    assert s.ok == list(VECO_TOWNS.values())
    assert s.failed == {}
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["osm_base"] == "2026-11-12T03:00:00Z"
    assert set(man["towns"]) == set(VECO_TOWNS.values())
    for psgc, entry in man["towns"].items():
        f = out / f"streets-{psgc}.json"
        assert entry["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
        assert entry["ways"] == 2
        assert entry["fetched_at"] == "2026-11-12T03:30:00+00:00"
        assert 'way["highway"]["name"]' in entry["query"]
        assert json.loads(f.read_text(encoding="utf-8"))["ways"][0]["id"] == 101
    assert not list(out.glob("*.tmp"))


def test_spacing_between_requests(tmp_path, boundaries):
    sl = Sleeps()
    fetch_all(
        _client(lambda r: httpx.Response(200, content=_body("ok_small.json"))),
        boundaries,
        tmp_path,
        NOW,
        sleep=sl,
    )
    assert sl.calls == [10] * (len(VECO_TOWNS) - 1)


def _seed_old(out: Path, psgc: str) -> bytes:
    out.mkdir(parents=True, exist_ok=True)
    old = b'{"town_psgc": "old", "ways": []}'
    (out / f"streets-{psgc}.json").write_bytes(old)
    man = {"osm_base": "2026-01-01T00:00:00Z", "towns": {psgc: {"sha256": "old", "ways": 9}}}
    (out / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return old


def test_remark_is_failure_keeps_old_file(tmp_path, boundaries):
    first = list(VECO_TOWNS.values())[0]
    old = _seed_old(tmp_path, first)
    calls = iter(range(100))

    def handler(req):
        name = "remark_timeout.json" if next(calls) == 0 else "ok_small.json"
        return httpx.Response(200, content=_body(name))

    s = fetch_all(_client(handler), boundaries, tmp_path, NOW, sleep=Sleeps())
    assert "runtime error" in s.failed[first]
    assert len(s.ok) == len(VECO_TOWNS) - 1
    assert (tmp_path / f"streets-{first}.json").read_bytes() == old
    man = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert man["towns"][first] == {"sha256": "old", "ways": 9}  # old entry kept
    # the oldest data still in use dates the extract
    assert man["osm_base"] == "2026-01-01T00:00:00Z"


def test_zero_ways_is_failure(tmp_path, boundaries):
    calls = iter(range(100))

    def handler(req):
        name = "empty.json" if next(calls) == 1 else "ok_small.json"
        return httpx.Response(200, content=_body(name))

    s = fetch_all(_client(handler), boundaries, tmp_path, NOW, sleep=Sleeps())
    second = list(VECO_TOWNS.values())[1]
    assert list(s.failed) == [second]
    assert not (tmp_path / f"streets-{second}.json").exists()


def test_bad_json_is_failure(tmp_path, boundaries):
    calls = iter(range(100))

    def handler(req):
        if next(calls) == 2:
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, content=_body("ok_small.json"))

    s = fetch_all(_client(handler), boundaries, tmp_path, NOW, sleep=Sleeps())
    assert list(s.failed) == [list(VECO_TOWNS.values())[2]]


def test_rate_limit_stops_and_keeps_fetched(tmp_path, boundaries):
    """Spec 7: after the back-offs, stop with an error; files fetched so far are kept."""
    calls = iter(range(100))

    def handler(req):
        if next(calls) >= 2:
            return httpx.Response(429)
        return httpx.Response(200, content=_body("ok_small.json"))

    towns = list(VECO_TOWNS.values())
    s = fetch_all(_client(handler), boundaries, tmp_path, NOW, sleep=Sleeps())
    assert s.ok == towns[:2]
    assert s.stopped
    assert set(s.failed) == set(towns[2:])
    assert (tmp_path / f"streets-{towns[1]}.json").exists()
    man = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert set(man["towns"]) == set(towns[:2])


@pytest.mark.parametrize("status", [502, 503])
def test_502_503_back_off_like_504(status):
    responses = iter([httpx.Response(status), httpx.Response(200, content=_body("ok_small.json"))])
    sl = Sleeps()
    got = fetch_town(_client(lambda r: next(responses)), "730600000", BBOX, sleep=sl)
    assert len(got["ways"]) == 2
    assert sl.calls == [30]


def test_fetch_all_only_listed_towns_keeps_others(tmp_path, boundaries):
    towns = list(VECO_TOWNS.values())
    ok = lambda r: httpx.Response(200, content=_body("ok_small.json"))  # noqa: E731
    fetch_all(_client(ok), boundaries, tmp_path, NOW, sleep=Sleeps())
    before = (tmp_path / f"streets-{towns[1]}.json").read_bytes()
    later = datetime(2026, 11, 12, 5, 0, tzinfo=UTC)
    seen = []

    def handler(req):
        seen.append(req)
        return ok(req)

    sl = Sleeps()
    s = fetch_all(
        _client(handler), boundaries, tmp_path, later, sleep=sl, towns=[towns[0], towns[3]]
    )
    assert s.ok == [towns[0], towns[3]]
    assert len(seen) == 2 and sl.calls == [10]
    assert (tmp_path / f"streets-{towns[1]}.json").read_bytes() == before
    man = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert set(man["towns"]) == set(towns)
    assert man["towns"][towns[0]]["fetched_at"] == later.isoformat()
    assert man["towns"][towns[1]]["fetched_at"] == NOW.isoformat()


def test_fetch_all_unknown_town_rejected(tmp_path, boundaries):
    with pytest.raises(FetchError, match="not a VECO town"):
        fetch_all(_client(lambda r: None), boundaries, tmp_path, NOW, towns=["123"])
