# Review street search — Design

Status: approved by the owner 2026-11-15 (short design). Base: `main` at `95c1714`. Branch: `feat/review-search`.

## Intent

The review page offers only the 5 OSM names spelled closest to VECO's. When VECO uses a local name for a road with a different OSM name (`SRP Road` → Cebu South Coastal Road in Talisay), the owner cannot pick it on the page and has to ask for a CSV edit. `--apply` already accepts any street name in the group's area; the page should too.

## Approaches

- **Search over the area's names, lines on demand (picked).** `/item` sends every in-area name (names only); picking one fetches its lines.
- **Send every name with its lines in `/item`.** Rejected: some areas hold hundreds of streets; every row would load slowly.
- **Free-text decision.** Rejected: a typo would be refused only at `--apply`, rows later.

## Design

- **Names:** `suggest.candidates(g, index, boundaries, inputs, k=10**6)`, the exact set `--apply` checks a named decision against (own key and generic names already left out). `/item` adds `area_names` (sorted, case-insensitive); empty without an extract.
- **`GET /streets/review/lines?town&key&name`:** that name drawn through the alias path, like the 5 candidates. 422 missing params; 409 no such undecided row or no mentions left; 404 name not in `area_names`; `{"name", "lines"}` otherwise.
- **`POST /decide`:** a name outside `queue.choices(row)` is accepted when it is in the row's `area_names` (checked on the server). `queue.set_decision(..., extra=())` gains the allowed extra names.
- **Page:** a search box under "Or choose another street in the area". Typing lists up to 8 matching names (every typed word must appear, case and accents ignored; names starting with the query first). Click, or Enter for the top match: the name joins the list, its line is drawn, it is selected ("Yes: <name>"). Esc clears the box and leaves it, so Enter / Delete decide again. Typing never decides (the key handler already ignores text fields). A pick whose row changed while its lines loaded is dropped.
- **Unchanged:** queue CSV, `--apply`, the 5 candidates, filter, keys.

## Tests

Python: `area_names` in `/item`; `/lines` for an in-area name, 404 outside, 409 decided; `/decide` accepts an in-area name and refuses one outside (422). JS: the matching rule.
