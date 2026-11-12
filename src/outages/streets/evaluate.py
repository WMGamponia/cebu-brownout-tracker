"""Street eval: seeded stratified sample of street mentions, and the scorer (spec 5).

The sample is drawn before any match rule exists, so the rules can't bias which mentions get
labelled. Splits, freezing and the frozen check are `outages.evaluate`'s.
"""

from __future__ import annotations

import json
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


def score(rows: list[dict], matches: dict) -> dict:
    """Precision = correctly drawn / drawn; coverage = correctly drawn / rows with a gold name
    (spec 5.3). A drawn row is correct when its OSM name's key equals the gold's key."""
    by_status: Counter[str] = Counter()
    drawn = correct = with_gold = 0
    for row in rows:
        m = matches.get((row["areas_hash"], row["seq"]))
        status = m.status if m else "missing"
        by_status[status] += 1
        gold = row.get("gold")
        with_gold += gold is not None
        if status in DRAWN:
            drawn += 1
            if gold is not None and m.osm_name and street_key(m.osm_name) == street_key(gold):
                correct += 1
    return {
        "precision": correct / drawn if drawn else None,
        "coverage": correct / with_gold if with_gold else 0.0,
        "drawn": drawn,
        "correct": correct,
        "with_gold": with_gold,
        "by_status": dict(by_status),
    }


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.3f}"


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
        ok = (
            drawn
            and r["gold"] is not None
            and street_key(m.osm_name or "") == street_key(r["gold"])
        ) or (not drawn and r["gold"] is None)
        mark = "✓" if ok else ("✗ wrong line" if drawn else "· missed")
        table.append(
            f"| {r['id']} | {r['name']} | {r['gold'] or '—'} | {m.status} | {m.osm_name or '—'}"
            f" | {mark} |"
        )
    s = score(rows, matches)
    lines += [
        f"- **precision** {_pct(s['precision'])} ({s['correct']} of {s['drawn']} drawn)",
        f"- **coverage** {_pct(s['coverage'])} ({s['correct']} of {s['with_gold']} with gold)",
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
