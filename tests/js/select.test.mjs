// Selecting a barangay: camera memory, revert, zoom-out deselect, UI padding (spec 15.2).
import assert from "node:assert/strict";
import { test } from "node:test";

import { createSelection, uiPadding } from "../../src/outages/map/static/js/select.js";

const V0 = { center: [10.32, 123.9], zoom: 11 };
const V1 = { center: [10.4, 123.95], zoom: 15 };

test("selecting A then B then clearing reverts to the view before A", () => {
  const s = createSelection();
  assert.equal(s.select("A", V0).kind, "fly");
  assert.equal(s.select("B", V1).kind, "fly"); // V1 is where A's fly landed: not remembered
  assert.equal(s.current(), "B");
  assert.deepEqual(s.clear(), { kind: "revert", to: V0 });
  assert.equal(s.current(), null);
});

test("toggling the selected barangay reverts; toggling another selects it", () => {
  const s = createSelection();
  assert.equal(s.toggle("A", V0).kind, "fly");
  assert.equal(s.toggle("B", V1).kind, "fly");
  assert.deepEqual(s.toggle("B", V1), { kind: "revert", to: V0 });
  assert.equal(s.current(), null);
});

test("clear with nothing selected does nothing", () => {
  assert.deepEqual(createSelection().clear(), { kind: "none" });
});

test("zooming out 2 levels below the fitted zoom deselects without a revert", () => {
  const s = createSelection();
  s.select("A", V0);
  s.fitted(15);
  assert.equal(s.zoomed(14).kind, "none");
  assert.deepEqual(s.zoomed(13), { kind: "deselect" });
  assert.equal(s.current(), null);
  assert.deepEqual(s.clear(), { kind: "none" }); // the memory went with it
});

test("the map's own animations never count as zooming out", () => {
  const s = createSelection();
  s.select("A", V0);
  s.fitted(15);
  s.animating(true);
  assert.equal(s.zoomed(11).kind, "none");
  s.animating(false);
  assert.equal(s.current(), "A");
});

test("before the fly lands there is no fitted zoom: zooming does nothing", () => {
  const s = createSelection();
  s.select("A", V0);
  assert.equal(s.zoomed(3).kind, "none");
});

test("uiPadding keeps the barangay clear of the floating cards", () => {
  const p = uiPadding({ w: 1366, h: 800 }, { left: 340, top: 80, right: 56, bottom: 64 });
  assert.deepEqual(p, { paddingTopLeft: [352, 92], paddingBottomRight: [68, 76] });
});

test("uiPadding: a phone with notices above and the card sheet below keeps that band", () => {
  const p = uiPadding({ w: 375, h: 812 }, { top: 174, bottom: 438 });
  assert.deepEqual(p.paddingTopLeft, [12, 186]); // the band between them is used as is
  assert.equal(p.paddingBottomRight[1], 450);
});

test("uiPadding caps each axis so 20 % of the viewport stays free", () => {
  const p = uiPadding({ w: 375, h: 812 }, { top: 300, bottom: 500 });
  const freeH = 812 - p.paddingTopLeft[1] - p.paddingBottomRight[1];
  assert.ok(freeH >= 0.2 * 812 - 1 && freeH < 0.2 * 812 + 2, `free height ${freeH}`);
  assert.equal(p.paddingTopLeft[0], 12);
});

test("only the latest camera move ends the animation guard", () => {
  const s = createSelection();
  s.select("A", V0);
  s.fitted(15);
  const a = s.animating(true);
  const b = s.animating(true);
  s.animating(false, a); // A's late finish must not end B's guard
  assert.equal(s.zoomed(11).kind, "none");
  s.animating(false, b);
  assert.equal(s.zoomed(10).kind, "deselect"); // the user zooms out further once B has landed
});

test("zooming in never deselects, even below the fitted zoom", () => {
  const s = createSelection();
  s.select("A", V0);
  s.fitted(15);
  s.zoomed(14); // the user zoomed out one level
  assert.equal(s.zoomed(14.6).kind, "none"); // an interrupted fly left a fraction; zooming in
  assert.equal(s.current(), "A");
  assert.equal(s.zoomed(13).kind, "deselect");
});
