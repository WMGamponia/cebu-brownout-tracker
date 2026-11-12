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

const KEYS = { y: "y", n: "n", s: "s" };
const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** "y" | "n" | "s" for a plain key press outside a text field; otherwise null. */
export function keyFor(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return null;
  const t = event.target || {};
  if (TYPING.has(t.tagName) || t.isContentEditable) return null;
  return KEYS[String(event.key || "").toLowerCase()] || null;
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
