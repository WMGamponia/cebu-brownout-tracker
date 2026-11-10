# Phase 1: Ingest, Parse, Store, Backfill

## Context
This session builds Phase 1 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` (holds the ROADMAP, spec and plan; nothing pushed, no remote yet).
- **Branch:** create `phase1-ingest-parser` from `main`.
- **Remote:** none yet. The GitHub repo is **private**, under `WMGamponia`, created only with the owner's OK.

## Where things stand (2026-11-10)
- Recon, brainstorm, spec and plan done. No code yet.
- Spec: `docs/superpowers/specs/2026-11-10-phase1-ingest-parser-design.md` (§3 has the recon findings).
- Plan: `docs/superpowers/plans/2026-11-10-phase1-ingest-parser.md` (Tasks 1–10, all in this session).
- Python 3.14.7 and 3.12.10 are installed (`py -3.14`, `py -3.12`); `gh` is logged in as `WMGamponia`.
- Dependency versions checked on PyPI on 2026-11-10: httpx 0.28.1, selectolax 1.0.2, APScheduler 3.11.3, tzdata 2026.6, pytest 9.2.0, pytest-cov 7.1.0, ruff 0.17.4.

## Scope of this session
The whole phase, plan **Tasks 1–10**, in one session:
- **Part A (1–5):** scaffold + CI (Ubuntu 3.12, Windows 3.14) + `.github/` docs; fixtures (sitemap + 10 real posts); `normalize` and `timeparse`; `extract`; `parse` with golden tests (at least 3 expected JSON files written **by hand from reading the post**).
- **Part B (6–10):** `db` + migrations; `upsert` matching and change log; `discover` + polite `fetch`; `pipeline`, CLI (`backfill`, `check`, `reparse`, `report`, `serve`); live backfill and report.

**Only mid-phase stop:** a post format spec §3 doesn't list (in the fixtures or the backfill). Stop and report it; don't guess.

**At the end, STOP** and show the owner the backfill report (posts, outages, warnings, failures). Ask before creating the GitHub repo, pushing or opening the PR.

## Read first
1. `ROADMAP.md`: Status and the 2026-11-10 decisions.
2. The spec, all of it.
3. The plan's Global Constraints and Review Focus, then every task.
4. For the shape of `.github/` docs: `E:\Work\Projects\PH_Legal_Docs_Assistant\.github\`. Adapt, don't copy domain content.

## Working rules
- **Ask before anything irreversible or outward-facing:** creating the GitHub repo, pushing, opening a PR, merging, deleting data.
- **Be gentle with visayanelectric.com:** one request at a time, the project User-Agent, ≥ 5 s apart for fixtures, 3–8 s during backfill. Tests never touch the network (`live` marker only).
- **No LLM in Phase 1.**
- **Never commit `data/` or `.env`.**
- **Keep `ROADMAP.md` current** as tasks complete, not only at the end.
- **Check pytest's real exit code.** Never pipe it through `tail`.
- **On Windows:**
  - Prefer the Edit tool over shell heredocs for code.
  - **Never leave an empty `python -` heredoc** in a command; it hangs. Use a script file in the scratchpad, or `python -c`.
  - Read and write files as UTF-8: posts contain `ñ`, U+2011 and non-breaking spaces.
- **Commits:** conventional, with a scope, and **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.
- **When a PR is opened** (only with the owner's OK), enable CI Auto-fix on it.

## End of session
1. Tasks 1–10 committed on `phase1-ingest-parser`; `ruff check`, `ruff format --check`, `pytest -q` pass locally; `pytest -m live` passed once.
2. Backfill done; `report` output shown to the owner with every failure and warning explained.
3. `ROADMAP.md`: Phase 1 items ticked, dated progress entry with the numbers, lessons recorded.
4. `prompts/phase2.md` written in this file's shape for the Phase 2 brainstorm.
