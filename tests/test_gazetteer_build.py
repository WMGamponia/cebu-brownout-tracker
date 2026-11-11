"""Gazetteer build: pinned-hash check and barangays.csv from a synthetic zip (spec 4.1)."""

from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from outages import gazetteer_build as gb


def _feature(town: int, code: int, name: str) -> dict:
    props = {"adm3_psgc": town, "adm4_psgc": code, "adm4_en": name, "geo_level": "Bgy"}
    return {"type": "Feature", "properties": props, "geometry": None}


def _zip(tmp_path, files: dict[int, list[tuple[int, str]]]):
    """A zip shaped like geojson-medres.zip: one municity file per town."""
    p = tmp_path / "g.zip"
    with zipfile.ZipFile(p, "w") as z:
        for town, bgys in files.items():
            fc = {"type": "FeatureCollection", "features": [_feature(town, c, n) for c, n in bgys]}
            z.writestr(
                f"geojson/municities/medres/bgysubmuns-municity-{town}.0.01.json", json.dumps(fc)
            )
    return p


TOWNS = {"Cebu City": "730600000", "Minglanilla": "702232000"}


def test_verify_rejects_wrong_hash(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    gb.verify(p, hashlib.sha256(b"abc").hexdigest())
    with pytest.raises(gb.GazetteerError, match="SHA-256"):
        gb.verify(p, "0" * 64)


def test_build_csv_writes_sorted_rows(tmp_path):
    z = _zip(
        tmp_path,
        {
            730600000: [(730600002, "Tisa"), (730600001, "Apas")],
            702232000: [(702232005, "Tungkop")],
        },
    )
    out = tmp_path / "barangays.csv"
    assert gb.build_csv(z, TOWNS, out) == 3
    assert out.read_bytes().decode("utf-8") == (
        "psgc_code,name,town,town_psgc\n"
        "730600001,Apas,Cebu City,730600000\n"
        "730600002,Tisa,Cebu City,730600000\n"
        "702232005,Tungkop,Minglanilla,702232000\n"
    )


def test_build_csv_skips_towns_not_listed(tmp_path):
    z = _zip(
        tmp_path,
        {
            730600000: [(730600001, "Apas")],
            702232000: [(702232005, "Tungkop")],
            731100000: [(731100001, "Agus")],
        },
    )
    out = tmp_path / "barangays.csv"
    assert gb.build_csv(z, TOWNS, out) == 2
    assert "Agus" not in out.read_text(encoding="utf-8")


def test_build_csv_fails_if_town_missing(tmp_path):
    z = _zip(tmp_path, {730600000: [(730600001, "Apas")]})
    out = tmp_path / "barangays.csv"
    with pytest.raises(gb.GazetteerError, match="Minglanilla"):
        gb.build_csv(z, TOWNS, out)
    assert not out.exists()
