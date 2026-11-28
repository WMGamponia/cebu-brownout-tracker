// Street lines on the map page (Phase 5 spec 4.7). Pure: no DOM, no browser zone.
import { activeInSlot, manila, onDay } from "./timeline.js";

const WHY = {
  not_found: "not found on OpenStreetMap near these barangays",
  no_osm_data: "street data not downloaded",
  not_processed: "street matching not run yet",
  drawing_off: "street lines are off: matching isn't precise enough yet",
};

/** How an outage's lines show at the selected day and 15-minute slot:
 *  "active" (bold orange), "active_cancelled" (grey dashed), "faint" (same day, other time),
 *  "hidden" (another day, or cancelled while cancelled outages are hidden). */
export function lineState(outage, day, slot, showCancelled) {
  if (!onDay(outage, day)) return "hidden";
  const cancelled = outage.status === "cancelled";
  if (cancelled && !showCancelled) return "hidden";
  if (!activeInSlot(outage, day, slot)) return "faint";
  return cancelled ? "active_cancelled" : "active";
}

export const LINE_ORDER = { hidden: -1, faint: 0, active_cancelled: 1, active: 2 };

/** Keys in paint order (bottom first): faint, then cancelled, then active on top. Stable within
 *  a state, so lines keep their start order. `states` maps key → lineState. */
export function lineOrder(states) {
  return [...states.keys()]
    .map((k, i) => [k, i])
    .sort(([a, i], [b, j]) => LINE_ORDER[states.get(a)] - LINE_ORDER[states.get(b)] || i - j)
    .map(([k]) => k);
}

/** {drawn, total} street mentions of an outage. */
export function drawnCount(outage) {
  const s = outage.streets || { drawn: [], not_drawn: [] };
  return { drawn: s.drawn.length, total: s.drawn.length + s.not_drawn.length };
}

/** Why a street isn't drawn, in plain words. */
export function whyText(why) {
  return WHY[why] || "not drawn";
}

function inRing(x, y, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < xi + ((y - yi) * (xj - xi)) / (yj - yi)) inside = !inside;
  }
  return inside;
}

/** True when any node of `lines` ([[[lon, lat], …], …]) lies inside a GeoJSON Polygon or
 *  MultiPolygon (outer rings). Used to lighten the fill of barangays a drawn line runs through. */
export function linesInGeometry(lines, geometry) {
  if (!geometry) return false;
  const polys =
    geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
  for (const line of lines) {
    for (const [x, y] of line) {
      if (polys.some((p) => p[0] && inRing(x, y, p[0]))) return true;
    }
  }
  return false;
}

/** True when the document's days don't include today in Manila (a past or future week). */
export function isPastWindow(meta, nowMs) {
  return !meta.days.includes(manila(nowMs).date);
}
