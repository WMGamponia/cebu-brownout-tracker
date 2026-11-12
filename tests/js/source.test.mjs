// The pages' data source (Phase 6 spec 4.3): live server URLs or a static snapshot folder.
// Run under several TZ values: nothing may depend on the machine's zone.
import assert from "node:assert/strict";
import { test } from "node:test";

import { makeSource, snapshotLabel } from "../../src/outages/map/static/js/source.js";
import { shiftDate } from "../../src/outages/map/static/js/timeline.js";

function weeksFrom(first, last) {
  const out = [];
  for (let w = first; w <= last; w = shiftDate(w, 7)) out.push(w);
  return out;
}

const SNAP = {
  format: 1,
  taken_at: "2026-11-12T20:05:11+08:00",
  last_check_at: "2026-11-12T20:00:42+08:00",
  snapshot_week: "2026-11-12",
  weeks: [...weeksFrom("2025-11-13", "2026-11-05"), "2026-11-12"],
  ranges: [
    { id: "last-12-months", label: "Last 12 months", from: "2025-11-13", to: "2026-11-12" },
    { id: "2025", label: "2025", from: "2025-01-01", to: "2025-12-31" },
  ],
  default_range: "last-12-months",
  veco_url: "https://www.visayanelectric.com/",
  code_url: "https://github.com/WMGamponia/cebu-brownout-tracker",
};
const q = (s) => new URLSearchParams(s);

test("the fixture has 53 weeks", () => {
  assert.equal(SNAP.weeks.length, 53);
});

test("live: today's server URLs", () => {
  const src = makeSource("live", null);
  assert.equal(src.isSnapshot, false);
  assert.equal(src.refetch, true);
  assert.deepEqual(src.ranges, []);
  assert.equal(src.mapUrl(q("from=2026-10-01&days=7")), "outages.geojson?from=2026-10-01&days=7");
  assert.equal(src.mapUrl(q("")), "outages.geojson");
  assert.equal(src.mapUrl(q("x=1")), "outages.geojson");
  assert.equal(src.statsUrl(q("from=2026-01-01&to=2026-06-30")), "stats.json?from=2026-01-01&to=2026-06-30");
  assert.equal(src.statsUrl(q("")), "stats.json");
  assert.equal(src.weekStep("2026-11-12", -1), "2026-11-05");
  assert.equal(src.weekStep("2026-11-12", 1), "2026-11-19");
  assert.equal(src.snapWeek("2026-11-08"), "2026-11-08");
});

test("live: now() is the clock", () => {
  const src = makeSource("live", null);
  const before = Date.now();
  const n = src.now();
  assert.ok(n >= before && n <= Date.now());
});

test("snapshot: now() is taken_at", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.isSnapshot, true);
  assert.equal(src.refetch, false);
  assert.equal(src.now(), Date.parse("2026-11-12T12:05:11Z"));
  assert.equal(src.ranges, SNAP.ranges);
  assert.equal(src.weeks, SNAP.weeks);
});

test("snapshot: week files", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.mapUrl(q("")), "weeks/2026-11-12.geojson");
  assert.equal(src.mapUrl(q("from=2026-11-05&days=7")), "weeks/2026-11-05.geojson");
  assert.equal(src.mapUrl(q("from=not-a-date")), "weeks/2026-11-12.geojson");
});

test("snapshot: snapWeek finds the stored week containing a date", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.snapWeek("2026-11-08"), "2026-11-05");
  assert.equal(src.snapWeek("2026-11-05"), "2026-11-05");
  assert.equal(src.snapWeek("2026-11-11"), "2026-11-05");
  assert.equal(src.snapWeek("2026-11-14"), "2026-11-12");
  assert.equal(src.snapWeek("2030-01-01"), "2026-11-12");
  assert.equal(src.snapWeek("2020-01-01"), "2025-11-13");
  assert.equal(src.snapWeek(null), "2026-11-12");
  assert.equal(src.snapWeek(""), "2026-11-12");
});

test("snapshot: a short snapshot week still snaps to itself", () => {
  // VECO's last stored end is two days away: the snapshot week is 12–13 Nov.
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.snapWeek("2026-11-13"), "2026-11-12");
  assert.equal(src.weekStep("2026-11-12", -1), "2026-11-05");
  assert.equal(src.weekStep("2026-11-05", 1), "2026-11-12");
});

test("snapshot: week stepping is bounded by the stored weeks", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.weekStep("2025-11-13", -1), null);
  assert.equal(src.weekStep("2025-11-13", 1), "2025-11-20");
  assert.equal(src.weekStep("2026-11-05", 1), "2026-11-12");
  assert.equal(src.weekStep("2026-11-12", 1), null);
  assert.equal(src.weekStep("2026-11-08", -1), "2026-10-29"); // from the week holding the date
});

test("snapshot: stats files by range id", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.statsUrl(q("range=2025")), "stats/2025.json");
  assert.equal(src.statsUrl(q("range=1999")), "stats/last-12-months.json");
  assert.equal(src.statsUrl(q("")), "stats/last-12-months.json");
  assert.equal(src.rangeId(q("range=2025")), "2025");
  assert.equal(src.rangeId(q("range=../x")), "last-12-months");
});

test("test_snapshot_ignores_live_params", () => {
  const src = makeSource("snapshot", SNAP);
  assert.equal(src.mapUrl(q("from=2024-02-01&days=7")), "weeks/2025-11-13.geojson");
  assert.equal(src.statsUrl(q("from=2024-01-01&to=2024-12-31")), "stats/last-12-months.json");
});

test("snapshotLabel: Manila wall time whatever the zone", () => {
  assert.equal(snapshotLabel("2026-11-12T20:05:11+08:00"), "Thu 12 Nov 2026, 20:05");
  assert.equal(snapshotLabel("2026-11-12T16:05:11Z"), "Fri 13 Nov 2026, 00:05");
});
