// The barangay card's pure parts (UI refresh spec 5.3): the week strip and the day's outages.
import assert from "node:assert/strict";
import { test } from "node:test";

import { dayOutages, weekStrip } from "../../src/outages/map/static/js/bcard.js";

const o = (start, end, status = "scheduled") => ({ start, end, status });
const DAYS3 = ["2026-11-12", "2026-11-13", "2026-11-14"];
const OUTAGES = {
  1: o("2026-11-12T08:00:00+08:00", "2026-11-12T12:00:00+08:00"),
  2: o("2026-11-14T13:00:00+08:00", "2026-11-14T15:00:00+08:00", "cancelled"),
  3: o("2026-11-12T14:00:00+08:00", "2026-11-12T16:00:00+08:00", "additional"),
};
const states = (strip) => strip.map((c) => c.state);

test("weekStrip: outage, none, only cancelled", () => {
  const strip = weekStrip([1, 2], OUTAGES, DAYS3, true);
  assert.deepEqual(strip.map((c) => c.day), DAYS3);
  assert.deepEqual(states(strip), ["outage", "none", "cancelled"]);
});

test("weekStrip follows Show cancelled", () => {
  assert.deepEqual(states(weekStrip([1, 2], OUTAGES, DAYS3, false)), ["outage", "none", "none"]);
});

test("weekStrip with no outages is all none, one cell per day", () => {
  const days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15"];
  assert.deepEqual(states(weekStrip([], OUTAGES, days, true)), Array(7).fill("none"));
});

test("weekStrip: an overnight outage marks both days", () => {
  const night = { 9: o("2026-11-12T22:00:00+08:00", "2026-11-13T05:00:00+08:00") };
  assert.deepEqual(states(weekStrip([9], night, DAYS3, true)), ["outage", "outage", "none"]);
});

test("dayOutages lists the day's outages by start and marks the one at the slot", () => {
  const list = dayOutages([3, 1, 2], OUTAGES, "2026-11-12", 36, true); // 09:00
  assert.deepEqual(list.map((x) => [x.id, x.now]), [["1", true], ["3", false]]);
  assert.deepEqual(dayOutages([2], OUTAGES, "2026-11-14", 52, false), []);
  assert.deepEqual(dayOutages([2], OUTAGES, "2026-11-14", 52, true).map((x) => x.now), [true]);
});
