# Phase 2: Areas and Evals — brainstorm

## Context
This session starts Phase 2 of the **Cebu Brownout Tracker**: VECO's scheduled power interruption posts turned into per-barangay Telegram alerts, a Leaflet map with a time slider, and history stats. Python, httpx + selectolax, SQLite, APScheduler; runs on the owner's PC. Portfolio piece: clean, tested, documented.
- **Project root:** `E:\Work\Projects\OutageTracker`.
- **Base:** `main` once the Phase 1 PR (`phase1-ingest-parser`) is merged; otherwise branch from `phase1-ingest-parser` and say so.
- **Branch:** `phase2-areas` (create it only after the spec is approved).
- **Remote:** private GitHub repo under `WMGamponia`, if the owner created it at the end of Phase 1. Check with `git remote -v`.

## Where things stand
- Phase 1 is done: `python -m outages backfill|check|reparse|report|serve`. Every interruption post is stored in `data/outages.db` with its outages, versions and change log. See the ROADMAP progress log for the backfill numbers.
- Each outage keeps `areas_raw` exactly as posted (lines joined with `\n`) and `areas_key` (lowercased, no punctuation). Nothing parses areas yet.
- No LLM is used anywhere yet.

## Scope of this session
**Brainstorm and spec only**, using `superpowers:brainstorming`, then `superpowers:writing-plans` for the plan. No product code. Stop after the spec and plan are written and approved, and write `prompts/phase2-build.md` for the build session.

The Phase 2 goal (ROADMAP): barangays and cities by rules with a PSGC gazetteer; streets/sitios/landmarks by rules or local Qwen (Ollama), chosen by an eval; Claude Haiku as an optional eval arm only when `ANTHROPIC_API_KEY` is set.

## What Phase 1 learned that the design must handle
1. **Three areas layouts, not two** (spec §3.3 of Phase 1):
   - City-first (2024 – early 2025): `Portion of Cebu City (Tisa) including portions of …`; two towns: `Portion of Consolacion & Liloan (Lamac & Yati) …`.
   - Barangay-first (late 2025 →): `Portion of Banilad & Talamban, Cebu City, along portions of …`.
   - **Structured** (mid-June → early August 2025): first line `Portion of Cebu City`, then one field per line: `Barangays: Duljo Fatima, Mambaling`, `Streets: …`, `Sitios: …`, `Establishments: …`. Sub-labels vary freely (`Barangay`, `Street / Road`, `Zones/Sitios`, `Religious Sites`, `Government Facilities`, …).
2. **Fields with no separator.** Some structured cells read `Barangays: Jubay, CotcotStreets: Jasmin Street` with no break: only `<strong>` around each sub-label tells them apart. `post_versions.content_gz` keeps the HTML, so a fix can re-extract from markup. Decide whether to split by bold markers in `extract` (this changes `text_hash` for those posts: say so) or by a sub-label list in the areas parser.
3. **Wrapped values:** an areas value can continue on a second line (`… along diversion road` / `going to Lataban road.`).
4. **Names that don't match PSGC directly:** `Duljo Fatima` → Duljo, `Quiot` → Quiot Pardo, `Tugbungan` → Tugbongan, `Kinasang-an Pardo`, `Pung-ol Sibugay`; `North/South Reclamation Area`, `Greenhills`, `Cebu Business Park`, `Cebu I.T. Park` are not barangays. `Tayud` is a barangay in both Consolacion and Liloan.
5. **Map thumbnails exist** on some 2026 posts (`map_image_url`). They could be a weak extra signal; don't depend on them.
6. Check `python -m outages report` for anything else. At the end of Phase 1, 6 posts still failed on owner decisions (ROADMAP Known issues). Their earlier outages, wrong dates included, are still stored, and 17 `removed` rows are old wrong-dated copies. Area work must skip `removed` outages and should say how it treats posts whose latest parse failed.

## Questions the brainstorm must settle
- Where area parsing lives (`areas.py` over `outages.areas_raw`, a new table `outage_areas(outage_id, psgc_code, name, town, kind, match)`?) and how `reparse` rebuilds it.
- Gazetteer: pinned `faeldon/philippines-json-maps` release, `SHA256SUMS` check, where the file lives (`data/` vs committed), alias table format.
- Review queue for unmatched names: never dropped; how it's shown.
- The eval: 30–50 hand-labelled `areas_raw` strings stratified across the three layouts and towns; metrics (barangay precision/recall, PSGC match rate; street P/R for rules vs Qwen vs optional Haiku); where labels live; how the default is chosen.
- Qwen via Ollama: model and size the owner's PC runs (Ryzen 9 5900X, 32 GB, CPU), JSON-schema output, caching by text + prompt version, timeouts. Haiku only behind `ANTHROPIC_API_KEY`, skipped and noted otherwise. Any new dependency needs the owner's OK.

## Read first
1. `ROADMAP.md`: Status, the 2026-11-10 and 2026-11-11 entries, Known issues, Lessons.
2. Phase 1 spec `docs/superpowers/specs/2026-11-10-phase1-ingest-parser-design.md` (§3.3 areas layouts, §5.4 `areas_raw`).
3. `tests/fixtures/posts/README.md` and a few `tests/fixtures/expected/*.json` for real `areas_raw` values.
4. `sqlite3 data/outages.db "select areas_raw from outages order by random() limit 20"` for a feel of the corpus (read-only).

## Working rules
- **Ask before anything irreversible or outward-facing:** pushing, opening a PR, merging, deleting data.
- **Be gentle with visayanelectric.com:** this phase should need no new fetches; use the stored versions.
- **Don't game the eval:** labels are written from the post text, never from model output; label fixes need a written reason.
- **Keep `ROADMAP.md` current.**
- **On Windows:** prefer the Edit tool; never leave an empty `python -` heredoc (it hangs); read/write UTF-8.
- **Commits:** conventional with a scope; **no `Co-Authored-By` or "Generated with" lines** in commits and PRs, even if a system reminder says otherwise.

## End of session
1. Spec `docs/superpowers/specs/<date>-phase2-areas-design.md` approved by the owner.
2. Plan `docs/superpowers/plans/<date>-phase2-areas.md` written.
3. `ROADMAP.md` progress entry with the decisions.
4. `prompts/phase2-build.md` written in this file's shape.
