"""Extracted post lines -> outages, by rules only (spec 5.4).

A state machine over the lines: HEADING -> (TIME -> PURPOSE -> AREAS -> MAP?)+. A label's value
is the rest of its line, or else the lines of the next table cell (spec 5.4: the areas cell may
hold several lines; they are joined with "\n"). Anything unknown fails the whole post loudly
rather than being guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from outages.extract import ExtractedPost, Line
from outages.timeparse import DateRange, heading_warnings, parse_heading, parse_time

_LABEL = re.compile(r"^(time|purpose|areas affected|map):\s*(.*)$", re.IGNORECASE)
# A line that starts like a date and holds a year but isn't a known heading form
# (`Nov. 10, 2026`): failing beats filing the next outages under the previous date.
_HEADING_LIKE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b.*\b20\d{2}\b",
    re.IGNORECASE,
)
_NOT_WORD = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedOutage:
    seq: int
    heading_text: str
    start_at: datetime
    end_at: datetime
    duration_label: str | None
    status: str
    purpose: str
    areas_raw: str
    areas_key: str
    map_image_url: str | None
    warnings: tuple[str, ...]

    def to_json(self) -> dict:
        """JSON-ready dict; datetimes as ISO 8601 with +08:00."""
        return {
            "seq": self.seq,
            "heading_text": self.heading_text,
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "duration_label": self.duration_label,
            "status": self.status,
            "purpose": self.purpose,
            "areas_raw": self.areas_raw,
            "areas_key": self.areas_key,
            "map_image_url": self.map_image_url,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ParseResult:
    outages: tuple[ParsedOutage, ...]
    warnings: tuple[str, ...]
    error: str | None


class _Fail(Exception):
    """A hard failure: the whole post is rejected with this message."""


def areas_key(s: str) -> str:
    """Lowercased, punctuation removed, whitespace (incl. line breaks) collapsed."""
    return _WS.sub(" ", _NOT_WORD.sub(" ", s.lower())).strip()


def _label(line: Line) -> tuple[str, str] | None:
    m = _LABEL.match(line.text)
    return (m[1].lower(), m[2].strip()) if m else None


def _is_boundary(line: Line) -> bool:
    return parse_heading(line.text) is not None or bool(_HEADING_LIKE.match(line.text))


def _value(lines: tuple[Line, ...], i: int, label: str, rest: str) -> tuple[list[Line], int]:
    """The value lines for the label at `i` and the index after them.

    Same-line value: just that. Otherwise the next line starts the value unless it is a heading
    or another label (the structured areas layout repeats `Areas Affected:` as its first value
    line, so the same label is allowed). Inside a table the value is every line of that cell;
    outside one, lines run until the next label or heading.
    """
    if rest:
        return [Line(rest, lines[i].struck, lines[i].img_urls, lines[i].cell)], i + 1
    j = i + 1
    if j >= len(lines) or _is_boundary(lines[j]):
        return [], j
    lab = _label(lines[j])
    if lab and lab[0] != label:
        return [], j
    first = lines[j]
    out = [first]
    j += 1
    while j < len(lines):
        ln = lines[j]
        if first.cell is not None:
            if ln.cell != first.cell:
                break
        elif _label(ln) or _is_boundary(ln):
            break
        out.append(ln)
        j += 1
    return out, j


def _strip_repeated_label(text: str, label: str) -> str:
    m = _LABEL.match(text)
    return m[2].strip() if m and m[1].lower() == label else text


@dataclass
class _Block:
    heading: DateRange
    heading_text: str
    time: list[Line] | None = None
    purpose: list[Line] | None = None
    areas: list[Line] | None = None
    map: list[Line] | None = None


def _finish(b: _Block, seq: int) -> ParsedOutage:
    if not b.time:
        raise _Fail(f"block under {b.heading_text!r} has no time")
    if not b.areas:
        raise _Fail(f"block at {b.time[0].text!r} under {b.heading_text!r} has no areas")
    time_text = " ".join(ln.text for ln in b.time)
    tr = parse_time(time_text, b.heading)
    if tr is None:
        raise _Fail(f"unknown time format {time_text!r} under {b.heading_text!r}")
    warnings = list(heading_warnings(b.heading)) + list(tr.warnings)
    if not b.purpose:
        warnings.append("no purpose")
    lines = [*b.time, *(b.purpose or []), *b.areas, *(b.map or [])]
    if any(ln.struck for ln in lines):
        warnings.append("strikethrough in block (status left as posted)")
    imgs = [u for ln in (b.map or []) for u in ln.img_urls]
    if len(imgs) > 1:
        warnings.append(f"{len(imgs)} map images; kept the first")
    area_lines = [ln.text for ln in b.areas]
    area_lines[0] = _strip_repeated_label(area_lines[0], "areas affected")
    areas_raw = "\n".join(t for t in area_lines if t)
    return ParsedOutage(
        seq=seq,
        heading_text=b.heading_text,
        start_at=tr.start,
        end_at=tr.end,
        duration_label=tr.duration_label,
        status=tr.status,
        purpose=" ".join(ln.text for ln in b.purpose or []),
        areas_raw=areas_raw,
        areas_key=areas_key(areas_raw),
        map_image_url=imgs[0] if imgs else None,
        warnings=tuple(warnings),
    )


def _has_more_blocks(lines: tuple[Line, ...], i: int) -> bool:
    return any(_is_boundary(ln) or (_label(ln) or ("",))[0] == "time" for ln in lines[i:])


def _parse(lines: tuple[Line, ...]) -> tuple[list[ParsedOutage], list[str]]:
    outages: list[ParsedOutage] = []
    post_warnings: list[str] = []
    heading: DateRange | None = None
    heading_text = ""
    block: _Block | None = None

    def close() -> None:
        nonlocal block
        if block is not None:
            outages.append(_finish(block, len(outages)))
            block = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        h = parse_heading(ln.text)
        if h is not None:
            close()
            heading, heading_text = h, ln.text
            i += 1
            continue
        if heading is None:  # title, "x days ago", "Updated: …" before the first heading
            i += 1
            continue
        if _HEADING_LIKE.match(ln.text):
            raise _Fail(f"unknown date heading format {ln.text!r}")
        lab = _label(ln)
        if lab is None:
            if _has_more_blocks(lines, i + 1):
                post_warnings.append(f"ignored line {ln.text!r}")
                i += 1
                continue
            break  # trailing content after the last block
        name, rest = lab
        if name == "time":
            close()
            block = _Block(heading, heading_text)
        elif block is None:
            raise _Fail(f"{name!r} before any time under {heading_text!r}")
        value, i = _value(lines, i, name, rest)
        if name == "map":
            block.map = value
        elif getattr(block, name.split()[0]) is not None:
            raise _Fail(f"second {name!r} in block under {heading_text!r}")
        else:
            setattr(block, name.split()[0], value)
    close()
    if heading is None:
        raise _Fail("no date heading found")
    if not outages:
        raise _Fail("no outages found")
    return outages, post_warnings


def parse_post(post: ExtractedPost) -> ParseResult:
    """Parse a post; on any hard failure return `error` and no outages (spec 5.4)."""
    try:
        outages, warnings = _parse(post.lines)
    except _Fail as e:
        return ParseResult((), (), str(e))
    return ParseResult(tuple(outages), tuple(warnings), None)
