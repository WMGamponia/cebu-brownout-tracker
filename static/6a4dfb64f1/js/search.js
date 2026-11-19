// Barangay search (UI refresh spec 15.3): matching, and each result's brownout status at the
// current time (never the timeline's). Pure helpers first; the DOM part renders the listbox.
import { el } from "./dom.js";
import { isPastWindow } from "./streets.js";
import { byStart, manila, parseIso, rangeLabel, shortRange } from "./timeline.js";

/** Which document search reads statuses from: the viewed one when it holds today, else the
 *  current window's (`fetch` asks the caller to load it when it isn't there yet). */
export function searchSource(viewed, current, nowMs) {
  if (!isPastWindow(viewed.meta, nowMs)) return { doc: viewed, fetch: false };
  return current ? { doc: current, fetch: false } : { doc: null, fetch: true };
}

/** Lower case, accents removed ("Santo Niño" → "santo nino"). */
const fold = (s) =>
  String(s || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase();

/** Covered barangays whose name + town contain every query word; names starting with the
 *  query first, then by name. Returns feature properties. */
export function searchBarangays(features, query, limit = 8) {
  const q = fold(query).trim();
  const words = q.split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const hits = [];
  for (const f of features) {
    const p = f.properties;
    if (!p.covered) continue;
    const hay = `${fold(p.name)} ${fold(p.town)}`;
    if (words.every((w) => hay.includes(w))) hits.push(p);
  }
  hits.sort((a, b) => {
    const pa = fold(a.name).startsWith(q) ? 0 : 1;
    const pb = fold(b.name).startsWith(q) ? 0 : 1;
    return pa - pb || a.name.localeCompare(b.name) || fold(a.town).localeCompare(fold(b.town));
  });
  return hits.slice(0, limit);
}

/** {state: "now" | "upcoming" | "cancelled" | null, text} at `nowMs`: an outage on now, else the
 *  next one, else a cancelled one now or later; cancelled ones never outrank real ones. */
export function searchStatus(outageIds, outages, nowMs, clock = "24") {
  const pairs = byStart(
    outageIds.map((id) => [String(id), outages[String(id)]]).filter(([, o]) => o && parseIso(o.end) > nowMs),
  );
  const isNow = (o) => parseIso(o.start) <= nowMs && parseIso(o.end) > nowMs;
  const real = pairs.filter(([, o]) => o.status !== "cancelled").map(([, o]) => o);
  const on = real.find(isNow);
  if (on) return { state: "now", text: `Now · ${shortRange(on.start, on.end, clock)}` };
  const next = real[0];
  if (next) {
    const today = manila(parseIso(next.start)).date === manila(nowMs).date;
    const text = today ? `Today ${shortRange(next.start, next.end, clock)}` : rangeLabel(next.start, next.end, clock);
    return { state: "upcoming", text };
  }
  const cx = pairs.find(([, o]) => o.status === "cancelled");
  if (cx) return { state: "cancelled", text: `Cancelled · ${shortRange(cx[1].start, cx[1].end, clock)}` };
  return { state: null, text: "" };
}

/** The search box and its result list. hooks: pick(psgc). */
export function mountSearch(root, hooks) {
  const input = el("input", {
    class: "search-input",
    attrs: {
      type: "search",
      placeholder: "Search barangays",
      "aria-label": "Search barangays",
      role: "combobox",
      "aria-expanded": "false",
      "aria-controls": "search-list",
      "aria-autocomplete": "list",
      autocomplete: "off",
      spellcheck: "false",
    },
  });
  const list = el("ul", { class: "search-list", attrs: { id: "search-list", role: "listbox" } });
  list.hidden = true;
  root.replaceChildren(input, list);

  let data = { features: [], outages: {}, nowMs: 0, clock: "24" };
  let results = [];
  let active = -1;

  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
    active = -1;
  };
  const choose = (i) => {
    const p = results[i];
    if (!p) return;
    input.value = "";
    close();
    input.blur();
    hooks.pick(p.psgc);
  };
  const mark = () => {
    [...list.children].forEach((li, i) => li.setAttribute("aria-selected", String(i === active)));
    if (active >= 0) input.setAttribute("aria-activedescendant", `search-opt-${active}`);
    else input.removeAttribute("aria-activedescendant");
  };
  const render = () => {
    results = searchBarangays(data.features, input.value);
    active = results.length ? 0 : -1;
    list.replaceChildren(
      ...results.map((p, i) => {
        const st = searchStatus(p.outage_ids || [], data.outages, data.nowMs, data.clock);
        const li = el(
          "li",
          {
            class: `search-row${st.state ? ` res-${st.state}` : ""}`,
            attrs: { id: `search-opt-${i}`, role: "option", "aria-selected": "false" },
          },
          [
            el("span", { class: "search-name", text: p.name }),
            el("span", { class: "search-town", text: p.town }),
            st.state ? el("span", { class: "search-status", text: st.text }) : null,
          ],
        );
        li.addEventListener("pointerdown", (e) => e.preventDefault()); // keep focus for the click
        li.addEventListener("click", () => choose(i));
        return li;
      }),
    );
    if (input.value.trim() && !results.length) {
      list.append(el("li", { class: "search-empty muted", text: "No barangay found" }));
    }
    const open = Boolean(input.value.trim());
    list.hidden = !open;
    input.setAttribute("aria-expanded", String(open));
    mark();
  };

  input.addEventListener("input", render);
  input.addEventListener("focus", () => input.value.trim() && render());
  input.addEventListener("blur", close);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!results.length) return;
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + results.length) % results.length;
      mark();
    } else if (e.key === "Enter") {
      e.preventDefault();
      choose(active);
    } else if (e.key === "Escape") {
      e.stopPropagation(); // Esc here clears the search, it doesn't deselect the map
      input.value = "";
      close();
    }
  });

  return {
    input,
    setData(features, outages, nowMs, clock) {
      data = { features, outages, nowMs, clock };
      if (!list.hidden) render();
    },
  };
}
