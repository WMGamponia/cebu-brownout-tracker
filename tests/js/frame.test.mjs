// Playback position and change tracking (UI refresh spec 5.2). Pure maths, no DOM.
import assert from "node:assert/strict";
import { test } from "node:test";

import { parseIso } from "../../src/outages/map/static/js/timeline.js";

import { MS_PER_SLOT, changedKeys, dropFades, followSlot, motionOn, playhead } from "../../src/outages/map/static/js/frame.js";

const D1 = "2026-11-12";
const D2 = "2026-11-13";
const days2 = [D1, D2];

test("a slot every 250 ms", () => {
  assert.equal(MS_PER_SLOT, 250);
});

test("playhead glides across slots and midnight", () => {
  assert.deepEqual(playhead(days2, D1, 94, 0), { day: D1, slot: 94, frac: 0 });
  assert.deepEqual(playhead(days2, D1, 94, 625), { day: D2, slot: 0, frac: 0.5 });
});

test("playhead is null past the last slot of the last day", () => {
  assert.equal(playhead(days2, D2, 95, 250), null);
  assert.deepEqual(playhead(days2, D2, 95, 249), { day: D2, slot: 95, frac: 0.996 });
});

test("changedKeys lists changed, added and removed keys", () => {
  const prev = new Map([["a", "x"], ["b", "y"], ["gone", "z"]]);
  const next = new Map([["a", "x"], ["b", "z"], ["c", "w"]]);
  assert.deepEqual(changedKeys(prev, next).sort(), ["b", "c", "gone"]);
  assert.deepEqual(changedKeys(next, next), []);
});

test("dropFades: more than 2 slow frames (> 50 ms)", () => {
  assert.equal(dropFades([16, 16, 60, 70, 16]), false);
  assert.equal(dropFades([60, 70, 80]), true);
  assert.equal(dropFades([50, 50, 50]), false);
});

test("followSlot: the current Manila slot while it is in the window", () => {
  const t = (iso) => parseIso(iso);
  assert.deepEqual(followSlot(t("2026-11-12T10:37:00+08:00"), days2), { day: D1, slot: 42 });
  assert.deepEqual(followSlot(t("2026-11-12T23:59:59+08:00"), days2), { day: D1, slot: 95 });
  assert.deepEqual(followSlot(t("2026-11-13T00:00:00+08:00"), days2), { day: D2, slot: 0 });
  assert.equal(followSlot(t("2026-11-14T00:00:00+08:00"), days2), null);
});

test("motionOn: the saved choice wins; otherwise the system's reduced-motion setting", () => {
  assert.equal(motionOn("on", true), true); // switched on in the menu despite the system
  assert.equal(motionOn("off", false), false);
  assert.equal(motionOn(null, true), false);
  assert.equal(motionOn(null, false), true);
  assert.equal(motionOn("junk", false), true);
});
