// The timeline bar's pure parts (UI refresh spec 5.2): track bands, x <-> slot, keys.
import assert from "node:assert/strict";
import { test } from "node:test";

import { hoverText, keyStep, keysApply, nearestSlotAtX, packLanes, slotAtX } from "../../src/outages/map/static/js/timebar.js";

const o = (start, end, status = "scheduled") => ({ start, end, status });
const D = "2026-11-12";
const on = (b) => b.flatMap((v, i) => (v ? [i] : []));

const at = (h, m = "00") => `${D}T${h}:${m}:00+08:00`;
const rows = (r) => Object.fromEntries(r.segments.map((x) => [x.id, x.row]));

test("packLanes puts overlapping outages in separate rows and reuses free rows", () => {
  const outages = {
    1: o(at("08"), at("12")),
    2: o(at("10"), at("14")),
    3: o(at("11"), at("13")),
    4: o(at("11", "30"), at("12", "30")),
    5: o(at("15"), at("16")),
  };
  const r = packLanes(outages, D, true);
  assert.deepEqual(rows(r), { 1: 0, 2: 1, 3: 2, 5: 0 });
  assert.deepEqual(r.overflow, ["4"]);
  const s1 = r.segments.find((x) => x.id === "1");
  assert.deepEqual([s1.a, s1.b, s1.cancelled], [32, 47, false]);
});

test("packLanes: an overnight outage starts at slot 0 on its second day", () => {
  const night = { 7: o(at("22"), "2026-11-13T05:00:00+08:00") };
  const [seg] = packLanes(night, "2026-11-13", true).segments;
  assert.deepEqual([seg.a, seg.b], [0, 19]);
});

test("packLanes follows Show cancelled", () => {
  const outages = { 1: o(at("08"), at("09"), "cancelled") };
  assert.equal(packLanes(outages, D, false).segments.length, 0);
  assert.equal(packLanes(outages, D, true).segments[0].cancelled, true);
});

test("hoverText: the time, then the barangays under it", () => {
  const segs = [{ id: "1", a: 40, b: 50 }, { id: "2", a: 60, b: 70 }];
  const names = { 1: ["Binaliw", "Pit-os"], 2: ["A", "B", "C", "D"] };
  assert.equal(hoverText(42, segs, names, "12"), "10:30 AM · Binaliw, Pit-os");
  assert.equal(hoverText(62, segs, names, "24"), "15:30 · A, B, C +1");
  assert.equal(hoverText(55, segs, names, "24"), "13:45");
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
