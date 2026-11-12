# Streets v2: a bigger street eval and an LLM alias suggester — build

## Context
This session builds the **Streets v2** phase of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider and week picker, a history page, and a static demo snapshot on GitHub Pages. Python, httpx + selectolax, SQLite, APScheduler, FastAPI + uvicorn; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Branch:** `streets-v2` (exists; holds the spec, plan, ROADMAP entry and this prompt). Base `main` at `e969763` of the **public** repo `WMGamponia/cebu-brownout-tracker`. The old private repo is the remote `private-archive`; never push there.
- **Commit email:** this checkout's `user.email` is the GitHub noreply address; keep it.

## Where things stand
- Phases 1–6: `python -m outages backfill|check|reparse|report|serve|gazetteer|areas|eval|alerts|bot|map|stats|streets|snapshot`. `data/outages.db` (schema 4); `serve` runs check + alerts at 06:00 / 20:00 Manila, the bot and the map at `127.0.0.1:8765`, under `data/serve.lock`. Street drawing is off (`STREETS_DRAW = False`) by the Phase 5 gate. The OSM extract in `data/osm/` is from 2026-11-12 (`osm_base 2026-11-12T02:14:58Z`).
- The spec was approved by the owner on 2026-11-12, section by section: `docs/superpowers/specs/2026-11-12-streets-v2-design.md`. Decisions: a 150-row mention-weighted test split (one per (town, key), excluding the 80 old keys), gold = the OSM `name` tag under GUIDE v2, labelled by a fresh subagent whose gold this session never reads, ≥ 25 labels reviewed by the owner, frozen; the old 80 rows relabelled as dev; a suggester (12 in-scope candidates → local `qwen3:14b` or `difflib`, whichever wins on dev) filling a local queue; a loopback-only review map at `/streets/review`; `streets suggest --apply` with provenance; automatic suggestions in `serve` (bounded, never fatal), applying always manual; one guarded test run; same gate; fail → drawing stays off for good; aliases approved after the test are unmeasured and the README says so.
- The plan is written: `docs/superpowers/plans/2026-11-12-streets-v2.md` (15 tasks, TDD). Its Review Focus lists five inputs no other test covers; each has a named test in its task.

## Scope of this session
**The whole of Streets v2, in this one session** (owner rule: one session per phase). Execute the plan task by task yourself with `superpowers:executing-plans` (native execution), TDD throughout, then one fresh reviewer (`superpowers:requesting-code-review`, most capable model) over the whole branch in Task 15. Read the spec, then the plan, before writing any code. Task 5's labelling is the one place a subagent is required (spec §6.2).

## Stops for the owner (and only these)
1. **Task 5 Step 4:** the owner reviews ≥ 25 labels in `data/streets/label-review.md` before the test split is frozen.
2. **Task 13 Step 4:** the owner decides the suggestion queue on the review page (or the CSV). Never make a decision for the owner.
3. **Task 15 Step 2a (pass only):** backup + `streets match --rebuild` on the real DB; the snapshot review and the `gh-pages` push.
4. **Task 15 Step 6:** push, PR, merge.
5. Anything the spec doesn't cover that would change what is drawn or published: stop and report. Exposing a server beyond localhost; deleting data; any Overpass request.

## Working rules
- **Never read the test gold.** After Task 5 Step 2, do not open, print or grep `evals/streets/test.jsonl`; use `eval check` (counts only). The one exception is the Task 15 results file, after the single run, and a row the owner asks you to correct.
- **One test run.** `eval run --suite streets --split test` happens once, in Task 15, after `ALIASES_FROZEN`. `--again` is only for a crashed run; say so if you use it.
- **Network:** only `streets suggest` and the `serve` hook reach `localhost:11434` (Ollama). Nothing contacts visayanelectric.com, Overpass or Telegram (the `serve` admin note is the existing path). Tests block the network. Don't run `ollama list` from the shell: it starts the Ollama app in the foreground; check `http://localhost:11434/api/tags` instead.
- **Real DB:** read-only (`db.connect_ro`) except the Task 15 rebuild with the owner's OK. `serve` may be running (Task Scheduler or the Terminal tab); never start a second one (it exits 3). For the review page on the real queue use `python -m outages map --port 8766` if `serve` isn't on this branch.
- **No new dependencies;** no npm packages. `node --test "tests/js/*.test.mjs"` (also under `$env:TZ="America/New_York"` and `"Asia/Tokyo"` in PowerShell).
- **Keep `ROADMAP.md` current**: a progress entry at each stop and at the end.
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8. Bash heredocs can mangle backslashes and backticks: write Python files with the Write tool.
- **Never commit `data/` or `.env`;** never read `.env` into the conversation.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. All 15 plan tasks done; `ruff check`, `ruff format --check`, pytest and `node --test` green locally and in CI.
2. The test split ran once; the gate's outcome is recorded in `evals/streets/results/`, the README and the ROADMAP; on a pass, drawing is on locally and (with the owner's OK) in the Pages snapshot with the ODbL text.
3. The PR is merged with the owner's OK.
4. `ROADMAP.md` progress entry; Streets v2 items ticked; Status points to the next phase (Phase 7, MCWD water) or says what is left; Known issues current.
5. `prompts/phase7.md` written in the shape of `prompts/streets-v2.md` (brainstorm prompt for MCWD water interruptions), if the owner wants Phase 7 next.
