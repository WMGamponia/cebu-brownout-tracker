# Review Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `/streets/review` hide names seen fewer than N times (default 2), counted live from the DB.

**Architecture:** `GET /streets/review/items` replaces the queue's frozen `mentions` with the live `not_found` count from `suggest.groups` and drops rows at 0. The page filters that list in the browser with a "Seen at least" select; pure logic lives in `reviewlogic.js`.

**Tech Stack:** Python 3.14, FastAPI, pytest; plain ES modules, `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-15-review-filter-design.md`

## Global Constraints

- Choices 1 / 2 / 3 / 5, default **2**; remembered in `localStorage` key `review.minMentions`, every access in try/catch.
- Counter: "N left" or "N left · M hidden" (M > 0 only); "1 left" singular as today.
- Order unchanged: pick first, then mentions (live) descending.
- No change to the CSV, `--apply`, decide / undo, keys.
- No AI attribution lines in commits or the PR.

## Review Focus

- A queue row whose (town, key) has no group (all mentions matched): absent from `/items`, never 409s on the page. → Task 1 test.
- Live count differs from the CSV count: the page shows and filters by the live one. → Task 1 test.
- Threshold above every row: empty state names the hidden count instead of "all done". → Task 2 test on `emptyText`.
- Back to a row that is now hidden: still loads it (history holds the index). → Task 2 test on `nextIndex` not being used by Back; manual check in Task 3.
- `localStorage` throws or holds junk ("7", "abc"): falls back to 2. → Task 2 test on `readMin`.

---

### Task 1: Live mention counts in `/streets/review/items`

**Files:**
- Modify: `src/outages/map/review.py` (`items()`; `_mentions` goes if unused)
- Test: `tests/test_map_review.py`

**Interfaces:**
- Produces: each item's `mentions` = live `not_found` count of its (town_psgc, veco_key); rows with 0 omitted. Keys unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def _set_status(world, h, seq, status):
    c = db.connect(world["db"])
    with c:
        c.execute(
            "UPDATE street_matches SET status = ? WHERE areas_hash = ? AND seq = ?",
            (status, h, seq),
        )
    c.close()


def test_items_count_live_mentions(world):
    _set_status(world, "hC", 5, "matched")  # CSV still says 2
    items = _client(world).get("/streets/review/items").json()
    assert {i["veco_key"]: i["mentions"] for i in items}["gorordo avnue"] == 1


def test_items_drop_rows_with_no_mentions_left(world):
    _set_status(world, "hA", 6, "matched")
    keys = [i["veco_key"] for i in _client(world).get("/streets/review/items").json()]
    assert keys == ["gorordo avnue"]
```

Update `test_items_picks_first_by_mentions`: insert DB mentions with `_add_streets` (a ×9, b ×2, c ×5, d ×3 on distinct hashes) with the CSV `mentions` all `1`; expected order stays `["c street", "b street", "a street", "d street"]`, and `mentions` equals `[5, 2, 9, 3]`.

- [ ] **Step 2: Run** `pytest tests/test_map_review.py -q` — the two new tests and the updated one FAIL.
- [ ] **Step 3: Implement** in `items()`: open `ro()`, build `{(g.town_psgc, g.key): len(g.mentions) for g in groups(conn, set(), set())}`, use it for `mentions`, skip rows not in it. Keep the existing 500 on any exception.
- [ ] **Step 4: Run** `pytest tests/test_map_review.py -q` — PASS.
- [ ] **Step 5: Commit** `feat(review): count mentions live and drop rows with none left`

### Task 2: "Seen at least" filter on the page

**Files:**
- Modify: `src/outages/map/review_static/reviewlogic.js`, `review.js`, `review.html`, `review.css` (if needed)
- Test: `tests/js/review.test.mjs`

**Interfaces:**
- Produces in `reviewlogic.js`:
  - `export const MIN_CHOICES = [1, 2, 3, 5]; export const DEFAULT_MIN = 2;`
  - `visible(item, min) -> boolean` — `item.mentions >= min`
  - `nextIndex(items, i, min = 1)` — as today, also skipping items not `visible`
  - `readMin(raw: string | null) -> number` — the number if in `MIN_CHOICES`, else `DEFAULT_MIN`
  - `counterText(left: number, hidden: number) -> string`
  - `emptyText(hidden: number) -> string | null` — null when 0 hidden, else e.g. "Nothing left at this filter: 720 hidden. Lower “Seen at least” to see them."

- [ ] **Step 1: Write the failing tests**

```js
test("nextIndex skips items under the threshold", () => {
  const xs = [{ decided: false, mentions: 3 }, { decided: false, mentions: 1 }, { decided: false, mentions: 2 }];
  assert.equal(nextIndex(xs, 0, 2), 2);
  assert.equal(nextIndex(xs, 2, 2), 0);
  assert.equal(nextIndex(xs, 0, 5), -1);
  assert.equal(nextIndex(xs, 0), 1); // default 1: unchanged behaviour
});
test("readMin falls back to 2", () => {
  assert.equal(readMin("3"), 3);
  for (const raw of [null, "", "7", "abc", "0"]) assert.equal(readMin(raw), 2);
});
test("counterText", () => {
  assert.equal(counterText(204, 720), "204 left · 720 hidden");
  assert.equal(counterText(1, 0), "1 left");
  assert.equal(counterText(0, 3), "0 left · 3 hidden");
});
test("emptyText", () => {
  assert.equal(emptyText(0), null);
  assert.match(emptyText(720), /720 hidden/);
});
```

Existing `nextIndex` tests keep passing (items without `mentions` must count as visible at min 1: treat a missing count as visible when `min <= 1`).

- [ ] **Step 2: Run** `node --test tests/js/review.test.mjs` — FAIL (exports missing).
- [ ] **Step 3: Implement** the exports. In `review.html` add `<label>Seen at least <select id="min-mentions"></select> times</label>` beside `#left`. In `review.js`: `let min = readMin(stored())` with try/catch around `localStorage`; fill the select from `MIN_CHOICES`; `left()` counts undecided visible and undecided hidden and writes `counterText`; every `nextIndex(items, …)` call passes `min`; on `change` save, update the counter and `load(nextIndex(items, -1, min))`; when that is -1 show the empty state with `emptyText(hidden)` if non-null. `back()` keeps loading `step.index` directly. Initial load uses `nextIndex(items, -1, min)` instead of index 0.
- [ ] **Step 4: Run** `node --test "tests/js/*.test.mjs"` — PASS.
- [ ] **Step 5: Commit** `feat(review): filter the queue by how often a name is seen`

### Task 3: Docs, full checks, browser check, PR

**Files:**
- Modify: `README.md` (review section), `ROADMAP.md` (progress log 2026-11-15)

- [ ] **Step 1:** README: one line on "Seen at least" (default 2, live counts, hidden rows stay in the queue). ROADMAP: progress line with the numbers (924 undecided → 204 shown at 2).
- [ ] **Step 2: Run** `ruff check . && ruff format --check . && pytest -q && node --test "tests/js/*.test.mjs"` — all pass.
- [ ] **Step 3: Browser check** against a copy of the real data (scratchpad launcher, port 8773): counter reads "N left · M hidden", switching to 1 shows all, choice survives a reload, Back after a decision works, `/items` time on the real DB noted.
- [ ] **Step 4: Commit** `docs: review filter` and push `feat/review-filter`; open the PR (no attribution lines).
