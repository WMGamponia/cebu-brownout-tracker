// The map page (Phase 4 spec 4.6): Leaflet + DOM over one GeoJSON document.
// Data reaches the DOM only through textContent; links only through safePostUrl + setAttribute.
/* global L */

import {
  STALE_AFTER_H,
  activeInHour,
  asOfLabel,
  byStart,
  checkAgeHours,
  dayLabel,
  defaultSelection,
  historyLabel,
  hourLabel,
  manila,
  needsRefetch,
  onDay,
  rangeLabel,
  safePostUrl,
  shiftDate,
} from "./timeline.js";
import { createDwell } from "./hover.js";
import { cullLabels, labelPoint, shortLabel } from "./labels.js";
import { isSnapshotPage, pageSource, showSnapshotChrome } from "./page.js";
import { makeSource, refetchAllowed } from "./source.js";
import { drawnCount, isPastWindow, lineState, linesInGeometry, whyText } from "./streets.js";
import { ambiguousText, entityList, reasonText, removedText } from "./text.js";
import { addTiles } from "./tiles.js";

// Every data URL and every "now" come from the source (Phase 6 spec 4.3): the live server, or a
// static snapshot pinned to its `taken_at`. URLs are relative, so the page works from a subpath.
// The page's own ?from=&days= (other weeks) are read on every load, so the week picker can change
// them with history.replaceState.
let src = makeSource("live", null);
const pageParams = () => new URLSearchParams(location.search);
const PLACES_SHOWN = 8;
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
const LINE_ORDER = { faint: 0, active_cancelled: 1, active: 2 };
const LIGHT_FILL = 0.22;

// The barangay under the mouse, and the one whose popup is open, get a dark outline.
const HIGHLIGHT = { color: "#111111", weight: 3, dashArray: null };

/** "24" or "12" (AM/PM), remembered per browser. Storage can be blocked: then 24-hour. */
function savedClock() {
  try {
    return localStorage.getItem("clock") === "12" ? "12" : "24";
  } catch {
    return "24";
  }
}

const $ = (id) => document.getElementById(id);

/** A DOM element with text set via textContent and plain attributes. */
function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = opts.text;
  for (const [k, v] of Object.entries(opts.attrs || {})) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

/** A link to VECO's post, or plain text when the URL isn't VECO's. */
function postLink(post, text = "VECO post") {
  const href = safePostUrl(post && post.url);
  if (!href) return el("span", { class: "muted", text: "(no VECO link)" });
  return el("a", { text, attrs: { href, target: "_blank", rel: "noopener noreferrer" } });
}

function badge(status) {
  if (status === "additional") return el("span", { class: "badge badge-additional", text: "Additional" });
  if (status === "cancelled") return el("span", { class: "badge badge-cancelled", text: "Cancelled" });
  return null;
}

const state = {
  doc: null,
  day: null,
  hour: 0,
  showCancelled: true,
  timer: null,
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
};

const drawing = () => Boolean(state.doc && state.doc.meta.osm && state.doc.meta.osm.drawing);
const streetsShown = () => drawing() && state.showStreets;

// --- map ---------------------------------------------------------------------------------------

const map = L.map("map", { zoomControl: true, attributionControl: true });
map.attributionControl.setPrefix(false);
addTiles(L, map); // offline: polygons still draw on a blank background
map.setView([10.32, 123.9], 11);

// Short labels sit above the shading and lines, below tooltips and popups; hidden while zooming.
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
  const now = os.filter(([, o]) => activeInHour(o, state.day, state.hour));
  if (now.some(([, o]) => SHADED.has(o.status))) return "active";
  if (now.length) return "cancelled";
  return "day";
}

function styleFor(feature) {
  const props = feature.properties;
  const st = featureState(props);
  const style = { ...STYLES[st], fill: true, opacity: 1 };
  const marked = (layer) => layer && layer.feature === feature;
  if (marked(state.hovered) || marked(state.openLayer)) Object.assign(style, HIGHLIGHT);
  if (st === "active" && streetsShown()) {
    const ids = state.linesIn.get(props.psgc);
    const lit = visibleOutages(props.outage_ids).some(
      ([id, o]) => ids && ids.has(id) && SHADED.has(o.status) && activeInHour(o, state.day, state.hour),
    );
    if (lit) style.fillOpacity = LIGHT_FILL;
  }
  return style;
}

function popupContent(props) {
  const box = el("div", { class: "pop" }, [
    el("h3", { text: props.name }),
    el("p", { class: "town", text: props.town }),
  ]);
  const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
  const active = os.filter(([, o]) => activeInHour(o, state.day, state.hour));
  const later = os.filter(([, o]) => !activeInHour(o, state.day, state.hour));
  if (!os.length) box.append(el("p", { class: "empty", text: "No outage listed for this day." }));
  for (const [, o] of [...active, ...later]) {
    box.append(outageBlock(o, props, active.some(([, a]) => a === o)));
  }
  return box;
}

function outageBlock(o, props, isActive) {
  const when = el("p", { class: "when", text: rangeLabel(o.start, o.end, state.clock) }, [badge(o.status)]);
  const block = el("div", { class: isActive ? "outage active" : "outage" }, [when]);
  if (o.purpose) block.append(el("p", { text: o.purpose }));
  const places = placesLine(o.entities);
  if (places) block.append(places);
  const b = o.barangays.find((x) => x.psgc === props.psgc);
  if (b && b.veco_name && b.veco_name !== b.name) {
    block.append(el("p", { class: "muted", text: `VECO wrote: ${b.veco_name}` }));
  }
  const amb = b ? ambiguousText(b.name, b.town, b.ambiguous_with) : null;
  if (amb) block.append(el("p", { class: "caveat", text: amb }));
  if (o.stale) {
    block.append(
      el("p", {
        class: "caveat",
        text: "VECO updated this post and we couldn't read the update; this is from the earlier version.",
      }),
    );
  }
  const streets = streetsBlock(o);
  if (streets) block.append(streets);
  block.append(el("p", {}, [postLink(o.post)]));
  return block;
}

/** "Streets drawn: k of n" and the others with the reason. Nothing when drawing is off: the
 *  places line already names the streets, and there are no lines to explain. */
function streetsBlock(o) {
  const { drawn, total } = drawnCount(o);
  if (!total || !drawing()) return null;
  const box = el("div", { class: "streets" }, [el("p", { text: `Streets drawn: ${drawn} of ${total}` })]);
  const items = o.streets.not_drawn.map((s) => el("li", { text: `${s.name}: ${whyText(s.why)}` }));
  if (items.length) box.append(el("ul", { class: "reasons" }, items));
  return box;
}

const bare = (name) => String(name || "").replace(/\.$/, "").trim();

function streetPopup(id, s) {
  const o = state.doc.outages[id];
  const box = el("div", { class: "pop" }, [el("h3", { text: s.name })]);
  if (s.osm_name && bare(s.osm_name) !== bare(s.name)) {
    box.append(el("p", { class: "muted", text: `OSM: ${s.osm_name}` }));
  }
  box.append(outageBlock(o, { psgc: null }, activeInHour(o, state.day, state.hour)));
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

function drawStreets() {
  if (!state.streetLayer) state.streetLayer = L.layerGroup().addTo(map);
  state.streetLayer.clearLayers();
  if (!streetsShown()) return;
  const lines = [];
  for (const [id, o] of byStart(Object.entries(state.doc.outages))) {
    const ls = lineState(o, state.day, state.hour, state.showCancelled);
    if (ls === "hidden" || !o.streets) continue;
    for (const s of o.streets.drawn) lines.push([LINE_ORDER[ls], ls, id, s]);
  }
  lines.sort((a, b) => a[0] - b[0]);
  for (const [, ls, id, s] of lines) {
    const pl = L.polyline(linePoints(s.lines), { ...LINE_STYLES[ls], interactive: true });
    pl.bindTooltip(el("span", { text: s.name }), { sticky: true });
    pl.bindPopup(() => streetPopup(id, s), { maxWidth: 320 });
    state.streetLayer.addLayer(pl);
  }
}

function zoomToLines(o) {
  const pts = o.streets.drawn.flatMap((s) => linePoints(s.lines).flat());
  if (!pts.length) return;
  map.fitBounds(L.latLngBounds(pts), { padding: [24, 24], maxZoom: 17 });
}

function placesLine(entities) {
  const all = entityList(entities, Infinity).shown;
  if (!all.length) return null;
  const { shown, more } = entityList(entities, PLACES_SHOWN);
  const p = el("p", { class: "places", text: shown.join("; ") });
  if (more) {
    const btn = el("button", { class: "more", text: `+${more} more`, attrs: { type: "button" } });
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      p.textContent = all.join("; ");
    });
    p.append(" ", btn);
  }
  return p;
}

function tooltipFor(props) {
  if (!props.covered) return el("span", { text: "MECO area, not covered" });
  return el("span", { text: `${props.name}, ${props.town}` });
}

/** Restyle one barangay; a highlighted one comes to the front, under the street lines. */
function restyle(layer) {
  layer.setStyle(styleFor(layer.feature));
  if (layer !== state.hovered && layer !== state.openLayer) return;
  layer.bringToFront();
  if (state.streetLayer) state.streetLayer.eachLayer((l) => l.bringToFront());
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
    if (covered) dwell.enter(layer);
  });
  layer.on("mousemove", (e) => {
    if (!mouseHover() || state.hovered !== layer) return;
    state.cursor = e.latlng;
    if (covered) dwell.enter(layer); // still moving: restart the dwell
  });
  layer.on("mouseout", () => {
    if (state.hovered === layer) state.hovered = null;
    restyle(layer);
    dwell.leave(layer);
  });
  if (!covered) return;
  layer.bindPopup(() => popupContent(feature.properties), { maxWidth: 320 });
  layer.on("click", () => dwell.pin(layer));
  layer.on("popupopen", (e) => {
    const was = state.openLayer;
    state.openLayer = layer;
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
}

// --- short labels -------------------------------------------------------------------------------

/** One short label per barangay with an outage shown this day: build them, then place them. */
function drawLabels() {
  const pane = state.labelPane;
  pane.replaceChildren();
  if (!state.doc) return;
  for (const f of state.doc.features) {
    const props = f.properties;
    if (!props.covered) continue;
    const os = visibleOutages(props.outage_ids).filter(([, o]) => onDay(o, state.day));
    const text = shortLabel(props.name, os, state.clock);
    const at = text && labelPoint(f.geometry);
    if (!at) continue;
    const node = el("div", {
      class: `map-label map-label-${featureState(props)}`,
      text,
      attrs: { "aria-hidden": "true" },
    });
    node.dataset.lat = String(at[1]);
    node.dataset.lng = String(at[0]);
    pane.append(node);
  }
  placeLabels();
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

// --- controls and panel ---------------------------------------------------------------------

function renderTopbar() {
  const meta = state.doc.meta;
  const asOf = asOfLabel(meta.last_check_at, state.clock);
  $("asof").textContent = asOf ? `Data as of ${asOf}` : "No check has run yet";
  $("clock-12").checked = state.clock === "12";
  const age = checkAgeHours(meta.last_check_at, src.now());
  const warn = $("old-warning");
  const past = isPastWindow(meta, src.now());
  $("history-banner").textContent = past ? historyLabel(meta.days) : "";
  $("history-banner").hidden = !past;
  $("week").value = meta.window.from;
  $("week-prev").disabled = src.weekStep(meta.window.from, -1) === null;
  $("week-next").disabled = src.weekStep(meta.window.from, 1) === null;
  $("streets-toggle").hidden = !drawing();
  $("legend-lines").hidden = !drawing();
  $("streets-note").textContent = drawing()
    ? "Lines: streets VECO names, found on OpenStreetMap inside the listed barangays. " +
      "The shading stays: VECO's lists also name sitios and buildings."
    : "Streets and sitios are in each barangay's popup. Street lines are off: matching " +
      "VECO's street names to the map isn't precise enough yet.";
  if (past) {
    warn.hidden = true; // another week: the history banner replaces the old-data warning
  } else if (age === null) {
    warn.textContent = "No data check has run yet, so this map may be empty or out of date.";
    warn.hidden = false;
  } else if (age > STALE_AFTER_H) {
    warn.textContent =
      `This data may be out of date: the last check was ${Math.floor(age)} hours ago ` +
      "(checks run at 06:00 and 20:00).";
    warn.hidden = false;
  } else {
    warn.hidden = true;
  }
  const links = $("stale-links");
  links.replaceChildren(...meta.stale_posts.map((p) => postLink(p, p.title || "VECO post")));
  $("stale").hidden = meta.stale_posts.length === 0;
  $("attribution").textContent = meta.attribution || "";
  const removed = removedText(meta.removed_count);
  $("removed").textContent = removed || "";
  $("removed").hidden = !removed;
}

function renderDays() {
  const today = manila(src.now()).date;
  const tabs = state.doc.meta.days.map((d) => {
    const selected = d === state.day;
    const b = el("button", {
      text: dayLabel(d, today),
      attrs: { type: "button", role: "tab", "aria-selected": String(selected), tabindex: selected ? "0" : "-1" },
    });
    b.addEventListener("click", () => select(d, state.hour));
    return b;
  });
  $("days").replaceChildren(...tabs);
}

function renderHour() {
  const input = $("hour");
  input.value = String(state.hour);
  input.setAttribute("aria-valuetext", hourLabel(state.hour, state.clock));
  $("hour-label").textContent = hourLabel(state.hour, state.clock);
}

function renderUnplaced() {
  const items = byStart(Object.entries(state.doc.outages))
    .map(([, o]) => o)
    .filter((o) => o.unplaced.length && onDay(o, state.day))
    .filter((o) => o.status !== "cancelled" || state.showCancelled);
  $("unplaced-title").textContent = `Not on the map (${items.length})`;
  $("panel-toggle").textContent = items.length ? `Details (${items.length} not on map)` : "Details";
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

function render() {
  renderDays();
  renderHour();
  renderUnplaced();
  drawStreets();
  if (state.layer) state.layer.setStyle(styleFor);
  for (const l of [state.hovered, state.openLayer]) if (l) restyle(l);
  drawLabels();
  if (state.openLayer) state.openLayer.setPopupContent(popupContent(state.openLayer.feature.properties));
}

function select(day, hour) {
  state.day = day;
  state.hour = hour;
  render();
}

function stopPlaying() {
  clearInterval(state.timer);
  state.timer = null;
  $("play").textContent = "▶";
  $("play").setAttribute("aria-label", "Play");
}

function step() {
  const days = state.doc.meta.days;
  const i = days.indexOf(state.day);
  if (state.hour < 23) return select(state.day, state.hour + 1);
  if (i < days.length - 1) return select(days[i + 1], 0);
  stopPlaying();
}

function togglePlay() {
  if (state.timer) return stopPlaying();
  const days = state.doc.meta.days;
  if (state.day === days[days.length - 1] && state.hour === 23) select(days[0], 0);
  state.timer = setInterval(step, 1000);
  $("play").textContent = "❚❚";
  $("play").setAttribute("aria-label", "Pause");
}

function setPanel(open) {
  $("panel").classList.toggle("open", open);
  $("panel-toggle").setAttribute("aria-expanded", String(open));
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
    state.hour = sel.hour;
  }
  state.openLayer = null;
  map.closePopup();
  indexLines();
  drawFeatures();
  if (!state.fitted) fitCovered();
  renderTopbar();
  render();
}

$("hour").addEventListener("input", (e) => select(state.day, Number(e.target.value)));
$("play").addEventListener("click", () => state.doc && togglePlay());
$("show-cancelled").addEventListener("change", (e) => {
  state.showCancelled = e.target.checked;
  if (state.doc) render();
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
  render();
});
$("show-streets").addEventListener("change", (e) => {
  state.showStreets = e.target.checked;
  if (state.doc) render();
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
$("panel-toggle").addEventListener("click", () => setPanel(!$("panel").classList.contains("open")));
$("panel-close").addEventListener("click", () => setPanel(false));
$("days").addEventListener("keydown", (e) => {
  if (!state.doc || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
  const days = state.doc.meta.days;
  const i = days.indexOf(state.day) + (e.key === "ArrowRight" ? 1 : -1);
  if (i < 0 || i >= days.length) return;
  select(days[i], state.hour);
  $("days").children[i].focus();
});
// A snapshot never refetches (its data and its "now" are fixed), not even while snapshot.json loads.
document.addEventListener("visibilitychange", () => {
  if (!refetchAllowed(document.documentElement.dataset.mode, src)) return;
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, src.now())) load();
});
// A tab left open (a wall display) never changes visibility: refetch it too, and let the
// old-data warning appear when runs are missed.
setInterval(() => {
  if (!refetchAllowed(document.documentElement.dataset.mode, src)) return;
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, src.now())) load();
  else if (state.doc) renderTopbar();
}, 5 * 60 * 1000);

/** Pick the data source (live, or the snapshot named by snapshot.json), then load the week. */
async function start() {
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
  load();
}

start();
