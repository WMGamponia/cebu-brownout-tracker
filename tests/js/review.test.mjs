// Street review page logic (Streets v2 spec 7.9). Pure; run under several TZ values.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
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

test("keyFor maps y / n / s, any case", () => {
  assert.equal(keyFor(ev("y")), "y");
  assert.equal(keyFor(ev("N")), "n");
  assert.equal(keyFor(ev("s")), "s");
  assert.equal(keyFor(ev("x")), null);
});

test("keyFor ignores keys while focus is in an input", () => {
  assert.equal(keyFor(ev("y", "INPUT")), null);
  assert.equal(keyFor(ev("y", "TEXTAREA")), null);
  assert.equal(keyFor(ev("y", "SELECT")), null);
  assert.equal(keyFor(ev("y", "DIV", { target: { tagName: "DIV", isContentEditable: true } })), null);
});

test("keyFor ignores modified keys and repeats", () => {
  assert.equal(keyFor(ev("y", "BODY", { ctrlKey: true })), null);
  assert.equal(keyFor(ev("n", "BODY", { metaKey: true })), null);
  assert.equal(keyFor(ev("s", "BODY", { altKey: true })), null);
  assert.equal(keyFor(ev("y", "BODY", { repeat: true })), null);
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
