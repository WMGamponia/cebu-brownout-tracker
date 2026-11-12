# Phase 5: Streets, history, multi-user — brainstorm

## Context
This session starts Phase 5 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` once the Phase 4 PR (`phase4-map`) is merged; otherwise branch from `phase4-map` and say so.
- **Branch:** `phase5-streets-history` (create it only after the spec is approved).
- **Remote:** private GitHub repo under `WMGamponia`. Check with `git remote -v`.

## Where things stand
- Phase 1: `backfill|check|reparse|report|serve` store every interruption post in `data/outages.db` (posts, versions, outages, `outage_changes`).
- Phase 2: `areas` parses each `areas_raw` into `area_parses(towns_json)`, `area_barangays(name_raw, town_psgc, psgc_code, match)` and `area_entities(kind, name)` (kinds: street, sitio_purok, subdivision, establishment, other). Gazetteer: 232 barangays in 8 VECO towns.
- Phase 3: Telegram alerts at 06:00 and 20:00 Manila, bot thread (`/start`, `/add`, `/list`, `/remove`), **allowlisted chats only**, admin notes.
- Phase 4: `python -m outages map` (and a thread in `serve`) serves a Leaflet map at `127.0.0.1:8765`. One GeoJSON document per request (`/outages.geojson?from=&days=`, 1–14 days): a feature per barangay from the committed `areas/boundaries.geojson` (232 VECO + 72 MECO), top-level `outages` (time, status, purpose, barangays, entities, unplaced reasons) and `meta`. Day tabs, hour slider, play, cancelled toggle, "Not on the map" list. Read-only DB; page JS is tested with `node --test` (`timeline.js`, `text.js`). The whole barangay is shaded for "portion of"; the popup lists the streets in words.

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/phase5-build.md` for the build session.

The Phase 5 goals (ROADMAP):
1. **Street highlighting** via Overpass inside the barangay polygon, falling back to barangay shading.
2. **History mode** (pick a past week) and a **range heatmap** of outage hours per barangay; **stats** (most-affected barangays, average duration, worst months).
3. **Multi-user subscriptions.**

## What Phases 2–4 learned that the design must handle
1. **Street names are hand-typed and partial.** `area_entities` holds VECO's spelling (`A. Seno Extension`, `Tiltilon Baranggay Road`, `portion of Cebu South Coastal Road`); OSM uses others. Matching needs normalisation, town/barangay scoping and an eval with frozen labels, like Phase 2's (set the margin after a dev baseline exists, Lessons).
2. **Overpass is a third-party service.** Like VECO: polite, cached, never from tests or CI, ask before the first request. Decide: fetch per street on demand, or one bounded extract per town cached in `data/` (and refreshed how often?). Licence: OSM data is ODbL; attribution and share-alike for a derived street file.
3. **Medium-resolution polygons are coarse** (~9 vertices per barangay); a street clipped to them may fall outside. Decide whether to clip at all.
4. **History is mostly there:** `?from=&days=` already serves any range ≤ 14 days, and the page passes its own query on. A heatmap/stats view needs aggregates over 3,000+ outages; decide SQL vs precomputed, and how cancelled, removed, stale-post and unplaced outages count (they must not silently vanish or inflate numbers).
5. **36 parsed outages name no barangay** (e.g. "Cebu Business Park", "South Reclamation Area"), shown as "VECO named places but no barangay". Streets may place some of them; stats must say how many were left out.
6. **Multi-user means strangers.** Today only allowlisted chats can use the bot. Opening it changes the threat model (spam, abuse, rate limits, data about who lives where) and Telegram's limits (~30 messages/s, 1/s per chat). Decide sign-up, limits, consent/privacy wording, and how to remove a user's data. Phase 6 adds auth for the web side; don't build it here unless needed.
7. **Times are Manila** (`+08:00`), never the browser's zone; overnight outages split across days; the 15 h old-data warning stays.
8. **Offline by default.** Every new command and endpoint must not reach VECO; the CLI dispatches explicitly and tests fail if an offline command builds the fetcher. The map must stay read-only on the DB.

## Questions the brainstorm must settle
- Overpass: which query (ways by name within a bbox/area), caching, refresh, failure fallback; a new dependency or plain httpx? Owner's OK before any request.
- Street matching: rules vs the local Qwen arm from Phase 2's eval; labels and metric; what "matched" must mean to draw a line rather than shade.
- The document: does `outages.geojson` grow street geometries, or a separate `streets.geojson` per window (size!)?
- History UI: a week picker on the existing page, or a separate stats page; heatmap metric (outage hours per barangay per range); which statuses count.
- Multi-user: who may subscribe, limits, abuse handling, and whether the admin approves new chats.
- Tests: golden street matches, Overpass responses as fixtures (no network), stats on a fixture DB, JS logic under `node --test`.

## Read first
1. `ROADMAP.md`: Status, the Phase 4 entries, Known issues, Lessons.
2. Phase 4 spec `docs/superpowers/specs/2026-11-12-phase4-map-design.md` (§4.3 the document, §4.6 the page, §12 accepted limits).
3. Phase 2 spec `docs/superpowers/specs/2026-11-11-phase2-areas-design.md` (§4.4 entities, the eval and its decision rule) and `evals/areas/`.
4. Phase 3 spec `docs/superpowers/specs/2026-11-11-phase3-alerts-design.md` (§5.9 allowlist, admin notes).
5. `python -m outages report` and the map on the real DB (`python -m outages map`, read-only).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data, sending any Telegram message, any Overpass request, and exposing a server beyond localhost.
- **Be gentle with visayanelectric.com and Overpass:** this session needs no fetches; use the stored data.
- **No new dependencies** without the owner's OK.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Backslash escapes in Bash heredocs can be mangled: write Python files with the Write tool. Git Bash doesn't pass `TZ` to node here; use PowerShell (`$env:TZ=...`) for the zone runs.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-phase5-streets-history-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-phase5-streets-history.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/phase5-build.md` written in this file's shape.
