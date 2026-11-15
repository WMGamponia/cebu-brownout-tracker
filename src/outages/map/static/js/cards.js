// The map page's floating title card and notices (UI refresh spec 5.1). `notices` is pure; the
// DOM part renders them into the notices stack, which a folded title card never hides.
import { el } from "./dom.js";
import { STALE_AFTER_H, checkAgeHours, historyLabel } from "./timeline.js";
import { isPastWindow } from "./streets.js";

export const STALE_TEXT = "VECO updated this post and we couldn't read the update; this is from the earlier version.";

/** The data notices for a document, in order: history week, old data, stale post. Each is
 *  {id, kind: "info" | "warn", text}. Snapshot, missing-week and load-error notices are fixed
 *  elements in the page. */
export function notices(meta, nowMs, clock) {
  const out = [];
  const past = isPastWindow(meta, nowMs);
  if (past) {
    out.push({ id: "history", kind: "info", text: historyLabel(meta.days) });
  } else {
    const age = checkAgeHours(meta.last_check_at, nowMs);
    if (age === null) {
      out.push({
        id: "old",
        kind: "warn",
        text: "No data check has run yet, so this map may be empty or out of date.",
      });
    } else if (age > STALE_AFTER_H) {
      out.push({
        id: "old",
        kind: "warn",
        text:
          `This data may be out of date: the last check was ${Math.floor(age)} hours ago ` +
          "(checks run at 06:00 and 20:00).",
      });
    }
  }
  if (meta.stale_posts && meta.stale_posts.length) out.push({ id: "stale", kind: "warn", text: STALE_TEXT });
  return out;
}

/** Render `list` into `box` (the data notices, before the page's fixed notice elements).
 *  `extras[id]` are nodes appended to that notice (e.g. the stale posts' links). */
export function renderNotices(box, list, extras = {}) {
  for (const old of box.querySelectorAll(".notice-data")) old.remove();
  const nodes = list.map((n) => {
    const p = el("p", {
      class: `notice notice-data ${n.kind === "warn" ? "notice-warn" : ""}`.trim(),
      text: n.text,
      attrs: { "data-notice": n.id },
    });
    for (const x of extras[n.id] || []) p.append(" ", x);
    return p;
  });
  box.prepend(...nodes);
}

/** The ▾ button folds the title card to its name. Starts folded under 600 px. */
export function mountTitleFold(card, button) {
  const set = (folded) => {
    card.classList.toggle("folded", folded);
    button.setAttribute("aria-expanded", String(!folded));
    button.setAttribute("aria-label", folded ? "Show details" : "Hide details");
  };
  set(window.matchMedia("(max-width: 599px)").matches);
  button.addEventListener("click", () => set(!card.classList.contains("folded")));
}
