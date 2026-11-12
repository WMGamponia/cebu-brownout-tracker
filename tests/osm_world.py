"""A tiny OSM extract over `map_world`'s squares (each 0.01° wide at x = 123.80 + i/100,
y 10.30–10.31): Sto. Niño 0, Lahug 1, Tisa 2, Tayud (Consolacion) 3, Poblacion (Liloan) 4,
Tayud (Liloan) 5, Linao 6. Written by `write(dir)` as `data/osm/` would be."""

from __future__ import annotations

import json
from pathlib import Path

OSM_BASE = "2026-11-12T02:14:58Z"
CEBU, CONSOLACION, LILOAN, MINGLANILLA = "730600000", "702219000", "702227000", "702232000"

Y = 10.305


def _x(i: int, frac: float) -> float:
    """Longitude at `frac` (0..1) across square `i`."""
    return round(123.8 + i / 100 + frac / 100, 6)


def _run(i0: float, i1: float, y: float = Y, step: float = 0.2) -> list[list[float]]:
    """Nodes from square position i0 to i1 (in squares), every `step` square."""
    n = round((i1 - i0) / step)
    return [[round(123.8 + (i0 + k * step) / 100, 6), y] for k in range(n + 1)]


def _way(wid: int, coords, **tags) -> dict:
    return {"id": wid, "tags": {"highway": "residential", **tags}, "geometry": coords}


# Cebu South Road crosses Sto. Niño, Lahug and Tisa (squares 0..2), from x 0.1 to 2.9.
CEBU_SOUTH = _way(101, _run(0.1, 2.9), name="Cebu South Road")
TOWNS: dict[str, list[dict]] = {
    CEBU: [
        CEBU_SOUTH,
        _way(102, _run(1.1, 1.5, y=10.302), name="Gorordo Avenue"),
        _way(103, _run(1.5, 1.9, y=10.302), name="Gorordo Avenue"),
        _way(104, _run(1.2, 1.6, y=10.308), name="Gorordo Ave."),
        _way(105, _run(1.2, 1.8, y=10.304), name="Juana Osmeña Street", old_name="Escario Street"),
        _way(106, _run(2.2, 2.8, y=10.307), name="Barangay Road"),
        # one node inside Tisa, the rest far north: never drawn
        _way(
            107,
            [[_x(2, 0.5), 10.305], [_x(2, 0.5), 10.35], [_x(2, 0.6), 10.36]],
            name="Salinas Drive",
        ),
        _way(108, _run(1.2, 1.8, y=10.306), name="Gorordo Avenue Extension"),
    ],
    CONSOLACION: [_way(201, _run(3.1, 3.9), name="Tayud Road")],
    LILOAN: [_way(301, _run(5.1, 5.9), name="Tayud Road")],
    MINGLANILLA: [_way(401, _run(6.1, 6.9), name="Linao Road")],
}


def write(osm_dir: Path) -> Path:
    osm_dir.mkdir(parents=True, exist_ok=True)
    towns = {}
    for psgc, ways in TOWNS.items():
        doc = {"town_psgc": psgc, "osm_base": OSM_BASE, "ways": ways}
        (osm_dir / f"streets-{psgc}.json").write_text(json.dumps(doc), encoding="utf-8")
        towns[psgc] = {"ways": len(ways), "osm_base": OSM_BASE, "sha256": "x"}
    man = {"osm_base": OSM_BASE, "towns": towns}
    (osm_dir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return osm_dir
