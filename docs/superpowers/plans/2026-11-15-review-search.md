# Review Street Search Implementation Plan

> Native execution. Spec: `docs/superpowers/specs/2026-11-15-review-search-design.md`

**Goal:** pick any in-area OSM street on `/streets/review`, not only the 5 closest spellings.

## Global Constraints

- The allowed names are exactly `suggest.candidates(g, index, boundaries, inputs, k=10**6)` for the row's group (what `--apply` accepts).
- Up to 8 results; Enter = top result; Esc clears and blurs.
- No AI attribution lines in commits or the PR.

## Review Focus

- A name outside the area posted straight to `/decide` → 422, queue untouched. → Task 1 test.
- No OSM extract: `area_names` empty, `/lines` 409/404 not 500. → Task 1 test.
- Row decided or gone between search and pick → page drops the stale pick (load guard / current check). → Task 2 code, browser check.
- Query with accents or dots ("Osmena", "a soriano") still matches. → Task 2 test.
- Enter inside the box never posts a decision. → Task 2, browser check.

### Task 1: Server

Files: `src/outages/map/review.py`, `src/outages/streets/queue.py`, `tests/test_map_review.py`.

- Tests first: `test_item_lists_area_names`, `test_lines_for_an_area_name`, `test_lines_refuses_a_name_outside_the_area` (404), `test_lines_409_when_decided`, `test_decide_accepts_an_area_name` (204, CSV decision = the name), `test_decide_refuses_a_name_outside_the_area` (422, decision empty).
- `queue.set_decision(path, town, key, decision, extra: Collection[str] = ())`.
- `review.py`: factor the group/scope work of `_item` into `_context(town, key)`; `_area_names(ctx)`; `/lines`; `/decide` passes `extra=_area_names(...)` only for a decision not y/n/skip and not in `queue.choices(row)`.
- Commit `feat(review): any street in the area can be chosen (server)`.

### Task 2: Page

Files: `reviewlogic.js` (`foldName(s)`, `searchNames(names, query, limit = 8)`), `review.js`, `review.html`, `review.css`, `tests/js/review.test.mjs`.

- Tests first: matching (case, accents, dots, every word), starts-with ranking, limit, empty query → [].
- Page: `#search` input + `#search-results`; picking adds a candidate button and polyline (`addCandidate`), then `choose(name)`.
- Commit `feat(review): search box for any street in the area`.

### Task 3: Docs and checks

README review paragraph, ROADMAP line; ruff, pytest, node; browser check on a real-data copy (SRP-like case: a far-spelled name in a Talisay row).
