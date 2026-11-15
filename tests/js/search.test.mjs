// Barangay search: matching, order, and the brownout status at the current time (spec 15.3).
import assert from "node:assert/strict";
import { test } from "node:test";

import { searchBarangays, searchStatus } from "../../src/outages/map/static/js/search.js";
import { parseIso } from "../../src/outages/map/static/js/timeline.js";

const f = (psgc, name, town, covered = true) => ({ properties: { psgc, name, town, covered, outage_ids: [] } });
const FEATURES = [
  f("1", "Lahug", "Cebu City"),
  f("2", "Apas", "Cebu City"),
  f("3", "Santo Niño", "Cebu City"),
  f("4", "Pulpogan", "Consolacion"),
  f("5", "Gun-ob", "Lapu-Lapu City", false),
  f("6", "Talamban", "Cebu City"),
  f("7", "Bulacao", "Talisay City"),
];
const names = (list) => list.map((p) => p.name);

test("every word must appear in the name or town, case and accents ignored", () => {
  assert.deepEqual(names(searchBarangays(FEATURES, "lahug")), ["Lahug"]);
  assert.deepEqual(names(searchBarangays(FEATURES, "cebu lah")), ["Lahug"]);
  assert.deepEqual(names(searchBarangays(FEATURES, "santo nino")), ["Santo Niño"]);
  assert.deepEqual(names(searchBarangays(FEATURES, "  ")), []);
});

test("names starting with the query come first, then by name; MECO areas are left out", () => {
  assert.deepEqual(names(searchBarangays(FEATURES, "la")), ["Lahug", "Bulacao", "Pulpogan", "Talamban"]);
  assert.deepEqual(names(searchBarangays(FEATURES, "gun")), []);
});

test("at most `limit` results", () => {
  assert.equal(searchBarangays(FEATURES, "c", 3).length, 3);
});

const o = (start, end, status = "scheduled") => ({ start, end, status });
const T = (s) => `2026-11-15T${s}:00+08:00`;
const at = (s) => parseIso(T(s));
const DAYTIME = { 1: o(T("08:00"), T("17:00")) };

test("status now: an outage on at the current time", () => {
  assert.deepEqual(searchStatus([1], DAYTIME, at("10:00"), "24"), { state: "now", text: "Now · 08:00–17:00" });
});

test("status upcoming: today, or with its date when later", () => {
  assert.deepEqual(searchStatus([1], DAYTIME, at("07:00"), "24"), { state: "upcoming", text: "Today 08:00–17:00" });
  const tomorrow = { 2: o("2026-11-16T09:00:00+08:00", "2026-11-16T12:00:00+08:00") };
  assert.deepEqual(searchStatus([2], tomorrow, at("07:00"), "24"), {
    state: "upcoming",
    text: "Mon 16 Nov 09:00–12:00",
  });
});

test("status cancelled when the only outage now or later is cancelled", () => {
  const cx = { 3: o(T("13:00"), T("15:00"), "cancelled") };
  assert.deepEqual(searchStatus([3], cx, at("10:00"), "24"), { state: "cancelled", text: "Cancelled · 13:00–15:00" });
});

test("a real outage outranks a cancelled one; past outages give no status", () => {
  const mix = { 3: o(T("09:00"), T("11:00"), "cancelled"), 4: o(T("18:00"), T("20:00")) };
  assert.equal(searchStatus([3, 4], mix, at("10:00"), "24").state, "upcoming");
  assert.deepEqual(searchStatus([1], DAYTIME, at("18:00"), "24"), { state: null, text: "" });
  assert.deepEqual(searchStatus([], DAYTIME, at("10:00"), "24"), { state: null, text: "" });
});

test("an overnight outage is now at 02:00 on its second day", () => {
  const night = { 5: o(T("22:00"), "2026-11-16T05:00:00+08:00") };
  assert.deepEqual(searchStatus([5], night, parseIso("2026-11-16T02:00:00+08:00"), "12"), {
    state: "now",
    text: "Now · 10 PM–5 AM",
  });
});
