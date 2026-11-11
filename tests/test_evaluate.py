"""Eval sampler, splits and freezing (spec 7.1, 7.2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from outages import evaluate as ev

CITY_FIRST = "Portion of Cebu City (Tisa) including portions along Buhisan Road."
BRGY_FIRST = "Portion of Jaclupan, Talisay City, along portions of Cebu-Toledo Wharf Rd."
STRUCTURED = "Portion of Talisay City\nBarangay: Jaclupan\nPuroks: Avocado, Banana"
INLINE = "Portion of Cebu City\nBarangay: Poblacion PardoStreets: Nevada Street"
INLINE_FIRST_LINE = "Portion of Cebu City Barangays: Tejero"
TWO_TOWN = "Portion of Consolacion & Liloan (Lamac & Yati) along Cebu North Road."
TWO_TOWN_BF = "Portion of Mandaue City & Consolacion, Basak, Casili, along Cebu North Road."
WRAPPED = "Portion of Cebu City (Lahug) along Gorordo Avenue,\nincluding Sitio Kawayan."


@pytest.mark.parametrize(
    ("text", "layout"),
    [
        (CITY_FIRST, "city_first"),
        (TWO_TOWN, "city_first"),
        ("A portion of San Fernando (Pitalo) along Natl Highway", "city_first"),
        (BRGY_FIRST, "barangay_first"),
        (TWO_TOWN_BF, "barangay_first"),
        (STRUCTURED, "structured"),
        (INLINE, "structured"),
        (INLINE_FIRST_LINE, "structured"),
        ("testing", "other"),
        ("Portion of Mandaue City DCA Brokerage Corp.", "other"),
    ],
)
def test_rough_layout(text, layout):
    assert ev.rough_layout(text) == layout


@pytest.mark.parametrize(
    ("text", "group"),
    [
        (CITY_FIRST, "cebu"),
        (BRGY_FIRST, "south"),
        ("Portion of Mandaue City (Banilad) along A.S Fortuna Street", "mandaue"),
        ("Portion of Liloan (Tayud) including portion of Sitio Simborio.", "north"),
        (TWO_TOWN, "north"),
        (TWO_TOWN_BF, "multi"),
        ("testing", "none"),
    ],
)
def test_town_group(text, group):
    assert ev.town_group(text) == group


def test_town_count_two_towns():
    assert ev.town_count(TWO_TOWN) == 2
    assert ev.town_count(CITY_FIRST) == 1


def _corpus() -> list[tuple[str, str]]:
    """~150 distinct strings across every layout x group cell, plus the forced shapes."""
    towns = {
        "cebu": "Cebu City",
        "mandaue": "Mandaue City",
        "south": "Minglanilla",
        "north": "Liloan",
    }
    rows = []
    for i in range(12):
        for t in towns.values():
            rows.append((f"s{i}", f"Portion of {t} (Brgy{i}) along Road {i}."))
            rows.append((f"s{i}", f"Portion of Brgy{i}, {t}, along Street {i}."))
            rows.append((f"s{i}", f"Portion of {t}\nBarangays: Brgy{i}\nStreets: Road {i}"))
    rows += [
        ("two1", TWO_TOWN),
        ("two2", TWO_TOWN_BF),
        ("two3", "Portion of Talisay City & Minglanilla (Lawaan I & Tulay) along Road."),
        ("two4", "Portion of Cebu City & Talisay City (Bulacao) along Road."),
        ("inl", INLINE),
        ("wrap", WRAPPED),
        ("oth", "Portion of Mandaue City DCA Brokerage Corp."),
        ("oth2", "testing"),
    ]
    return rows


def _ids(rows):
    return [r["id"] for r in rows]


def test_sample_is_deterministic():
    a = ev.sample(_corpus(), seed=20261111)
    b = ev.sample(list(reversed(_corpus())), seed=20261111)
    assert [r["areas_raw"] for r in a[0] + a[1]] == [r["areas_raw"] for r in b[0] + b[1]]
    c = ev.sample(_corpus(), seed=1)
    assert [r["areas_raw"] for r in a[0] + a[1]] != [r["areas_raw"] for r in c[0] + c[1]]


def test_sample_sizes_and_row_shape():
    dev, test = ev.sample(_corpus(), seed=20261111)
    assert (len(dev), len(test)) == (20, 40)
    r = dev[0]
    assert list(r) == ["id", "areas_hash", "slug", "areas_raw", "towns", "barangays", "entities"]
    assert r["areas_hash"] == hashlib.sha256(r["areas_raw"].encode("utf-8")).hexdigest()
    assert _ids(dev)[:2] == ["dev-01", "dev-02"] and _ids(test)[-1] == "test-40"


def test_sample_stratifies():
    dev, test = ev.sample(_corpus(), seed=20261111)
    cells = {(ev.rough_layout(r["areas_raw"]), ev.town_group(r["areas_raw"])) for r in dev + test}
    for layout in ("city_first", "barangay_first", "structured"):
        for group in ("cebu", "mandaue", "south", "north"):
            assert (layout, group) in cells


def test_sample_forced_picks():
    dev, test = ev.sample(_corpus(), seed=20261111)
    both = dev + test
    assert sum(ev.town_count(r["areas_raw"]) >= 2 for r in both) >= 3
    assert any(ev.has_inline_label(r["areas_raw"]) for r in both)
    assert any(ev.is_wrapped(r["areas_raw"]) for r in both)
    assert any(ev.rough_layout(r["areas_raw"]) == "other" for r in both)
    forced = {TWO_TOWN, TWO_TOWN_BF, INLINE, WRAPPED}
    assert any(r["areas_raw"] in forced for r in dev)
    assert any(r["areas_raw"] in forced for r in test)


def test_sample_distinct_texts():
    rows = _corpus() + [("dup", CITY_FIRST), ("dup2", CITY_FIRST)]
    dev, test = ev.sample(rows, seed=20261111)
    texts = [r["areas_raw"] for r in dev + test]
    assert len(texts) == len(set(texts))


def test_load_split_round_trip(tmp_path):
    dev, _ = ev.sample(_corpus(), seed=20261111)
    p = tmp_path / "dev.jsonl"
    ev.write_split(p, dev)
    assert ev.load_split(p) == dev
    assert p.read_bytes().count(b"\n") == 20 and b"\r" not in p.read_bytes()


def test_format_counts_layout_by_group():
    dev, test = ev.sample(_corpus(), seed=20261111)
    out = ev.format_counts(dev, test)
    assert out.splitlines()[0].split() == [
        "layout",
        "cebu",
        "mandaue",
        "south",
        "north",
        "multi",
        "none",
        "total",
    ]
    assert "total" in out.splitlines()[-1] and out.rstrip().endswith("60")


def test_write_sample_refuses_when_frozen(tmp_path):
    dev, test = ev.sample(_corpus(), seed=20261111)
    ev.write_sample(tmp_path, dev, test)
    assert len(ev.load_split(tmp_path / "test.jsonl")) == 40
    ev.freeze(tmp_path / "test.jsonl", tmp_path / "FROZEN")
    with pytest.raises(ev.EvalError, match="frozen"):
        ev.write_sample(tmp_path, dev, test)


def test_check_frozen_detects_edit(tmp_path):
    t = tmp_path / "test.jsonl"
    f = tmp_path / "FROZEN"
    t.write_text(json.dumps({"id": "test-01"}) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ev.EvalError, match="not frozen"):
        ev.check_frozen(t, f)
    ev.freeze(t, f)
    ev.check_frozen(t, f)
    t.write_text(json.dumps({"id": "test-02"}) + "\n", encoding="utf-8", newline="\n")
    with pytest.raises(ev.EvalError, match="changed"):
        ev.check_frozen(t, f)


def test_frozen_matches():
    """The committed test split is unchanged since the freeze (spec 7.2). Prints nothing."""
    root = Path(__file__).parent.parent / "evals" / "areas"
    ev.check_frozen(root / "test.jsonl", root / "FROZEN")


# --- scoring, decision rule, report (spec 7.3, Task 9) ---

from datetime import UTC, datetime  # noqa: E402

from outages import db  # noqa: E402
from outages.areas.entities import Entity as E  # noqa: E402


def test_prf_counts():
    gold = [[E("street", "A St."), E("street", "B Rd."), E("other", "X")]]
    pred = [[E("street", "A St"), E("street", "C Ave."), E("other", "x")]]
    s = ev.score_entities(gold, pred)
    assert (s["typed"].tp, s["typed"].fp, s["typed"].fn) == (2, 1, 1)
    assert s["typed"].p == pytest.approx(2 / 3) and s["typed"].r == pytest.approx(2 / 3)
    assert s["typed"].f1 == pytest.approx(2 / 3)
    assert (s["street"].tp, s["street"].fp, s["street"].fn) == (1, 1, 1)
    assert s["street"].f1 == pytest.approx(0.5)
    assert s["per_kind"]["other"].f1 == pytest.approx(1.0)


def test_typed_vs_untyped():
    s = ev.score_entities([[E("subdivision", "Villa Uno")]], [[E("other", "Villa Uno")]])
    assert (s["untyped"].tp, s["untyped"].fp, s["untyped"].fn) == (1, 0, 0)
    assert (s["typed"].tp, s["typed"].fp, s["typed"].fn) == (0, 1, 1)


def test_duplicates_are_multiset():
    s = ev.score_entities([[E("street", "A St."), E("street", "A St.")]], [[E("street", "A St.")]])
    assert (s["typed"].tp, s["typed"].fp, s["typed"].fn) == (1, 0, 1)


def test_score_barangays():
    s = ev.score_barangays([["1", "2"], ["3"]], [["1", None], ["3", "4"]])
    assert (s["prf"].tp, s["prf"].fp, s["prf"].fn) == (2, 1, 1)
    assert s["match_rate"] == pytest.approx(3 / 4)


def scores(street: float, typed: float) -> dict:
    return {"street": ev.PRF(0, 0, street, 0, 0, 0), "typed": ev.PRF(0, 0, typed, 0, 0, 0)}


def test_decide_qwen_wins_by_margin():
    winner, why = ev.decide(scores(0.80, 0.70), scores(0.85, 0.70))
    assert winner == "qwen" and why


def test_decide_margin_not_met():
    assert ev.decide(scores(0.80, 0.70), scores(0.849, 0.90))[0] == "rules"


def test_decide_typed_lower():
    assert ev.decide(scores(0.80, 0.70), scores(0.90, 0.699))[0] == "rules"


def test_decide_no_qwen():
    winner, why = ev.decide(scores(0.80, 0.70), None)
    assert winner == "rules" and "qwen" in why


ROW = {
    "id": "dev-01",
    "areas_hash": "h",
    "slug": "s",
    "areas_raw": "Portion of Cebu City (Lahug) along Gorordo Avenue.",
    "towns": ["Cebu City"],
    "barangays": [{"name": "Lahug", "psgc_code": None}],
    "entities": [{"kind": "street", "name": "Gorordo Avenue"}],
}
NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)


def test_eval_run_refuses_unfrozen_test(tmp_path):
    d = tmp_path / "areas"
    d.mkdir()
    ev.write_split(d / "test.jsonl", [ROW])
    conn = db.connect(tmp_path / "t.db")
    with pytest.raises(ev.EvalError, match="not frozen"):
        ev.run_eval("test", ["rules"], conn, NOW, eval_dir=d)
    ev.freeze(d / "test.jsonl", d / "FROZEN")
    (d / "test.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ev.EvalError, match="changed"):
        ev.run_eval("test", ["rules"], conn, NOW, eval_dir=d)
    conn.close()


def test_report_lists_skipped_haiku(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "areas"
    d.mkdir()
    ev.write_split(d / "dev.jsonl", [ROW])
    conn = db.connect(tmp_path / "t.db")
    out = ev.run_eval("dev", ["rules", "haiku"], conn, NOW, eval_dir=d)
    conn.close()
    assert out == d / "results" / "2026-11-11-dev.md"
    text = out.read_text(encoding="utf-8")
    assert "haiku: skipped (no ANTHROPIC_API_KEY)" in text
    assert "rules" in text and "Street F1" in text
    assert "+ 5 points" in text  # the pre-registered rule is stated
    assert "coverage" in text  # the barangay caveat
