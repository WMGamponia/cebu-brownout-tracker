# Map UI Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-screen map with dark floating cards (corner layout), a smooth 15-minute timeline, state-coloured pills and a pinned barangay card; `/history` in the same dark look.

**Architecture:** One shared `base.css` (tokens, cards, buttons, pills, popovers) for both pages. Time moves from hours to 15-minute slots in `timeline.js`; the map page's UI pieces move out of `map.js` into small modules whose pure parts are tested with `node --test`. `map.js` splits rendering into a per-day pass (build layers, place labels) and a per-slot pass (restyle only what changed) so playback at a slot every 250 ms stays smooth.

**Tech Stack:** Vanilla JS ES modules, Leaflet 1.9.4 (vendored), CSS custom properties, `node --test` (Node 22+), FastAPI static serving, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-15-ui-refresh-design.md`

Branch: `feat/ui-refresh` (already created; spec committed as `5dd4bca`). Static root below: `S = src/outages/map/static`.

## Global Constraints

- The map's own colours never change: `STYLES` and `LINE_STYLES` in `S/js/map.js`, `COLORS` / `MECO` in `S/js/history.js`, OSM tiles (spec §1, §11).
- Page JS never uses `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `getHours`, `toLocale`, `eval(`, or assigns `.href =` (`tests/test_map_app.py::test_page_js_has_no_html_sinks`). Data reaches the DOM via `textContent` / the `el()` helper.
- No `Date.now(` in page modules; "now" comes from `src.now()`. `performance.now()` and rAF timestamps are fine for animation timing.
- Nothing loaded from outside the page; all asset paths relative (`static/…`), so the snapshot works (`test_page_assets_served`).
- Keep these in both pages: `id="snapshot-banner"` and `id="snapshot-credits"` with `hidden`; the exact link `<a … href="https://www.openstreetmap.org/fixthemap" …>Report a map issue</a>`; `id="attribution"`. History keeps `id="range-pick"` (hidden) containing `<select id="range-select">`.
- JS tests are zone-independent (existing tests run under several `TZ` values): only `timeline.js` maths, never local getters.
- No new dependencies, no web fonts, no build step.
- Contrast: text ≥ 4.5:1, UI parts ≥ 3:1, translucent colours composited over white and over the active fill (spec §7).
- `prefers-reduced-motion: reduce` → no glide, no fades.
- Commits: conventional style, **no AI attribution lines** (owner rule).
- CI: `ruff check .`, `pytest -q`, `node --test "tests/js/*.test.mjs"`; run with `set -o pipefail` when chaining.

## Review Focus

1. **Window edges while stepping or playing:** the last day's 23:45 slot, an overnight outage (22:00–05:00) crossing into the next day, and a window shorter than 7 days. Expect: › at the last slot does nothing, play stops at the end, the overnight outage is "now" at 02:00 on the second day. → tests in Task 1 (`stepSlot`) and Task 3 (`playhead`).
2. **Toggling Show cancelled or AM/PM while playing or with a card open:** the pills, track bands, strip and card all follow at once, and playback isn't reset. → Task 4 (`bands` with `showCancelled=false`), Task 8 (`weekStrip` with `showCancelled=false`), browser check in Task 10.
3. **Keyboard focus on the map or a control:** ← / → pan the focused map and Space presses the focused button; the global shortcuts don't fire on top. → Task 4 (`keysApply`).
4. **A past week or the snapshot (no "today" in the window):** the default selection is the first day at 00:00, the old-data notice is hidden, and the history-week notice shows. → Task 1 (`defaultSelection`), Task 6 (`notices`).
5. **Touch device:** a tap opens the card as a sheet and no hover popup, and the sheet never covers the timeline bar. → browser check in Task 10 at 375 × 667 with touch emulation.

---

### Task 1: 15-minute slots in the time model

**Files:**
- Modify: `S/js/timeline.js` (add slot API; remove `activeInHour`, `hourLabel` once callers are gone)
- Modify: `S/js/streets.js:14-21` (`lineState` takes a slot)
- Modify: `S/js/map.js` (state.hour → state.slot; `#hour` range 0–95; play every 250 ms; legend wording)
- Modify: `S/index.html` (`#hour` `max="95"`; legend text "Outage at this time (scheduled or additional)", "Only a cancelled outage at this time", "Outage this day, not at this time")
- Test: `tests/js/timeline.test.mjs`, `tests/js/streets.test.mjs`

**Interfaces:**
- Produces (in `timeline.js`):
  - `SLOT_MIN = 15`, `SLOTS = 96`
  - `activeInSlot(o, dateStr, slot) -> boolean`: overlap of `[start, end)` with `[dayStart + slot·15 min, +15 min)`
  - `slotTime(slot) -> {hour, minute}`
  - `slotLabel(slot, fmt = "24") -> string` ("22:30" / "10:30 PM")
  - `slotOf(ms) -> {date, slot}` (Manila, floored)
  - `defaultSelection(nowMs, days) -> {day, slot}` (was `{day, hour}`)
  - `stepSlot(days, day, slot, delta) -> {day, slot} | null` (crosses midnight inside `days`; null past either end)
- Produces (in `streets.js`): `lineState(outage, day, slot, showCancelled)`

- [ ] **Step 1: Write failing tests** in `timeline.test.mjs` (replace the `activeInHour` / `hourLabel` tests):

```js
test("activeInSlot uses 15-minute overlap", () => {
  const odd = o("2026-11-12T08:10:00+08:00", "2026-11-12T09:00:00+08:00");
  assert.equal(activeInSlot(odd, "2026-11-12", 32), true);   // 08:00 slot
  assert.equal(activeInSlot(odd, "2026-11-12", 35), true);   // 08:45
  assert.equal(activeInSlot(odd, "2026-11-12", 36), false);  // 09:00: ends exactly at slot start
  assert.equal(activeInSlot(NIGHT, "2026-11-13", 8), true);  // 02:00 next day
  assert.equal(activeInSlot(NIGHT, "2026-11-12", 87), false); // 21:45
  assert.equal(activeInSlot(NIGHT, "2026-11-12", 88), true);  // 22:00
});
test("slotLabel and slotOf", () => {
  assert.equal(slotLabel(90), "22:30");
  assert.equal(slotLabel(90, "12"), "10:30 PM");
  assert.equal(slotLabel(0, "12"), "12:00 AM");
  assert.deepEqual(slotOf(parseIso("2026-11-13T00:44:59+08:00")), { date: "2026-11-13", slot: 2 });
});
test("defaultSelection rounds now down to the slot; first day at 00:00 otherwise", () => {
  const days = ["2026-11-12", "2026-11-13"];
  assert.deepEqual(defaultSelection(parseIso("2026-11-13T10:37:00+08:00"), days), { day: "2026-11-13", slot: 42 });
  assert.deepEqual(defaultSelection(parseIso("2026-11-20T10:37:00+08:00"), days), { day: "2026-11-12", slot: 0 });
});
test("stepSlot crosses midnight and stops at the window's ends", () => {
  const days = ["2026-11-12", "2026-11-13"];
  assert.deepEqual(stepSlot(days, "2026-11-12", 95, 1), { day: "2026-11-13", slot: 0 });
  assert.deepEqual(stepSlot(days, "2026-11-13", 2, -4), { day: "2026-11-12", slot: 94 });
  assert.equal(stepSlot(days, "2026-11-13", 95, 1), null);
  assert.equal(stepSlot(days, "2026-11-12", 0, -1), null);
  assert.equal(stepSlot(["2026-11-12"], "2026-11-12", 93, 4), null); // one-day window
});
```

In `streets.test.mjs`, change `lineState` calls from hours to slots (hour h → slot 4h) and add: an outage 08:30–10:00 is `"faint"` at slot 33 (08:15) and `"active"` at slot 34.

- [ ] **Step 2: Run** `node --test "tests/js/*.test.mjs"` → FAIL (`activeInSlot` is not exported).
- [ ] **Step 3: Implement** the slot API in `timeline.js` (`slotLabel` = `clockText(slotTime(slot), fmt)`), `lineState` on slots, and switch `map.js` to `state.slot`: `featureState`, `styleFor`, `popupContent`, `streetPopup`, `drawStreets` use `activeInSlot` / `lineState(…, state.slot, …)`; `renderHour` shows `slotLabel`; `step()` uses `stepSlot(days, day, slot, 1)` and stops on null; `togglePlay` restarts from `days[0]`, slot 0 at the end; the interval becomes 250 ms. Remove `activeInHour` and `hourLabel` (no callers left; `grep -rn "activeInHour\|hourLabel" src tests` is empty).
- [ ] **Step 4: Run** `node --test "tests/js/*.test.mjs"` → PASS. Run `pytest -q tests/test_map_app.py tests/test_pages.py` → PASS.
- [ ] **Step 5: Commit** `feat(map): 15-minute time slots`

---

### Task 2: Pill state and label text

**Files:**
- Modify: `S/js/labels.js` (`shortLabel` new signature, add `pillState`)
- Modify: `S/js/map.js` (`drawLabels` uses the new calls; class `map-label-<pillState>`)
- Test: `tests/js/labels.test.mjs`

**Interfaces:**
- Consumes: `activeInSlot`, `parseIso`, `dayStartMs`, `SLOT_MIN`, `shortRange` from Task 1 / `timeline.js`.
- Produces:
  - `pillState(outages, day, slot) -> "now" | "later" | "ended" | "cancelled" | null`. `outages` is the `[id, o]` pairs already filtered for the day and for Show cancelled. Cancelled outages never count as now, later or ended. The result is `"cancelled"` only when every pair is cancelled, and `null` when there are no pairs. "later" means the outage starts at or after the end of the slot.
  - `pillOutage(outages, day, slot) -> [id, o] | null`: the outage the pill speaks of. It's the first "now" by start; else the first "later"; else the last "ended"; when all are cancelled, the first.
  - `shortLabel(name, outages, day, slot, fmt = "24") -> string | null`: `"<name> · <shortRange of pillOutage>"`, plus `" +N"` where N = the other non-cancelled outages (or the other cancelled ones when all are cancelled). When all are cancelled, `" (cancelled)"` goes before ` +N`.

- [ ] **Step 1: Write failing tests:**

```js
const S = (start, end, status = "scheduled") => ({ start: `2026-11-12T${start}:00+08:00`, end: `2026-11-12T${end}:00+08:00`, status });
const D = "2026-11-12";
test("pillState: now > later > ended; cancelled never counts", () => {
  const ended = ["1", S("06:00", "09:00")];
  const cxNow = ["2", S("12:00", "15:00", "cancelled")];
  assert.equal(pillState([ended, cxNow], D, 52), "ended");          // 13:00
  assert.equal(pillState([cxNow], D, 52), "cancelled");
  assert.equal(pillState([["3", S("18:00", "22:00")], ended], D, 52), "later");
  assert.equal(pillState([["3", S("13:00", "14:00")]], D, 52), "now");
  assert.equal(pillState([], D, 52), null);
});
test("shortLabel names the relevant outage and counts the rest", () => {
  const a = ["1", S("08:00", "10:00")], b = ["2", S("22:00", "23:30")];
  assert.equal(shortLabel("Apas", [a, b], D, 80, "12"), "Apas · 10 PM–11:30 PM +1"); // 20:00 → later = b
  assert.equal(shortLabel("Apas", [a, b], D, 33), "Apas · 08:00–10:00 +1");          // 08:15 → now = a
  assert.equal(shortLabel("Pardo", [["4", S("13:00", "16:00", "cancelled")]], D, 0), "Pardo · 13:00–16:00 (cancelled)");
});
```

- [ ] **Step 2: Run** `node --test tests/js/labels.test.mjs` → FAIL.
- [ ] **Step 3: Implement** in `labels.js`. In `map.js` `drawLabels`, use `shortLabel(name, os, state.day, state.slot, state.clock)` and the class `map-label map-label-${pillState(os, state.day, state.slot)}`. For now, add plain CSS for the four classes in `map.css` (Task 5 restyles them).
- [ ] **Step 4: Run** `node --test "tests/js/*.test.mjs"` → PASS.
- [ ] **Step 5: Commit** `feat(map): pill state follows the selected time`

---

### Task 3: Playback and change-tracking maths

**Files:**
- Create: `S/js/frame.js` (pure; no DOM)
- Test: `tests/js/frame.test.mjs`
- Modify: `tests/test_pages.py::test_pages_never_read_the_clock_directly` (check every `S/js/*.js` except `vendor`, not just two files)

**Interfaces:**
- Consumes: `stepSlot`, `SLOTS` (Task 1).
- Produces:
  - `MS_PER_SLOT = 250`
  - `playhead(days, startDay, startSlot, elapsedMs) -> {day, slot, frac} | null`: position `elapsedMs / MS_PER_SLOT` slots after the start, with `frac` in [0, 1); null once past the end of the last slot of the last day.
  - `changedKeys(prev: Map<string,string>, next: Map<string,string>) -> string[]`: keys whose value differs, including keys missing from either map.
  - `dropFades(intervalsMs: number[]) -> boolean`: true when more than 2 of the frame intervals recorded in the first 2 s of play exceed 50 ms.

- [ ] **Step 1: Write failing tests:** `playhead(days2, D1, 94, 0)` → `{day: D1, slot: 94, frac: 0}`; `playhead(days2, D1, 94, 625)` → `{day: D2, slot: 0, frac: 0.5}`; `playhead(days2, D2, 95, 250)` → `null`. `changedKeys(new Map([["a","x"],["b","y"]]), new Map([["a","x"],["b","z"],["c","w"]]))` → `["b","c"]` (order-insensitive compare). `dropFades([16,16,60,70,16])` → false; `dropFades([60,70,80])` → true.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `node --test "tests/js/*.test.mjs"` and `pytest -q tests/test_pages.py` → PASS.
- [ ] **Step 5: Commit** `feat(map): playback position and change tracking`

---

### Task 4: Time bar logic

**Files:**
- Create: `S/js/timebar.js` (pure helpers + `mountTimebar`)
- Test: `tests/js/timebar.test.mjs`

**Interfaces:**
- Consumes: `activeInSlot`, `onDay`, `slotLabel`, `dayLabel`, `SLOTS` (Task 1).
- Produces (pure):
  - `bands(outages, day, showCancelled) -> boolean[96]`: `outages` is the doc's `outages` object. A slot is true when any shown outage is active in it.
  - `slotAtX(x, width) -> 0..95` (floor, clamped) and `nearestSlotAtX(x, width) -> 0..95` (round, clamped).
  - `keyStep(key, shiftKey) -> number | "toggle" | null`: ArrowLeft −1, ArrowRight +1, with Shift ±4, `" "` → `"toggle"`, anything else null.
  - `keysApply(kind) -> boolean`: kind is one of `"body" | "track" | "map" | "control" | "text" | "popover"`. True only for `"body"` and `"track"`.
  - `focusKind(el, {track, map}) -> kind` (DOM helper, not unit-tested).
- Produces (DOM): `mountTimebar(root, hooks) -> {render(view), setPlayhead(slot, frac), setPlaying(bool)}`. `hooks = {select(day, slot), stepDay(dir), stepHour(dir), togglePlay(), dragStart(), dragEnd()}`. `view = {day, slot, days, today, clock, bands}`.
  - The bar's markup is a play button, `‹ day ›`, `‹ time ›`, and a track `div[role=slider][tabindex=0]` with `aria-valuemin=0`, `aria-valuemax=95`, `aria-valuenow`, and `aria-valuetext="Tue 17 Nov, 10:30 PM"`.
  - Ticks every 3 h; band segments drawn from `view.bands`; the playhead element is positioned by `setPlayhead`.
  - Pointer drag: `hooks.dragStart`, then `select` with `slotAtX` while moving, then `select` with `nearestSlotAtX` and `hooks.dragEnd` on release.

- [ ] **Step 1: Write failing tests:**
  - `bands` for a single outage 08:30–10:00 → true exactly at slots 34–39.
  - A cancelled outage counts with `showCancelled=true` and not with `false` (Review Focus 2).
  - The overnight `NIGHT` gives slots 0–19 on day 2.
  - `slotAtX(0,960)` → 0, `slotAtX(959,960)` → 95, `slotAtX(-5,960)` → 0, `nearestSlotAtX(965,960)` → 95.
  - `keyStep("ArrowRight", true)` → 4, `keyStep(" ", false)` → `"toggle"`.
  - `keysApply("map")` → false, `keysApply("control")` → false, `keysApply("track")` → true (Review Focus 3).
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** the pure helpers and `mountTimebar` (built with the `el()` pattern; copy the 10-line helper from `map.js` into a shared `S/js/dom.js` exporting `el` and `$`, and import it in `map.js` too). **Step 4: Run** `node --test "tests/js/*.test.mjs"` → PASS.
- [ ] **Step 5: Commit** `feat(map): timeline bar module`

---

### Task 5: Shared look and the contrast test

**Files:**
- Create: `S/base.css`
- Create: `tests/js/contrast.test.mjs`

**Interfaces:**
- Produces these tokens on `:root` in `base.css`, each a `#rrggbb` or `rgba(r, g, b, a)` literal, one per line as `--name: value;`. Starting values are given; adjust them until the test passes.
  - `--bg: #111418`
  - `--card: rgba(24, 28, 33, 0.92)`
  - `--card-dim: rgba(24, 28, 33, 0.72)`
  - `--text: #f1f3f5`
  - `--muted: #aab4bd`
  - `--hairline: rgba(255, 255, 255, 0.12)`
  - `--accent: #f28c28`
  - `--accent-strong: #c2410c`
  - `--warn-bg: #3a2a12`
  - `--warn-text: #ffd59a`
  - `--error-bg: #3d1515`
  - `--error-text: #ffb4ab`
  - `--focus: #7cc4ff`
  - `--track: rgba(255, 255, 255, 0.10)`
  - `--band: #f28c28`
- Produces these classes:
  - `.card`: radius 12px, shadow, `background: var(--card)`, plus `backdrop-filter: blur(8px)` inside `@supports`.
  - `.round-btn`: 40px; 44px under `@media (pointer: coarse)`.
  - `.pill` and `.pill-now`, `.pill-later`, `.pill-ended`, `.pill-cancelled` (spec §4).
  - `.popover`, `.notice`, `.notice-warn`, `.notice-error`, `.muted`, `:focus-visible` ring.
  - Restyled Leaflet: `.leaflet-popup-content-wrapper`, `.leaflet-popup-tip`, `.leaflet-bar a`, `.leaflet-control-attribution`.
  - System font stack; one type scale (13 / 14 / 16 / 20 px).

- [ ] **Step 1: Write the failing test.** Read `S/base.css`, parse each `--name: value;`, and composite every rgba value over each backdrop with `c = a·fg + (1−a)·bg`, using WCAG relative luminance. The backdrops are white `#ffffff` and the active fill `#f28c28` at 0.45 over white.
  - Pairs at ≥ 4.5:1: `text/card`, `muted/card`, `accent/card`, `muted/card-dim`, `warn-text/warn-bg` (over card), `error-text/error-bg` (over card).
  - Pairs at ≥ 3:1: `focus/card`, `focus/#ffffff`, `band/track` (track over card), `card/#ffffff` (the button against a white map).
  - Fail with the pair's name and ratio.
- [ ] **Step 2: Run** `node --test tests/js/contrast.test.mjs` → FAIL (no `base.css`).
- [ ] **Step 3: Write `base.css`** with the tokens and classes above.
- [ ] **Step 4: Run** → PASS (tune the values if a pair fails, never the thresholds).
- [ ] **Step 5: Commit** `feat(ui): shared dark look and contrast test`

---

### Task 6: Map page shell: full-bleed map, cards, menus, legend, time bar

**Files:**
- Rewrite: `S/index.html`, `S/map.css` (layout only; shared parts come from `base.css`, linked before `map.css`)
- Create: `S/js/cards.js` (`notices` pure + `mountCards` DOM), `S/js/menu.js`
- Modify: `S/js/map.js` (remove day tabs, panel, `#controls` wiring; mount the time bar, menus, cards; zoom control top-right)
- Test: `tests/js/cards.test.mjs`; `tests/test_map_app.py::test_page_assets_served` (add `/static/base.css`, `/static/js/timebar.js`, `/static/js/cards.js`, `/static/js/menu.js`, `/static/js/frame.js`, `/static/js/dom.js`)

**Interfaces:**
- Consumes: `mountTimebar`, `bands`, `keyStep`, `keysApply`, `focusKind` (Task 4); `stepSlot` (Task 1); `.card`, `.round-btn`, `.popover`, `.notice*` (Task 5).
- Produces:
  - `notices(meta, nowMs, clock) -> Array<{id, kind: "info"|"warn", text}>` in this order:
    1. `history` (past window: the `historyLabel` text)
    2. `old` (only when not past: the existing no-check / > 15 h texts, word for word)
    3. `stale` (when `meta.stale_posts` is non-empty: the existing sentence; `mountCards` appends the post links)

    Snapshot, week-missing and load-error notices stay element-driven (`#snapshot-banner`, `#week-missing`, `#banner` with `#retry`), all inside `#notices`.
  - `mountPopover(button, panel) -> {open(), close()}`:
    - sets `aria-expanded` and `aria-controls`
    - Esc and a click outside close it, and focus returns to the button
    - opening one popover closes any other
- Page structure (ids the later tasks use):
  - `#map` fills `100dvh`.
  - `#title-card` contains `h1`, `#asof`, `#outage-count` and a fold button `#title-fold`. It starts folded under 600 px.
  - `#notices` holds the notice cards, outside the fold.
  - `#menu-btn` opens `#menu`. The menu holds:
    - `#week-prev`, `#week`, `#week-next`, `#week-today`
    - `#show-cancelled`, `#clock-12`
    - `#streets-toggle` with `#show-streets` (hidden unless drawing is on)
    - `#unplaced-btn` labelled "Not on the map (N)"
    - the History link
  - `#info-btn` opens `#info`, which holds `#attribution`, the fixthemap link, the data source, Leaflet credits and `#snapshot-credits`.
  - `#legend` contains a row of swatches: Now, Today, Cancelled, None, MECO, not covered. Each carries the full sentence as `title`. `#legend-lines` is hidden unless drawing is on. Under 600 px the legend collapses behind `#legend-btn`.
  - `#timebar` (mounted by `mountTimebar`).
  - `#left-slot` holds either `#unplaced-card` or the barangay card (Task 8), never both.
- Global keys: a `keydown` listener on `document` acts only when `keysApply(focusKind(e.target, …))` is true, then calls `stepSlot` or toggles play.

- [ ] **Step 1: Write the failing `notices` tests:**
  - `meta.days` without today → `[{id:"history", …}]` and no `old` (Review Focus 4).
  - `last_check_at` null → `old` with "No data check has run yet, so this map may be empty or out of date."
  - Checked 20 h ago → `old` beginning "This data may be out of date: the last check was 20 hours ago".
  - One stale post → `stale`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `cards.js`, `menu.js`, the new `index.html` / `map.css`, and the `map.js` wiring. `renderTopbar` becomes `mountCards`' render. `#outage-count` shows "N outages this week", where N is the outages in `doc.outages` with status other than cancelled.
- [ ] **Step 4: Run** `node --test "tests/js/*.test.mjs"` and `pytest -q tests/test_map_app.py tests/test_pages.py` → PASS. Run `python -m outages map --port 8790` from the repo root. It reads the real `data/outages.db` read-only; record `sha256sum data/outages.db` before and after, and the two must match, as in Phase 5. Then open the page in the preview at desktop and 375 px: the map fills the screen, every control works, and the console is clean. On a 375 × 667 screen with the title folded and no card open, nothing floating covers the map's centre.
- [ ] **Step 5: Commit** `feat(map): full-bleed map with floating cards`

---

### Task 7: Per-slot rendering, glide and fades

**Files:**
- Modify: `S/js/map.js`, `S/map.css`
- Test: (logic is covered by Task 3's tests; this task's check is the browser and the budget)

**Interfaces:**
- Consumes: `playhead`, `changedKeys`, `dropFades`, `MS_PER_SLOT` (Task 3); `pillState` (Task 2); `lineState` (Task 1).
- Produces in `map.js`:
  - `renderDay()`, run on load, day change, toggles, AM/PM and week change. It:
    1. builds the street layer once, with one polyline per drawn street for the day, kept in `state.lines: Map<key, {pl, outage}>`;
    2. builds and places the labels;
    3. renders the time bar's bands;
    4. then calls `renderSlot()`.
  - `renderSlot()`. It:
    1. computes `next = Map(psgc → featureState + lit)` for barangays, line states for `state.lines` and `pillState` for each label;
    2. applies `setStyle` / `className` only to the `changedKeys`;
    3. updates the time bar, `#asof`, the open popup and the barangay card (Task 8).
  - Labels are re-placed on `moveend zoomend resize`, on `renderDay()` and on pause. They are not re-placed per slot while playing.
  - The play loop:
    - Uses `requestAnimationFrame`, starting from `{day, slot}` and `t0`.
    - Each frame calls `playhead()`, and `timebar.setPlayhead(slot, frac)`.
    - When the slot changes, it sets `state.day` / `state.slot` and calls `renderSlot()`, or `renderDay()` when the day changed.
    - On `null` it stops at the last slot.
    - Any `select`, step, drag, key or week change pauses it.
  - Fades:
    - `map.css` puts `transition: fill-opacity .2s, stroke .2s, stroke-opacity .2s, fill .2s` on `.fades .leaflet-interactive`, and `transition: opacity .2s, color .2s, border-color .2s` on `.fades .pill`.
    - `.fades` sits on `#map` unless `matchMedia("(prefers-reduced-motion: reduce)")` matches, or `dropFades()` trips during the first 2 s of a play (then it stays off until reload).
    - Under reduced motion, `setPlayhead` is called with `frac = 0` (no glide).
  - Debug switch: `?debug=perf` registers a `PerformanceObserver({type: "longtask"})` that `console.warn`s each long task's duration and logs `max longtask: N ms` when play stops.

- [ ] **Step 1:** Split `render()` into `renderDay()` / `renderSlot()` as above. Remove the per-slot `drawStreets()` / `drawLabels()` calls.
- [ ] **Step 2:** Replace `setInterval` playback with the rAF loop, and add `.fades` and the reduced-motion and `dropFades` handling.
- [ ] **Step 3: Run** `node --test "tests/js/*.test.mjs"` and `pytest -q` → PASS.
- [ ] **Step 4: Measure** on the real DB (read-only, as in Task 6) with streets drawn. Open the page with `?debug=perf` in Chrome DevTools (mid-tier mobile, 4× CPU) and play a full week. Expected: `max longtask` ≤ 50 ms.
  - If it fails: switch fades off and re-measure.
  - If it still fails: hold street lines at their state at play start and update them on pause, then re-measure.
  - If it still fails: **stop and ask the owner** (spec §5.2).
  - Record the numbers for the PR.
- [ ] **Step 5: Commit** `perf(map): restyle only what changed; smooth playback`

---

### Task 8: Barangay card

**Files:**
- Create: `S/js/bcard.js` (pure `weekStrip`, `dayOutages`, plus `mountBarangayCard`)
- Modify: `S/js/map.js` (a click pins the card instead of opening a click popup; the hover popup is unchanged), `S/map.css`
- Test: `tests/js/bcard.test.mjs`

**Interfaces:**
- Consumes: `onDay`, `activeInSlot`, `byStart`, `rangeLabel`, `safePostUrl` (`timeline.js`); `entityList` (`text.js`); `el` (`dom.js`).
- Produces:
  - `weekStrip(outageIds, outages, days, showCancelled) -> Array<{day, state: "outage"|"cancelled"|"none"}>`, one entry per day in `days`. A day is "outage" when any shown non-cancelled outage is on it, "cancelled" when only cancelled ones are.
  - `dayOutages(outageIds, outages, day, slot, showCancelled) -> Array<{id, o, now: boolean}>`, by start.
  - `mountBarangayCard(slotEl, hooks) -> {show(props), hide(), render(view), isOpen(), psgc()}`:
    - `hooks = {selectDay(day), close()}`; `view = {day, slot, days, clock, showCancelled, outages, today}`.
    - Under 600 px or `(pointer: coarse)` it renders as a sheet above `#timebar`, using the class `sheet`.
    - Clicking a strip cell calls `selectDay(day)`; `map.js` then selects the start slot of that day's first shown outage (`slotOf(parseIso(o.start))` when it falls on that day, else 0).
    - ✕, Esc and a click on empty map all call `close()`.
    - Opening the card closes `#unplaced-card`, and the reverse.
    - MECO features show "MECO area, not covered" and no strip.
    - The card's outage block reuses the existing popup content builders (`outageBlock`, `placesLine`), moved from `map.js` into `bcard.js` and shared with the hover popup.

- [ ] **Step 1: Write the failing tests:**
  - `weekStrip` over a 3-day window with a scheduled outage on day 1 and a cancelled one on day 3 → `["outage", "none", "cancelled"]`; with `showCancelled=false` → `["outage", "none", "none"]` (Review Focus 2).
  - A 7-day window with no outages → all `"none"`.
  - `dayOutages` marks `now` only for the outage active at the slot.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `node --test "tests/js/*.test.mjs"` → PASS, then check in the browser:
  - desktop: hover still shows the popup after 500 ms, a click pins the card, the card follows a change of day or time, and ✕ / Esc / empty-map close it;
  - 375 px with touch emulation: a tap opens the sheet, and the timeline stays visible.
- [ ] **Step 5: Commit** `feat(map): pinned barangay card with a week strip`

---

### Task 9: `/history` in the shared look

**Files:**
- Modify: `S/history.html` (link `base.css` first; sticky `.top` bar; a notice card for the snapshot banner and the "scheduled, not measured" note; map, legend, detail, table and chart wrapped in `.card`)
- Modify: `S/history.css` (history-only rules; shared ones removed), `S/js/history.js` (an inline bar cell per table row: width = hours / top row's hours; the `#legend`'s "no scheduled outage" swatch gets a visible outline on dark)
- Modify: `tests/js/contrast.test.mjs` (add `#months` bar `#fdbb84` and worst bar `#e34a33` against `--card` at ≥ 3:1, and the chart's axis text `--muted` on `--card` at ≥ 4.5:1)

**Interfaces:**
- Consumes: `base.css` tokens and classes (Task 5).
- Keeps: `COLORS`, `MECO` and the polygon styles in `history.js` unchanged. If a month-bar colour fails against the dark card, change the CSS bar colour in `history.css` (the chart is not the map), and note it in the PR.

- [ ] **Step 1:** Add the contrast pairs → run `node --test tests/js/contrast.test.mjs` → FAIL until the history colours are in `base.css` / `history.css`.
- [ ] **Step 2:** Restyle the markup and CSS, and add the inline bars.
- [ ] **Step 3: Run** `node --test "tests/js/*.test.mjs"` and `pytest -q tests/test_pages.py tests/test_map_app.py` → PASS. In the browser: `/history` at desktop and 375 px, the range form works, a barangay click fills the detail card, and the console is clean.
- [ ] **Step 4: Commit** `feat(history): shared dark look`

---

### Task 10: Real browser pass, snapshot and docs

**Files:**
- Modify: `tests/test_snapshot.py::test_build_files_layout` (assert `static/base.css`, `static/js/timebar.js`, `static/js/bcard.js` are in the built files)
- Modify: `README.md` (screenshots replaced; mention 15-minute steps and the keys), `ROADMAP.md` (tick the six Map UI refresh items with notes; add a progress-log entry)

- [ ] **Step 1:** Add the snapshot assertion → `pytest -q tests/test_snapshot.py` → PASS (the snapshot copies all of `static/`).
- [ ] **Step 2: Browser pass** on the real-DB copy at desktop and 375 × 667. Check each of these:
  - play a full week;
  - drag the track;
  - ← / → / Shift / Space with focus on the page, the map and a menu button (Review Focus 3);
  - toggle Show cancelled and AM/PM while playing and with a card open (Review Focus 2);
  - week ◀ ▶ Today, and a past week (Review Focus 4);
  - touch emulation: tap → sheet, no hover popup, sheet above the timeline (Review Focus 5);
  - reduced motion;
  - snapshot mode (build a snapshot into a temp folder with `python -m outages snapshot --out <tmp>`, serve it with `python -m http.server`, open `index.html` and `history.html`);
  - console clean.

  Fix what fails, test-first where a pure function is at fault.
- [ ] **Step 3:** Take screenshots (desktop and phone, map and history; before and after) for the README and the PR.
- [ ] **Step 4: Run the full suite:** `set -o pipefail; ruff check . && pytest -q && node --test "tests/js/*.test.mjs"` → all green.
- [ ] **Step 5: Commit** `docs: map UI refresh in README and ROADMAP`
- [ ] **Step 6: Owner check (stop):** the owner spends ten minutes on the real page, desktop and phone (spec §9). Apply their fixes.
- [ ] **Step 7:** Get one fresh whole-branch review on the most capable model; fix the important findings test-first.
- [ ] **Step 8:** Push and open a PR (no AI attribution lines) with the before/after screenshots and the performance numbers from Task 7. Refreshing and publishing `site/` waits for the owner's OK (spec §11).
