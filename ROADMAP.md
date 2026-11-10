# Cebu Brownout Tracker — Roadmap

Turns Visayan Electric (VECO) scheduled power interruption advisories into per-barangay Telegram alerts, an interactive map of the VECO franchise area with a time slider, and historical stats.
Runs on my own PC: **Python + httpx/selectolax + SQLite + APScheduler**, later FastAPI, Leaflet and a Telegram bot. The LLM part is small, deliberate and measured with evals.

> Be gentle with visayanelectric.com: discover posts from the sitemap, one request at a time, a named User-Agent, at most ~4 requests a day once backfilled.

---

## Status

**Current phase:** Phase 1 — Ingest, parse, store, backfill (in progress, branch `phase1-ingest-parser`; code done, backfill next).
**Source:** `https://www.visayanelectric.com/blog-posts-sitemap.xml` → posts whose slug contains `interruption` (151 posts, Jan 2024 → now; one lacks the `service-` prefix).

### Progress log
- **2026-11-10** — Project started. Recon and brainstorm done. Phase 1 spec: `docs/superpowers/specs/2026-11-10-phase1-ingest-parser-design.md`.
  - **Recon** (details in the spec §3): robots.txt allows the blog; no terms page found. Discover posts from the sitemap (with `lastmod`), not the 56 listing pages. The archive starts January 2024. No post so far carries a VECO map image. Two "Areas Affected" layouts (`Portion of Cebu City (Tisa)` and `Portion of Tisa, Cebu City,`), two-town lines, `CANCELLED` and `ADDITIONAL` suffixes, overnight entries with explicit dates.
  - **Boundaries:** `faeldon/philippines-json-maps` (PSGC 3Q 2026, MIT), pinned release. All 8 VECO towns are complete (Cebu City 80, Mandaue 27, Talisay 22, Naga 28, Minglanilla 19, Consolacion 21, Liloan 14, San Fernando 21). HDX COD-AB rejected: 2018 boundaries, 1 GB.
  - **Barangays and cities by rules, LLM only for streets/sitios/landmarks** (owner, 2026-11-10). The eval compares rules and LLM on the same labelled set.
  - **Runs on my PC** (owner, 2026-11-10): one `serve` process, catch-up on start. Oracle Always Free rejected (idle-reclaim rule would hit a mostly idle app). A static demo snapshot on GitHub Pages comes in Phase 6.
  - **Polling:** checks at 07:00 and 17:00 Manila, plus one fetch of the current week's post right before each alert run (Phase 3), so ≤ ~4 requests a day (owner, 2026-11-10). Sitemap `lastmod` is date-only, so it can't catch a second same-day edit.
  - **Identity on re-parse:** matching passes (exact → same areas, new time → same time, new areas → removed/added) instead of date+time+areas, so a time change is a change, not delete+add.
  - **Map scope:** VECO's 8 towns only; Lapu-Lapu and Cordova (MECO) shown as not covered.
  - **GitHub repo private for now** (owner, 2026-11-10).
  - **Local Qwen (Ollama) is the default LLM; Claude Haiku is an optional eval arm** (owner, 2026-11-10). A claude.ai plan gives no API access, and the project must not depend on a paid service. The eval runs rules vs local Qwen, plus Haiku only when `ANTHROPIC_API_KEY` is set (skipped and noted otherwise). The pipeline uses whichever of rules / Qwen wins.

- **2026-11-10** — Spec approved. Phase 1 plan written: `docs/superpowers/plans/2026-11-10-phase1-ingest-parser.md` (10 tasks, TDD): scaffold + CI, fixtures, normalize/timeparse, extract, parser with golden tests, DB, upsert, discover/fetch, CLI + serve, live backfill and report. Start it with `prompts/phase1.md`.
  - **One session per phase** (owner, 2026-11-10): a phase's tasks are no longer split across sessions; one prompt per phase (`prompts/phaseN.md`), review at the end of the phase.
  - Planning caught one inconsistent slug: `interruption-june-22-28-2025` has no `service-` prefix, so discovery matches slugs containing `interruption` (151 posts).
  - The text hash skips everything before the first date heading, because `5 days ago` / `Updated: 6 hours ago` sit inside the post and would make every fetch look like an edit.

- **2026-11-11** — **Phase 1 Tasks 1–9 done** (scaffold → CLI), 153 tests + 1 live test passing, ruff clean.
  - **Fixtures turned up a third areas layout** (stopped and asked, owner decided 2026-11-11): mid-June → early August 2025 posts list `Barangays:` / `Streets:` / `Sitios:` / … one per line inside the areas cell, after a repeated `Areas Affected:`. One normal post also wraps its areas over two lines. Rule: `areas_raw` = every line of the Areas cell, joined with `\n`. Spec §3.3 and §5.4 amended. 12 fixtures instead of 10.
  - **Recon was wrong about maps:** `…september-27-october-3-2026` has a Wix thumbnail in all 30 `Map:` cells. Stored as the original `static.wixstatic.com/media/<id>` URL so resize parameters aren't edits.
  - Also seen and handled: status in Unicode bold `𝐂𝐀𝐍𝐂𝐄𝐋𝐋𝐄𝐃` with no dash (NFKC), `(2.5hrs)` / `(8 hrs)`, `:30` times, a `copy-of-` slug that is the only post for its week.
  - Each post is one DB transaction, so an interrupted backfill resumes cleanly; `reparse` logs changes only for a version newer than the outages' source, so a parser fix is never reported as a VECO edit.

- **2026-11-11** — **Live backfill: 151 posts fetched, 113 parsed (2,112 outages: 127 cancelled, 29 additional), 38 failed, 45 warnings, 0 fetch errors** (~19 min, one DNS error recovered by the retry). Stopped for the owner: the failures are formats the spec doesn't list.

### Known issues
- **38 posts fail to parse** (2026-11-11 backfill), each on its first unknown line: two time ranges in one line (7), `CANCELLED` on the date heading (3), other heading forms (9), unclosed `(8hrs` (5), and 14 one-offs (`12:00 NN`, `A.M.`, `5:0 0 PM`, `(4hrs))`, `Time:` repeated in the value, `CANCELLEDD`, `(ADDITIONAL)`, `ADDITIONAL-CANCELLED`, `REVISED`, `(1hr 30mins)`, `ime:`, a missing `Time:` label, a second `Purpose:`). Waiting on the owner's decisions.
- **Some stored dates are probably wrong**: VECO typos in headings (`November 7, 2024 (Saturday)` in the Dec 7–11 2024 post, `February 25, 2025 (Wednesday)` in Feb 1–7, `January 15-26`) and AM/PM slips (`8hrs` label, 20 h span). Today they're stored with a warning. Waiting on the owner's decision.

### Lessons for later phases
_None yet._

## Phase 0 — Setup
- [x] Recon, brainstorm, ROADMAP, Phase 1 spec.
- [x] Phase 1 plan and hand-off prompt.
- [ ] Git repo (private, `WMGamponia`), `pyproject.toml`, ruff + pytest, `.github/` (copilot instructions, PR template, prompts), CI on Ubuntu + Windows. → all done locally except the GitHub repo (waits for the owner's OK).

## Phase 1 — Ingest, parse, store, backfill
- [x] Saved real posts as fixtures covering every recon variation. → 12 posts + sitemap in `tests/fixtures/`, README lists what each covers.
- [x] Text normalizer and rule-based parser (date headings, times incl. overnight and explicit-date form, status, purpose, raw areas, optional map image). → `normalize`, `timeparse`, `extract`, `parse`; 12 golden files (3 by hand).
- [x] SQLite schema with numbered migrations: `posts`, `post_versions`, `outages`, `outage_changes`. → `migrations/001_init.sql` + `meta`.
- [x] Re-parse upsert with matching passes and a change log. → `upsert.match` + `db.apply_changes`.
- [x] Sitemap discovery + polite fetcher; `backfill`, `check`, `reparse`, `serve` (scheduler only). → plus `report`.
- [ ] Backfill all posts; report per-post outage counts, warnings, failures.

## Phase 2 — Areas and evals
- [ ] Rule-based barangay/city extraction for both layouts and two-town lines.
- [ ] PSGC gazetteer from the pinned boundary release; alias table (e.g. Duljo Fatima → Duljo, Quiot → Quiot Pardo, Tugbungan → Tugbongan); unmatched → review queue, never dropped.
- [ ] Street/sitio/landmark extraction behind one interface: rule-based splitter, local Qwen via Ollama (default LLM, JSON-schema output), Claude Haiku (optional, only with an API key).
- [ ] Hand-label 30–50 historical "Areas Affected" strings; pytest eval reports barangay precision/recall and PSGC match rate (rules), and street precision/recall for rules vs Qwen (vs Haiku when a key is set). Pick the default from the results. Results in README.

## Phase 3 — Telegram alerts (my barangay)
- [ ] Bot, alert at ~20:00 the evening before and ~06:00 the morning of, and on cancelled / time changed / added / removed.
- [ ] Fresh fetch of the current week's post right before each alert run; catch-up alerts on start for outages that haven't begun.
- [ ] "May affect you" wording.

## Phase 4 — Map
- [ ] FastAPI serves the week's outages as one GeoJSON; Leaflet + OSM tiles, vanilla JS.
- [ ] Barangay shading (soft fill for "portion of"), day tabs, hourly slider, play button, defaults to now, cancelled greyed and toggleable, popup with time, purpose, streets, link to post.

## Phase 5 — Streets, history, multi-user
- [ ] Street highlighting via Overpass inside the barangay polygon, fallback to shading.
- [ ] History mode (pick a past week) and range heatmap of outage hours per barangay; stats (most-affected barangays, average duration, worst months).
- [ ] Multi-user subscriptions.

## Phase 6 — Ship it
- [ ] README: what it does, architecture, eval results, screenshots/GIF, how to run, start-at-login guide.
- [ ] Static demo snapshot (GeoJSON + map) on GitHub Pages.

## Phase 7 — Later
- [ ] MCWD water interruptions.

---

## Stack

| Layer | Choice |
|---|---|
| Fetching | httpx, sitemap discovery |
| Parsing | selectolax + regex state machine (no LLM) |
| Storage | SQLite via stdlib `sqlite3`, numbered migrations |
| Scheduling | APScheduler in the `serve` process |
| Areas | Rules + PSGC gazetteer; streets by rules or local Qwen (Ollama), whichever the eval picks; Claude Haiku optional in the eval |
| Alerts | python-telegram-bot |
| Map | FastAPI, Leaflet + OSM tiles, vanilla JS |
| Quality | pytest, ruff, GitHub Actions |
