# Cebu Brownout Tracker — Roadmap

Turns Visayan Electric (VECO) scheduled power interruption advisories into per-barangay Telegram alerts, an interactive map of the VECO franchise area with a time slider, and historical stats.
Runs on my own PC: **Python + httpx/selectolax + SQLite + APScheduler**, later FastAPI, Leaflet and a Telegram bot. The LLM part is small, deliberate and measured with evals.

> Be gentle with visayanelectric.com: discover posts from the sitemap, one request at a time, a named User-Agent, at most ~4 requests a day once backfilled.

---

## Status

**Current phase:** Phase 0 — Setup (in progress).
**Source:** `https://www.visayanelectric.com/blog-posts-sitemap.xml` → posts whose slug contains `service-interruption` (151 posts, Jan 2024 → now).

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

### Known issues
_None yet._

### Lessons for later phases
_None yet._

## Phase 0 — Setup
- [x] Recon, brainstorm, ROADMAP, Phase 1 spec.
- [ ] Phase 1 plan with session schedule and hand-off prompts.
- [ ] Git repo (private, `WMGamponia`), `pyproject.toml`, ruff + pytest, `.github/` (copilot instructions, PR template, prompts), CI on Ubuntu + Windows.

## Phase 1 — Ingest, parse, store, backfill
- [ ] Saved real posts as fixtures covering every recon variation.
- [ ] Text normalizer and rule-based parser (date headings, times incl. overnight and explicit-date form, status, purpose, raw areas, optional map image).
- [ ] SQLite schema with numbered migrations: `posts`, `post_versions`, `outages`, `outage_changes`.
- [ ] Re-parse upsert with matching passes and a change log.
- [ ] Sitemap discovery + polite fetcher; `backfill`, `check`, `reparse`, `serve` (scheduler only).
- [ ] Backfill all posts; report per-post outage counts, warnings, failures.

## Phase 2 — Areas and evals
- [ ] Rule-based barangay/city extraction for both layouts and two-town lines.
- [ ] PSGC gazetteer from the pinned boundary release; alias table (e.g. Duljo Fatima → Duljo, Quiot → Quiot Pardo, Tugbungan → Tugbongan); unmatched → review queue, never dropped.
- [ ] LLM (Claude Haiku, behind a swappable interface) extracts streets, sitios and landmarks only.
- [ ] Hand-label 30–50 historical "Areas Affected" strings; pytest eval reports barangay precision/recall (rules vs LLM), PSGC match rate, street precision/recall. Results in README.

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
| Areas | Rules + PSGC gazetteer; Claude Haiku for streets only (swappable) |
| Alerts | python-telegram-bot |
| Map | FastAPI, Leaflet + OSM tiles, vanilla JS |
| Quality | pytest, ruff, GitHub Actions |
