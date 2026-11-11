"""Area eval: seeded stratified sample, frozen splits, scoring and the report (spec 7).

The sampler uses its own rough layout heuristic, not `areas.segment`, so segmenter bugs can't
bias which strings get labelled.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from outages.areas.entities import KINDS, Entity, entity_key, normalize_entity
from outages.areas.gazetteer import Gazetteer
from outages.areas.gazetteer import load as load_gazetteer
from outages.areas.segment import segment
from outages.config import OLLAMA_URL, QWEN_MODEL

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


# --- scoring (spec 7.3) ---

DECISION_RULE = (
    "Qwen becomes the default only if its **test street F1 ≥ rules + 5 points** and its "
    "**test typed-entity F1 ≥ rules**. Otherwise rules. Ties go to rules. Haiku is reported, "
    "never the default."
)
BARANGAY_CAVEAT = (
    "Barangay numbers are rules only. Aliases were seeded from the whole corpus's review queue, "
    "test strings included, so they are a coverage and regression number, not a model comparison."
)


@dataclass(frozen=True)
class PRF:
    p: float
    r: float
    f1: float
    tp: int
    fp: int
    fn: int


def prf(tp: int, fp: int, fn: int) -> PRF:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    return PRF(p, r, f1, tp, fp, fn)


def _micro(gold: list[Counter], pred: list[Counter]) -> PRF:
    tp = fp = fn = 0
    for g, p in zip(gold, pred, strict=True):
        hit = sum((g & p).values())
        tp += hit
        fp += sum(p.values()) - hit
        fn += sum(g.values()) - hit
    return prf(tp, fp, fn)


def _only(rows: list[Counter], kind: str) -> list[Counter]:
    return [Counter({k: v for k, v in c.items() if k[0] == kind}) for c in rows]


def _untyped(rows: list[Counter]) -> list[Counter]:
    out = []
    for c in rows:
        u: Counter = Counter()
        for k, v in c.items():
            u[k[1:]] += v
        out.append(u)
    return out


def score_entities(gold: list[list[Entity]], pred: list[list[Entity]]) -> dict:
    """Multiset matching on `entity_key` per string: street, typed, untyped, per kind."""
    g = [Counter(map(entity_key, row)) for row in gold]
    p = [Counter(map(entity_key, row)) for row in pred]
    present = {k[0] for c in g + p for k in c}
    return {
        "street": _micro(_only(g, "street"), _only(p, "street")),
        "typed": _micro(g, p),
        "untyped": _micro(_untyped(g), _untyped(p)),
        "per_kind": {k: _micro(_only(g, k), _only(p, k)) for k in KINDS if k in present},
    }


def score_barangays(gold: list[list[str | None]], pred: list[list[str | None]]) -> dict:
    """Micro P/R/F1 on PSGC codes per string; match rate = predicted rows with a code / all."""
    g = [Counter(c for c in row if c) for row in gold]
    p = [Counter(c for c in row if c) for row in pred]
    rows = sum(len(r) for r in pred)
    coded = sum(1 for r in pred for c in r if c)
    return {"prf": _micro(g, p), "match_rate": coded / rows if rows else 0.0}


def decide(rules: dict, qwen: dict | None) -> tuple[str, str]:
    """The pre-registered rule (spec 2), applied as written. Points = F1 x 100."""
    if qwen is None:
        return "rules", "no qwen result, so rules by default"

    def pts(x: PRF) -> float:
        return round(x.f1 * 100, 6)

    rs, qs = pts(rules["street"]), pts(qwen["street"])
    rt, qt = pts(rules["typed"]), pts(qwen["typed"])
    margin_ok, typed_ok = qs >= round(rs + 5, 6), qt >= rt
    detail = f"street {qs:.1f} vs {rs:.1f} (+5 needed), typed {qt:.1f} vs {rt:.1f}"
    if margin_ok and typed_ok:
        return "qwen", f"qwen meets both conditions: {detail}"
    why = "street margin not met" if not margin_ok else "typed F1 below rules"
    return "rules", f"{why}; {detail}"


# --- running the arms ---


@dataclass
class ArmResult:
    name: str
    scores: dict | None = None
    seconds: list[float] | None = None
    failures: int = 0
    skipped: str | None = None


def gold_entities(row: dict) -> list[Entity]:
    return [x for e in row["entities"] for x in normalize_entity(Entity(e["kind"], e["name"]))]


def run_arm(
    rows: list[dict], extractor, gaz: Gazetteer, conn: sqlite3.Connection | None
) -> ArmResult:
    """Score one extractor over labelled rows. Seconds come from `llm_cache` for LLM arms."""
    from outages.areas.llm import cache_key, render_input

    gold, pred, seconds = [], [], []
    failures = 0
    for row in rows:
        seg = segment(row["areas_raw"])
        _, not_brgy = gaz.resolve(seg)
        start = time.perf_counter()
        found = extractor.extract(seg)
        took = time.perf_counter() - start
        if getattr(extractor, "last_extractor", None) == "rules_fallback":
            failures += 1
        model = getattr(extractor, "model", None)
        if model and conn is not None:
            hit = conn.execute(
                "SELECT seconds FROM llm_cache WHERE key = ?",
                (cache_key(model, render_input(seg)),),
            ).fetchone()
            seconds.append(hit[0] if hit else took)
        else:
            seconds.append(took)
        pred.append([Entity("other", n) for n in not_brgy] + found)
        gold.append(gold_entities(row))
    return ArmResult(extractor.name, score_entities(gold, pred), seconds, failures)


def barangay_scores(rows: list[dict], gaz: Gazetteer) -> dict:
    gold, pred = [], []
    for row in rows:
        matches, _ = gaz.resolve(segment(row["areas_raw"]))
        pred.append([m.psgc_code for m in matches])
        gold.append([b["psgc_code"] for b in row["barangays"]])
    return score_barangays(gold, pred)


def _ollama_digest(model: str) -> str:
    try:
        tags = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=10).json()
    except (httpx.HTTPError, ValueError):
        return "unknown"
    for m in tags.get("models", []):
        if m.get("name") == model:
            return m.get("digest", "unknown")
    return "unknown"


def _build_arm(name: str, conn: sqlite3.Connection):
    """(extractor, None) or (None, skip reason)."""
    from outages.areas.entities_rules import RulesExtractor
    from outages.areas.llm import OllamaExtractor, anthropic_from_env

    if name == "rules":
        return RulesExtractor(), None
    if name == "qwen":
        return OllamaExtractor(conn), None
    if name == "haiku":
        ex = anthropic_from_env(conn)
        return (ex, None) if ex else (None, "no ANTHROPIC_API_KEY")
    raise EvalError(f"unknown arm {name!r}")


def _pct(x: float) -> str:
    return f"{x * 100:.1f}"


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, -(-95 * len(s) // 100) - 1)]


def format_report(
    split: str,
    n: int,
    results: list[ArmResult],
    brgy: dict,
    decision: tuple[str, str] | None,
    now: datetime,
    digest: str | None,
) -> str:
    from outages.areas.llm import PROMPT_VERSION

    out = [
        f"# Areas eval: {split} split ({n} strings), {now:%Y-%m-%d}",
        "",
        "## Decision rule (pre-registered, spec 2)",
        "",
        DECISION_RULE,
        "",
        "## Entities",
        "",
        "| arm | Street F1 | Street P | Street R | Typed F1 | Typed P | Typed R | Untyped F1 "
        "| schema failures | mean s | p95 s |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in results:
        if a.skipped:
            continue
        st, ty, un = a.scores["street"], a.scores["typed"], a.scores["untyped"]
        secs = a.seconds or []
        mean = sum(secs) / len(secs) if secs else 0.0
        out.append(
            f"| {a.name} | {_pct(st.f1)} | {_pct(st.p)} | {_pct(st.r)} | {_pct(ty.f1)} "
            f"| {_pct(ty.p)} | {_pct(ty.r)} | {_pct(un.f1)} | {a.failures} "
            f"| {mean:.2f} | {_p95(secs):.2f} |"
        )
    skipped = [f"{a.name}: skipped ({a.skipped})" for a in results if a.skipped]
    if skipped:
        out += [""] + skipped
    out += ["", "Schema failures = strings that fell back to rules after two invalid answers."]
    out += ["", "### Per kind (F1, tp/fp/fn)", ""]
    for a in results:
        if a.skipped:
            continue
        parts = ", ".join(
            f"{k} {_pct(v.f1)} ({v.tp}/{v.fp}/{v.fn})" for k, v in a.scores["per_kind"].items()
        )
        out.append(f"- {a.name}: {parts}")
    b = brgy["prf"]
    out += [
        "",
        "## Barangays (rules)",
        "",
        f"P {_pct(b.p)}, R {_pct(b.r)}, F1 {_pct(b.f1)} (tp/fp/fn {b.tp}/{b.fp}/{b.fn}); "
        f"match rate {_pct(brgy['match_rate'])}%.",
        "",
        BARANGAY_CAVEAT,
        "",
        "## Models",
        "",
        f"- Qwen: `{QWEN_MODEL}`, digest `{digest or 'not run'}`, PROMPT_VERSION {PROMPT_VERSION}",
    ]
    if decision:
        scope = "" if split == "test" else " (the rule applies to the test split only)"
        out += ["", "## Outcome", "", f"**{decision[0]}**{scope}: {decision[1]}"]
    return "\n".join(out) + "\n"


def run_eval(
    split: str,
    arms: list[str],
    conn: sqlite3.Connection,
    now: datetime,
    eval_dir: Path = EVAL_DIR,
) -> Path:
    """Run the arms on a split and write `results/<date>-<split>.md`. Refuses an unfrozen test."""
    path = eval_dir / f"{split}.jsonl"
    if split == "test":
        check_frozen(path, eval_dir / "FROZEN")
    rows = load_split(path)
    gaz = load_gazetteer()
    results = []
    for name in arms:
        ex, skip = _build_arm(name, conn)
        results.append(ArmResult(name, skipped=skip) if skip else run_arm(rows, ex, gaz, conn))
    by = {a.name: a.scores for a in results if not a.skipped}
    decision = decide(by["rules"], by.get("qwen")) if "rules" in by else None
    digest = _ollama_digest(QWEN_MODEL) if "qwen" in by else None
    brgy = barangay_scores(rows, gaz)
    text = format_report(split, len(rows), results, brgy, decision, now, digest)
    out = eval_dir / "results" / f"{now:%Y-%m-%d}-{split}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out
