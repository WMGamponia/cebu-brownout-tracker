// 12-hour (AM/PM) and 24-hour clock text on the map page. Run under several TZ values:
// the results must not depend on the machine's zone.
import assert from "node:assert/strict";
import { test } from "node:test";

import { asOfLabel, hourLabel, rangeLabel, shortRange } from "../../src/outages/map/static/js/timeline.js";

test("hourLabel in 12-hour time", () => {
  assert.equal(hourLabel(0, "12"), "12:00 AM–1:00 AM");
  assert.equal(hourLabel(11, "12"), "11:00 AM–12:00 PM");
  assert.equal(hourLabel(12, "12"), "12:00 PM–1:00 PM");
  assert.equal(hourLabel(22, "12"), "10:00 PM–11:00 PM");
  assert.equal(hourLabel(23, "12"), "11:00 PM–12:00 AM");
  assert.equal(hourLabel(23, "24"), "23:00–24:00");
  assert.equal(hourLabel(23), "23:00–24:00");
});

test("rangeLabel in 12-hour time", () => {
  assert.equal(
    rangeLabel("2026-11-12T08:00:00+08:00", "2026-11-12T17:00:00+08:00", "12"),
    "Thu 12 Nov 8:00 AM–5:00 PM",
  );
  assert.equal(
    rangeLabel("2026-11-12T22:00:00+08:00", "2026-11-13T05:00:00+08:00", "12"),
    "Thu 12 Nov 10:00 PM – Fri 13 Nov 5:00 AM",
  );
  assert.equal(
    rangeLabel("2026-11-12T20:00:00+08:00", "2026-11-13T00:00:00+08:00", "12"),
    "Thu 12 Nov 8:00 PM–12:00 AM",
  );
  assert.equal(
    rangeLabel("2026-11-12T00:30:00+08:00", "2026-11-12T12:45:00+08:00", "12"),
    "Thu 12 Nov 12:30 AM–12:45 PM",
  );
});

test("asOfLabel in 12-hour time", () => {
  assert.equal(asOfLabel("2026-11-12T20:00:00+08:00", "12"), "Thu 12 Nov, 8:00 PM");
  assert.equal(asOfLabel("2026-11-12T16:05:00+00:00", "12"), "Fri 13 Nov, 12:05 AM");
  assert.equal(asOfLabel(null, "12"), null);
});

test("shortRange: times only, compact in 12-hour time", () => {
  assert.equal(shortRange("2026-11-12T22:00:00+08:00", "2026-11-13T06:00:00+08:00"), "22:00–06:00");
  assert.equal(shortRange("2026-11-12T22:00:00+08:00", "2026-11-13T06:00:00+08:00", "12"), "10 PM–6 AM");
  assert.equal(shortRange("2026-11-12T08:30:00+08:00", "2026-11-12T16:45:00+08:00", "12"), "8:30 AM–4:45 PM");
  assert.equal(shortRange("2026-11-12T20:00:00+08:00", "2026-11-13T00:00:00+08:00"), "20:00–24:00");
  assert.equal(shortRange("2026-11-12T20:00:00+08:00", "2026-11-13T00:00:00+08:00", "12"), "8 PM–12 AM");
});
