"""Download the pinned PSGC gazetteer and write `areas/barangays.csv` (spec 4.1).

The only network use in Phase 2 besides the LLM arms: one GitHub release asset, checked against
our own pinned SHA-256 (the release's SHA256SUMS comes from the same place as the zip, so it
can't catch a swapped file).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import httpx

from outages.config import GAZETTEER_SHA256, GAZETTEER_URL, USER_AGENT, VECO_TOWNS

ZIP_NAME = "geojson-medres.zip"
CSV_PATH = Path(__file__).parent / "areas" / "barangays.csv"
HEADER = ("psgc_code", "name", "town", "town_psgc")


class GazetteerError(Exception):
    pass


def verify(path: Path, sha256: str) -> None:
    """Raise unless the file's SHA-256 is the pinned one."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != sha256:
        raise GazetteerError(f"SHA-256 mismatch for {path.name}: got {h.hexdigest()}")


def _town_file(names: list[str], town_psgc: str) -> str | None:
    suffix = f"/bgysubmuns-municity-{town_psgc}.0.01.json"
    return next((n for n in names if n.endswith(suffix)), None)


def build_csv(zip_path: Path, towns: dict[str, str], out: Path) -> int:
    """Write the listed towns' barangays, sorted by (town, name); return the row count.

    A listed town with no barangay file fails and writes nothing.
    """
    rows: list[tuple[str, str, str, str]] = []
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for town, town_psgc in towns.items():
            entry = _town_file(names, town_psgc)
            if entry is None:
                raise GazetteerError(f"no barangay file for {town} ({town_psgc}) in {zip_path}")
            for feat in json.loads(z.read(entry))["features"]:
                p = feat["properties"]
                rows.append((str(p["adm4_psgc"]), p["adm4_en"], town, town_psgc))
    rows.sort(key=lambda r: (r[2], r[1]))
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    w.writerows(rows)
    out.write_text(buf.getvalue(), encoding="utf-8", newline="\n")
    return len(rows)


def download(dest_dir: Path, url: str = GAZETTEER_URL, sha256: str = GAZETTEER_SHA256) -> Path:
    """Fetch the zip into `dest_dir` (skipped if a verified copy is there); return its path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / ZIP_NAME
    if dest.exists():
        verify(dest, sha256)
        return dest
    tmp = dest.with_suffix(".part")
    headers = {"User-Agent": USER_AGENT}
    with (
        httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60) as r,
        tmp.open("wb") as f,
    ):
        r.raise_for_status()
        for chunk in r.iter_bytes():
            f.write(chunk)
    try:
        verify(tmp, sha256)
    except GazetteerError:
        tmp.unlink()
        raise
    os.replace(tmp, dest)
    return dest


def counts(towns: dict[str, str], csv_path: Path = CSV_PATH) -> dict[str, int]:
    """Barangays per town in the written CSV."""
    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {t: sum(1 for r in rows if r["town"] == t) for t in towns}


def run(data_dir: Path) -> dict[str, int]:
    """Download, verify and write barangays.csv; return per-town counts."""
    z = download(data_dir)
    build_csv(z, VECO_TOWNS, CSV_PATH)
    return counts(VECO_TOWNS)
