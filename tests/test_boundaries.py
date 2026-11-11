"""`areas/boundaries.geojson`: built from the pinned zip, committed, checked against the CSV
(Phase 4 spec 4.1)."""

from __future__ import annotations

import csv
import json
import re
import zipfile
from pathlib import Path

import pytest

from outages import gazetteer_build as gb
from outages.config import MECO_TOWNS

GOLDEN = Path(__file__).parent / "golden" / "map" / "boundaries_tiny.geojson"


def _feature(town: int, code: int, name: str, geometry: dict) -> dict:
    props = {"adm3_psgc": town, "adm4_psgc": code, "adm4_en": name, "geo_level": "Bgy"}
    return {"type": "Feature", "properties": props, "geometry": geometry}


SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [123.123456789, 10.987654321],
            [123.2, 10.9],
            [123.3000004, 10.8],
            [123.123456789, 10.987654321],
        ]
    ],
}
MULTI = {
    "type": "MultiPolygon",
    "coordinates": [
        [[[123.7, 10.3], [123.71, 10.31], [123.7200009, 10.3], [123.7, 10.3]]],
        [[[123.8, 10.4], [123.81, 10.41], [123.82, 10.4], [123.8, 10.4]]],
    ],
}
TINY_TOWNS = {"Cebu City": "730600000", "Minglanilla": "702232000"}
TINY_MECO = {"Cordova": "702223000"}


def _zip(tmp_path, files: dict[int, list[tuple[int, str, dict]]]) -> Path:
    p = tmp_path / "g.zip"
    with zipfile.ZipFile(p, "w") as z:
        for town, bgys in files.items():
            fc = {
                "type": "FeatureCollection",
                "features": [_feature(town, c, n, g) for c, n, g in bgys],
            }
            z.writestr(
                f"geojson/municities/medres/bgysubmuns-municity-{town}.0.01.json", json.dumps(fc)
            )
    return p


def _tiny_zip(tmp_path) -> Path:
    return _zip(
        tmp_path,
        {
            702223000: [(702223001, "Alegria", SQUARE)],
            730600000: [(730600002, "Tisa", SQUARE), (730600001, "Apas", MULTI)],
            702232000: [(702232005, "Tungkop", SQUARE)],
        },
    )


def test_build_boundaries_tiny_zip(tmp_path):
    out = tmp_path / "b.geojson"
    assert gb.build_boundaries(_tiny_zip(tmp_path), TINY_TOWNS, TINY_MECO, out) == 4
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc == json.loads(GOLDEN.read_text(encoding="utf-8"))
    # covered first, then by (town, name); MECO last
    assert [f["id"] for f in doc["features"]] == [
        "730600001",
        "730600002",
        "702232005",
        "702223001",
    ]
    for f in doc["features"]:
        assert set(f["properties"]) == {"psgc", "name", "town", "town_psgc", "covered"}


def test_build_boundaries_rounds_to_6_decimals(tmp_path):
    out = tmp_path / "b.geojson"
    gb.build_boundaries(_tiny_zip(tmp_path), TINY_TOWNS, TINY_MECO, out)
    assert _max_decimals(out.read_text(encoding="utf-8")) <= 6


def test_build_boundaries_missing_town_fails(tmp_path):
    z = _zip(tmp_path, {730600000: [(730600001, "Apas", SQUARE)]})
    out = tmp_path / "b.geojson"
    with pytest.raises(gb.GazetteerError, match="Minglanilla"):
        gb.build_boundaries(z, TINY_TOWNS, {}, out)
    assert not out.exists()


def _max_decimals(text: str) -> int:
    return max((len(m) for m in re.findall(r"\d\.(\d+)", text)), default=0)


def test_committed_boundaries_match_csv():
    doc = json.loads(gb.BOUNDARIES_PATH.read_text(encoding="utf-8"))
    with gb.CSV_PATH.open(encoding="utf-8", newline="") as f:
        csv_codes = [r["psgc_code"] for r in csv.DictReader(f)]
    covered = [f["id"] for f in doc["features"] if f["properties"]["covered"]]
    assert sorted(covered) == sorted(csv_codes)
    assert len(covered) == len(set(covered)) == 232
    others = [f for f in doc["features"] if not f["properties"]["covered"]]
    assert others
    assert {f["properties"]["town_psgc"] for f in others} == set(MECO_TOWNS.values())
    for f in doc["features"]:
        assert f["id"] == f["properties"]["psgc"]
    assert doc["attribution"] == gb.ATTRIBUTION
    assert _max_decimals(gb.BOUNDARIES_PATH.read_text(encoding="utf-8")) <= 6
