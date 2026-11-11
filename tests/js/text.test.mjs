// Fixed page copy (Phase 4 spec 4.6).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ambiguousText,
  entityList,
  joinAnd,
  reasonText,
  removedText,
} from "../../src/outages/map/static/js/text.js";

test("joinAnd", () => {
  assert.equal(joinAnd([]), "");
  assert.equal(joinAnd(["Liloan"]), "Liloan");
  assert.equal(joinAnd(["Consolacion", "Liloan"]), "Consolacion and Liloan");
  assert.equal(joinAnd(["A", "B", "C"]), "A, B and C");
});

test("reasonText for each unplaced kind", () => {
  assert.equal(
    reasonText({ name: "Poblacion", towns: ["Minglanilla"], why: "other_town" }),
    "VECO listed Poblacion under Minglanilla; no Minglanilla barangay has that name",
  );
  assert.equal(
    reasonText({ name: "Poblacion", towns: ["Cebu City", "Talisay City"], why: "other_town" }),
    "VECO listed Poblacion under Cebu City and Talisay City; no Cebu City or Talisay City " +
      "barangay has that name",
  );
  assert.equal(
    reasonText({ name: "Poblacion", towns: [], why: "other_town" }),
    "VECO's town for Poblacion has no barangay of that name",
  );
  assert.equal(
    reasonText({ name: "Lipata", towns: [], why: "unmatched" }),
    "We couldn't match Lipata to a barangay",
  );
  assert.equal(
    reasonText({ name: null, towns: [], why: "not_parsed" }),
    "Areas not processed yet",
  );
  assert.equal(
    reasonText({ name: null, towns: ["Cebu City"], why: "no_barangay" }),
    "VECO named places but no barangay",
  );
});

test("ambiguousText lists every town VECO might mean", () => {
  assert.equal(
    ambiguousText("Tayud", "Liloan", ["Consolacion"]),
    "VECO lists Tayud under Consolacion and Liloan.",
  );
  assert.equal(ambiguousText("Tayud", "Liloan", []), null);
});

test("removedText", () => {
  assert.equal(removedText(0), null);
  assert.equal(removedText(1), "1 outage no longer in VECO's post");
  assert.equal(removedText(3), "3 outages no longer in VECO's post");
});

test("entityList flattens kinds and truncates at 8", () => {
  const e = {
    street: ["A", "B", "C", "D", "E"],
    sitio_purok: ["F", "G"],
    subdivision: ["H", "I"],
    establishment: ["J"],
    other: [],
  };
  assert.deepEqual(entityList(e, 8), { shown: ["A", "B", "C", "D", "E", "F", "G", "H"], more: 2 });
  assert.deepEqual(entityList(e, 100).more, 0);
  assert.deepEqual(entityList({}, 8), { shown: [], more: 0 });
});
