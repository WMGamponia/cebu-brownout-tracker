# Streets v2 — Design

Status: approved in brainstorming 2026-11-12, section by section. Base: `main` at `e969763` of `WMGamponia/cebu-brownout-tracker`. Branch: `streets-v2` (created after this spec is approved).

## 1. Intent

Give street drawing on the map one fair, final chance. Phase 5 built the matcher and lines, but its pre-registered gate (test precision ≥ 0.95 over ≥ 20 drawn rows) could not be met by a 50-row split, and one of its 13 drawn rows was "wrong" only because the label used an `alt_name`. This phase:

1. **Measures properly:** a fresh 150-row test split, labelled under a convention written first, sampled and frozen before anything new is built, run once, same bar.
2. **Finds more streets:** a local LLM suggests which in-scope OSM road an unmatched VECO name means (`Bugos` → Burgos Street, `2nd St.` → Second Street). The owner approves each suggestion; approved ones become town-scoped aliases.

Success:
- The test split is decided once, on numbers nothing was tuned on; every guard (test hash, alias hash, extract date, suggester inputs) is checked by the eval and printed in the report.
- The suggester's value is measured (dev accuracy against a `difflib` baseline, the owner's acceptance rate, mentions unlocked), not assumed.
- Nothing reaches `street_aliases.csv` or the map without the owner's decision. Nothing leaves the PC: local Qwen only.
- Pass → drawing on, locally and (after review) in the public snapshot with a correct ODbL notice. Fail → drawing stays off for good, numbers published.

## 2. Decisions (owner, 2026-11-12)

| Topic | Decision |
|---|---|
| Test sample | **Mention-weighted, distinct (town, key):** mentions drawn uniformly over the corpus, one row per (town, `street_key`), excluding the 80 keys already labelled. Precision then means "how often a line on the map is wrong". No per-town quota; the report breaks down by town. |
| Size | **150 rows**, gold = the OSM `name` tag (§5). |
| Old 50-row split | **Folded into dev.** All 80 old rows (30 dev + 50 test) relabelled under GUIDE v2; every change logged in `CHANGES.md`. Phase 5's results file stays as history. |
| Who labels test | **A fresh subagent.** The build session samples rows without gold and never reads the test gold; the owner reviews ≥ 25 labels before freezing. |
| Suggester | **Prefilter (12 in-scope names) → local `qwen3:14b` picks one or none**, against a `difflib` top-1 baseline on dev. No Haiku: VECO text and OSM names stay on the PC. |
| Review | **A queue file** (`data/streets/alias_queue.csv`) **decided on a local review page with a map** (`/streets/review` in the map server, loopback only): scope shaded, the proposed road drawn as the matcher would draw it, other candidates as thin lines, Yes / No / Skip / pick another. The spreadsheet still works as a fallback. `streets suggest --apply` records accepted aliases with provenance and rejects so they are never asked again. Reason: names alone can't show which of two nearby roads is meant; what the owner approves is what gets drawn. |
| Automation | **Suggestions are automatic inside `serve`** (after each run, new groups only, bounded, never fatal); **applying is always manual.** The first full pass is a manual `streets suggest`. |
| Fail | **Drawing stays off and the street-drawing line of work ends.** No third split. Approved aliases stay. |
| Aliases after the test | **Accepted and documented (a):** aliases approved after the test run draw unmeasured lines; provenance records the date; the README says measured precision covers the aliases frozen on the test date. |
| OSM refetch | **None.** The extract is from 2026-11-12 (`osm_base 2026-11-12T02:14:58Z`); labels record it and the eval refuses another. |
| Dependencies | **None new.** |

## 3. Findings that shaped the design

From `data/outages.db` and `data/osm/` (read-only, 2026-11-12):
- 5,032 street mentions on non-removed outages; 4,368 once the 80 labelled keys are excluded; 1,932 distinct (town, key).
- **Drawn rate:** 0.451 of mentions, 0.277 of distinct keys. At n = 150 the mention-weighted sample gives ~68 drawn rows expected (the key-uniform Phase 5 method ~42); P(≥ 20 drawn) ≈ 1 either way. **The binding constraint is now the precision bar:** with ~60 drawn rows, 0.95 allows 3 wrong lines; if the true precision is 0.95, a pass is roughly a coin flip. That is accepted: the bar stays.
- Per-town drawn rate varies from 0.00 (`702241000`, 42 mentions) to 0.50 (Cebu City, 2,488 mentions); Phase 5's 10-per-town quota over-sampled the small towns.
- **Suggester workload:** 1,502 distinct (town, key) groups are `not_found` (8 generic). Over the last 12 months VECO introduced ~35 new `not_found` groups a month (~8 a week).
- `streets --review` groups by key across towns, though aliases are town-scoped; fixed here.
- The matcher's `osm_name` is the most common `name` tag of the matched ways, so a way matched only by `alt_name`/`old_name` and lacking `name` reports `None` and would score wrong even when right; fixed by a fallback order (§5.1).
- Drawn street geometry today totals 2.9 MB across all outages; the snapshot's week files are ~8.3 MB.
- `qwen3:14b` is installed in Ollama (Phase 2 measured it in VRAM at ~54 tok/s).

## 4. Order of operations (the leak-proofing)

Each step finishes before the next starts. ⏸ = stop for the owner.

1. **GUIDE v2** written (§5.1), before any v2 label.
2. **Dev relabel:** the 80 old rows relabelled under GUIDE v2 → `evals/streets/dev.jsonl` (80 rows, `origin` = `p5-dev` / `p5-test`). Changes logged in `CHANGES.md`. Old `test.jsonl` and `FROZEN` move to `evals/streets/p5/` with the Phase 5 results (history, not used).
3. **Sample test:** `eval sample --suite streets-v2` (§6.1) writes `evals/streets/test.rows.jsonl` (no gold) and `ROWS_FROZEN` (its SHA-256).
4. **Label test:** a fresh subagent labels every row (§6.2) and writes `evals/streets/test.jsonl`. The main session runs `eval check --suite streets-v2` (schema, ids equal the frozen rows, `osm_base`), which prints counts only, never gold.
5. ⏸ **Owner reviews ≥ 25 labels** (spread over towns, null and non-null, list golds, `two roads`). Corrections through `CHANGES.md`; then `FROZEN` = SHA-256 of `test.jsonl`. From here the test is never opened.
6. **Suggester built and tuned on dev only** (§7); dev comparison report (§7.5).
7. **First full suggest:** `streets suggest` (manual) over all groups.
8. ⏸ **Owner decides the queue** on the review page (§7.9), or in the `decision` column.
9. **Apply:** `streets suggest --apply` (§7.7).
10. **Freeze aliases:** `eval freeze-aliases` writes `evals/streets/ALIASES_FROZEN` (SHA-256 of `street_aliases.csv`).
11. **Test run, once:** `eval run --suite streets --split test` (§6.4). Results to `evals/streets/results/<date>-test.md`.
12. **Gate (§6.5) → pass or fail path (§8).**

The in-`serve` suggest hook (§7.8) is inert until step 5's `FROZEN` exists. It can never change `street_aliases.csv`, so it cannot move the alias hash between steps 10 and 11.

## 5. Labelling and scoring

### 5.1 GUIDE v2 (`evals/streets/GUIDE.md`)

`gold` is the OSM name of the way(s) VECO means, **inside the scope + 150 m** (scope unchanged from Phase 5: the placed barangays, else the listed towns' barangays), or `null`.

1. **The name read is the way's `name` tag**, whichever tag VECO's wording happens to match. VECO's "Cebu South Road" in Minglanilla → gold `Natalio Bacalso Avenue` if that is the in-scope way's `name`. "Right line, other name" cannot occur: gold and the matcher read the same tag.
2. **No `name` tag:** the first present of `official_name`, `alt_name`, `old_name`, `short_name` (the order `primary_name()` uses in code). `name:en` is not indexed and is ignored.
3. **A road whose `name` changes inside the scope:** gold is a **list** of every `name` the meant way(s) carry in scope.
4. **Two different roads in scope carry VECO's name and only one is meant:** `null`, note `two roads`. The matcher would draw both; half the drawing is wrong, so it counts as a wrong line.
5. Phase 5 rules 3–7 carry over unchanged: spelling and abbreviations; similar names only when OSM has no exact form and nothing competes; generic names `null` unless OSM has that exact name in scope and it is clearly meant; not-a-street → `null` with a note; unsure → `null` with a note.
6. Each row records `osm_base` (the extract's) and may carry a `note`.

### 5.2 Scorer (`streets/evaluate.py`)

- `gold` may be a string, a list of strings or `null`. A drawn row is correct when `street_key(osm_name)` is among the gold keys.
- `match_street`'s `osm_name` becomes the most common `primary_name(way)` (§5.1 rule 2) instead of the most common `name`.
- Precision = correct drawn / drawn; coverage = correct drawn / rows with non-null gold (unchanged).
- Reported, never gated: precision split by status (`matched` vs `alias`), precision and drawn count by town, a 95 % Wilson interval on precision, counts by status.
- Refuses (exit 2) if any row's `osm_base` differs from the loaded extract's.

## 6. The test split

### 6.1 Sample (`eval sample --suite streets-v2 --seed 20261113`)

Population: every street mention of a non-removed outage (`street_rows`) with a non-empty `street_key` that is not the key of any of the 80 old rows, in any town (Phase 5 sampled distinct keys globally, so its keys are excluded everywhere). Draw mentions uniformly at random (seeded), skipping a mention whose (town, key) is already taken, until 150 rows. Row: `id` (`t2-001`…), `areas_hash`, `seq`, `name`, `areas_raw`, `towns`, `barangays`, `town_psgc`. No gold. Writes `test.rows.jsonl` + `ROWS_FROZEN`; refuses to overwrite either.

### 6.2 Labelling by a fresh subagent

The build session dispatches one subagent with: GUIDE v2, `test.rows.jsonl`, and a read-only helper `python -m outages streets candidates --rows <file>` that prints, per row, every OSM way name tag in scope (all of `NAME_KEYS`, with way counts). The matcher and suggester are not given to it. It writes `test.jsonl` (rows + `gold`, `osm_base`, optional `note`) and returns only counts (rows, null, non-null, lists, notes). The main session does not read `test.jsonl` content; `eval check` validates it.

### 6.3 Owner review

`eval review-sample --suite streets-v2 --n 25` writes `data/streets/label-review.md`: 25 rows stratified over town and null/non-null (lists and `two roads` notes included when present), each with gold, note and the in-scope candidate names. The main session runs it but prints only the path; the owner opens the file. The owner writes corrections in the file (or tells the session the row id and new gold); the session applies them to `test.jsonl` without reading other rows, and logs each in `CHANGES.md`. Accepted limit: the owner sees ~25 golds and may later meet some of those keys in the queue.

### 6.4 The run

`eval run --suite streets --split test` refuses (exit 2, naming the failed guard) unless:
1. `test.jsonl` matches `FROZEN` and its row ids/`(areas_hash, seq)` match `test.rows.jsonl` / `ROWS_FROZEN`;
2. `street_aliases.csv` matches `ALIASES_FROZEN`;
3. every row's `osm_base` equals the extract's;
4. no test `(areas_hash, seq)` appears in `data/streets/suggest_inputs.jsonl`.

The report prints all four hashes/checks, then the metrics (§5.2), the gate line and the per-row table. A second run on the same date refuses unless `--again` (which still writes a new file and is logged; it exists for a crashed run, not a re-roll).

### 6.5 Gate (pre-registered, unchanged)

`STREETS_DRAW = True` only if **test precision ≥ 0.95 with ≥ 20 drawn rows.** Coverage, the matched/alias split, per-town numbers and the interval are reported, not gated. One run.

## 7. The suggester (`streets/suggest.py`)

### 7.1 Groups

The `not_found` rows of `street_matches`, excluding `GENERIC_STREET_KEYS`, grouped by (town_psgc of the mention, `street_key`). A test row's own `(areas_hash, seq)` is removed from its group; an empty group is skipped. Groups already in the queue (any decision) or in `street_alias_rejects.csv` are skipped. Every mention a run sends to the model is appended to `data/streets/suggest_inputs.jsonl` (`areas_hash`, `seq`, town, key, run time).

### 7.2 Candidates

Union of `StreetIndex.names_in(search_towns, scope)` over the group's mentions (at most 20 mentions, in a fixed order), deduplicated by `street_key`, ranked by `difflib.SequenceMatcher` ratio of keys, top 12. Every candidate is in scope by construction. A group with no candidates is recorded `no_candidates` without a model call.

### 7.3 Prompt and answer

`STREET_PROMPT_VERSION = "1"` (bumped on every change; tuned on dev only). Input: VECO's name as typed, up to 2 `areas_raw` examples, the 12 numbered candidates. Instructions: pick a candidate only if it is plainly the same road (typo, abbreviation, missing or extra word, numeral ↔ word); `none` if unsure; never on a shared generic word (`Street`, `Road`, `Barangay`) alone. JSON schema: `{"pick": integer 1..12 or null, "reason": string ≤ 15 words}`.

### 7.4 Model and cache

`qwen3:14b` via Ollama `/api/chat` (`think: false`, temperature 0, schema in `format`), the Phase 2 client pattern over httpx. Valid answers cached in `llm_cache` with key `sha256(model + STREET_PROMPT_VERSION + input)`. An answer outside 1..12 or not matching the schema is invalid; two invalid → group status `invalid` (kept in the queue, no pick). Ollama unreachable → the manual command stops with exit 2; the `serve` hook skips (§7.8).

### 7.5 Dev comparison (`eval run --suite streets-suggest --split dev`)

On dev rows the rules leave `not_found`: prefilter recall (gold among the 12), Qwen accuracy (right pick on non-null gold; `none` on null gold), `difflib` top-1 accuracy (`get_close_matches`, cutoff 0.75, same candidates). The arm with the higher accuracy fills the queue's `pick`; ties → `difflib`. Recorded in `config.STREET_SUGGEST_PRIMARY`. Dev holds ~10–15 near-miss rows, so the comparison is directional; the report says so.

### 7.6 Queue (`data/streets/alias_queue.csv`, local, UTF-8 with BOM for spreadsheet use)

Columns: `town_psgc`, `town`, `veco_key`, `veco_name` (most common spelling), `mentions`, `pick`, `pick_source` (`qwen`/`difflib`), `other_pick`, `candidates` (top 5, `|`-separated), `reason`, `status` (`ok`/`invalid`/`no_candidates`), `decision`. Sorted by mentions, highest first. A re-run merges by (town, key): existing rows and decisions are kept; new groups are appended.

### 7.7 Apply (`streets suggest --apply`)

`decision`: `y` → `pick`; an exact candidate name → that name; `n` → reject; empty → left. Anything else, or a name not in the group's scope, is refused by row and reported; the rest apply. Accepted rows are appended to `src/outages/areas/street_aliases.csv`, which gains `source` (`manual`/`qwen`/`difflib`/`owner` when the owner chose another candidate), `model`, `prompt_version`, `decided` (date); the existing two rows become `manual`. `load_aliases` ignores the extra columns. Rejects go to the committed `src/outages/areas/street_alias_rejects.csv` (`town_psgc`, `veco_key`, `decided`). Applied and rejected rows leave the queue. Prints: decided, accepted by source, acceptance rate by source, rejected, refused. Then `streets match --dry-run` gives the mentions unlocked (owner runs it; the real `--rebuild` waits for §8).

### 7.8 Automatic suggestions in `serve`

After each 06:00 / 20:00 run **has finished** (check, then alerts), `serve` runs `suggest_new(budget)`: new groups only (§7.1), at most 20 groups or 5 minutes per run, whichever first; the rest waits. Inert (one log line) until `evals/streets/FROZEN` exists, or with no extract, or when Ollama is unreachable. Any exception is logged and swallowed; alerts are never delayed or stopped. Off with `STREETS_SUGGEST_AUTO = False` or `serve --no-suggest`. When rows were added, the next alert run's admin notes carry one line: "N new street suggestions waiting (data/streets/alias_queue.csv)" (key `suggest:<date>:<n>`). Applying stays manual.

### 7.9 Review page (`/streets/review` in the map server)

A local page for deciding the queue with a map. Part of the existing FastAPI app (thread in `serve`, or `python -m outages map`); reads the DB read-only as today and writes **only the queue file**, never the DB or `street_aliases.csv`.

- **Loopback only:** the review routes are registered only when the server binds a loopback host (`server.is_loopback`); with the LAN opt-in they don't exist (404). POSTs also require a per-process random token embedded in the page and an `Origin` equal to the server's own; anything else → 403.
- **`GET /streets/review`:** the page (vanilla JS, vendored Leaflet, OSM tiles as on the map).
- **`GET /streets/review/items`:** undecided queue rows with `status` `ok` or `invalid`, most mentions first.
- **`GET /streets/review/item?town=&key=`:** for one group: the scope's barangay polygons and the 150 m buffer outline; for the pick and each of the top 5 candidates, its ways clipped to the scope exactly as `match_street` would (`clip`), with the candidate name; VECO's spelling, mentions, up to 2 `areas_raw`, the model's reason, `pick_source`, `other_pick`. The extract is loaded once, on the first request.
- **`POST /streets/review/decide`** `{town, key, decision, token}`: `decision` is `y`, `n`, `skip` (leaves it empty) or the exact name of one of the group's candidates. Written to the queue row's `decision` (atomic: temp file + replace, under a lock shared with the queue writer). Anything else → 422.
- **Page:** map on the left (scope shaded, buffer dashed, pick bold, other candidates thin and labelled, click one to choose it); on the right VECO's name, mentions, area texts, reason, and **Yes / No / Skip** (keys `y` / `n` / `s`); after each decision the next item loads; a counter "N left". 375 px stacks the panel under the map.
- These lines are drawn **regardless of `STREETS_DRAW`**: the page is a local review tool; the public map and the snapshot never include it (`snapshot` doesn't copy it).
- Applying stays a separate step: `streets suggest --apply` (§7.7) reads the decisions.

## 8. Pass and fail

**Pass:**
1. `STREETS_DRAW = True`, committed with the results file.
2. ⏸ Owner OK → backup `data/outages.db.bak-<date>`, `streets match --dry-run` (counts), `streets match --rebuild` (new aliases and the `primary_name` fix).
3. Browser check on the real DB (read-only): lines on top of shading, clipped to scope + 150 m, cancelled grey dashed, 375 px.
4. Snapshot: the `LICENSES.md` street paragraph in `snapshot.py` is chosen by `STREETS_DRAW`: on, it states the lines are derived from OSM, © OpenStreetMap contributors, ODbL, the extract's `osm_base`, and where the derived data is (`weeks/*.geojson`, `streets.drawn[].lines`). The map's attribution adds "street lines © OpenStreetMap contributors (ODbL)" when a document has drawn lines. Snapshot built, sizes reported (expected ~11–12 MB of weeks), leak scan as before. ⏸ Owner review → publish.
5. README eval section: both runs (Phase 5: 0.923 over 13; Streets v2 with interval and the matched/alias split), the suggester's dev comparison, acceptance rate and mentions unlocked, and the note on aliases approved after the test date.

**Fail:** `STREETS_DRAW` stays `False`; aliases stay (they improve the review and the data); results in README and ROADMAP; Known issues: "Street drawing closed after two pre-registered runs." No snapshot change.

## 9. CLI

| Command | What |
|---|---|
| `eval sample --suite streets-v2` | §6.1; refuses to overwrite |
| `eval check --suite streets-v2` | validate `test.jsonl` against the frozen rows; counts only |
| `eval review-sample --suite streets-v2 --n 25` | §6.3 |
| `eval freeze --suite streets-v2` | write `FROZEN` |
| `eval freeze-aliases` | write `ALIASES_FROZEN` |
| `eval run --suite streets --split dev\|test [--again]` | §6.4 (test guards) |
| `eval run --suite streets-suggest --split dev` | §7.5 |
| `streets candidates --rows <file>` | in-scope OSM names per row (labelling helper; read-only) |
| `streets suggest [--limit N]` | §7.1–7.6; localhost Ollama only |
| `streets suggest --apply` | §7.7 |
| `serve --no-suggest` | §7.8 off |
| `http://127.0.0.1:8765/streets/review` | §7.9 (with `serve` or `map` running, loopback only) |

All offline: only `streets suggest` and the hook reach `localhost:11434`; nothing contacts VECO, Overpass or Telegram except as before.

## 10. Error handling

- `suggest` with no extract, no `street_matches`, or Ollama down: exit 2 with the reason, nothing written.
- Queue unreadable or a column missing: `--apply` exits 2, nothing applied.
- `eval sample` with existing rows; `eval run` test with a failed guard; labels against a different `osm_base`: exit 2 naming the problem.
- `serve` hook: never raises; logs and skips.
- Review page: no queue file → an empty state with the `streets suggest` hint; no extract → a message, no map lines; a queue row gone or already decided → 409 and the page moves on. A failure in a review route never affects the map routes.

## 11. Testing

TDD; the network stays blocked in tests.
- Sampler: seeded determinism; one row per (town, key); the 80 old keys excluded; uniform over mentions (a frequent key is more likely in); refuses to overwrite.
- Scorer: list gold; `primary_name` fallback; a `name`-less way matched by `alt_name`; Wilson interval; matched/alias split; `osm_base` mismatch refused.
- Each test-run guard has a failing test.
- Suggester: groups by (town, key) and exclude test rows; candidates only from the scope; top-12 order; prompt built from the candidates; answers outside 1..12 invalid; cache hit makes no HTTP call (MockTransport); Ollama down raises (manual) / skips (hook); the input log is written.
- Queue: merge keeps decisions; apply provenance, rejects, refusal of out-of-scope names; rejects never re-queued.
- `serve` hook: runs after alerts, respects budget, inert before `FROZEN`, swallows exceptions; admin note once.
- Snapshot licence text in both `STREETS_DRAW` states.
- Review routes: absent on a non-loopback bind; POST without the token or with a foreign `Origin` → 403; a decision not among the candidates → 422; the clipped lines equal `match_street`'s for the same name; the queue write is atomic and keeps other rows; `snapshot` never copies the page. JS: the pure decision/next-item logic under `node --test` (three zones, as now). One real-browser check on the real queue before the owner starts.

Not covered by tests: label quality (the owner's review is the check) and Qwen beyond dev.

## 12. Gates (ask the owner first)

The ≥ 25-label review; the queue decisions; the real-DB backup and `streets match --rebuild`; publishing the snapshot; push, PR, merge. Any Overpass request (none planned).

## 13. Out of scope

Auto-applying aliases; Haiku or any off-PC model; an OSM refetch; a third street split; changing the gate's bar; per-segment (rather than per-name) correctness.

## 14. Risks and accepted limits

- **The bar may not be met** even with a correct matcher (true precision 0.95 passes about half the time on ~60 drawn rows). Accepted: the bar is what makes a pass mean something.
- **Name-based scoring** can't see a wrongly clipped run of the right road; rule 4 (`two roads`) covers the main case.
- **The owner sees ~25 test golds** before reviewing the queue.
- **Aliases approved after the test run are unmeasured** (decision (a)); provenance dates show which.
- **The dev suggester comparison is small** (~10–15 near-miss rows).
- **Qwen in `serve`** uses ~9 GB VRAM for minutes after each run; bounded and skippable.
- **The map server gets its first write route.** Limited to loopback binds, a per-process token and a same-origin check, and it writes only the local queue file; applying stays a CLI step.
