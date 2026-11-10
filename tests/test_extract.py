"""Post HTML -> normalized lines and text hash (spec 5.1)."""

from __future__ import annotations

import re

import pytest
from conftest import POSTS, read_post

from outages.extract import ExtractError, extract_post
from outages.timeparse import parse_heading

NOV = "service-interruption-november-8-14-2026"
ALL_SLUGS = sorted(p.stem for p in POSTS.glob("*.html"))


def _wrap(body: str) -> str:
    """Minimal page shaped like a Wix post."""
    return (
        '<html><body><article><h1 data-hook="post-title">T</h1>'
        '<span data-hook="time-ago">5 days ago</span>'
        f'<section data-hook="post-description">{body}</section></article></body></html>'
    )


def test_lines_in_order():
    lines = [ln.text for ln in extract_post(read_post(NOV)).lines]
    first = next(i for i, s in enumerate(lines) if parse_heading(s))
    assert lines[first] == "November 8, 2026 (Sunday)"
    assert lines[first + 1 : first + 4] == ["Time:", "7:00 AM to 4:00 PM (9hrs)", "Purpose:"]


def test_title():
    assert extract_post(read_post(NOV)).title == "Service Interruption: November 8-14, 2026"


def test_related_posts_excluded():
    lines = [ln.text for ln in extract_post(read_post(NOV)).lines]
    assert "Service Interruption: November 03-08, 2026" not in lines


def test_structured_areas_one_line_per_paragraph():
    lines = [ln.text for ln in extract_post(read_post("interruption-june-22-28-2025")).lines]
    i = lines.index("Areas Affected: Portion of Cebu City")
    assert lines[i + 1] == "Barangays: Camputhaw"
    assert lines[i + 2].startswith("Streets: Queens Road")


def test_hash_ignores_relative_time_lines():
    html = read_post(NOV)
    base = extract_post(html).text_hash
    other = html.replace("5 days ago", "6 days ago").replace("6 hours ago", "1 minute ago")
    assert other != html
    assert extract_post(other).text_hash == base
    assert extract_post(html.replace("(9hrs)", "(8hrs)", 1)).text_hash != base


def test_hash_ignores_lines_before_first_heading():
    body = "<p>{}</p><p>November 9, 2026 (Monday)</p><p>Time:</p>"
    a = extract_post(_wrap(body.format("Updated: 6 hours ago")))
    b = extract_post(_wrap(body.format("Updated: 2 days ago")))
    assert a.text_hash == b.text_hash


def test_hash_ignores_markup_churn():
    html = read_post(NOV)
    churned = re.sub(r'class="[^"]*"', 'class="zz"', html)
    assert extract_post(churned).text_hash == extract_post(html).text_hash


MAP_POST = "service-interruption-september-27-october-3-2026"


@pytest.mark.parametrize("slug", [s for s in ALL_SLUGS if s != MAP_POST])
def test_no_map_images_in_fixtures(slug):
    assert all(ln.img_urls == () for ln in extract_post(read_post(slug)).lines)


def test_map_images_in_sep_2026_post():
    """Recon saw only empty Map cells; this post has a thumbnail in each one."""
    lines = extract_post(read_post(MAP_POST)).lines
    imgs = [(i, ln) for i, ln in enumerate(lines) if ln.img_urls]
    assert len(imgs) == 30
    assert all(lines[i - 1].text == "Map:" and ln.text == "" for i, ln in imgs)
    assert imgs[0][1].img_urls == (
        "https://static.wixstatic.com/media/a69d9c_b60e2d38655a4acf97e8e2d76f0aff96~mv2.png",
    )


def test_wix_thumbnail_canonicalised():
    thumb = (
        "https://www.visayanelectric.com/_media/a69d9c_b6~mv2.png/v1/fill/w_49,h_38,blur_2,"
        "enc_avif/a69d9c_b6~mv2.png"
    )
    post = extract_post(_wrap(f'<p><img src="{thumb}"></p>'))
    assert post.lines[0].img_urls == ("https://static.wixstatic.com/media/a69d9c_b6~mv2.png",)


def test_map_image_found():
    url = "https://static.wixstatic.com/media/x.jpg"
    post = extract_post(
        _wrap(f'<table><tr><td><p>Map:</p></td><td><p><img src="{url}"></p></td></tr></table>')
    )
    assert [ln.img_urls for ln in post.lines if ln.img_urls] == [(url,)]


def test_strikethrough_flag():
    post = extract_post(_wrap("<p>Time:</p><p><s>9:00 AM to 5:00 PM</s></p>"))
    assert [(ln.text, ln.struck) for ln in post.lines] == [
        ("Time:", False),
        ("9:00 AM to 5:00 PM", True),
    ]


def test_strikethrough_by_style():
    post = extract_post(
        _wrap('<p><span style="text-decoration: line-through">9:00 AM to 5:00 PM</span></p>')
    )
    assert post.lines[0].struck


def test_cell_holding_paragraphs_is_not_doubled():
    post = extract_post(_wrap("<table><tr><td><p>a</p><p>b</p></td><td>c</td></tr></table>"))
    assert [ln.text for ln in post.lines] == ["a", "b", "c"]


def test_cell_index_groups_paragraphs():
    post = extract_post(
        _wrap("<p>h</p><table><tr><td><p>a</p><p>b</p></td><td><p>c</p></td></tr></table>")
    )
    assert [(ln.text, ln.cell) for ln in post.lines] == [
        ("h", None),
        ("a", 0),
        ("b", 0),
        ("c", 1),
    ]


def test_no_content_raises():
    with pytest.raises(ExtractError):
        extract_post("<html></html>")
