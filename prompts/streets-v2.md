# Streets v2: a bigger street eval and an LLM alias suggester — brainstorm

## Context
This session starts the **Streets v2** phase of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider and week picker, a history page, and a static demo snapshot on GitHub Pages. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` of the **public** repo `WMGamponia/cebu-brownout-tracker` (Phase 6 merged; history rewritten). The old private repo is the remote `private-archive`; never push there.
- **Branch:** `streets-v2` (create it only after the spec is approved).
- **Commit email:** this checkout's `user.email` is the GitHub noreply address; keep it.

## Where things stand
- Phases 1–6: `backfill|check|reparse|report|serve|gazetteer|areas|eval|alerts|bot|map|stats|streets|snapshot`. `data/outages.db` (schema 4) holds every interruption post since Jan 2024. `serve` runs check + alerts at 06:00 / 20:00 Manila, the bot and the map at `127.0.0.1:8765`, under a single-instance lock (`data/serve.lock`); `scripts/install-task.ps1` starts it at logon.
- **The demo** is a hand-refreshed snapshot on the `gh-pages` branch (worktree `site/`), published with `python -m outages snapshot --out site` after a review; see the README.
- **Streets today:** `streets fetch` (8 Overpass requests, by hand) → `data/osm/` (ODbL, never committed); `streets match` → `street_matches` (rules on `street_key`, any OSM name tag, clipped to the listed barangays + 150 m; town-scoped aliases in `src/outages/areas/street_aliases.csv`). **Drawing is off** (`STREETS_DRAW = False`): the pre-registered gate needed test precision ≥ 0.95 over ≥ 20 drawn rows; the frozen 50-row split drew 13 at 0.923, and the one miss drew the right road under its OSM `name` while the label used the `alt_name`.
- **Coverage:** 2,669 of 5,032 street mentions find no OSM way of that name near their barangays; many are near-misses ("Bugos" → Burgos Street, "De Julio St." → 18 de Julio Street, "Mary Ellen St." → Paseo Mary Ellen). `streets --review` suggests close names; none are applied automatically.
- ROADMAP "Streets v2": (1) a fresh street test split of ≥ 150 rows, distinct from the 80 already labelled, gold = the OSM `name` tag, same bar, run once; (2) an offline LLM alias suggester (OSM names in scope only; dev and unlabelled mentions only; the owner approves each into `street_aliases.csv`; aliases frozen before the test run).

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/streets-v2-build.md` for the build session (one session per phase).

## What Phases 1–6 learned that the design must handle
1. **Size the test set from the expected hit rate before labelling.** ~40 % of VECO's street names are in OSM; ≥ 20 drawn rows at that rate needs ≥ 50 drawn-eligible rows, so ≥ 150 rows is the floor, not a target. Compute the expected drawn count (and its spread) from the dev data first, and say what happens if it still falls short.
2. **Write the labelling convention for multi-named things first.** Gold = the OSM `name` tag; decide `alt_name` / `official_name` / `old_name` / `name:en` up front, and what "right line, other name" scores.
3. **Fix the decision rule before the results, and make it winnable.** Same bar (precision ≥ 0.95 over ≥ 20 drawn), run once on a frozen split. Coverage is reported, not gated.
4. **The suggester must not leak into the test.** Suggestions only from dev and unlabelled mentions; the test rows are sampled and frozen **before** any suggestion run, and aliases are frozen before the test run. Decide how to prove it (row ids, hashes, a check in the eval).
5. **The LLM proposes, the owner decides.** Offline; it may only pick one of the OSM names actually in scope (the barangays' buffered area) or "none"; nothing reaches `street_aliases.csv` or the map unapproved. Phase 2 kept rules over Qwen on a margin that couldn't be won; measure the suggester (acceptance rate on a sample, how many mentions each accepted alias unlocks), don't assume it helps.
6. **Model choice has a privacy side.** Local Qwen via Ollama is free and keeps VECO's text and OSM names on the PC, but is slow (~5 s a call; ~2,700 names); Claude Haiku is fast but needs an API key and sends text off the PC. Decide, and whether a cache and batching make Qwen enough.
7. **Turning drawing on changes the public demo.** If the gate passes, the snapshot's week files carry derived ODbL street lines: `LICENSES.md` and the map already have the notice; re-check that, the snapshot sizes, and the README's eval section.
8. **Offline by default.** No VECO, Overpass or Telegram traffic unless a command's job is that; an Overpass refetch is the owner's call. Tests block the network.

## Questions the brainstorm must settle
- The new split: how many rows, sampled how (by town, by street type, excluding the 80 already labelled), who labels, the labelling guide changes, and the expected drawn count.
- What happens to the 50-row split: retired, kept as dev, or reported alongside.
- Suggester: model, prompt shape (VECO name + the in-scope OSM names → one name or none), which mentions it sees, caching, the review UI (CLI queue? a file the owner edits?), and how approved aliases are recorded with their provenance.
- Whether an OSM refetch is needed first (the extract is from 2026-11-12) and how that interacts with frozen labels.
- If the gate passes: turn `STREETS_DRAW` on, refresh the demo, update the README; if it fails again: what is reported and whether there is a next step at all.

## Read first
1. `ROADMAP.md`: Status, the Phase 5 and Phase 6 entries, Known issues, Lessons.
2. Phase 5 spec `docs/superpowers/specs/2026-11-12-phase5-streets-history-design.md` (§4.2–4.5 streets, §5 the street eval and gate).
3. `evals/streets/` (GUIDE, CHANGES, dev/test, results) and `src/outages/streets/`.
4. Phase 6 spec `docs/superpowers/specs/2026-11-12-phase6-ship-design.md` (§4.2 what goes public, §4.6 licences).
5. `python -m outages streets --review` on the real DB (read-only).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data, publishing the snapshot (GitHub Pages is public), sending any Telegram message, any Overpass request, any API call that sends VECO text off the PC, and exposing a server beyond localhost.
- **No new dependencies** without the owner's OK.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Bash heredocs can mangle backslashes and backticks: write Python files with the Write tool. Use PowerShell (`$env:TZ=...`) for the node zone runs, and `node --test "tests/js/*.test.mjs"`.
- **`serve` may be running** (Task Scheduler or the Terminal tab); never start a second one against the real DB (it exits 3 by design).
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-streets-v2-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-streets-v2.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/streets-v2-build.md` written in this file's shape.
