# Phase 5 Streets, History, Multi-user — Design

Status: approved in brainstorming 2026-11-12, section by section. Base: `phase4-map` at `e1d73e3` (Phase 4 PR #4 still open; rebase onto `main` once it merges). Branch: `phase5-streets-history`.

## 1. Intent

Three additions to the tracker, each honest about what it can't know:
- **Streets:** draw the streets VECO names as lines on the map, clipped to the barangays VECO lists, on top of the Phase 4 "may be affected" shading. A street is drawn only when the match is measured to be precise.
- **History:** look at any past week on the map, and a `/history` page with a heatmap of scheduled outage hours per barangay and stats (most-affected barangays, durations, worst months), saying what was left out.
- **Multi-user:** people you approve can subscribe to alerts; they can delete their data; the bot says plainly that it runs on one person's PC.

Success:
- Every new command and endpoint works offline; only `streets fetch` contacts Overpass, and only when run by hand. Nothing contacts VECO except `check`/`backfill`/`serve` as before.
- No outage silently vanishes from the map or the stats: what is left out is counted with the reason.
- A wrong street line is worse than none: drawing is on only if the frozen test split meets the pre-registered precision bar (§5.4).
- A second Telegram account can join (with approval), subscribe, and `/forget` itself, leaving no row with its chat ID.

## 2. Decisions (owner, 2026-11-12)

| Topic | Decision |
|---|---|
| Multi-user | **Approve to join.** Anyone may `/start`; the admin approves each chat with a button. `TELEGRAM_ALLOWED_CHAT_IDS` stays as an always-approved list. Private chats only. |
| OSM source | **One extract, refreshed by hand:** `streets fetch` makes 8 Overpass requests (one per VECO town) and saves them under `data/osm/` (not committed: ODbL). Plain httpx, no new dependency. Never at serve or request time. |
| Display | **Lines on top of shading.** Barangays stay shaded (lighter where lines are drawn); matched streets are bold lines. Nothing is unshaded because some streets matched. |
| Matching | **Rules + town-scoped aliases;** fuzzy similarity only as review suggestions. Labelled eval (dev/test, test frozen); drawing on by default only if test precision ≥ 0.95. |
| History UI | **Week picker on the map** (`?from=&days=7`) **and a `/history` page** (heatmap + stats) backed by `/stats.json`; a `stats` CLI prints the same tables. |
| Metric | **Outage hours per barangay = union of the intervals** of counted outages placing that barangay, clipped to the range. |
| Dependencies | **None new.** Point-in-polygon and clipping in pure Python; charts as inline SVG; `difflib` for suggestions. |

## 3. Findings that shaped the design

From `data/outages.db` (read-only, 2026-11-12):
- **3,025 outages** (2,743 scheduled, 51 additional, 214 cancelled, 17 removed), 23 Jan 2024 → 14 Nov 2026; mean scheduled duration 6.6 h. Monthly counts 9 (Jan 2024, partial) to 120.
- **Streets:** 5,032 `street` entities, 2,743 distinct as typed, **1,899 after normalisation**; 1,050 occur once. 2,399 of 3,008 non-removed outages name at least one street.
- **Long roads dominate:** `Cebu South Road` 141, `Cebu North Road` 52, `Central Nautical Highway` 49, `Gorordo Avenue` 43. Drawing a whole OSM road would light up 30+ km, so lines must be clipped to the listed barangays.
- **Scope is often several barangays:** only 2,342 of 5,032 street mentions come from an outage with one placed barangay; 44 from outages with none.
- **36 outages name no barangay** (e.g. "Cebu Business Park"); their `towns_json` usually names a town.
- **Subscribers:** 1 (the owner), 3 subscriptions.

## 4. Components

```
src/outages/
  migrations/004_streets_users.sql
  osm/
    __init__.py
    overpass.py     # client: query per town, polite, atomic write, manifest
    extract.py      # load data/osm/ → StreetIndex (ways by normalised name, per town)
  streets/
    __init__.py
    normalize.py    # street_key(name) shared by VECO and OSM names
    geo.py          # point-in-polygon, point-segment distance (metres), buffered scope, clip
    match.py        # (entity, scope, index, aliases) → Match; pure
    store.py        # street_matches read/write; run_match(conn, …)
    review.py       # unmatched + difflib suggestions
  aliases: areas/street_aliases.csv   # town_psgc, veco_key, osm_name (committed)
  history/
    __init__.py
    stats.py        # rows → Stats (pure): union hours, months, tops, left-out
    load.py         # read-only SQL → rows for a range
  map/
    build.py        # + streets per outage, meta.osm
    app.py          # + /history, /stats.json
    static/history.html, js/streets.js, js/bins.js, js/history.js
  bot.py            # + join flow, /privacy, /forget, admin commands
  alerts/store.py   # + approved-only selection, forget(), revoke()
evals/streets/      # GUIDE.md, dev.jsonl, test.jsonl, FROZEN, results/
tests/
  fixtures/osm/     # hand-made Overpass responses
  test_streets_normalize.py, test_streets_geo.py, test_streets_match.py,
  test_streets_store.py, test_overpass.py, test_history_stats.py,
  test_eval_streets.py, golden/map/*.geojson, golden/history/*.json
  js/streets.test.mjs, js/bins.test.mjs
```

### 4.1 Migration 004
```sql
CREATE TABLE street_matches (
    areas_hash    TEXT NOT NULL,
    seq           INTEGER NOT NULL,          -- area_entities.seq
    status        TEXT NOT NULL,             -- matched | alias | not_found | no_osm_data
    osm_name      TEXT,                      -- OSM name that matched
    geometry_json TEXT,                      -- MultiLineString coordinates, 5 decimals; NULL unless drawn
    osm_date      TEXT,                      -- manifest fetched_at of the extract used
    PRIMARY KEY (areas_hash, seq)
);
ALTER TABLE subscribers ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'; -- pending | approved | blocked
ALTER TABLE subscribers ADD COLUMN requested_at TEXT;
ALTER TABLE subscribers ADD COLUMN decided_at TEXT;
```
Existing subscribers become `approved` through the default. Schema version 4.

### 4.2 Overpass (`osm/overpass.py`, `streets fetch`)
- One request per VECO town (`VECO_TOWNS`), bbox = the town's barangay polygons from `boundaries.geojson`, padded 500 m:
  `[out:json][timeout:90];way["highway"]["name"](S,W,N,E);out tags geom;`
- Endpoint `OVERPASS_URL = "https://overpass-api.de/api/interpreter"` in `config.py`; the project's named User-Agent; one request at a time, ≥ 10 s apart; 429/504 → back off (30 s, 120 s), two retries, then stop with an error.
- Each town is written to `data/osm/streets-<town_psgc>.json.tmp` then renamed; a town that fails keeps its old file. A response without `elements`, or with 0 ways, is a failure.
- Kept per way: `id`, tags `name`, `alt_name`, `old_name`, `official_name`, `short_name`, `highway`, and `geometry` (lat/lon, 6 decimals).
- `data/osm/manifest.json`: per town `fetched_at` (UTC ISO), `query`, `sha256`, `ways`; plus `osm_base` from Overpass's `osm3s.timestamp_osm_base`.
- The command refuses without `--yes` and prints the 8 requests it will make. Only this command builds an Overpass client.

### 4.3 Normalisation (`streets/normalize.py`)
`street_key(s)`: NFKD and drop combining marks; casefold; drop a leading `portion(s) of`, `part of`, `along`; drop a trailing `.`; word map `st→street`, `str→street`, `ave→avenue`, `av→avenue`, `rd→road`, `brgy→barangay`, `baranggay→barangay`, `bgy→barangay`, `ext→extension`, `hwy→highway`, `gen→general`, `gov→governor`, `sto→santo`, `sta→santa`; punctuation (`.`,`,`,`-`,`'`) → space; collapse whitespace. Applied to VECO names and to every OSM name tag. The exact table is fixed with dev examples only.

### 4.4 Geometry (`streets/geo.py`)
Pure Python, metres via an equirectangular projection at 10.3° N (error < 0.1 % at this scale).
- `Scope(polygons, buffer_m=150)`: a point is inside if inside any polygon or within 150 m of any polygon edge. Bboxes pre-checked.
- `clip(way_coords, scope) -> list[list[(lon,lat)]]`: keep maximal runs of consecutive nodes inside the scope; a run of one node is dropped; a way leaving and re-entering gives two runs. Segments are not cut at the boundary (nodes are dense enough; the buffer absorbs the rest).

### 4.5 Matching (`streets/match.py`, `streets/store.py`)
For each `area_entities` row with `kind = 'street'`:
1. **Scope:** polygons of the outage parse's placed barangays (`exact`, `alias`, `ambiguous`); if none, the polygons of every barangay of the towns in `towns_json`; if none, status `not_found` with no geometry.
2. **Extract missing** (no `data/osm/manifest.json`) → `no_osm_data`.
3. **Key:** `street_key(name)`; if `street_aliases.csv` has (town, key) for any town in scope, use its `osm_name`'s key and status `alias`.
4. **Candidates:** OSM ways in the scope's towns whose any-name key equals the key, and that have ≥ 2 nodes inside the scope.
5. **Result:** candidates → `matched` (or `alias`), `osm_name` = the most common `name` among them, geometry = all their clipped runs; none → `not_found`.
6. **Generic names are never drawn:** a key in `GENERIC_STREET_KEYS` (`road`, `main road`, `barangay road`, `national road`, `national highway`, `highway`, `provincial road`, `city road`, `access road`, `diversion road`) is `not_found` without lookup, even if OSM has a way of that name in scope (it would usually be a different road). An alias can still map one.

`run_match(conn, index, gaz, rebuild)` writes rows for every `areas_hash` with street entities lacking a row (or all with `--rebuild`), one transaction per 500 hashes; `osm_date` = manifest `osm_base`. `check`/`reparse` call it after `areas` when an extract exists. `--dry-run` prints counts and writes nothing.

**Review** (`streets --review`): `not_found` names by frequency, each with up to 3 OSM names in scope by `difflib.get_close_matches(cutoff=0.75)`. Suggestions are never applied automatically; an accepted one goes into `street_aliases.csv` by hand.

### 4.6 The map document
Each outage in `outages` gains:
```jsonc
"streets": {
  "drawn":     [{"name": "Cebu South Road.", "osm_name": "Cebu South Road",
                 "lines": [[[123.84, 10.26], …], …]}],
  "not_drawn": [{"name": "Tiltilon Baranggay Road", "why": "not_found"}]   // not_found | no_osm_data | not_processed | drawing_off
}
```
`meta.osm = {"data_date": "2026-11-…" | null, "drawing": true | false}`; `meta.attribution` adds "Streets © OpenStreetMap contributors (ODbL)" when any line is drawn. `drawing` follows `config.STREETS_DRAW` (set by the gate, §5.4). Drawing off → every street in `not_drawn` with `why: "drawing_off"`. A street entity with no `street_matches` row (match not run since the areas step) → `why: "not_processed"` (page: "street matching not run yet"). Schema 3 DB (no `street_matches`) → every street `no_osm_data`; the map still works. Coordinates rounded to 5 decimals. The real-DB check records the default document's size.

### 4.7 The map page
- **Lines** for the selected day/hour (logic in pure `streets.js`): active scheduled/additional → bold orange; active cancelled (shown) → grey dashed; same day, other hour → thin faint; other days → not drawn. Toggle "Show streets", on by default (hidden when `meta.osm.drawing` is false).
- **Shading:** a barangay containing an active outage's drawn line keeps its fill at lower opacity. A barangay is never unshaded.
- **Popups:** a line → street name ("OSM: …" when different) and the outage's usual details; a barangay → adds "Streets drawn: 3 of 5" and the others with the reason ("not found on OpenStreetMap near these barangays").
- **No-barangay outages with lines** stay in "Not on the map", with "drawn by its streets only" and a "show" link that zooms to the lines.
- **Week picker:** "Week of" date input + ◀ ▶; sets `?from=<date>&days=7` with `history.replaceState` and refetches. When the window doesn't contain today, a "History: 3–9 Nov 2025" banner replaces the old-data warning.

### 4.8 History (`history/`, `/stats.json`, `/history`, `stats`)
`/stats.json?from=YYYY-MM-DD&to=YYYY-MM-DD`: inclusive Manila dates; default to = today, from = to − 364 days; `from ≤ to`, both valid dates, else 422. Read-only, computed per request.

**Counting:**

| Outages | Counted in hours | Reported as |
|---|---|---|
| scheduled, additional | yes | — |
| from a failed-parse (stale) post | yes | `stale_count` |
| `ambiguous` rows | yes, for each listed town's barangay | per barangay `ambiguous_hours` |
| cancelled | no | `cancelled_count` |
| removed | no | `removed_count` |
| other_town, unmatched, not_parsed, no barangay | no | `left_out: {count, hours, by_reason}` |

An outage counts toward the range by the part of its interval inside `[from 00:00, to+1 00:00)` Manila. A barangay's hours = length of the union of its counted intervals. Months: hours split at Manila month starts.

**Document:**
```jsonc
{
  "range": {"from": "…", "to": "…"},
  "coverage": {"first_outage": "2024-01-23", "posts": 151, "posts_parsed": 150},
  "barangays": {"730600022": {"hours": 41.5, "outages": 7, "ambiguous_hours": 0,
                               "last": {"start": "…", "id": 4012}}},   // only barangays with hours > 0
  "top": [{"psgc": "…", "name": "…", "town": "…", "hours": …, "outages": …}],   // 10, by hours then name
  "duration": {"mean_h": 6.6, "median_h": 8.0, "n": …},   // counted outages, full scheduled length
  "months": [{"month": "2025-10", "hours": …, "outages": …}],   // every month in range, zero-filled
  "worst_months": ["2025-10", …],                               // 3, by hours
  "towns": [{"town": "Cebu City", "hours": …, "outages": …}],
  "counts": {"counted": …, "cancelled": …, "removed": …, "stale": …},
  "left_out": {"count": …, "hours": …, "by_reason": {"other_town": …, "unmatched": …, "not_parsed": …, "no_barangay": …}},
  "generated_at": "…+08:00"
}
```
"Hours" in `towns`/`months` are outage-hours summed over outages (an outage's own duration in range, once), not barangay-hours.

**Page `/history`:** banner "These are VECO's scheduled interruptions, not measured outages" and coverage ("Posts from 23 Jan 2024; 150 of 151 posts read"); from/to inputs (default last 12 months); choropleth with 5 sequential bins whose edges are quantiles of non-zero barangays (`bins.js`, pure), legend with real hour values, MECO grey, zero outline only; barangay click → hours, outages, ambiguous hours, latest outage, "see that week on the map" link; tables: top 10, duration, towns, counts and left-out with reasons; months as an inline SVG bar chart with the worst 3 named. Same safety rules as the map (`textContent`, checked links). Uses `boundaries.geojson` served at `/boundaries.geojson` (static, cacheable).

**CLI `stats [--from --to]`:** prints the same numbers as text; read-only.

### 4.9 Multi-user bot
- **Statuses:** `pending`, `approved`, `blocked`. Chats in `TELEGRAM_ALLOWED_CHAT_IDS` are treated as approved by the bot, and the bot writes `status = 'approved'` for any of their existing rows when it starts (`store.sync_allowlist`), so alert runs select only `active = 1 AND status = 'approved'` from the DB.
- **Private chats only:** a non-private chat gets one "This bot works in private chats only." (once per chat, in `admin_notes_sent`), then silence.
- **`/start` from an unknown chat:** create `pending` with `requested_at` and `display_name` = first name; reply with the welcome (runs on one person's computer, alerts can be late or missed; stores chat ID, first name, barangays; `/privacy`, `/forget`); send the admin chat "Join request: <first name> (@username, chat <id>)" with Approve / Deny buttons (`ap:<chat_id>`, `dn:<chat_id>`).
- **Callbacks `ap:`/`dn:`** are honoured only from `TELEGRAM_ADMIN_CHAT_ID`. Approve → `approved`, `decided_at`, user told "You're in. Try /add <barangay>." Deny → `blocked`, `display_name` cleared, one "Not approved." reply.
- **Pending:** any message → "Waiting for approval", at most once per 24 h per chat. **Blocked:** silence.
- **Limits:** ≤ 20 pending (above: "Not taking new requests right now", admin note once per day); ≤ 10 subscriptions per user (`/add` refuses with the reason); > 20 updates/min from one chat → ignored for 10 min (in-memory, logged once).
- **Sending:** sequential, ≥ 50 ms between messages; 429 honours `retry_after` (existing `retry` mapping).
- **Admin commands** (admin chat only): `/pending` (with buttons), `/users` (first name, status, number of subscriptions; no barangays), `/revoke <chat_id>` (delete subscriptions, `alerts_sent`, clear name, set `blocked`).
- **`/privacy`:** what is stored, why, how to delete. **`/forget`:** one transaction deletes the user's `subscriptions`, `alerts_sent`, `admin_notes_sent` keyed by their chat ID, and the `subscribers` row; replies "Deleted. /start to ask again." Allowlisted chats can forget too.
- Logs keep chat IDs only, never message text.

## 5. Eval (`evals/streets/`)
### 5.1 Sample
`eval sample --suite streets --seed 20261112` draws **80 street mentions** (distinct `(areas_hash, seq)`, distinct keys) **before any match rule exists**, stratified by town (10 per VECO town) and by scope (single placed barangay vs several vs none, proportional). 30 dev, 50 test.

### 5.2 Labels
Each row: the VECO name, `areas_raw`, the outage's barangays/towns, and `gold`: the OSM `name` of the way(s) VECO means, inside or near the listed barangays, or `null` (not in OSM). Labelled by Claude against the fetched extract, looking up candidates by hand (not with the matcher); `GUIDE.md` fixes the conventions (a road with different OSM names along its length takes the one inside the scope; `Barangay Road` names are `null` unless OSM has that name). The owner reviews a sample of ≥ 15; then test is frozen by hash (`FROZEN`), never opened during rule work.

### 5.3 Metrics
Per arm (rules only): **precision** = drawn rows whose `osm_name` key equals the gold key / drawn rows; **coverage** = correctly drawn / rows with non-null gold; counts by status. Results to `evals/streets/results/<date>-{dev,test}.md`.

### 5.4 Pre-registered gate
`STREETS_DRAW = True` in `config.py` only if **test precision ≥ 0.95** (with ≥ 20 drawn test rows; fewer → off, too little evidence). Otherwise `False`, the numbers go in the ROADMAP, and the map behaves as Phase 4 plus "drawing_off". Coverage has no bar. The test split is run once, after the rules are frozen on dev.

## 6. CLI

| Command | Network | Does |
|---|---|---|
| `streets fetch --yes` | **Overpass, 8 requests** | §4.2. Without `--yes`: prints the plan, exits 2. |
| `streets match [--rebuild] [--dry-run]` | none | §4.5. |
| `streets --review` | none | §4.5. |
| `stats [--from] [--to]` | none | §4.8; read-only. |
| `eval sample\|run --suite streets [--split dev\|test]` | none | §5. `--suite` defaults to `areas`, so Phase 2's commands are unchanged. |
| `map`, `serve`, `bot` | as before | + `/history`, `/stats.json`, `/boundaries.geojson`; join flow; admin commands. |

Dispatch stays explicit; every offline command's CLI test fails if it builds the VECO fetcher, the Overpass client or a Telegram client.

## 7. Error handling

| Situation | Behaviour |
|---|---|
| Overpass 429/504 | Back off 30 s / 120 s, then stop with an error; files fetched so far are kept. |
| Overpass bad JSON / no ways | That town fails; its old file stays. |
| No extract | `match` writes `no_osm_data`; map says "street data not downloaded". |
| DB schema 3 | Map without streets; `/history` and `/stats.json` → 503 "run migrations"; bot keeps the allowlist only. |
| Bad `/stats.json` range | 422 with the reason. |
| Admin chat not set | Join requests can't be approved: `/start` replies "not taking requests" and logs a WARNING. |
| Callback from a non-admin chat | Ignored, logged. |
| Telegram 403 on a pending/approved user | `active = 0`, as Phase 3. |

## 8. Testing
No network in CI; the suite-wide block stays.
- **`test_overpass`** (`MockTransport`): query per town, User-Agent, ≥ 10 s spacing (injected sleep), 429/504 retries, atomic write, failed town keeps the old file, manifest.
- **`test_streets_normalize`:** a table of VECO and OSM spellings.
- **`test_streets_geo`:** inside/outside, within the buffer, a way leaving and re-entering, single-node runs dropped, distance accuracy against a known pair.
- **`test_streets_match` / `_store`:** a fixture world (`tests/fixtures/osm/`, three barangays): Cebu South Road clipped to two barangays, an alias, a typo left `not_found`, a no-barangay outage placed by town, no extract → `no_osm_data`, idempotent re-run, `--rebuild`, `--dry-run` writes nothing.
- **`test_history_stats`:** overlapping intervals (union), a month boundary, an outage crossing the range edge, ambiguous, cancelled, removed, stale, each left-out reason; golden `tests/golden/history/default.json`.
- **`test_map_build` / `_app`:** goldens with `streets` (drawing on and off), schema-3 fallback, `/stats.json` 200/422/503, `/history`, DB hash unchanged.
- **`test_bot` / `test_alerts_store`:** join → admin buttons → approve/deny; callbacks only from admin; pending cap; once-per-24h pending reply; rate limit; 10-subscription cap; groups refused; `/forget` leaves no row with the chat ID (checked over every table); `/revoke`; alert runs skip pending/blocked; migration keeps the existing subscriber approved.
- **`test_eval_streets`:** sampler determinism, scorer.
- **JS:** `streets.test.mjs` (active/faint/hidden by hour, overnight), `bins.test.mjs` (quantile edges, ties, all-zero); under `TZ=America/New_York` and `Asia/Tokyo`.

## 9. Build order (for the plan)
1. Migration 004, config (`OVERPASS_URL`, `STREETS_DRAW`, limits).
2. `streets/normalize.py`, `streets/geo.py` (TDD).
3. `osm/overpass.py` + `streets fetch` with mocks. **Stop: owner's OK for the 8 requests.**
4. `eval streets sample`, labels, owner review, freeze.
5. `osm/extract.py`, `streets/match.py`, store, `match`/`--review`; tune on dev; aliases.
6. Test split once; apply the gate; record results. **Stop: owner's OK before `streets match` on the real DB (backup first).**
7. Map document + `streets.js` + page; week picker.
8. `history/` + `/stats.json` + `stats`.
9. `/history` page + `bins.js`.
10. Bot join flow, admin commands, `/privacy`, `/forget`. **Stop: owner's OK for the live Telegram test.**
11. Real-DB check in a browser (read-only), README, ROADMAP, Phase 6 prompt.

## 10. Definition of done
- §8 passes on Ubuntu and Windows and in the Node job; ruff clean.
- On the real DB: streets drawn (or off by the gate, numbers recorded); a known long road appears only inside its listed barangays; `/history` numbers equal `stats` output; left-out counts add up to the outages in range.
- Live: a second Telegram account goes through join → approve → `/add` → `/forget`, and the DB holds no row with its chat ID afterwards.

## 11. Gates (ask the owner first)
Overpass fetch; label review and freeze; migration and `streets match` on the real DB (backup first); any Telegram message; LAN exposure; push, PR, merge.

## 12. Out of scope
Streets in history; drawing sitios, subdivisions or establishments; per-street alerts; automatic OSM refresh; web auth or sign-up (Phase 6); group chats; per-user alert times; a Qwen arm for street matching.

## 13. Risks and accepted limits
- **OSM coverage of Cebu side streets is uneven;** coverage is reported, not promised.
- **Coarse polygons:** the 150 m buffer can let a line run slightly into a neighbouring barangay; accepted for a "may be affected" map.
- **Stats are VECO's plans, not measured outages,** and January 2024 is partial.
- **Join requests depend on the admin answering;** a pending user gets nothing until then.
- **ODbL:** the extract and derived lines stay local. A public Phase 6 snapshot that includes street lines must carry the ODbL notice and offer the derived street data under ODbL; re-check before it goes live.
- **Reliability:** strangers depend on a home PC; the welcome text says so.
