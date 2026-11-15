// The floating timeline bar (UI refresh spec 5.2): play, ‹ day ›, ‹ time ›, a 24-hour track with
// the day's outage bands and a gliding playhead. Pure helpers first; the DOM part only renders
// and reports input through hooks (map.js owns the selection and the play loop).
import { el } from "./dom.js";
import { SLOTS, activeInSlot, dateLabel, dayLabel, onDay, slotLabel, stepSlot } from "./timeline.js";

/** One boolean per slot of `day`: true when any shown outage is active in it. `outages` is the
 *  document's id → outage object. */
export function bands(outages, day, showCancelled) {
  const out = new Array(SLOTS).fill(false);
  for (const o of Object.values(outages)) {
    if (o.status === "cancelled" && !showCancelled) continue;
    if (!onDay(o, day)) continue;
    for (let s = 0; s < SLOTS; s++) if (!out[s] && activeInSlot(o, day, s)) out[s] = true;
  }
  return out;
}

const clamp = (s) => Math.min(SLOTS - 1, Math.max(0, s));

/** The slot under x on a track `width` px wide (floored, clamped). */
export function slotAtX(x, width) {
  return clamp(Math.floor((x / width) * SLOTS));
}

/** The slot boundary nearest x (rounded, clamped): where a released drag snaps. */
export function nearestSlotAtX(x, width) {
  return clamp(Math.round((x / width) * SLOTS));
}

/** ← / → one slot, with Shift one hour; Space toggles play; anything else null. */
export function keyStep(key, shiftKey) {
  if (key === " ") return "toggle";
  const dir = key === "ArrowRight" ? 1 : key === "ArrowLeft" ? -1 : 0;
  if (!dir) return null;
  return dir * (shiftKey ? 4 : 1);
}

/** The page-wide time keys apply only with focus on the page body or the track: a focused map
 *  pans with its arrows, and Space presses a focused control. */
export function keysApply(kind) {
  return kind === "body" || kind === "track";
}

/** What has focus, as keysApply's kind. */
export function focusKind(node, { track, map }) {
  if (!node || node === document.body || node === document.documentElement) return "body";
  if (track && track.contains(node)) return "track";
  if (node.closest && node.closest(".popover")) return "popover";
  if (map && map.contains(node)) return "map";
  const tag = node.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || node.isContentEditable) {
    return node.type === "checkbox" || node.type === "radio" ? "control" : "text";
  }
  return "control";
}

/** Runs of true in a bands array as [first, last] slot pairs. */
function runs(b) {
  const out = [];
  for (let s = 0; s < b.length; s++) {
    if (!b[s]) continue;
    const first = s;
    while (s + 1 < b.length && b[s + 1]) s++;
    out.push([first, s]);
  }
  return out;
}

const pct = (slots) => `${(slots / SLOTS) * 100}%`;

/** Build the bar inside `root`. hooks: select(day, slot), stepDay(dir), stepHour(dir),
 *  togglePlay(), dragStart(), dragEnd(). */
export function mountTimebar(root, hooks) {
  const btn = (cls, label, text) => el("button", { class: cls, text, attrs: { type: "button", "aria-label": label } });
  const play = btn("tb-play", "Play", "▶");
  const dayPrev = btn("tb-arrow", "Previous day", "‹");
  const dayNext = btn("tb-arrow", "Next day", "›");
  const dayText = el("span", { class: "tb-value tb-day" });
  const hourPrev = btn("tb-arrow", "An hour earlier", "‹");
  const hourNext = btn("tb-arrow", "An hour later", "›");
  const timeText = el("span", { class: "tb-value tb-time" });
  const ticks = el("div", { class: "tb-ticks", attrs: { "aria-hidden": "true" } });
  for (let h = 3; h < 24; h += 3) {
    const t = el("span", { class: "tb-tick" });
    t.style.left = pct(h * 4);
    ticks.append(t);
  }
  const bandBox = el("div", { class: "tb-bands", attrs: { "aria-hidden": "true" } });
  const head = el("div", { class: "tb-head", attrs: { "aria-hidden": "true" } });
  const track = el(
    "div",
    {
      class: "tb-track",
      attrs: {
        role: "slider",
        tabindex: "0",
        "aria-label": "Time of day",
        "aria-valuemin": "0",
        "aria-valuemax": String(SLOTS - 1),
      },
    },
    [ticks, bandBox, head],
  );
  root.replaceChildren(
    play,
    el("div", { class: "tb-step tb-step-day" }, [dayPrev, dayText, dayNext]),
    el("div", { class: "tb-step tb-step-time" }, [hourPrev, timeText, hourNext]),
    track,
  );

  let view = null;
  play.addEventListener("click", () => hooks.togglePlay());
  dayPrev.addEventListener("click", () => hooks.stepDay(-1));
  dayNext.addEventListener("click", () => hooks.stepDay(1));
  hourPrev.addEventListener("click", () => hooks.stepHour(-1));
  hourNext.addEventListener("click", () => hooks.stepHour(1));

  // Drag: the playhead follows the pointer freely, the map follows the slot under it, and the
  // release snaps to the nearest slot.
  let dragging = false;
  const width = () => track.getBoundingClientRect().width || 1;
  const xOf = (e) => Math.min(Math.max(e.clientX - track.getBoundingClientRect().left, 0), width());
  const follow = (e) => {
    const x = xOf(e);
    head.style.left = pct(Math.min((x / width()) * SLOTS, SLOTS - 1));
    const slot = slotAtX(x, width());
    if (view && slot !== view.slot) hooks.select(view.day, slot);
  };
  track.addEventListener("pointerdown", (e) => {
    if (!view || e.button !== 0) return;
    dragging = true;
    track.setPointerCapture(e.pointerId);
    hooks.dragStart();
    follow(e);
  });
  track.addEventListener("pointermove", (e) => dragging && follow(e));
  const release = (e) => {
    if (!dragging) return;
    dragging = false;
    if (view) hooks.select(view.day, nearestSlotAtX(xOf(e), width()));
    head.style.left = pct(view ? view.slot : 0);
    hooks.dragEnd();
  };
  track.addEventListener("pointerup", release);
  track.addEventListener("pointercancel", release);

  function setPlayhead(slot, frac = 0) {
    if (!dragging) head.style.left = pct(slot + frac);
  }

  return {
    track,
    render(v) {
      view = v;
      const i = v.days.indexOf(v.day);
      const date = dateLabel(v.day);
      dayText.textContent = dayLabel(v.day, v.today) === "Today" ? `Today, ${date}` : date;
      timeText.textContent = slotLabel(v.slot, v.clock);
      dayPrev.disabled = i <= 0;
      dayNext.disabled = i < 0 || i >= v.days.length - 1;
      hourPrev.disabled = stepSlot(v.days, v.day, v.slot, -4) === null;
      hourNext.disabled = stepSlot(v.days, v.day, v.slot, 4) === null;
      track.setAttribute("aria-valuenow", String(v.slot));
      track.setAttribute("aria-valuetext", `${date}, ${slotLabel(v.slot, v.clock)}`);
      if (v.bands) {
        bandBox.replaceChildren(
          ...runs(v.bands).map(([a, b]) => {
            const seg = el("span", { class: "tb-band" });
            seg.style.left = pct(a);
            seg.style.width = pct(b - a + 1);
            return seg;
          }),
        );
      }
      setPlayhead(v.slot, 0);
    },
    setPlayhead,
    setPlaying(on) {
      play.textContent = on ? "❚❚" : "▶";
      play.setAttribute("aria-label", on ? "Pause" : "Play");
    },
  };
}
