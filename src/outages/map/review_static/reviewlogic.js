// Street review page logic (Streets v2 spec 7.9): pure, no DOM, tested under node --test.

/** Index of the next undecided item after `i`, wrapping to the first; -1 when none is left. */
export function nextIndex(items, i) {
  const n = items.length;
  for (let step = 1; step <= n; step++) {
    const j = (((i + step) % n) + n) % n;
    if (!items[j].decided) return j;
  }
  return -1;
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
