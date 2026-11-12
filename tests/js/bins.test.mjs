// History page helpers (Phase 5 spec 4.8). Pure; run under several TZ values.
import assert from "node:assert/strict";
import { test } from "node:test";

import { binOf, legendLabels, monthBars, quantileEdges } from "../../src/outages/map/static/js/bins.js";
import { mondayOf } from "../../src/outages/map/static/js/timeline.js";

const range = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);

test("quantile edges over 1..100", () => {
  assert.deepEqual(quantileEdges(range(1, 100)), [20, 40, 60, 80, 100]);
  assert.deepEqual(quantileEdges(range(1, 100), 4), [25, 50, 75, 100]);
});

test("zeros are ignored; all zero gives no edges", () => {
  assert.deepEqual(quantileEdges([0, 0, 0]), []);
  assert.deepEqual(quantileEdges([]), []);
  assert.deepEqual(quantileEdges([0, 0, ...range(1, 10)]), [2, 4, 6, 8, 10]);
});

test("ties collapse into fewer bins", () => {
  assert.deepEqual(quantileEdges([7, 7, 7, 7]), [7]);
  assert.deepEqual(quantileEdges([1, 1, 1, 1, 9]), [1, 9]);
});

test("binOf: 0 for zero hours, the top bin above the top edge", () => {
  const e = [20, 40, 60, 80, 100];
  assert.equal(binOf(0, e), 0);
  assert.equal(binOf(-1, e), 0);
  assert.equal(binOf(0.5, e), 1);
  assert.equal(binOf(20, e), 1);
  assert.equal(binOf(20.1, e), 2);
  assert.equal(binOf(100, e), 5);
  assert.equal(binOf(250, e), 5);
  assert.equal(binOf(3, []), 0);
});

test("legend labels with real hour values", () => {
  assert.deepEqual(legendLabels([20, 40, 60, 80, 100]), ["up to 20 h", "20–40 h", "40–60 h", "60–80 h", "80–100 h"]);
  assert.deepEqual(legendLabels([7]), ["up to 7 h"]);
  assert.deepEqual(legendLabels([1.25, 8.5]), ["up to 1.3 h", "1.3–8.5 h"]);
  assert.deepEqual(legendLabels([]), []);
});

test("month bars fill the width; heights scale to the largest month", () => {
  const months = [
    { month: "2026-01", hours: 10 },
    { month: "2026-02", hours: 0 },
    { month: "2026-03", hours: 40 },
    { month: "2026-04", hours: 20 },
  ];
  const bars = monthBars(months, 400, 100);
  assert.equal(bars.length, 4);
  const slot = 100;
  bars.forEach((b, i) => {
    assert.equal(b.label, months[i].month);
    assert.ok(Math.abs(b.x - (i * slot + slot * 0.1)) < 1e-9);
    assert.ok(Math.abs(b.w - slot * 0.8) < 1e-9);
    assert.ok(Math.abs(b.y + b.h - 100) < 1e-9);
  });
  assert.equal(bars[2].h, 100);
  assert.equal(bars[0].h, 25);
  assert.equal(bars[1].h, 0);
  assert.equal(bars.length * slot, 400);
  assert.deepEqual(monthBars([{ month: "2026-01", hours: 0 }], 100, 50)[0].h, 0);
  assert.deepEqual(monthBars([], 100, 50), []);
});

test("mondayOf: the Monday on or before a Manila date", () => {
  assert.equal(mondayOf("2026-11-12"), "2026-11-09"); // Thursday
  assert.equal(mondayOf("2026-11-09"), "2026-11-09"); // Monday
  assert.equal(mondayOf("2026-11-15"), "2026-11-09"); // Sunday
  assert.equal(mondayOf("2026-03-01"), "2026-02-23");
});
