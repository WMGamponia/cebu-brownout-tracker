# Phase 3: Telegram Alerts — brainstorm

## Context
This session starts Phase 3 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` once the Phase 2 PR (`phase2-areas`) is merged; otherwise branch from `phase2-areas` and say so.
- **Branch:** `phase3-alerts` (create it only after the spec is approved).
- **Remote:** private GitHub repo under `WMGamponia`. Check with `git remote -v`.

## Where things stand
- Phase 1: `python -m outages backfill|check|reparse|report|serve` stores every interruption post in `data/outages.db` with outages, versions and a change log (`outage_changes`: added, removed, cancelled, time changed, areas changed).
- Phase 2: `python -m outages areas` parses each distinct `areas_raw` (keyed by `outages.areas_hash`) into `area_parses`, `area_barangays(name_raw, town_psgc, psgc_code, match)` and `area_entities(kind, name)`. `check` and `reparse` run it at the end. The default entity extractor is **rules** (the eval: `evals/areas/results/2026-11-11-test.md`); Qwen stays available with `OUTAGES_ENTITY_EXTRACTOR=qwen`.
- No Telegram code, no users, no secrets handling beyond `ANTHROPIC_API_KEY` in `.env`.

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/phase3-build.md` for the build session.

The Phase 3 goal (ROADMAP): a bot that alerts a subscriber about outages in their barangay at ~20:00 the evening before and ~06:00 the morning of, and on cancelled / time changed / added / removed; a fresh fetch of the current week's post right before each alert run; catch-up alerts on start for outages that haven't begun; "may affect you" wording.

## What Phase 2 learned that the design must handle
1. **Which barangay rows may alert.** `match` is `exact`, `alias`, `ambiguous`, `other_town` or `unmatched`.
   - `exact`/`alias`: safe.
   - `ambiguous`: one row per listed town (`Tayud` under Consolacion & Liloan). Alert both; a missed alert is worse than two.
   - **`other_town` rows carry another town's PSGC code** (VECO named a barangay under the wrong town: `Jagobiao` under Consolacion, `Poblacion` under Minglanilla). Alerting from them sends false alerts. Decide: never alert, or alert with explicit wording.
   - `unmatched` has no code (`Lipata`, Minglanilla; `Sto. Niño`, Cebu City are missing from the gazetteer). Subscribers there can't be matched by code; decide what they get.
2. **"Portion of" is everywhere.** Almost every line affects a *portion* of a barangay; street/sitio entities say which portion. Wording should say "may affect you" and show the streets.
3. **A failed parse keeps old outages** (Phase 1 lesson). Don't alert from a post whose latest parse failed; say what happens instead.
4. **Changes come from `outage_changes`**, written by `check`. Alerts on changes must be idempotent: one alert per change per subscriber, even across restarts.
5. **Network budget:** at most ~4 requests a day to visayanelectric.com; the alert runs' "fresh fetch" counts. The Phase 2 build accidentally sent ~16 requests through a CLI fall-through; keep every new command offline unless it is meant to fetch, and test that.

## Questions the brainstorm must settle
- Subscriptions: one user (the owner) now, multi-user in Phase 5. Data model (`subscribers`, `subscriptions(psgc_code)`), how a user picks a barangay (bot command with search over `barangays.csv`?).
- Telegram: Bot API over httpx (no new dependency) vs a library (needs the owner's OK). Long polling vs webhooks on a home PC. Where the bot token lives (`.env`, never logged).
- Scheduling inside `serve`: alert runs at ~20:00 and ~06:00 Manila next to the 07:00/17:00 checks; catch-up on start; what happens when the PC was asleep.
- An `alerts_sent` ledger for idempotency; message format (Markdown escaping), grouping several outages into one message.
- Tests: no real Telegram calls in CI (`httpx.MockTransport`); a dry-run mode that prints the messages it would send.

## Read first
1. `ROADMAP.md`: Status, the Phase 2 entries, Known issues (the areas review queue), Lessons.
2. Phase 2 spec `docs/superpowers/specs/2026-11-11-phase2-areas-design.md` (§4.3 resolution, §5 storage).
3. `src/outages/migrations/001_init.sql`, `002_areas.sql`; `src/outages/pipeline.py`, `scheduler.py`.
4. `python -m outages report` and `python -m outages areas --review` (read-only views).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data, and **sending any Telegram message**.
- **Be gentle with visayanelectric.com:** this session needs no fetches; use the stored data.
- **No new dependencies** without the owner's OK.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8.
- **Never commit `data/` or `.env`.**
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-phase3-alerts-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-phase3-alerts.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/phase3-build.md` written in this file's shape.
