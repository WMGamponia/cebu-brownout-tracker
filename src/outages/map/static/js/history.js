// The history page (Phase 5 spec 4.8): a choropleth of scheduled outage hours per barangay and
// the stats tables, from /boundaries.geojson and /stats.json. Data reaches the DOM only through
// textContent; the one link is built from a date we format ourselves.
/* global L */

import { binOf, legendLabels, monthBars, quantileEdges } from "./bins.js";
import { makeSource } from "./source.js";
import { manila, mondayOf, parseIso } from "./timeline.js";
import { addTiles } from "./tiles.js";

const COLORS = ["#ffffff", "#fee8c8", "#fdbb84", "#fc8d59", "#e34a33", "#b30000"];
const MECO = "#9e9e9e";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const REASONS = {
  other_town: "barangay in a town VECO doesn't serve",
  unmatched: "barangay name not found",
  not_parsed: "areas not processed yet",
  no_barangay: "VECO named places but no barangay",
};

const $ = (id) => document.getElementById(id);

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = opts.text;
  for (const [k, v] of Object.entries(opts.attrs || {})) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const h1 = (x) => String(Number(Number(x).toFixed(1)));

function dateLabel(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  return `${d} ${MON[m - 1]} ${y}`;
}

const state = { doc: null, boundaries: null, layer: null, edges: [], fitted: false };
// The live server's /stats.json, or a snapshot's stats/<range>.json (Phase 6 spec 4.3).
let src = makeSource("live", null);

const map = L.map("map", { attributionControl: true });
map.attributionControl.setPrefix(false);
addTiles(L, map);
map.setView([10.32, 123.9], 11);

function hoursOf(psgc) {
  const b = state.doc.barangays[psgc];
  return b ? b.hours : 0;
}

function styleFor(feature) {
  const p = feature.properties;
  if (!p.covered) return { color: "#8a8a8a", weight: 0.5, fillColor: MECO, fillOpacity: 0.4 };
  const bin = binOf(hoursOf(p.psgc), state.edges);
  if (bin === 0) return { color: "#a7adb3", weight: 0.5, fillOpacity: 0 };
  return { color: "#8a5a3c", weight: 0.6, fillColor: COLORS[bin], fillOpacity: 0.75 };
}

function showDetail(p) {
  const box = $("detail");
  const kids = [el("h3", { text: p.name }), el("p", { class: "muted", text: p.town })];
  if (!p.covered) {
    kids.push(el("p", { text: "MECO area, not covered." }));
    return box.replaceChildren(...kids);
  }
  const b = state.doc.barangays[p.psgc];
  if (!b) {
    kids.push(el("p", { text: "No scheduled outage in this range." }));
    return box.replaceChildren(...kids);
  }
  kids.push(el("p", { text: `${h1(b.hours)} h scheduled, in ${plural(b.outages, "outage")}` }));
  if (b.ambiguous_hours > 0) {
    kids.push(
      el("p", {
        class: "muted",
        text: `${h1(b.ambiguous_hours)} h of them from posts naming a barangay found in two towns`,
      }),
    );
  }
  const last = manila(parseIso(b.last.start)).date;
  kids.push(el("p", { text: `Latest: ${dateLabel(last)}` }));
  const href = `./?from=${mondayOf(last)}&days=7`;
  kids.push(el("p", {}, [el("a", { text: "See that week on the map", attrs: { href } })]));
  box.replaceChildren(...kids);
}

function drawMap() {
  if (state.layer) state.layer.remove();
  state.layer = L.geoJSON(state.boundaries, {
    style: styleFor,
    onEachFeature(feature, layer) {
      const p = feature.properties;
      layer.bindTooltip(el("span", { text: `${p.name}, ${p.town}` }), { sticky: true });
      layer.on("click", () => showDetail(p));
    },
  }).addTo(map);
  if (!state.fitted) fitCovered();
}

/** Fit to VECO's barangays once the map has a size (a 0×0 map would pick a useless zoom). */
function fitCovered() {
  const box = map.getContainer();
  if (!box.clientWidth || !box.clientHeight || !state.boundaries) return;
  const covered = state.boundaries.features.filter((f) => f.properties.covered);
  const bounds = L.geoJSON({ type: "FeatureCollection", features: covered }).getBounds();
  if (!bounds.isValid()) return;
  map.invalidateSize();
  map.fitBounds(bounds, { padding: [8, 8] });
  state.fitted = true;
}

function renderLegend() {
  const items = legendLabels(state.edges).map((label, i) =>
    el("li", {}, [el("span", { class: "sw", attrs: { style: `background:${COLORS[i + 1]}` } }), label]),
  );
  items.unshift(el("li", {}, [el("span", { class: "sw", attrs: { style: "background:#fff" } }), "no scheduled outage"]));
  items.push(el("li", {}, [el("span", { class: "sw", attrs: { style: `background:${MECO}` } }), "MECO area, not covered"]));
  $("legend").replaceChildren(...items);
}

function renderTables() {
  const d = state.doc;
  const cov = d.coverage;
  $("coverage").textContent = cov.first_outage
    ? `Posts from ${dateLabel(cov.first_outage)}; ${cov.posts_parsed} of ${cov.posts} posts read.`
    : "No posts stored yet.";
  $("top").replaceChildren(
    ...d.top.map((b, i) =>
      el("tr", {}, [
        el("td", { text: String(i + 1) }),
        el("td", { text: b.name }),
        el("td", { text: b.town }),
        el("td", { class: "num", text: h1(b.hours) }),
        el("td", { class: "num", text: String(b.outages) }),
      ]),
    ),
  );
  if (!d.top.length) $("top").replaceChildren(el("tr", {}, [el("td", { text: "No outages in this range.", attrs: { colspan: "5" } })]));
  const du = d.duration;
  $("duration").textContent = du.n
    ? `Mean ${h1(du.mean_h)} h, median ${h1(du.median_h)} h, over ${du.n} outages (each outage's full scheduled length).`
    : "No counted outages.";
  $("towns").replaceChildren(
    ...d.towns.map((t) =>
      el("tr", {}, [el("td", { text: t.town }), el("td", { class: "num", text: h1(t.hours) }), el("td", { class: "num", text: String(t.outages) })]),
    ),
  );
  const c = d.counts;
  $("counts").replaceChildren(
    el("li", { text: `${c.counted} scheduled or additional outages counted` }),
    el("li", { text: `${c.stale} of them from a post VECO updated that we couldn't read again (earlier version used)` }),
    el("li", { text: `${c.cancelled} cancelled: not counted` }),
    el("li", { text: `${c.removed} removed from VECO's posts: not counted` }),
  );
  const lo = d.left_out;
  $("left-out").textContent = `${lo.count} outages (${h1(lo.hours)} h) couldn't be placed on a barangay:`;
  $("left-reasons").replaceChildren(
    ...Object.entries(lo.by_reason)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => el("li", { text: `${n}: ${REASONS[k] || k}` })),
  );
}

function renderMonths() {
  const box = $("months");
  const width = box.clientWidth || 600;
  const height = 150;
  box.setAttribute("viewBox", `0 0 ${width} ${height + 20}`);
  const worst = new Set(state.doc.worst_months);
  const bars = monthBars(state.doc.months, width, height);
  const every = Math.max(1, Math.ceil(bars.length / Math.floor(width / 40)));
  const nodes = [];
  bars.forEach((b, i) => {
    const m = state.doc.months[i];
    const rect = svg("rect", { x: b.x, y: b.y, width: b.w, height: Math.max(b.h, 0.5) });
    if (worst.has(m.month)) rect.setAttribute("class", "worst");
    const title = svg("title");
    title.textContent = `${m.month}: ${h1(m.hours)} h, ${m.outages} outages`;
    rect.append(title);
    nodes.push(rect);
    if (i % every === 0) {
      const [y, mo] = m.month.split("-").map(Number);
      const t = svg("text", { x: b.x + b.w / 2, y: height + 14, "text-anchor": "middle" });
      t.textContent = mo === 1 || i === 0 ? `${MON[mo - 1]} ${String(y).slice(2)}` : MON[mo - 1];
      nodes.push(t);
    }
  });
  box.replaceChildren(...nodes);
  const names = state.doc.worst_months.map((m) => {
    const [y, mo] = m.split("-").map(Number);
    return `${MON[mo - 1]} ${y}`;
  });
  $("worst").textContent = names.length ? `Worst months: ${names.join(", ")}.` : "No scheduled hours in this range.";
}

function showBanner(text) {
  $("banner-text").textContent = text;
  $("banner").hidden = false;
}

async function getJson(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try {
      detail = (await r.json()).error || detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return r.json();
}

async function load() {
  try {
    const [doc, boundaries] = await Promise.all([
      getJson(src.statsUrl(new URLSearchParams(location.search))),
      state.boundaries ? Promise.resolve(state.boundaries) : getJson("boundaries.geojson"),
    ]);
    state.doc = doc;
    state.boundaries = boundaries;
  } catch (e) {
    showBanner(`Couldn't load the history (${e.message}).`);
    return;
  }
  $("banner").hidden = true;
  $("from").value = state.doc.range.from;
  $("to").value = state.doc.range.to;
  $("attribution").textContent = state.boundaries.attribution || "";
  state.edges = quantileEdges(Object.values(state.doc.barangays).map((b) => b.hours));
  drawMap();
  renderLegend();
  renderTables();
  renderMonths();
  $("detail").replaceChildren(el("p", { class: "muted", text: "Click a barangay for its numbers." }));
}

$("range").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = new URLSearchParams({ from: $("from").value, to: $("to").value });
  history.replaceState(null, "", `${location.pathname}?${q}`);
  load();
});
new ResizeObserver(() => {
  map.invalidateSize();
  if (!state.fitted) fitCovered();
  if (state.doc) renderMonths();
}).observe(document.body);
new ResizeObserver(() => {
  map.invalidateSize();
  if (!state.fitted) fitCovered();
}).observe(map.getContainer());

load();
