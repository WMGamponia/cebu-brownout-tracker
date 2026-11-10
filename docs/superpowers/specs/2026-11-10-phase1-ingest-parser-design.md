# Phase 1 Ingest, Parse, Store, Backfill — Design

Status: approved in brainstorming 2026-11-10. Recon (§3) was done before this spec was written.

## 1. Intent

Phase 1 builds the deterministic half of the pipeline: find every VECO service interruption post, fetch it politely, turn it into outage records with rules only (no LLM), store them in SQLite with their edit history, and keep them current as VECO edits posts.

It also sets up the project skeleton every later phase builds on (package layout, CLI, tests on saved real pages, CI).

Success:
- `python -m outages backfill` fetches all interruption posts (151 at recon time) and stores their outages. The backfill report shows, per post, the outage count, warnings and failures. Every failure is a format the parser doesn't know yet, reported, never guessed.
- `python -m outages check` picks up new and edited posts, and a re-parse of an edited post updates existing outages and logs each change (`added`, `cancelled`, `time_changed`, `areas_changed`, `removed`).
- The parser is a pure function of the post HTML and is tested in CI on saved real posts, with no network.
- Overnight, multi-day, midnight, CANCELLED and ADDITIONAL cases each have explicit tests.

## 2. Decisions (owner, 2026-11-10)

| Topic | Decision |
|---|---|
| Discovery | `blog-posts-sitemap.xml`, posts whose slug contains `service-interruption`. Slugs are never constructed. The listing pages are not crawled. |
| Polling | Checks at 07:00 and 17:00 Asia/Manila. Phase 3 adds one fetch of the current week's post right before each alert run (≤ ~4 requests/day in total). |
| Runs on | The owner's PC. One `serve` process; catch-up check on start if the last check is > 12 h old. |
| LLM | None in Phase 1. Phase 2: barangays and cities by rules; streets/sitios/landmarks by rules or local Qwen via Ollama (the default LLM), chosen by the eval. Claude Haiku is an optional eval arm, run only when `ANTHROPIC_API_KEY` is set. No paid service is required. |
| Identity on re-parse | Matching passes (§6.3), not "date + time + areas", so a time change is recorded as a change. |
| Vanished outages | An outage missing from a newer version without a CANCELLED mark becomes `removed` (kept, logged), not deleted. |
| Map image | Optional column. No post at recon time has one; store it if one appears. |
| Outage times | ISO 8601 with `+08:00` (Asia/Manila, no DST). Bookkeeping timestamps (`fetched_at` etc.) in UTC ISO 8601. |
| Package / CLI | Package `outages`, run as `python -m outages <command>`. Named for the domain, so MCWD water interruptions (Phase 7) fit. |
| CI | `ruff check`, `ruff format --check`, pytest on `ubuntu-latest` (Python 3.12) and `windows-latest` (Python 3.14). Tests marked `live` are skipped. |
| Repo | Private GitHub repo under `WMGamponia`. Branch per phase, PR to `main`. |
| Attribution | No `Co-Authored-By` or "Generated with" lines in commits or PRs. |

## 3. Recon findings (2026-11-10)

Fetched with User-Agent `CebuBrownoutTracker/0.1`, a few seconds apart: `robots.txt`, `sitemap.xml`, `blog-posts-sitemap.xml`, `pages-sitemap.xml`, `privacy-statement`, and five posts:

| Post slug | Era | Notes |
|---|---|---|
| `service-interruption-january-23-2024` | one-day post | `Portion of Cebu City (Tisa)` layout, `Time :` with a space, no Map field |
| `service-interruption-february-29-march-01-2024` | one overnight post | heading `February  29, 2024 - March 01, 2024  (Thursday-Friday)`, time `10:00 PM of February 29 to 6:00 AM of March 1 (8hrs)`, two-town line `Consolacion & Liloan (Lamac & Yati)` |
| `service-interruption-january-18-24-2025` | weekly | city-first layout, `( Saturday)`, U+2011 non-breaking hyphen in `Kinasang‑an Pardo`, two-town line `San Fernando & City of Naga (Pitalo & Langtad)` |
| `service-interruption-november-30-december-6-2025` | weekly | barangay-first layout `Portion of Carreta, Cebu City,`, `- **ADDITIONAL**` suffix, `12:00 AM to 6:00 AM` |
| `service-interruption-november-8-14-2026` | weekly, edited after publish | `- **CANCELLED**` and `-**CANCELLED**`, empty `Map:` cells, multi-day headings `November 11-12, 2026 (Wednesday-Thursday)` |

### 3.1 Site and access
- `robots.txt`: `User-agent: * / Allow: / / Disallow: *?lightbox=`. Blog posts are allowed.
- No terms-of-use page in `pages-sitemap.xml`. The privacy statement says nothing about automated access.
- Wix site. Each post page is ~1.3 MB; the post content is server-rendered (Ricos), largely as table cells.
- `blog-posts-sitemap.xml` lists 552 posts with `lastmod` (a date, no time). 151 slugs contain `service-interruption`, the oldest from January 2024. 2024 has many one-day posts and follow-ups with a `-1` suffix (`…january-24-2024-1`).

### 3.2 Post structure
- A date heading is followed by one or more outage blocks. The date is **not** repeated per outage.
- A block is `Time:` / `Purpose:` / `Areas Affected:` and, from 2026, `Map:`. Each label's value is on the following line (separate table cell).
- Time: `9:00 AM to 12:00 PM (3hrs)`. Overnight: under a multi-day heading, `10:00 PM of November 11 to 5:00 AM of November 12 (7hrs)`. Also `12:00 AM to 6:00 AM (6hrs)` and `10:00 PM to 11:00 PM (1hr)`.
- Status suffix after the duration: `- **CANCELLED**`, `-**CANCELLED**`, `- **ADDITIONAL**`. A stray non-breaking space can trail the duration.
- No map image in any sampled post: the 2026 `Map:` cell is empty, older posts have no Map field. The only `static.wixstatic.com/media` URLs on the page are site chrome.

### 3.3 "Areas Affected" (raw text kept in Phase 1; parsed in Phase 2)
- City-first (2024 – early 2025): `Portion of Cebu City (Tisa) including portions of South Hills Subdivision.`
- Barangay-first (late 2025 →): `Portion of Banilad & Talamban, Cebu City, along portions of Banilad Road, …`
- Two towns: `Portion of Consolacion & Liloan (Calero, Cansaga, Tayud)`. Tayud is a barangay in both towns.
- Names that don't match PSGC directly: `Duljo Fatima` (→ Duljo), `Quiot` (→ Quiot Pardo), `Tugbungan` (→ Tugbongan), `North/South Reclamation Area` and `Greenhills` (not barangays).

### 3.4 Boundaries (for Phase 2+)
`faeldon/philippines-json-maps`, PSGC 3Q 2026 (built 2026-11-07), MIT, per-city barangay GeoJSON. All 8 VECO towns are complete and every shape has `geom_src: source`. HDX COD-AB is rejected (2018 boundaries, 1 GB, CC BY-IGO). Phase 2 pins a release and checks its `SHA256SUMS`.

## 4. Constraints

- Windows 11, Python 3.14 in `.venv`; the code targets 3.12+.
- New dependencies: `httpx`, `selectolax`, `apscheduler`; dev: `pytest`, `pytest-cov`, `ruff`. Later phases confirm their own (`fastapi`, `python-telegram-bot`, `ollama`, optional `anthropic`, …).
- Personal tool, single user. Simplicity beats generality.
- Raw HTML, the database and logs live in `data/` (gitignored). Fixtures are public posts and need no scrubbing.

## 5. Components

```
src/outages/
  __main__.py      CLI: backfill, check, reparse, serve, report
  config.py        paths, User-Agent, schedule, delays
  discover.py      sitemap XML -> list[PostRef(url, slug, lastmod)]
  fetch.py         polite httpx client: one at a time, UA, timeout, retry, delay
  extract.py       post HTML -> list[str] normalized lines (selectolax)
  normalize.py     text normalization (NFKC, nbsp, U+2011, spacing, "Time :")
  parse.py         lines -> ParseResult(outages: list[ParsedOutage], warnings, error)
  timeparse.py     date headings and time ranges -> start/end datetimes
  db.py            connection, migrations, queries
  migrations/      001_init.sql, ...
  upsert.py        match a new parse against stored outages -> changes
  pipeline.py      discover -> fetch -> store version -> parse -> upsert
  scheduler.py     APScheduler jobs for serve
```

Each module has one job and is testable alone. `parse.py`, `timeparse.py`, `normalize.py` and `upsert.py` are pure (no I/O).

### 5.1 `extract.py`
Find the post content node (the Ricos viewer inside the post `<article>`), walk block elements (paragraphs, headings, table cells, list items) in document order, and emit one normalized line per non-empty block. Also report whether a block contained strikethrough (`<s>`, `text-decoration: line-through`) and any `<img>` inside a `Map:` cell. The stored text hash (`text_hash`) is SHA-256 of these lines joined by `\n`, so Wix markup churn doesn't count as an edit.

### 5.2 `normalize.py`
NFKC; ` ` and other spaces → space; `‑`, `–`, `—` → `-`; collapse whitespace; strip `**`-style artifacts; `Label :` → `Label:`; `( Saturday)` → `(Saturday)`.

### 5.3 `timeparse.py`
- Date heading forms (case-insensitive, optional weekday in parentheses):
  - `November 8, 2026 (Sunday)`
  - `November 11-12, 2026 (Wednesday-Thursday)`
  - `November 30 - December 1, 2025 (…)` (cross-month, same year; expected but not yet seen, so the parser accepts it and a test covers it)
  - `February 29, 2024 - March 01, 2024 (Thursday-Friday)`
- Time forms:
  - `H:MM AM to H:MM PM (Nhrs)`: start on the heading's first date; if end ≤ start, end is the next day.
  - `H:MM PM of <Month D> to H:MM AM of <Month D> (Nhrs)`: dates explicit, year from the heading (December → January rolls the year).
  - `12:00 AM` is midnight at the start of the day; `12:00 PM` is noon.
- Warnings (not failures): weekday doesn't match the date; the `(Nhrs)` label doesn't match the computed duration; an explicit date outside the heading's range.

### 5.4 `parse.py`
A state machine over the lines: `HEADING → (TIME → PURPOSE → AREAS → MAP?)+`. Label and value may share a line or sit on consecutive lines. Lines before the first heading (title, "x days ago", "Updated: …") and after the last block (related-posts list) are ignored. Status comes from a suffix after the time (`CANCELLED`, `ADDITIONAL`, loose on dashes/spaces); strikethrough in a block adds a warning but does not change status.

`ParsedOutage`: `seq` (order in the post), `heading_text`, `start_at`, `end_at`, `duration_label`, `status` (`scheduled` | `cancelled` | `additional`), `purpose`, `areas_raw`, `areas_key`, `map_image_url | None`, `warnings: list[str]`.

`areas_key`: `areas_raw` lowercased, punctuation removed, whitespace collapsed.

**Hard failure** (whole post): no heading found; a time line that matches no form; a block missing Time or Areas; zero outages. The result carries `error` and no outages.

## 6. Storage

### 6.1 Schema (`001_init.sql`)
- `schema_version(version)`
- `posts(id, url UNIQUE, slug, title, sitemap_lastmod, first_seen_at, last_fetched_at, text_hash, parse_status CHECK IN ('ok','failed','pending'), parse_error)`
- `post_versions(id, post_id, fetched_at, text_hash, content_gz BLOB)`: one row per distinct `text_hash`. `content_gz` is the gzipped HTML of the post content node only.
- `outages(id, post_id, seq, start_at, end_at, duration_label, status CHECK IN ('scheduled','cancelled','additional','removed'), purpose, areas_raw, areas_key, map_image_url, warnings_json, first_seen_version_id, last_seen_version_id)`
- `outage_changes(id, outage_id, version_id, kind CHECK IN ('added','cancelled','time_changed','areas_changed','status_changed','removed'), old_json, new_json, detected_at, notified_at NULL)`
- Indexes: `outages(start_at)`, `outages(post_id)`, `outage_changes(notified_at)`.

On the first parse of a post, its outages are inserted with no `added` changes (backfill must not look like a flood of news). Changes are logged only when a post that already has outages is re-parsed.

### 6.2 Versions
A fetch whose `text_hash` equals the post's current hash only updates `last_fetched_at`. A new hash stores a `post_versions` row, re-parses, and upserts. A failed parse keeps the old outages untouched and sets `parse_status='failed'`.

### 6.3 Upsert matching (`upsert.py`)
Given stored outages O (not `removed`) and new parse N for the same post, match in passes; each outage is matched at most once; ties pair in page order:
1. Same start date, start/end time and `areas_key` → unchanged; if status differs, log `cancelled` or `status_changed`.
2. Same start date and `areas_key`, different time → `time_changed`.
3. Same start date and start/end time, different `areas_key` → `areas_changed`.
4. Remaining O → status `removed`, log `removed`. Remaining N → insert, log `added`.

Matched rows take the new values and `last_seen_version_id`.

### 6.4 Cross-post duplicates
An outage with the same `start_at`, `end_at` and `areas_key` in two posts is stored under both. Phase 3 dedupes alerts on that triple. `report` lists such pairs.

## 7. Fetching and scheduling

- `fetch.py`: httpx, User-Agent `CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/<repo>)`, 30 s timeout, one retry after 30 s on network errors or 5xx, no concurrency. Backfill waits a random 3–8 s between posts. Non-200 → logged, post left as is.
- `check`: fetch the sitemap; fetch posts that are new, have a newer `lastmod` than stored, or whose date range includes today or a later day (the current/upcoming week). Usually 2 requests.
- `backfill`: every interruption post in the sitemap not yet stored (resumable).
- `reparse`: re-run parse + upsert over stored versions, no network (after a parser fix).
- `serve`: APScheduler jobs at 07:00 and 17:00 Asia/Manila; on start, run `check` if the last check is > 12 h old. No web server yet (FastAPI arrives in Phase 4).
- `report`: per post: date range, outages, warnings, failures; plus cross-post duplicates. Printed as a table and written to `data/reports/`.
- Logging: stdlib `logging` to console and a rotating file in `data/logs/`.

## 8. Testing

- **Fixtures** `tests/fixtures/posts/<slug>.html`: about 10 real posts covering both area layouts, overnight with explicit dates, a multi-day heading, a cross-month week, CANCELLED, ADDITIONAL, a `-1` follow-up, a 2024 one-day post and the newest week. `tests/fixtures/posts/README.md` lists each one's URL, fetch date and what it covers. The fixture files are fetched once by a session script, and not by tests.
- **Golden tests:** fixture → expected outages JSON (`tests/fixtures/expected/<slug>.json`). The expected JSON is written by hand from reading the post, not copied from parser output, for at least 3 fixtures. For the rest, parser output is reviewed line by line before it's accepted.
- **Unit tests:** `normalize`, every date and time form, overnight, midnight/noon, year rollover, duration mismatch warning, status suffixes, hard-failure cases.
- **Upsert tests:** synthetic before/after pairs for each change kind and for the tie case (same areas twice on one day).
- **Discovery tests:** a saved sitemap fixture.
- **DB tests:** migrations on a temp DB; version dedupe by hash.
- **Live tests** (`@pytest.mark.live`, skipped in CI): fetch the sitemap and the newest post, parse without error.

## 9. Definition of done

- CI green on Ubuntu and Windows.
- Backfill run on the owner's PC; `report` output shown to the owner: posts fetched, outages stored, warnings and failures each explained (new format → stop and report, per the brief).
- A manual `check` after VECO edits the current week's post logs the expected changes.
- ROADMAP updated with the backfill numbers and any lessons.

## 10. Session schedule (detailed in the plan)

- **Session 2:** scaffold + CI, fixtures, `normalize`, `timeparse`, `extract`, `parse` with golden tests. Stop for review.
- **Session 3:** schema + migrations, `upsert`, `discover`, `fetch`, `pipeline`, CLI, `serve`, live backfill, report. Stop for review.

## 11. Risks and accepted limits

- **Wix markup changes** break `extract.py`. Mitigation: the hash and parser work on normalized lines, the content node selector is one function with a test, and a hard failure is loud.
- **Unknown formats in the 146 posts not sampled.** Expected; the backfill report surfaces them. Each new format gets a fixture and a test before it is handled.
- **Same-day edits after the last check** are missed until the next check (Phase 3 adds pre-alert fetches).
- **`lastmod` is date-only**, so `check` always re-fetches the current/upcoming week's posts regardless of `lastmod`.
- **Raw HTML grows** by ~10–50 KB gzipped per version; fine for years.
- **Times are only as right as VECO's post.** The duration label cross-check catches some typos; it doesn't fix them.
