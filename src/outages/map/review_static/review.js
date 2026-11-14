// Street review page (Streets v2 spec 7.9). Local only: decides the alias queue, one group at a
// time, with the candidates drawn exactly as the matcher would clip them. Text is set with
// textContent only.
/* global L */
import { addTiles } from "/static/js/tiles.js";
import {
  keyFor,
  lineStyle,
  messageFor,
  nextIndex,
  selectedName,
  labelEl,
} from "/streets/review/static/reviewlogic.js";

const BASE = "/streets/review";
const token = document.querySelector('meta[name="review-token"]').content;
const $ = (id) => document.getElementById(id);

const map = L.map("map", { zoomControl: true }).setView([10.32, 123.9], 12);
addTiles(L, map);
const layers = L.layerGroup().addTo(map);
const lines = new Map(); // candidate name -> its polyline
let bufferLayer = null;
let bufferM = 150;

let items = [];
let at = -1;
let current = null; // the loaded item document
let chosen = null; // a candidate name the owner clicked, or null for the pick
let busy = false;
let stopped = false;

function say(text) {
  $("message").textContent = text;
  $("message").hidden = !text;
}

function left() {
  const n = items.filter((x) => !x.decided).length;
  $("left").textContent = n === 1 ? "1 left" : `${n} left`;
  return n;
}

function bufferWeight() {
  // A stroke 2 × 150 m wide centred on the boundary: its outer edge is the 150 m reach.
  const lat = map.getCenter().lat;
  const mpp = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** map.getZoom();
  return Math.max(2, (2 * bufferM) / mpp);
}

map.on("zoomend", () => bufferLayer && bufferLayer.setStyle({ weight: bufferWeight() }));

function li(parent, child) {
  const el = document.createElement("li");
  el.appendChild(child);
  parent.appendChild(el);
  return el;
}

function restyle() {
  // The selected street is bold and on top; every other candidate thin.
  const sel = selectedName(chosen, current && current.pick);
  for (const [name, line] of lines) {
    line.setStyle(lineStyle(name, sel));
    if (name === sel) line.bringToFront();
  }
}

function choose(name) {
  chosen = current && name === current.pick ? null : name;
  const target = selectedName(chosen, current && current.pick);
  for (const b of $("candidates").querySelectorAll("button")) {
    b.classList.toggle("chosen", b.dataset.name === target);
  }
  restyle();
  $("yes").textContent = "";
  $("yes").append(target ? `Yes: ${target} ` : "Yes ");
  const k = document.createElement("kbd");
  k.textContent = "y";
  $("yes").appendChild(k);
  $("yes").disabled = !target;
}

function draw(doc) {
  layers.clearLayers();
  bufferM = doc.buffer_m || 150;
  const scope = L.geoJSON(doc.scope, {
    style: { color: "#2e7d32", weight: 1, fillColor: "#2e7d32", fillOpacity: 0.12 },
    interactive: false,
  });
  bufferLayer = L.geoJSON(doc.scope, {
    style: { color: "#2e7d32", weight: bufferWeight(), opacity: 0.12, fill: false, dashArray: "6 8" },
    interactive: false,
  });
  bufferLayer.addTo(layers);
  scope.addTo(layers);
  lines.clear();
  for (const c of doc.candidates) {
    if (!c.lines.length) continue;
    const latlngs = c.lines.map((line) => line.map(([lon, lat]) => [lat, lon]));
    const line = L.polyline(latlngs, lineStyle(c.name, selectedName(null, doc.pick)));
    line.bindTooltip(labelEl(document, c.name), { permanent: true, className: "street-label" });
    line.on("click", () => choose(c.name));
    line.addTo(layers);
    lines.set(c.name, line);
  }
  restyle();
  const b = scope.getBounds();
  if (b.isValid()) map.fitBounds(b, { padding: [24, 24] });
}

function render(doc) {
  current = doc;
  chosen = null;
  $("item").hidden = false;
  $("empty").hidden = true;
  $("veco-name").textContent = doc.veco_name;
  $("meta").textContent = `${doc.mentions} mention${doc.mentions === 1 ? "" : "s"}`;
  $("pick").textContent = doc.pick || "No suggestion";
  $("reason").textContent = doc.reason ? `“${doc.reason}”` : "";
  const src = doc.pick ? `Picked by ${doc.pick_source}.` : "";
  const other = doc.other_pick && doc.other_pick !== doc.pick ? ` The other method picked ${doc.other_pick}.` : "";
  $("other").textContent = (src + other).trim() || (doc.status === "invalid" ? "The model gave no valid answer." : "");
  $("map-note").textContent = doc.message || "";
  $("map-note").hidden = !doc.message;
  const ul = $("candidates");
  ul.replaceChildren();
  for (const c of doc.candidates) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.name = c.name;
    btn.textContent = c.name;
    const note = c.generic
      ? " (generic name: --apply refuses it)"
      : c.lines.length
        ? ""
        : " (not drawn in this scope)";
    if (note) {
      const s = document.createElement("span");
      s.className = "nolines";
      s.textContent = note;
      btn.appendChild(s);
    }
    btn.addEventListener("click", () => choose(c.name));
    li(ul, btn);
  }
  const areas = $("areas");
  areas.replaceChildren();
  for (const a of doc.areas) {
    const p = document.createElement("span");
    p.textContent = a;
    li(areas, p);
  }
  choose(null);
  draw(doc);
}

function showEmpty() {
  current = null;
  layers.clearLayers();
  $("item").hidden = true;
  $("empty").hidden = false;
}

async function load(i) {
  at = i;
  if (i < 0) {
    showEmpty();
    return;
  }
  const it = items[i];
  const q = new URLSearchParams({ town: it.town_psgc, key: it.veco_key });
  const r = await fetch(`${BASE}/item?${q}`, { cache: "no-store" });
  if (r.status === 409) {
    it.decided = true;
    left();
    say(messageFor(409));
    return load(nextIndex(items, i));
  }
  if (!r.ok) {
    say(messageFor(r.status));
    return;
  }
  render(await r.json());
}

async function decide(kind) {
  if (busy || stopped || !current || at < 0) return;
  const it = items[at];
  let decision = kind;
  if (kind === "y") {
    if (chosen) decision = chosen;
    else if (!current.pick) return;
  }
  busy = true;
  try {
    const r = await fetch(`${BASE}/decide`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ town: it.town_psgc, key: it.veco_key, decision, token }),
    });
    if (r.status === 204 || r.status === 409) {
      say(r.status === 409 ? messageFor(409) : "");
      it.decided = true;
      left();
      await load(nextIndex(items, at));
    } else {
      say(messageFor(r.status));
      if (r.status === 403) stopped = true;
    }
  } catch {
    say("The server isn't answering; is `serve` or `map` still running?");
  } finally {
    busy = false;
  }
}

$("yes").addEventListener("click", () => decide("y"));
$("no").addEventListener("click", () => decide("n"));
$("skip").addEventListener("click", () => decide("skip"));
document.addEventListener("keydown", (e) => {
  const k = keyFor(e);
  if (!k) return;
  e.preventDefault();
  decide(k === "s" ? "skip" : k);
});

async function start() {
  const r = await fetch(`${BASE}/items`, { cache: "no-store" });
  if (!r.ok) {
    say(messageFor(r.status));
    $("left").textContent = "";
    return;
  }
  items = (await r.json()).map((x) => ({ ...x, decided: false }));
  left();
  await load(items.length ? 0 : -1);
}

start();
