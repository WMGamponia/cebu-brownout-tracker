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
  hourLabel,
  manila,
  needsRefetch,
  onDay,
  rangeLabel,
  safePostUrl,
} from "./timeline.js";
import { ambiguousText, entityList, reasonText, removedText } from "./text.js";

// Relative, so the Phase 6 snapshot is this page + that file. The page's own ?from=&days= (other
// weeks) are passed on; the server validates them.
const DATA_URL = (() => {
  const page = new URLSearchParams(location.search);
  const q = new URLSearchParams();
  for (const k of ["from", "days"]) if (page.has(k)) q.set(k, page.get(k));
  return q.toString() ? `outages.geojson?${q}` : "outages.geojson";
})();
const PLACES_SHOWN = 8;
const SHADED = new Set(["scheduled", "additional"]);

const STYLES = {
  active: { color: "#c2410c", weight: 2, dashArray: null, fillColor: "#f28c28", fillOpacity: 0.45 },
  cancelled: { color: "#757575", weight: 1.5, dashArray: "4 4", fillColor: "#bdbdbd", fillOpacity: 0.5 },
  day: { color: "#f5a25d", weight: 1.5, dashArray: null, fillColor: "#f28c28", fillOpacity: 0 },
  none: { color: "#a7adb3", weight: 0.5, dashArray: null, fillColor: "#ffffff", fillOpacity: 0 },
  meco: { color: "#8a8a8a", weight: 0.5, dashArray: null, fillColor: "#9e9e9e", fillOpacity: 0.4 },
};

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
};

// --- map ---------------------------------------------------------------------------------------

const map = L.map("map", { zoomControl: true, attributionControl: true });
map.attributionControl.setPrefix(false);
const tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap contributors",
});
tiles.on("tileerror", () => {}); // offline: polygons still draw on a blank background
tiles.addTo(map);
map.setView([10.32, 123.9], 11);

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
  return { ...STYLES[featureState(feature.properties)], fill: true, opacity: 1 };
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
  const when = el("p", { class: "when", text: rangeLabel(o.start, o.end) }, [badge(o.status)]);
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
  block.append(el("p", {}, [postLink(o.post)]));
  return block;
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

function drawFeatures() {
  if (state.layer) state.layer.remove();
  state.layer = L.geoJSON(
    { type: "FeatureCollection", features: state.doc.features },
    {
      style: styleFor,
      onEachFeature(feature, layer) {
        layer.bindTooltip(tooltipFor(feature.properties), { sticky: true });
        if (!feature.properties.covered) return;
        layer.bindPopup(() => popupContent(feature.properties), { maxWidth: 320 });
        layer.on("popupopen", () => (state.openLayer = layer));
        layer.on("popupclose", () => (state.openLayer = null));
      },
    },
  ).addTo(map);
}

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
  const asOf = asOfLabel(meta.last_check_at);
  $("asof").textContent = asOf ? `Data as of ${asOf}` : "No check has run yet";
  const age = checkAgeHours(meta.last_check_at, Date.now());
  const warn = $("old-warning");
  if (age === null) {
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
  const today = manila(Date.now()).date;
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
  input.setAttribute("aria-valuetext", hourLabel(state.hour));
  $("hour-label").textContent = hourLabel(state.hour);
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
    return el("li", {}, [
      el("p", { class: "when", text: rangeLabel(o.start, o.end) }, [badge(o.status)]),
      el("p", { class: "veco-text", text: o.areas_raw }),
      reasons,
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
  if (state.layer) state.layer.setStyle(styleFor);
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
    const r = await fetch(DATA_URL, { cache: "no-store" });
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
    showBanner(`Couldn't load the outage data (${e.message}).`);
    return;
  }
  $("banner").hidden = true;
  const first = state.doc === null;
  state.doc = doc;
  state.fetchedAt = Date.now();
  const days = doc.meta.days;
  if (first || !days.includes(state.day)) {
    const sel = defaultSelection(Date.now(), days);
    state.day = sel.day;
    state.hour = sel.hour;
  }
  state.openLayer = null;
  map.closePopup();
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
$("retry").addEventListener("click", load);
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
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, Date.now())) load();
});
// A tab left open (a wall display) never changes visibility: refetch it too, and let the
// old-data warning appear when runs are missed.
setInterval(() => {
  if (document.visibilityState === "visible" && needsRefetch(state.fetchedAt, Date.now())) load();
  else if (state.doc) renderTopbar();
}, 5 * 60 * 1000);

load();
