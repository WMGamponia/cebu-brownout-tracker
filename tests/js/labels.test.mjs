// Always-on short labels: text, anchor point and collision culling (nearest the centre wins).
import assert from "node:assert/strict";
import { test } from "node:test";

import { cullLabels, labelPoint, pillOutage, pillState, shortLabel } from "../../src/outages/map/static/js/labels.js";

const out = (start, end, status = "scheduled") => ({ start, end, status });
const NIGHT = out("2026-11-12T22:00:00+08:00", "2026-11-13T06:00:00+08:00");
const DAY = out("2026-11-12T08:00:00+08:00", "2026-11-12T17:00:00+08:00");

test("shortLabel: one outage shows its times; null without outages", () => {
  assert.equal(shortLabel("Mambaling", [["1", NIGHT]], "2026-11-12", 0), "Mambaling · 22:00–06:00");
  assert.equal(shortLabel("Mambaling", [["1", NIGHT]], "2026-11-12", 0, "12"), "Mambaling · 10 PM–6 AM");
  assert.equal(shortLabel("Apas", [], "2026-11-12", 0), null);
});

const S = (start, end, status = "scheduled") => ({
  start: `2026-11-12T${start}:00+08:00`,
  end: `2026-11-12T${end}:00+08:00`,
  status,
});
const D = "2026-11-12";

test("pillState: now > later > ended; cancelled never counts", () => {
  const ended = ["1", S("06:00", "09:00")];
  const cxNow = ["2", S("12:00", "15:00", "cancelled")];
  assert.equal(pillState([ended, cxNow], D, 52), "ended"); // 13:00
  assert.equal(pillState([cxNow], D, 52), "cancelled");
  assert.equal(pillState([["3", S("18:00", "22:00")], ended], D, 52), "later");
  assert.equal(pillState([["3", S("13:00", "14:00")]], D, 52), "now");
  assert.equal(pillState([], D, 52), null);
});

test("pillState: an outage starting inside the slot is now, not later", () => {
  assert.equal(pillState([["1", S("13:10", "14:00")]], D, 52), "now");
  assert.equal(pillState([["1", S("13:15", "14:00")]], D, 52), "later");
});

test("pillOutage: first now, else first later, else last ended", () => {
  const a = ["1", S("06:00", "07:00")];
  const b = ["2", S("08:00", "09:00")];
  const c = ["3", S("20:00", "21:00")];
  const d = ["4", S("22:00", "23:00")];
  assert.equal(pillOutage([a, b, c, d], D, 34)[0], "2"); // 08:30 now
  assert.equal(pillOutage([a, b, c, d], D, 48)[0], "3"); // 12:00 later
  assert.equal(pillOutage([a, b], D, 48)[0], "2"); // all ended: the last
  assert.equal(pillOutage([], D, 48), null);
});

test("shortLabel names the relevant outage and counts the rest", () => {
  const a = ["1", S("08:00", "10:00")];
  const b = ["2", S("22:00", "23:30")];
  assert.equal(shortLabel("Apas", [a, b], D, 80, "12"), "Apas · 10 PM–11:30 PM +1"); // 20:00 → later = b
  assert.equal(shortLabel("Apas", [a, b], D, 33), "Apas · 08:00–10:00 +1"); // 08:15 → now = a
  assert.equal(shortLabel("Pardo", [["4", S("13:00", "16:00", "cancelled")]], D, 0), "Pardo · 13:00–16:00 (cancelled)");
  const cx = ["5", S("15:00", "16:00", "cancelled")];
  assert.equal(shortLabel("Apas", [a, cx], D, 0), "Apas · 08:00–10:00"); // a cancelled one isn't counted
  assert.equal(
    shortLabel("Pardo", [["4", S("13:00", "16:00", "cancelled")], cx], D, 0),
    "Pardo · 13:00–16:00 (cancelled) +1",
  );
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
