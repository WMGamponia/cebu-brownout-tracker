// Street review page logic (Streets v2 spec 7.9). Pure; run under several TZ values.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  backStep,
  counterText,
  DEFAULT_MIN,
  emptyText,
  foldName,
  searchNames,
  loadGuard,
  MIN_CHOICES,
  readMin,
  keyFor,
  labelEl,
  lineStyle,
  messageFor,
  nextIndex,
  selectedName,
} from "../../src/outages/map/review_static/reviewlogic.js";

const items = (flags) => flags.map((decided, i) => ({ id: i, decided }));

test("nextIndex: the next undecided item after i", () => {
  assert.equal(nextIndex(items([false, false, false]), 0), 1);
  assert.equal(nextIndex(items([false, true, false]), 0), 2);
});

test("nextIndex wraps to the first undecided", () => {
  assert.equal(nextIndex(items([false, true, false]), 2), 0);
  assert.equal(nextIndex(items([true, false, true]), 2), 1);
});

test("nextIndex: -1 when nothing is left", () => {
  assert.equal(nextIndex(items([true, true]), 0), -1);
  assert.equal(nextIndex([], 0), -1);
});

test("nextIndex can stay on i only when i itself is the last undecided", () => {
  assert.equal(nextIndex(items([true, false, true]), 1), 1);
});

const ev = (key, tagName = "BODY", extra = {}) => ({ key, target: { tagName }, ...extra });

test("keyFor: Enter yes, Delete no, Space skip, Backspace back (owner, 2026-11-15)", () => {
  assert.equal(keyFor(ev("Enter")), "y");
  assert.equal(keyFor(ev("Delete")), "n");
  assert.equal(keyFor(ev(" ")), "s");
  assert.equal(keyFor(ev("Backspace")), "b");
});

test("keyFor: the old letter keys do nothing now", () => {
  for (const k of ["y", "n", "s", "b", "Y", "x"]) assert.equal(keyFor(ev(k)), null);
});

test("backStep: the last step, and whether the server must clear a decision", () => {
  assert.equal(backStep([]), null);
  assert.deepEqual(backStep([{ index: 3, decision: "y" }]), { index: 3, undo: true });
  assert.deepEqual(backStep([{ index: 3, decision: "Other Street" }]), { index: 3, undo: true });
  assert.deepEqual(backStep([{ index: 3, decision: "n" }, { index: 5, decision: "skip" }]), {
    index: 5,
    undo: false,
  });
});

test("keyFor ignores keys while focus is in an input", () => {
  assert.equal(keyFor(ev("Enter", "INPUT")), null);
  assert.equal(keyFor(ev("Backspace", "TEXTAREA")), null);
  assert.equal(keyFor(ev(" ", "SELECT")), null);
  assert.equal(keyFor(ev("Delete", "DIV", { target: { tagName: "DIV", isContentEditable: true } })), null);
});

test("keyFor ignores modified keys and repeats (a held Backspace undoes once)", () => {
  assert.equal(keyFor(ev("Enter", "BODY", { ctrlKey: true })), null);
  assert.equal(keyFor(ev("Delete", "BODY", { metaKey: true })), null);
  assert.equal(keyFor(ev(" ", "BODY", { altKey: true })), null);
  assert.equal(keyFor(ev("Backspace", "BODY", { repeat: true })), null);
});

test("messageFor: 403 asks to reload", () => {
  assert.equal(messageFor(403), "Session expired: reload the page");
  assert.equal(messageFor(409), "Already decided or queue busy; moving on");
  assert.equal(messageFor(422), "Not a valid choice");
  assert.match(messageFor(500), /500/);
});

test("selectedName: the clicked street, else the pick", () => {
  assert.equal(selectedName(null, "A Street"), "A Street");
  assert.equal(selectedName("B Street", "A Street"), "B Street");
  assert.equal(selectedName(null, ""), null);
});

test("lineStyle: only the selected street is bold", () => {
  const on = lineStyle("B Street", "B Street");
  const off = lineStyle("A Street", "B Street");
  assert.ok(on.weight > off.weight);
  assert.notEqual(on.color, off.color);
  assert.deepEqual(lineStyle("A Street", null), off);
});

test("labelEl: a street name becomes text, never HTML (review #4)", () => {
  const made = [];
  const doc = { createElement: (tag) => { const e = { tag }; made.push(e); return e; } };
  const el = labelEl(doc, '<img src=x onerror="alert(1)">');
  assert.equal(el.tag, "span");
  assert.equal(el.textContent, '<img src=x onerror="alert(1)">');
  assert.equal(el.innerHTML, undefined);
});

test("nextIndex skips items under the threshold", () => {
  const xs = [
    { decided: false, mentions: 3 },
    { decided: false, mentions: 1 },
    { decided: false, mentions: 2 },
  ];
  assert.equal(nextIndex(xs, 0, 2), 2);
  assert.equal(nextIndex(xs, 2, 2), 0);
  assert.equal(nextIndex(xs, 0, 5), -1);
  assert.equal(nextIndex(xs, 0), 1); // default 1: unchanged
  assert.equal(nextIndex(xs, -1, 2), 0); // the first visible, for the initial load
});

test("readMin: a listed choice, else 2", () => {
  assert.deepEqual(MIN_CHOICES, [1, 2, 3, 5]);
  assert.equal(DEFAULT_MIN, 2);
  assert.equal(readMin("3"), 3);
  for (const raw of [null, "", "7", "abc", "0", "2.5"]) assert.equal(readMin(raw), 2);
});

test("counterText", () => {
  assert.equal(counterText(204, 720), "204 left · 720 hidden");
  assert.equal(counterText(1, 0), "1 left");
  assert.equal(counterText(0, 3), "0 left · 3 hidden");
});

test("emptyText: null with nothing hidden, else names the hidden count", () => {
  assert.equal(emptyText(0), null);
  assert.match(emptyText(720), /720 hidden/);
});

test("loadGuard: only the latest load may render", () => {
  const g = loadGuard();
  const a = g.next();
  const b = g.next();
  assert.equal(g.isLatest(a), false);
  assert.equal(g.isLatest(b), true);
});

test("foldName: case, accents and punctuation ignored", () => {
  assert.equal(foldName("Juana Osmeña St."), "juana osmena st");
  assert.equal(foldName("A. Soriano Jr. Avenue"), "a soriano jr avenue");
});

test("searchNames: every word must appear; names starting with the query first", () => {
  const names = ["Cebu South Road", "Cebu South Coastal Road", "Juana Osmeña Street", "A. Soriano Jr. Avenue", "Coastal Drive"];
  assert.deepEqual(searchNames(names, "coastal"), ["Coastal Drive", "Cebu South Coastal Road"]);
  assert.deepEqual(searchNames(names, "south coastal"), ["Cebu South Coastal Road"]);
  assert.deepEqual(searchNames(names, "osmena"), ["Juana Osmeña Street"]);
  assert.deepEqual(searchNames(names, "a soriano"), ["A. Soriano Jr. Avenue"]);
  assert.deepEqual(searchNames(names, "  "), []);
  assert.deepEqual(searchNames(names, "zzz"), []);
});

test("searchNames: at most `limit` results", () => {
  const names = Array.from({ length: 20 }, (_, i) => `Road ${i}`);
  assert.equal(searchNames(names, "road").length, 8);
  assert.equal(searchNames(names, "road", 3).length, 3);
});
