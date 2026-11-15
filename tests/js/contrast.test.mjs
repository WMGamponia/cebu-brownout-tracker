// WCAG 2.1 AA contrast of the shared dark look (UI refresh spec 7). Reads the tokens from
// base.css, so a later colour edit can't break a pair silently. Translucent colours are composited
// over the worst backdrops a floating card sits on: a white map and the active orange shading.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../../src/outages/map/static/base.css", import.meta.url), "utf8");

/** --name → [r, g, b, a] from `--name: #rrggbb;` or `--name: rgba(r, g, b, a);` in :root. */
function tokens(text) {
  const out = {};
  for (const m of text.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6}|rgba?\([^)]*\))\s*;/g)) {
    out[m[1]] = parse(m[2]);
  }
  return out;
}

function parse(v) {
  if (v.startsWith("#")) return [1, 3, 5].map((i) => parseInt(v.slice(i, i + 2), 16)).concat(1);
  const [r, g, b, a = 1] = v.replace(/rgba?\(|\)/g, "").split(",").map(Number);
  return [r, g, b, a];
}

/** Composite a colour over an opaque backdrop. */
const over = ([r, g, b, a], [br, bg, bb]) => [r * a + br * (1 - a), g * a + bg * (1 - a), b * a + bb * (1 - a), 1];

function lum([r, g, b]) {
  const ch = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

const WHITE = [255, 255, 255, 1];
const ORANGE_FILL = over([242, 140, 40, 0.45], WHITE); // active shading over white tiles
const BACKDROPS = { white: WHITE, "active fill": ORANGE_FILL };
const T = tokens(css);

/** The ratio of `fg` on `bg`, each composited (bg over the backdrop, fg over that). Layers are
 *  token names or literal colours; `under` stacks a surface under bg (e.g. a pill on a card). */
function worst(fg, bg, under = []) {
  let min = Infinity;
  for (const base of Object.values(BACKDROPS)) {
    let surface = base;
    for (const layer of [...under, bg]) surface = over(col(layer), surface);
    min = Math.min(min, ratio(over(col(fg), surface), surface));
  }
  return min;
}

const col = (x) => (Array.isArray(x) ? x : T[x] || assert.fail(`no --${x} in base.css`));

const TEXT_PAIRS = [
  ["text", "card"],
  ["muted", "card"],
  ["accent", "card"],
  ["muted", "card-dim"],
  ["warn-text", "warn-bg", ["card"]],
  ["error-text", "error-bg", ["card"]],
];
const UI_PAIRS = [
  ["focus", "card"],
  ["band", "track", ["card"]],
];

test("text pairs reach 4.5:1 over a white map and the active shading", () => {
  for (const [fg, bg, under] of TEXT_PAIRS) {
    const r = worst(fg, bg, under);
    assert.ok(r >= 4.5, `${fg} on ${bg}: ${r.toFixed(2)}`);
  }
});

test("UI parts reach 3:1", () => {
  for (const [fg, bg, under] of UI_PAIRS) {
    const r = worst(fg, bg, under);
    assert.ok(r >= 3, `${fg} on ${bg}: ${r.toFixed(2)}`);
  }
  // The focus ring is two-tone: the ring on its dark halo, and the halo and a card's edge
  // against the light map itself.
  const ring = ratio(col("focus"), col("focus-halo"));
  assert.ok(ring >= 3, `focus on focus-halo: ${ring.toFixed(2)}`);
  for (const name of ["focus-halo", "card"]) {
    for (const [where, base] of Object.entries(BACKDROPS)) {
      const r = ratio(over(col(name), base), base);
      assert.ok(r >= 3, `${name} on ${where}: ${r.toFixed(2)}`);
    }
  }
});
