"""Trim a saved VECO post page to what the parser reads (Phase 6 spec 4.7).

A whole Wix page is ~1.4 MB of scripts, styles and layout. The parser (`outages.extract`) reads
only the page title, `h1[data-hook="post-title"]` (kept with its header's byline, which tests
edit) and the post's content cell
`article section[data-hook="post-description"]`; inside that cell it reads text, `<br>`, table
cells, `<img src|data-src>`, strike tags and `line-through` styles. `trim()` keeps exactly those
and drops every other attribute, so each fixture stays small while every parsed line, flag and
text hash stays the same.

    python scripts/trim_fixture.py tests/fixtures/posts/*.html

rewrites the files in place, and refuses (exit 1, nothing written for that file) when the trimmed
page doesn't extract to the same title, lines and text hash as the original.
"""

from __future__ import annotations

import sys
from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

CONTENT = 'article section[data-hook="post-description"]'
TITLE = 'h1[data-hook="post-title"]'
KEEP = {"src", "data-src"}
HOOKS = {("h1", "post-title"), ("section", "post-description")}  # what extract.py selects


def _strip(node) -> None:
    """Drop every attribute the parser doesn't read, in `node` and below."""
    for n in [node, *node.css("*")]:  # lexbor's css() may include the node itself
        attrs = n.attrs
        for k in list(attrs.keys()):
            v = attrs[k] or ""
            if k in KEEP or (k == "data-hook" and (n.tag, v) in HOOKS):
                continue
            if k == "style" and "line-through" in v.replace(" ", ""):
                attrs[k] = "text-decoration:line-through"
                continue
            del attrs[k]


def _header(h1):
    """The post's header (title and the "x days ago" / "Updated:" byline), else the h1 alone."""
    n = h1.parent
    while n is not None and n.tag not in ("header", "article", "body"):
        n = n.parent
    return n if n is not None and n.tag == "header" else h1


def trim(html: str) -> str:
    """The minimal page for `html`; pure and idempotent. Raises ValueError without a post."""
    tree = LexborHTMLParser(html)
    section = tree.css_first(CONTENT)
    if section is None:
        raise ValueError("no post content cell (section[data-hook=post-description])")
    title_node = tree.css_first("title")
    title = title_node.html if title_node is not None else "<title></title>"
    h1 = tree.css_first(TITLE)
    head = ""
    if h1 is not None:
        top = _header(h1)
        _strip(top)
        head = top.html or ""
    _strip(section)
    return (
        '<!doctype html>\n<html><head><meta charset="utf-8">'
        f"{title}</head><body><article>{head}{section.html}</article></body></html>\n"
    )


def _same_extraction(original: str, trimmed: str) -> bool:
    from outages.extract import extract_post

    a, b = extract_post(original), extract_post(trimmed)
    return (a.title, a.lines, a.text_hash) == (b.title, b.lines, b.text_hash)


def main(argv: list[str]) -> int:
    failed = 0
    for name in argv:
        path = Path(name)
        original = path.read_text(encoding="utf-8")
        trimmed = trim(original)
        if not _same_extraction(original, trimmed):
            print(f"{path}: trimmed page extracts differently; not written", file=sys.stderr)
            failed += 1
            continue
        path.write_text(trimmed, encoding="utf-8", newline="\n")
        print(f"{path}: {len(original.encode()):,} -> {len(trimmed.encode()):,} bytes")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
