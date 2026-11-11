"""Area eval regression (spec 7.4): the rules arm on dev and test must not drop below
`evals/areas/baseline.json`, and the test split must still match `FROZEN`.

This file prints numbers only, never rows: the test split is scored, not read."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from outages import evaluate as ev
from outages.areas.entities_rules import RulesExtractor
from outages.areas.gazetteer import load

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "evals" / "areas"
METRICS = ("street_f1", "typed_f1", "untyped_f1", "barangay_f1", "match_rate")


def rules_metrics(split: str) -> dict[str, float]:
    rows = ev.load_split(EVAL_DIR / f"{split}.jsonl")
    gaz = load()
    arm = ev.run_arm(rows, RulesExtractor(), gaz, None)
    brgy = ev.barangay_scores(rows, gaz)
    return {
        "street_f1": arm.scores["street"].f1,
        "typed_f1": arm.scores["typed"].f1,
        "untyped_f1": arm.scores["untyped"].f1,
        "barangay_f1": brgy["prf"].f1,
        "match_rate": brgy["match_rate"],
    }


def test_frozen_unchanged():
    ev.check_frozen(EVAL_DIR / "test.jsonl", EVAL_DIR / "FROZEN")


@pytest.mark.parametrize("split", ["dev", "test"])
def test_rules_not_below_baseline(split):
    base = json.loads((EVAL_DIR / "baseline.json").read_text(encoding="utf-8"))[split]
    got = rules_metrics(split)
    low = {m: (round(got[m], 4), base[m]) for m in METRICS if got[m] < base[m] - 1e-9}
    assert not low, f"{split}: below baseline (got, baseline): {low}"
