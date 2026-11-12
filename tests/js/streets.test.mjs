// Street lines and the week picker (Phase 5 spec 4.7). Run under several TZ values:
// the results must not depend on the machine's zone.
import assert from "node:assert/strict";
import { test } from "node:test";

import { drawnCount, isPastWindow, lineState, whyText } from "../../src/outages/map/static/js/streets.js";
import { historyLabel, shiftDate } from "../../src/outages/map/static/js/timeline.js";

const o = (status, start, end) => ({ status, start, end });
const day = "2026-11-12";

test("active in the hour, faint the rest of the day, hidden other days", () => {
  const x = o("scheduled", "2026-11-12T08:00:00+08:00", "2026-11-12T17:00:00+08:00");
  assert.equal(lineState(x, day, 8, true), "active");
  assert.equal(lineState(x, day, 16, true), "active");
  assert.equal(lineState(x, day, 17, true), "faint");
  assert.equal(lineState(x, day, 7, true), "faint");
  assert.equal(lineState(x, "2026-11-13", 8, true), "hidden");
  assert.equal(lineState(o("additional", x.start, x.end), day, 9, true), "active");
});

test("overnight outage: active at 23 on day 1 and 0 on day 2", () => {
  const x = o("scheduled", "2026-11-12T22:00:00+08:00", "2026-11-13T05:00:00+08:00");
  assert.equal(lineState(x, day, 23, true), "active");
  assert.equal(lineState(x, "2026-11-13", 0, true), "active");
  assert.equal(lineState(x, "2026-11-13", 5, true), "faint");
  assert.equal(lineState(x, day, 21, true), "faint");
});

test("cancelled: grey when shown, hidden when the toggle is off", () => {
  const x = o("cancelled", "2026-11-12T08:00:00+08:00", "2026-11-12T12:00:00+08:00");
  assert.equal(lineState(x, day, 9, true), "active_cancelled");
  assert.equal(lineState(x, day, 13, true), "faint");
  assert.equal(lineState(x, day, 9, false), "hidden");
  assert.equal(lineState(x, day, 13, false), "hidden");
});

test("drawnCount", () => {
  assert.deepEqual(drawnCount({ streets: { drawn: [{}, {}, {}], not_drawn: [{}, {}] } }), { drawn: 3, total: 5 });
  assert.deepEqual(drawnCount({}), { drawn: 0, total: 0 });
});

test("whyText for each reason", () => {
  assert.equal(whyText("not_found"), "not found on OpenStreetMap near these barangays");
  assert.equal(whyText("no_osm_data"), "street data not downloaded");
  assert.equal(whyText("not_processed"), "street matching not run yet");
  assert.equal(whyText("drawing_off"), "street lines are off: matching isn't precise enough yet");
  assert.equal(whyText("anything else"), "not drawn");
});

test("isPastWindow at 00:30 Manila (the previous evening in New York)", () => {
  const now = Date.parse("2026-11-12T00:30:00+08:00");
  assert.equal(isPastWindow({ days: ["2026-11-12", "2026-11-13"] }, now), false);
  assert.equal(isPastWindow({ days: ["2026-11-11"] }, now), true);
  assert.equal(isPastWindow({ days: ["2025-11-03", "2025-11-09"] }, now), true);
});

test("shiftDate moves whole Manila days, across months and years", () => {
  assert.equal(shiftDate("2026-11-12", -7), "2026-11-05");
  assert.equal(shiftDate("2026-11-28", 7), "2026-12-05");
  assert.equal(shiftDate("2026-01-02", -7), "2025-12-26");
  assert.equal(shiftDate("2024-02-26", 7), "2024-03-04");
});

test("historyLabel", () => {
  assert.equal(historyLabel(["2025-11-03", "2025-11-09"]), "History: 3–9 Nov 2025");
  assert.equal(historyLabel(["2025-10-29", "2025-11-04"]), "History: 29 Oct – 4 Nov 2025");
  assert.equal(historyLabel(["2025-12-29", "2026-01-04"]), "History: 29 Dec 2025 – 4 Jan 2026");
  assert.equal(historyLabel(["2025-11-03"]), "History: 3 Nov 2025");
});

test("linesInGeometry: any node inside any polygon part", async () => {
  const { linesInGeometry } = await import("../../src/outages/map/static/js/streets.js");
  const sq = [[[123.81, 10.3], [123.82, 10.3], [123.82, 10.31], [123.81, 10.31], [123.81, 10.3]]];
  const poly = { type: "Polygon", coordinates: sq };
  const multi = { type: "MultiPolygon", coordinates: [[[[0, 0], [1, 0], [1, 1], [0, 0]]], sq] };
  const inside = [[[123.805, 10.305], [123.815, 10.305]]];
  const outside = [[[123.80, 10.305], [123.805, 10.305]]];
  assert.equal(linesInGeometry(inside, poly), true);
  assert.equal(linesInGeometry(outside, poly), false);
  assert.equal(linesInGeometry(inside, multi), true);
  assert.equal(linesInGeometry([], poly), false);
  assert.equal(linesInGeometry(inside, null), false);
});
