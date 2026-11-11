"""Extracted post lines -> outages, by rules only (spec 5.4).

A state machine over the lines: HEADING -> (TIME -> PURPOSE -> AREAS -> MAP?)+. A label's value
is the rest of its line, or else the lines of the next table cell (spec 5.4: the areas cell may
hold several lines; they are joined with "\n"). Anything unknown fails the whole post loudly
rather than being guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from outages.extract import ExtractedPost, Line
from outages.timeparse import (
    AmPmSlip,
    DateRange,
    HeadingOutsideWeek,
    fix_heading,
    heading_warnings,
    parse_heading,
    parse_time,
    slug_week,
)

# `ime:` is a real typo for `Time:` (2026-11-11 backfill).
_LABEL = re.compile(r"^(t?ime|purpose|areas affected|map):\s*(.*)$", re.IGNORECASE)
# `Updated Schedule: <range> Original Schedule: <range> - CANCELLED` (March 8-13 2026): the
# updated range is stored as posted, the original as cancelled (owner, 2026-11-11).
_RESCHEDULE = re.compile(r"^updated schedule:\s*(.+?)\s*original schedule:\s*(.+)$", re.IGNORECASE)
# A time value with no `Time:` label before it.
_BARE_TIME = re.compile(r"^\d{1,2}:\s?\d")
# A line that starts like a date and then holds a year-ish number or a `(weekday` but isn't a
# known heading form (`Nov. 10, 2026`, `November 20, 024 (Wednesday)`): failing beats filing
# the next outages under the previous date.
_HEADING_LIKE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\b"
    r"(?:.*\b\d{2,4}\b|\s*\()",
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
    if not m:
        return None
    name = m[1].lower()
    return ("time" if name == "ime" else name, m[2].strip())


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
    heading_fix: tuple[str, ...] = ()
    time: list[Line] | None = None
    purpose: list[Line] | None = None
    areas: list[Line] | None = None
    map: list[Line] | None = None


def _finish(b: _Block, seq: int) -> list[ParsedOutage]:
    """The block's outages: one, or one per range when a line holds `A to B (1hr); C to D`."""
    if not b.time:
        raise _Fail(f"block under {b.heading_text!r} has no time")
    if not b.areas:
        raise _Fail(f"block at {b.time[0].text!r} under {b.heading_text!r} has no areas")
    time_text = " ".join(ln.text for ln in b.time)
    # (time text, forced status, warning) per outage.
    resched = _RESCHEDULE.match(time_text)
    if resched:
        parts = [
            (resched[1], None, "updated schedule; the original is stored as cancelled"),
            (resched[2], "cancelled", "original schedule, replaced by the updated one"),
        ]
    else:
        split = [p.strip() for p in time_text.split(";") if p.strip()]
        note = f"one of {len(split)} time ranges on one line" if len(split) > 1 else None
        parts = [(p, None, note) for p in split]
    ranges = []
    for part, forced, note in parts:
        try:
            tr = parse_time(part, b.heading)
        except AmPmSlip as e:
            raise _Fail(f"{e} under {b.heading_text!r}") from None
        if tr is None:
            raise _Fail(f"unknown time format {time_text!r} under {b.heading_text!r}")
        ranges.append((tr, forced, note))

    common = [*b.heading_fix, *heading_warnings(b.heading)]
    if not b.purpose:
        common.append("no purpose")
    lines = [*b.time, *(b.purpose or []), *b.areas, *(b.map or [])]
    if any(ln.struck for ln in lines):
        common.append("strikethrough in block (status left as posted)")
    imgs = [u for ln in (b.map or []) for u in ln.img_urls]
    if len(imgs) > 1:
        common.append(f"{len(imgs)} map images; kept the first")
    if b.heading.status == "cancelled":
        common.append("cancelled by its date heading")
    area_lines = [ln.text for ln in b.areas]
    area_lines[0] = _strip_repeated_label(area_lines[0], "areas affected")
    areas_raw = "\n".join(t for t in area_lines if t)
    return [
        ParsedOutage(
            seq=seq + k,
            heading_text=b.heading_text,
            start_at=tr.start,
            end_at=tr.end,
            duration_label=tr.duration_label,
            status=b.heading.status or forced or tr.status,
            purpose=" ".join(ln.text for ln in b.purpose or []),
            areas_raw=areas_raw,
            areas_key=areas_key(areas_raw),
            map_image_url=imgs[0] if imgs else None,
            warnings=(*common, *([note] if note else []), *tr.warnings),
        )
        for k, (tr, forced, note) in enumerate(ranges)
    ]


def _has_more_blocks(lines: tuple[Line, ...], i: int) -> bool:
    return any(_is_boundary(ln) or (_label(ln) or ("",))[0] == "time" for ln in lines[i:])


def _parse(
    lines: tuple[Line, ...], week: tuple[date, date] | None
) -> tuple[list[ParsedOutage], list[str]]:
    outages: list[ParsedOutage] = []
    post_warnings: list[str] = []
    heading: DateRange | None = None
    heading_text = ""
    heading_fix: tuple[str, ...] = ()
    block: _Block | None = None

    def close() -> None:
        nonlocal block
        if block is not None:
            outages.extend(_finish(block, len(outages)))
            block = None

    i = 0
    while i < len(lines):
        ln = lines[i]
        h = parse_heading(ln.text)
        if h is not None:
            close()
            try:
                heading, heading_fix = fix_heading(h, week)
            except HeadingOutsideWeek as e:
                raise _Fail(f"{e} ({ln.text!r})") from None
            heading_text = ln.text
            i += 1
            continue
        if heading is None:  # title, "x days ago", "Updated: …" before the first heading
            i += 1
            continue
        if _HEADING_LIKE.match(ln.text):
            raise _Fail(f"unknown date heading format {ln.text!r}")
        lab = _label(ln)
        if lab is not None and ln.text[:4].lower() == "ime:":
            post_warnings.append(f"label {ln.text[:4]!r} read as 'Time:'")
        if lab is None and _BARE_TIME.match(ln.text):
            post_warnings.append(f"time line without 'Time:' label: {ln.text!r}")
            lab = ("time", ln.text)
        if lab is None:
            if _has_more_blocks(lines, i + 1):
                post_warnings.append(f"ignored line {ln.text!r}")
                i += 1
                continue
            break  # trailing content after the last block
        name, rest = lab
        if name == "time":
            close()
            block = _Block(heading, heading_text, heading_fix)
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


def parse_post(post: ExtractedPost, slug: str | None = None) -> ParseResult:
    """Parse a post; on any hard failure return `error` and no outages (spec 5.4).

    `slug` gives the post's own week, used to correct heading dates outside it (fix_heading).
    """
    try:
        outages, warnings = _parse(post.lines, slug_week(slug))
    except _Fail as e:
        return ParseResult((), (), str(e))
    return ParseResult(tuple(outages), tuple(warnings), None)
