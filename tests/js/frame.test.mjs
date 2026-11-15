// Playback position and change tracking (UI refresh spec 5.2). Pure maths, no DOM.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MS_PER_SLOT, changedKeys, dropFades, playhead } from "../../src/outages/map/static/js/frame.js";

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
