// Hover-to-open popups: dwell before opening, grace before closing, clicks pin.
import assert from "node:assert/strict";
import { test } from "node:test";

import { createDwell } from "../../src/outages/map/static/js/hover.js";

/** A fake clock: setTimeout/clearTimeout driven by tick(ms). */
function fakeTimers() {
  let now = 0;
  let next = 1;
  const due = new Map();
  return {
    setTimeout(fn, ms) {
      const id = next++;
      due.set(id, [now + ms, fn]);
      return id;
    },
    clearTimeout(id) {
      due.delete(id);
    },
    tick(ms) {
      const end = now + ms;
      for (;;) {
        const ready = [...due].filter(([, [t]]) => t <= end).sort((a, b) => a[1][0] - b[1][0]);
        if (!ready.length) break;
        const [id, [t, fn]] = ready[0];
        due.delete(id);
        now = t;
        fn();
      }
      now = end;
    },
  };
}

function setup() {
  const timers = fakeTimers();
  const log = [];
  const dwell = createDwell({
    delay: 500,
    grace: 300,
    timers,
    open: (l) => log.push(`open ${l}`),
    close: (l) => {
      log.push(`close ${l}`);
      dwell.closed(l); // as Leaflet's popupclose would
    },
  });
  return { timers, log, dwell };
}

test("a popup opens after the cursor rests for the dwell time", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(499);
  assert.deepEqual(log, []);
  timers.tick(1);
  assert.deepEqual(log, ["open A"]);
});

test("moving inside the barangay restarts the dwell: it opens once the cursor rests", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(400);
  dwell.enter("A"); // mousemove
  timers.tick(400);
  assert.deepEqual(log, []);
  timers.tick(100);
  assert.deepEqual(log, ["open A"]);
});

test("passing over a barangay quickly opens nothing", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(200);
  dwell.leave("A");
  dwell.enter("B");
  timers.tick(200);
  dwell.leave("B");
  timers.tick(2000);
  assert.deepEqual(log, []);
});

test("leaving closes a hover-opened popup after the grace period", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(500);
  dwell.leave("A");
  timers.tick(299);
  assert.deepEqual(log, ["open A"]);
  timers.tick(1);
  assert.deepEqual(log, ["open A", "close A"]);
});

test("moving into the popup keeps it open; leaving the popup closes it", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(500);
  dwell.leave("A");
  timers.tick(100);
  dwell.enterPopup();
  timers.tick(5000);
  assert.deepEqual(log, ["open A"]);
  dwell.leavePopup();
  timers.tick(300);
  assert.deepEqual(log, ["open A", "close A"]);
});

test("coming back onto the barangay from its popup keeps it open", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(500);
  dwell.leave("A");
  dwell.enterPopup();
  dwell.leavePopup();
  dwell.enter("A");
  timers.tick(5000);
  assert.deepEqual(log, ["open A"]);
});

test("a clicked popup is pinned: leaving doesn't close it, hovering others doesn't replace it", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(500);
  dwell.pin("A");
  dwell.leave("A");
  dwell.enter("B");
  timers.tick(5000);
  assert.deepEqual(log, ["open A"]);
  dwell.closed("A"); // closed with its × or a map click
  dwell.enter("C");
  timers.tick(500);
  assert.deepEqual(log, ["open A", "open C"]);
});

test("a click during the dwell cancels the pending hover open", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(200);
  dwell.pin("A"); // Leaflet opened it on the click
  timers.tick(5000);
  dwell.leave("A");
  timers.tick(5000);
  assert.deepEqual(log, []);
});

test("hovering from one barangay to the next swaps the popup", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  timers.tick(500);
  dwell.leave("A");
  dwell.enter("B");
  timers.tick(500);
  assert.deepEqual(log, ["open A", "close A", "open B"]);
});

test("reset drops pending timers (the layers were redrawn)", () => {
  const { timers, log, dwell } = setup();
  dwell.enter("A");
  dwell.reset();
  timers.tick(5000);
  assert.deepEqual(log, []);
});
