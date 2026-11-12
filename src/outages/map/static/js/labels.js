// Always-on short labels on shaded barangays. Pure: no DOM, no browser zone.
// Labels that would overlap are dropped; the one nearest the map centre wins.
import { shortRange } from "./timeline.js";

/** "Mambaling · 22:00–06:00", "Apas · 2 outages", or null with no outage.
 *  `outages` are the barangay's `[id, outage]` pairs shown for the day. */
export function shortLabel(name, outages, fmt = "24") {
  if (!outages.length) return null;
  if (outages.length > 1) return `${name} · ${outages.length} outages`;
  const o = outages[0][1];
  const when = shortRange(o.start, o.end, fmt);
  return o.status === "cancelled" ? `${name} · ${when} (cancelled)` : `${name} · ${when}`;
}

/** Keys of the labels to show, nearest the centre first. Each item is {key, x, y, w, h}: the
 *  anchor (x, y) in container pixels, with a w×h box centred on it. Labels whose anchor is off
 *  the w×h `size` are dropped; a label overlapping (within `gap` px) one already kept is too. */
export function cullLabels(items, size, gap = 4) {
  const cx = size.w / 2;
  const cy = size.h / 2;
  const d2 = (i) => (i.x - cx) ** 2 + (i.y - cy) ** 2;
  const order = items
    .filter((i) => i.x >= 0 && i.x <= size.w && i.y >= 0 && i.y <= size.h)
    .sort((a, b) => d2(a) - d2(b) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const kept = [];
  for (const i of order) {
    const hit = kept.some(
      (k) => Math.abs(i.x - k.x) * 2 < i.w + k.w + gap * 2 && Math.abs(i.y - k.y) * 2 < i.h + k.h + gap * 2,
    );
    if (!hit) kept.push(i);
  }
  return kept.map((k) => k.key);
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

function ringArea(ring) {
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1];
  return Math.abs(a / 2);
}

/** A [lon, lat] inside a GeoJSON Polygon or MultiPolygon (its largest part) to hang a label on:
 *  the bounding-box centre when that is inside, else the middle of the widest stretch of the
 *  polygon along the box's middle latitude. Null for anything else. */
export function labelPoint(geometry) {
  if (!geometry) return null;
  const polys =
    geometry.type === "Polygon" ? [geometry.coordinates] : geometry.type === "MultiPolygon" ? geometry.coordinates : [];
  const rings = polys.map((p) => p[0]).filter((r) => r && r.length >= 3);
  if (!rings.length) return null;
  const ring = rings.reduce((a, b) => (ringArea(b) > ringArea(a) ? b : a));
  const xs = ring.map((p) => p[0]);
  const ys = ring.map((p) => p[1]);
  const x = (Math.min(...xs) + Math.max(...xs)) / 2;
  const y = (Math.min(...ys) + Math.max(...ys)) / 2;
  if (inRing(x, y, ring)) return [x, y];
  const cuts = [];
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y) cuts.push(xi + ((y - yi) * (xj - xi)) / (yj - yi));
  }
  cuts.sort((a, b) => a - b);
  let best = null;
  for (let k = 0; k + 1 < cuts.length; k += 2) {
    if (!best || cuts[k + 1] - cuts[k] > best[1] - best[0]) best = [cuts[k], cuts[k + 1]];
  }
  return best ? [(best[0] + best[1]) / 2, y] : [ring[0][0], ring[0][1]];
}
