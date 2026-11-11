# Phase 4: Map — build

## Context
This session builds Phase 4 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `phase4-map` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `main` at `9373e1c` (Phase 3 merged, PR #3).
- **Remote:** private `github.com/WMGamponia/OutageTracker`.

## Where things stand
- Phases 1–3 are merged: `python -m outages backfill|check|reparse|report|serve|gazetteer|areas|eval|alerts|bot`. `data/outages.db` (schema 3) holds 151 posts, 3,008 outages, parsed areas and the alert tables. `serve` runs a check + alert run at 06:00 and 20:00 Manila with a bot thread; the owner usually has it running.
- The Phase 4 spec was approved by the owner on 2026-11-12, section by section: `docs/superpowers/specs/2026-11-12-phase4-map-design.md`.
- The plan is written: `docs/superpowers/plans/2026-11-12-phase4-map.md` (10 tasks, TDD). Its Review Focus lists five inputs no other test covers; each has a named test in its task.
- The pinned gazetteer zip is already in `data/gazetteer/` (verified); building `boundaries.geojson` needs no download.

## Scope of this session
**The whole of Phase 4, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (owner chose native execution, 2026-11-12), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`, most capable model) over the whole branch before the PR. Read the spec, then the plan, before writing any code.

## Stops for the owner (and only these)
1. **Task 1 Step 1:** installing `fastapi` and `uvicorn` (approved in brainstorming; say which versions you got).
2. **Task 8 Step 1:** the one download of Leaflet 1.9.4's release; show the URL first.
3. **Task 9 Step 5:** ask the owner to restart their own `serve` on this branch; never start a second `serve` against the real DB.
4. **Task 9 Step 6:** the real-DB screenshots and findings, before docs.
5. Anything in the data the spec doesn't cover that changes what the map shows as affected: stop and report.
6. Running the map on `0.0.0.0` / the LAN; pushing, opening a PR, merging, deleting data.

## Working rules
- **No requests to visayanelectric.com, Telegram or tile servers from code or tests.** `map` is offline and read-only; tests block the network. The browser pane fetching OSM tiles during Task 9 is the only tile traffic (light, one person, with attribution).
- **Read-only map:** check the real DB's SHA-256 before and after Task 9; it must not change.
- **No dependencies beyond FastAPI, uvicorn and vendored Leaflet;** no npm packages. Node is for `node --test` only.
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Backslash escapes in Bash heredocs can be mangled: write Python files with the Write tool.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. All 10 plan tasks done; `ruff check`, `ruff format --check`, pytest and `node --test tests/js/` green locally.
2. The owner has seen the map on the real DB (desktop and phone widths) and `serve` running it; the DB hash is unchanged by the map.
3. `ROADMAP.md` progress entry; Phase 4 items ticked; Stack and Known issues current.
4. `prompts/phase5.md` written in the shape of `prompts/phase4.md`.
5. Ask the owner before pushing and opening the PR to `main`.
