// Street review page logic (Streets v2 spec 7.9): pure, no DOM, tested under node --test.

// Owner's filter (2026-11-15): hide names VECO wrote fewer than `min` times (live counts).
export const MIN_CHOICES = [1, 2, 3, 5];
export const DEFAULT_MIN = 2;

/** Shown at this threshold. An item without a count is shown only at 1. */
export function visible(item, min) {
  return (item.mentions ?? (min <= 1 ? min : 0)) >= min;
}

/** Index of the next undecided, visible item after `i`, wrapping to the first; -1 when none. */
export function nextIndex(items, i, min = 1) {
  const n = items.length;
  for (let step = 1; step <= n; step++) {
    const j = (((i + step) % n) + n) % n;
    if (!items[j].decided && visible(items[j], min)) return j;
  }
  return -1;
}

/** Numbers overlapping loads: a response renders only if no newer load started after it. */
export function loadGuard() {
  let n = 0;
  return { next: () => ++n, isLatest: (seq) => seq === n };
}

/** A name for matching: lower case, accents and punctuation dropped, spaces collapsed. */
export function foldName(s) {
  return s
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

/** The area's names containing every typed word, names starting with the query first,
 *  then alphabetical; at most `limit`. */
export function searchNames(names, query, limit = 8) {
  const q = foldName(query);
  if (!q) return [];
  const words = q.split(" ");
  const hits = names
    .map((name) => ({ name, f: foldName(name) }))
    .filter((x) => words.every((w) => x.f.includes(w)));
  hits.sort((a, b) => b.f.startsWith(q) - a.f.startsWith(q) || a.f.localeCompare(b.f));
  return hits.slice(0, limit).map((x) => x.name);
}

/** The stored threshold if it is one of the choices, else the default. */
export function readMin(raw) {
  const n = Number(raw);
  return raw && MIN_CHOICES.includes(n) ? n : DEFAULT_MIN;
}

export function counterText(left, hidden) {
  const base = left === 1 ? "1 left" : `${left} left`;
  return hidden > 0 ? `${base} · ${hidden} hidden` : base;
}

/** The empty state when rows are only hidden, not done; null when nothing is hidden. */
export function emptyText(hidden) {
  if (!hidden) return null;
  return `Nothing left at this filter: ${hidden} hidden. Lower “Seen at least” to see them.`;
}

// Owner's keys (2026-11-15). The page calls preventDefault on them, so Enter / Space don't
// also click a focused button and Space doesn't scroll.
const KEYS = { Enter: "y", Delete: "n", " ": "s", Backspace: "b" };

/** Where Back goes: the last step taken on this page, and whether its decision must be
 *  cleared on the server (a skip wrote nothing); null when there is nothing to go back to. */
export function backStep(history) {
  if (!history.length) return null;
  const last = history[history.length - 1];
  return { index: last.index, undo: last.decision !== "skip" };
}
const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** "y" | "n" | "s" | "b" for a plain Enter / Delete / Space / Backspace outside a text field;
 *  otherwise null. */
export function keyFor(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return null;
  const t = event.target || {};
  if (TYPING.has(t.tagName) || t.isContentEditable) return null;
  return Object.hasOwn(KEYS, event.key) ? KEYS[event.key] : null;
}

const MESSAGES = {
  403: "Session expired: reload the page",
  409: "Already decided or queue busy; moving on",
  422: "Not a valid choice",
};

export function messageFor(status) {
  return MESSAGES[status] || `Something went wrong (HTTP ${status})`;
}

/** The street the Yes button would accept: the one clicked, else the pick; null if neither. */
export function selectedName(chosen, pick) {
  return chosen || pick || null;
}

const SELECTED = { color: "#c2185b", weight: 6, opacity: 0.95 };
const OTHER = { color: "#1565c0", weight: 3, opacity: 0.8 };

/** Leaflet style for a candidate's line: bold for the selected street, thin otherwise. */
export function lineStyle(name, selected) {
  return { ...(name === selected ? SELECTED : OTHER) };
}

/** A label element whose text is set with textContent: OSM names are data anyone can edit,
 *  and Leaflet renders a string tooltip as HTML (review #4). */
export function labelEl(doc, text) {
  const el = doc.createElement("span");
  el.textContent = text;
  return el;
}
