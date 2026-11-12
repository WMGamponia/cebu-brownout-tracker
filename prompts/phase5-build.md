# Phase 5: Streets, history, multi-user — build

## Context
This session builds Phase 5 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `phase5-streets-history` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `phase4-map` at `e1d73e3`, because Phase 4's PR #4 was still open. If PR #4 has merged by now, rebase onto `main` first and say so.
- **Remote:** private `github.com/WMGamponia/OutageTracker`.

## Where things stand
- Phases 1–4: `python -m outages backfill|check|reparse|report|serve|gazetteer|areas|eval|alerts|bot|map`. `data/outages.db` (schema 3) holds 151 posts, 3,025 outages (3,008 not removed), parsed areas (5,032 `street` entities, 1,899 distinct after normalisation), 1 subscriber. `serve` runs check + alerts at 06:00 / 20:00 Manila, the bot and the map at `127.0.0.1:8765`; the owner usually has it running.
- The Phase 5 spec was approved by the owner on 2026-11-12, section by section: `docs/superpowers/specs/2026-11-12-phase5-streets-history-design.md`. Decisions: approve-to-join bot; one Overpass extract per town, refreshed by hand, kept in `data/osm/` (ODbL, never committed); lines drawn on top of shading; rules + aliases matcher with a frozen eval and a pre-registered gate (test precision ≥ 0.95 with ≥ 20 drawn rows, else drawing ships off); week picker + `/history` page with union-hours stats; no new dependencies.
- The plan is written: `docs/superpowers/plans/2026-11-12-phase5-streets-history.md` (12 tasks, TDD). Its Review Focus lists five inputs no other test covers; each has a named test in its task.

## Scope of this session
**The whole of Phase 5, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (native execution, as in Phases 3–4), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`, most capable model) over the whole branch before the PR. Read the spec, then the plan, before writing any code.

## Stops for the owner (and only these)
1. **Task 3 Step 6:** the 8 Overpass requests. Show the printed plan (towns and bboxes) first.
2. **Task 4 Step 6:** the owner reviews ≥ 15 street labels before the test split is frozen.
3. **Task 6 Step 5 result:** report the test-split numbers and the gate's outcome (drawing on or off) before going on.
4. **Task 6 Step 6:** backup, migration 004 and `streets match` on the real DB.
5. **Task 8/10 browser checks** use a scratch fixture DB; the real-DB check in **Task 12 Step 1** is shown to the owner (screenshots, numbers) before docs. Ask the owner to restart their own `serve` on this branch; never start a second `serve` against the real DB.
6. **Task 11 Step 6:** the live Telegram test with a second account.
7. Anything in the data the spec doesn't cover that changes what is drawn or counted: stop and report.
8. LAN exposure; pushing, opening a PR, merging, deleting data.

## Working rules
- **Network:** only `streets fetch --yes` contacts Overpass (after stop 1); nothing contacts visayanelectric.com; Telegram only in the live test. Tests block the network; every offline command has a test that fails if it builds the VECO fetcher, the Overpass client or a Telegram client.
- **Labels before rules:** the street sample and labels are made before `streets/match.py` exists; `test.jsonl` is never opened during rule work; the test split runs once.
- **Read-only map and stats:** check the real DB's SHA-256 before and after Task 12 Step 1; it must not change.
- **No new dependencies;** no npm packages. Node is for `node --test` only (run it under `TZ=America/New_York` and `Asia/Tokyo` via PowerShell `$env:TZ`).
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Backslash escapes in Bash heredocs can be mangled: write Python files with the Write tool.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. All 12 plan tasks done; `ruff check`, `ruff format --check`, pytest and `node --test tests/js/` green locally.
2. The owner has seen streets (or the gate's "off"), a past week and `/history` on the real DB, and the live join → approve → `/add` → `/forget` run; the DB hash is unchanged by the map and stats.
3. `ROADMAP.md` progress entry (eval numbers, gate, real-DB numbers); Phase 5 items ticked; Stack and Known issues current.
4. `prompts/phase6.md` written in the shape of `prompts/phase5.md`.
5. Ask the owner before pushing and opening the PR (to `main`, after PR #4).
