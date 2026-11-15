// The barangay card (UI refresh spec 5.3): a click pins it on the left (a sheet above the
// timeline on a phone) with a week strip and the selected day's outages. Also the outage blocks
// shared with the hover popup. Pure helpers first; data reaches the DOM only via textContent.
import { el } from "./dom.js";
import { drawnCount, whyText } from "./streets.js";
import { ambiguousText, entityList } from "./text.js";
import { activeInSlot, byStart, dateLabel, onDay, rangeLabel, safePostUrl } from "./timeline.js";

const PLACES_SHOWN = 8;
const DOW1 = { Mon: "M", Tue: "T", Wed: "W", Thu: "T", Fri: "F", Sat: "S", Sun: "S" };

/** Shown `[id, outage]` pairs of a barangay, by start. */
function shown(outageIds, outages, showCancelled) {
  const out = [];
  for (const id of outageIds) {
    const o = outages[String(id)];
    if (!o || (o.status === "cancelled" && !showCancelled)) continue;
    out.push([String(id), o]);
  }
  return byStart(out);
}

/** One cell per day: "outage" (any shown non-cancelled outage), "cancelled" (only cancelled
 *  ones) or "none". */
export function weekStrip(outageIds, outages, days, showCancelled) {
  const os = shown(outageIds, outages, showCancelled);
  return days.map((day) => {
    const on = os.filter(([, o]) => onDay(o, day));
    const state = !on.length ? "none" : on.some(([, o]) => o.status !== "cancelled") ? "outage" : "cancelled";
    return { day, state };
  });
}

/** The day's shown outages by start, each {id, o, now}: now = active in the selected slot. */
export function dayOutages(outageIds, outages, day, slot, showCancelled) {
  return shown(outageIds, outages, showCancelled)
    .filter(([, o]) => onDay(o, day))
    .map(([id, o]) => ({ id, o, now: activeInSlot(o, day, slot) }));
}

/** Everything the card shows, as a string: while playing, the card is rebuilt only when this
 *  changes (a rebuild every slot would swallow clicks and close "+N more"). */
export function cardKey(props, v) {
  const strip = weekStrip(props.outage_ids, v.outages, v.days, v.showCancelled).map((c) => c.state[0]);
  const list = dayOutages(props.outage_ids, v.outages, v.day, v.slot, v.showCancelled).map((x) => `${x.id}${x.now ? "*" : ""}`);
  return [props.psgc, v.day, v.clock, v.drawing ? 1 : 0, strip.join(""), list.join(",")].join("|");
}

// --- outage blocks (card and hover popup) -----------------------------------------------------

/** A link to VECO's post, or plain text when the URL isn't VECO's. */
export function postLink(post, text = "VECO post") {
  const href = safePostUrl(post && post.url);
  if (!href) return el("span", { class: "muted", text: "(no VECO link)" });
  return el("a", { text, attrs: { href, target: "_blank", rel: "noopener noreferrer" } });
}

export function badge(status) {
  if (status === "additional") return el("span", { class: "badge badge-additional", text: "Additional" });
  if (status === "cancelled") return el("span", { class: "badge badge-cancelled", text: "Cancelled" });
  return null;
}

export function placesLine(entities) {
  const all = entityList(entities, Infinity).shown;
  if (!all.length) return null;
  const { shown: first, more } = entityList(entities, PLACES_SHOWN);
  const p = el("p", { class: "places", text: first.join("; ") });
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

/** "Streets drawn: k of n" and the others with the reason. Nothing when drawing is off: the
 *  places line already names the streets, and there are no lines to explain. */
function streetsBlock(o, drawing) {
  const { drawn, total } = drawnCount(o);
  if (!total || !drawing) return null;
  const box = el("div", { class: "streets" }, [el("p", { text: `Streets drawn: ${drawn} of ${total}` })]);
  const items = o.streets.not_drawn.map((s) => el("li", { text: `${s.name}: ${whyText(s.why)}` }));
  if (items.length) box.append(el("ul", { class: "reasons" }, items));
  return box;
}

/** One outage: time, badge, purpose, places, caveats, streets, post link. ctx = {clock, drawing}. */
export function outageBlock(o, props, isActive, ctx) {
  const when = el("p", { class: "when", text: rangeLabel(o.start, o.end, ctx.clock) }, [badge(o.status)]);
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
  const streets = streetsBlock(o, ctx.drawing);
  if (streets) block.append(streets);
  block.append(el("p", {}, [postLink(o.post)]));
  return block;
}

// --- the card ---------------------------------------------------------------------------------

const STATE_TEXT = { outage: "outage", cancelled: "only cancelled", none: "no outage" };

/** Build the card inside `slotEl`. hooks: selectDay(day), close(). */
export function mountBarangayCard(slotEl, hooks) {
  const title = el("h2", { attrs: { id: "bcard-title" } });
  const close = el("button", { class: "close", text: "✕", attrs: { type: "button", "aria-label": "Close" } });
  const town = el("p", { class: "muted bcard-town" });
  const strip = el("div", { class: "strip", attrs: { role: "group", "aria-label": "Days this week" } });
  const list = el("div", { class: "bcard-list" });
  const card = el(
    "section",
    { class: "card side-card bcard", attrs: { id: "bcard", "aria-labelledby": "bcard-title" } },
    [el("div", { class: "side-head" }, [title, close]), town, strip, list],
  );
  card.hidden = true;
  slotEl.append(card);
  close.addEventListener("click", () => hooks.close());

  let props = null;
  let shownKey = null;
  const sheet = window.matchMedia("(max-width: 599px), (pointer: coarse)");
  const fitSheet = () => card.classList.toggle("sheet", sheet.matches);
  sheet.addEventListener("change", fitSheet);
  fitSheet();

  return {
    card,
    show(p) {
      props = p;
      shownKey = null;
      card.hidden = false;
    },
    hide() {
      props = null;
      card.hidden = true;
    },
    isOpen: () => props !== null,
    psgc: () => (props ? props.psgc : null),
    /** view = {day, slot, days, clock, drawing, showCancelled, outages} */
    render(v) {
      if (!props) return;
      const key = props.covered ? cardKey(props, v) : `meco|${props.psgc}`;
      if (key === shownKey) return;
      shownKey = key;
      title.textContent = props.name;
      if (!props.covered) {
        town.textContent = "MECO area, not covered";
        strip.replaceChildren();
        list.replaceChildren();
        return;
      }
      town.textContent = props.town;
      strip.replaceChildren(
        ...weekStrip(props.outage_ids, v.outages, v.days, v.showCancelled).map((c) => {
          const label = dateLabel(c.day);
          const b = el("button", {
            class: `cell cell-${c.state}${c.day === v.day ? " selected" : ""}`,
            text: DOW1[label.slice(0, 3)] || label.slice(0, 1),
            attrs: {
              type: "button",
              "aria-label": `${label}: ${STATE_TEXT[c.state]}`,
              "aria-pressed": String(c.day === v.day),
            },
          });
          b.addEventListener("click", () => hooks.selectDay(c.day));
          return b;
        }),
      );
      const items = dayOutages(props.outage_ids, v.outages, v.day, v.slot, v.showCancelled);
      list.replaceChildren(
        ...(items.length
          ? items.map((x) => outageBlock(x.o, props, x.now, v))
          : [el("p", { class: "muted empty", text: "No outage listed for this day." })]),
      );
    },
  };
}
