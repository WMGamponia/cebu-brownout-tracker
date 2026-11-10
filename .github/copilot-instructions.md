# Copilot Instructions — Cebu Brownout Tracker

Turns Visayan Electric (VECO) scheduled power interruption posts into structured outages, per-barangay Telegram alerts and a map:
**sitemap discovery → polite fetch → extract lines → rule-based parse → SQLite with edit history → (Phase 2+) areas, alerts, map**.
Plan, phase status, known issues and lessons learned: `ROADMAP.md` (read its Status section first). Design specs and plans: `docs/superpowers/specs/`, `docs/superpowers/plans/`.

These rules apply to every suggestion. Files in `.github/instructions/` add rules per area; if one conflicts with this file, the specific file wins unless it weakens correctness.

## Priorities (in order)
1. **Correct** — a wrong outage time is worse than none: people plan their day around it. Never guess a format silently; warn, fail the post loudly, or report it.
2. **Polite to the source** — visayanelectric.com is a utility's site, not an API. Few requests, one at a time, a named User-Agent.
3. **Reproducible** — the parser is a pure function of the post HTML; `reparse` over stored versions gives the same result without the network.
4. **Simple** — smallest change that works, in the style of the surrounding code. Personal tool, single user.

## Hard rules
- **Network only through `outages.fetch`**: User-Agent `CebuBrownoutTracker/0.1 (+https://github.com/WMGamponia/OutageTracker)`, no concurrency, 3–8 s between posts during backfill. Discover posts from `blog-posts-sitemap.xml` only; never construct slugs or crawl listing pages.
- **No LLM in Phase 1.** Phase 2 uses rules for barangays/cities and local Qwen (Ollama) or rules for streets, chosen by the eval.
- **Never delete outages.** One missing from a newer version becomes `removed` and is logged. A failed re-parse leaves stored outages untouched.
- **Tests never touch the network** except those marked `live` (run with `pytest -m live`).
- **Never commit `data/` or `.env`.**
- **Commits and PRs carry no co-author or AI attribution**: no `Co-Authored-By:` trailers, no "Generated with …" lines.

## Where things live
| Area | Path |
|---|---|
| CLI (`backfill`, `check`, `reparse`, `report`, `serve`) | `src/outages/__main__.py` — `python -m outages <command>` |
| Paths, User-Agent, schedule | `src/outages/config.py` |
| Pure layers: normalize → timeparse → extract → parse → upsert | `src/outages/{normalize,timeparse,extract,parse,upsert}.py` |
| Storage and migrations | `src/outages/db.py`, `src/outages/migrations/NNN_*.sql` |
| Discovery, fetching, pipeline, scheduler | `src/outages/{discover,fetch,pipeline,scheduler}.py` |
| Tests, saved real posts, expected outages | `tests/`, `tests/fixtures/posts/`, `tests/fixtures/expected/` |
| Local DB, logs, reports (gitignored) | `data/` |
| Session hand-off prompts | `prompts/` |

## Keep these in sync (most bugs come from here)
| When you change… | Also change… |
|---|---|
| Parser or normalizer behavior | Golden files in `tests/fixtures/expected/` (review each changed line against the post); run `python -m outages reparse` then `report` |
| A new post format appears | Save the post as a fixture, add a failing test, then handle it; note it in `tests/fixtures/posts/README.md` |
| The schema | A new numbered file in `src/outages/migrations/`; never edit an applied migration |
| What `extract` emits | `text_hash` changes for every post: expect one new version per post on the next check — say so |
| A phase step finishes, a number changes, a lesson emerges, an issue is deferred | `ROADMAP.md`: Status, Progress log (dated), checkboxes, Known issues, Lessons |

## How to work
- Match the surrounding code's patterns, naming and comment style; don't introduce new layers.
- No new dependencies in `pyproject.toml` without asking.
- Behavior change → add or update tests. Parser bug → regression test from the **real post** that exposed it.
- Comments explain *why*, citing the post slug or spec section (`spec 6.3`).
- Don't read `data/` or `.venv/` wholesale; query the DB or grep.

## Verify before calling it done
```bash
.venv/Scripts/python -m ruff check
.venv/Scripts/python -m ruff format --check
.venv/Scripts/python -m pytest -q          # no network
.venv/Scripts/python -m pytest -q -m live  # fetches the sitemap and the newest post
```
Check pytest's real exit code — don't pipe it through `tail` and trust the last line.

Environment: Windows, PowerShell/Git Bash, Python 3.14 venv in `.venv` (code targets 3.12+). Posts contain `ñ`, U+2011 and non-breaking spaces: always read/write files as UTF-8.

## Git
- Branch per phase or fix: `phase1-ingest-parser`, `fix/<short-topic>`. `main` holds finished phases.
- Conventional commits with a scope: `feat(parse): …`, `fix(fetch): …`, `test(…)`, `docs: …`. Imperative subject under 72 characters; the body says *why*.
- No co-author trailers or AI attribution in commits or PRs. Don't create the repo, push or merge without the owner's OK.

## Area-specific instructions
| Files | Instructions |
|---|---|
| `**/*.py` | `instructions/python.instructions.md` |
