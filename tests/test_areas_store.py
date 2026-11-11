"""`areas` step: derived tables, idempotence, resume, orphans, review queue (spec 5, 6)."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime

import pytest
from conftest import read_post
from test_pipeline import FEB, JAN, NOV, NOW, Site, ref, sitemap

from outages import __main__ as cli
from outages import db, pipeline, report
from outages.areas import store
from outages.areas.entities import Entity
from outages.areas.entities_rules import RulesExtractor
from outages.areas.gazetteer import load
from outages.config import SITEMAP_URL, Paths

T = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)
TISA = "Portion of Cebu City (Tisa) along Katipunan St., including Sitio Kawayan."
TAYUD = "Portion of Consolacion & Liloan (Tayud & Tagba-o) along Cebu North Road."
SRA = "Portion of Cebu City (South Reclamation Area) including portion along SRP Bypass Road."


def _seed(conn, rows: list[tuple[str, str]], parse_status: str = "ok") -> None:
    """Outages as (areas_raw, status), all in one post with slug `post-a`."""
    with conn:
        conn.execute(
            "INSERT INTO posts (id, url, slug, first_seen_at, parse_status)"
            " VALUES (1, 'https://x/post/post-a', 'post-a', 't', ?)",
            (parse_status,),
        )
        conn.execute(
            "INSERT INTO post_versions (id, post_id, fetched_at, text_hash, content_gz)"
            " VALUES (1, 1, 't', 'h', x'00')"
        )
        for seq, (areas, status) in enumerate(rows):
            conn.execute(
                "INSERT INTO outages (post_id, seq, heading_text, start_at, end_at, status,"
                " purpose, areas_raw, areas_key, first_seen_version_id, last_seen_version_id)"
                " VALUES (1, ?, 'h', 's', 'e', ?, 'p', ?, ?, 1, 1)",
                (seq, status, areas, areas.casefold()),
            )


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


@pytest.fixture(scope="module")
def gaz():
    return load()


def _n(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_areas_writes_rows(conn, gaz):
    _seed(conn, [(TISA, "scheduled"), (TAYUD, "scheduled"), (SRA, "scheduled")])
    s = store.run_areas(conn, RulesExtractor(), gaz, T)
    assert s.parsed == 3 and _n(conn, "area_parses") == 3
    h = store.areas_hash(TISA)
    assert conn.execute("SELECT count(*) FROM outages WHERE areas_hash = ?", (h,)).fetchone()[0]
    row = conn.execute("SELECT * FROM area_parses WHERE areas_hash = ?", (h,)).fetchone()
    assert (row["layout"], row["towns_json"], row["extractor"]) == (
        "city_first",
        '["Cebu City"]',
        "rules",
    )
    brgy = conn.execute(
        "SELECT name_raw, psgc_code, match FROM area_barangays WHERE areas_hash = ?", (h,)
    ).fetchall()
    assert [tuple(r) for r in brgy] == [("Tisa", "730600085", "exact")]
    ents = conn.execute(
        "SELECT kind, name, extractor FROM area_entities WHERE areas_hash = ? ORDER BY seq", (h,)
    ).fetchall()
    assert [tuple(r) for r in ents] == [
        ("street", "Katipunan St.", "rules"),
        ("sitio_purok", "Kawayan.", "rules"),
    ]
    tayud = conn.execute(
        "SELECT seq, match FROM area_barangays WHERE areas_hash = ? ORDER BY seq",
        (store.areas_hash(TAYUD),),
    ).fetchall()
    assert [tuple(r) for r in tayud] == [(0, "ambiguous"), (1, "ambiguous"), (2, "unmatched")]
    sra = conn.execute(
        "SELECT kind, name, extractor FROM area_entities WHERE areas_hash = ? ORDER BY seq",
        (store.areas_hash(SRA),),
    ).fetchall()
    assert tuple(sra[0]) == ("other", "South Reclamation Area", "gazetteer")


def test_areas_idempotent(conn, gaz):
    _seed(conn, [(TISA, "scheduled"), (TISA, "scheduled")])
    assert store.run_areas(conn, RulesExtractor(), gaz, T).parsed == 1
    before = [tuple(r) for r in conn.execute("SELECT * FROM area_entities")]
    assert store.run_areas(conn, RulesExtractor(), gaz, T).parsed == 0
    assert [tuple(r) for r in conn.execute("SELECT * FROM area_entities")] == before


def test_rebuild_reparses_all(conn, gaz):
    _seed(conn, [(TISA, "scheduled")])
    store.run_areas(conn, RulesExtractor(), gaz, T)
    assert store.run_areas(conn, RulesExtractor(), gaz, T, rebuild=True).parsed == 1
    assert _n(conn, "area_parses") == 1


def test_areas_skips_removed(conn, gaz):
    _seed(conn, [(TISA, "removed"), (TAYUD, "cancelled")])
    store.run_areas(conn, RulesExtractor(), gaz, T)
    hashes = {r[0] for r in conn.execute("SELECT areas_hash FROM area_parses")}
    assert hashes == {store.areas_hash(TAYUD)}


def test_areas_parses_failed_post_outages(conn, gaz):
    _seed(conn, [(TISA, "scheduled")], parse_status="failed")
    assert store.run_areas(conn, RulesExtractor(), gaz, T).parsed == 1


def test_areas_follows_areas_changed(conn, gaz):
    _seed(conn, [(TISA, "scheduled"), (TAYUD, "scheduled")])
    store.run_areas(conn, RulesExtractor(), gaz, T)
    with conn:  # what upsert does on `areas_changed`
        conn.execute("UPDATE outages SET areas_raw = ? WHERE seq = 0", (SRA,))
    s = store.run_areas(conn, RulesExtractor(), gaz, T)
    assert (s.parsed, s.deleted) == (1, 1)
    hashes = {r[0] for r in conn.execute("SELECT areas_hash FROM area_parses")}
    assert hashes == {store.areas_hash(SRA), store.areas_hash(TAYUD)}
    assert conn.execute("SELECT areas_hash FROM outages WHERE seq = 0").fetchone()[0] == (
        store.areas_hash(SRA)
    )
    assert _n(conn, "area_barangays") == 3  # SRA has none; TAYUD has 3 (orphans cascaded)


def test_orphans_deleted(conn, gaz):
    _seed(conn, [(TISA, "scheduled")])
    store.run_areas(conn, RulesExtractor(), gaz, T)
    with conn:
        conn.execute("UPDATE outages SET status = 'removed'")
    s = store.run_areas(conn, RulesExtractor(), gaz, T)
    assert s.deleted == 1
    assert (_n(conn, "area_parses"), _n(conn, "area_entities")) == (0, 0)


def test_dry_run_writes_nothing(tmp_path, gaz):
    p = tmp_path / "o.db"
    c = db.connect(p)
    _seed(c, [(TISA, "scheduled"), (TAYUD, "scheduled"), ("testing", "scheduled")])
    c.close()
    ro = db.connect_ro(p)
    s = store.run_areas(ro, RulesExtractor(), gaz, T, dry_run=True)
    ro.close()
    assert s.parsed == 3
    assert s.layouts == {"city_first": 2, "other": 1}
    assert s.matches["ambiguous"] == 2 and s.matches["exact"] == 1
    c = db.connect(p)
    assert _n(c, "area_parses") == 0
    assert c.execute("SELECT count(*) FROM outages WHERE areas_hash IS NOT NULL").fetchone()[0] == 0
    c.close()


class _Boom(RulesExtractor):
    def __init__(self, fail_on: int | None = None):
        self.calls = 0
        self.fail_on = fail_on

    def extract(self, seg):
        self.calls += 1
        if self.calls == self.fail_on:
            raise KeyboardInterrupt
        return super().extract(seg)


def test_areas_resumes_after_interrupt(conn, gaz):
    _seed(
        conn, [(TISA, "scheduled"), (TAYUD, "scheduled"), (SRA, "scheduled"), ("x", "additional")]
    )
    with pytest.raises(KeyboardInterrupt):
        store.run_areas(conn, _Boom(fail_on=3), gaz, T)
    assert _n(conn, "area_parses") == 2
    again = _Boom()
    s = store.run_areas(conn, again, gaz, T)
    assert (again.calls, s.parsed, _n(conn, "area_parses")) == (2, 2, 4)


def test_review_lists_unmatched_with_suggestions(conn, gaz):
    _seed(
        conn,
        [
            (TAYUD, "scheduled"),
            ("testing", "scheduled"),
            ("Portion of Talisay City (Lawan I) along Cebu South Road.", "scheduled"),
        ],
    )
    store.run_areas(conn, RulesExtractor(), gaz, T)
    items = {(i.name_raw, i.match): i for i in store.review_queue(conn, gaz)}
    lawan = items[("Lawan I", "unmatched")]
    assert (lawan.count, lawan.example_slug) == (1, "post-a")
    assert lawan.suggestions[0] == "Lawaan I (Talisay City)"
    assert ("Tayud", "ambiguous") in items
    assert ("testing", "layout other") in items
    text = store.format_review(list(items.values()))
    assert "Lawan I" in text and "Lawaan I (Talisay City)" in text


def test_review_on_dry_run_matches_stored(conn, gaz):
    _seed(conn, [(TAYUD, "scheduled")])
    dry = store.review_queue(conn, gaz, dry_run=True)
    store.run_areas(conn, RulesExtractor(), gaz, T)
    assert dry == store.review_queue(conn, gaz)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Paths(tmp_path)


def test_review_prints_on_cp1252_console(home, monkeypatch):
    c = db.connect(home.db)
    _seed(c, [("Portion of Cebu City (Kinasang‑an & Sto. Niño) along X St.", "scheduled")])
    c.close()
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    assert cli.main(["areas"]) == 0
    assert cli.main(["areas", "--review"]) == 0
    out.flush()
    written = list((home.reports).glob("areas-review-*.md"))
    assert written and "Kinasang‑an" in written[0].read_text(encoding="utf-8")


def test_cli_areas_dry_run_does_not_migrate(home, capsys):
    c = db.connect(home.db)
    _seed(c, [(TISA, "scheduled")])
    c.close()
    assert cli.main(["areas", "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    c = db.connect(home.db)
    assert _n(c, "area_parses") == 0
    c.close()


def test_reparse_runs_areas_step(conn):
    pipeline.process_post(conn, ref(NOV), read_post(NOV), NOW)
    assert _n(conn, "area_parses") == 0
    pipeline.reparse(conn, NOW)
    assert _n(conn, "area_parses") > 0


def test_check_runs_areas_step(conn):
    jan, feb = ref(JAN, "2024-01-22"), ref(FEB, "2024-02-28")
    pages = {SITEMAP_URL: sitemap(jan, feb), jan.url: read_post(JAN), feb.url: read_post(FEB)}
    pipeline.check(conn, Site(pages).fetcher(), NOW)
    assert _n(conn, "area_parses") > 0


def test_areas_step_failure_does_not_fail_check(conn, caplog):
    class Broken:
        name = "broken"

        def extract(self, seg):
            raise RuntimeError("model gone")

    jan = ref(JAN, "2024-01-22")
    pages = {SITEMAP_URL: sitemap(jan), jan.url: read_post(JAN)}
    s = pipeline.check(conn, Site(pages).fetcher(), NOW, extractor=Broken())
    assert (s.fetched, s.new) == (1, 1)
    assert "areas step failed" in caplog.text


def test_report_has_areas_line(conn, gaz):
    _seed(conn, [(TISA, "scheduled"), (TAYUD, "scheduled")])
    store.run_areas(conn, RulesExtractor(), gaz, T)
    line = report.areas_line(conn)
    assert line.startswith("Areas: 4 barangay rows")
    assert "exact 1" in line and "ambiguous 2" in line and "unmatched 1" in line
    text = report.format_table(report.build(conn), report.duplicates(conn), areas=line)
    assert line in text


def test_entity_type_is_shared():
    assert Entity("street", "x") == Entity("street", "x")
