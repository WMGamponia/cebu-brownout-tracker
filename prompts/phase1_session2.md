# Phase 1 Session 2: Scaffold, Fixtures, Parser

## Context
This session starts building the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` (holds the ROADMAP, spec and plan; nothing pushed, no remote yet).
- **Branch:** create `phase1-ingest-parser` from `main`.
- **Remote:** none yet. The GitHub repo is **private**, under `WMGamponia`, created only with the owner's OK.

## Where things stand (2026-11-10)
- Recon, brainstorm, spec and plan done. No code yet.
- Spec: `docs/superpowers/specs/2026-11-10-phase1-ingest-parser-design.md` (§3 has the recon findings).
- Plan: `docs/superpowers/plans/2026-11-10-phase1-ingest-parser.md` (Tasks 1–5 are this session; 6–10 are session 3).
- Python 3.14.7 and 3.12.10 are installed (`py -3.14`, `py -3.12`); `gh` is logged in as `WMGamponia`.
- Dependency versions checked on PyPI on 2026-11-10: httpx 0.28.1, selectolax 1.0.2, APScheduler 3.11.3, tzdata 2026.6, pytest 9.2.0, pytest-cov 7.1.0, ruff 0.17.4.

## Scope of this session
Plan **Tasks 1–5**:
1. Scaffold, ruff, pytest, CI (Ubuntu 3.12 + Windows 3.14), `.github/` docs.
2. **Fixtures:** the sitemap and 10 real posts (6 named in the plan + 4 picked by the criteria there). If a post shows a format spec §3 doesn't list, **stop and report it** before writing the parser.
3. `normalize` and `timeparse`.
4. `extract` (content lines, hash that ignores "x days ago" / "Updated: …", strikethrough flag).
5. `parse` with golden tests. At least 3 expected JSON files written **by hand from reading the post**.

**Then STOP** and show the owner: fixture list, test results, and anything surprising. Session 3 (storage, fetch, CLI, backfill) starts only after their review.

## Read first
1. `ROADMAP.md`: Status and the 2026-11-10 decisions.
2. The spec, all of it.
3. The plan's Global Constraints and Review Focus, then Tasks 1–5.
4. For the shape of `.github/` docs: `E:\Work\Projects\PH_Legal_Docs_Assistant\.github\` and `E:\Work\Projects\Price_Tracker` (once it has them). Adapt, don't copy domain content.

## Working rules
- **Ask before anything irreversible or outward-facing:** creating the GitHub repo, pushing, opening a PR, merging, deleting data.
- **Be gentle with visayanelectric.com:** fixtures are fetched once, one request at a time, at least 5 s apart, with the project User-Agent. Tests never touch the network (`live` marker only).
- **No LLM in Phase 1.** If a rule can't handle a format, report it; don't guess.
- **Never commit `data/` or `.env`.**
- **Check pytest's real exit code.** Never pipe it through `tail`.
- **On Windows:**
  - Prefer the Edit tool over shell heredocs for code.
  - **Never leave an empty `python -` heredoc** in a command; it hangs. Use a script file in the scratchpad, or `python -c`.
  - Read and write files as UTF-8: posts contain `ñ`, U+2011 and non-breaking spaces.
- **Commits:** conventional, with a scope, and **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.
- **When a PR is opened** (only with the owner's OK), enable CI Auto-fix on it.

## End of session
1. Tasks 1–5 committed on `phase1-ingest-parser`; `ruff check`, `ruff format --check`, `pytest -q` pass locally.
2. Fixtures README lists all 10 posts and what each covers; generated expected JSON marked reviewed.
3. `ROADMAP.md` progress entry dated; Current phase reads "Phase 1 — Ingest, parse, store, backfill (in progress)".
4. `prompts/phase1_session3.md` written in this file's shape.
