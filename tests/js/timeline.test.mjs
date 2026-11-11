// Manila time logic for the map page (Phase 4 spec 4.6). Run under several TZ values:
// the results must not depend on the machine's zone.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  STALE_AFTER_H,
  activeInHour,
  asOfLabel,
  byStart,
  checkAgeHours,
  dayLabel,
  dayStartMs,
  defaultSelection,
  hourLabel,
  manila,
  needsRefetch,
  onDay,
  parseIso,
  rangeLabel,
  safePostUrl,
} from "../../src/outages/map/static/js/timeline.js";

const o = (start, end) => ({ start, end });
const DAY = o("2026-11-12T08:00:00+08:00", "2026-11-12T17:00:00+08:00");
const NIGHT = o("2026-11-12T22:00:00+08:00", "2026-11-13T05:00:00+08:00");

test("parseIso and manila read +08:00 wall time whatever the zone", () => {
  const ms = parseIso("2026-11-13T00:30:00+08:00");
  assert.equal(ms, Date.UTC(2026, 10, 12, 16, 30));
  assert.deepEqual(manila(ms), { date: "2026-11-13", hour: 0, minute: 30, dow: 5 });
  assert.equal(dayStartMs("2026-11-13"), Date.UTC(2026, 10, 12, 16, 0));
});

test("active-hour edges", () => {
  assert.equal(activeInHour(DAY, "2026-11-12", 7), false);
  assert.equal(activeInHour(DAY, "2026-11-12", 8), true);
  assert.equal(activeInHour(DAY, "2026-11-12", 16), true);
  assert.equal(activeInHour(DAY, "2026-11-12", 17), false);
  const half = o("2026-11-12T08:30:00+08:00", "2026-11-12T16:45:00+08:00");
  assert.equal(activeInHour(half, "2026-11-12", 8), true);
  assert.equal(activeInHour(half, "2026-11-12", 16), true);
  assert.equal(activeInHour(half, "2026-11-12", 7), false);
  assert.equal(activeInHour(half, "2026-11-12", 17), false);
});

test("an overnight outage splits across both days", () => {
  for (const h of [22, 23]) assert.equal(activeInHour(NIGHT, "2026-11-12", h), true);
  assert.equal(activeInHour(NIGHT, "2026-11-12", 21), false);
  for (const h of [0, 1, 2, 3, 4]) assert.equal(activeInHour(NIGHT, "2026-11-13", h), true);
  assert.equal(activeInHour(NIGHT, "2026-11-13", 5), false);
  assert.equal(activeInHour(NIGHT, "2026-11-13", 22), false);
  assert.equal(onDay(NIGHT, "2026-11-12"), true);
  assert.equal(onDay(NIGHT, "2026-11-13"), true);
  assert.equal(onDay(NIGHT, "2026-11-14"), false);
  assert.equal(onDay(NIGHT, "2026-11-11"), false);
});

test("an end at midnight is not on the next day", () => {
  const late = o("2026-11-12T20:00:00+08:00", "2026-11-13T00:00:00+08:00");
  assert.equal(onDay(late, "2026-11-13"), false);
  assert.equal(activeInHour(late, "2026-11-12", 23), true);
});

test("defaultSelection at 00:30 Manila follows Manila, not the browser", () => {
  const now = parseIso("2026-11-13T00:30:00+08:00"); // still 12 Nov in New York
  assert.deepEqual(defaultSelection(now, ["2026-11-12", "2026-11-13"]), {
    day: "2026-11-13",
    hour: 0,
  });
  assert.deepEqual(defaultSelection(now, ["2026-11-20", "2026-11-21"]), {
    day: "2026-11-20",
    hour: 0,
  });
  const mid = parseIso("2026-11-12T15:10:00+08:00");
  assert.deepEqual(defaultSelection(mid, ["2026-11-12"]), { day: "2026-11-12", hour: 15 });
});

test("labels", () => {
  assert.equal(dayLabel("2026-11-12", "2026-11-12"), "Today");
  assert.equal(dayLabel("2026-11-13", "2026-11-12"), "Fri 13");
  assert.equal(hourLabel(22), "22:00–23:00");
  assert.equal(hourLabel(0), "00:00–01:00");
  assert.equal(hourLabel(23), "23:00–24:00");
  assert.equal(rangeLabel(DAY.start, DAY.end), "Thu 12 Nov 08:00–17:00");
  assert.equal(rangeLabel(NIGHT.start, NIGHT.end), "Thu 12 Nov 22:00 – Fri 13 Nov 05:00");
  assert.equal(
    rangeLabel("2026-11-12T20:00:00+08:00", "2026-11-13T00:00:00+08:00"),
    "Thu 12 Nov 20:00–24:00",
  );
  assert.equal(
    rangeLabel("2026-11-12T08:30:00+08:00", "2026-11-12T16:45:00+08:00"),
    "Thu 12 Nov 08:30–16:45",
  );
});

test("checkAgeHours", () => {
  const now = parseIso("2026-11-13T12:00:00+08:00");
  assert.ok(checkAgeHours("2026-11-12T20:00:00+08:00", now) >= STALE_AFTER_H);
  assert.equal(checkAgeHours("2026-11-13T06:00:00+08:00", now), 6);
  assert.equal(checkAgeHours(null, now), null);
  assert.equal(STALE_AFTER_H, 15);
});

test("safePostUrl keeps only VECO https links", () => {
  const veco = "https://www.visayanelectric.com/blog-posts/power-interruption-nov-12";
  assert.equal(safePostUrl(veco), veco);
  for (const bad of [
    "http://www.visayanelectric.com/blog-posts/x",
    "javascript:alert(1)",
    "https://evil.test/blog-posts/x",
    "https://www.visayanelectric.com.evil.test/",
    "https://www.visayanelectric.com@evil.test/",
    "https://www.visayanelectric.com:8443/x",
    "",
    null,
    undefined,
  ]) {
    assert.equal(safePostUrl(bad), null, String(bad));
  }
});

test("asOfLabel", () => {
  assert.equal(asOfLabel("2026-11-12T20:00:00+08:00"), "Thu 12 Nov, 20:00");
  assert.equal(asOfLabel("2026-11-12T16:05:00+00:00"), "Fri 13 Nov, 00:05");
  assert.equal(asOfLabel(null), null);
});

test("byStart orders by Manila start time, then id, not by object key order", () => {
  // JS orders integer-like keys numerically, so {"12": …, "3": …} iterates 3 before 12
  const outages = {
    3: o("2026-11-12T13:00:00+08:00", "2026-11-12T17:00:00+08:00"),
    12: o("2026-11-12T08:00:00+08:00", "2026-11-12T10:00:00+08:00"),
    5: o("2026-11-12T13:00:00+08:00", "2026-11-12T14:00:00+08:00"),
  };
  assert.deepEqual(
    byStart(Object.entries(outages)).map(([id]) => id),
    ["12", "3", "5"],
  );
});

test("needsRefetch after 30 minutes, also on a tab that stays visible", () => {
  const t0 = parseIso("2026-11-12T08:00:00+08:00");
  assert.equal(needsRefetch(t0, t0 + 29 * 60 * 1000), false);
  assert.equal(needsRefetch(t0, t0 + 31 * 60 * 1000), true);
  assert.equal(needsRefetch(0, t0), true);
});
