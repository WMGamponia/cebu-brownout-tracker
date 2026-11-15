# Review filter — Design

Status: approved by the owner 2026-11-15 (short design). Base: `main` at `03fcdb0` (PR #11 merged). Branch: `feat/review-filter`.

## Intent

Affirming streets on `/streets/review` is tedious: 924 undecided rows, 734 of them names seen once. Most one-off names are spellings OSM simply lacks, so they rarely pay back the time. Let the owner review the names that recur first and hide the long tail, without deleting anything from the queue.

Success: the default view holds the ~200 rows seen at least twice (about half the unmatched mentions), and a hidden name surfaces by itself once VECO uses it again.

## Findings that shaped it

- **Queue mention counts are frozen.** `mentions` is written when the suggester first queues a name; re-runs keep existing rows as they are. A filter on that column would hide a recurring name forever. The list endpoint therefore counts live.
- **Recurring names rarely have a pick.** Of 204 rows with ≥ 2 mentions, 2 have a Qwen pick; the 179 picks are on one-off names. The default view is mostly "choose a candidate or No", which is accepted.
- **Rows with nothing left to draw** (every mention now matched, e.g. after an alias) already make `/item` answer 409 and the page show "Already decided or queue busy; moving on". Dropping them from the list removes that noise.

## Approaches

- **Filter in the page (picked).** The list is ~1,000 rows; filtering in the browser makes switching instant and keeps the endpoint simple.
- **`?min=` on `/streets/review/items`.** Rejected: a round trip per change and a second code path for the same rule, for no gain at this size.
- **Delete or auto-reject one-off rows in the CSV.** Rejected: it loses names that may recur, and a reject would stop the suggester from ever asking again.

## Design

- **`GET /streets/review/items`:** `mentions` is the live number of `not_found` mentions of the row's (town, key), from `suggest.groups(conn, set(), set())` (the same grouping `/item` uses). Rows with 0 live mentions are left out. Order unchanged: rows with a pick first, each part by mentions (now the live count).
- **Page control:** "Seen at least" select with 1 / 2 / 3 / 5 times, above the counter; default 2; remembered per browser in `localStorage` (wrapped in try/catch; default when unavailable).
- **Counter:** "N left · M hidden" (only the "· M hidden" part when M > 0). Hidden = undecided rows below the threshold.
- **Navigation:** next, skip and Back move only through visible rows; Back to a row now hidden still works (the step is in the history). Changing the threshold reloads the first visible undecided row; with none visible the empty state says how many are hidden and to lower the filter.
- **Unchanged:** the CSV, `--apply`, decide / undo endpoints, keys.

## Non-goals

No server-side filter, no change to the suggester's merge (the CSV `mentions` stays as written), no new decision types.

## Tests

- Python: `/items` returns live counts (a mention matched after queueing lowers it), drops rows with 0, keeps order.
- JS (`reviewlogic.js`): the visibility rule, `nextIndex` skipping hidden rows, the counter text.

## Docs

README review section (the filter, default 2), ROADMAP progress line.

## Left to the builder

Exact markup and styling of the select; how the empty-state text reads.
