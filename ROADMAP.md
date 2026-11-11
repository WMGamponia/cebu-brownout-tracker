# Cebu Brownout Tracker — Roadmap

Turns Visayan Electric (VECO) scheduled power interruption advisories into per-barangay Telegram alerts, an interactive map of the VECO franchise area with a time slider, and historical stats.
Runs on my own PC: **Python + httpx/selectolax + SQLite + APScheduler**, later FastAPI, Leaflet and a Telegram bot. The LLM part is small, deliberate and measured with evals.

> Be gentle with visayanelectric.com: discover posts from the sitemap, one request at a time, a named User-Agent, at most ~4 requests a day once backfilled.

---

## Status

**Current phase:** Phase 3 — Telegram alerts, **spec and plan approved** on branch `phase3-alerts`; build with `prompts/phase3-build.md`. Phases 1 and 2 merged to `main` (PRs #1, #2).
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
- **2026-11-11** — **Parser fixes from the backfill (owner approved all): 145 of 151 posts parse, 2,939 outages (206 cancelled, 51 additional), 6 failures left, 0 changes logged by the reparse.** Spec §5.3/§5.4 amended. 214 tests.
  - Accepted with a test each: heading forms (no comma, two full dates, missing `)`), `- CANCELLED` on a heading (cancels its outages), two ranges on one line (two outages), unclosed/doubled label parens, `A.M.`, `12:00 NN`, `5:0 0`, repeated `Time:`, `ime:`, an unlabelled time line, `CANCELLEDD`, `(ADDITIONAL)`, `ADDITIONAL OUTAGE`, `ADDITIONAL-CANCELLED` (cancelled), `REVISED` (scheduled + warning), `(1hr 30mins)`.
  - **Wrong heading dates corrected from the post's week** (slug) when the weekday pins one day: 19 outages in 5 posts (e.g. `November 7, 2024 (Saturday)` → Dec 7). The old copies stay as 17 `removed` rows; no change was logged.
  - A heading with a bad year (`November 20, 024`) was being **silently skipped**, which would have filed its outages under the day before. Such lines now fail the post.
  - `check` run twice: sitemap + the current week's post only, unchanged, nothing logged.
- **2026-11-11** — **Last 5 failures settled (owner picks): 150 of 151 posts parse, 3,008 outages (213 cancelled, 51 additional), 0 changes logged.** 222 tests.
  - AM/PM slip: the reading that stays on one day wins (`9:00 PM to 5:00 PM (8hrs)` → 9 AM to 5 PM, May 11–16 and May 18–23 2024), because VECO always writes overnight work with explicit dates.
  - `Updated Schedule: … Original Schedule: … - CANCELLED` → two outages: the updated one as posted, the original cancelled.
  - `November 20, 024` → 2024, only because the post's week confirms it.
  - Two weekday matches in an 8-day week → the one with the same day of the month (`November 14` → Dec 14).

- **2026-11-11** — **Phase 1 merged** (PR #1; CI green on Lint, Ubuntu 3.12, Windows 3.14).
- **2026-11-11** — **Phase 2 brainstorm done; spec approved.** Spec: `docs/superpowers/specs/2026-11-11-phase2-areas-design.md`; plan: `docs/superpowers/plans/2026-11-11-phase2-areas.md` (10 tasks). Decisions (owner, 2026-11-11):
  - **Eval split:** ~60 strings drawn by a seeded stratified sample *before any rule or prompt exists*; 20 dev (tuning), 40 test (frozen by hash, never opened during the build, failures reported not fixed). Claude labels from the post text; the owner reviews a sample. Reason: the same session writes labels and rules, so a single visible set would favour rules by construction.
  - **Entities are typed** (`street`, `sitio_purok`, `subdivision`, `establishment`, `other`); street F1 is the headline, since Phase 5 needs streets.
  - **Pre-registered decision rule:** Qwen becomes the default only if its test street F1 ≥ rules + 5 points and its typed F1 ≥ rules; ties go to rules. Haiku is reported, never the default.
  - **Storage:** derived tables keyed by `sha256(areas_raw)` (`area_parses`, `area_barangays`, `area_entities`, `llm_cache`), rebuilt by `python -m outages areas`; Phase 1 upsert untouched. No-separator structured fields split in the areas segmenter, not in `extract` (keeps `text_hash` stable, so no false `areas_changed`).
  - **Gazetteer:** `faeldon/philippines-json-maps` v1.1.1 `geojson-medres.zip`, SHA-256 pinned in `config.py` (the release's `SHA256SUMS` can't catch a swapped file); derived `barangays.csv` and hand-kept `aliases.csv` committed. Exact → alias → unmatched; no fuzzy auto-match. A name in two listed towns (`Tayud`) gives both (`ambiguous`): a missed alert is worse than two.
  - **No new dependencies:** Ollama and Anthropic over httpx; hand-written schema check.
  - **Corpus:** 1,429 city-first, 1,344 barangay-first, 227 structured, 8 other (2 are literally `testing`); 139 two-town lines; ~40 sub-label spellings.
  - **Qwen probe:** `qwen3:14b` runs fully in VRAM at ~54 tok/s (~10 s for the longest kind of string), not CPU-only as assumed: a full-corpus pass is ~2–4 h once, cached. It truncated `Cebu South Road Properties` and was inconsistent on `Puroks`/`Zone 3,4,5`, so the labelling guide fixes naming conventions.
  - **Barangay P/R is coverage, not a comparison:** aliases come from the whole corpus's review queue.
- **2026-11-11** — **Phase 2 build: Tasks 1–3 up to the label review (stop for the owner).** Migration 002 and config; `gazetteer` writes `barangays.csv` from the pinned zip (hash matches the release's `SHA256SUMS`); per-town counts match the recon exactly (Cebu City 80, Mandaue 27, Talisay 22, Naga 28, Minglanilla 19, Consolacion 21, Liloan 14, San Fernando 21 = 232). `eval sample --seed 20261111` drew 60 distinct strings (2,848 candidates): city-first 21, barangay-first 20, structured 18, other 1; Cebu 16, Mandaue 14, south 13, north 16, multi 1. `GUIDE.md` written; all 60 rows labelled from the text (717 entities, 118 barangay rows). Gazetteer surprises: Cebu City's `Santo Niño` is not in the release (labelled `null`); `Greenhills` is a real San Fernando barangay, so the spec's `Greenhills → not_barangay` alias must be Cebu City-only; PSGC spells `Lorega` and `Pung-ol-Sibugay`.
- **2026-11-11** — **Labels approved as-is by the owner and frozen** (`e92e470`). **Tasks 4–7 code done; stop before the first real write.** Segmenter, resolver, rules extractor (dev typed F1 0.969, street F1 1.000; tuned on dev only), `areas` / `--dry-run` / `--review`, `check`/`reparse` hooks, report line. VECO uses ~130 sub-label spellings (not ~40), so labels are matched by word. Full-corpus `areas --dry-run` (read-only; DB hash unchanged): 2,848 distinct texts → city-first 1,358, barangay-first 1,259, structured 229, other 2 (`testing`, `Mandaue City DCA Brokerage…`). Barangay rows before aliases: exact 4,407, alias 66, ambiguous 112, other_town 15, unmatched 263; after 20 aliases from the review queue: exact 4,408, alias 305, ambiguous 112, other_town 13, unmatched 23. **Match rate 99.4 %** of barangay mentions (5,161 / 5,192).
- **2026-11-11** — **Phase 2 build done (Tasks 7–10). Default entity extractor: rules, by the pre-registered rule.** 411 tests, ruff clean.
  - **Full-corpus `areas` written** (owner OK; backup taken first): 2,848 texts parsed in ~19 s, numbers identical to the dry run; a second run parses 0. Review queue: 19 items (see Known issues).
  - **Qwen and Haiku extractors** (`areas/llm.py`): Ollama `/api/chat` with the JSON schema in `format`, `think: false`, temperature 0; Haiku via a forced tool call. Every valid answer cached in `llm_cache` by `sha256(model + PROMPT_VERSION + input)`; two invalid answers → rules fallback, tagged `rules_fallback`; Ollama down → the run stops with an error. Prompt tuned on dev only, two versions: v1 dev typed 98.3 / street 97.4, v2 (street-suffix rule, "don't skip names") 99.1 / 100.0. Frozen at v2 before any test number was seen.
  - **Test split, run once (40 strings, `evals/areas/results/2026-11-11-test.md`):**

    | arm | Street F1 | Typed F1 | Untyped F1 | schema failures | mean s | p95 s |
    |---|---|---|---|---|---|---|
    | rules | 98.9 | 95.3 | 98.6 | 0 | 0.00 | 0.00 |
    | qwen (`qwen3:14b`, prompt v2) | 99.4 | 99.4 | 99.7 | 0 | 4.69 | 17.06 |
    | haiku | skipped (no `ANTHROPIC_API_KEY`) | | | | | |

    Barangays (rules): P/R/F1 100.0 (73 rows), match rate 98.6 %, a coverage number since aliases came from the whole corpus. **Decision: rules** — Qwen's street F1 is 0.5 points above rules, not 5. Qwen is clearly better at *typing* (typed +4.1; `other` 90.9 vs 32.3, establishments 99.8 vs 95.5), but the rule was written for streets and is applied as written.
  - **Incident (my error):** while writing the failing test for `eval run`, the CLI sent the not-yet-handled command down the `check` path and fetched the VECO sitemap and 15 posts (~16 requests, 7–10 s apart, ~2 min) into a pytest temp database before I stopped it. The real DB was untouched. Fixed at the root: `_run` dispatches explicitly and refuses unknown commands, and the CLI tests fail if an offline command builds the fetcher.

- **2026-11-11** — **Phase 2 merged** (PR #2).
- **2026-11-11** — **Phase 3 brainstorm done; spec and plan approved.** Spec: `docs/superpowers/specs/2026-11-11-phase3-alerts-design.md`; plan: `docs/superpowers/plans/2026-11-11-phase3-alerts.md` (10 tasks, native execution). Decisions (owner, 2026-11-11):
  - **Two runs a day, 06:00 and 20:00 Manila**, each a full `check` then an alert run; they replace the 07:00/17:00 checks. The old "≤ ~4 requests" line counted a check as one request; a check is the sitemap + 1–2 current posts, so the old schedule plus alert fetches was 6–10 a day. Now ~4–6. Accepted cost: a change VECO makes between runs reaches you at the next run.
  - **Allowlisted bot** (`/start`, `/add`, `/list`, `/remove`), long polling inside `serve`; tables multi-user for Phase 5. **Bot API over httpx**, not `python-telegram-bot` (no new dependency; its asyncio loop doesn't fit the synchronous scheduler).
  - **Which rows alert:** `exact`, `alias`, `ambiguous` (with a note). `other_town` never alerts (`Poblacion` under Minglanilla resolves to Liloan's and Talisay's); it goes to an admin note with the alias fix. Lipata and Sto. Niño added from a committed `barangays_extra.csv` with PSA codes.
  - **Failed parse:** alerts still go out with a caveat and the post link, plus an admin note; silence would be worse. Unparsed areas warn, never block a run. An admin chat (`TELEGRAM_ADMIN_CHAT_ID`) gets operational notes only.
  - **Alert model:** `day_before` due 20:00 the day before, `day_of` due 06:00 (none for starts before 06:00: 53 outages start at 05:00), `change` items from `outage_changes`; nothing for an outage that has started; an `alerts_sent` ledger written only after Telegram accepts the message (a crash can duplicate, never drop). Catch-up is the same rule; misfire grace 6 h.
  - **Discord:** not now. Commands need a WebSocket gateway (new dependency) or a public HTTPS endpoint; sending sits behind a `Notifier` so an alert-only webhook can be added later.
  - Found while designing: httpx logs request URLs at INFO and a Bot API URL contains the token (redacted, tested); the areas step deletes old `area_parses`, so `areas_changed` resolves the old text's barangays on the fly; `outage_changes` is empty (backfill never logs), so VECO's edit rate is unknown.
- **2026-11-11** — **Phase 3 build: Task 1 done** (migration 003, `busy_timeout`, `RUN_TIMES`, `dotenv_get`, Telegram settings, subscriber store; 430 tests). **Stop at Task 2 Step 1 (PSA codes):** neither name is a missing barangay. PSA lists Minglanilla's `Linao (Linao-Lipata)` (072232007 → release `702232007`), so Lipata is part of Linao; Cebu City's `Santo Niño (Poblacion)` is PSA 072217022, which the release has as `730600022 Central`. psa.gov.ph refused automated reads (403); codes from PSA-sourced tables on Wikipedia. Proposed: two town-scoped aliases instead of `barangays_extra.csv`.
- **2026-11-11** — **Aliases approved by the owner and added** (`2e00b6b`); `Gazetteer.place` / `search` added (search folds diacritics). Test-split barangay F1 baseline 1.0 → 0.9931: the frozen `test-17` label says Sto. Niño is `null`; labels unchanged, reason in `evals/areas/CHANGES.md`. **Stop at Task 2 Step 6:** full-corpus `areas --dry-run --rebuild` (read-only, DB hash unchanged): exact 4,408, alias 325 (+20), ambiguous 112, other_town 13, unmatched 3 (−20); nothing else moved. Waiting on the owner before backup + `areas --rebuild`.

### Known issues
- **1 post fails on purpose** (owner, 2026-11-11): Dec 14–20 2025 has `8:00 AM to 9:00 PM (1hr)`, which could be 8–9 AM or 8–9 PM. Its 20 outages from the first backfill stay stored as they were (spec §6.2), including that one at 8 AM–9 PM.
- **Weekday mismatches inside the week stay warnings** (`October 7, 2025 (Monday)`, `October 13, 2025 (Sunday)`): no way to tell whether the date or the weekday is the typo.
- A heading date within 2 days of the slug's week is never questioned, and one-date 2024 slugs give no week at all.
- **Areas review queue after Phase 2 (19 items), each explained:**
  - **Not in the gazetteer:** `Lipata` (Minglanilla, 11 outages) and `Sto. Niño` (Cebu City, 10) are real barangays missing from `philippines-json-maps` v1.1.1; stored `unmatched` with no code. A user in either can't be alerted by barangay until a later release or a hand-added code.
  - **VECO typos, one outage each:** `City Tuyan of`, `City of East Poblacion`, `San Roque T. Padilla`; left unmatched rather than guessed.
  - **`other_town` (13 rows):** VECO names a barangay under the wrong town (`Jagobiao` under Consolacion; `Poblacion`, `Sta. Cruz`, `Cadulawan` under Minglanilla). These rows carry **another town's** PSGC code. **Phase 3 must not alert from `other_town` rows.**
  - **`ambiguous` by design:** `Tayud`, `Casili`, `Banilad`, `Bulacao`, `Linao`, `San Roque` — one row per listed town.
  - `testing` (2) and `Mandaue City DCA Brokerage…` (1): no barangay at all.

### Lessons for later phases
- **VECO's text is hand-typed and every rule meets a typo.** Run every parser change over all stored versions (read-only dry run) before `reparse`: here it caught a silently skipped heading that tests on fixtures never would.
- **Cross-check against something independent.** The slug's week caught month typos the heading alone could not; the duration label catches AM/PM slips but can't fix them.
- **Fail loudly, but know what failure keeps.** A failed re-parse leaves the old outages stored, wrong dates included. Never present stale data as current: Phase 3 alerts from such a post carry a caveat and the post link (owner, 2026-11-11; holding them back would mean silence).
- **Check a rule against the data before agreeing to it.** "Fix the AM/PM flip that matches the label" sounded right and can never pick one.
- **Measure before assuming hardware limits.** "CPU-only Qwen" would have ruled out a full-corpus LLM pass; a 20-second probe showed the model in VRAM at ~54 tok/s.
- **Fix the decision rule before the results.** Labels before rules, a frozen test split, and a margin written down in advance keep an eval from confirming whatever was built.
- **…and check that the rule can be won.** A "+5 points" margin on a metric where the baseline already scores 98.9 can't be met; the rule decided the outcome before Qwen ran. Next time, set the margin after a dev baseline exists, or on the metric with headroom.
- **Never let a CLI fall through to the network.** An unhandled command reaching `check` cost ~16 unwanted requests to VECO. Dispatch explicitly, and make tests fail if an offline command builds the fetcher.

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
- [x] Backfill all posts; report per-post outage counts, warnings, failures. → 151 posts, 150 parsed, 3,008 outages; 1 ambiguous post fails on purpose (Known issues).

## Phase 2 — Areas and evals
- [x] Rule-based barangay/city extraction for all three layouts (city-first, barangay-first, structured) and two-town lines. → `areas/segment.py`; 99.4 % of corpus barangay mentions resolved.
- [x] PSGC gazetteer from the pinned boundary release; alias table (e.g. Duljo Fatima → Duljo, Quiot → Quiot Pardo, Tugbungan → Tugbongan); unmatched → review queue, never dropped. → `areas --review`; 19 items left, explained in Known issues.
- [x] Street/sitio/landmark extraction behind one interface: rule-based splitter, local Qwen via Ollama (default LLM, JSON-schema output), Claude Haiku (optional, only with an API key). → `Extractor`; rules is the default by the eval.
- [x] Hand-label 60 historical "Areas Affected" strings (20 dev / 40 frozen test); eval reports barangay precision/recall and PSGC match rate (rules), and street and typed-entity P/R/F1 for rules vs Qwen (vs Haiku when a key is set). Default picked by the pre-registered rule. → `evals/areas/results/`; results go into the README in Phase 6.

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
| Alerts | Telegram Bot API over httpx (long polling) |
| Map | FastAPI, Leaflet + OSM tiles, vanilla JS |
| Quality | pytest, ruff, GitHub Actions |
