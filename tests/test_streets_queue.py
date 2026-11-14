"""Suggestion queue, `streets suggest`, `--apply` and the review grouping (spec 7.6-7.7)."""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime

import pytest
from map_world import boundaries, populate
from osm_world import CEBU, CONSOLACION, OSM_BASE

from outages import db
from outages.__main__ import main
from outages.areas.llm import LLMUnavailable
from outages.osm.extract import StreetIndex, Way
from outages.streets import queue
from outages.streets.match import load_aliases, load_rejects
from outages.streets.review import review
from outages.streets.suggest import STREET_PROMPT_VERSION, Pick, apply, run

NOW = datetime(2026, 11, 13, 1, 0, tzinfo=UTC)
TODAY = date(2026, 11, 13)


@pytest.fixture(scope="module")
def bnd():
    return boundaries()


def _row(town=CEBU, key="x street", mentions=1, decision="", **kw) -> dict:
    r = {c: "" for c in queue.COLUMNS}
    r.update(town_psgc=town, town="Cebu City", veco_key=key, veco_name=key.title())
    r.update(mentions=str(mentions), status="ok", decision=decision, **kw)
    return r


# --- queue file ---------------------------------------------------------------------------


def test_merge_keeps_decisions():
    old = [_row(key="a street", mentions=2, decision="y", pick="A Street")]
    new = [_row(key="a street", mentions=9, pick="Other"), _row(key="b street", mentions=5)]
    out = queue.merge(old, new)
    assert [(r["veco_key"], r["decision"], r["pick"]) for r in out] == [
        ("b street", "", ""),
        ("a street", "y", "A Street"),
    ]


def test_queue_sorted_by_mentions(tmp_path):
    p = tmp_path / "q.csv"
    queue.write(p, [_row(key=k, mentions=m) for k, m in (("a", 2), ("b", 10), ("c", 3))])
    assert [r["veco_key"] for r in queue.read(p)] == ["b", "c", "a"]
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")  # BOM for spreadsheets


def test_queue_write_when_locked_keeps_file(tmp_path, monkeypatch):
    p = tmp_path / "q.csv"
    queue.write(p, [_row()])
    before = p.read_bytes()

    def held(*a):
        raise PermissionError("in use")

    monkeypatch.setattr(queue.os, "replace", held)
    with pytest.raises(queue.QueueBusy):
        queue.write(p, [_row(), _row(key="y street")])
    assert p.read_bytes() == before
    assert [f.name for f in tmp_path.iterdir()] == ["q.csv"]


def test_queue_reads_excel_saved_csv(tmp_path):
    p = tmp_path / "q.csv"
    header = ",".join(queue.COLUMNS)
    line = ",".join(_row(decision="y", pick="X Street")[c] for c in queue.COLUMNS)
    p.write_bytes(b"\xef\xbb\xbf" + f"{header}\r\n{line}\r\n".encode())
    (r,) = queue.read(p)
    assert (r["town_psgc"], r["decision"]) == (CEBU, "y")


def test_queue_missing_column_refused(tmp_path):
    p = tmp_path / "q.csv"
    p.write_text("town_psgc,veco_key\n1,x\n", encoding="utf-8")
    with pytest.raises(queue.QueueError, match="decision"):
        queue.read(p)


def test_set_decision_rules(tmp_path):
    p = tmp_path / "q.csv"
    queue.write(
        p,
        [
            _row(key="a", pick="A Street", candidates="A Street|B Street"),
            _row(key="b", pick="", candidates="C Street"),
        ],
    )
    queue.set_decision(p, CEBU, "a", "skip")
    assert queue.read(p)[0]["decision"] == ""
    queue.set_decision(p, CEBU, "a", "B Street")
    with pytest.raises(queue.QueueConflict):
        queue.set_decision(p, CEBU, "a", "y")
    with pytest.raises(ValueError):
        queue.set_decision(p, CEBU, "b", "y")  # nothing picked
    with pytest.raises(ValueError):
        queue.set_decision(p, CEBU, "b", "Elsewhere Street")
    with pytest.raises(KeyError):
        queue.set_decision(p, CONSOLACION, "a", "n")
    queue.set_decision(p, CEBU, "b", "n")
    assert {r["veco_key"]: r["decision"] for r in queue.read(p)} == {"a": "B Street", "b": "n"}


# --- suggest run --------------------------------------------------------------------------


def _conn(tmp_path, streets):
    conn = db.connect(tmp_path / "o.db")
    populate(conn)
    with conn:
        for h, seq, name in streets:
            conn.execute(
                "INSERT INTO area_entities (areas_hash, seq, kind, name, extractor)"
                " VALUES (?, ?, 'street', ?, 'rules')",
                (h, seq, name),
            )
            conn.execute(
                "INSERT INTO street_matches (areas_hash, seq, status) VALUES (?, ?, 'not_found')",
                (h, seq),
            )
    return conn


def _way(wid, name, row=0, x0=123.811):
    y = 10.3005 + row * 0.0006
    return Way(wid, {"name": name}, [(x0 + k * 0.002, y) for k in range(3)], CEBU)


INDEX = StreetIndex(
    OSM_BASE,
    [
        _way(1, "Gorordo Avenue"),
        _way(2, "Escario Street", row=1),
        _way(3, "Far Street", x0=123.835),  # outside Lahug + 150 m
    ],
)
STREETS = [("hA", 5, "Gorordo Avnue"), ("hC", 5, "Gorordo Avnue"), ("hA", 6, "Escarrio St")]


class FakeQwen:
    name, model = "qwen", "qwen3:14b"

    def __init__(self, picks=None, down=False):
        self.picks, self.down, self.calls = picks or {}, down, 0

    def check(self):
        if self.down:
            raise LLMUnavailable("Ollama not reachable")

    def pick(self, g, cands):
        self.calls += 1
        if not cands:
            return Pick("no_candidates", None, "")
        return Pick("ok", self.picks.get(g.key), "fake reason")


def _run(tmp_path, conn, picker, **kw):
    data = tmp_path / "data"
    kw.setdefault("rejects_path", tmp_path / "rejects.csv")
    return run(conn, INDEX, boundaries(), picker, tmp_path / "evals", data, NOW, **kw)


def test_suggest_run_both_arms(tmp_path):
    conn = _conn(tmp_path, [*STREETS, ("hD", 5, "Gorordo Avnue")])
    s = _run(tmp_path, conn, FakeQwen({"gorordo avnue": "Escario Street"}), primary="qwen")
    assert (s.new, s.picked, s.no_candidates) == (3, 1, 1)  # qwen picks for Gorordo only
    rows = {
        (r["town_psgc"], r["veco_key"]): r
        for r in queue.read(tmp_path / "data" / "streets" / "alias_queue.csv")
    }
    g = rows[(CEBU, "gorordo avnue")]
    assert (g["pick"], g["pick_source"], g["other_pick"]) == (
        "Escario Street",
        "qwen",
        "Gorordo Avenue",
    )
    assert (g["mentions"], g["status"], g["town"], g["reason"]) == (
        "2",
        "ok",
        "Cebu City",
        "fake reason",
    )
    assert "Far Street" not in g["candidates"]
    assert rows[(CONSOLACION, "gorordo avnue")]["status"] == "no_candidates"
    log = (tmp_path / "data" / "streets" / "suggest_inputs.jsonl").read_text(encoding="utf-8")
    sent = [(x["areas_hash"], x["seq"]) for x in map(json.loads, log.splitlines())]
    assert sorted(sent) == [("hA", 5), ("hA", 6), ("hC", 5)]  # hD had no candidates: not sent
    conn.close()


def test_suggest_run_difflib_primary(tmp_path):
    conn = _conn(tmp_path, STREETS)
    _run(tmp_path, conn, FakeQwen({"gorordo avnue": "Escario Street"}), primary="difflib")
    (g,) = [
        r
        for r in queue.read(tmp_path / "data" / "streets" / "alias_queue.csv")
        if r["veco_key"] == "gorordo avnue"
    ]
    assert (g["pick"], g["pick_source"], g["other_pick"]) == (
        "Gorordo Avenue",
        "difflib",
        "Escario Street",
    )
    conn.close()


def test_suggest_run_respects_limit_and_budget(tmp_path):
    conn = _conn(tmp_path, STREETS)
    assert _run(tmp_path, conn, FakeQwen(), budget_s=0).new == 0
    assert _run(tmp_path, conn, FakeQwen(), limit=1).new == 1
    path = tmp_path / "data" / "streets" / "alias_queue.csv"
    queue.set_decision(path, CEBU, queue.read(path)[0]["veco_key"], "n")
    assert _run(tmp_path, conn, FakeQwen()).new == 1
    rows = queue.read(path)
    assert len(rows) == 2 and sum(r["decision"] == "n" for r in rows) == 1
    conn.close()


def test_suggest_run_skips_test_rows(tmp_path):
    conn = _conn(tmp_path, STREETS)
    ev = tmp_path / "evals"
    ev.mkdir()
    rows = [{"id": "t2-001", "areas_hash": "hA", "seq": 6}]
    (ev / "test.rows.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    assert _run(tmp_path, conn, FakeQwen()).new == 1  # only Gorordo; Escarrio is a test row
    conn.close()


def test_suggest_run_ollama_down_writes_nothing(tmp_path):
    conn = _conn(tmp_path, STREETS)
    with pytest.raises(LLMUnavailable):
        _run(tmp_path, conn, FakeQwen(down=True))
    assert not (tmp_path / "data" / "streets").exists()
    conn.close()


# --- apply --------------------------------------------------------------------------------


def _aliases(tmp_path):
    p = tmp_path / "aliases.csv"
    p.write_text(
        "town_psgc,veco_key,osm_name\n702250000,bugos street,Burgos Street\n", encoding="utf-8"
    )
    return p


def _apply(tmp_path, conn, rows):
    q = tmp_path / "q.csv"
    queue.write(q, rows)
    a, rj = _aliases(tmp_path), tmp_path / "rejects.csv"
    s = apply(q, a, rj, INDEX, boundaries(), conn, TODAY)
    return s, q, a, rj


def _dicts(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_apply_provenance(tmp_path):
    conn = _conn(tmp_path, STREETS)
    rows = [
        _row(
            key="gorordo avnue",
            mentions=2,
            decision="y",
            pick="Gorordo Avenue",
            pick_source="qwen",
            other_pick="Gorordo Avenue",
            candidates="Gorordo Avenue|Escario Street",
        ),
        _row(
            key="escarrio street",
            decision="Escario Street",
            pick="Gorordo Avenue",
            pick_source="difflib",
            candidates="Gorordo Avenue|Escario Street",
        ),
        _row(key="later street", pick="X"),  # undecided: stays
    ]
    s, q, a, _ = _apply(tmp_path, conn, rows)
    assert s.accepted_by_source == {"qwen": 1, "owner": 1}
    assert (s.rejected, s.refused) == (0, [])
    assert s.decided_by_source == {"qwen": 1, "difflib": 1}
    out = _dicts(a)
    assert out[0]["source"] == "manual"
    assert out[1] == {
        "town_psgc": CEBU,
        "veco_key": "gorordo avnue",
        "osm_name": "Gorordo Avenue",
        "source": "qwen",
        "model": "qwen3:14b",
        "prompt_version": STREET_PROMPT_VERSION,
        "decided": "2026-11-13",
    }
    assert (out[2]["source"], out[2]["osm_name"], out[2]["model"]) == (
        "owner",
        "Escario Street",
        "",
    )
    assert load_aliases(a)[(CEBU, "escarrio street")] == "Escario Street"
    assert [r["veco_key"] for r in queue.read(q)] == ["later street"]
    conn.close()


def test_apply_rejects_recorded_never_requeued(tmp_path):
    conn = _conn(tmp_path, STREETS)
    s, q, _, rj = _apply(
        tmp_path,
        conn,
        [
            _row(
                key="gorordo avnue",
                mentions=2,
                decision="n",
                pick="Gorordo Avenue",
                pick_source="qwen",
            )
        ],
    )
    assert s.rejected == 1 and s.decided_by_source == {"qwen": 1} and s.accepted_by_source == {}
    assert _dicts(rj) == [{"town_psgc": CEBU, "veco_key": "gorordo avnue", "decided": "2026-11-13"}]
    assert load_rejects(rj) == {(CEBU, "gorordo avnue")}
    assert queue.read(q) == []
    data = tmp_path / "data"
    run(conn, INDEX, boundaries(), FakeQwen(), tmp_path / "evals", data, NOW, rejects_path=rj)
    keys = [r["veco_key"] for r in queue.read(data / "streets" / "alias_queue.csv")]
    assert keys == ["escarrio street"]
    conn.close()


def test_apply_refuses_out_of_scope_name(tmp_path):
    conn = _conn(tmp_path, STREETS)
    rows = [
        _row(key="gorordo avnue", mentions=2, decision="Far Street", pick="Gorordo Avenue"),
        _row(key="escarrio street", decision="maybe", pick="Escario Street"),
        _row(key="nowhere street", decision="y", pick="Escario Street"),  # no such group
        _row(key="escarrio street", town=CONSOLACION, decision="y", pick="", pick_source="qwen"),
    ]
    s, q, a, _ = _apply(tmp_path, conn, rows)
    assert len(s.refused) == 4 and s.accepted_by_source == {}
    assert len(_dicts(a)) == 1
    assert len(queue.read(q)) == 4  # refused rows stay for the owner to fix
    conn.close()


def test_apply_one_good_row_among_refused(tmp_path):
    conn = _conn(tmp_path, STREETS)
    rows = [
        _row(key="gorordo avnue", mentions=2, decision="Far Street", pick="Gorordo Avenue"),
        _row(key="escarrio street", decision="y", pick="Escario Street", pick_source="difflib"),
    ]
    s, q, a, _ = _apply(tmp_path, conn, rows)
    assert len(s.refused) == 1 and s.accepted_by_source == {"difflib": 1}
    assert [r["veco_key"] for r in queue.read(q)] == ["gorordo avnue"]
    conn.close()


def test_load_aliases_ignores_extra_columns(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text(
        "town_psgc,veco_key,osm_name,source,model,prompt_version,decided\n"
        "702219000,x road,X Road,qwen,qwen3:14b,2,2026-11-13\n",
        encoding="utf-8",
    )
    assert load_aliases(p) == {("702219000", "x road"): "X Road"}
    assert load_rejects(tmp_path / "none.csv") == set()


def test_committed_alias_files_have_provenance_columns():
    from outages.streets.match import ALIASES_PATH, REJECTS_PATH

    rows = _dicts(ALIASES_PATH)
    assert rows and all(r["source"] for r in rows)
    assert REJECTS_PATH.read_text(encoding="utf-8").strip() == "town_psgc,veco_key,decided"


# --- review grouping ----------------------------------------------------------------------


def test_review_groups_by_town_and_key(tmp_path, bnd):
    conn = _conn(tmp_path, [("hA", 5, "Gorordo Avnue"), ("hD", 5, "Gorordo Avnue")])
    items = [i for i in review(conn, INDEX, bnd) if i.name == "Gorordo Avnue"]
    assert sorted((i.town_psgc, i.count) for i in items) == [(CONSOLACION, 1), (CEBU, 1)]
    conn.close()


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def cli_world(monkeypatch, tmp_path):
    from osm_world import write

    from outages import __main__ as cli

    monkeypatch.setattr(cli, "load_boundaries", lambda: boundaries())
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "data" / "osm")
    _conn(tmp_path / "data", STREETS).close()  # data/o.db
    (tmp_path / "data" / "o.db").rename(tmp_path / "data" / "outages.db")
    return cli


def test_suggest_cli_ollama_down_exit_2_writes_nothing(capsys, monkeypatch, cli_world):
    monkeypatch.setattr(cli_world, "make_street_picker", lambda paths: FakeQwen(down=True))
    assert main(["streets", "suggest"]) == 2
    assert "Ollama" in capsys.readouterr().err
    assert not (cli_world.Path("data") / "streets").exists()


def test_suggest_cli_then_apply(capsys, monkeypatch, cli_world):
    monkeypatch.setattr(cli_world, "make_street_picker", lambda paths: FakeQwen())
    assert main(["streets", "suggest", "--limit", "5"]) == 0
    assert "new" in capsys.readouterr().out
    q = cli_world.Path("data") / "streets" / "alias_queue.csv"
    rows = queue.read(q)
    for r in rows:
        r["decision"] = "n"
    queue.write(q, rows)
    a = cli_world.Path("aliases.csv")
    a.write_text("town_psgc,veco_key,osm_name\n", encoding="utf-8")
    monkeypatch.setattr(cli_world, "ALIASES_PATH", a)
    monkeypatch.setattr(cli_world, "REJECTS_PATH", cli_world.Path("rejects.csv"))
    assert main(["streets", "suggest", "--apply"]) == 0
    out = capsys.readouterr().out
    assert f"rejected {len(rows)}" in out and "streets match --dry-run" in out


# --- serve hook: suggest_new (spec 7.8) ---------------------------------------------------


@pytest.fixture
def hook_home(tmp_path):
    """A Paths root with the map world DB, the OSM world extract and a frozen test split."""
    from osm_world import write

    from outages.config import OSM_DIR_NAME, Paths

    paths = Paths(tmp_path)
    paths.data.mkdir()
    names = [(f"h{x}", 10 + i, f"Zeta {i:02d} Lane") for i, x in enumerate("A" * 30)]
    _conn(paths.data, names).close()
    (paths.data / "o.db").rename(paths.db)
    write(paths.data / OSM_DIR_NAME)
    ev = tmp_path / "evals" / "streets"
    ev.mkdir(parents=True)
    (ev / "FROZEN").write_text("x\n", encoding="utf-8")
    return paths


def _hook(paths, picker, **kw):
    from outages.streets.suggest import suggest_new

    return suggest_new(paths, NOW, picker=picker, boundaries=boundaries(), **kw)


def test_hook_budget(hook_home):
    s = _hook(hook_home, FakeQwen())
    assert s.new == 20  # SUGGEST_BUDGET_GROUPS
    assert len(queue.read(hook_home.data / "streets" / "alias_queue.csv")) == 20
    assert _hook(hook_home, FakeQwen()).new == 10  # the rest, next run


def test_hook_time_budget(hook_home, monkeypatch):
    from outages.streets import suggest

    ticks = iter(range(0, 10_000, 100))  # each group "takes" 100 s
    monkeypatch.setattr(suggest.time, "monotonic", lambda: next(ticks))
    assert _hook(hook_home, FakeQwen()).new == 2  # start 0; checks at 100, 200 pass, 300 stops


def test_hook_inert_before_frozen(hook_home, caplog):
    (hook_home.root / "evals" / "streets" / "FROZEN").unlink()
    q = FakeQwen()
    assert _hook(hook_home, q) is None
    assert q.calls == 0 and "FROZEN" in caplog.text
    assert not (hook_home.data / "streets").exists()


def test_hook_inert_without_extract(hook_home, caplog):
    import shutil

    from outages.config import OSM_DIR_NAME

    shutil.rmtree(hook_home.data / OSM_DIR_NAME)
    assert _hook(hook_home, FakeQwen()) is None
    assert "extract" in caplog.text


def test_hook_ollama_down_skips(hook_home, caplog):
    assert _hook(hook_home, FakeQwen(down=True)) is None
    assert "Ollama" in caplog.text
    assert not (hook_home.data / "streets").exists()


def test_hook_swallows_picker_errors(hook_home, caplog):
    class Broken(FakeQwen):
        def pick(self, g, cands):
            raise RuntimeError("picker broke")

    assert _hook(hook_home, Broken()) is None
    assert "picker broke" in caplog.text


# --- final review fixes ---------------------------------------------------------------------


def test_run_keeps_decisions_made_mid_run(tmp_path):
    """Review #1: a decision recorded while `run` is picking survives the run's write."""
    conn = _conn(tmp_path, STREETS)
    path = tmp_path / "data" / "streets" / "alias_queue.csv"
    _run(tmp_path, conn, FakeQwen(), limit=1)
    (first,) = queue.read(path)

    class DecidesMidRun(FakeQwen):
        def pick(self, g, cands):
            queue.set_decision(path, first["town_psgc"], first["veco_key"], "n")
            return super().pick(g, cands)

    assert _run(tmp_path, conn, DecidesMidRun()).new == 1
    rows = {r["veco_key"]: r["decision"] for r in queue.read(path)}
    assert rows[first["veco_key"]] == "n" and len(rows) == 2
    conn.close()


def test_queue_temp_name_is_per_writer(tmp_path, monkeypatch):
    """Review #1: two processes writing at once never swap each other's temp file."""
    seen = []
    real = queue.os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real(src, dst)

    monkeypatch.setattr(queue.os, "replace", spy)
    queue.write(tmp_path / "q.csv", [_row()])
    import os

    assert str(os.getpid()) in seen[0] and seen[0].endswith(".tmp")


def test_apply_retry_after_busy_queue_no_duplicates(tmp_path, monkeypatch):
    """Review #2: the queue held by Excel at --apply, then a retry: no duplicate rows."""
    conn = _conn(tmp_path, STREETS)
    q = tmp_path / "q.csv"
    queue.write(
        q,
        [
            _row(
                key="gorordo avnue",
                mentions=2,
                decision="y",
                pick="Gorordo Avenue",
                pick_source="qwen",
            ),
            _row(key="escarrio street", decision="n", pick="Escario Street", pick_source="difflib"),
        ],
    )
    a, rj = _aliases(tmp_path), tmp_path / "rejects.csv"
    real = queue.os.replace

    def held(src, dst):
        if str(dst) == str(q):
            raise PermissionError("in use")
        return real(src, dst)

    monkeypatch.setattr(queue.os, "replace", held)
    with pytest.raises(queue.QueueBusy):
        apply(q, a, rj, INDEX, boundaries(), conn, TODAY)
    monkeypatch.setattr(queue.os, "replace", real)
    s = apply(q, a, rj, INDEX, boundaries(), conn, TODAY)
    assert s.accepted_by_source == {} and s.rejected == 0  # already applied: skipped
    assert [r["veco_key"] for r in _dicts(a)] == ["bugos street", "gorordo avnue"]
    assert [r["veco_key"] for r in _dicts(rj)] == ["escarrio street"]
    assert queue.read(q) == []
    conn.close()


def test_queue_lock_is_shared_across_processes(tmp_path):
    """Review #1: the CLI and `serve` are different processes; the lock file covers both."""
    import subprocess
    import sys
    import time as _time

    q = tmp_path / "q.csv"
    queue.write(q, [_row()])
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path; from outages.streets import queue\n"
            "with queue.locked(Path(sys.argv[1])):\n"
            "    print('held', flush=True); time.sleep(3)",
            str(q),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        start = _time.monotonic()
        with pytest.raises(queue.QueueBusy), queue.locked(q, wait_s=0.5):
            pass
        assert _time.monotonic() - start >= 0.5
    finally:
        holder.wait(timeout=10)
    with queue.locked(q, wait_s=0.5):  # free again once the other process is done
        pass


def test_generic_names_never_candidates_nor_applied(tmp_path):
    """Review #3: a generic OSM name ("Barangay Road") is never offered and --apply refuses it:
    as an alias it would draw every way of that name in scope."""
    from outages.streets.store import scope_inputs
    from outages.streets.suggest import candidates, groups

    idx = StreetIndex(OSM_BASE, [*INDEX.ways, _way(9, "Barangay Road", row=2)])
    conn = _conn(tmp_path, STREETS)
    (g,) = [x for x in groups(conn, set(), set()) if x.key == "gorordo avnue"]
    inputs = scope_inputs(conn, [h for h, _ in g.mentions])
    assert "Barangay Road" not in candidates(g, idx, boundaries(), inputs)
    q = tmp_path / "q.csv"
    queue.write(
        q,
        [
            _row(
                key="gorordo avnue",
                mentions=2,
                decision="Barangay Road",
                pick="Barangay Road",
                candidates="Barangay Road",
            )
        ],
    )
    a, rj = _aliases(tmp_path), tmp_path / "rejects.csv"
    s = apply(q, a, rj, idx, boundaries(), conn, TODAY)
    assert len(s.refused) == 1 and len(_dicts(a)) == 1
    conn.close()
