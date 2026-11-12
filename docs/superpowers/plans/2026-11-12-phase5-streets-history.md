# Phase 5 Streets, History, Multi-user Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw VECO's streets as clipped OSM lines on the map (only if a frozen eval says they're precise), add a week picker plus a `/history` heatmap-and-stats page, and open the bot to people the admin approves, with `/forget`.

**Architecture:** A hand-run `streets fetch` saves one Overpass extract per town under `data/osm/`. An offline `streets match` turns each `street` entity into a `street_matches` row (status + clipped geometry) using pure `streets/normalize.py` and `streets/geo.py`. `map/build.py` reads those rows into each outage's `streets`; a pure `streets.js` picks which lines show. A pure `history/stats.py` computes union-hours stats from rows loaded read-only by `history/load.py`, served as `/stats.json` and drawn by `/history` with a pure `bins.js`. The bot gains a `pending → approved|blocked` flow on `subscribers.status`, admin buttons and commands, `/privacy` and `/forget`.

**Tech Stack:** Python 3.12+ (dev 3.14), stdlib `sqlite3`, `json`, `difflib`, `unicodedata`; httpx (Overpass, Telegram); FastAPI + uvicorn; Leaflet 1.9.4 vendored; Node 22 `node:test`. pytest, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-12-phase5-streets-history-design.md` (read it first; § numbers below refer to it). Phase 4 spec for the existing document and page.

## Global Constraints

- **No new dependencies.** Geometry in pure Python; charts as inline SVG; no npm packages.
- **Network:** only `streets fetch --yes` contacts Overpass (8 requests, ≥ 10 s apart, `config.USER_AGENT`); only `check`/`backfill`/`serve` contact VECO; Telegram only from `bot`/`serve`/`alerts --send`. Every offline command gets a CLI test that fails if it builds the VECO fetcher (`make_fetcher`), the Overpass client (`osm.overpass.make_client`) or a Telegram client. The suite-wide network block in `tests/conftest.py` stays.
- **Gates (stop and ask the owner):** the Overpass fetch (Task 3), label review and freeze (Task 4), migration + backup + `streets match` on the real DB (Task 6), the live Telegram test (Task 11), LAN exposure, push, PR, merge.
- **Read-only:** the map, `/stats.json`, `/history`, `stats`, `streets --review`, `match --dry-run` open `db.connect_ro`; the real DB's SHA-256 is unchanged after them.
- **Times:** Manila `+08:00`; JS never uses the browser's zone (Manila = epoch + 8 h with UTC getters). Months and days split at Manila midnight.
- **Values fixed by the spec:** buffer `150` m; bbox pad `500` m; Overpass spacing `10` s, back-off `30` s then `120` s, 2 retries; coordinates 6 decimals in `data/osm/`, 5 in `street_matches` and the document; gate `precision ≥ 0.95` with `≥ 20` drawn test rows; eval seed `20261112`, 80 rows (10 per town), 30 dev / 50 test; stats default range last 365 days (`from = to − 364`); 5 bins; top 10; worst 3 months; pending cap `20`; subscriptions cap `10`; rate limit `> 20` updates/min → ignore `10` min; send spacing `≥ 50` ms; pending reply at most once per `24` h.
- **Copy fixed by the spec:** history banner `These are VECO's scheduled interruptions, not measured outages.`; streets attribution `Streets © OpenStreetMap contributors (ODbL)`; popup `Streets drawn: <k> of <n>`; not-drawn reasons `not found on OpenStreetMap near these barangays` / `street data not downloaded` / `street matching not run yet`; side list `drawn by its streets only`; group refusal `This bot works in private chats only.`; join `Join request: <first name> (@username, chat <id>)`; approve `You're in. Try /add <barangay>.`; deny `Not approved.`; pending `Waiting for approval`; cap `Not taking new requests right now`; forget `Deleted. /start to ask again.`
- **Page safety:** data into the DOM only via `textContent`; links via `setAttribute('href', …)` after the existing VECO-URL check; no `innerHTML` with data.
- Files `encoding="utf-8"`, `newline="\n"`; JSON `ensure_ascii=False`. `data/` and `.env` are never committed; never read `.env` into the conversation.
- Commits: conventional with a scope, imperative, < 72 chars. **No `Co-Authored-By` or "Generated with" lines.**
- Branch `phase5-streets-history` from `phase4-map` (spec `84a84a5`). If PR #4 merges first, rebase onto `main` before the PR. Windows: prefer the Edit tool; write Python with the Write tool; never leave an empty `python -` heredoc; for TZ runs of node use PowerShell `$env:TZ=...`.

## Review Focus

1. **Overpass answers 200 with a `remark` (runtime error / timeout) and partial `elements`:** that town is a failure and keeps its old file; it must not be saved as a smaller "complete" extract → Task 3 `test_remark_is_failure_keeps_old_file`.
2. **A generic VECO name ("Barangay Road", "National Highway") matching an unrelated OSM way of that name in scope:** never drawn unless an alias says so → Task 5 `test_generic_names_never_drawn`.
3. **A stranger sends `/start` five times while pending:** one subscriber row and exactly one admin join message → Task 10 `test_repeat_start_while_pending_sends_one_request`.
4. **`/forget` arrives while an alert run is sending to that user** (bot and scheduler are separate threads): recording `alerts_sent` for a subscriber that no longer exists must not raise or recreate a trace of them → Task 10 `test_record_after_forget_is_skipped`.
5. **Areas re-parsed after the last `streets match`** (a new text): its streets show `not_processed`, not `not_found`, and nothing crashes → Task 7 `test_entity_without_match_row_is_not_processed`.

---

### Task 1: Migration 004 and config

**Files:**
- Create: `src/outages/migrations/004_streets_users.sql` (spec §4.1 verbatim)
- Modify: `src/outages/config.py`, `src/outages/map/app.py` (`/healthz` schema value follows the latest migration, not a literal 3)
- Test: `tests/test_db.py`, `tests/test_config.py`

**Interfaces:**
- Produces (`config.py`): `OVERPASS_URL = "https://overpass-api.de/api/interpreter"`, `OVERPASS_SPACING_S = 10`, `OVERPASS_BACKOFF_S = (30, 120)`, `OSM_DIR_NAME = "osm"` (under `data/`), `STREET_BUFFER_M = 150`, `BBOX_PAD_M = 500`, `STREETS_DRAW = False` (Task 6 sets it by the gate), `PENDING_CAP = 20`, `SUBSCRIPTION_CAP = 10`, `RATE_LIMIT_PER_MIN = 20`, `RATE_LIMIT_MUTE_S = 600`, `SEND_SPACING_S = 0.05`, `PENDING_REPLY_EVERY_H = 24`.
- Produces: schema version 4; `subscribers.status`, `requested_at`, `decided_at`; table `street_matches`.

- [ ] **Step 1: Write failing tests** — `test_migration_004_tables`: after `db.connect` on a temp path, `street_matches` has columns `areas_hash, seq, status, osm_name, geometry_json, osm_date`; `subscribers` has `status, requested_at, decided_at`; `_version(conn) == 4`. `test_migration_004_keeps_subscribers_approved`: a DB migrated to 3 with one subscriber, then `connect` → its `status == "approved"`. `test_phase5_settings`: each constant above equals its value.
- [ ] **Step 2: Run** `pytest tests/test_db.py tests/test_config.py -v` → FAIL.
- [ ] **Step 3: Implement** the SQL file and constants.
- [ ] **Step 4: Run** `pytest -q && ruff check` → all pass (fix `/healthz` golden/test expecting `schema: 3`).
- [ ] **Step 5: Commit** `feat(db): migration 004 for street matches and join status`

### Task 2: Street normalisation and geometry (pure)

**Files:**
- Create: `src/outages/streets/__init__.py`, `src/outages/streets/normalize.py`, `src/outages/streets/geo.py`
- Test: `tests/test_streets_normalize.py`, `tests/test_streets_geo.py`

**Interfaces:**
- Produces: `street_key(s: str) -> str` (spec §4.3); `GENERIC_STREET_KEYS: frozenset[str]` (spec §4.5 step 6).
- Produces (`geo.py`): `Point = tuple[float, float]` as (lon, lat); `distance_m(a: Point, b: Point) -> float`; `point_segment_m(p: Point, a: Point, b: Point) -> float`; `in_polygon(p: Point, ring: list[Point]) -> bool` (ray casting; outer ring only, holes ignored — medres polygons have none); `class Scope: __init__(polygons: list[list[list[Point]]], buffer_m: float = STREET_BUFFER_M)`, `contains(p: Point) -> bool`, `bbox -> tuple[float, float, float, float]` (W, S, E, N, padded by the buffer); `clip(coords: list[Point], scope: Scope) -> list[list[Point]]`; `round_lines(lines, ndigits=5)`; `polygons_of(feature: dict) -> list[list[list[Point]]]` (Polygon and MultiPolygon).
- Projection: x = lon·111 320·cos(10.3°), y = lat·110 574 metres.

- [ ] **Step 1: Write failing tests** — normalise: `"A. Seno Extension"`, `"A. Seno Ext."` → same key; `"Gov. M. Cuenco Ave."` == `street_key("Governor M. Cuenco Avenue")`; `"portion of Cebu South Coastal Road."` == `"cebu south coastal road"`; `"Tiltilon Baranggay Road"` == `"tiltilon barangay road"`; `"Sto. Niño St."` == `"santo nino street"`; `"Brgy. Lanas Road."` == `"barangay lanas road"`; `"M.L. Quezon St."` == `"m l quezon street"`; `"barangay road" in GENERIC_STREET_KEYS`. Geo: `distance_m` of 0.01° lat ≈ 1,105.7 m (±1 m); `in_polygon` for inside/outside/vertex-adjacent points of a square; `Scope.contains` true for a point 100 m outside an edge, false for 200 m; `clip` of a way crossing the square gives one run; leaving and re-entering gives two runs; a run of one node is dropped; a way entirely outside gives `[]`; MultiPolygon features give all parts.
- [ ] **Step 2: Run** `pytest tests/test_streets_normalize.py tests/test_streets_geo.py -v` → FAIL.
- [ ] **Step 3: Implement.** `street_key` order: NFKD + drop combining marks → casefold → strip → drop leading `portion(s) of|part of|along` → punctuation `.,-'` to spaces → word map (spec §4.3) word by word → collapse spaces. Keep the word map as a module dict so dev tuning (Task 5) edits one place.
- [ ] **Step 4: Run** the two test files → PASS; `ruff check`.
- [ ] **Step 5: Commit** `feat(streets): street_key normalisation and pure geometry`

### Task 3: Overpass client and `streets fetch` (STOP before the real fetch)

**Files:**
- Create: `src/outages/osm/__init__.py`, `src/outages/osm/overpass.py`, `tests/fixtures/osm/` (hand-made responses: `ok_small.json`, `remark_timeout.json`, `empty.json`)
- Modify: `src/outages/__main__.py` (`streets` command: `fetch` action, `--yes`), `.gitignore` (confirm `data/` covers `data/osm/`)
- Test: `tests/test_overpass.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `boundaries.geojson` via `map.build.load_boundaries()`; `geo.polygons_of`.
- Produces: `town_bbox(boundaries: dict, town_psgc: str, pad_m: float = BBOX_PAD_M) -> tuple[float, float, float, float]` (S, W, N, E for Overpass); `query_for(bbox) -> str` (spec §4.2 text); `make_client() -> httpx.Client` (User-Agent, timeout 120 s); `class FetchError(Exception)`; `fetch_town(client, town_psgc, bbox, sleep=time.sleep) -> dict` (returns the slimmed `{"ways": [{"id", "tags", "geometry": [[lon, lat], …]}], "osm_base": str}`; retries 429/504 with `OVERPASS_BACKOFF_S`; raises `FetchError` on other status, bad JSON, a `remark` containing `runtime error`, or 0 ways); `fetch_all(client, boundaries, out_dir: Path, now: datetime, sleep=time.sleep) -> FetchSummary(ok: list[str], failed: dict[str, str])` (towns in `VECO_TOWNS` order, `OVERPASS_SPACING_S` between requests, write `streets-<psgc>.json.tmp` then `os.replace`, then update `manifest.json`: per town `fetched_at`, `query`, `sha256`, `ways`; top-level `osm_base`).
- CLI: `streets fetch` without `--yes` prints the 8 towns and bboxes and returns 2 without building a client.

- [ ] **Step 1: Write failing tests** (`httpx.MockTransport`, injected `sleep` recording calls): `test_query_shape` (contains `way["highway"]["name"]` and the bbox, `out tags geom`); `test_town_bbox_padded` (bbox wider than the polygons by ≈ 500 m each side); `test_fetch_all_writes_files_and_manifest` (8 files, manifest has 8 towns with `sha256` of each file, `osm_base`); `test_spacing_between_requests` (sleep called with ≥ 10 between towns); `test_user_agent_sent`; `test_429_then_ok_retries_with_backoff` (sleeps 30); `test_504_twice_then_fail` (town in `failed`, others still fetched); `test_remark_is_failure_keeps_old_file` (pre-existing file unchanged); `test_zero_ways_is_failure`; `test_bad_json_is_failure`; CLI: `test_streets_fetch_without_yes_builds_no_client` (`make_client` patched to raise; exit 2; output lists 8 towns).
- [ ] **Step 2: Run** `pytest tests/test_overpass.py tests/test_cli.py -k "streets or overpass" -v` → FAIL.
- [ ] **Step 3: Implement** `osm/overpass.py` and the `streets` subcommand (`action` choices `fetch`, `match`; `--yes`, `--rebuild`, `--dry-run`, `--review`); `streets` is dispatched explicitly in `_run` before the fall-through guard.
- [ ] **Step 4: Run** → PASS; `pytest -q && ruff check`.
- [ ] **Step 5: Commit** `feat(osm): polite Overpass extract per town with manifest`
- [ ] **Step 6: STOP — ask the owner** for the 8 Overpass requests, showing the printed plan. On OK: `python -m outages streets fetch --yes`; report ways per town, file sizes, `osm_base`. Not committed (`data/`).

### Task 4: Street eval sample, labels, freeze (STOP for review)

**Files:**
- Create: `src/outages/streets/evaluate.py`, `evals/streets/GUIDE.md`, `evals/streets/dev.jsonl`, `evals/streets/test.jsonl`, `evals/streets/FROZEN`, `evals/streets/CHANGES.md`
- Modify: `src/outages/__main__.py` (`eval --suite areas|streets`, default `areas`)
- Test: `tests/test_eval_streets.py`

**Interfaces:**
- Consumes: `evaluate.write_split`, `load_split`, `freeze`, `check_frozen` (reuse; don't copy).
- Produces: `street_rows(conn) -> list[StreetRow]` (`areas_hash, seq, name, areas_raw, towns, barangays: list[psgc], scope_kind: "one"|"several"|"none", town_psgc` = first placed barangay's town, else first listed town); `sample_streets(rows, seed: int = 20261112) -> tuple[list[dict], list[dict]]` (distinct `street_key`; 10 per VECO town; within a town, scope kinds proportional; 3 dev + 7 test per town, ids `dev-01…`, `test-01…`); label row shape `{"id","areas_hash","seq","name","areas_raw","towns","barangays","gold": str | null}`; `score(rows, matches: dict[(hash, seq), Match]) -> {"precision","coverage","drawn","correct","by_status"}` (spec §5.3, comparing `street_key`s).

- [ ] **Step 1: Write failing tests** — `test_sample_deterministic` (same seed → same ids); `test_sample_stratified` (10 per town, 30/50 split, distinct keys); `test_score_precision_coverage` (hand-built: 4 drawn, 3 correct, 5 non-null gold → precision 0.75, coverage 0.6); `test_null_gold_drawn_counts_against_precision`; `test_eval_suite_default_is_areas` (CLI parse); `test_eval_streets_offline` (fetcher/overpass/telegram factories raise).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; commit `feat(eval): street eval sampler and scorer`.
- [ ] **Step 5: Sample on the real DB** (`connect_ro`): `python -m outages eval sample --suite streets`. Write `GUIDE.md` (spec §5.2 conventions). Label all 80 from `data/osm/` by searching the extract by hand (a small read-only helper script in the scratchpad that lists OSM names near the listed barangays; never the matcher, which doesn't exist yet). Commit `docs(eval): street labels for review`.
- [ ] **Step 6: STOP — owner reviews ≥ 15 labels.** Apply corrections; `freeze` test (`FROZEN` holds its hash); commit `docs(eval): freeze street test split`.

### Task 5: Extract index, matcher, review

**Files:**
- Create: `src/outages/osm/extract.py`, `src/outages/streets/match.py`, `src/outages/streets/review.py`, `src/outages/areas/street_aliases.csv` (header `town_psgc,veco_key,osm_name`)
- Test: `tests/test_streets_match.py`, `tests/fixtures/osm/world/` (a manifest + one town file over `tests/map_world.py`'s squares)

**Interfaces:**
- Produces: `class StreetIndex` with `load(osm_dir: Path) -> StreetIndex | None` (None when no manifest), `osm_base: str`, `ways_named(town_psgc: str, key: str) -> list[Way]`, `names_in(town_psgcs, scope) -> list[str]`; `Way(id: int, names: dict[str, str], coords: list[Point], town_psgc: str)`.
- Produces: `Match(status: str, osm_name: str | None, lines: list[list[Point]])`; `load_aliases(path=…) -> dict[tuple[str, str], str]`; `scope_for(barangay_psgcs: list[str], town_psgcs: list[str], boundaries: dict) -> Scope | None`; `match_street(name: str, scope: Scope | None, towns: list[str], index: StreetIndex | None, aliases) -> Match` (spec §4.5 steps 1–6; status values `matched`, `alias`, `not_found`, `no_osm_data`).
- Produces: `review(conn, index, boundaries) -> list[ReviewItem(name, count, suggestions: list[str])]` (`difflib.get_close_matches(key, names, n=3, cutoff=0.75)`).

- [ ] **Step 1: Write failing tests** on the fixture world: `test_long_road_clipped_to_listed_barangays` (a way crossing three squares, outage lists two → lines only inside those two plus buffer); `test_alias_applies_town_scoped` (alias in town A doesn't apply in town B); `test_typo_not_found` (`"Gorordo Avnue"` → `not_found`); `test_no_barangay_uses_towns` (scope from town polygons); `test_no_index_is_no_osm_data`; `test_generic_names_never_drawn` (OSM has a way named "Barangay Road" in scope; VECO "Brgy. Road" → `not_found`; with an alias → `alias`); `test_any_name_tag_matches` (`old_name`); `test_way_with_one_node_inside_not_matched`; `test_osm_name_most_common`; `test_review_suggests_close_names`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Wire `eval run --suite streets --split dev|test`** in `__main__.py`: `connect_ro`, `StreetIndex.load`, `match_street` per labelled row in memory (no store), `score`, write `evals/streets/results/<date>-<split>.md`; `--split test` calls `check_frozen` first. CLI test: offline.
- [ ] **Step 6: Tune on dev only.** `python -m outages eval run --suite streets --split dev`; adjust the word map and add aliases from the dev failures and `review`; write `evals/streets/results/<date>-dev.md`. Never open `test.jsonl`.
- [ ] **Step 7: Commit** `feat(streets): rules matcher with aliases and review queue`

### Task 6: Store, `streets match`, eval run, gate (STOP before real writes)

**Files:**
- Create: `src/outages/streets/store.py`
- Modify: `src/outages/__main__.py` (`streets match [--rebuild] [--dry-run]`, `streets --review`), `src/outages/pipeline.py` (`areas_step` then `streets_step`), `src/outages/config.py` (`STREETS_DRAW` by the gate), `src/outages/report.py` (street match counts by status)
- Test: `tests/test_streets_store.py`, `tests/test_pipeline.py`, `tests/test_cli.py`, `tests/test_report.py`

**Interfaces:**
- Consumes: Task 5's `StreetIndex.load`, `match_street`, `scope_for`, `load_aliases`.
- Produces: `MatchSummary(written: int, by_status: dict[str, int])`; `run_match(conn, index: StreetIndex | None, boundaries: dict, rebuild: bool = False, dry_run: bool = False) -> MatchSummary` (spec §4.5 last paragraph; rows deleted for hashes no longer in `area_entities`; transaction per 500 hashes; `osm_date = index.osm_base`); `streets_step(conn, data_dir) -> None` in `pipeline.py` (only when `data/osm/manifest.json` exists; failures logged, never fail the check, as `areas_step`).

- [ ] **Step 1: Write failing tests** — `test_run_match_writes_rows_and_geometry` (5-decimal coordinates); `test_idempotent_second_run_writes_none`; `test_rebuild_rewrites_all`; `test_dry_run_writes_nothing` (DB hash unchanged); `test_orphan_rows_deleted`; `test_check_runs_streets_after_areas_only_with_extract`; `test_streets_step_failure_does_not_fail_check`; `test_streets_match_and_review_offline` (CLI); `test_report_street_counts`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; `pytest -q && ruff check`; commit `feat(streets): street_matches store and match command`.
- [ ] **Step 5: Test split, once.** `check_frozen`, then `python -m outages eval run --suite streets --split test` (in memory, `connect_ro`); write `evals/streets/results/<date>-test.md` with precision, coverage, drawn count, by-status. Apply §5.4: `STREETS_DRAW = precision >= 0.95 and drawn >= 20`. Commit `docs(eval): street test results; set STREETS_DRAW`.
- [ ] **Step 6: STOP — ask the owner** before touching the real DB: backup `data/outages.db.bak-<date>`, `streets match --dry-run` (counts), then on OK `python -m outages streets match` (applies migration 004). Report counts by status and the backup path.

### Task 7: Streets in the map document

**Files:**
- Modify: `src/outages/map/build.py`, `tests/map_world.py` (street entities + `street_matches` rows), `tests/golden/map/*.geojson` (regenerated, reviewed by diff)
- Create: `tests/golden/map/drawing_off.geojson`
- Test: `tests/test_map_build.py`, `tests/test_map_app.py`

**Interfaces:**
- Consumes: `street_matches` rows; `config.STREETS_DRAW`.
- Produces: `build(conn, gaz, boundaries, window, now, draw: bool = STREETS_DRAW) -> dict` with each outage's `streets` and `meta.osm` (spec §4.6). `osm.data_date` = the newest `osm_date` among rows in the window, else `null`. Order: `drawn` and `not_drawn` in entity `seq` order.

- [ ] **Step 1: Write failing tests** — `test_streets_drawn_and_not_drawn`; `test_drawing_off_moves_all_to_not_drawn` (golden `drawing_off.geojson`); `test_entity_without_match_row_is_not_processed`; `test_schema3_db_streets_no_osm_data` (a DB migrated only to 3); `test_meta_osm_and_attribution` (attribution gains the ODbL line only when something is drawn); `test_no_barangay_outage_with_lines_stays_unplaced`; `test_map_app_db_hash_unchanged` still passes.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** one more read-only query (`street_matches` for the window's hashes). **Step 4: Run** → PASS; regenerate goldens and read the diff before committing.
- [ ] **Step 5: Commit** `feat(map): street lines in the map document`

### Task 8: Streets and the week picker on the page

**Files:**
- Create: `src/outages/map/static/js/streets.js`, `tests/js/streets.test.mjs`
- Modify: `src/outages/map/static/js/map.js`, `src/outages/map/static/index.html`, `src/outages/map/static/map.css`, `src/outages/map/static/js/timeline.js` (export `weekStart(dateStr, deltaWeeks)` only if needed by the picker)

**Interfaces:**
- Consumes: `timeline.js` exports (parse, active-in-hour, on-day).
- Produces (`streets.js`, pure ES module): `lineState(outage, day, hour, showCancelled) -> "active" | "active_cancelled" | "faint" | "hidden"`; `drawnCount(outage) -> {drawn, total}`; `whyText(why) -> string` (the spec copy); `isPastWindow(meta, nowMs) -> boolean` (today not in `meta.days`).

- [ ] **Step 1: Write failing tests** (`node --test tests/js/streets.test.mjs`): active vs faint vs hidden at hour edges; overnight outage active at 23 on day 1 and 0 on day 2; cancelled hidden when the toggle is off; `whyText` for each `why`; `isPastWindow` under `TZ=America/New_York` at 00:30 Manila.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `streets.js`; then `map.js`: a line layer per drawn street (styles: bold orange / grey dashed / thin faint), "Show streets" toggle hidden when `meta.osm.drawing` is false, lower fill opacity on barangays with an active drawn line, line popup, "Streets drawn: k of n" + reasons in the barangay popup, "drawn by its streets only" + "show" zoom link in the side list; week picker (date input + ◀ ▶ → `?from=&days=7`, `history.replaceState`, refetch) and the "History: …" banner replacing the old-data warning when `isPastWindow`.
- [ ] **Step 4: Run** `node --test "tests/js/*.test.mjs"` under both TZs (PowerShell) → PASS.
- [ ] **Step 5: Check in the browser** with `python -m outages map` on a fixture-world DB (`tests/map_world.py` populate into a scratch DB): lines show, toggle, picker moves weeks. Screenshot.
- [ ] **Step 6: Commit** `feat(map): draw streets, week picker`

### Task 9: History stats, `/stats.json`, `stats` CLI

**Files:**
- Create: `src/outages/history/__init__.py`, `src/outages/history/load.py`, `src/outages/history/stats.py`, `tests/golden/history/default.json`
- Modify: `src/outages/map/app.py` (`/stats.json`, `/boundaries.geojson`), `src/outages/__main__.py` (`stats [--from] [--to]`)
- Test: `tests/test_history_stats.py`, `tests/test_map_app.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: `Range(from_date: date, to_date: date)` with `start`/`end` (Manila); `parse_range(from_: str | None, to: str | None, now: datetime) -> Range` (raises `WindowError` → 422); `OutageRow(id, start, end, status, stale: bool, placed: list[(psgc, ambiguous: bool)], left_out_reason: str | None)` — reason `other_town` / `unmatched` / `not_parsed` / `no_barangay` when nothing is placed (first reason by that order when several); `load_rows(conn, rng) -> tuple[list[OutageRow], Coverage]`; `compute(rows, rng, gaz, coverage, now) -> dict` (spec §4.8 document; hours rounded to 0.1); `union_hours(intervals: list[tuple[datetime, datetime]]) -> float`; `format_text(doc) -> str` for the CLI.

- [ ] **Step 1: Write failing tests** — `test_union_overlapping_intervals` (8–12 and 10–14 → 6 h); `test_range_clips_interval` (an outage from the day before the range counts only its part inside); `test_month_split_at_manila_midnight` (31 Oct 22:00 → 1 Nov 04:00 → 2 h Oct, 4 h Nov); `test_ambiguous_counted_each_town_with_ambiguous_hours`; `test_cancelled_removed_counted_not_in_hours`; `test_stale_counted_and_flagged`; `test_left_out_reasons`; `test_months_zero_filled`; `test_top_ten_tie_by_name`; `test_median_and_mean_duration`; golden `default.json` on `map_world` at an injected `now`; app: `/stats.json` 200, bad dates 422, `from > to` 422, schema-3 DB 503, `/boundaries.geojson` 200, DB hash unchanged; CLI `stats` offline.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; `pytest -q && ruff check`.
- [ ] **Step 5: Commit** `feat(history): union-hours stats, /stats.json and stats command`

### Task 10: `/history` page

**Files:**
- Create: `src/outages/map/static/history.html`, `src/outages/map/static/js/bins.js`, `src/outages/map/static/js/history.js`, `tests/js/bins.test.mjs`
- Modify: `src/outages/map/app.py` (`/history` → `history.html`), `src/outages/map/static/index.html` (link to History), `map.css`

**Interfaces:**
- Produces (`bins.js`): `quantileEdges(values: number[], n = 5) -> number[]` (edges over non-zero values, deduplicated, ascending); `binOf(value, edges) -> 0..n` (0 = zero hours); `legendLabels(edges) -> string[]` (e.g. `"1–8 h"`); `monthBars(months, width, height) -> [{x, y, w, h, label}]` (pure SVG geometry).

- [ ] **Step 1: Write failing tests** — edges for 1..100, ties (all equal → one bin), all-zero → `[]`, a value above the top edge, legend labels, bar geometry sums to width.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `bins.js`; then `history.js` + `history.html`: banner and coverage, from/to inputs (default from the document's `range`), choropleth from `/boundaries.geojson` + `/stats.json`, MECO grey, zero outline only, barangay click panel with "see that week on the map" (`/?from=<Monday of last>&days=7`), tables, inline SVG month bars with the worst 3 named; `textContent` only.
- [ ] **Step 4: Run** node tests under both TZs → PASS; `test_map_app` covers `/history` 200.
- [ ] **Step 5: Check in the browser** on the fixture DB; screenshot. **Step 6: Commit** `feat(history): heatmap and stats page`

### Task 11: Multi-user bot (STOP before the live test)

**Files:**
- Modify: `src/outages/alerts/store.py`, `src/outages/alerts/select.py` (`status = 'approved'`), `src/outages/alerts/run.py` (send spacing; skip recording for a vanished subscriber), `src/outages/bot.py`, `src/outages/__main__.py` (`_bot`/`serve` call `sync_allowlist` on start), `src/outages/report.py` (pending/approved/blocked counts)
- Test: `tests/test_bot.py`, `tests/test_alerts_store.py`, `tests/test_alerts_run.py`, `tests/test_alerts_select.py`

**Interfaces:**
- Produces (`store.py`): `Subscriber` gains `status: str`; `request_join(conn, chat_id, first_name, now) -> bool` (True when a new pending row was created or a forgotten chat re-requests; False when already pending/approved/blocked); `decide(conn, chat_id, approve: bool, now) -> Subscriber | None` (idempotent; None when no row); `pending(conn) -> list[Subscriber]`; `pending_count(conn) -> int`; `users(conn) -> list[tuple[Subscriber, int]]`; `forget(conn, chat_id) -> bool` (one transaction: `alerts_sent`, `subscriptions`, `admin_notes_sent` keys containing the chat ID, `subscribers`); `revoke(conn, chat_id, now) -> bool`; `sync_allowlist(conn, allowed: frozenset[int]) -> int`; `subscription_count(conn, subscriber_id) -> int`.
- Produces (`bot.py`): `handle(update, conn, gaz, allowed, now, admin_chat_id: int | None, limiter: RateLimiter) -> list[Reply]`; `class RateLimiter(per_min=RATE_LIMIT_PER_MIN, mute_s=RATE_LIMIT_MUTE_S)` with `allow(chat_id, now) -> bool`; admin replies carry `reply_markup` with `ap:<id>`/`dn:<id>` buttons. Pending-reply and cap notes use `admin_notes_sent` keys `pending_reply:<chat_id>:<YYYY-MM-DD>` and `pending_cap:<YYYY-MM-DD>`; group refusal `group:<chat_id>`.
- Admin-note keys that hold a chat ID end with `:<chat_id>` or contain `:<chat_id>:`; `forget` deletes `key LIKE '%:<id>' OR key LIKE '%:<id>:%'`. Phase 3's `stranger:<chat_id>` already fits; the stranger "private bot" path is replaced by the join flow (non-allowlisted private chats now `request_join`).

- [ ] **Step 1: Write failing tests** — store: `test_request_join_creates_pending`; `test_decide_idempotent`; `test_forget_leaves_no_row_with_chat_id` (scan every table's every column for the ID); `test_revoke_keeps_blocked_row_without_name`; `test_sync_allowlist_approves`; select: `test_only_approved_alerted` (pending and blocked skipped); run: `test_record_after_forget_is_skipped` (subscriber deleted between send and record → no exception, no rows); `test_send_spacing` (injected sleep ≥ 0.05 between messages). Bot: `test_stranger_start_pending_and_admin_request`; `test_repeat_start_while_pending_sends_one_request`; `test_approve_callback_from_admin_only`; `test_deny_blocks_and_replies_once`; `test_pending_reply_once_per_day`; `test_pending_cap`; `test_subscription_cap`; `test_rate_limit_mutes_ten_minutes`; `test_group_chat_refused_once`; `test_privacy_text`; `test_forget_command`; `test_admin_users_lists_no_barangays`; `test_admin_pending_lists_with_buttons`; `test_admin_commands_ignored_outside_admin_chat`; `test_admin_revoke`; `test_no_admin_chat_refuses_requests`; `test_allowlisted_chat_works_without_approval`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** Welcome and `/privacy` text: spec §4.9 points, plain and short. **Step 4: Run** → PASS; `pytest -q && ruff check`.
- [ ] **Step 5: Commit** `feat(bot): approve-to-join, admin commands, /privacy and /forget`
- [ ] **Step 6: STOP — ask the owner** for the live test (needs a second Telegram account): `python -m outages bot`; the second account `/start` → admin request → Approve → `/add <barangay>` → `/list` → `/forget`; then a read-only query shows no row with that chat ID. No token in logs.

### Task 12: Real-DB check, docs, ROADMAP, Phase 6 prompt

**Files:**
- Modify: `README.md` (streets: fetch/match/review, ODbL; history page; stats; joining and privacy; admin commands), `ROADMAP.md`
- Create: `prompts/phase6.md`

- [ ] **Step 1: Real DB, read-only** (`python -m outages map`; DB hash before/after): a known long road (Cebu South Road outage) drawn only inside its listed barangays; "Streets drawn: k of n" agrees with `street_matches`; record the default document's size in bytes; a past week via the picker; `/history` numbers equal `python -m outages stats` output for the same range; `left_out.count` + counted + cancelled + removed = outages in range (query). Desktop and 375 px screenshots.
- [ ] **Step 2: Whole-branch review** by one fresh reviewer; fix important findings test-first; list deferred minors.
- [ ] **Step 3: Docs.** README sections; ROADMAP progress entry (eval numbers, gate result, real-DB numbers), Phase 5 checkboxes, known issues (OSM coverage, ODbL for Phase 6), lessons; `prompts/phase6.md` in the shape of `prompts/phase5.md`.
- [ ] **Step 4: Run** `pytest -q && ruff check && node --test "tests/js/*.test.mjs"` → all pass.
- [ ] **Step 5: Commit** `docs: Phase 5 README, ROADMAP and Phase 6 prompt`
- [ ] **Step 6: STOP — ask the owner** before push and PR.
