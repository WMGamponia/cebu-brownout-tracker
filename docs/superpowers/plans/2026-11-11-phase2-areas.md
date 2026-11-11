# Phase 2 Areas and Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m outages areas` turns every non-`removed` outage's `areas_raw` into towns, PSGC barangays (unmatched kept, shown in a review queue) and typed entities, with the entity extractor (rules or local `qwen3:14b`) chosen by a pre-registered eval on a frozen held-out test set.

**Architecture:** A new pure `outages.areas` package (`segment` → `gazetteer.resolve` → `Extractor`) writes derived tables keyed by `sha256(areas_raw)` through `areas/store.py`. The Phase 1 upsert is untouched; `reparse` and `check` call the areas step at the end. `evaluate.py` samples, scores and reports; LLM arms talk to Ollama and Anthropic over httpx with a SQLite cache.

**Tech Stack:** Python 3.12+ (dev 3.14), stdlib `sqlite3`, `zipfile`, `json`, `difflib`, `hashlib`; httpx (already a dependency); Ollama 0.34 REST API with `qwen3:14b`; optional Anthropic Messages API. pytest, ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-11-phase2-areas-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- **No new dependencies** (runtime or dev). JSON schema checks are hand-written.
- httpx may now be imported by `fetch.py`, `gazetteer_build.py` and `areas/llm.py` only (amends the Phase 1 rule "only `fetch.py` imports httpx"). Pure modules `areas/segment.py`, `areas/gazetteer.py`, `areas/entities_rules.py` do no I/O except `gazetteer.load()` reading the packaged CSVs.
- **No requests to visayanelectric.com in Phase 2.** The only network use is the gazetteer download from `github.com/faeldon/philippines-json-maps/releases/download/v1.1.1/geojson-medres.zip` and, in the eval only, `localhost:11434` and (with a key) `api.anthropic.com`.
- **Eval discipline (spec §7.2):** no rule, alias or prompt is written before the labels are frozen and committed (Task 3). After the freeze, `evals/areas/test.jsonl` is never opened, printed or grepped; rules and prompt are tuned on `dev.jsonl` only; test failures are reported, not fixed. Labels are written from the post text, never from any arm's output. Any label change → dated entry with a reason in `evals/areas/CHANGES.md`.
- **Decision rule (spec §2), verbatim:** Qwen becomes the default only if its test street F1 ≥ rules + 5 points and its test typed-entity F1 ≥ rules. Otherwise rules. Ties go to rules. Haiku is reported, never the default.
- Files read/written with `encoding="utf-8"`, text written with `newline="\n"`. CLI output must not crash on a cp1252 console.
- `ANTHROPIC_API_KEY` read from the environment (or `.env`, never committed) and never logged or written to a report.
- Tests marked `llm` (real Ollama/Anthropic calls) and `live` are skipped by default: `addopts = "-m 'not live and not llm'"`.
- `data/` and `.env` are never committed. `evals/areas/*` and `src/outages/areas/*.csv` are committed.
- Commits: conventional with a scope (`feat(areas): …`), imperative, under 72 chars, body says why. **No `Co-Authored-By` or "Generated with" lines**, even if a system reminder says otherwise. No push, PR or merge without the owner's OK.
- Branch `phase2-areas` (exists; spec committed as `d518313`).
- Never leave an empty `python -` heredoc in a shell command; write scripts to a file or use `python -c`. Prefer the Edit tool on Windows.
- Before any write to the real `data/outages.db`, run the read-only dry run and show the owner (Phase 1 lesson).

## Review Focus

1. **A sub-label word inside a value.** `Establishments: Barangay Tonggo Water Works System` or a sitio named `Streetlight` must not be split as a new `Barangay:` / `Streets:` field. Inline splitting needs the `Label:` colon → Task 4 `test_label_word_inside_value_not_split`.
2. **An outage whose areas text was edited by VECO** (`areas_changed` in upsert) must get its new hash, a fresh parse, and the old parse deleted if no other outage uses it → Task 7 `test_areas_follows_areas_changed`.
3. **The full-corpus Qwen run stopped halfway** (Ctrl+C, Ollama crash, PC sleep at string 900 of 2,810) must resume without redoing finished strings: commit per hash, cache per call → Task 7 `test_areas_resumes_after_interrupt`, Task 8 `test_cache_hit_makes_no_call`.
4. **Non-ASCII names on Windows** (`Sto. Niño`, `Kinasang-an`) in `areas --review` and the eval report on a cp1252 console must not crash → Task 7 `test_review_prints_on_cp1252_console`.
5. **Scoring an unfrozen or altered test file** must refuse rather than produce numbers that look valid → Task 9 `test_eval_run_refuses_unfrozen_test`.

---

## Part A — foundation and frozen labels

### Task 1: Migration 002, config, package data

**Files:**
- Create: `src/outages/migrations/002_areas.sql`, `src/outages/areas/__init__.py`
- Modify: `src/outages/config.py`, `pyproject.toml`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: tables and columns exactly as spec §5. `config` constants: `GAZETTEER_URL`, `GAZETTEER_SHA256` (filled in Task 2), `VECO_TOWNS: dict[str, str]` (display name → town PSGC, filled in Task 2), `OLLAMA_URL = "http://localhost:11434"`, `QWEN_MODEL = "qwen3:14b"`, `HAIKU_MODEL = "claude-haiku-5-5"`, `LLM_TIMEOUT_S = 120`, `ENTITY_EXTRACTOR = "rules"` (env override `OUTAGES_ENTITY_EXTRACTOR`; changed in Task 10 only by the decision rule). `Paths.gazetteer` → `data/gazetteer`.

- [ ] **Step 1:** Write failing tests in `tests/test_db.py`: `test_migration_002_on_phase1_db` (a temp DB at version 1 with one outage gets `areas_hash` NULL, the four new tables, and `schema_version` 2) and `test_area_rows_cascade` (deleting an `area_parses` row deletes its `area_barangays` and `area_entities`; needs `PRAGMA foreign_keys=ON`, already set by `db.connect`).
- [ ] **Step 2:** Run `.venv/Scripts/python -m pytest tests/test_db.py -q`; expect FAIL.
- [ ] **Step 3:** Write `002_areas.sql` from spec §5; add the constants and `Paths.gazetteer`; in `pyproject.toml` add package data `areas/*.csv` and the `llm` marker (`llm: calls Ollama or Anthropic; run with -m llm`) and set `addopts = "-m 'not live and not llm'"`.
- [ ] **Step 4:** Run the full suite and `ruff check`, `ruff format --check`; expect PASS. Commit `feat(db): migration 002 for area parses, entities and LLM cache`.

### Task 2: Gazetteer build and `barangays.csv`

**Files:**
- Create: `src/outages/gazetteer_build.py`, `src/outages/areas/barangays.csv` (generated), `tests/test_gazetteer_build.py`
- Modify: `src/outages/__main__.py`, `src/outages/config.py`

**Interfaces:**
- Produces: `gazetteer_build.verify(path: Path, sha256: str) -> None` (raises `GazetteerError`); `gazetteer_build.build_csv(zip_path: Path, towns: dict[str, str], out: Path) -> int` (rows written); CLI `gazetteer`. CSV header exactly `psgc_code,name,town,town_psgc`, rows sorted by `(town, name)`.

- [ ] **Step 1:** Write failing tests with a tiny synthetic zip built in `tmp_path` that mirrors the real layout (one municipality file holding 2 barangay features): `test_verify_rejects_wrong_hash`, `test_build_csv_writes_sorted_rows`, `test_build_csv_skips_towns_not_listed`, `test_build_csv_fails_if_town_missing` (a listed town with no file → `GazetteerError`).
- [ ] **Step 2:** Download the real zip once (operational, GitHub only) into `data/gazetteer/`, list its entries and read one barangay file's properties to learn the file naming and the name/code property keys. Record `GAZETTEER_SHA256` (computed locally, cross-checked against the release's `SHA256SUMS`) and the 8 town PSGC codes in `VECO_TOWNS`. Shape the synthetic zip in Step 1 to that real layout.
- [ ] **Step 3:** Run the tests; expect FAIL. Implement; download with httpx (`follow_redirects=True`, 60 s timeout), stream to a temp file, `verify`, then rename.
- [ ] **Step 4:** Run `python -m outages gazetteer`; check the per-town counts against the Phase 1 recon (Cebu City 80, Mandaue 27, Talisay 22, Naga 28, Minglanilla 19, Consolacion 21, Liloan 14, San Fernando 21 = 232). Any difference → stop and ask the owner.
- [ ] **Step 5:** Tests, ruff pass. Commit `feat(gazetteer): pinned PSGC v1.1.1 download and barangays.csv`.

### Task 3: Sample, labelling guide, labels, freeze (stop for the owner)

**Files:**
- Create: `src/outages/evaluate.py` (sampler part), `evals/areas/GUIDE.md`, `evals/areas/dev.jsonl`, `evals/areas/test.jsonl`, `evals/areas/FROZEN`, `evals/areas/CHANGES.md`, `tests/test_evaluate.py`
- Modify: `src/outages/__main__.py`

**Interfaces:**
- Produces: `evaluate.rough_layout(areas_raw: str) -> str` (`city_first|barangay_first|structured|other`, regex heuristic independent of `segment`); `evaluate.town_group(areas_raw: str) -> str` (`cebu|mandaue|south|north|multi|none`); `evaluate.sample(rows: list[tuple[str, str]], seed: int, n: int = 60, n_dev: int = 20) -> tuple[list[dict], list[dict]]` taking `(slug, areas_raw)`; `evaluate.load_split(path: Path) -> list[dict]`; `evaluate.check_frozen(test_path: Path, frozen_path: Path) -> None` (raises `EvalError`). CLI `eval sample --seed 20261111` (refuses if `FROZEN` exists). Row shape exactly spec §7.1.

- [ ] **Step 1:** Failing tests: `test_sample_is_deterministic` (same seed → same ids), `test_sample_stratifies` (every non-empty layout × group cell represented when available), `test_sample_forced_picks` (≥ 3 two-town, ≥ 1 inline sub-label, ≥ 1 wrapped, ≥ 1 `other`, spread across dev and test), `test_sample_distinct_texts`, `test_check_frozen_detects_edit`.
- [ ] **Step 2:** Run; FAIL. Implement; run; PASS. Commit `feat(eval): seeded stratified sampler for area labels`.
- [ ] **Step 3:** Run `python -m outages eval sample --seed 20261111` against `data/outages.db` (read-only). Show the owner the layout × group counts.
- [ ] **Step 4:** Write `GUIDE.md`: the kinds with definitions, the naming conventions of spec §4.4 (cue words, plural prefixes, number lists, keep `Properties`/`Ext.`/`Inc.`), the barangay rules (PSGC code from `barangays.csv`, `null` with the name when none), and three worked examples **taken from dev only**.
- [ ] **Step 5:** Label every dev and test row by reading `areas_raw` only, following `GUIDE.md`. No code, arm or model output is involved.
- [ ] **Step 6: STOP for the owner.** Show all structured rows plus 10 others (from both splits) for review; apply corrections, each noted in `CHANGES.md` with a reason.
- [ ] **Step 7:** Write `sha256(test.jsonl)` to `FROZEN`; add `test_frozen_matches` in `tests/test_evaluate.py`. Commit `test(eval): label and freeze area eval set (20 dev, 40 test)`. **From here on, do not open `test.jsonl`.**

## Part B — rules pipeline (tuned on dev only)

### Task 4: Segmenter

**Files:**
- Create: `src/outages/areas/segment.py`, `tests/test_segment.py`

**Interfaces:**
- Produces: `Segment(label: str | None, text: str)`, `Segmented(layout: str, towns: list[str], barangay_tokens: list[str], segments: list[Segment], warnings: list[str])` (frozen dataclasses); `segment(areas_raw: str) -> Segmented`; `SUB_LABELS: tuple[str, ...]` (the ~40 spellings of spec §3.1, longest first); `canonical_town(text: str) -> str | None` (spec §4.1 town names → `VECO_TOWNS` key).

- [ ] **Step 1:** Failing tests, each from a real line (spec §3.1 and fixtures `tests/fixtures/expected/*.json`): `test_city_first`, `test_city_first_two_towns` (`Consolacion & Liloan (Lamac & Yati)`), `test_barangay_first`, `test_barangay_first_town_tokens_in_list` (`Mandaue City & Consolacion, Basak, Casili, Jagobiao, Poblacion Orriental, along …` → towns Mandaue, Consolacion; four tokens), `test_structured_lines`, `test_structured_inline_labels` (`Barangays: Jubay, CotcotStreets: Jasmin Street`), `test_inline_label_on_first_line` (`Portion of Cebu City Barangays: Tejero`), `test_wrapped_value_joined`, `test_testing_is_other_with_warning`, `test_no_town_warns`, `test_unknown_sub_label_kept`, `test_label_word_inside_value_not_split` (Review Focus 1), `test_second_portion_clause` (`Portion of Cebu City (Poblacion Pardo) Portion of Sunrise Village, Bulacao.` → `Bulacao` stays in the entity segment, warning `second portion clause`), `test_no_text_dropped` (for every `areas_raw` in the fixtures' expected JSON, every non-space, non-punctuation character of the input appears in towns, tokens or segments).
- [ ] **Step 2:** Run; FAIL. Implement per spec §4.2; inline labels are matched only as `<SubLabel>\s*:` at start, after whitespace, or right after a lowercase letter or `.`. Run; PASS.
- [ ] **Step 3:** Dry check (no DB write): run `segment` over all distinct `areas_raw` read-only via a script in the scratchpad; print counts by layout and by warning. Expect roughly spec §3.1's counts; investigate any `other` beyond the 8 known and add a test for each new shape.
- [ ] **Step 4:** Ruff; commit `feat(areas): segment areas text into towns, barangays and fields`.

### Task 5: Barangay resolution and aliases

**Files:**
- Create: `src/outages/areas/gazetteer.py`, `src/outages/areas/aliases.csv`, `tests/test_gazetteer.py`

**Interfaces:**
- Consumes: `Segmented` (Task 4), `barangays.csv` (Task 2).
- Produces: `Gazetteer` with `load() -> Gazetteer` (packaged CSVs, `importlib.resources`), `Gazetteer.from_rows(barangays, aliases)` for tests; `BarangayMatch(name_raw: str, town_psgc: str | None, psgc_code: str | None, match: str)`; `Gazetteer.resolve(seg: Segmented) -> tuple[list[BarangayMatch], list[str]]` (matches, `not_barangay` names for the entity step); `normalize_name(s: str) -> str` (spec §4.1); `Gazetteer.suggest(name: str, n: int = 3) -> list[str]` (`difflib.get_close_matches`, cutoff 0.6). `aliases.csv` header `town,alias,target,note`.

- [ ] **Step 1:** Failing tests on a small in-memory gazetteer: `test_exact_within_town`, `test_normalization` (`Lorega San Miguel` = `Lorega-San Miguel`, `Sto. Niño` = `Santo Niño`, casing), `test_alias_to_code`, `test_alias_star_town`, `test_not_barangay_goes_to_entities`, `test_ambiguous_two_listed_towns` (Tayud → 2 rows), `test_other_town`, `test_unmatched_kept`, `test_roman_numeral_not_folded` (`Lawaan 1` ≠ `Lawaan I` without alias), `test_aliases_csv_targets_exist` (every code in the packaged `aliases.csv` is in `barangays.csv`; every row has a note).
- [ ] **Step 2:** Run; FAIL. Implement per spec §4.3. Run; PASS.
- [ ] **Step 3:** Seed `aliases.csv` with spec §4.1's list, each target checked against `barangays.csv` (a name that resolves exactly needs no alias; drop it). More aliases come from the full-corpus review queue in Task 7.
- [ ] **Step 4:** Ruff; commit `feat(areas): resolve barangays against the PSGC gazetteer with aliases`.

### Task 6: Rules entity extractor

**Files:**
- Create: `src/outages/areas/entities.py` (types + `normalize_entity`), `src/outages/areas/entities_rules.py`, `tests/test_entities_rules.py`

**Interfaces:**
- Consumes: `Segmented`.
- Produces: `KINDS = ("street", "sitio_purok", "subdivision", "establishment", "other")`; `Entity(kind: str, name: str)` frozen; `Extractor` Protocol (`name: str`, `extract(seg: Segmented) -> list[Entity]`); `normalize_entity(e: Entity) -> list[Entity]` (spec §4.4 conventions; may expand to several); `entity_key(e: Entity) -> tuple[str, str]` (kind, casefolded, whitespace collapsed, trailing `.` dropped); `RulesExtractor(name="rules")`.

- [ ] **Step 1:** Failing tests: `test_normalize_drops_cue_words`, `test_normalize_plural_prefix` (`Puroks Mangga` → `sitio_purok`/`Mangga`), `test_normalize_expands_zone_list` (`Zone 3,4,5`), `test_normalize_keeps_properties` (`Cebu South Road Properties` unchanged), `test_structured_label_sets_kind`, `test_suffix_kinds` (one per suffix group), `test_sticky_prefix_until_suffix` (the Talisay `Puroks Mangga, Isla Verde, Sitios Zone 3,4,5, Sombria, …, St. Ignatius De loyola Townhomes` line from spec §3.1: `Isla Verde` → sitio_purok, `Townhomes` → subdivision), `test_unknown_is_other`, `test_dev_examples` (parametrized over `evals/areas/dev.jsonl`: reports per-row diffs; it's a tuning aid marked `xfail(strict=False)`, not a gate).
- [ ] **Step 2:** Run; FAIL. Implement per spec §4.4. Run; PASS.
- [ ] **Step 3:** Tune suffix and prefix lists **on dev only** until dev typed F1 stops improving with general rules (no rule that names one specific string). Ruff; commit `feat(areas): rule-based typed entity extractor`.

### Task 7: Store, `areas` command, review queue, hooks

**Files:**
- Create: `src/outages/areas/store.py`, `tests/test_areas_store.py`
- Modify: `src/outages/__main__.py`, `src/outages/pipeline.py` (`check`, `reparse` call the areas step), `src/outages/report.py` (one summary line), `tests/test_cli.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces: `store.refresh_hashes(conn) -> int`; `store.pending_hashes(conn, rebuild: bool) -> list[tuple[str, str]]` (hash, areas_raw); `store.write_parse(conn, h: str, seg: Segmented, matches: list[BarangayMatch], entities: list[Entity], extractor: str, now: datetime) -> None` (one transaction per hash); `store.delete_orphans(conn) -> int`; `AreasSummary` dataclass (counts by layout, match kind, extractor; warnings); `store.run_areas(conn, extractor: Extractor, gaz: Gazetteer, now: datetime, dry_run: bool = False, rebuild: bool = False) -> AreasSummary`; `store.review_queue(conn, gaz) -> list[ReviewItem(name_raw, match, count, example_slug, example_seq, suggestions)]` plus `other`-layout and no-town outages; `format_review(items) -> str`. CLI `areas [--rebuild] [--dry-run] [--review]`; `extractor_from_config() -> Extractor` in `__main__` (rules now; qwen after Task 8).

- [ ] **Step 1:** Failing tests on a temp DB seeded with a few outages: `test_areas_writes_rows`, `test_areas_idempotent`, `test_areas_skips_removed`, `test_areas_parses_failed_post_outages` (post `parse_status='failed'` still parsed, spec §6), `test_areas_follows_areas_changed` (Review Focus 2), `test_orphans_deleted`, `test_dry_run_writes_nothing`, `test_areas_resumes_after_interrupt` (an extractor that raises on the 3rd hash: first 2 hashes stored; rerun finishes the rest without re-extracting them; Review Focus 3), `test_review_lists_unmatched_with_suggestions`, `test_review_prints_on_cp1252_console` (Review Focus 4), `test_reparse_runs_areas_step`, `test_check_runs_areas_step` (fake fetcher, existing pipeline test helpers), `test_report_has_areas_line`.
- [ ] **Step 2:** Run; FAIL. Implement. Run; PASS. Ruff; commit `feat(areas): areas command, review queue and pipeline hook`.
- [ ] **Step 3 (operational):** `python -m outages areas --dry-run` on the real DB. **Show the owner** the summary (layouts, match counts, warnings).
- [ ] **Step 4:** Work the review queue: for each unmatched / other_town name, add an alias with a note or a `not_barangay` row, or leave it with a reason for the ROADMAP. Re-run the dry run until the queue is empty or every name left is explained. Add a test for any new segmenter shape found. Commit `fix(areas): aliases from the full-corpus review queue`.
- [ ] **Step 5:** Ask the owner, then run `python -m outages areas` for real (rules). Commit nothing from `data/`.

## Part C — LLM arms and the eval

### Task 8: Ollama and Anthropic extractors with cache

**Files:**
- Create: `src/outages/areas/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Entity`, `Extractor`, `RulesExtractor`, `normalize_entity`.
- Produces: `PROMPT_VERSION = "1"`; `ENTITY_SCHEMA: dict` (spec §4.4); `validate(obj: object) -> list[Entity]` (raises `SchemaError`); `render_input(seg: Segmented) -> str` (labelled segments only, no barangays); `OllamaExtractor(conn, client: httpx.Client | None = None, model=QWEN_MODEL)` (`name="qwen"`); `AnthropicExtractor(conn, api_key: str, client=None, model=HAIKU_MODEL)` (`name="haiku"`); `anthropic_from_env(conn) -> AnthropicExtractor | None`; `LLMUnavailable` exception. Both cache in `llm_cache` with key `sha256(model + PROMPT_VERSION + render_input(seg))`, store `seconds`, and fall back to `RulesExtractor` after two invalid outputs, tagging those entities' extractor `rules_fallback` (expose `last_extractor: str`).

- [ ] **Step 1:** Failing tests with `httpx.MockTransport`: `test_ollama_request_shape` (model, `think: false`, `temperature: 0`, `format` = schema, `stream: false`), `test_valid_response_parsed_and_normalized`, `test_invalid_then_valid_retries`, `test_invalid_twice_falls_back_to_rules`, `test_timeout_retries_once_then_raises`, `test_unreachable_raises_llm_unavailable` (message names `OLLAMA_URL` and `ENTITY_EXTRACTOR=rules`), `test_cache_hit_makes_no_call` (Review Focus 3), `test_prompt_version_changes_key`, `test_anthropic_request_shape` (headers `x-api-key`, `anthropic-version: 2023-06-01`, tool with the schema), `test_anthropic_skipped_without_key`, `test_key_never_logged` (caplog has no key). Plus one `@pytest.mark.llm` smoke test `test_qwen_real_call` on one dev row.
- [ ] **Step 2:** Run; FAIL. Implement. Run; PASS.
- [ ] **Step 3:** Tune the prompt **on dev only** (`python -m outages eval run --split dev --arms qwen`, after Task 9's runner exists; until then, a scratch script over dev). Bump `PROMPT_VERSION` on each change. Wire `extractor_from_config()` for `qwen`. Ruff; commit `feat(areas): Qwen and Haiku entity extractors with SQLite cache`.

### Task 9: Scorer, `eval run`, CI regression

**Files:**
- Modify: `src/outages/evaluate.py`, `src/outages/__main__.py`
- Create: `tests/test_eval_areas.py`, `evals/areas/baseline.json`, `evals/areas/results/`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- Consumes: `segment`, `Gazetteer`, extractors, `entity_key`, `load_split`, `check_frozen`.
- Produces: `PRF(p: float, r: float, f1: float, tp: int, fp: int, fn: int)`; `score_entities(gold: list[list[Entity]], pred: list[list[Entity]]) -> dict` with keys `street`, `typed`, `untyped`, `per_kind` (multiset matching on `entity_key`; untyped ignores kind); `score_barangays(gold, pred) -> dict` (`prf`, `match_rate`); `decide(rules: dict, qwen: dict | None) -> tuple[str, str]` (winner, one-line reason) implementing the decision rule verbatim; `run_eval(split: str, arms: list[str], conn, now) -> Path` writing `evals/areas/results/<date>-<split>.md` (decision rule text, per-arm table, timing mean/p95 from `llm_cache.seconds`, schema failures, skipped arms with reason, `QWEN_MODEL` + Ollama model digest + `PROMPT_VERSION`, the barangay caveat of spec §7.3). CLI `eval run --split dev|test --arms rules,qwen,haiku`.

- [ ] **Step 1:** Failing tests: `test_prf_counts` (hand-computed), `test_typed_vs_untyped` (right name, wrong kind: untyped TP, typed FP+FN), `test_duplicates_are_multiset`, `test_decide_qwen_wins_by_margin` (street +5.0, typed equal → qwen), `test_decide_margin_not_met` (+4.9 → rules), `test_decide_typed_lower` (street +10, typed −0.1 → rules), `test_decide_no_qwen` (→ rules), `test_eval_run_refuses_unfrozen_test` (Review Focus 5), `test_report_lists_skipped_haiku`.
- [ ] **Step 2:** Run; FAIL. Implement. Run; PASS.
- [ ] **Step 3:** `tests/test_eval_areas.py` (CI): rules arm on dev and test, every metric ≥ `baseline.json`, plus `check_frozen`. The test prints numbers only, never test rows. Write `baseline.json` from the first rules run (its `note` field says when and why). Ruff; commit `feat(eval): scorer, decision rule, eval report and CI regression`.

### Task 10: Run the eval, set the default, full run, hand-off (operational)

- [ ] **Step 1:** Make sure Ollama is running with `qwen3:14b`. Run `python -m outages eval run --split test --arms rules,qwen,haiku` **once** (Haiku runs only if `ANTHROPIC_API_KEY` is set; ask the owner before any paid call). Commit the report: `docs(eval): Phase 2 test results`.
- [ ] **Step 2:** Apply the decision rule as computed by `decide()`; set `ENTITY_EXTRACTOR` accordingly with the report path in the commit body. No re-run, prompt change or label change after seeing test results, unless the owner approves it with a written reason in `CHANGES.md`.
- [ ] **Step 3:** If the winner is `qwen`: `python -m outages areas --dry-run` (rules for barangays, Qwen for entities, ~2–4 h, resumable), show the owner, then `areas --rebuild`. If it's `rules`, the Task 7 run stands.
- [ ] **Step 4:** Full suite, ruff, CI green on the pushed branch (push only with the owner's OK).
- [ ] **Step 5:** ROADMAP: tick Phase 2 items, a progress entry with layout counts, match rate, review-queue outcome, the eval table and the decision; Known issues for names left unmatched; Lessons. Write `prompts/phase3.md` in the shape of `prompts/phase2.md`. Commit `docs: Phase 2 results and Phase 3 prompt`. Ask before opening the PR.
