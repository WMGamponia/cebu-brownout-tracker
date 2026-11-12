"""Street eval: seeded stratified sample of street mentions, and the scorer (spec 5).

The sample is drawn before any match rule exists, so the rules can't bias which mentions get
labelled. Splits, freezing and the frozen check are `outages.evaluate`'s.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from outages.config import VECO_TOWNS
from outages.evaluate import EvalError, check_frozen, load_split
from outages.osm.extract import StreetIndex
from outages.streets.match import load_aliases, match_street, scope_for, search_towns
from outages.streets.normalize import street_key

EVAL_DIR = Path("evals") / "streets"
P5_DIR = "p5"  # Phase 5's retired split and results (history; Streets v2 spec 4 step 2)
TEST_ROWS = "test.rows.jsonl"  # the v2 test rows, no gold
ROWS_FROZEN = "ROWS_FROZEN"
ALIASES_FROZEN = "ALIASES_FROZEN"
SEED = 20261112
PER_TOWN = 10
N_DEV = 30  # spec 5.1: 30 dev / 50 test (8 towns: six give 4 dev rows, two give 3)
SCOPE_KINDS = ("one", "several", "none")
PLACED = ("exact", "alias", "ambiguous")
DRAWN = ("matched", "alias")


@dataclass(frozen=True)
class StreetRow:
    areas_hash: str
    seq: int
    name: str
    areas_raw: str
    towns: list[str]
    barangays: list[str]  # placed psgc codes, in parse order
    scope_kind: str  # one | several | none
    town_psgc: str | None  # first placed barangay's town, else the first listed VECO town


def street_rows(conn: sqlite3.Connection) -> list[StreetRow]:
    """Every street entity of an areas text used by a non-removed outage."""
    raw = {
        h: text
        for h, text in conn.execute(
            "SELECT areas_hash, areas_raw FROM outages WHERE status != 'removed'"
            " AND areas_hash IS NOT NULL ORDER BY id DESC"
        )
    }
    towns = {
        h: json.loads(t) for h, t in conn.execute("SELECT areas_hash, towns_json FROM area_parses")
    }
    placed: dict[str, list[tuple[str, str]]] = {}
    for h, psgc, town in conn.execute(
        "SELECT areas_hash, psgc_code, town_psgc FROM area_barangays"
        f" WHERE match IN {PLACED} AND psgc_code IS NOT NULL ORDER BY areas_hash, seq"
    ):
        if psgc not in [p for p, _ in placed.get(h, [])]:
            placed.setdefault(h, []).append((psgc, town))
    out = []
    for h, seq, name in conn.execute(
        "SELECT areas_hash, seq, name FROM area_entities WHERE kind = 'street'"
        " ORDER BY areas_hash, seq"
    ):
        if h not in raw:
            continue
        brgys = placed.get(h, [])
        listed = towns.get(h, [])
        n = len(brgys)
        town = (
            brgys[0][1] if brgys else next((VECO_TOWNS[t] for t in listed if t in VECO_TOWNS), None)
        )
        out.append(
            StreetRow(
                areas_hash=h,
                seq=seq,
                name=name,
                areas_raw=raw[h],
                towns=listed,
                barangays=[p for p, _ in brgys],
                scope_kind="none" if n == 0 else "one" if n == 1 else "several",
                town_psgc=town,
            )
        )
    return out


def _allocate(counts: dict[str, int], n: int) -> dict[str, int]:
    """Largest-remainder allocation of `n` over `counts`, capped by what each kind has."""
    total = sum(counts.values())
    if not total:
        return dict.fromkeys(counts, 0)
    quotas = {k: n * c / total for k, c in counts.items()}
    alloc = {k: min(int(q), counts[k]) for k, q in quotas.items()}
    for k in sorted(counts, key=lambda k: (-(quotas[k] - int(quotas[k])), SCOPE_KINDS.index(k))):
        if sum(alloc.values()) >= n:
            break
        if alloc[k] < counts[k]:
            alloc[k] += 1
    return alloc


def _label_row(i: str, r: StreetRow) -> dict:
    return {
        "id": i,
        "areas_hash": r.areas_hash,
        "seq": r.seq,
        "name": r.name,
        "areas_raw": r.areas_raw,
        "towns": r.towns,
        "barangays": r.barangays,
        "gold": None,
    }


def sample_streets(rows: list[StreetRow], seed: int = SEED) -> tuple[list[dict], list[dict]]:
    """80 mentions with distinct keys: 10 per VECO town, scope kinds proportional within a
    town; 30 dev, 50 test, every town in both. Returns (dev, test) label rows."""
    rng = random.Random(seed)
    rows = sorted(rows, key=lambda r: (r.areas_hash, r.seq))
    used: set[str] = set()
    dev: list[StreetRow] = []
    test: list[StreetRow] = []
    towns = list(VECO_TOWNS.values())
    order = towns[:]
    rng.shuffle(order)
    base, extra = divmod(N_DEV, len(towns))
    n_dev = {t: base + (i < extra) for i, t in enumerate(order)}
    for town in towns:
        pools: dict[str, list[StreetRow]] = {k: [] for k in SCOPE_KINDS}
        for r in rows:
            if r.town_psgc == town and street_key(r.name):
                pools[r.scope_kind].append(r)
        for pool in pools.values():
            rng.shuffle(pool)
        want = _allocate({k: len(p) for k, p in pools.items()}, PER_TOWN)
        picked: list[StreetRow] = []
        # Each kind its quota, then (when duplicate keys left a kind short) any kind.
        for quota in (want, dict.fromkeys(SCOPE_KINDS, PER_TOWN)):
            for kind in SCOPE_KINDS:
                got = sum(r.scope_kind == kind for r in picked)
                for r in pools[kind]:
                    if got >= quota[kind] or len(picked) >= PER_TOWN:
                        break
                    k = street_key(r.name)
                    if k not in used:
                        used.add(k)
                        picked.append(r)
                        got += 1
        rng.shuffle(picked)
        dev += picked[: n_dev[town]]
        test += picked[n_dev[town] :]
    rng.shuffle(dev)
    rng.shuffle(test)
    return (
        [_label_row(f"dev-{i:02d}", r) for i, r in enumerate(dev, 1)],
        [_label_row(f"test-{i:02d}", r) for i, r in enumerate(test, 1)],
    )


class GuardError(EvalError):
    """A refusal (spec 10): a guard failed or labels don't fit; the CLI exits 2."""


def gold_keys(gold: str | list[str] | None) -> set[str]:
    """Gold may be one name, a list (a road renamed inside the scope) or null (GUIDE v2)."""
    if gold is None:
        return set()
    names = [gold] if isinstance(gold, str) else gold
    return {k for k in (street_key(n) for n in names) if k}


def is_correct(gold: str | list[str] | None, osm_name: str | None) -> bool:
    return bool(osm_name) and street_key(osm_name) in gold_keys(gold)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95 % Wilson score interval for k successes in n."""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def check_osm_base(rows: list[dict], index_osm_base: str | None) -> None:
    """Labels name the extract they were made against; refuse any other (spec 5.2)."""
    for r in rows:
        if "osm_base" in r and r["osm_base"] != index_osm_base:
            raise GuardError(
                f"row {r.get('id')} was labelled against osm_base {r['osm_base']}, but the "
                f"extract is {index_osm_base}; labels and extract must match"
            )


def score(rows: list[dict], matches: dict) -> dict:
    """Precision = correctly drawn / drawn; coverage = correctly drawn / rows with a gold name
    (spec 5.3). A drawn row is correct when its OSM name's key is among the gold keys."""
    by_status: Counter[str] = Counter()
    by_drawn = {k: {"drawn": 0, "correct": 0} for k in DRAWN}
    by_town: dict[str, dict[str, int]] = {}
    drawn = correct = with_gold = 0
    for row in rows:
        m = matches.get((row["areas_hash"], row["seq"]))
        status = m.status if m else "missing"
        by_status[status] += 1
        gold = row.get("gold")
        with_gold += gold is not None
        town = by_town.setdefault(row.get("town_psgc") or "?", {"drawn": 0, "correct": 0})
        if status in DRAWN:
            ok = is_correct(gold, m.osm_name)
            drawn += 1
            correct += ok
            for bucket in (by_drawn[status], town):
                bucket["drawn"] += 1
                bucket["correct"] += ok
    return {
        "precision": correct / drawn if drawn else None,
        "coverage": correct / with_gold if with_gold else 0.0,
        "drawn": drawn,
        "correct": correct,
        "with_gold": with_gold,
        "by_status": dict(by_status),
        "by_drawn_status": by_drawn,
        "by_town": by_town,
        "interval": wilson(correct, drawn),
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


def _interval(iv: tuple[float, float] | None) -> str:
    return "n/a" if iv is None else f"[{iv[0]:.3f}, {iv[1]:.3f}]"


def _gold_text(gold: str | list[str] | None) -> str:
    if gold is None:
        return "—"
    return gold if isinstance(gold, str) else " / ".join(gold)


def _town_name(psgc: str) -> str:
    names = {v: k for k, v in VECO_TOWNS.items()}
    return names.get(psgc, psgc)


def run_streets(
    split: str, eval_dir: Path, osm_dir: Path, boundaries: dict, now: datetime
) -> tuple[Path, dict]:
    """Match every labelled row in memory (no DB, no store), score, write the report.

    The test split must be frozen and unchanged (spec 5.4)."""
    path = eval_dir / f"{split}.jsonl"
    if split == "test":
        check_frozen(path, eval_dir / "FROZEN")
    index = StreetIndex.load(osm_dir)
    if index is None:
        raise EvalError(f"no OSM extract in {osm_dir}; run `streets fetch` first")
    rows = load_split(path)
    check_osm_base(rows, index.osm_base)
    aliases = load_aliases()
    matches = {}
    lines = [
        f"# Street eval — {split} — {now:%Y-%m-%d}",
        "",
        f"Extract osm_base {index.osm_base}; {len(aliases)} aliases; rules only.",
        "",
    ]
    table = ["| id | VECO name | gold | status | OSM name | ok |", "|---|---|---|---|---|---|"]
    for r in rows:
        towns = [VECO_TOWNS[t] for t in r["towns"] if t in VECO_TOWNS]
        scope = scope_for(r["barangays"], towns, boundaries)
        m = match_street(
            r["name"], scope, search_towns(r["barangays"], towns, boundaries), index, aliases
        )
        matches[(r["areas_hash"], r["seq"])] = m
        drawn = m.status in DRAWN
        ok = (drawn and is_correct(r["gold"], m.osm_name)) or (not drawn and r["gold"] is None)
        mark = "✓" if ok else ("✗ wrong line" if drawn else "· missed")
        table.append(
            f"| {r['id']} | {r['name']} | {_gold_text(r['gold'])} | {m.status}"
            f" | {m.osm_name or '—'}"
            f" | {mark} |"
        )
    s = score(rows, matches)
    lines += [
        f"- **precision** {_pct(s['precision'])} ({s['correct']} of {s['drawn']} drawn)",
        f"- 95 % Wilson interval on precision: {_interval(s['interval'])}",
        f"- **coverage** {_pct(s['coverage'])} ({s['correct']} of {s['with_gold']} with gold)",
        "- drawn by status: "
        + ", ".join(f"{k} {v['correct']}/{v['drawn']}" for k, v in s["by_drawn_status"].items()),
        "- drawn by town: "
        + (
            ", ".join(
                f"{_town_name(t)} {v['correct']}/{v['drawn']}"
                for t, v in sorted(s["by_town"].items())
                if v["drawn"]
            )
            or "none"
        ),
        f"- by status: {', '.join(f'{k} {v}' for k, v in sorted(s['by_status'].items()))}",
        f"- gate (spec 5.4, test split only): precision ≥ 0.95 with ≥ 20 drawn → "
        f"{'on' if gate(s) else 'off'}",
        "",
        *table,
        "",
    ]
    out_dir = eval_dir / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{now:%Y-%m-%d}-{split}.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out, s


def gate(s: dict) -> bool:
    """Spec 5.4: drawing on only with test precision ≥ 0.95 over ≥ 20 drawn rows."""
    return s["precision"] is not None and s["precision"] >= 0.95 and s["drawn"] >= 20


# --- Streets v2: the 150-row test split (spec 6.1-6.3) ---

SEED_V2 = 20261113
N_TEST_V2 = 150
REVIEW_CANDIDATES = 15  # per row in label-review.md, closest first
ROW_FIELDS = ("id", "areas_hash", "seq", "name", "areas_raw", "towns", "barangays", "town_psgc")


def sample_v2(
    rows: list[StreetRow], exclude_keys: set[str], n: int = N_TEST_V2, seed: int = SEED_V2
) -> list[dict]:
    """Mentions drawn uniformly (seeded), one per (town, street_key), keys in `exclude_keys`
    left out everywhere. Rows carry no gold."""
    pool = sorted(
        (r for r in rows if street_key(r.name) and street_key(r.name) not in exclude_keys),
        key=lambda r: (r.areas_hash, r.seq),
    )
    random.Random(seed).shuffle(pool)
    taken: set[tuple[str | None, str]] = set()
    out: list[dict] = []
    for r in pool:
        pair = (r.town_psgc, street_key(r.name))
        if pair in taken:
            continue
        taken.add(pair)
        out.append(
            {
                "id": f"t2-{len(out) + 1:03d}",
                "areas_hash": r.areas_hash,
                "seq": r.seq,
                "name": r.name,
                "areas_raw": r.areas_raw,
                "towns": r.towns,
                "barangays": r.barangays,
                "town_psgc": r.town_psgc,
            }
        )
        if len(out) >= n:
            break
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_rows_frozen(rows_path: Path) -> None:
    frozen = rows_path.parent / ROWS_FROZEN
    if not frozen.is_file():
        raise GuardError(f"{frozen} missing: the test rows were never frozen")
    if _sha(rows_path) != frozen.read_text(encoding="utf-8").strip():
        raise GuardError(f"{rows_path} changed since {ROWS_FROZEN} was written")


def check_labels(rows_path: Path, test_path: Path, index_osm_base: str | None) -> dict:
    """The labelled file fits the frozen rows (same ids, same mentions, in order), every row
    has a gold of the right type and the extract's osm_base. Returns counts only: no gold
    ever reaches a message (spec 6.2)."""
    check_rows_frozen(rows_path)
    rows = load_split(rows_path)
    labels = load_split(test_path) if test_path.is_file() else []
    by_id = {r.get("id"): r for r in labels}
    for r in rows:
        lab = by_id.get(r["id"])
        if lab is None:
            raise GuardError(f"row {r['id']} is missing from {test_path.name}")
        if (lab.get("areas_hash"), lab.get("seq")) != (r["areas_hash"], r["seq"]):
            raise GuardError(f"row {r['id']}: areas_hash/seq differ from the frozen rows")
        if "gold" not in lab:
            raise GuardError(f"row {r['id']} has no gold field")
        g = lab["gold"]
        ok = g is None or (isinstance(g, str) and g.strip())
        ok = ok or (isinstance(g, list) and g and all(isinstance(x, str) and x for x in g))
        if not ok:
            raise GuardError(f"row {r['id']}: gold must be a name, a list of names or null")
        if lab.get("osm_base") != index_osm_base:
            raise GuardError(
                f"row {r['id']}: osm_base {lab.get('osm_base')} differs from the extract's "
                f"{index_osm_base}"
            )
    extra = [str(i) for i in by_id if i not in {r["id"] for r in rows}]
    if extra or len(labels) != len(rows):
        raise GuardError(f"{test_path.name} has rows not in the frozen rows: {', '.join(extra)}")
    golds = [lab["gold"] for lab in labels]
    return {
        "rows": len(labels),
        "null": sum(g is None for g in golds),
        "non_null": sum(g is not None for g in golds),
        "lists": sum(isinstance(g, list) for g in golds),
        "notes": sum(bool(lab.get("note")) for lab in labels),
    }


def review_sample(test_rows: list[dict], n: int = 25, seed: int = SEED_V2) -> list[dict]:
    """`n` rows for the owner's review (spec 6.3): every list gold and `two roads` note first
    (when they fit), then round-robin over (town, null / non-null) strata. Sorted by id."""
    rng = random.Random(seed)
    rows = sorted(test_rows, key=lambda r: r["id"])
    special = [
        r
        for r in rows
        if isinstance(r.get("gold"), list) or "two roads" in (r.get("note") or "").lower()
    ]
    picked = special[:n] if len(special) <= n else rng.sample(special, n)
    ids = {r["id"] for r in picked}
    strata: dict[tuple[str, bool], list[dict]] = {}
    for r in rows:
        if r["id"] not in ids:
            strata.setdefault((r.get("town_psgc") or "?", r.get("gold") is None), []).append(r)
    for pool in strata.values():
        rng.shuffle(pool)
    keys = sorted(strata)
    rng.shuffle(keys)
    while len(picked) < n and any(strata[k] for k in keys):
        for k in keys:
            if strata[k] and len(picked) < n:
                picked.append(strata[k].pop())
    return sorted(picked, key=lambda r: r["id"])


def format_review(rows: list[dict], index: StreetIndex | None, boundaries: dict) -> str:
    """`data/streets/label-review.md`: each sampled row with its gold, note and the in-scope
    candidate names, for the owner (spec 6.3)."""
    from outages.streets.candidates import in_scope

    out = [
        "# Street test labels — owner review",
        "",
        f"{len(rows)} rows. For each: is `gold` the OSM name VECO means (GUIDE v2)? Write a",
        "correction under the row (`correct: <new gold>` or `correct: null`), or tell the session",
        "the row id and the new gold.",
        "",
    ]
    for r in rows:
        g = r.get("gold")
        gold = "null" if g is None else json.dumps(g, ensure_ascii=False)
        out += [
            f"## {r['id']} — {_town_name(r.get('town_psgc') or '?')}",
            "",
            f"- VECO name: **{r['name']}**",
            f"- gold: **{gold}**" + (f" — note: {r['note']}" if r.get("note") else ""),
            "- area text:",
            "",
            *[f"  > {line}" for line in (r.get("areas_raw") or "").splitlines()],
            "",
            f"- in-scope OSM names, the {REVIEW_CANDIDATES} closest to VECO's name"
            " (primary [tags] (ways)):",
        ]
        cands = in_scope(r, index, boundaries) if index else []
        key = street_key(r["name"])
        ranked = sorted(
            cands,
            key=lambda c: (
                -max(
                    difflib.SequenceMatcher(None, key, street_key(v)).ratio()
                    for v in c["tags"].values()
                )
            ),
        )
        for c in ranked[:REVIEW_CANDIDATES]:
            tags = " ".join(f"{k}={v}" for k, v in c["tags"].items())
            out.append(f"  - {c['primary']} [{tags}] ({c['ways']})")
        if not cands:
            out.append("  - (none)")
        elif len(cands) > REVIEW_CANDIDATES:
            out.append(f"  - … {len(cands) - REVIEW_CANDIDATES} more in scope")
        out.append("")
    return "\n".join(out)
