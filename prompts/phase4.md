# Phase 4: Map — brainstorm

## Context
This session starts Phase 4 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` once the Phase 3 PR (`phase3-alerts`) is merged; otherwise branch from `phase3-alerts` and say so.
- **Branch:** `phase4-map` (create it only after the spec is approved).
- **Remote:** private GitHub repo under `WMGamponia`. Check with `git remote -v`.

## Where things stand
- Phase 1: `python -m outages backfill|check|reparse|report|serve` stores every interruption post in `data/outages.db` with outages, versions and a change log (`outage_changes`).
- Phase 2: `python -m outages areas` parses each distinct `areas_raw` (keyed by `outages.areas_hash`) into `area_parses`, `area_barangays(name_raw, town_psgc, psgc_code, match)` and `area_entities(kind, name)`. The gazetteer (`src/outages/areas/barangays.csv`, 232 barangays in the 8 VECO towns) comes from the pinned `philippines-json-maps` v1.1.1 release; `python -m outages gazetteer` downloads that zip (GeoJSON boundaries, medium resolution) into `data/gazetteer/`.
- Phase 3: Telegram alerts. `serve` runs a check then an alert run at 06:00 and 20:00 Manila, with a bot thread (`/start`, `/add`, `/list`, `/remove`, allowlisted). `alerts --dry-run` is read-only; `alerts_sent` is the per-subscriber ledger; the admin chat gets operational notes. Lipata and Sto. Niño resolve through aliases (Linao; `730600022`, which the release names `Central`).
- No web server, no HTML/JS, no map yet.

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/phase4-build.md` for the build session.

The Phase 4 goal (ROADMAP): FastAPI serves the week's outages as one GeoJSON; Leaflet + OSM tiles, vanilla JS; barangay shading (soft fill for "portion of"), day tabs, an hourly slider, a play button, defaults to now, cancelled greyed and toggleable, a popup with time, purpose, streets and a link to the post.

## What Phases 2 and 3 learned that the design must handle
1. **Which barangay rows go on the map.** `exact`/`alias`: shade. `ambiguous` (`Tayud` under Consolacion and Liloan): one row per listed town; shading both is the alert rule, but a map can say "VECO lists it under both". `other_town` rows carry **another town's** code (VECO named the wrong town); Phase 3 never alerts from them. Decide whether the map draws them, and how. `unmatched` (3 rows left) has no polygon.
2. **"Portion of" is almost every line**, so a shaded barangay overstates. The streets in `area_entities` say which portion; street-level geometry is Phase 5.
3. **Never present stale data as current.** A post whose latest parse failed keeps its earlier outages; alerts carry a caveat (`VECO updated this post and we couldn't read the update; this is from the earlier version.`). The map needs the same honesty, and a "data as of" (`meta.last_check_at`).
4. **Times are Manila (`+08:00`)**, never the browser's zone; overnight outages cross midnight; 53 outages start at 05:00 and 252 at 22:00, so day tabs and the slider must handle both.
5. **Status:** `scheduled`, `additional`, `cancelled`, and `removed` (no longer in VECO's post; not the same as cancelled).
6. **Display names:** the release calls Sto. Niño `Central`; aliases exist for other VECO spellings. Decide whether the map shows VECO's name, the PSGC name, or both.
7. **The `serve` process already owns the scheduler and the bot thread.** A web server inside it shares the SQLite file (`busy_timeout=30000` is set) and the PC's uptime.
8. **Offline by default.** Every new command and endpoint must not reach visayanelectric.com; the CLI dispatches explicitly and tests fail if an offline command builds the fetcher. OSM tiles are fetched by the browser, not by us (respect the tile usage policy).

## Questions the brainstorm must settle
- **FastAPI (+ uvicorn) is a new dependency:** confirm with the owner, or argue for the stdlib `http.server`. Same process as `serve` (thread) or a separate `map` command?
- Geometry: ship the release's medium-resolution polygons for the 232 barangays as a static file built once (size? simplify?), or join per request.
- The GeoJSON shape: one feature per (outage, barangay)? Per barangay with a list of outages? What the slider filters on.
- Which week: the current week's post, the next 7 days, or any range; how cancelled/removed and failed-post outages appear.
- Access: localhost only, or LAN; no auth now (Phase 6 ships it).
- Tests: endpoint tests with FastAPI's `TestClient` (or equivalent), golden GeoJSON, a JS smoke check without a browser in CI; no tile or VECO requests in CI.

## Read first
1. `ROADMAP.md`: Status, the Phase 3 entries, Known issues, Lessons.
2. Phase 3 spec `docs/superpowers/specs/2026-11-11-phase3-alerts-design.md` (§4.2 which rows concern a subscription, §4.4 wording).
3. Phase 2 spec `docs/superpowers/specs/2026-11-11-phase2-areas-design.md` (§4.3 resolution, §5 storage); `src/outages/gazetteer_build.py`.
4. `python -m outages report` and `python -m outages alerts --dry-run` (read-only views).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data, sending any Telegram message, and exposing a server beyond localhost.
- **Be gentle with visayanelectric.com:** this session needs no fetches; use the stored data.
- **No new dependencies** without the owner's OK.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Backslash escapes in Bash heredocs can be mangled: write Python files with the Write tool.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-phase4-map-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-phase4-map.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/phase4-build.md` written in this file's shape.
