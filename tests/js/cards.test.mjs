// Which notices the map page shows for a document (UI refresh spec 5.1). Wording is unchanged
// from the old top bar; only where it sits changed.
import assert from "node:assert/strict";
import { test } from "node:test";

import { notices } from "../../src/outages/map/static/js/cards.js";
import { parseIso } from "../../src/outages/map/static/js/timeline.js";

const NOW = parseIso("2026-11-13T10:00:00+08:00");
const meta = (over = {}) => ({
  days: ["2026-11-13", "2026-11-14"],
  last_check_at: "2026-11-13T06:00:00+08:00",
  stale_posts: [],
  ...over,
});
const ids = (list) => list.map((n) => n.id);

test("a fresh current week shows no notice", () => {
  assert.deepEqual(notices(meta(), NOW, "24"), []);
});

test("a past week shows the history notice and never the old-data one", () => {
  const list = notices(meta({ days: ["2026-11-02", "2026-11-03"], last_check_at: null }), NOW, "24");
  assert.deepEqual(ids(list), ["history"]);
  assert.equal(list[0].text, "History: 2–3 Nov 2026");
  assert.equal(list[0].kind, "info");
});

test("no check yet", () => {
  const [n] = notices(meta({ last_check_at: null }), NOW, "24");
  assert.equal(n.id, "old");
  assert.equal(n.kind, "warn");
  assert.equal(n.text, "No data check has run yet, so this map may be empty or out of date.");
});

test("a check 20 hours ago is old data", () => {
  const [n] = notices(meta({ last_check_at: "2026-11-12T14:00:00+08:00" }), NOW, "24");
  assert.equal(n.id, "old");
  assert.ok(n.text.startsWith("This data may be out of date: the last check was 20 hours ago"), n.text);
});

test("a stale post adds the stale notice after the others", () => {
  const list = notices(meta({ last_check_at: null, stale_posts: [{ title: "x" }] }), NOW, "24");
  assert.deepEqual(ids(list), ["old", "stale"]);
  assert.equal(
    list[1].text,
    "VECO updated this post and we couldn't read the update; this is from the earlier version.",
  );
});
