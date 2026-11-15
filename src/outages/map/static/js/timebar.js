// The floating timeline bar (UI refresh spec 5.2): play, ‹ day ›, ‹ time ›, a 24-hour track with
// the day's outage bands and a gliding playhead. Pure helpers first; the DOM part only renders
// and reports input through hooks (map.js owns the selection and the play loop).
import { el } from "./dom.js";
import { SLOTS, activeInSlot, dateLabel, dayLabel, onDay, slotLabel, stepSlot } from "./timeline.js";

/** The day's outages as track segments, packed into rows so overlapping ones sit apart (greedy
 *  by start, then id). Each segment covers its first to last active slot of `day`. Outages that
 *  fit none of the `rows` go to `overflow` (their ids). */
export function packLanes(outages, day, showCancelled, rows = 3) {
  const items = [];
  for (const [id, o] of Object.entries(outages)) {
    if (o.status === "cancelled" && !showCancelled) continue;
    if (!onDay(o, day)) continue;
    let a = -1;
    let b = -1;
    for (let s = 0; s < SLOTS; s++) {
      if (!activeInSlot(o, day, s)) continue;
      if (a < 0) a = s;
      b = s;
    }
    if (a >= 0) items.push({ id, a, b, cancelled: o.status === "cancelled" });
  }
  items.sort((x, y) => x.a - y.a || Number(x.id) - Number(y.id));
  const ends = new Array(rows).fill(-1); // last slot used in each row
  const segments = [];
  const overflow = [];
  for (const it of items) {
    const row = ends.findIndex((end) => end < it.a);
    if (row < 0) {
      overflow.push(it.id);
      continue;
    }
    ends[row] = it.b;
    segments.push({ ...it, row });
  }
  return { segments, overflow };
}

/** "10:30 AM · Binaliw, Pit-os": the time at `slot`, then the barangays of the segments covering
 *  it (at most 3 names, then "+n"). `namesById` maps an outage id to its barangay names. */
export function hoverText(slot, segments, namesById, clock = "24") {
  const names = [];
  for (const seg of segments) {
    if (slot < seg.a || slot > seg.b) continue;
    for (const n of namesById[seg.id] || []) if (!names.includes(n)) names.push(n);
  }
  const time = slotLabel(slot, clock);
  if (!names.length) return time;
  const more = names.length - 3;
  return `${time} · ${names.slice(0, 3).join(", ")}${more > 0 ? ` +${more}` : ""}`;
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

/** Whether a time key (keyStep's result) applies with focus of this kind: everything on the
 *  page body or the track; arrows also on the bar's buttons (Space presses the button itself);
 *  nothing on the map (it pans with its arrows), other controls, text fields or menus. */
export function keysApply(kind, step) {
  if (kind === "body" || kind === "track") return true;
  return kind === "bar" && step !== "toggle";
}

/** What has focus, as keysApply's kind. */
export function focusKind(node, { track, bar, map }) {
  if (!node || node === document.body || node === document.documentElement) return "body";
  if (track && track.contains(node)) return "track";
  if (bar && bar.contains(node)) return "bar";
  if (node.closest && node.closest(".popover")) return "popover";
  if (map && map.contains(node)) return "map";
  const tag = node.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA" || node.isContentEditable) {
    return node.type === "checkbox" || node.type === "radio" ? "control" : "text";
  }
  return "control";
}

const pct = (slots) => `${(slots / SLOTS) * 100}%`;

/** Build the bar inside `root`. hooks: select(day, slot), stepDay(dir), stepHour(dir),
 *  togglePlay(), now(), dragStart(), dragEnd(). */
export function mountTimebar(root, hooks) {
  const btn = (cls, label, text) => el("button", { class: cls, text, attrs: { type: "button", "aria-label": label } });
  const play = btn("tb-play", "Play", "▶");
  const now = btn("tb-now", "Follow the current time", "Now");
  now.setAttribute("aria-pressed", "false");
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
  const more = el("span", { class: "tb-more", attrs: { "aria-hidden": "true" } });
  const tip = el("div", { class: "tb-tip", attrs: { "aria-hidden": "true" } });
  tip.hidden = true;
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
    [ticks, bandBox, more, head, tip],
  );
  root.replaceChildren(
    play,
    now,
    el("div", { class: "tb-step tb-step-day" }, [dayPrev, dayText, dayNext]),
    el("div", { class: "tb-step tb-step-time" }, [hourPrev, timeText, hourNext]),
    track,
  );

  let view = null;
  play.addEventListener("click", () => hooks.togglePlay());
  now.addEventListener("click", () => hooks.now());
  dayPrev.addEventListener("click", () => hooks.stepDay(-1));
  dayNext.addEventListener("click", () => hooks.stepDay(1));
  hourPrev.addEventListener("click", () => hooks.stepHour(-1));
  hourNext.addEventListener("click", () => hooks.stepHour(1));

  // Drag: the playhead follows the pointer freely, the map follows the slot under it, and the
  // release snaps to the nearest slot.
  let dragging = false;
  const width = () => track.getBoundingClientRect().width || 1;
  const xOf = (e) => Math.min(Math.max(e.clientX - track.getBoundingClientRect().left, 0), width());
  /** The label above the cursor: the time there and the barangays out then. */
  const showTip = (e) => {
    if (!view || !view.lanes) return;
    const x = xOf(e);
    tip.textContent = hoverText(slotAtX(x, width()), view.lanes.segments, view.names || {}, view.clock);
    tip.style.left = `${x}px`;
    tip.hidden = false;
  };
  track.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse" || dragging) showTip(e);
  });
  track.addEventListener("pointerleave", () => {
    if (!dragging) tip.hidden = true;
  });
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
    if (e.pointerType !== "mouse") tip.hidden = true;
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
      // A slot render passes no lanes: keep the day's lanes, names and selection for the label.
      const fresh = Boolean(v.lanes);
      view = v.lanes || !view ? v : { ...v, lanes: view.lanes, names: view.names, selected: view.selected };
      v = view;
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
      if (fresh) {
        const sel = v.selected || new Set();
        bandBox.replaceChildren(
          ...v.lanes.segments.map((s) => {
            const dim = sel.size && !sel.has(s.id);
            const seg = el("span", {
              class: `tb-band row-${s.row}${s.cancelled ? " cancelled" : ""}${dim ? " dim" : ""}`,
            });
            seg.style.left = pct(s.a);
            seg.style.width = pct(s.b - s.a + 1);
            return seg;
          }),
        );
        const n = v.lanes.overflow.length;
        more.hidden = !n;
        more.textContent = n ? `+${n}` : "";
        const names = v.lanes.overflow.flatMap((id) => (v.names || {})[id] || []);
        if (n) more.setAttribute("title", `Also out this day: ${names.join(", ")}`);
      }
      setPlayhead(v.slot, 0);
    },
    setPlayhead,
    /** The Now button shows whether the selection follows the clock. */
    setFollowing(on) {
      now.classList.toggle("active", on);
      now.setAttribute("aria-pressed", String(on));
    },
    setPlaying(on) {
      play.textContent = on ? "❚❚" : "▶";
      play.setAttribute("aria-label", on ? "Pause" : "Play");
    },
  };
}
