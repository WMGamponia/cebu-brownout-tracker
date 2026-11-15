// The timeline bar's pure parts (UI refresh spec 5.2): track bands, x <-> slot, keys.
import assert from "node:assert/strict";
import { test } from "node:test";

import { bands, keyStep, keysApply, nearestSlotAtX, slotAtX } from "../../src/outages/map/static/js/timebar.js";

const o = (start, end, status = "scheduled") => ({ start, end, status });
const D = "2026-11-12";
const on = (b) => b.flatMap((v, i) => (v ? [i] : []));

test("bands mark the slots any shown outage is active in", () => {
  const b = bands({ 1: o(`${D}T08:30:00+08:00`, `${D}T10:00:00+08:00`) }, D, true);
  assert.equal(b.length, 96);
  assert.deepEqual(on(b), [34, 35, 36, 37, 38, 39]);
});

test("bands follow Show cancelled", () => {
  const outages = { 1: o(`${D}T08:00:00+08:00`, `${D}T08:30:00+08:00`, "cancelled") };
  assert.deepEqual(on(bands(outages, D, true)), [32, 33]);
  assert.deepEqual(on(bands(outages, D, false)), []);
});

test("bands of an overnight outage on its second day", () => {
  const night = { 1: o(`${D}T22:00:00+08:00`, "2026-11-13T05:00:00+08:00") };
  assert.deepEqual(on(bands(night, "2026-11-13", true)), [...Array(20).keys()]);
});

test("slotAtX floors and clamps; nearestSlotAtX rounds and clamps", () => {
  assert.equal(slotAtX(0, 960), 0);
  assert.equal(slotAtX(959, 960), 95);
  assert.equal(slotAtX(-5, 960), 0);
  assert.equal(slotAtX(2000, 960), 95);
  assert.equal(slotAtX(19, 960), 1);
  assert.equal(nearestSlotAtX(14, 960), 1);
  assert.equal(nearestSlotAtX(965, 960), 95);
  assert.equal(nearestSlotAtX(-3, 960), 0);
});

test("keyStep: arrows step a slot, Shift an hour, Space toggles", () => {
  assert.equal(keyStep("ArrowRight", false), 1);
  assert.equal(keyStep("ArrowLeft", false), -1);
  assert.equal(keyStep("ArrowRight", true), 4);
  assert.equal(keyStep("ArrowLeft", true), -4);
  assert.equal(keyStep(" ", false), "toggle");
  assert.equal(keyStep("a", false), null);
});

test("keysApply: everything on the page body or the track", () => {
  for (const step of [1, -4, "toggle"]) {
    assert.equal(keysApply("body", step), true);
    assert.equal(keysApply("track", step), true);
  }
});

test("keysApply: arrows on a timeline-bar button, but Space presses the button itself", () => {
  assert.equal(keysApply("bar", 1), true);
  assert.equal(keysApply("bar", -4), true);
  assert.equal(keysApply("bar", "toggle"), false);
});

test("keysApply: never on the map, other controls, text fields or menus", () => {
  for (const k of ["map", "control", "text", "popover"]) {
    assert.equal(keysApply(k, 1), false, k);
    assert.equal(keysApply(k, "toggle"), false, k);
  }
});
