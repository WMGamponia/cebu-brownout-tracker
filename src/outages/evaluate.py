"""Area eval: seeded stratified sample, frozen splits (spec 7.1, 7.2).

The sampler uses its own rough layout heuristic, not `areas.segment`, so segmenter bugs can't
bias which strings get labelled.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
from pathlib import Path

EVAL_DIR = Path("evals") / "areas"

_TOWNS = {
    r"Cebu\s+City": ("Cebu City", "cebu"),
    r"Mandaue(?:\s+City)?": ("Mandaue City", "mandaue"),
    r"Talisay(?:\s+City)?": ("Talisay City", "south"),
    r"City\s+of\s+Naga|Naga(?:\s+City)?": ("City of Naga", "south"),
    r"Minglanilla": ("Minglanilla", "south"),
    r"San\s+Fernando": ("San Fernando", "south"),
    r"Consolacion": ("Consolacion", "north"),
    r"Liloan": ("Liloan", "north"),
}
_TOWN_RE = re.compile(r"\b(" + "|".join(f"(?:{p})" for p in _TOWNS) + r")\b")
_LABEL = (
    r"(?:Barangays?|Streets?|Roads?|Sitios?|Puroks?|Zones?|Subdivisions?|Villages?"
    r"|Establishments?|Schools?|Churches|Chapels?|Hospitals?|Government)"
)
_LABEL_RE = re.compile(_LABEL + r"\b[ \w/&]{0,40}:")
_LINE_LABEL_RE = re.compile(r"^\s*" + _LABEL + r"\b[ \w/&]{0,40}:")
_INLINE_RE = re.compile(r"(?<=[a-z.])" + _LABEL + r"\b[ \w/&]{0,40}:")
_PORTION = r"^(?:a\s+)?portions?\s+of\s+"
_CITY_FIRST_RE = re.compile(_PORTION + r"([^(,\n]*?)\s*\(", re.IGNORECASE)
_HEAD_CUT_RE = re.compile(r"\(|\balong\b|\bincluding\b", re.IGNORECASE)

LAYOUTS = ("city_first", "barangay_first", "structured")
GROUPS = ("cebu", "mandaue", "south", "north")


class EvalError(Exception):
    pass


def areas_hash(areas_raw: str) -> str:
    return hashlib.sha256(areas_raw.encode("utf-8")).hexdigest()


def _first_line(s: str) -> str:
    return s.strip().split("\n", 1)[0].strip()


def _head(s: str) -> str:
    """The first line up to `(`, `along` or `including`: where the towns are named."""
    return _HEAD_CUT_RE.split(_first_line(s), maxsplit=1)[0]


def _canonical(match: str) -> tuple[str, str]:
    for pat, val in _TOWNS.items():
        if re.fullmatch(pat, match):
            return val
    raise AssertionError(match)  # pragma: no cover


def _towns(s: str) -> list[tuple[str, str]]:
    seen: dict[str, str] = {}
    for m in _TOWN_RE.finditer(_head(s)):
        name, group = _canonical(m.group(1))
        seen.setdefault(name, group)
    return list(seen.items())


def town_count(areas_raw: str) -> int:
    return len(_towns(areas_raw))


def town_group(areas_raw: str) -> str:
    """`cebu|mandaue|south|north`, `multi` across groups, `none` without a town."""
    groups = {g for _, g in _towns(areas_raw)}
    if not groups:
        return "none"
    return groups.pop() if len(groups) == 1 else "multi"


def has_inline_label(areas_raw: str) -> bool:
    return bool(_INLINE_RE.search(areas_raw) or _LABEL_RE.search(_first_line(areas_raw)))


def is_wrapped(areas_raw: str) -> bool:
    """Several lines, at least one of them (after the first) not starting with a sub-label."""
    lines = [ln for ln in areas_raw.strip().split("\n")[1:] if ln.strip()]
    return any(not _LINE_LABEL_RE.match(ln) for ln in lines)


def rough_layout(areas_raw: str) -> str:
    """`structured|city_first|barangay_first|other` by a regex heuristic (not the segmenter)."""
    if _LABEL_RE.search(areas_raw):
        return "structured"
    first = _first_line(areas_raw)
    m = _CITY_FIRST_RE.match(first)
    if m and _TOWN_RE.search(m.group(1)):
        return "city_first"
    head = _head(areas_raw)
    if re.match(_PORTION, head, re.IGNORECASE) and re.search(
        _TOWN_RE.pattern + r"\s*(?:,|$)", head
    ):
        return "barangay_first"
    return "other"


def _row(i: str, slug: str, text: str) -> dict:
    return {
        "id": i,
        "areas_hash": areas_hash(text),
        "slug": slug,
        "areas_raw": text,
        "towns": [],
        "barangays": [],
        "entities": [],
    }


_FORCED = (
    ("two_town", 3, lambda s: town_count(s) >= 2),
    ("inline", 1, has_inline_label),
    ("wrapped", 1, is_wrapped),
    ("other", 1, lambda s: rough_layout(s) == "other"),
)


def sample(
    rows: list[tuple[str, str]], seed: int, n: int = 60, n_dev: int = 20
) -> tuple[list[dict], list[dict]]:
    """Draw `n` distinct texts from `(slug, areas_raw)` rows; return (dev, test).

    Forced picks first (spec 7.1), then round-robin over the layout x town-group cells so the
    allocation is as even as each cell allows. Forced picks alternate between dev and test.
    """
    by_text: dict[str, str] = {}
    for slug, text in sorted(rows):
        by_text.setdefault(text, slug)
    cands = sorted(by_text)
    rng = random.Random(seed)
    chosen: set[str] = set()
    forced: list[str] = []
    for _, k, pred in _FORCED:
        pool = [t for t in cands if t not in chosen and pred(t)]
        for t in rng.sample(pool, min(k, len(pool))):
            chosen.add(t)
            forced.append(t)
    cells: dict[tuple[str, str], list[str]] = {(lay, g): [] for lay in LAYOUTS for g in GROUPS}
    for t in cands:
        key = (rough_layout(t), town_group(t))
        if key in cells and t not in chosen:
            cells[key].append(t)
    for pool in cells.values():
        rng.shuffle(pool)
    rest: list[str] = []
    want = min(n, len(cands)) - len(forced)
    while len(rest) < want and any(cells.values()):
        for key in sorted(cells):
            if cells[key] and len(rest) < want:
                rest.append(cells[key].pop())
    dev = forced[0::2]
    test = forced[1::2]
    rng.shuffle(rest)
    for t in rest:
        (dev if len(dev) < n_dev else test).append(t)
    rng.shuffle(dev)
    rng.shuffle(test)
    return (
        [_row(f"dev-{i:02d}", by_text[t], t) for i, t in enumerate(dev, 1)],
        [_row(f"test-{i:02d}", by_text[t], t) for i, t in enumerate(test, 1)],
    )


def format_counts(dev: list[dict], test: list[dict]) -> str:
    """Layout x town-group counts of the sample (dev + test), for the owner's review."""
    groups = (*GROUPS, "multi", "none")
    layouts = (*LAYOUTS, "other")
    counts: dict[tuple[str, str], int] = {}
    for r in dev + test:
        key = (rough_layout(r["areas_raw"]), town_group(r["areas_raw"]))
        counts[key] = counts.get(key, 0) + 1
    lines = [f"{'layout':<16}" + "".join(f"{g:>9}" for g in (*groups, "total"))]
    for lay in layouts:
        cells = [counts.get((lay, g), 0) for g in groups]
        lines.append(f"{lay:<16}" + "".join(f"{c:>9}" for c in (*cells, sum(cells))))
    totals = [sum(counts.get((lay, g), 0) for lay in layouts) for g in groups]
    lines.append(f"{'total':<16}" + "".join(f"{c:>9}" for c in (*totals, sum(totals))))
    return "\n".join(lines) + "\n"


def corpus_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(slug, areas_raw) of every non-`removed` outage."""
    return [
        (r[0], r[1])
        for r in conn.execute(
            "SELECT p.slug, o.areas_raw FROM outages o JOIN posts p ON p.id = o.post_id"
            " WHERE o.status != 'removed'"
        )
    ]


def write_split(path: Path, rows: list[dict]) -> None:
    text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8", newline="\n")


def load_split(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_sample(out_dir: Path, dev: list[dict], test: list[dict]) -> None:
    """Write dev.jsonl and test.jsonl; refuse once the test split is frozen."""
    if (out_dir / "FROZEN").exists():
        raise EvalError(f"{out_dir / 'FROZEN'} exists: the test split is frozen, not resampling")
    out_dir.mkdir(parents=True, exist_ok=True)
    write_split(out_dir / "dev.jsonl", dev)
    write_split(out_dir / "test.jsonl", test)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(test_path: Path, frozen_path: Path) -> None:
    frozen_path.write_text(_file_sha(test_path) + "\n", encoding="utf-8", newline="\n")


def check_frozen(test_path: Path, frozen_path: Path) -> None:
    """Raise unless test.jsonl is frozen and unchanged since."""
    if not frozen_path.exists():
        raise EvalError(f"{test_path} is not frozen ({frozen_path} missing)")
    if _file_sha(test_path) != frozen_path.read_text(encoding="utf-8").strip():
        raise EvalError(f"{test_path} changed since freeze; see evals/areas/CHANGES.md")
