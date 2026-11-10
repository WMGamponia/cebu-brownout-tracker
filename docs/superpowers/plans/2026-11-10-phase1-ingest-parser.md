# Phase 1 Ingest, Parse, Store, Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m outages backfill` stores every VECO service interruption post and its outages in SQLite, `check` keeps them current with a logged change history, and the rule-based parser is tested in CI on saved real posts.

**Architecture:** One `outages` package under `src/`. Pure layers (`normalize` → `timeparse` → `extract` → `parse` → `upsert.match`) are tested on fixtures with no I/O; `db`, `fetch`, `discover` and `pipeline` wrap them; the CLI and `serve` sit on top. The spec's §5 module list is the file map.

**Tech Stack:** Python 3.12+ (dev on 3.14), httpx 0.28, selectolax 1.0, APScheduler 3.11, tzdata, stdlib `sqlite3`; pytest 9 + pytest-cov 7, ruff 0.17, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-10-phase1-ingest-parser-design.md`

## Global Constraints

- `requires-python = ">=3.12"`; venv `.venv` on 3.14 (`py -3.14`); package `outages` in `src/outages/`, installed editable.
- Runtime deps: `httpx>=0.28`, `selectolax>=1.0`, `apscheduler>=3.11,<4`, `tzdata>=2026.1`. Dev deps: `pytest>=9`, `pytest-cov>=7`, `ruff>=0.17`. No others without asking.
- Outage times: `datetime` with `ZoneInfo("Asia/Manila")`, stored as `isoformat()` (`2026-11-11T22:00:00+08:00`). Bookkeeping timestamps: UTC `isoformat(timespec="seconds")`.
- User-Agent exactly `CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/OutageTracker)`.
- Only `fetch.py` imports httpx; only `scheduler.py` imports APScheduler. Pure modules (`normalize`, `timeparse`, `extract`, `parse`, `upsert`) do no I/O.
- Files read/written with `encoding="utf-8"`; text written with `newline="\n"`. CLI output must not crash on a cp1252 console (`sys.stdout.reconfigure(errors="replace")` in `main`).
- `main(argv: list[str] | None = None) -> int`: 0 ok, 1 some posts failed or fetch errors, 2 usage. Errors on stderr prefixed `error:`.
- Tests marked `live` run only with `-m live` (`addopts = "-m 'not live'"`). No test touches the network otherwise.
- `data/` is gitignored and never committed. Fixtures live in `tests/fixtures/`.
- Commits: conventional with a scope (`feat(parse): …`), imperative, under 72 chars, body says why. **No `Co-Authored-By` or "Generated with" lines.** No repo creation, push, PR or merge without the owner's OK.
- Branch: `phase1-ingest-parser` from `main`.
- Never put an empty `python -` heredoc in a shell command (it hangs); use files or `python -c`.

## Review Focus

1. **Relative-time lines change every fetch.** `5 days ago`, `Updated: 6 hours ago` sit inside the article; if they reach `text_hash`, every check looks like an edit and stores a new version. Lines before the first date heading must be excluded from the hash → Task 4 `test_hash_ignores_relative_time_lines`.
2. **Year rollover.** A late-December week with `10:00 PM of December 31 to 5:00 AM of January 1` or a `December 28, 2025 - January 3, 2026` heading must end in the next year → Task 3 `test_explicit_dates_roll_year`, `test_heading_cross_year`.
3. **A post that parsed before and now fails** (VECO typo in an edit) must leave its outages untouched, not mark them `removed` → Task 9 `test_failed_reparse_keeps_outages`.
4. **Interrupted backfill** (Ctrl+C or network drop at post 80) must resume from where it stopped, with no duplicate posts or outages → Task 9 `test_backfill_resumes_without_duplicates`.
5. **Non-ASCII names on Windows** (`Kinasang-an`, `Sto. Niño`, `Ma. Theresa`) printed by `report` on a cp1252 console must not crash → Task 9 `test_report_prints_on_cp1252_console`.

---

## Part A — scaffold, fixtures, parser

### Task 1: Project scaffold, CI and `.github/` docs

**Files:**
- Create: `pyproject.toml`, `src/outages/__init__.py`, `src/outages/__main__.py`, `src/outages/config.py`, `tests/conftest.py`, `tests/test_cli.py`
- Create: `.github/workflows/ci.yml`, `.github/copilot-instructions.md`, `.github/instructions/python.instructions.md`, `.github/pull_request_template.md`, `.github/prompts/pr.prompt.md`, `.github/prompts/roadmap.prompt.md`

**Interfaces:**
- Produces: `outages.__main__.main(argv) -> int` with subcommands `backfill`, `check`, `reparse`, `report`, `serve` (stubs printing `error: not implemented`, returning 1, until Task 9). `outages.config.Paths(root: Path)` frozen dataclass with `data`, `db` (`data/outages.db`), `logs`, `reports`; `Paths.default()` uses `Path.cwd()`. Constants `USER_AGENT`, `SITEMAP_URL = "https://www.visayanelectric.com/blog-posts-sitemap.xml"`, `SLUG_MARKER = "interruption"`, `CHECK_TIMES = ("07:00", "17:00")`, `BACKFILL_DELAY_S = (3, 8)`, `CATCH_UP_AFTER_H = 12`.

- [ ] **Step 1:** `git switch -c phase1-ingest-parser`; `py -3.14 -m venv .venv`.
- [ ] **Step 2: Write `pyproject.toml`**: setuptools, `where = ["src"]`, deps from Global Constraints, `[project.optional-dependencies] dev`, pytest `testpaths = ["tests"]`, marker `live: hits visayanelectric.com; run with -m live`, `addopts = "-m 'not live'"`; ruff `line-length = 100`, `target-version = "py312"`, `select = ["E", "F", "I", "UP", "B", "SIM"]`.
- [ ] **Step 3:** `.venv/Scripts/python -m pip install -e ".[dev]"`.
- [ ] **Step 4: Write the failing tests** in `tests/test_cli.py`: `test_help_lists_commands` (`main(["--help"])` raises `SystemExit(0)`, stdout contains `backfill`, `check`, `reparse`, `report`, `serve`) and `test_unknown_command_exits_2`.
- [ ] **Step 5:** Run `.venv/Scripts/python -m pytest -q`; expect FAIL (no module). Implement `__main__.py` (argparse) and `config.py`; run again, expect PASS.
- [ ] **Step 6: CI** `.github/workflows/ci.yml`: on push/PR to `main`; job `lint` (ubuntu, 3.12: `ruff check`, `ruff format --check`); job `test` matrix `{ubuntu-latest: "3.12", windows-latest: "3.14"}`: `pip install -e ".[dev]"`, `pytest -q --cov=outages --cov-report=term`.
- [ ] **Step 7: `.github/` docs**: adapt the shape of `E:\Work\Projects\PH_Legal_Docs_Assistant\.github\` (not its domain content): project summary, layout, commands, the Global Constraints above, "no attribution lines", PR template with Summary / Changes / Testing / Roadmap.
- [ ] **Step 8:** `ruff check`, `ruff format --check`, `pytest -q` all pass. Commit `chore(scaffold): package, CLI stubs, ruff, pytest and CI`.

### Task 2: Fixtures (operational, no product code)

**Files:**
- Create: `tests/fixtures/posts/<slug>.html` (10 files), `tests/fixtures/sitemap/blog-posts-sitemap.xml`, `tests/fixtures/posts/README.md`
- Scratch only (not committed): a fetch script in the session scratchpad.

**Interfaces:**
- Produces: the fixture files later tasks read by slug; README table `slug | url | fetched (UTC) | covers`.

- [ ] **Step 1:** Write a scratchpad script that fetches a list of URLs one at a time with the Global User-Agent and a 5 s gap, saving raw bytes decoded as UTF-8. Never run it in a loop over the whole sitemap.
- [ ] **Step 2: Fetch the sitemap** into `tests/fixtures/sitemap/`.
- [ ] **Step 3: Fetch these 6 posts** (recon set + follow-up):
  `service-interruption-january-23-2024`, `service-interruption-february-29-march-01-2024`, `service-interruption-january-24-2024-1`, `service-interruption-january-18-24-2025`, `service-interruption-november-30-december-6-2025`, `service-interruption-november-8-14-2026`.
- [ ] **Step 4: Pick 4 more from the sitemap fixture** by slug, fetch, and check by eye: a late-December/early-January week (year rollover), a cross-month week (e.g. `…september-27-october-3-2026`), a mid-2024 post, and `interruption-june-22-28-2025` (the one slug without `service-`). If one shows a format the spec §3 doesn't list, **stop and report it to the owner** before Task 3.
- [ ] **Step 5:** Write `tests/fixtures/posts/README.md` (one row per fixture with what it covers, per spec §8). Commit `test(fixtures): ten real posts and the blog sitemap`.

### Task 3: `normalize` and `timeparse`

**Files:**
- Create: `src/outages/normalize.py`, `src/outages/timeparse.py`, `tests/test_normalize.py`, `tests/test_timeparse.py`

**Interfaces:**
- Produces:
  - `normalize_line(s: str) -> str` (spec §5.2).
  - `MANILA = ZoneInfo("Asia/Manila")`.
  - `@dataclass(frozen=True) DateRange(start: date, end: date, weekday_text: str | None)`.
  - `@dataclass(frozen=True) TimeRange(start: datetime, end: datetime, duration_label: str | None, status: Literal["scheduled","cancelled","additional"], warnings: tuple[str, ...])`.
  - `parse_heading(line: str) -> DateRange | None`.
  - `parse_time(line: str, heading: DateRange) -> TimeRange | None` (None = no form matched; caller treats as hard failure).

- [ ] **Step 1: Write the failing tests.** `test_normalize.py`: nbsp → space; `Kinasang‑an` (U+2011) → `Kinasang-an`; `Time :` → `Time:`; `( Saturday)` → `(Saturday)`; double spaces collapsed; `**` stripped.
  `test_timeparse.py` (each a separate test, values exact):
  - `parse_heading("November 8, 2026 (Sunday)") == DateRange(date(2026,11,8), date(2026,11,8), "Sunday")`
  - `"November 11-12, 2026 (Wednesday-Thursday)"` → 11 to 12 Nov 2026
  - `"February 29, 2024 - March 01, 2024 (Thursday-Friday)"` → 29 Feb to 1 Mar 2024
  - `"November 30 - December 1, 2025 (Sunday-Monday)"` → cross-month
  - `test_heading_cross_year`: `"December 28, 2025 - January 3, 2026"` → end `date(2026,1,3)`
  - `parse_heading("Time:") is None`; `parse_heading("Service Interruption: November 8-14, 2026") is None` (a title is not a heading)
  - `"9:00 AM to 12:00 PM (3hrs)"` under 9 Nov 2026 → 09:00–12:00 +08:00, label `3hrs`, status `scheduled`, no warnings
  - `test_overnight_without_dates`: `"9:00 PM to 7:00 AM (10hrs)"` under 9 Nov → end 10 Nov 07:00
  - `test_explicit_dates`: `"10:00 PM of November 11 to 5:00 AM of November 12 (7hrs)"` under 11–12 Nov 2026 → 11 Nov 22:00 to 12 Nov 05:00
  - `test_explicit_dates_roll_year`: `"10:00 PM of December 31 to 5:00 AM of January 1 (7hrs)"` under 31 Dec 2025–1 Jan 2026 → end `2026-01-01T05:00+08:00`
  - `"12:00 AM to 6:00 AM (6hrs)"` → 00:00–06:00 same day; `"9:00 AM to 12:00 PM"` noon
  - `"10:00 PM to 11:00 PM (1hr)"` → label `1hr`
  - status: `"9:00 AM to 5:00 PM (8hrs)- CANCELLED"`, `"(8hrs)-CANCELLED"`, `"(8hrs) - cancelled"` → `cancelled`; `"(2hrs)- ADDITIONAL"` → `additional`
  - `test_duration_mismatch_warns`: `"8:00 AM to 5:00 PM (8hrs)"` → warning containing `duration`
  - `test_explicit_date_outside_heading_warns`: `"10:00 PM of November 14 to 5:00 AM of November 15 (7hrs)"` under 11–12 Nov 2026 → parsed, warning contains `outside heading`
  - `parse_time("9 AM - 5 PM", h) is None`
- [ ] **Step 2:** Run `pytest tests/test_normalize.py tests/test_timeparse.py -q`; expect FAIL.
- [ ] **Step 3: Implement.** Month names via a dict (no locale). Heading regex accepts an optional second month and optional second year; a range whose end < start rolls the end year +1. Explicit `of <Month D>` dates take the heading's start year, +1 when the month is before the heading's start month. A trailing non-breaking space after `)` is already gone after `normalize_line`.
- [ ] **Step 4:** Run the tests; expect PASS. Commit `feat(parse): text normalization and date/time parsing`.

### Task 4: `extract`

**Files:**
- Create: `src/outages/extract.py`, `tests/test_extract.py`

**Interfaces:**
- Consumes: `normalize_line`, `parse_heading`.
- Produces: `@dataclass(frozen=True) Line(text: str, struck: bool, img_urls: tuple[str, ...])`; `@dataclass(frozen=True) ExtractedPost(title: str | None, lines: tuple[Line, ...], content_html: str, text_hash: str)`; `extract_post(html: str) -> ExtractedPost`; `ExtractError(Exception)` when no content node is found.

- [ ] **Step 1: Write the failing tests** on fixtures:
  - `test_lines_in_order`: for `…november-8-14-2026`, the first line that `parse_heading` accepts is `November 8, 2026 (Sunday)`, and the lines `Time:`, `7:00 AM to 4:00 PM (9hrs)`, `Purpose:` follow in that order.
  - `test_title`: `title == "Service Interruption: November 8-14, 2026"`.
  - `test_related_posts_excluded`: no line equals `Service Interruption: November 03-08, 2026` (the related-posts footer).
  - `test_hash_ignores_relative_time_lines`: replacing `5 days ago` / `Updated: 6 hours ago` in the fixture HTML with other values gives the same `text_hash`; changing `(9hrs)` to `(8hrs)` changes it.
  - `test_hash_ignores_markup_churn`: renaming every `class="…"` attribute value gives the same hash.
  - `test_no_map_images`: every `Line.img_urls` is empty in all fixtures (recon: no maps), and a synthetic post with an `<img src="https://static.wixstatic.com/media/x.jpg">` in the cell after `Map:` yields that URL.
  - `test_strikethrough_flag`: synthetic HTML with `<s>9:00 AM to 5:00 PM</s>` gives `struck=True` on that line.
  - `test_no_content_raises`: `extract_post("<html></html>")` raises `ExtractError`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3: Implement** with selectolax: content node = the post's rich-content container inside `<article>` (pick the selector by inspecting a fixture; keep it in one function `_content_node(tree)`); walk `p, h1–h6, li, td` in document order, emit `normalize_line(text)` when non-empty. `text_hash` = SHA-256 hex of lines **from the first heading line onward** joined by `\n`. `content_html` = the content node's HTML.
- [ ] **Step 4:** Run; expect PASS. Commit `feat(extract): post content lines, hash and strikethrough flag`.

### Task 5: `parse` with golden tests

**Files:**
- Create: `src/outages/parse.py`, `tests/test_parse.py`, `tests/fixtures/expected/<slug>.json` (10 files)

**Interfaces:**
- Consumes: `ExtractedPost`, `Line`, `parse_heading`, `parse_time`, `normalize_line`.
- Produces: `@dataclass(frozen=True) ParsedOutage(seq: int, heading_text: str, start_at: datetime, end_at: datetime, duration_label: str | None, status: str, purpose: str, areas_raw: str, areas_key: str, map_image_url: str | None, warnings: tuple[str, ...])` with `to_json() -> dict` (datetimes as ISO strings); `@dataclass(frozen=True) ParseResult(outages: tuple[ParsedOutage, ...], warnings: tuple[str, ...], error: str | None)`; `parse_post(post: ExtractedPost) -> ParseResult`; `areas_key(s: str) -> str`.

- [ ] **Step 1: Write expected JSON by hand** from reading the post (not from parser output) for `…january-23-2024` (5 outages), `…february-29-march-01-2024` (1 overnight outage), and the first 6 outages of `…november-8-14-2026` (stored as `…november-8-14-2026.partial.json`; test compares the prefix).
- [ ] **Step 2: Write the failing tests:**
  - `test_golden[slug]` parametrized over every `expected/*.json`: `[o.to_json() for o in parse_post(extract_post(html)).outages] == expected` (prefix for `.partial.json`).
  - `test_nov_2026_counts`: 34 outages; 3 `cancelled`; seq 0..33 in page order.
  - `test_additional_status`: `…november-30-december-6-2025` has exactly 3 `additional`.
  - `test_label_and_value_on_one_line`: synthetic lines `["November 9, 2026 (Monday)", "Time: 9:00 AM to 12:00 PM (3hrs)", "Purpose: X", "Areas Affected: Portion of Lahug, Cebu City"]` parse to one outage.
  - `test_weekday_mismatch_warns`: heading `November 9, 2026 (Sunday)` → outage warning contains `weekday`.
  - `test_struck_block_warns_not_cancels`: a struck time line → status `scheduled`, warning contains `strikethrough`.
  - Hard failures (`error` set, `outages == ()`): no heading; unparseable time line; block missing Areas; zero outages.
  - `test_areas_key`: `areas_key("Portion of Banilad & Talamban, Cebu City, along Banilad Road.") == "portion of banilad talamban cebu city along banilad road"`.
- [ ] **Step 3:** Run; expect FAIL.
- [ ] **Step 4: Implement** the state machine (spec §5.4). Value = text after `Label:` on the same line, else the next non-empty line. Purpose and Map may be missing (Purpose missing → warning; Map missing is normal before 2026). Lines after the last block's Areas/Map that aren't a heading or `Time:` end parsing.
- [ ] **Step 5:** Run; expect PASS. Then generate expected JSON for the remaining 7 fixtures from parser output, **review each line against the post**, commit them only after that review, and note "reviewed" in the fixtures README.
- [ ] **Step 6:** Full suite + ruff pass. Commit `feat(parse): rule-based post parser with golden tests`.

---

## Part B — storage, fetching, CLI, backfill

### Task 6: `db` and migrations

**Files:**
- Create: `src/outages/db.py`, `src/outages/migrations/001_init.sql`, `tests/test_db.py`

**Interfaces:**
- Consumes: `ExtractedPost`, `ParsedOutage`.
- Produces:
  - `connect(path: Path) -> sqlite3.Connection` (creates parent dir, `PRAGMA foreign_keys=ON`, `row_factory=sqlite3.Row`, applies pending migrations in a transaction).
  - `@dataclass(frozen=True) StoredOutage(id: int, seq: int, start_at: str, end_at: str, status: str, areas_key: str, …all ParsedOutage fields)`.
  - `upsert_post(conn, url: str, slug: str, lastmod: date | None) -> int` (post id).
  - `store_version(conn, post_id: int, ex: ExtractedPost, fetched_at: str) -> int | None` (new version id, or None when `text_hash` equals the post's current hash; always updates `last_fetched_at`).
  - `active_outages(conn, post_id) -> list[StoredOutage]` (status != `removed`, ordered by seq).
  - `set_parse_status(conn, post_id, status: str, error: str | None)`.
  - `get_meta(conn, key) -> str | None`, `set_meta(conn, key, value)` (table `meta(key PRIMARY KEY, value)`, used for `last_check_at`).

- [ ] **Step 1: Write the failing tests:** fresh DB has `schema_version == 1` and all spec §6.1 tables; `connect` twice doesn't re-apply; `store_version` returns an id the first time and `None` for the same hash, a new id for a new hash; `content_gz` round-trips (`gzip.decompress(...).decode() == ex.content_html`); a `status` outside the CHECK list raises `sqlite3.IntegrityError`.
- [ ] **Step 2:** Run; expect FAIL. Implement `001_init.sql` exactly per spec §6.1 plus `meta`. Run; expect PASS.
- [ ] **Step 3:** Commit `feat(db): schema, migrations and post versions`.

### Task 7: `upsert`

**Files:**
- Create: `src/outages/upsert.py`, `tests/test_upsert.py`
- Modify: `src/outages/db.py` (add `apply_changes`)

**Interfaces:**
- Consumes: `StoredOutage`, `ParsedOutage`.
- Produces: `@dataclass(frozen=True) Change(kind: Literal["unchanged","added","cancelled","status_changed","time_changed","areas_changed","removed"], old: StoredOutage | None, new: ParsedOutage | None)`; `match(old: list[StoredOutage], new: list[ParsedOutage]) -> list[Change]` (pure); `db.apply_changes(conn, post_id: int, version_id: int, changes: list[Change], log: bool, detected_at: str) -> None` (`log=False` on a post's first parse: insert rows, no `outage_changes`).

- [ ] **Step 1: Write the failing tests** (build `StoredOutage`/`ParsedOutage` with a small helper):
  - identical lists → all `unchanged`
  - same date/time/areas, new status `cancelled` → `cancelled`; `cancelled`→`scheduled` → `status_changed`
  - same date and areas, start 09:00→10:00 → `time_changed` (not removed + added)
  - same date and times, areas text typo-fixed → `areas_changed`
  - old outage missing from new → `removed`; new extra → `added`
  - `test_tie_pairs_in_page_order`: two old and two new rows with the same areas on one date, times swapped in pass 2 → paired by seq order
  - pass priority: a row that matches pass 1 is never re-matched in pass 2
  - `apply_changes` with `log=False` writes outages and zero `outage_changes`; with `log=True` writes one row per non-`unchanged` change with `old_json`/`new_json`, and `removed` sets status `removed` without deleting.
- [ ] **Step 2:** Run; expect FAIL. Implement passes per spec §6.3 (compare full `start_at`/`end_at`; "same date" = `start_at` date). Run; expect PASS.
- [ ] **Step 3:** Commit `feat(upsert): match re-parsed outages and log changes`.

### Task 8: `discover` and `fetch`

**Files:**
- Create: `src/outages/discover.py`, `src/outages/fetch.py`, `tests/test_discover.py`, `tests/test_fetch.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) PostRef(url: str, slug: str, lastmod: date | None)`; `parse_sitemap(xml: str) -> list[PostRef]` (only slugs containing `SLUG_MARKER`, sorted by lastmod then slug); `Fetcher(client: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep, delay_s: tuple[float, float] = (0, 0))` with `get(url: str) -> str | None` (None on non-200 after retry, logged) and `close()`.

- [ ] **Step 1: Write the failing tests:** sitemap fixture → the interruption-post count noted in the fixtures README (151 at recon), including `interruption-june-22-28-2025`, and no non-interruption post (e.g. `meter-inspection-services`); `lastmod` parsed as `date`. `Fetcher` with `httpx.MockTransport`: sends the exact User-Agent; one retry after a 503 then success; 404 → `None` without retry; `sleep` called with a value in `delay_s` between consecutive `get`s, not before the first.
- [ ] **Step 2:** Run; expect FAIL. Implement (stdlib `xml.etree` for the sitemap; 30 s timeout; retry once after 30 s on `httpx.TransportError` or 5xx; the delay is drawn with `random.uniform`). Run; expect PASS.
- [ ] **Step 3:** Commit `feat(fetch): sitemap discovery and polite fetcher`.

### Task 9: `pipeline`, CLI, `report`, `serve`

**Files:**
- Create: `src/outages/pipeline.py`, `src/outages/report.py`, `src/outages/scheduler.py`, `tests/test_pipeline.py`, `tests/test_report.py`
- Modify: `src/outages/__main__.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `process_post(conn, ref: PostRef, html: str, now: datetime) -> Literal["new","changed","unchanged","failed"]`: store version → extract → parse → on error `set_parse_status('failed')` and leave outages; else `match` + `apply_changes(log = post had outages before)`.
  - `check(conn, fetcher, now: datetime) -> CheckSummary(fetched: int, new: int, changed: int, failed: int, errors: int)`: sitemap → posts that are new, have newer `lastmod`, or have an active outage ending on/after today (Manila) → fetch + process; sets meta `last_check_at`.
  - `backfill(conn, fetcher, now) -> CheckSummary`: every sitemap ref without a stored version.
  - `reparse(conn, now) -> CheckSummary`: latest version of every post, no network.
  - `report.build(conn) -> list[ReportRow]`, `report.format_table(rows) -> str`, cross-post duplicate pairs listed after the table; written to `data/reports/report-<UTC date>.txt`.
  - `scheduler.build(run_check: Callable[[], None]) -> BlockingScheduler` (cron jobs at `CHECK_TIMES`, `Asia/Manila`); `serve` runs `check` first when `last_check_at` is older than `CATCH_UP_AFTER_H`.
- [ ] **Step 1: Write the failing tests** (temp DB, `Fetcher` over `MockTransport` serving fixtures):
  - `test_first_parse_logs_no_changes`
  - `test_edit_logs_changes`: process the Nov 2026 fixture, then a copy with one time changed and one block removed → one `time_changed`, one `removed`
  - `test_unchanged_hash_skips_parse`
  - `test_failed_reparse_keeps_outages`: process a good fixture, then a broken copy (time line garbled) → `failed`, `active_outages` unchanged, no `removed` change
  - `test_backfill_resumes_without_duplicates`: fetcher raises on the 2nd of 3 posts; rerun → 3 posts, outages counted once each
  - `test_check_refetches_current_week`: a post with an outage ending tomorrow is fetched even with an unchanged `lastmod`
  - `test_report_prints_on_cp1252_console`: report containing `Sto. Niño` written to a `TextIOWrapper(BytesIO(), encoding="cp1252")` stdout via `main(["report"])` returns 0
  - `test_cli_backfill_exit_codes`: 0 when all ok, 1 when any post failed
  - `test_report_lists_cross_post_duplicates`: the same outage (start, end, areas_key) stored under two posts appears once in the duplicates section
  - `test_serve_catch_up`: `last_check_at` 13 h ago → `run_check` called once before the scheduler starts (scheduler start is stubbed)
- [ ] **Step 2:** Run; expect FAIL. Implement; logging to console + `RotatingFileHandler` in `data/logs/outages.log`. Run; expect PASS.
- [ ] **Step 3:** Add `@pytest.mark.live` test `test_live_newest_post_parses` (sitemap + newest post, no error).
- [ ] **Step 4:** Full suite + ruff. Commit `feat(cli): backfill, check, reparse, report and serve`.

### Task 10: Live backfill, review, hand-off (operational)

- [ ] **Step 1:** `python -m pytest -m live -q` once.
- [ ] **Step 2:** `python -m outages backfill` (≈15 min, one request at a time). Then `python -m outages report`.
- [ ] **Step 3: Triage every failure and warning.** A new format → add a fixture + failing test, fix, `reparse`. Do not guess; anything unclear goes to the owner.
- [ ] **Step 4:** Run `check` twice in a row; the second fetches only the sitemap and current-week posts and logs no changes.
- [ ] **Step 5:** Update `ROADMAP.md` (tick Phase 1 items, progress entry with posts/outages/warnings counts, lessons). Commit `docs: Phase 1 backfill results`.
- [ ] **Step 6: Stop and show the owner** the report. Ask before creating the private GitHub repo, pushing and opening the PR (enable CI Auto-fix on it). Write `prompts/phase2.md` for the Phase 2 brainstorm.
