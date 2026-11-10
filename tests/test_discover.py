"""Sitemap discovery (spec 2, 7)."""

from __future__ import annotations

from datetime import date

from conftest import FIXTURES

from outages.discover import PostRef, parse_sitemap

SITEMAP = (FIXTURES / "sitemap" / "blog-posts-sitemap.xml").read_text(encoding="utf-8")


def test_counts_interruption_posts():
    refs = parse_sitemap(SITEMAP)
    assert len(refs) == 151
    slugs = {r.slug for r in refs}
    assert "interruption-june-22-28-2025" in slugs
    assert "copy-of-service-interruption-july-6-12-2025" in slugs
    assert "meter-inspection-services" not in slugs
    assert all("interruption" in s for s in slugs)


def test_lastmod_and_order():
    refs = parse_sitemap(SITEMAP)
    assert refs[-1] == PostRef(
        "https://www.visayanelectric.com/post/service-interruption-november-8-14-2026",
        "service-interruption-november-8-14-2026",
        date(2026, 11, 10),
    )
    keys = [(r.lastmod or date.min, r.slug) for r in refs]
    assert keys == sorted(keys)


def test_missing_lastmod_and_trailing_slash():
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://x/post/service-interruption-a/</loc></url>"
        "<url><loc>https://x/post/other</loc><lastmod>2026-01-01</lastmod></url>"
        "</urlset>"
    )
    assert parse_sitemap(xml) == [
        PostRef("https://x/post/service-interruption-a/", "service-interruption-a", None)
    ]
