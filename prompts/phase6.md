# Phase 6: Ship it — brainstorm

## Context
This session starts Phase 6 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider and week picker, and a history page. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` once the Phase 5 PR (`phase5-streets-history`) is merged (it follows the Phase 4 PR); otherwise branch from `phase5-streets-history` and say so.
- **Branch:** `phase6-ship` (create it only after the spec is approved).
- **Remote:** private GitHub repo under `WMGamponia`. Check with `git remote -v`.

## Where things stand
- Phases 1–4: `backfill|check|reparse|report|serve|areas|eval|alerts|bot|map`. `data/outages.db` (schema 4) holds every interruption post since Jan 2024 (~150 posts, ~3,000 outages); `serve` runs check + alerts at 06:00 / 20:00 Manila, the bot and the map at `127.0.0.1:8765`.
- Phase 5:
  - **Streets:** `streets fetch` (8 Overpass requests, by hand) → `data/osm/` (ODbL, never committed); `streets match` → `street_matches` (status + clipped lines); `streets --review`; town-scoped aliases in `areas/street_aliases.csv`. **Drawing is off:** the pre-registered gate needed test precision ≥ 0.95 over ≥ 20 drawn rows; the frozen 50-row split drew 13 at 0.923. The one miss drew the right road under its OSM `name` (the label used the `alt_name`). With ~40 % of VECO's street names in OSM, a 50-row split can hardly reach 20 drawn rows.
  - **History:** week picker on the map; `/history` (quantile heat map of union outage-hours per barangay, top 10, durations, towns, months, counted vs left out) from `/stats.json`; `stats` CLI.
  - **Multi-user bot:** approve-to-join with admin buttons, `/pending`, `/users`, `/revoke`, `/privacy`, `/forget`; limits (20 pending, 10 barangays, rate limit, 50 ms send spacing). The live test with a second account passed after a fix (a second `/forget` left a trace).
- ROADMAP Phase 6: README (what it does, architecture, eval results, screenshots/GIF, how to run, start-at-login guide) and a **static demo snapshot** (GeoJSON + map) on GitHub Pages.

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/phase6-build.md` for the build session.

## What Phases 1–5 learned that the design must handle
1. **A public snapshot is a different threat model.** Today everything is local and read-only. A GitHub Pages copy is world-readable and permanent once pushed: decide exactly what goes in (outage data is VECO's public posts; subscriber data never), how it is generated (a command, never by hand), and how often.
2. **Licences travel with the data.** OSM tiles' usage policy forbids heavy or commercial use of tile.openstreetmap.org: re-check it for a public page or pick another provider. Street lines (if any) are ODbL derived data: share-alike notice and an offer of the derived data. Boundaries are MIT (faeldon), Leaflet BSD-2. VECO's text: link back, don't republish whole posts.
3. **Street drawing is off by a pre-registered gate.** Turning it on needs new evidence, not a re-run: a fresh, larger test split (≥ 150 rows, distinct from the 80 already labelled) labelled with the corrected convention (gold = the OSM `name` tag), the same bar, run once. Decide whether that belongs in Phase 6 or stays off.
4. **Eval results are part of the README** (Phase 2 areas, Phase 5 streets): report them as measured, including the gate that failed and why.
5. **Start-at-login on Windows:** `serve` must survive reboots and sleep (two runs were missed in Phase 3–4). Task Scheduler vs a shortcut in Startup; how logs and the 15 h old-data warning interact; how the owner restarts it after an update.
6. **The web side has no login.** Phase 5 left web auth or sign-up to Phase 6; a static snapshot needs none. Don't build auth unless the design needs it.
7. **Times are Manila** (`+08:00`), never the browser's zone; the snapshot must say when it was taken.
8. **Offline by default.** New commands must not reach VECO, Overpass or Telegram unless that is their job; tests fail if an offline command builds the VECO fetcher, the Overpass client or a Telegram client.

## Questions the brainstorm must settle
- Snapshot: which window (this week? last 12 months of history?), file layout (the page + `outages.geojson` + `stats.json` + `boundaries.geojson`), size, how `/history` and the week picker behave without a server, how it says "snapshot of <date>".
- Publishing: a `snapshot` command that writes a folder; who pushes it, and with what review of the diff before it goes public.
- Tiles for a public page; ODbL notice if any street data is included.
- README: architecture diagram, eval tables, screenshots/GIF (made from the fixture DB or the real one?), how to run, start-at-login.
- Street gate: a second, larger eval now or later.

## Read first
1. `ROADMAP.md`: Status, the Phase 5 entries, Known issues, Lessons.
2. Phase 5 spec `docs/superpowers/specs/2026-11-12-phase5-streets-history-design.md` (§4.6–4.8 the document and pages, §5 the street eval and gate, §13 risks, ODbL).
3. Phase 4 spec `docs/superpowers/specs/2026-11-12-phase4-map-design.md` (§4.3 the document, §12 accepted limits, tiles).
4. `evals/areas/results/` and `evals/streets/results/`.
5. The map and `/history` on the real DB (`python -m outages map`, read-only).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data, publishing anything (GitHub Pages is public), sending any Telegram message, any Overpass request, and exposing a server beyond localhost.
- **Be gentle with visayanelectric.com and Overpass:** this session needs no fetches; use the stored data.
- **No new dependencies** without the owner's OK.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Bash heredocs can mangle backslashes and backticks: write Python files with the Write tool. Git Bash doesn't pass `TZ` to node here; use PowerShell (`$env:TZ=...`) for the zone runs, and `node --test "tests/js/*.test.mjs"` (Node 24 wants the glob).
- **`serve` runs in the Claude app's Terminal tab** when the owner asked for a restart; never start a second one against the real DB.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-phase6-ship-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-phase6-ship.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/phase6-build.md` written in this file's shape.
