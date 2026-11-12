# Phase 6: Ship it — build

## Context
This session builds Phase 6 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider and week picker, and a history page. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `phase6-ship` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `main` at `689dba9` (Phases 1–5 merged).
- **Remotes:** today `origin` = private `github.com/WMGamponia/OutageTracker`. **Nothing from Phase 6 is pushed there.** In Task 10 the rewritten history goes to a new public repo (default `WMGamponia/cebu-brownout-tracker`), which becomes `origin`; the private one becomes `private-archive`.

## Where things stand
- Phases 1–5: `python -m outages backfill|check|reparse|report|serve|gazetteer|areas|eval|alerts|bot|map|stats|streets`. `data/outages.db` (schema 4) holds every interruption post since Jan 2024; `serve` runs check + alerts at 06:00 / 20:00 Manila, the bot and the map at `127.0.0.1:8765`, usually in the Claude app's Terminal tab. Street drawing is off by its gate.
- The Phase 6 spec was approved by the owner on 2026-11-12, section by section: `docs/superpowers/specs/2026-11-12-phase6-ship-design.md`. Decisions: new public repo with a rewritten history (mailmap to `235282035+WMGamponia@users.noreply.github.com`, the barangay line reworded, fixtures trimmed), MIT; a hand-refreshed portfolio snapshot on GitHub Pages (53 weeks + 4 stats ranges, offline `snapshot` command, authorizer + leak scan, `gh-pages` worktree at `site/`); same pages with a `source.js` data source; OSM tiles kept per the policy; Streets v2 deferred; Task Scheduler script + wake task; `data/serve.lock`.
- The plan is written: `docs/superpowers/plans/2026-11-12-phase6-ship.md` (10 tasks, TDD). Its Review Focus lists five inputs no other test covers; each has a named test in its task.

## Scope of this session
**The whole of Phase 6, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (native execution), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`, most capable model) over the whole branch in Task 9, before anything goes public. Read the spec, then the plan, before writing any code.

## Stops for the owner (and only these)
1. **Task 8 Step 1:** whether to run `check` first (VECO traffic) if the data is more than 15 h old.
2. **Task 8 Step 4:** the popup and "Not on the map" screenshots (and the optional GIF) are the owner's; show the real-DB snapshot (numbers, headless screenshots) before writing the README.
3. **Task 10, every ⛔:** installing `git-filter-repo`; creating the public repo (ask for its name); pushing to it; resetting this checkout's branches to the rewritten history; opening the PR; merging; pushing `gh-pages`; the Pages setting. Show the Step 5 verification numbers before the first push.
4. Anything in the data or the history the spec doesn't cover that changes what goes public: stop and report.
5. Exposing a server beyond localhost; deleting data.

## Working rules
- **Network:** nothing contacts visayanelectric.com, Overpass or Telegram (a `check` only with stop 1's OK). Tests block the network; `snapshot` has a test that fails if it builds the VECO fetcher, the Overpass client or a Telegram client.
- **Read-only snapshot:** record the real DB's SHA-256 before and after Task 8 Step 2; it must not change.
- **Never run `git filter-repo` in this checkout**; only in a scratch clone, after the mirror backup exists. Never push Phase 6 to the private repo.
- **`install-task.ps1`:** parse it and run `-WhatIf` only; the owner installs it.
- **`serve`:** never start a second one against the real DB; once the lock exists, a second start exits 3 by design.
- **No new dependencies;** no npm packages. Node is for `node --test "tests/js/*.test.mjs"` only (also under `$env:TZ="America/New_York"` and `"Asia/Tokyo"` in PowerShell).
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Bash heredocs can mangle backslashes and backticks: write Python files with the Write tool.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise. After Task 10 Step 7 this checkout's `user.email` is the noreply address.

## End of session
1. All 10 plan tasks done; `ruff check`, `ruff format --check`, pytest and `node --test` green locally and in the public repo's CI.
2. The public repo's reachable history passes spec §4.8 step 4 (numbers in ROADMAP); the PR is merged with the owner's OK.
3. The Pages demo loads at its URL, says "Snapshot of <date>, not live", and passes the spec §7 by-hand checks; the README links it.
4. `ROADMAP.md` progress entry; Phase 6 items ticked; Status points to Streets v2; Known issues current.
5. `prompts/streets-v2.md` written in the shape of `prompts/phase6.md` (brainstorm prompt for the ≥ 150-row street eval and the LLM alias suggester).
6. Remind the owner to archive the private repo when ready.
