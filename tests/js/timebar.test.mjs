// The timeline bar's pure parts (UI refresh spec 5.2): track bands, x <-> slot, keys.
import assert from "node:assert/strict";
import { test } from "node:test";

import { hoverText, keyStep, keysApply, markSpans, nearestSlotAtX, slotAtX, trackSpans } from "../../src/outages/map/static/js/timebar.js";

const o = (start, end, status = "scheduled") => ({ start, end, status });
const D = "2026-11-12";
const on = (b) => b.flatMap((v, i) => (v ? [i] : []));

const at = (h, m = "00") => `${D}T${h}:${m}:00+08:00`;
const bands = (r) => r.bands.map((x) => [x.a, x.b, x.cancelled]);

test("trackSpans merges the day's outages into one bar", () => {
  const outages = {
    1: o(at("08"), at("12")),
    2: o(at("10"), at("14")),
    3: o(at("15"), at("16")),
  };
  const r = trackSpans(outages, D, true);
  assert.deepEqual(bands(r), [[32, 55, false], [60, 63, false]]);
  assert.deepEqual(
    r.items.map((x) => [x.id, x.a, x.b, x.cancelled]),
    [["1", 32, 47, false], ["2", 40, 55, false], ["3", 60, 63, false]],
  );
});

test("trackSpans: cancelled time is grey only where no real outage is on", () => {
  const outages = {
    1: o(at("08"), at("12")),
    2: o(at("07"), at("17"), "cancelled"),
    3: o(at("20"), at("22"), "cancelled"),
  };
  assert.deepEqual(bands(trackSpans(outages, D, true)), [
    [28, 31, true],
    [32, 47, false],
    [48, 67, true],
    [80, 87, true],
  ]);
  assert.deepEqual(bands(trackSpans(outages, D, false)), [[32, 47, false]]);
  assert.equal(trackSpans(outages, D, false).items.length, 1);
});

test("trackSpans: an overnight outage starts at slot 0 on its second day", () => {
  const night = { 7: o(at("22"), "2026-11-13T05:00:00+08:00") };
  assert.deepEqual(bands(trackSpans(night, "2026-11-13", true)), [[0, 19, false]]);
});

test("markSpans outlines only the selected barangay's outages, merged", () => {
  const items = [
    { id: "1", a: 32, b: 47 },
    { id: "2", a: 40, b: 55 },
    { id: "3", a: 60, b: 63 },
    { id: "4", a: 70, b: 75 },
  ];
  assert.deepEqual(markSpans(items, new Set(["1", "2", "4"])), [{ a: 32, b: 55 }, { a: 70, b: 75 }]);
  assert.deepEqual(markSpans(items, new Set()), []);
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

test("the hover label names every outage under the cursor", () => {
  const outages = {
    1: o(at("08"), at("12")),
    2: o(at("09"), at("12")),
    3: o(at("10"), at("12")),
    4: o(at("11"), at("12")),
  };
  const { items } = trackSpans(outages, D, true);
  const names = { 1: ["A"], 2: ["B"], 3: ["C"], 4: ["D"] };
  assert.equal(hoverText(45, items, names, "24"), "11:15 · A, B, C +1");
});
