"""discover -> fetch -> store version -> parse -> upsert, over saved posts (spec 6.2, 7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import read_post

from outages import db, pipeline
from outages.config import SITEMAP_URL
from outages.discover import PostRef
from outages.fetch import Fetcher

NOV = "service-interruption-november-8-14-2026"
JAN = "service-interruption-january-23-2024"
FEB = "service-interruption-february-29-march-01-2024"
BASE = "https://www.visayanelectric.com/post/"
NOW = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)  # 09:00 Manila, Tuesday of the Nov 8-14 week


def ref(slug: str, lastmod: str | None = "2026-11-10") -> PostRef:
    return PostRef(BASE + slug, slug, date.fromisoformat(lastmod) if lastmod else None)


def sitemap(*refs: PostRef) -> str:
    urls = "".join(
        f"<url><loc>{r.url}</loc>"
        + (f"<lastmod>{r.lastmod.isoformat()}</lastmod>" if r.lastmod else "")
        + "</url>"
        for r in refs
    )
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'


class Site:
    """A fake visayanelectric.com serving given pages; records request URLs."""

    def __init__(self, pages: dict[str, str], fail: set[str] | None = None):
        self.pages = pages
        self.fail = fail or set()
        self.requests: list[str] = []

    def fetcher(self) -> Fetcher:
        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            self.requests.append(url)
            if url in self.fail:
                raise RuntimeError(f"boom on {url}")
            if url in self.pages:
                return httpx.Response(200, text=self.pages[url])
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        return Fetcher(client=client, sleep=lambda s: None)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "o.db")
    yield c
    c.close()


def _changes(conn) -> list[str]:
    return [r[0] for r in conn.execute("SELECT kind FROM outage_changes ORDER BY id")]


def _count(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def test_first_parse_logs_no_changes(conn):
    assert pipeline.process_post(conn, ref(NOV), read_post(NOV), NOW) == "new"
    assert _count(conn, "SELECT count(*) FROM outages") == 34
    assert _changes(conn) == []
    assert _count(conn, "SELECT count(*) FROM posts WHERE parse_status='ok'") == 1


def test_edit_logs_changes(conn):
    html = read_post(NOV)
    pipeline.process_post(conn, ref(NOV), html, NOW)
    # VECO moves the first outage an hour later and drops the Centro block.
    edited = html.replace("7:00 AM to 4:00 PM (9hrs)", "8:00 AM to 5:00 PM (9hrs)", 1)
    centro = "Portion of Centro, Mandaue City"
    assert centro in edited
    edited = _drop_block(edited, centro)
    assert pipeline.process_post(conn, ref(NOV), edited, NOW) == "changed"
    assert _changes(conn) == ["time_changed", "removed"]
    assert _count(conn, "SELECT count(*) FROM outages WHERE status='removed'") == 1


def _drop_block(html: str, areas_marker: str) -> str:
    """Remove the table holding `areas_marker` (one outage block) from the page."""
    i = html.index(areas_marker)
    start = html.rindex("<table", 0, i)
    end = html.index("</table>", i) + len("</table>")
    return html[:start] + html[end:]


def test_unchanged_hash_skips_parse(conn, monkeypatch):
    html = read_post(JAN)
    pipeline.process_post(conn, ref(JAN), html, NOW)
    monkeypatch.setattr(pipeline, "parse_post", lambda ex, slug=None: pytest.fail("parsed again"))
    assert pipeline.process_post(conn, ref(JAN), html, NOW) == "unchanged"
    assert _count(conn, "SELECT count(*) FROM post_versions") == 1


def test_failed_reparse_keeps_outages(conn):
    html = read_post(JAN)
    pipeline.process_post(conn, ref(JAN), html, NOW)
    before = db.active_outages(conn, 1)
    broken = html.replace("8:00 AM to 10:00 AM (2hrs)", "8 AM till 10 AM", 1)
    assert pipeline.process_post(conn, ref(JAN), broken, NOW) == "failed"
    assert db.active_outages(conn, 1) == before
    assert _changes(conn) == []
    row = conn.execute("SELECT parse_status, parse_error FROM posts").fetchone()
    assert row[0] == "failed"
    assert "8 AM till 10 AM" in row[1]
    assert _count(conn, "SELECT count(*) FROM post_versions") == 2


def test_backfill_resumes_without_duplicates(conn):
    refs = [ref(JAN, "2024-01-22"), ref(FEB, "2024-02-28"), ref(NOV, "2026-11-10")]
    pages = {SITEMAP_URL: sitemap(*refs), **{r.url: read_post(r.slug) for r in refs}}
    site = Site(pages, fail={refs[1].url})
    with pytest.raises(RuntimeError):
        pipeline.backfill(conn, site.fetcher(), NOW)
    assert _count(conn, "SELECT count(*) FROM posts") == 1

    site2 = Site(pages)
    s = pipeline.backfill(conn, site2.fetcher(), NOW)
    assert (s.fetched, s.new, s.failed, s.errors) == (2, 2, 0, 0)
    assert refs[0].url not in site2.requests
    assert _count(conn, "SELECT count(*) FROM posts") == 3
    assert _count(conn, "SELECT count(*) FROM outages") == 5 + 1 + 34


def test_check_refetches_current_week(conn):
    jan, nov = ref(JAN, "2024-01-22"), ref(NOV, "2026-11-10")
    pages = {SITEMAP_URL: sitemap(jan, nov), jan.url: read_post(JAN), nov.url: read_post(NOV)}
    pipeline.backfill(conn, Site(pages).fetcher(), NOW)

    site = Site(pages)
    s = pipeline.check(conn, site.fetcher(), NOW)
    assert site.requests == [SITEMAP_URL, nov.url]  # same lastmod, but outages still ahead
    assert (s.fetched, s.new, s.changed, s.failed, s.errors) == (1, 0, 0, 0, 0)
    assert db.get_meta(conn, "last_check_at") == "2026-11-10T01:00:00+00:00"


def test_check_fetches_new_and_newer_lastmod(conn):
    jan = ref(JAN, "2024-01-22")
    pages = {SITEMAP_URL: sitemap(jan), jan.url: read_post(JAN)}
    pipeline.backfill(conn, Site(pages).fetcher(), NOW)
    jan2, feb = ref(JAN, "2024-01-25"), ref(FEB, "2024-02-28")
    pages = {SITEMAP_URL: sitemap(jan2, feb), jan.url: read_post(JAN), feb.url: read_post(FEB)}
    site = Site(pages)
    s = pipeline.check(conn, site.fetcher(), NOW)
    assert sorted(site.requests[1:]) == sorted([jan.url, feb.url])
    assert (s.fetched, s.new) == (2, 1)


def test_check_counts_fetch_errors_and_keeps_going(conn):
    jan, feb = ref(JAN, "2024-01-22"), ref(FEB, "2024-02-28")
    pages = {SITEMAP_URL: sitemap(jan, feb), feb.url: read_post(FEB)}  # jan -> 404
    s = pipeline.check(conn, Site(pages).fetcher(), NOW)
    assert (s.fetched, s.new, s.errors) == (1, 1, 1)


def test_sitemap_failure_is_an_error_and_not_a_check(conn):
    s = pipeline.check(conn, Site({}).fetcher(), NOW)
    assert s.errors == 1
    assert db.get_meta(conn, "last_check_at") is None


def test_reparse_round_trips_without_network(conn):
    for slug in (JAN, NOV):
        pipeline.process_post(conn, ref(slug), read_post(slug), NOW)
    s = pipeline.reparse(conn, NOW)
    assert (s.fetched, s.changed, s.failed) == (0, 0, 0)
    assert _changes(conn) == []
    assert _count(conn, "SELECT count(*) FROM outages WHERE status != 'removed'") == 39


def test_reparse_after_parser_fix_logs_nothing(conn, monkeypatch):
    pipeline.process_post(conn, ref(JAN), read_post(JAN), NOW)
    real = pipeline.parse_post

    def fixed(ex, slug=None):  # a "parser fix" that changes one outage's areas
        assert slug == JAN  # the slug gives parse_post the post's week
        r = real(ex, slug)
        o2 = replace(r.outages[0], areas_raw="X", areas_key="x")
        return replace(r, outages=(o2, *r.outages[1:]))

    monkeypatch.setattr(pipeline, "parse_post", fixed)
    s = pipeline.reparse(conn, NOW)
    assert s.changed == 1
    assert _changes(conn) == []  # a parser fix is not news from VECO
    assert conn.execute("SELECT areas_raw FROM outages WHERE seq=0").fetchone()[0] == "X"


def test_reparse_of_newer_failed_version_logs_changes(conn, monkeypatch):
    html = read_post(JAN)
    pipeline.process_post(conn, ref(JAN), html, NOW)
    edited = html.replace("8:00 AM to 10:00 AM (2hrs)", "8:00 AM to 11:00 AM (3hrs) maybe", 1)
    assert pipeline.process_post(conn, ref(JAN), edited, NOW) == "failed"
    # The parser learns to ignore "maybe": reparse now reads the newer version, a real edit.
    real = pipeline.parse_post

    def lenient(ex, slug=None):
        lines = tuple(replace(ln, text=ln.text.replace(" maybe", "")) for ln in ex.lines)
        return real(replace(ex, lines=lines), slug)

    monkeypatch.setattr(pipeline, "parse_post", lenient)
    pipeline.reparse(conn, NOW)
    assert _changes(conn) == ["time_changed"]
    assert conn.execute("SELECT parse_status FROM posts").fetchone()[0] == "ok"
