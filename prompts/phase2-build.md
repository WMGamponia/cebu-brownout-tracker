# Phase 2: Areas and Evals — build

## Context
This session builds Phase 2 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `phase2-areas` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `main` at `c951f34` (Phase 1 merged).
- **Remote:** private `github.com/WMGamponia/OutageTracker`.

## Where things stand
- Phase 1 is merged: `python -m outages backfill|check|reparse|report|serve`. `data/outages.db` holds 151 posts and 3,008 outages; 1 post fails on purpose (ambiguous AM/PM), and 17 `removed` rows are old wrong-dated copies.
- The Phase 2 spec was approved by the owner on 2026-11-11, section by section: `docs/superpowers/specs/2026-11-11-phase2-areas-design.md`.
- The plan is written: `docs/superpowers/plans/2026-11-11-phase2-areas.md` (10 tasks, TDD).
- Ollama 0.34 is installed with `qwen3:14b` (Q4_K_M, runs fully in VRAM, ~54 tok/s). Start it before Task 8.

## Scope of this session
**The whole of Phase 2, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (owner chose native execution, 2026-11-11), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`) over the whole branch before the PR. Read the spec, then the plan, before writing any code.

## Stops for the owner (and only these)
1. **Task 2 Step 4:** the gazetteer's per-town counts differ from the recon (232 total).
2. **Task 3 Step 3 and Step 6:** show the sample's layout × town counts; then the label review (all structured rows + 10 others). Freeze only after the owner's corrections.
3. **Task 7 Step 3 and Step 5:** show the full-corpus `areas --dry-run` summary; ask before the first real write to `data/outages.db`.
4. **Task 10 Step 1:** ask before any paid Haiku call (only if `ANTHROPIC_API_KEY` is set).
5. **Task 10 Step 3:** if Qwen wins, show the full-corpus dry-run summary before `areas --rebuild`.
6. Any VECO areas format the spec doesn't list that a rule can't handle cleanly: stop and report, as in Phase 1.
7. Pushing, opening a PR, merging, deleting data.

## Eval discipline (non-negotiable)
- **No rule, alias or prompt before the labels are frozen and committed** (Task 3).
- **After the freeze, never open, print or grep `evals/areas/test.jsonl`.** Tune rules and the prompt on `dev.jsonl` only. The CI regression test prints numbers, never rows.
- Labels are written from the post text, never from any arm's output. Any label change needs a dated entry with a reason in `evals/areas/CHANGES.md` and an updated `FROZEN`.
- Run the test split once per arm (Task 10). Apply the pre-registered rule as `decide()` computes it: Qwen becomes the default only if its test street F1 ≥ rules + 5 points and its typed F1 ≥ rules; ties go to rules; Haiku is never the default. No re-runs or prompt/label changes after seeing test results without the owner's written OK.

## Working rules
- **No requests to visayanelectric.com.** The only network use is the pinned GitHub gazetteer download, local Ollama, and Anthropic only with a key and the owner's OK.
- **No new dependencies.** httpx (already present) for Ollama and Anthropic; the schema check is hand-written.
- **Run every parser change over the whole corpus read-only before writing** (Phase 1 lesson).
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8.
- **Never commit `data/` or `.env`.**
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. All 10 plan tasks done; `ruff check`, `ruff format --check` and pytest green locally.
2. `evals/areas/results/<date>-test.md` committed; `ENTITY_EXTRACTOR` set by the rule.
3. Full-corpus `areas` written to `data/outages.db`; review queue empty or every remaining name explained in the ROADMAP.
4. `ROADMAP.md` progress entry with layout counts, match rate, the eval table and the decision; Phase 2 items ticked.
5. `prompts/phase3.md` written in the shape of `prompts/phase2.md`.
6. Ask the owner before pushing and opening the PR to `main`.
