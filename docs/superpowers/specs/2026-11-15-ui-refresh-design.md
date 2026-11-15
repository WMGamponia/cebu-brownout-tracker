# Map UI refresh — Design

Status: approved by the owner 2026-11-15. Base: `main` at `9f67e51`. Branch: `feat/ui-refresh`. One session, one PR (project workflow).

## 1. Intent

The owner, 2026-11-13: the page around the map looks bland. Take cues from zoom.earth's interface elements (the ideas only, not its branding or exact layout). **The map itself stays as is:** OSM tiles, the barangay shading colours, the street line styles.

Success: the map page reads as one full-screen map with a few dark floating controls, on desktop and on a phone in daylight; `/history` looks like the same app; playing the week moves smoothly and at a finer grain than today's hourly jumps.

The ROADMAP's "Map UI refresh" items (full-bleed map, bottom timeline bar, pills with state colours, barangay card, compact legend + ⓘ credits, AA contrast) are all in this phase. The reading aids from PR #3 (labels, hover outline, 500 ms hover popups, AM/PM) stay and are restyled.

## 2. Decisions (owner, 2026-11-15)

- **Scope: the map page and `/history`.** The review page (`/streets/review`) is not touched.
- **Approach: a shared look + split `map.js`** (see §3).
- **Layout: corners** (mockup A). Title card top-left, ⋮ and ⓘ round buttons top-right, legend bottom-left, timeline bar bottom-centre, barangay card on the left (a sheet above the timeline on a phone).
- **Card style: dark.** Near-black translucent cards, buttons and pills over the light map; `/history` goes dark too.
- **Time steps: 15 minutes, not hours.** The map answers "is there an outage at this moment" for 15-minute slots (VECO's times often start at :30). Playback is smooth: the playhead glides and the map changes with short fades.

Mockups: `.superpowers/brainstorm/125-1794730664/content/` (`layout.html` option A, `style.html` option 2). Not committed; the descriptions here are the reference.

## 3. Approaches

- **Shared look + split `map.js` (picked).** One `base.css` holds tokens and the card, button, pill and menu styles for both pages. The map page's markup is rebuilt as floating cards; the UI pieces move out of `map.js` (715 lines) into small modules with `node --test` tests. Timeline maths, labels and hover logic stay where they are and are re-wired. Bigger diff, but `map.js` stops growing and the new card and time bar are testable.
- **Restyle in place.** Same CSS and markup, all re-wiring and the barangay card inside `map.js`. Rejected: `map.js` passes ~850 lines and the card's logic is hard to test.
- **A framework or build step (Tailwind, Preact).** Rejected: the project is vanilla JS with vendored files and no build; the snapshot copies files as they are.

## 4. The look (`base.css`, shared)

- **Tokens on `:root`** (names are the builder's; these are the roles): page background (`#111418`-ish), card background (near-black, ~92 % opaque, `rgba(24,28,33,.92)`-ish), card text (`#f1f3f5`-ish), muted text, hairline, accent orange (from today's `#f28c28` / `#c2410c` family), warning and error tints that work on dark, focus ring.
- **Card:** rounded (~12 px), soft dark shadow, the translucent background with `backdrop-filter: blur()` where supported (a solid fallback must still pass contrast on its own).
- **Round button:** 40 px on desktop, ≥ 44 px touch target on a phone.
- **Pills** (map labels):
  - **now** (an outage at the selected time): dark pill, orange text, orange edge;
  - **later today** (on this day, starts after the selected time): dark pill, white text;
  - **ended today** (on this day, ended before the selected time): dark pill, muted text. *(My call: the ROADMAP named three states; a fourth is needed once labels follow the time.)*
  - **cancelled** (only cancelled outages that day): dimmed pill, muted struck-through text.
  - **Cancelled outages never count** for now, later or ended *(my call)*. A pill is "cancelled" only when every outage that day is cancelled. So a barangay whose scheduled outage ended this morning and whose cancelled one would run now shows **ended** (while its shading is grey "cancelled", as `featureState` decides today; the pill speaks of real outages, the shading of the hour).
  - A barangay with several (non-cancelled) outages that day takes the state of the most relevant one: now > later > ended. **Its text shows that outage's range plus a count of the rest** *(my call)*: "Apas · 10 PM–6 AM +1", replacing today's "Apas · 2 outages", because the pill now follows the time. With Show cancelled off, cancelled outages are left out entirely (as today).
- **Menus / popovers:** dark cards anchored to their button.
- **Type:** the system font stack stays; one size scale for both pages. No web fonts (nothing new leaves the page).
- **The map's own colours do not change** (`STYLES`, `LINE_STYLES` in `map.js`, the history bins). The Leaflet popups are restyled dark to match.

## 5. Map page

### 5.1 Layout

The map fills the viewport (`100dvh`). Everything else floats over it:

- **Title card** (top-left): "Cebu Brownout Map", last update ("Updated 6:02 AM"), outage count for the window. A ▾ folds it to the name only. On a phone (< 600 px) it starts folded.
- **Notices** stack in one card under the title card, replacing today's full-width bars: snapshot banner, history-week banner, week missing from the snapshot, old-data warning (> 15 h), stale-post caveat with links, load error with **Retry**. Wording unchanged. A notice is never hidden by folding the title card.
- **⋮ menu** (top-right): week picker (◀ date ▶ Today), Show cancelled, AM/PM time, Show streets (only when drawing is on), "Not on the map (N)" (opens the list as a card), History link.
- **ⓘ button:** attribution, data source, OSM/Leaflet credits, snapshot credits — the content of today's credits section.
- **Zoom +/−:** under ⋮/ⓘ on the right, dark. Leaflet's attribution control stays visible (OSM's licence requires it), restyled small and dark at the bottom-right.
- **Legend** (bottom-left): a compact card with swatches in a row — outage now, outage this day, cancelled, none, MECO — plus the three line swatches only when drawing is on. Labels shortened ("Now", "Today", "Cancelled", "None", "MECO, not covered"); the full sentences move to `title` / the ⓘ card. On a phone the legend collapses behind a small swatch button.
- **Timeline bar** (bottom-centre, between the legend and the right edge): see §5.2.
- **Barangay card:** see §5.3.
- **"Not on the map" card:** opened from the menu; the existing list (reasons, links) in a card on the left. It shares the left slot with the barangay card: opening one closes the other.

Nothing floating may cover the map's centre on a 375 × 667 phone with the title folded and no card open.

### 5.2 Time and the timeline bar

**Time model.** The selection is a day plus a **slot**: a 15-minute step, 0–95 (00:00, 00:15, … 23:45). An outage is **active** in a slot when its interval overlaps `[slot start, slot start + 15 min)` (same overlap rule as today's `activeInHour`, at 15 minutes). Shading, pills, street lines (`streets.js`), popups and the card all use the slot. *(Overlap, not "at the instant", so an outage of 08:10–09:00 still shows in the 08:00 slot; VECO's odd minutes never disappear.)*

- `timeline.js` gains the slot functions (`activeInSlot`, slot ↔ clock text, slot of a Manila time). `activeInHour` is kept for anything still hourly (the builder decides whether anything is) or removed with its callers.
- Default selection: now in Manila, rounded down to the slot.
- Wording: "Outage in this hour" → "Outage at this time"; the time label shows the slot ("10:30 PM", or "22:30").

**The bar** (one floating card):

- **Play / pause** button.
- **‹ day ›** stepper ("Tue 17 Nov"); ‹ on the first day / › on the last day are disabled. The full day list is the stepper plus the week picker in the menu; the day tabs go.
- **‹ time ›** stepper ("10:30 PM"): steps **one hour**, keeping the minutes, crossing midnight into the next/previous day. *(My call: 96 taps per day on a phone is too many; the track and keys give the 15-minute grain.)*
- **24-hour track** for the selected day: tick marks every 3 h, a shaded band for every slot with at least one shown outage (any barangay; cancelled ones only when Show cancelled is on), and the playhead. Click or drag sets the time; dragging moves the playhead freely and snaps to the nearest slot on release (the map follows the slot under the playhead while dragging).
- **Keys** *(my call on scope)*: ← / → one slot, Shift+← / → one hour, Space play/pause — only when focus is on the page body or inside the timeline bar. Not when the Leaflet map has focus (its arrows pan), not on any other button, checkbox or field (Space presses them), and not while a menu or card has focus. The track is a `role="slider"` with `aria-valuetext` "Tue 17 Nov, 10:30 PM".
- On a phone the bar spans the width; the track sits on its own row under play and the steppers.

**Playback.**

- Speed: **one hour of map time per second** (a slot every 250 ms), as today. Plays to the end of the last day, then stops; Play at the end restarts from the first day at 00:00 (as today).
- The playhead **glides** continuously (`requestAnimationFrame`, position from elapsed time), not jump-per-slot.
- When the slot changes, only barangays (and lines, pills) whose state changed are restyled. Fill and stroke changes **fade over ~200 ms** (CSS transitions on the Leaflet SVG paths' `fill-opacity` / `stroke`; pills cross-fade).
- **Incremental updates are a core task, not a fallback** *(my call)*. Today `render()` rebuilds every street polyline (`drawStreets`), rebuilds every label and measures each one's `offsetWidth` (`drawLabels`), and calls `setStyle` on every path, once a second. At a slot every 250 ms that has to change: a slot change computes each feature's new state, and touches only paths, lines and pills whose state changed. Street lines are created once per day/week and restyled, not rebuilt. Label **placement** (which pills show, culling) is recomputed only on pan/zoom, day change, toggles and pause, not on every slot while playing; while playing, only the state of the pills already placed changes.
- Any manual input (step, drag, key, day change) pauses playback.
- **`prefers-reduced-motion: reduce`:** no glide, no fades; play still steps a slot every 250 ms.
- **Performance budget (a merge gate, since smoothness is the owner's ask):** in Chrome DevTools with the mid-tier mobile profile (4× CPU slowdown), playing a full real week with streets drawn shows **no long task over 50 ms** during play (checked with a `PerformanceObserver` for `longtask`, logged in a debug mode the builder adds or with a Performance trace).
- **If it still fails** after the incremental updates, cut in this order, re-measuring after each *(my call)*: (1) fades off on that device (a frame-time check during the first seconds of play); (2) street lines held at their state at the start of play and updated on pause. The glide and the 15-minute slots are never cut. If all that still fails, stop and ask the owner.

### 5.3 Barangay card

- **Desktop:** hover keeps today's behaviour (outline, popup after 500 ms). A **click pins the card** on the left instead of opening a click popup. Clicking another barangay replaces it; ✕, Esc, or clicking empty map closes it.
- **Phone:** a tap opens the card as a sheet above the timeline bar (no hover popups on touch, as today).
- **Content:** name and town; a **week strip** — one cell per day of the loaded window (up to 7; fewer when the window is shorter), each cell **outage** / **only cancelled** / **none**, the selected day outlined; under it each outage on the selected day: time range, status badge, purpose, streets and sitios (as in today's popup, with "more"), VECO post link; a short "no outages this day" line otherwise.
- Clicking a strip day selects that day and jumps to the start of its first outage (00:00 if none).
- The card follows the selection: changing day or time re-renders it; the outage active at the selected time is marked "now".
- A barangay with no outage in the whole window still opens a card (strip all "none").
- MECO areas: a click shows the existing "MECO area, not covered" text in the card.
- Street lines: clicking a drawn street keeps today's street popup.

### 5.4 Module split

New modules under `src/outages/map/static/js/` (names are the builder's):

| Module | Holds | Pure part tested with `node --test` |
|---|---|---|
| `timebar.js` | the bar: steppers, track, drag, keys, play loop | slot maths, x ↔ slot, the track's outage bands for a day, next-slot / end-of-week rule |
| `bcard.js` | the barangay card and its sheet mode | the week strip (cell states per day), the outage list for a day, "now" marking |
| `cards.js` | title card, notices stack, legend, "Not on the map" card | which notices show for a document state |
| `menu.js` | ⋮ menu and ⓘ popover: open/close, Esc, focus return, click-outside | — (DOM only) |
| `pills.js` or in `labels.js` | pill state for a barangay at a slot | the state rule of §4 |

`map.js` keeps loading, the Leaflet map, layers, styling and wiring. Existing modules (`timeline.js`, `labels.js`, `hover.js`, `streets.js`, `text.js`, `source.js`, `page.js`, `bins.js`, `tiles.js`) are changed only where the slot model or new markup needs it; their tests are updated, not dropped.

## 6. `/history`

Same tokens, cards, buttons and type; **layout stays a scrolling page** (it has a table and a chart, so full-bleed doesn't fit).

- Sticky dark top bar: "Cebu Brownout History", ← Map, the range form / range select. The snapshot banner and the "scheduled, not measured" note move into a notice card under it.
- The heatmap map in a card, its legend and the detail box as cards over or beside it (as today, restyled). The map tiles stay light; the 5 bins are kept unless one fails contrast against the dark legend card (§7), in which case only the legend swatch outline changes, never the bin colours on the map.
- "Most affected barangays" table in a card, with an inline bar per row (hours relative to the top row).
- "By month" SVG chart in a card; its colours and axis text re-checked on dark.
- `history.css` keeps only history-specific rules; everything shared comes from `base.css`.

## 7. Contrast (WCAG 2.1 AA)

- Text ≥ 4.5:1, large text and UI parts (pill edges, buttons, track, focus ring) ≥ 3:1.
- Translucent cards are checked **as composited over the worst backdrop**: white (`#ffffff`) and the brightest map colour under a card (the active orange fill at its opacity over white).
- A `node --test` (`contrast.test.mjs`) reads the tokens from `base.css` (and the pill colours) and asserts the listed pairs, so a later edit can't break them silently. The pairs are listed in the test.
- Checked by eye on a phone in daylight before the PR (owner, with a screenshot pair in the PR).

## 8. Snapshot

`python -m outages snapshot` copies the whole `static/` folder, so new files go along. The snapshot must work the same (`static/` paths, no `/history` route). The `site/` snapshot is refreshed and published only with the owner's OK (as in Phase 6).

## 9. Testing

- **JS (`node --test`):** the pure parts of §5.4; slot functions and the overlap rule (including an outage of 08:10–09:00, overnight outages, an outage ending exactly at a slot start); pill state; the week strip; the contrast test. Existing JS tests updated for slots.
- **Python:** `app.py` still serves `/`, `/history` and the new static files with the same headers; the snapshot includes the new files and passes its leak scan. No server data change is expected; if `build.py` needs a field, it gets a test.
- **Real browser, real DB** (lesson from Phase 4/streets): desktop and 375 px; play a full week; drag the track; open/close every card and menu by mouse, keyboard and touch emulation; history week mode; snapshot mode from `site/`; reduced motion; the performance budget of §5.2; console clean.
- **Owner uses it before the PR** (lesson from Streets v2): ten minutes on the real page, desktop and phone.

## 10. Definition of done

- Every item in §5–§7 works on the real DB, desktop and phone, in the served page and the snapshot.
- All tests green on CI (Ubuntu + Windows); ruff clean.
- README screenshots replaced; ROADMAP items ticked with notes.
- PR with before/after screenshots (desktop and phone, map and history).

## 11. Gates (ask the owner first)

- Publishing the refreshed snapshot to GitHub Pages.
- Any change to the map's own colours (shading, lines, history bins).

## 12. Out of scope

OSM tiles and the map's colours; the review page; alerts and the bot; server data and the GeoJSON format (unless §5.2 needs a field); a light theme or following the system's light/dark setting; web fonts; any framework or build step.

## 13. Risks and accepted limits

- **Playback cost.** Restyling ~250 paths with fades every 250 ms may stutter on cheap phones; mitigated by restyling only changed features, the budget in §5.2 and the fade fallback.
- **15-minute slots change a meaning users may know.** "In this hour" becomes "at this time"; the legend, popups and labels say so.
- **Dark cards over a light map** can feel heavy on a phone; the title card folds and the legend collapses there.
- **`backdrop-filter`** is missing on some browsers; the solid fallback carries the contrast.
- **Leaflet popups and controls** are styled by overriding Leaflet's CSS classes; a Leaflet update could need touch-ups (Leaflet is vendored and pinned, so not soon).

## 14. Left to the builder

Module and token names; exact sizes, radii and shadows within §4; how the play loop is structured; whether `activeInHour` survives; the debug switch for the performance check; the order of work (suggested: tokens + `base.css` → slot model and tests → time bar → layout and cards → barangay card → `/history` → contrast test → browser pass → snapshot).

## 15. Addendum (owner, 2026-11-15, after first use): selection, search, Now, lanes

Same branch and PR. Owner's requests are in **bold**; the rest are proposals stated to the owner (lanes picked by the owner; the other calls not objected to).

### 15.1 Timeline track: hover label and lanes (owner picked "lanes")
- **Hovering the track shows a label above the cursor** with the time under it ("10:30 PM"); over an outage segment it adds the barangays ("10:30 PM · Binaliw, Pit-os", more than 3 → "+n"). Touch: the label shows while dragging.
- **The track splits the orange band into lanes**, one segment per outage on that day: outages are packed into rows so overlapping ones sit in different rows (greedy, by start). At most **3 rows**; outages that don't fit add to a "+n" count at the right end of the track (hovering it lists them). Cancelled outages: a grey dashed segment, only when Show cancelled is on.
- Segments are per **outage**, not per barangay: one VECO outage often covers several barangays, which share one segment.
- **With a barangay selected**, its outages' segments stay bright and every other segment dims, so the selected barangay's times and its overlap with others are visible at once.

### 15.2 Selecting a barangay: zoom, dim, revert
- **Selecting** (click/tap on the map, or a search pick) pins the card as today **and flies the map to fit the barangay**, with padding for the floating UI (title and notices, left card or phone sheet, round buttons, the bar), so most of the barangay is visible and not under a card. Max zoom 16.
- **The rest of the map is dimmed slightly** (a dark mask with the selected barangay as a hole, ~35 %), above the shading and lines, below the labels.
- **Camera memory:** the view (centre, zoom) from before the **first** selection of a sequence is kept; selecting other barangays in a row does not replace it.
- **Deselect and revert to that view:** clicking the selected barangay again, **Esc**, the card's ✕, or clicking empty map.
- **Zooming out sufficiently deselects** (no revert: the user moved the camera): when the user zooms out to 2 levels below the barangay's fitted zoom. The map's own fly-to and revert animations never trigger it.
- MECO areas: selecting shows the existing "not covered" card without zoom or dim.

### 15.3 Search
- **A search box** in the top-left stack, under the title card (always visible, also on a phone with the title folded). Typing lists up to 8 barangays (every typed word must appear in name or town, case and accents ignored; names starting with the query first). ↑ ↓ move, Enter picks, Esc clears. Picking selects the barangay as in 15.2.
- **Each row shows the barangay's brownout status at the current time, never the timeline's time:**
  - **now** (an outage is on now): orange fill, "Now · 08:00–17:00";
  - **upcoming** (the next outage later in the current window): orange outline, "Today 22:00–06:00" or "Tue 17 Nov 08:00–12:00";
  - **cancelled** (the only outage now or upcoming is cancelled): grey outline, "Cancelled · …";
  - nothing listed: no status.
- "Current time" is `src.now()` (the snapshot's time in a snapshot). The status uses the **current window's** outages, fetched once (and with each refetch) when the page shows another week; when the page shows the current window, the loaded document is reused.

### 15.4 Now button
- **A "Now" button on the timeline bar.** The page starts **following** the current time (as today's default selection); while following, the selection moves to the current slot as time passes (checked every 15 s) and the button shows as active.
- **Any manipulation stops following:** a step, the day or time steppers, drag, keys, ▶, a strip day click, or a week change. Clicking Now resumes it: it jumps to the current slot, and from another week it returns to the current window first.
- In a snapshot "now" is fixed, so following holds still.

### 15.5 Testing additions
Pure, `node --test`: lane packing (overlap → different rows, cap 3, "+n"), the hover label text, search matching and order, search status at a time (now / upcoming / cancelled / none, overnight, cancelled-only), the zoom-out rule, follow on/off rules, the UI padding from card rectangles. Browser: select → zoom → dim → reselect another → Esc reverts to the first view; zoom out deselects; search pick on desktop and phone; Now follows across a slot boundary (by a faked clock in debug mode) and stops on a step.
