# Phase 3: Telegram Alerts — build

## Context
This session builds Phase 3 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `phase3-alerts` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `main` at `3f0f0fd` (Phase 2 merged, PR #2).
- **Remote:** private `github.com/WMGamponia/OutageTracker`.

## Where things stand
- Phases 1 and 2 are merged: `python -m outages backfill|check|reparse|report|serve|gazetteer|areas|eval`. `data/outages.db` holds 151 posts, 3,008 outages and their parsed areas (`area_barangays`, `area_entities`); the entity extractor is **rules**.
- The Phase 3 spec was approved by the owner on 2026-11-11, section by section: `docs/superpowers/specs/2026-11-11-phase3-alerts-design.md`.
- The plan is written: `docs/superpowers/plans/2026-11-11-phase3-alerts.md` (10 tasks, TDD).
- No Telegram bot exists yet. The owner creates it with BotFather at Task 9 and puts the token in `.env`.

## Scope of this session
**The whole of Phase 3, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (owner chose native execution, 2026-11-11), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`) over the whole branch before the PR. Read the spec, then the plan, before writing any code.

## Stops for the owner (and only these)
1. **Task 2 Step 1:** the PSA codes for Lipata and Sto. Niño and whether they fit the release's 9-digit form.
2. **Task 2 Step 6:** the full-corpus `areas --dry-run` result; ask before `areas --rebuild` on the real DB (back it up first).
3. **Task 9 Step 2:** the owner creates the bot and fills `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_ADMIN_CHAT_ID`).
4. **Task 9 Steps 3–5:** show the real-DB `alerts --dry-run`; ask before **every** real Telegram send (`bot`, `alerts --send`, `-m live_telegram`).
5. Anything in the data the spec doesn't cover that changes who gets alerted: stop and report.
6. Pushing, opening a PR, merging, deleting data.

## Working rules
- **No requests to visayanelectric.com.** The build needs none: `alerts`, `bot` and `report` are offline toward VECO, and tests block the network. Don't run `check`, `backfill` or `serve` against the real site without the owner's OK; a `serve` start can fetch (catch-up).
- **Telegram:** no real call in tests (`httpx.MockTransport`); never print, log or commit the token; never read `.env` into the conversation.
- **No new dependencies.**
- **Back up `data/outages.db` before its first write** this phase (migration 003 or `areas --rebuild`), and run the read-only dry run first.
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8.
- **Never commit `data/` or `.env`.**
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. All 10 plan tasks done; `ruff check`, `ruff format --check` and pytest green locally.
2. The owner received one real alert and a second run sent nothing.
3. `ROADMAP.md` progress entry (counts only, no message contents with personal data); Phase 3 items ticked; Stack, polling line and Known issues current.
4. `prompts/phase4.md` written in the shape of `prompts/phase3.md`.
5. Ask the owner before pushing and opening the PR to `main`.
