// The map page (Phase 4 spec 4.6): Leaflet + DOM over one GeoJSON document.
// Data reaches the DOM only through textContent; links only through safePostUrl + setAttribute
// (bcard.js postLink).
/* global L */

import {
  SLOTS,
  activeInSlot,
  asOfLabel,
  byStart,
  defaultSelection,
  manila,
  needsRefetch,
  onDay,
  parseIso,
  rangeLabel,
  shiftDate,
  slotOf,
  slotLabel,
  stepSlot,
} from "./timeline.js";
import { mountTitleFold, notices, renderNotices } from "./cards.js";
import { mountSearch } from "./search.js";
import { createSelection, uiPadding } from "./select.js";
import { badge, mountBarangayCard, outageBlock, postLink } from "./bcard.js";
import { $, el } from "./dom.js";
import { createDwell } from "./hover.js";
import { changedKeys, dropFades, followSlot, playhead } from "./frame.js";
import { cullLabels, labelPoint, pillState, shortLabel } from "./labels.js";
import { isSnapshotPage, pageSource, showSnapshotChrome } from "./page.js";
import { closeOpenPopover, mountPopover } from "./menu.js";
import { makeSource, refetchAllowed } from "./source.js";
import { isPastWindow, lineOrder, lineState, linesInGeometry } from "./streets.js";
import { reasonText, removedText } from "./text.js";
import { focusKind, keyStep, keysApply, mountTimebar, packLanes } from "./timebar.js";
import { addTiles } from "./tiles.js";

// Every data URL and every "now" come from the source (Phase 6 spec 4.3): the live server, or a
// static snapshot pinned to its `taken_at`. URLs are relative, so the page works from a subpath.
// The page's own ?from=&days= (other weeks) are read on every load, so the week picker can change
// them with history.replaceState.
let src = makeSource("live", null);
const pageParams = () => new URLSearchParams(location.search);
const SHADED = new Set(["scheduled", "additional"]);

const STYLES = {
  active: { color: "#c2410c", weight: 2, dashArray: null, fillColor: "#f28c28", fillOpacity: 0.45 },
  cancelled: { color: "#757575", weight: 1.5, dashArray: "4 4", fillColor: "#bdbdbd", fillOpacity: 0.5 },
  day: { color: "#f5a25d", weight: 1.5, dashArray: null, fillColor: "#f28c28", fillOpacity: 0 },
  none: { color: "#a7adb3", weight: 0.5, dashArray: null, fillColor: "#ffffff", fillOpacity: 0 },
  meco: { color: "#8a8a8a", weight: 0.5, dashArray: null, fillColor: "#9e9e9e", fillOpacity: 0.4 },
};

// Lines on top of shading (Phase 5 spec 4.7). A barangay a drawn active line runs through keeps
// its fill, lighter; it is never unshaded.
const LINE_STYLES = {
  active: { color: "#9a3412", weight: 5, opacity: 0.95, dashArray: null },
  active_cancelled: { color: "#616161", weight: 4, opacity: 0.9, dashArray: "6 6" },
  faint: { color: "#c2410c", weight: 2, opacity: 0.35, dashArray: null },
};
const LIGHT_FILL = 0.22;

// The barangay under the mouse, and the one whose popup is open, get a dark outline.
const HIGHLIGHT = { color: "#111111", weight: 3, dashArray: null };
const OUTLINE = { ...HIGHLIGHT, opacity: 1, fill: false };

/** "24" or "12" (AM/PM), remembered per browser. Storage can be blocked: then 24-hour. */
function savedClock() {
  try {
    return localStorage.getItem("clock") === "12" ? "12" : "24";
  } catch {
    return "24";
  }
}

const state = {
  doc: null,
  day: null,
  slot: 0, // 15-minute slot of the day, 0–95
  showCancelled: true,
  play: null, // {raf, startDay, startSlot, t0, last, intervals, checked} while playing
  fades: true, // short fades on slot changes (off under reduced motion or on a slow device)
  keys: { features: new Map(), lines: new Map(), labels: new Map() }, // last applied state per item
  lines: [], // [{key, pl, outage}] street polylines of the selected day
  labels: [], // [{key, node, name, os}] short labels of the selected day
  fetchedAt: 0,
  layer: null,
  openLayer: null,
  fitted: false,
  showStreets: true,
  streetLayer: null,
  linesIn: new Map(), // psgc -> Set of outage ids with a drawn line inside that barangay
  clock: savedClock(),
  hovered: null, // the barangay layer under the mouse
  cursor: null, // the mouse's latlng over a barangay: hover popups open there
  labelPane: null,
  cardLayer: null, // the barangay whose card is pinned
  follow: false, // the selection follows the clock (the Now button)
  followAfterLoad: false, // Now from another week: follow once the current window is loaded
  popupKey: null, // what the open hover popup shows (refreshPopup)
};

const cardCtx = () => ({ clock: state.clock, drawing: drawing() });

const drawing = () => Boolean(state.doc && state.doc.meta.osm && state.doc.meta.osm.drawing);
const streetsShown = () => drawing() && state.showStreets;

// --- map ---------------------------------------------------------------------------------------

const map = L.map("map", { zoomControl: false, attributionControl: true });
L.control.zoom({ position: "topright" }).addTo(map);
map.attributionControl.setPrefix(false);
addTiles(L, map); // offline: polygons still draw on a blank background
map.setView([10.32, 123.9], 11);

// Short labels sit above the shading and lines, below tooltips and popups; hidden while zooming.
// The dim mask over everything but the selected barangay: above shading and lines, below labels.
map.createPane("dim").style.zIndex = "420";
map.getPane("dim").style.pointerEvents = "none";
state.labelPane = map.createPane("labels");
state.labelPane.classList.add("leaflet-zoom-hide");

// Hover (mouse only): touch taps keep opening popups as before. A tap also fires mouseover,
// so the last pointer type decides, not just the device's hover capability.
const canHover = window.matchMedia("(hover: hover)").matches;
let lastPointer = "mouse";
for (const type of ["pointerdown", "pointermove"]) {
  document.addEventListener(type, (e) => (lastPointer = e.pointerType || "mouse"), { capture: true, passive: true });
}
const mouseHover = () => canHover && lastPointer === "mouse";

const dwell = createDwell({
  // No auto-pan on hover: panning would slide another barangay under the cursor and close it.
  open: (layer) => {
    const popup = layer.getPopup();
    popup.options.autoPan = false;
    layer.openPopup(state.cursor || undefined);
    popup.options.autoPan = true;
  },
  close: (layer) => layer.closePopup(),
});

function visibleOutages(ids) {
  const out = [];
  for (const id of ids) {
    const o = state.doc.outages[String(id)];
    if (!o) continue;
    if (o.status === "cancelled" && !state.showCancelled) continue;
    out.push([String(id), o]);
  }
  return byStart(out);
}

function featureState(props) {
  if (!props.covered) return "meco";
  const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
  if (!os.length) return "none";
  const now = os.filter(([, o]) => activeInSlot(o, state.day, state.slot));
  if (now.some(([, o]) => SHADED.has(o.status))) return "active";
  if (now.length) return "cancelled";
  return "day";
}

/** Everything a barangay's style depends on, as a string: the slot pass restyles only the
 *  barangays whose key changed. */
function styleKey(feature) {
  const props = feature.properties;
  const st = featureState(props);
  const marked = (layer) => layer && layer.feature === feature;
  const hi = marked(state.hovered) || marked(state.openLayer) || marked(state.cardLayer);
  let lit = false;
  if (st === "active" && streetsShown()) {
    const ids = state.linesIn.get(props.psgc);
    lit = visibleOutages(props.outage_ids).some(
      ([id, o]) => ids && ids.has(id) && SHADED.has(o.status) && activeInSlot(o, state.day, state.slot),
    );
  }
  return `${st}|${hi ? 1 : 0}|${lit ? 1 : 0}`;
}

function styleFor(feature) {
  const [st, hi, lit] = styleKey(feature).split("|");
  const style = { ...STYLES[st], fill: true, opacity: 1 };
  if (hi === "1") Object.assign(style, HIGHLIGHT);
  if (lit === "1") style.fillOpacity = LIGHT_FILL;
  return style;
}

function popupContent(props) {
  const box = el("div", { class: "pop" }, [
    el("h3", { text: props.name }),
    el("p", { class: "town", text: props.town }),
  ]);
  const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
  const active = os.filter(([, o]) => activeInSlot(o, state.day, state.slot));
  const later = os.filter(([, o]) => !activeInSlot(o, state.day, state.slot));
  if (!os.length) box.append(el("p", { class: "empty", text: "No outage listed for this day." }));
  for (const [, o] of [...active, ...later]) {
    box.append(outageBlock(o, props, active.some(([, a]) => a === o), cardCtx()));
  }
  return box;
}

const bare = (name) => String(name || "").replace(/\.$/, "").trim();

function streetPopup(id, s) {
  const o = state.doc.outages[id];
  const box = el("div", { class: "pop" }, [el("h3", { text: s.name })]);
  if (s.osm_name && bare(s.osm_name) !== bare(s.name)) {
    box.append(el("p", { class: "muted", text: `OSM: ${s.osm_name}` }));
  }
  box.append(outageBlock(o, { psgc: null }, activeInSlot(o, state.day, state.slot), cardCtx()));
  return box;
}

function linePoints(lines) {
  return lines.map((line) => line.map(([x, y]) => [y, x]));
}

function indexLines() {
  state.linesIn = new Map();
  for (const f of state.doc.features) {
    for (const id of f.properties.outage_ids) {
      const o = state.doc.outages[String(id)];
      if (!o || !o.streets) continue;
      if (o.streets.drawn.some((s) => linesInGeometry(s.lines, f.geometry))) {
        if (!state.linesIn.has(f.properties.psgc)) state.linesIn.set(f.properties.psgc, new Set());
        state.linesIn.get(f.properties.psgc).add(String(id));
      }
    }
  }
}

/** The day's street lines, built once per day (and per toggle); renderSlot restyles them. */
function buildStreets() {
  if (!state.streetLayer) state.streetLayer = L.layerGroup().addTo(map);
  state.streetLayer.clearLayers();
  state.lines = [];
  state.keys.lines = new Map();
  if (!streetsShown()) return;
  for (const [id, o] of byStart(Object.entries(state.doc.outages))) {
    if (!o.streets || lineState(o, state.day, state.slot, state.showCancelled) === "hidden") continue;
    o.streets.drawn.forEach((s, i) => {
      const pl = L.polyline(linePoints(s.lines), { interactive: true });
      pl.bindTooltip(el("span", { text: s.name }), { sticky: true });
      pl.bindPopup(() => streetPopup(id, s), { maxWidth: 320 });
      pl.on("click", L.DomEvent.stopPropagation); // a street popup keeps the card open
      state.streetLayer.addLayer(pl);
      state.lines.push({ key: `${id}:${i}`, pl, outage: o });
    });
  }
}

/** Restyle the lines whose state changed; active lines go on top. */
function restyleStreets() {
  const next = new Map(state.lines.map((l) => [l.key, lineState(l.outage, state.day, state.slot, state.showCancelled)]));
  const changed = new Set(changedKeys(state.keys.lines, next));
  if (!changed.size) return;
  for (const l of state.lines) {
    if (!changed.has(l.key)) continue;
    l.pl.setStyle(LINE_STYLES[next.get(l.key)]);
  }
  state.keys.lines = next;
  raiseLines();
}

/** Bring the street lines to the front in paint order (active on top): after a restyle, and
 *  after the hover outlines are redrawn under them. */
function raiseLines() {
  const byKey = new Map(state.lines.map((l) => [l.key, l]));
  for (const key of lineOrder(state.keys.lines)) byKey.get(key).pl.bringToFront();
}

function zoomToLines(o) {
  const pts = o.streets.drawn.flatMap((s) => linePoints(s.lines).flat());
  if (!pts.length) return;
  map.fitBounds(L.latLngBounds(pts), { padding: [24, 24], maxZoom: 17 });
}

function tooltipFor(props) {
  if (!props.covered) return el("span", { text: "MECO area, not covered" });
  return el("span", { text: `${props.name}, ${props.town}` });
}

/** Restyle one barangay and redraw the highlight outlines. */
function restyle(layer) {
  layer.setStyle(styleFor(layer.feature));
  state.keys.features.set(layer.feature.properties.psgc, styleKey(layer.feature));
  drawOutlines();
}

/** Restyle only the barangays whose style key changed since the last pass. */
function restyleFeatures() {
  if (!state.layer) return;
  const layers = state.layer.getLayers();
  const next = new Map(layers.map((l) => [l.feature.properties.psgc, styleKey(l.feature)]));
  const changed = new Set(changedKeys(state.keys.features, next));
  for (const l of layers) if (changed.has(l.feature.properties.psgc)) l.setStyle(styleFor(l.feature));
  state.keys.features = next;
}

/** The hovered and open barangays' borders, drawn on top as non-interactive copies (under the
 *  street lines) so neighbours can't cover them. The barangay's own path is never moved: in
 *  Chrome, re-appending the SVG path under the cursor loses its mouseout, so its sticky tooltip
 *  and hover state stayed stuck after moving on to the next barangay. */
function drawOutlines() {
  if (!state.outlines) state.outlines = L.layerGroup().addTo(map);
  state.outlines.clearLayers();
  for (const l of new Set([state.hovered, state.openLayer, state.cardLayer])) {
    if (l) state.outlines.addLayer(L.geoJSON(l.feature, { interactive: false, style: () => OUTLINE }));
  }
  raiseLines();
}

const hooked = new WeakSet(); // popup elements already listening for the mouse

function wireFeature(feature, layer) {
  layer.bindTooltip(tooltipFor(feature.properties), { sticky: true });
  const covered = feature.properties.covered;
  layer.on("mouseover", (e) => {
    if (!mouseHover()) return;
    const was = state.hovered;
    state.hovered = layer;
    state.cursor = e.latlng;
    if (was && was !== layer) restyle(was);
    restyle(layer);
    if (covered && state.cardLayer !== layer) dwell.enter(layer); // its card already shows it
  });
  layer.on("mousemove", (e) => {
    if (!mouseHover() || state.hovered !== layer) return;
    state.cursor = e.latlng;
    if (covered && state.cardLayer !== layer) dwell.enter(layer); // still moving: restart the dwell
  });
  layer.on("mouseout", () => {
    if (state.hovered === layer) state.hovered = null;
    restyle(layer);
    dwell.leave(layer);
  });
  layer.on("click", (e) => {
    L.DomEvent.stopPropagation(e); // a click on empty map deselects
    toggleBarangay(layer);
  });
  if (!covered) return;
  layer.bindPopup(() => popupContent(feature.properties), { maxWidth: 320 });
  // The popup is for hover only: a click pins the card instead. Leaflet (1.9.4, vendored) opens
  // a bound popup on click through _openPopup; drop just that handler.
  layer.off("click", layer._openPopup, layer);
  layer.on("popupopen", (e) => {
    const was = state.openLayer;
    state.openLayer = layer;
    state.popupKey = null; // freshly built by bindPopup; the next slot change compares from here
    if (was && was !== layer) restyle(was);
    restyle(layer);
    layer.closeTooltip(); // the popup already names the barangay
    const box = e.popup.getElement();
    if (box && !hooked.has(box)) {
      hooked.add(box);
      box.addEventListener("mouseenter", () => dwell.enterPopup());
      box.addEventListener("mouseleave", () => dwell.leavePopup());
    }
  });
  layer.on("popupclose", () => {
    if (state.openLayer === layer) state.openLayer = null;
    dwell.closed(layer);
    restyle(layer);
  });
}

function drawFeatures() {
  dwell.reset();
  state.hovered = null;
  if (state.layer) state.layer.remove();
  state.layer = L.geoJSON(
    { type: "FeatureCollection", features: state.doc.features },
    { style: styleFor, onEachFeature: wireFeature },
  ).addTo(map);
  state.keys.features = new Map(state.doc.features.map((f) => [f.properties.psgc, styleKey(f)]));
}

// --- short labels -------------------------------------------------------------------------------

/** One short label per barangay with an outage shown this day: build them, then place them. */
function drawLabels() {
  const pane = state.labelPane;
  pane.replaceChildren();
  state.labels = [];
  state.keys.labels = new Map();
  if (!state.doc) return;
  for (const f of state.doc.features) {
    const props = f.properties;
    if (!props.covered) continue;
    const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
    const at = os.length && labelPoint(f.geometry);
    if (!at) continue;
    const node = el("div", { class: "map-label pill", attrs: { "aria-hidden": "true" } });
    node.dataset.lat = String(at[1]);
    node.dataset.lng = String(at[0]);
    pane.append(node);
    state.labels.push({ key: props.psgc, node, name: props.name, os });
  }
  updateLabels();
  placeLabels();
}

/** Each label's text and pill colour at the selected slot; only changed ones touch the DOM. */
function updateLabels() {
  for (const l of state.labels) {
    const text = shortLabel(l.name, l.os, state.day, state.slot, state.clock);
    const pill = pillState(l.os, state.day, state.slot);
    const key = `${pill}|${text}`;
    if (state.keys.labels.get(l.key) === key) continue;
    state.keys.labels.set(l.key, key);
    l.node.className = `map-label pill pill-${pill}`;
    l.node.textContent = text;
  }
}

/** Position the labels and hide the ones that would overlap (nearest the centre wins). */
function placeLabels() {
  const nodes = [...state.labelPane.children];
  if (!nodes.length) return;
  const size = map.getSize();
  const items = nodes.map((node, i) => {
    const ll = L.latLng(Number(node.dataset.lat), Number(node.dataset.lng));
    const lp = map.latLngToLayerPoint(ll);
    node.style.transform = `translate(${Math.round(lp.x)}px, ${Math.round(lp.y)}px) translate(-50%, -50%)`;
    node.hidden = false;
    const cp = map.latLngToContainerPoint(ll);
    return { key: String(i), x: cp.x, y: cp.y, w: node.offsetWidth, h: node.offsetHeight };
  });
  const keep = new Set(cullLabels(items, { w: size.x, h: size.y }));
  nodes.forEach((node, i) => (node.hidden = !keep.has(String(i))));
}

map.on("moveend zoomend resize", placeLabels);

function fitCovered() {
  const box = map.getContainer();
  if (!box.clientWidth || !box.clientHeight) return; // hidden tab: fit when it gets a size
  const covered = state.doc.features.filter((f) => f.properties.covered);
  if (!covered.length) return;
  const bounds = L.geoJSON({ type: "FeatureCollection", features: covered }).getBounds();
  if (!bounds.isValid()) return;
  map.fitBounds(bounds, { padding: [8, 8] });
  state.fitted = true;
}

// A page opened in a background tab has a 0×0 map, and fitBounds would pick max zoom.
new ResizeObserver(() => {
  map.invalidateSize();
  if (state.doc && !state.fitted) fitCovered();
}).observe(map.getContainer());

// --- cards and the timeline bar ---------------------------------------------------------------

function renderTopbar() {
  const meta = state.doc.meta;
  const asOf = asOfLabel(meta.last_check_at, state.clock);
  $("asof").textContent = asOf ? `Data as of ${asOf}` : "No check has run yet";
  const n = Object.values(state.doc.outages).filter((o) => o.status !== "cancelled").length;
  const when = isPastWindow(meta, src.now()) ? "that week" : "this week";
  $("outage-count").textContent = `${n} ${n === 1 ? "outage" : "outages"} ${when}`;
  $("clock-12").checked = state.clock === "12";
  $("week").value = meta.window.from;
  $("week-prev").disabled = src.weekStep(meta.window.from, -1) === null;
  $("week-next").disabled = src.weekStep(meta.window.from, 1) === null;
  $("streets-toggle").hidden = !drawing();
  $("legend-lines").hidden = !drawing();
  $("streets-note").textContent = drawing()
    ? "Lines: streets VECO names, found on OpenStreetMap inside the listed barangays. " +
      "The shading stays: VECO's lists also name sitios and buildings."
    : "Streets and sitios are in each barangay's card. Street lines are off: matching " +
      "VECO's street names to the map isn't precise enough yet.";
  const staleLinks = meta.stale_posts.map((p) => postLink(p, p.title || "VECO post"));
  renderNotices($("notices"), notices(meta, src.now(), state.clock), { stale: staleLinks });
  $("attribution").textContent = meta.attribution || "";
  const removed = removedText(meta.removed_count);
  $("removed").textContent = removed || "";
  $("removed").hidden = !removed;
}

function renderBar() {
  timebar.render({
    day: state.day,
    slot: state.slot,
    days: state.doc.meta.days,
    today: manila(src.now()).date,
    clock: state.clock,
    lanes: packLanes(state.doc.outages, state.day, state.showCancelled),
    names: Object.fromEntries(
      Object.entries(state.doc.outages).map(([id, o]) => [id, (o.barangays || []).map((b) => b.name)]),
    ),
    selected: selectedOutageIds(),
  });
}

/** The pinned barangay's outage ids: its segments stay bright on the track, the rest dim. */
function selectedOutageIds() {
  const props = state.cardLayer && state.cardLayer.feature.properties;
  return new Set(props && props.covered ? props.outage_ids.map(String) : []);
}

function renderUnplaced() {
  const items = byStart(Object.entries(state.doc.outages))
    .map(([, o]) => o)
    .filter((o) => o.unplaced.length && onDay(o, state.day))
    .filter((o) => o.status !== "cancelled" || state.showCancelled);
  $("unplaced-title").textContent = `Not on the map (${items.length})`;
  $("unplaced-btn").textContent = `Not on the map (${items.length})`;
  const list = items.map((o) => {
    const reasons = el("ul", { class: "reasons" }, o.unplaced.map((u) => el("li", { class: "reason", text: reasonText(u) })));
    let byStreets = null;
    if (streetsShown() && o.streets && o.streets.drawn.length) {
      const show = el("button", { class: "more", text: "show", attrs: { type: "button" } });
      show.addEventListener("click", () => zoomToLines(o));
      byStreets = el("p", { class: "muted", text: "drawn by its streets only " }, [show]);
    }
    return el("li", {}, [
      el("p", { class: "when", text: rangeLabel(o.start, o.end, state.clock) }, [badge(o.status)]),
      el("p", { class: "veco-text", text: o.areas_raw }),
      reasons,
      byStreets,
      el("p", {}, [postLink(o.post)]),
    ]);
  });
  if (!list.length) list.push(el("li", { class: "muted", text: "Every outage this day is on the map." }));
  $("unplaced").replaceChildren(...list);
}

/** Everything that depends on the day or a toggle: lanes, lists, street lines, labels. */
function renderDay() {
  renderBar();
  renderUnplaced();
  buildStreets();
  state.keys.features = new Map(); // a toggle can change any barangay: restyle them all
  renderSlot();
  drawOutlines();
  drawLabels();
}

/** Everything that depends on the slot, touching only what changed. */
function renderSlot() {
  timebar.render({
    day: state.day,
    slot: state.slot,
    days: state.doc.meta.days,
    today: manila(src.now()).date,
    clock: state.clock,
    lanes: null, // the day's lanes are drawn by renderBar
  });
  restyleFeatures();
  restyleStreets();
  updateLabels();
  if (!state.play) placeLabels();
  refreshPopup();
  renderCard();
}

/** Rebuild the open hover popup only when its content changes (day, clock, active outages). */
function refreshPopup() {
  const layer = state.openLayer;
  if (!layer) return;
  const props = layer.feature.properties;
  const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
  const active = os.filter(([, o]) => activeInSlot(o, state.day, state.slot)).map(([id]) => id);
  const key = [props.psgc, state.day, state.clock, drawing() ? 1 : 0, os.map(([id]) => id).join(","), active.join(",")].join("|");
  if (key === state.popupKey) return;
  state.popupKey = key;
  layer.setPopupContent(popupContent(props));
}

function renderCard() {
  if (!bcard.isOpen()) return;
  bcard.render({
    day: state.day,
    slot: state.slot,
    days: state.doc.meta.days,
    showCancelled: state.showCancelled,
    outages: state.doc.outages,
    ...cardCtx(),
  });
}

function openCard(layer) {
  dwell.reset();
  layer.closePopup();
  layer.closeTooltip(); // a tap opens the sticky "Name, Town" tooltip; the card names it already
  showUnplaced(false);
  const was = state.cardLayer;
  state.cardLayer = layer;
  if (was && was !== layer) restyle(was);
  restyle(layer);
  bcard.show(layer.feature.properties);
  renderCard();
  renderBar(); // dim the other outages on the track
}

// --- selection: zoom to the barangay, dim the rest, revert (spec 15.2) -------------------------

const selection = createSelection();
const reduceMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const viewNow = () => ({ center: [map.getCenter().lat, map.getCenter().lng], zoom: map.getZoom() });

/** A click or tap on a barangay: select it (zoom, dim, card), or deselect it when it is the
 *  selected one. MECO areas only show their card. */
function toggleBarangay(layer) {
  const props = layer.feature.properties;
  if (!props.covered) {
    deselect();
    openCard(layer);
    return;
  }
  const act = selection.toggle(props.psgc, viewNow());
  if (act.kind === "revert") return finishDeselect(act);
  selectBarangay(layer);
}

/** Select by psgc (a search pick): same as a click on an unselected barangay. */
function selectByPsgc(psgc) {
  const layer = state.layer && state.layer.getLayers().find((l) => l.feature.properties.psgc === psgc);
  if (!layer) return;
  if (selection.current() === psgc) return;
  selection.select(psgc, viewNow());
  selectBarangay(layer);
}

function selectBarangay(layer) {
  openCard(layer);
  drawDim(layer.feature);
  flyToBarangay(layer);
}

/** Deselect (Esc, ✕, empty map, "Not on the map"): back to the view before the first selection
 *  of the sequence, unless `revert` is false (a reload). */
function deselect(revert = true) {
  const act = selection.clear();
  finishDeselect(revert ? act : { kind: "none" });
}

function finishDeselect(act) {
  closeCard();
  drawDim(null);
  if (act.kind === "revert" && act.to) {
    moveCamera(() => map.setView(act.to.center, act.to.zoom, { animate: !reduceMotion() }), act.to.zoom);
  }
}

/** Run a camera move the selection must not mistake for the user zooming out. A moveend also
 *  fires as a fly starts, so the move counts as done only once the map reaches `zoom` (or after
 *  2 s, if something interrupts it). */
function moveCamera(run, zoom) {
  selection.animating(true);
  let timer = null;
  const done = () => {
    if (Math.abs(map.getZoom() - zoom) > 0.01) return;
    finish();
  };
  const finish = () => {
    map.off("moveend", done);
    clearTimeout(timer);
    selection.animating(false);
  };
  map.on("moveend", done);
  timer = setTimeout(finish, 2000);
  run();
}

/** Fit the barangay in the part of the map the floating UI leaves free. */
function flyToBarangay(layer) {
  const box = map.getContainer().getBoundingClientRect();
  // Shown elements only (fixed-position ones have no offsetParent, so test their boxes).
  const rect = (el) => (el && !el.hidden && el.getClientRects().length ? el.getBoundingClientRect() : null);
  const card = rect(bcard.card);
  const sheet = card && bcard.card.classList.contains("sheet");
  // The top-left stack: title, search and any notices; the lowest edge counts.
  const tops = [$("title-card"), $("search"), $("notices")].map(rect).filter(Boolean);
  const topEdge = tops.length ? Math.max(...tops.map((r) => r.bottom)) : 0;
  const bar = rect($("timebar"));
  const tr = rect(document.querySelector(".corner-tr .round-row"));
  const pad = uiPadding(
    { w: box.width, h: box.height },
    {
      left: card && !sheet ? card.right - box.left : 0,
      top: topEdge ? topEdge - box.top : 0,
      right: tr ? box.right - tr.left : 0,
      bottom: Math.max(bar ? box.bottom - bar.top : 0, sheet ? box.bottom - card.top : 0),
    },
  );
  const bounds = layer.getBounds();
  const target = Math.min(
    16,
    map.getBoundsZoom(bounds, false, L.point(pad.paddingTopLeft).add(pad.paddingBottomRight)),
  );
  selection.fitted(target);
  moveCamera(() => map.flyToBounds(bounds, { ...pad, maxZoom: 16, animate: !reduceMotion(), duration: 0.6 }), target);
}

const WORLD = [
  [-90, -360],
  [-90, 360],
  [90, 360],
  [90, -360],
];

/** The dim mask: the world with the selected barangay's outer rings as holes; null removes it. */
function drawDim(feature) {
  if (state.dim) state.dim.remove();
  state.dim = null;
  if (!feature) return;
  const g = feature.geometry;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [];
  const holes = polys.map((p) => p[0].map(([x, y]) => [y, x]));
  state.dim = L.polygon([WORLD, ...holes], {
    pane: "dim",
    interactive: false,
    stroke: false,
    fillColor: "#111418",
    fillOpacity: 0.35,
  }).addTo(map);
}

map.on("zoomend", () => {
  const act = selection.zoomed(map.getZoom());
  if (act.kind === "deselect") finishDeselect(act); // zoomed out far enough: camera stays
});

function closeCard() {
  if (!bcard.isOpen()) return;
  bcard.hide();
  const was = state.cardLayer;
  state.cardLayer = null;
  if (was) restyle(was);
  if (state.doc) renderBar();
}

function select(day, slot) {
  const newDay = day !== state.day;
  state.day = day;
  state.slot = slot;
  if (newDay) renderDay();
  else renderSlot();
}

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const perfDebug = new URLSearchParams(location.search).get("debug") === "perf";
let maxLongTask = 0;
if (perfDebug && "PerformanceObserver" in window) {
  new PerformanceObserver((list) => {
    for (const e of list.getEntries()) {
      maxLongTask = Math.max(maxLongTask, e.duration);
      console.warn(`longtask ${Math.round(e.duration)} ms`);
    }
  }).observe({ type: "longtask", buffered: false });
}

function setFades(on) {
  state.fades = on;
  $("map").classList.toggle("fades", on && !reducedMotion.matches);
}
reducedMotion.addEventListener("change", () => setFades(state.fades));

function stopPlaying() {
  const p = state.play;
  if (!p) return;
  cancelAnimationFrame(p.raf);
  state.play = null;
  timebar.setPlaying(false);
  timebar.setPlayhead(state.slot, 0);
  placeLabels(); // labels keep their places while playing; re-place on pause
  if (perfDebug) console.warn(`max longtask: ${Math.round(maxLongTask)} ms`);
}

/** One animation frame of playback: glide the playhead, change the map on each new slot. */
function frame(ts) {
  const p = state.play;
  if (!p) return;
  if (p.t0 === null) {
    p.t0 = ts;
  } else {
    const gap = ts - p.last;
    if (gap > 1000) p.t0 += gap; // a hidden tab: carry on where it stopped, don't skip ahead
    else if (ts - p.t0 <= 2000) p.intervals.push(gap);
    if (!p.checked && ts - p.t0 > 2000) {
      p.checked = true;
      if (state.fades && dropFades(p.intervals)) setFades(false); // stays off until reload
    }
  }
  p.last = ts;
  const at = playhead(state.doc.meta.days, p.startDay, p.startSlot, ts - p.t0);
  if (!at) {
    const days = state.doc.meta.days;
    stopPlaying();
    select(days[days.length - 1], SLOTS - 1);
    return;
  }
  if (at.day !== state.day || at.slot !== state.slot) select(at.day, at.slot);
  timebar.setPlayhead(at.slot, reducedMotion.matches ? 0 : at.frac);
  p.raf = requestAnimationFrame(frame);
}

function togglePlay() {
  if (!state.doc) return;
  if (state.play) return stopPlaying();
  setFollow(false);
  const days = state.doc.meta.days;
  if (state.day === days[days.length - 1] && state.slot === SLOTS - 1) select(days[0], 0);
  state.play = { startDay: state.day, startSlot: state.slot, t0: null, last: 0, intervals: [], checked: false };
  state.play.raf = requestAnimationFrame(frame);
  timebar.setPlaying(true);
}

/** A manual change of day or time: pause, then select. */
function pick(day, slot) {
  if (!state.doc) return;
  stopPlaying();
  setFollow(false); // any manual change stops following the clock
  select(day, slot);
}

// --- Now: follow the current time (spec 15.4) ---------------------------------------------------

function setFollow(on) {
  state.follow = on;
  timebar.setFollowing(on);
}

/** While following, move to the current slot; stop once the clock leaves the window. */
function followTick() {
  if (!state.follow || !state.doc || state.play) return;
  const at = followSlot(src.now(), state.doc.meta.days);
  if (!at) return setFollow(false);
  if (at.day !== state.day || at.slot !== state.slot) select(at.day, at.slot);
}
setInterval(followTick, 15 * 1000);

function nowClicked() {
  if (!state.doc) return;
  stopPlaying();
  const at = followSlot(src.now(), state.doc.meta.days);
  if (!at || isPastWindow(state.doc.meta, src.now())) {
    state.followAfterLoad = true; // another week: load the current window, then follow
    goToWeek(null);
    return;
  }
  setFollow(true);
  select(at.day, at.slot);
}

function stepBy(delta) {
  const next = state.doc && stepSlot(state.doc.meta.days, state.day, state.slot, delta);
  if (next) pick(next.day, next.slot);
}

function showUnplaced(open) {
  if (open) deselect();
  $("unplaced-card").hidden = !open;
  if (open) $("unplaced-close").focus({ preventScroll: true });
}

// --- data ---------------------------------------------------------------------------------------

function showBanner(text) {
  $("banner-text").textContent = text;
  $("banner").hidden = false;
}

async function load() {
  let doc;
  try {
    const r = await fetch(src.mapUrl(pageParams()), { cache: "no-store" });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        detail = (await r.json()).error || detail;
      } catch {
        /* not JSON */
      }
      throw new Error(detail);
    }
    doc = await r.json();
  } catch (e) {
    const asked = pageParams().get("from");
    if (src.isSnapshot && asked && src.snapWeek(asked) !== src.snap.snapshot_week) {
      // A week file missing from the snapshot (404 or bad JSON): say so, show the snapshot week.
      goToWeek(null);
      $("week-missing").hidden = false;
      return;
    }
    showBanner(`Couldn't load the outage data (${e.message}).`);
    return;
  }
  $("banner").hidden = true;
  const first = state.doc === null;
  state.doc = doc;
  state.fetchedAt = src.now();
  const days = doc.meta.days;
  if (first || !days.includes(state.day)) {
    const sel = defaultSelection(src.now(), days);
    state.day = sel.day;
    state.slot = sel.slot;
  }
  if (first || state.followAfterLoad) {
    const at = followSlot(src.now(), days); // the page starts following the clock
    if (at) {
      state.day = at.day;
      state.slot = at.slot;
    }
    setFollow(Boolean(at));
    state.followAfterLoad = false;
  } else if (state.follow) {
    const at = followSlot(src.now(), days);
    if (at) [state.day, state.slot] = [at.day, at.slot];
    else setFollow(false);
  }
  state.openLayer = null;
  deselect(false); // the barangay layers are rebuilt; keep the camera where it is
  state.current = null; // refetch the current window for search if this isn't it
  map.closePopup();
  indexLines();
  drawFeatures();
  if (!state.fitted) fitCovered();
  renderTopbar();
  renderDay();
  updateSearch();
}

$("show-cancelled").addEventListener("change", (e) => {
  state.showCancelled = e.target.checked;
  if (state.doc) renderDay();
});
$("retry").addEventListener("click", start);
$("clock-12").addEventListener("change", (e) => {
  state.clock = e.target.checked ? "12" : "24";
  try {
    localStorage.setItem("clock", state.clock);
  } catch {
    /* storage blocked: the choice lasts until the page closes */
  }
  if (!state.doc) return;
  renderTopbar();
  renderDay();
  updateSearch();
});
$("show-streets").addEventListener("change", (e) => {
  state.showStreets = e.target.checked;
  if (state.doc) renderDay();
});

/** Show the 7 days from `from` (null: back to the default window around today). */
function goToWeek(from) {
  const q = new URLSearchParams(location.search);
  if (from) {
    q.set("from", from);
    q.set("days", "7");
  } else {
    q.delete("from");
    q.delete("days");
  }
  const qs = q.toString();
  history.replaceState(null, "", qs ? `${location.pathname}?${qs}` : location.pathname);
  $("week-missing").hidden = true;
  stopPlaying();
  if (!state.followAfterLoad) setFollow(false);
  load();
}
$("week").addEventListener("change", (e) => goToWeek(e.target.value ? src.snapWeek(e.target.value) : null));
function stepWeek(dir) {
  const to = state.doc && src.weekStep(state.doc.meta.window.from, dir);
  if (to) goToWeek(to);
}
$("week-prev").addEventListener("click", () => stepWeek(-1));
$("week-next").addEventListener("click", () => stepWeek(1));
$("week-today").addEventListener("click", () => goToWeek(null));

// --- floating controls ------------------------------------------------------------------------

const timebar = mountTimebar($("timebar"), {
  select: (day, slot) => pick(day, slot),
  stepDay(dir) {
    const days = state.doc ? state.doc.meta.days : [];
    const i = days.indexOf(state.day) + dir;
    if (i >= 0 && i < days.length) pick(days[i], state.slot);
  },
  stepHour: (dir) => stepBy(4 * dir),
  togglePlay,
  now: nowClicked,
  dragStart: stopPlaying,
  dragEnd() {},
});
// The card sheet, the legend and the attribution sit above the bar: use its real height (it wraps
// to more rows on a narrow phone or with larger text).
new ResizeObserver(() => {
  const h = $("timebar").offsetHeight;
  if (h) document.documentElement.style.setProperty("--bar-h", `${h}px`);
}).observe($("timebar"));

const bcard = mountBarangayCard($("left-slot"), {
  selectDay(day) {
    const first = dayOutages(day)[0];
    const at = first && slotOf(parseIso(first.start));
    pick(day, at && at.date === day ? at.slot : 0);
  },
  close: () => deselect(),
});
map.on("click", () => deselect());

/** The pinned barangay's shown outages on `day`, by start. */
function dayOutages(day) {
  const props = state.cardLayer && state.cardLayer.feature.properties;
  if (!props) return [];
  return visibleOutages(props.outage_ids)
    .filter(([, o]) => onDay(o, day))
    .map(([, o]) => o);
}

const search = mountSearch($("search"), { pick: (psgc) => selectByPsgc(psgc) });

/** Search shows each barangay's status at the current time, from the current window's data:
 *  the loaded document when it is the current window, else a separate fetch of the default
 *  window (kept until the next load). */
async function updateSearch() {
  if (!state.doc) return;
  let doc = state.doc;
  if (isPastWindow(state.doc.meta, src.now())) {
    if (!state.current) {
      try {
        const r = await fetch(src.mapUrl(new URLSearchParams()), { cache: "no-store" });
        if (r.ok) state.current = await r.json();
      } catch {
        /* search then shows no statuses; the map itself is unaffected */
      }
    }
    doc = state.current;
  }
  const data = doc || { features: state.doc.features, outages: {} };
  search.setData(data.features, data.outages, src.now(), state.clock);
}

const menu = mountPopover($("menu-btn"), $("menu"));
mountPopover($("info-btn"), $("info"));
mountTitleFold($("title-card"), $("title-fold"));
$("unplaced-btn").addEventListener("click", () => {
  menu.close(false);
  showUnplaced(true);
});
$("unplaced-close").addEventListener("click", () => {
  showUnplaced(false);
  $("menu-btn").focus({ preventScroll: true });
});
$("legend-btn").addEventListener("click", () => {
  const open = !$("legend").classList.contains("open");
  $("legend").classList.toggle("open", open);
  $("legend-btn").setAttribute("aria-expanded", String(open));
});

// Time keys: only with focus on the page or the track (a focused map pans with its arrows, and
// Space presses a focused control).
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || e.defaultPrevented) return;
  if (e.key === "Escape") {
    if (closeOpenPopover()) return;
    if (!$("unplaced-card").hidden) showUnplaced(false);
    deselect();
    return;
  }
  if (!state.doc) return;
  const k = keyStep(e.key, e.shiftKey);
  if (k === null) return;
  if (!keysApply(focusKind(e.target, { track: timebar.track, bar: $("timebar"), map: $("map") }), k)) return;
  e.preventDefault();
  if (k === "toggle") togglePlay();
  else stepBy(k);
});

// A snapshot never refetches (its data and its "now" are fixed), not even while snapshot.json loads.
document.addEventListener("visibilitychange", () => {
  if (!refetchAllowed(document.documentElement.dataset.mode, src)) return;
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, src.now())) load();
});
document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && followTick());
// A tab left open (a wall display) never changes visibility: refetch it too, and let the
// old-data warning appear when runs are missed.
setInterval(() => {
  if (!refetchAllowed(document.documentElement.dataset.mode, src)) return;
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, src.now())) load();
  else if (state.doc) {
    renderTopbar();
    updateSearch(); // statuses follow the clock
  }
}, 5 * 60 * 1000);

/** Pick the data source (live, or the snapshot named by snapshot.json), then load the week. */
async function start() {
  setFades(state.fades);
  if (isSnapshotPage() && !src.isSnapshot) {
    try {
      src = await pageSource();
    } catch (e) {
      showBanner(`Couldn't load the snapshot (${e.message}).`);
      return;
    }
    showSnapshotChrome(src.snap);
    const week = $("week");
    week.min = src.weeks[0];
    week.max = shiftDate(src.snap.snapshot_week, 6);
  }
  // Debug only: ?debug=clock&at=<ISO> runs the live page at another time (to check Now).
  const q = pageParams();
  if (!src.isSnapshot && q.get("debug") === "clock" && q.get("at")) {
    const base = src.now;
    const offset = parseIso(q.get("at")) - base();
    if (Number.isFinite(offset)) src = { ...src, now: () => base() + offset };
  }
  load();
}

start();
