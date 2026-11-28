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
  let lastZoom = null; // the zoom at the previous zoomend: only a zoom-out can deselect
  let move = 0; // the latest camera move's token; 0 when none is running
  let moves = 0;

  const reset = () => {
    psgc = null;
    before = null;
    fittedZoom = null;
    lastZoom = null;
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
      lastZoom = zoom;
    },
    /** animating(true) starts a camera move and returns its token; animating(false, token) ends
     *  it, but only the latest move's end clears the guard (an earlier move can finish late). */
    animating(on, token) {
      if (on) {
        move = ++moves;
        return move;
      }
      if (token === undefined || token === move) move = 0;
      return move;
    },
    /** The user zoomed: out to 2 levels below the fit deselects, camera stays. Zooming in never
     *  does (an interrupted fly can leave a fraction below the line). */
    zoomed(zoom) {
      const prev = lastZoom;
      lastZoom = zoom;
      if (psgc === null || move || fittedZoom === null) return { kind: "none" };
      if (zoom > fittedZoom - 2 || (prev !== null && zoom >= prev)) return { kind: "none" };
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
