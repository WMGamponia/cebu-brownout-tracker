// Hover-to-open popups on the map page. Pure: no DOM; timers and Leaflet calls are injected.
//
// - The cursor rests on a barangay for `delay` ms: its popup opens ("hover-opened").
// - Leaving the barangay closes a hover-opened popup after `grace` ms, unless the cursor moves
//   into the popup (to click the VECO link) or back onto the barangay first.
// - A click pins the popup: it stays until closed, and hovering elsewhere doesn't replace it.
// The caller reports Leaflet's popupclose with closed(layer).

export function createDwell({ delay = 500, grace = 300, open, close, timers = globalThis }) {
  let pending = null; // {layer, id}: a dwell that will open `layer`
  let graceId = null;
  let shown = null; // the layer whose popup was hover-opened
  let pinned = null; // the layer whose popup was opened by a click

  const cancelPending = () => {
    if (pending) timers.clearTimeout(pending.id);
    pending = null;
  };
  const cancelGrace = () => {
    if (graceId !== null) timers.clearTimeout(graceId);
    graceId = null;
  };
  const startGrace = () => {
    cancelGrace();
    graceId = timers.setTimeout(() => {
      graceId = null;
      const layer = shown;
      shown = null;
      if (layer) close(layer);
    }, grace);
  };

  return {
    enter(layer) {
      if (pinned) return;
      if (shown === layer) return cancelGrace();
      cancelPending();
      const id = timers.setTimeout(() => {
        pending = null;
        if (pinned) return;
        cancelGrace();
        open(layer); // Leaflet closes the previous popup first (its closed() runs here)
        shown = layer;
      }, delay);
      pending = { layer, id };
    },
    leave(layer) {
      if (pending && pending.layer === layer) cancelPending();
      if (shown === layer) startGrace();
    },
    enterPopup() {
      cancelGrace();
    },
    leavePopup() {
      if (shown) startGrace();
    },
    pin(layer) {
      cancelPending();
      cancelGrace();
      shown = null;
      pinned = layer;
    },
    closed(layer) {
      if (shown === layer) {
        shown = null;
        cancelGrace();
      }
      if (pinned === layer) pinned = null;
    },
    reset() {
      cancelPending();
      cancelGrace();
      shown = null;
      pinned = null;
    },
  };
}
