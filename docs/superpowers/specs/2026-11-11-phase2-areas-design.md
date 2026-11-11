# Phase 2 Areas and Evals — Design

Status: approved in brainstorming 2026-11-11, section by section. Base: `main` at `c951f34` (Phase 1 merged).

## 1. Intent

Phase 2 turns each outage's `areas_raw` into structured places:
- **towns and barangays** (with PSGC codes) by rules and a pinned PSGC gazetteer, for Phase 3 alerts and the Phase 4 map;
- **typed entities** (streets, sitios/puroks, subdivisions/villages, establishments, other) by rules or local Qwen, whichever wins a pre-registered eval, for Phase 4 popups and Phase 5 street highlighting.

Success:
- `python -m outages areas` parses every non-`removed` outage's areas; no barangay name is dropped (unmatched names are stored and shown in a review queue).
- A frozen, held-out test set of hand-labelled strings scores rules vs `qwen3:14b` (vs Claude Haiku when `ANTHROPIC_API_KEY` is set), and the default extractor follows a decision rule written down before any result existed.
- The rules arm's scores are a CI regression test.

## 2. Decisions (owner, 2026-11-11)

| Topic | Decision |
|---|---|
| Eval integrity | ~60 strings drawn by a seeded, stratified sample **before any rule or prompt is written**; 20 dev (rules/prompt tuned on these), 40 test (frozen, only scored; failures reported, not fixed, in Phase 2). Claude labels from the post text; the owner reviews a sample. |
| Entity output | Typed entities `{kind, name}`, kind ∈ `street`, `sitio_purok`, `subdivision`, `establishment`, `other`. Street F1 is the headline metric (Phase 5 consumer). |
| Default rule (pre-registered) | Qwen becomes the default only if its **test street F1 ≥ rules + 5 points** and its **test typed-entity F1 ≥ rules**. Otherwise rules. Ties go to rules. Haiku is reported, never the default (paid service). |
| Storage | Derived tables keyed by `sha256(areas_raw)`, rebuilt by `python -m outages areas`; the Phase 1 upsert and change log are untouched. |
| No-separator fields | Split in the areas segmenter by a sub-label pattern, **not** in `extract` (that would change `text_hash` for every structured post and log false `areas_changed` on the next `check`). |
| Gazetteer | `faeldon/philippines-json-maps` **v1.1.1**, `geojson-medres.zip`, SHA-256 pinned in `config.py`; derived `barangays.csv` committed. |
| Matching | Exact (normalized) within the listed towns → alias → unmatched. No fuzzy auto-match; fuzzy only suggests in the review queue. |
| LLM | `qwen3:14b` (Q4_K_M, installed, fully in VRAM) via Ollama's REST API with httpx. Haiku via the Messages API with httpx, only with `ANTHROPIC_API_KEY`. |
| Dependencies | **None new.** httpx is already a dependency; the JSON schema check is hand-written. |

## 3. Findings that shaped the design

### 3.1 Corpus (2026-11-11, `data/outages.db`, read-only)
3,008 non-`removed` outages, 2,810 distinct `areas_key`.

| Layout | Outages | Shape |
|---|---|---|
| City-first | 1,429 | `Portion of Cebu City (Tisa) including portions of …`; `Portion of Consolacion & Liloan (Lamac & Yati) …` |
| Barangay-first | 1,344 | `Portion of Banilad & Talamban, Cebu City, along portions of …` |
| Structured | 227 | `Portion of Cebu City` / `Barangays: …` / `Streets: …` / `Sitios: …` / … |
| Other | 8 | `testing` (2); `Portion of Cebu City Barangays: Tejero` (no separator); `Portion of Talamban Cebu City & Banilad Cebu City & Mandaue City …`; `A portion of San Fernando & City of Naga (…)`; `Portion of Mandaue City DCA Brokerage …` (no barangay) |

- 139 outages name two towns. Some don't attribute barangays: `Portion of Mandaue City & Consolacion, Basak, Casili, Jagobiao, Poblacion Oriental, along …` (the gazetteer settles it).
- 237 are multi-line (structured or wrapped).
- About 40 structured sub-label spellings: `Barangays` 153, `Streets` 112, `Establishments` 103, `Sitios` 101, `Barangay` 75, `Subdivisions` 37, `Establishments / Institutions` 32, `Puroks` 30, `Schools` 27, …, `Puroks/Sitios`, `Villages / Subdivisions / Compounds`, `Churches/Chapels`, `Government Facilities`, `Hospitals`, `Religious Sites`.
- Lists after `including portions of` switch type mid-list: `Puroks Mangga, Isla Verde, Sitios Zone 3,4,5, Sombria, …, St. Ignatius De loyola Townhomes, Ramona Village, …, BF Better Homes`. Typing these is where an LLM might beat rules.
- Same barangay name in two towns: `Tayud` (Consolacion, Liloan), `Bulacao` (Cebu City, Talisay; `Portion of Cebu City (Poblacion Pardo) Portion of Sunrise Village, Bulacao.`).
- PSGC name forms to expect: `Lorega-San Miguel`, `Ward IV`, `Lawaan I`/`II`/`III`, `Poblacion Oriental` (posted once as `Poblacion Orriental`).

### 3.2 Qwen probe (2026-11-11, throwaway)
`qwen3:14b`, `think=false`, `temperature=0`, JSON schema in `format`, on the longest kind of string (the Talisay `Puroks Mangga …` line): 505 output tokens in 9.4 s (~54 tok/s) + 11 s cold load. The model sits fully in VRAM, so the brief's "CPU only" assumption was wrong. A full-corpus pass is ~2–4 h once (cached afterwards); a new week is minutes.

Errors it made, which the labelling guide must pin down: `Cebu South Road Properties` → `Cebu South Road` (a different place); kept the plural prefix in `Puroks Mangga`; expanded `Sitios Zone 3,4,5` to three names.

### 3.3 Gazetteer release
`faeldon/philippines-json-maps` v1.1.1 (published 2026-11-07): `geojson-medres.zip` 13.8 MB, `geojson-hires.zip` 94 MB, `SHA256SUMS`. PSGC 3Q 2026; the 8 VECO towns are complete (Phase 1 recon).

## 4. Components

```
src/outages/
  areas/
    __init__.py
    segment.py          areas_raw -> Segmented(layout, towns, barangay_tokens, segments, warnings)   [pure]
    gazetteer.py        load barangays.csv + aliases.csv; resolve(tokens, towns) -> list[BarangayMatch]  [pure]
    entities.py         Entity, KINDS, Extractor protocol, normalize_entity, entity_key            [pure]
    entities_rules.py   RulesExtractor                                                                [pure]
    llm.py              OllamaExtractor, AnthropicExtractor, schema check, cache
    store.py            write/read area_parses, area_barangays, area_entities; areas_hash upkeep
    barangays.csv       committed, generated by `gazetteer`
    aliases.csv         committed, hand-edited
  gazetteer_build.py    download + verify the pinned zip, write barangays.csv
  evaluate.py           sample, score, write the eval report
  migrations/002_areas.sql
evals/areas/
  GUIDE.md  dev.jsonl  test.jsonl  FROZEN  CHANGES.md  baseline.json  results/
```

### 4.1 Gazetteer (`gazetteer_build.py`, `gazetteer.py`)
- `python -m outages gazetteer` downloads `geojson-medres.zip` from the v1.1.1 release into `data/gazetteer/` (gitignored) and checks it against `GAZETTEER_SHA256` in `config.py`. The release's own `SHA256SUMS` comes from the same place as the zip, so it can't catch a swapped file; the pinned hash can. A mismatch fails loudly and writes nothing.
- From the 8 VECO towns' barangay files it writes `src/outages/areas/barangays.csv`: `psgc_code, name, town, town_psgc`, sorted, UTF-8, `\n` (232 rows per the Phase 1 recon counts). It is committed so tests and CI never need the network. Phase 4 reuses the zip for shapes.
- `aliases.csv`: `town, alias, target, note`. `target` is a PSGC code (`Duljo Fatima` → Duljo, `Quiot` → Quiot Pardo, `Tugbungan` → Tugbongan, `Kinasang-an` → Kinasang-an Pardo, `Pung-ol` → Pung-ol Sibugay, `Poblacion Orriental` → Poblacion Oriental) or `not_barangay` (`Greenhills`, `Cebu Business Park`, `Cebu I.T. Park`, `North Reclamation Area`, `South Reclamation Area`). A `not_barangay` name becomes an entity of kind `other`. `town` may be `*`. Every row needs a `note`. The exact alias targets are confirmed against `barangays.csv` when it is built, not assumed.
- **Name normalization** for matching only (the stored `name_raw` keeps the text as posted): NFKC, casefold, `-` and spaces equal, collapsed whitespace, trailing `.` dropped, `Sto.` = `Santo`, `Sta.` = `Santa`, `Pob.` = `Poblacion`. Roman numerals stay as written (`Lawaan I` ≠ `Lawaan 1` unless an alias says so).
- **Town names:** `Cebu City`; `Mandaue City`/`Mandaue`; `Talisay City`/`Talisay`; `City of Naga`/`Naga City`/`Naga`; `Minglanilla`; `Consolacion`; `Liloan`; `San Fernando`.

### 4.2 Segmenter (`segment.py`)
Input: `areas_raw`. Output: `Segmented(layout, towns, barangay_tokens, segments: list[Segment(label | None, text)], warnings)`.
- Layout: `structured` if a sub-label line or inline sub-label is present; else `city_first` if the first line is `(A) portion(s) of <town(s)> (`; else `barangay_first` if the first line is `(A) portion(s) of <names>, <town>`; else `other`.
- **City-first:** towns before `(`; barangay tokens inside the first `( … )`, split on `,`, `&`, `and`. Text after `)` is the entity segment.
- **Barangay-first:** names up to the town name, split on `,` `&` `and`; tokens that are themselves town names go to towns (`Mandaue City & Consolacion, Basak, …`). The rest after the town is the entity segment.
- **Structured:** the first line gives the towns. Each `Label: value` gives a segment with its label. **Inline sub-labels** (`Barangays: Jubay, CotcotStreets: Jasmin Street`; `Portion of Cebu City Barangays: Tejero`) are split by a pattern built from the sub-label vocabulary (§3.1), matched case-sensitively at a word or lower→upper boundary so `Streets` inside a name isn't split. `Barangay(s):` values are the barangay tokens.
- **Wrapped lines** in a non-structured layout are joined with a space before segmenting.
- Warnings, not failures: no town found; no barangay token; `other` layout; an unknown sub-label (kept as a segment with that label). `testing` gives layout `other` and a warning.
- The segmenter never drops text: every character of `areas_raw` ends up in towns, barangay tokens or segments (tested).

### 4.3 Barangay resolution (`gazetteer.resolve`)
For each barangay token, in order:
1. Exact (normalized) match within the listed towns. Found in one → `exact`. Found in two listed towns (`Tayud` under Consolacion & Liloan) → one row per town, `ambiguous`. A missed alert is worse than two.
2. Alias (town-specific, then `*`) → `alias`, or → entity `other` if `not_barangay`.
3. Exact match only in an **unlisted** VECO town → `other_town` (stored with that town's code, sent to review).
4. Otherwise → `unmatched`, `psgc_code` NULL.

### 4.4 Entities
`Extractor` protocol: `name: str`, `extract(seg: Segmented) -> list[Entity(kind, name)]`. Barangays are already removed, so the LLM never has to find them.

**Rules (`RulesExtractor`):**
- Structured segments: the kind comes from the label (`Streets/Roads` → street; `Sitios/Puroks/Zones` → sitio_purok; `Subdivisions/Villages/Compounds` → subdivision; `Establishments/Institutions/Schools/Churches/Chapels/Hospitals/Government Facilities/Religious Sites` → establishment; unknown → other). Values are split as below.
- Free text: split on cue words (`along`, `including`, `portion(s) of`, `near`), then on `,` `&` `and` `;`. Each item gets a kind:
  - by suffix: `St.`, `Street`, `Rd.`, `Road`, `Ave.`, `Avenue`, `Highway`, `Hwy`, `Blvd`, `Ext.`, `Drive` → street; `Subd.`, `Subdivision`, `Village`, `Homes`, `Townhomes`, `Compound`, `Estate(s)`, `Residences` → subdivision; `Inc.`, `Corp.`, `Corporation`, `Co.`, `School`, `Elem`, `College`, `Chapel`, `Church`, `Hospital`, `Center`, `Bank`, `Office`, `Building` → establishment;
  - by a **sticky prefix**: `Sitio(s)`, `Purok(s)`, `Zone(s)` set `sitio_purok` for that item and the following unsuffixed items, until an item with a suffix breaks the run;
  - otherwise `other`.
- The suffix and prefix lists are tuned on the dev split only.

**Qwen (`OllamaExtractor`):** `POST /api/chat` on `http://localhost:11434`, model `qwen3:14b`, `think: false`, `temperature: 0`, `format` = the entity JSON schema, one string per call (the segments after barangay removal, with their labels). 120 s timeout, one retry. Output is checked by `llm.validate()`, a hand-written check (object with `entities`, each `{kind in enum, name non-empty str}`). Invalid twice → that string falls back to `RulesExtractor` and its rows are tagged `rules_fallback`. Ollama unreachable → the run stops with `error: Ollama not reachable at …; start it or set ENTITY_EXTRACTOR=rules`. It does not silently degrade. `PROMPT_VERSION` is a constant, bumped on any prompt change. The prompt is tuned on dev only.

**Haiku (`AnthropicExtractor`, eval only):** `POST https://api.anthropic.com/v1/messages` with httpx, model `claude-haiku-5-5` (a constant in `config.py`), the same prompt and schema via a tool definition. It exists only when `ANTHROPIC_API_KEY` is set; otherwise the eval writes `haiku: skipped (no ANTHROPIC_API_KEY)`. The key is read from the environment or `.env` and never logged.

**Cache (`llm_cache`):** key = `sha256(model + PROMPT_VERSION + segmented text)`. A hit returns the stored response with no call. Each row stores the seconds taken, for the eval's timing columns.

**Name conventions** (in `evals/areas/GUIDE.md`; applied to labels and to every arm's output via `normalize_entity()` before scoring and storing):
- Drop leading cue words (`portion(s) of`, `along`, `including`, `near`) and a plural type prefix (`Puroks Mangga` → `Mangga`, kind `sitio_purok`; `Sitio Kawayan` → `Kawayan`).
- Expand number lists: `Zone 3,4,5` → `Zone 3`, `Zone 4`, `Zone 5`.
- Otherwise keep the name as written, including `Properties`, `Ext.`, `Inc.`. A truncated or merged name is wrong.

### 4.5 Default extractor
`config.ENTITY_EXTRACTOR` (`"rules"` | `"qwen"`, env override `OUTAGES_ENTITY_EXTRACTOR`) is set at the end of the build from the §2 rule and the test results. `areas` uses it.

## 5. Storage (`002_areas.sql`)

```sql
ALTER TABLE outages ADD COLUMN areas_hash TEXT;              -- sha256(areas_raw), kept by `areas`
CREATE INDEX outages_areas_hash ON outages(areas_hash);

CREATE TABLE area_parses (
  areas_hash TEXT PRIMARY KEY, layout TEXT NOT NULL
    CHECK (layout IN ('city_first','barangay_first','structured','other')),
  towns_json TEXT NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
  extractor TEXT NOT NULL, parsed_at TEXT NOT NULL);
CREATE TABLE area_barangays (
  areas_hash TEXT NOT NULL REFERENCES area_parses(areas_hash) ON DELETE CASCADE,
  seq INTEGER NOT NULL, name_raw TEXT NOT NULL, town_psgc TEXT, psgc_code TEXT,
  match TEXT NOT NULL CHECK (match IN ('exact','alias','ambiguous','other_town','unmatched')),
  PRIMARY KEY (areas_hash, seq));
CREATE INDEX area_barangays_psgc ON area_barangays(psgc_code);
CREATE TABLE area_entities (
  areas_hash TEXT NOT NULL REFERENCES area_parses(areas_hash) ON DELETE CASCADE,
  seq INTEGER NOT NULL, kind TEXT NOT NULL
    CHECK (kind IN ('street','sitio_purok','subdivision','establishment','other')),
  name TEXT NOT NULL, extractor TEXT NOT NULL, PRIMARY KEY (areas_hash, seq));
CREATE TABLE llm_cache (
  key TEXT PRIMARY KEY, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  response_json TEXT NOT NULL, seconds REAL NOT NULL, created_at TEXT NOT NULL);
```

- SQL can't compute SHA-256, so `outages.areas_hash` is set by the `areas` command (§6) for every non-`removed` outage on every run (3k hashes, milliseconds). The Phase 1 upsert is not changed.
- An `area_parses` row whose hash no outage uses any more is deleted by `areas` (cascade). `llm_cache` is never pruned automatically.
- An `ambiguous` name has one row per candidate town (same `name_raw`, consecutive `seq`).

## 6. CLI

| Command | Does |
|---|---|
| `gazetteer` | Download and verify the pinned zip, write `barangays.csv`. The only network use in Phase 2 (GitHub, not VECO). |
| `areas` | Refresh `outages.areas_hash`; parse every hash with no `area_parses` row using the default extractor; delete orphan rows; print counts by layout, match kind and extractor. `--rebuild` re-parses all hashes. `--dry-run` parses everything in memory, writes nothing, prints the same summary. |
| `areas --review` | Review queue: every `unmatched`, `other_town` and `ambiguous` name with its count, one example (slug + outage seq), and the top 3 `difflib` suggestions from `barangays.csv`; also outages with layout `other` or a no-town warning. Printed and written to `data/reports/areas-review-<date>.md`. |
| `eval sample` | Draw the stratified sample into `evals/areas/dev.jsonl` + `test.jsonl` (refuses to overwrite a frozen test file). |
| `eval run --split dev\|test --arms rules,qwen,haiku` | Score the arms, write `evals/areas/results/<date>-<split>.md`. |

`reparse` and `check` call the `areas` step at the end (new hashes only). `report` gains one line: barangay match counts and the size of the review queue.

**Posts whose latest parse failed:** their stored outages are still areas-parsed (the text is real). Phase 3 owns "don't alert from a post whose latest parse failed" via `outages.post_id → posts.parse_status`.

## 7. The eval

### 7.1 Sample
`eval sample --seed 20261111` takes distinct `areas_raw` of non-`removed` outages and stratifies by layout (`city_first`, `barangay_first`, `structured`) × town group (Cebu City; Mandaue; south = Talisay, Minglanilla, Naga, San Fernando; north = Consolacion, Liloan). Allocation: 60 strings, as even as each cell allows, plus forced picks: ≥ 3 two-town lines, ≥ 1 inline sub-label, ≥ 1 wrapped value, ≥ 1 `other` layout. The 60 are shuffled with the same seed and split 20 dev / 40 test, with the forced picks spread over both. The sampler uses a simple layout heuristic (not the segmenter) so the segmenter's bugs can't bias the sample.

Rows: `{"id", "areas_hash", "slug", "areas_raw", "towns": [...], "barangays": [{"name", "psgc_code" | null}], "entities": [{"kind", "name"}]}`. Post text is public; the files are committed.

### 7.2 Labels and freezing
1. Sample, then write `GUIDE.md` (conventions §4.4, kind definitions, examples from dev only).
2. Claude labels **dev and test from the post text only**, before any rule, alias or prompt is written. PSGC codes come from `barangays.csv`.
3. The owner reviews all structured rows plus 10 others and marks corrections; corrections are applied with a reason.
4. Freeze: `FROZEN` holds `sha256(test.jsonl)`. Commit.
5. From then on `test.jsonl` is not opened during the build. Any change needs a dated entry in `CHANGES.md` with the reason, and an updated `FROZEN`. A test checks that the hash matches `FROZEN`.

### 7.3 Metrics
- **Barangays (rules only):** micro precision/recall/F1 on `(psgc_code)` per string; **match rate** = barangay mentions resolved to a code / all mentions. Aliases are seeded from the **whole corpus's** review queue, test strings included, so this is a coverage/regression number, not a model comparison, and the report says so.
- **Entities (per arm):** street P/R/F1 (headline); typed-entity P/R/F1 (name and kind both right); untyped F1 (name right, any kind, which separates splitting from typing errors); per-kind F1; schema failures; mean and p95 seconds per string (rules ≈ 0).
- Matching: multiset of `(kind, normalize_entity(name))` per string, compared casefolded with whitespace collapsed and trailing `.` dropped.
- The report states the §2 decision rule, each arm's numbers, the outcome and the Qwen model and `PROMPT_VERSION`. A skipped arm is listed as skipped with the reason.

### 7.4 In pytest
- `tests/test_eval_areas.py` (runs in CI): rules arm on dev and test; asserts every metric ≥ `evals/areas/baseline.json` (written once from the first full run, with a written reason for any later change). It also asserts `FROZEN` matches.
- Qwen and Haiku runs are `@pytest.mark.llm`, skipped in CI and by default.

## 8. Testing

- **TDD**, each case from a real line: every layout; two-town lines (attributed and not); inline sub-labels; wrapped values; `testing`; `Portion of Cebu City Barangays: Tejero`; `Poblacion Pardo … Bulacao`; the "no text dropped" property over all fixtures' `areas_raw`.
- Gazetteer: exact, alias, `not_barangay`, ambiguous, other_town, unmatched; normalization; build from a tiny synthetic zip (the real zip is never in tests); SHA mismatch fails.
- Rules entities: suffixes, sticky prefixes, `Zone 3,4,5`, structured labels.
- LLM arms: an `httpx.MockTransport` fake: valid response, invalid then valid, invalid twice → `rules_fallback`, timeout, unreachable, cache hit (no call), Haiku skipped without a key.
- DB: `002` applies on a Phase 1-shaped DB; orphan cleanup; `areas` is idempotent.
- **Before writing to the real DB:** `areas --dry-run` over the whole corpus, summary shown to the owner (the Phase 1 lesson).

## 9. Build order (for the plan)
1. Migration, `gazetteer` command, `barangays.csv`.
2. `eval sample`, `GUIDE.md`, labels, owner review, freeze. **No rules, aliases or prompt before this commit.**
3. Segmenter, resolver, aliases (from the full-corpus review queue), rules entities, store, `areas` and `--review`, tuned on dev.
4. LLM arms + cache, prompt tuned on dev.
5. `eval run` on test, once per arm; set `ENTITY_EXTRACTOR`; baseline; full-corpus `areas`; review queue cleared or explained.

## 10. Definition of done
- CI green on Ubuntu 3.12 and Windows 3.14.
- `areas --dry-run` summary shown to the owner, then the real run; review queue empty or every remaining name explained in the ROADMAP.
- `evals/areas/results/<date>-test.md` committed; default extractor set by the §2 rule; ROADMAP progress entry with the numbers.

## 11. Out of scope
Map thumbnails (`map_image_url`) are not used. Geometry and GeoJSON (Phase 4). Overpass street lookups (Phase 5). Alerts (Phase 3).

## 12. Risks and accepted limits
- **40 test strings make per-kind F1 noisy** for rare kinds. Only street F1 and typed F1 drive the decision; the margin absorbs some noise.
- **The labeller is also the rule author.** Mitigated by labelling before any rule exists, the frozen test split, and the owner's review sample; not eliminated. The README says so.
- **Barangay numbers are not held out** (aliases come from the whole corpus); stated in the report.
- **Qwen output can change across Ollama/model updates.** The cache pins past outputs; the report records model digest and `PROMPT_VERSION`.
- **New VECO formats** appear as `other` layout or warnings in the review queue, never as silent drops.
- **PSGC renames** in later releases need a new pin, a rebuilt CSV and a review of the alias table.
