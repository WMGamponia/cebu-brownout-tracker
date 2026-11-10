"""Post HTML -> normalized lines, strikethrough flags, image URLs and a text hash (spec 5.1).

Pure: no I/O. Wix renders the post body (Ricos) inside `section[data-hook="post-description"]`,
mostly as table cells holding paragraphs. Each leaf block (a block with no block inside it)
becomes one line, so a cell with several paragraphs (the structured mid-2025 areas layout)
yields several lines, in document order.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from selectolax.lexbor import LexborHTMLParser, LexborNode

from outages.normalize import normalize_line
from outages.timeparse import parse_heading

_BLOCKS = "p, h1, h2, h3, h4, h5, h6, li, td"
_STRIKE = "s, strike, del"
_WIX_MEDIA = re.compile(r"/_?media/([0-9a-f]+_[0-9a-f]+~mv2\.[a-z]+)", re.IGNORECASE)


class ExtractError(Exception):
    """The page has no post content node (not a post, or Wix changed its markup)."""


@dataclass(frozen=True)
class Line:
    text: str
    struck: bool
    img_urls: tuple[str, ...]


@dataclass(frozen=True)
class ExtractedPost:
    title: str | None
    lines: tuple[Line, ...]
    content_html: str
    text_hash: str


def _content_node(tree: LexborHTMLParser) -> LexborNode:
    node = tree.css_first('article section[data-hook="post-description"]')
    if node is None:
        raise ExtractError("no post content node (section[data-hook=post-description])")
    return node


def _struck(node: LexborNode) -> bool:
    if node.css_first(_STRIKE) is not None:
        return True
    for n in [node, *node.css("[style]")]:
        if "line-through" in (n.attributes.get("style") or "").replace(" ", ""):
            return True
    return False


def _canonical_img(src: str) -> str:
    """Wix serves a blurred, resized thumbnail (`…/_media/<id>/v1/fill/w_49,…/<id>`); keep the
    original `https://static.wixstatic.com/media/<id>` so layout churn isn't an edit."""
    m = _WIX_MEDIA.search(src)
    return f"https://static.wixstatic.com/media/{m[1]}" if m else src


def _img_urls(node: LexborNode) -> tuple[str, ...]:
    urls = []
    for img in node.css("img"):
        src = img.attributes.get("src") or img.attributes.get("data-src")
        if src:
            urls.append(_canonical_img(src))
    return tuple(urls)


def _hash(lines: tuple[Line, ...]) -> str:
    """SHA-256 of the lines from the first date heading on.

    Lines before it (title, `x days ago`, `Updated: …`) change on every fetch; the strike flag
    and image URLs are included so striking a block or adding a map counts as an edit.
    """
    start = next((i for i, ln in enumerate(lines) if parse_heading(ln.text)), 0)
    rows = [
        ln.text + ("\t<s>" if ln.struck else "") + "".join(f"\t<img {u}>" for u in ln.img_urls)
        for ln in lines[start:]
    ]
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def extract_post(html: str) -> ExtractedPost:
    """Extract the post's lines; raises ExtractError when there is no content node."""
    tree = LexborHTMLParser(html)
    content = _content_node(tree)
    content_html = content.html or ""
    for br in content.css("br"):
        br.replace_with(" ")
    lines = []
    for node in content.css(_BLOCKS):
        if len(node.css(_BLOCKS)) > 1:  # lexbor's node.css() includes the node itself
            continue  # not a leaf: its inner blocks give the lines
        text = normalize_line(node.text(separator=""))
        imgs = _img_urls(node)
        if text or imgs:
            lines.append(Line(text, _struck(node), imgs))
    title_node = tree.css_first('h1[data-hook="post-title"]')
    title = normalize_line(title_node.text()) if title_node else None
    lines_t = tuple(lines)
    return ExtractedPost(title or None, lines_t, content_html, _hash(lines_t))
