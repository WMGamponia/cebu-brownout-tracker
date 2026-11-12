// Always-on short labels: text, anchor point and collision culling (nearest the centre wins).
import assert from "node:assert/strict";
import { test } from "node:test";

import { cullLabels, labelPoint, shortLabel } from "../../src/outages/map/static/js/labels.js";

const out = (start, end, status = "scheduled") => ({ start, end, status });
const NIGHT = out("2026-11-12T22:00:00+08:00", "2026-11-13T06:00:00+08:00");
const DAY = out("2026-11-12T08:00:00+08:00", "2026-11-12T17:00:00+08:00");

test("shortLabel: one outage shows its times, several show a count", () => {
  assert.equal(shortLabel("Mambaling", [["1", NIGHT]]), "Mambaling · 22:00–06:00");
  assert.equal(shortLabel("Mambaling", [["1", NIGHT]], "12"), "Mambaling · 10 PM–6 AM");
  assert.equal(shortLabel("Apas", [["1", DAY], ["2", NIGHT]]), "Apas · 2 outages");
  assert.equal(shortLabel("Apas", [["1", { ...DAY, status: "cancelled" }]]), "Apas · 08:00–17:00 (cancelled)");
  assert.equal(shortLabel("Apas", []), null);
});

const box = (key, x, y, w = 100, h = 20) => ({ key, x, y, w, h });
const SIZE = { w: 800, h: 600 };

test("cullLabels keeps the label nearest the centre when two overlap", () => {
  const near = box("near", 400, 300);
  const far = box("far", 450, 305);
  assert.deepEqual(cullLabels([far, near], SIZE), ["near"]);
});

test("cullLabels keeps labels that don't overlap, nearest first", () => {
  const a = box("a", 400, 300);
  const b = box("b", 400, 400);
  const c = box("c", 100, 100);
  assert.deepEqual(cullLabels([c, b, a], SIZE), ["a", "b", "c"]);
});

test("cullLabels drops labels whose anchor is off screen", () => {
  assert.deepEqual(cullLabels([box("off", -10, 300), box("on", 10, 300, 10)], SIZE), ["on"]);
  assert.deepEqual(cullLabels([box("below", 400, 601)], SIZE), []);
});

test("cullLabels keeps a small gap between labels", () => {
  // Boxes 100 wide centred 102 apart: 2 px apart, inside the default 4 px gap.
  assert.deepEqual(cullLabels([box("a", 400, 300), box("b", 502, 300)], SIZE), ["a"]);
  assert.deepEqual(cullLabels([box("a", 400, 300), box("b", 510, 300)], SIZE), ["a", "b"]);
});

test("cullLabels breaks distance ties by key, so the result is stable", () => {
  assert.deepEqual(cullLabels([box("b", 460, 300), box("a", 340, 300)], SIZE), ["a", "b"]);
  assert.deepEqual(cullLabels([box("b", 410, 300), box("a", 390, 300)], SIZE), ["a"]);
});

const square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]];
// A "C" shape: the bounding-box centre (5, 5) is in the notch, outside the polygon.
const cShape = [[0, 0], [10, 0], [10, 2], [2, 2], [2, 8], [10, 8], [10, 10], [0, 10], [0, 0]];

test("labelPoint is the bounding-box centre when that lies inside", () => {
  assert.deepEqual(labelPoint({ type: "Polygon", coordinates: [square] }), [5, 5]);
});

test("labelPoint moves inside a concave polygon", () => {
  const [x, y] = labelPoint({ type: "Polygon", coordinates: [cShape] });
  assert.equal(y, 5);
  assert.ok(x > 0 && x < 2, `x=${x} should be in the C's spine`);
});

test("labelPoint uses the largest part of a MultiPolygon", () => {
  const small = [[20, 20], [21, 20], [21, 21], [20, 21], [20, 20]];
  assert.deepEqual(labelPoint({ type: "MultiPolygon", coordinates: [[small], [square]] }), [5, 5]);
});

test("labelPoint without a polygon is null", () => {
  assert.equal(labelPoint(null), null);
  assert.equal(labelPoint({ type: "Point", coordinates: [1, 2] }), null);
});
