// Selecting a barangay (UI refresh spec 15.2): which camera move each action asks for, and how
// much room the floating UI takes. Pure: map.js does the Leaflet calls.

const MARGIN = 12;
const MAX_COVER = 0.8; // at most 80 % of each axis may be padding: 20 % stays free (a phone sheet is tall)

/** Selection state with camera memory. Each action returns {kind, to?}:
 *  "fly" (fit the new barangay), "revert" (back to `to`, the view before the first selection
 *  of the sequence), "deselect" (no camera move) or "none". */
export function createSelection() {
  let psgc = null;
  let before = null; // the view before the first selection of the sequence
  let fittedZoom = null;
  let moving = false;

  const reset = () => {
    psgc = null;
    before = null;
    fittedZoom = null;
  };

  const api = {
    current: () => psgc,
    select(p, view) {
      if (psgc === null) before = view;
      psgc = p;
      fittedZoom = null;
      return { kind: "fly" };
    },
    toggle(p, view) {
      return p === psgc ? api.clear() : api.select(p, view);
    },
    clear() {
      if (psgc === null) return { kind: "none" };
      const to = before;
      reset();
      return { kind: "revert", to };
    },
    fitted(zoom) {
      fittedZoom = zoom;
    },
    animating(on) {
      moving = on;
    },
    /** The user zoomed: far enough out (2 levels below the fit) deselects, camera stays. */
    zoomed(zoom) {
      if (psgc === null || moving || fittedZoom === null) return { kind: "none" };
      if (zoom > fittedZoom - 2) return { kind: "none" };
      reset();
      return { kind: "deselect" };
    },
  };
  return api;
}

/** Fit padding for Leaflet's fitBounds: each side clears the floating UI covering it (plus a
 *  margin); an axis whose padding would cover more than 80 % of the viewport is scaled down. */
export function uiPadding(viewport, rects) {
  const side = (v) => (v ? v + MARGIN : MARGIN);
  let [l, r, t, b] = [side(rects.left), side(rects.right), side(rects.top), side(rects.bottom)];
  const fit = (a, c, size) => {
    const max = MAX_COVER * size;
    if (a + c <= max) return [a, c];
    const k = max / (a + c);
    return [Math.floor(a * k), Math.floor(c * k)];
  };
  [l, r] = fit(l, r, viewport.w);
  [t, b] = fit(t, b, viewport.h);
  return { paddingTopLeft: [l, t], paddingBottomRight: [r, b] };
}
