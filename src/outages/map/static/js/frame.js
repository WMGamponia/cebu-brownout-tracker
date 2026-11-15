// Playback position and change tracking for the map page (UI refresh spec 5.2).
// Pure: no DOM, no clock reads; the caller passes elapsed time.
import { SLOTS, slotOf, stepSlot } from "./timeline.js";

export const MS_PER_SLOT = 250; // one hour of map time a second

/** Where playback started at {startDay, startSlot} is after `elapsedMs`: the slot and how far
 *  into it (frac, 0–1) for the gliding playhead; null once past the window's last slot. */
export function playhead(days, startDay, startSlot, elapsedMs) {
  const steps = Math.floor(elapsedMs / MS_PER_SLOT);
  const at = stepSlot(days, startDay, startSlot, steps);
  if (!at) return null;
  return { ...at, frac: (elapsedMs - steps * MS_PER_SLOT) / MS_PER_SLOT };
}

/** The current Manila slot while its date is in the window (the Now button follows it); null
 *  once the clock has left the window. */
export function followSlot(nowMs, days) {
  const at = slotOf(nowMs);
  return days.includes(at.date) ? { day: at.date, slot: at.slot } : null;
}

/** Keys whose value differs between two maps, including keys in only one of them. */
export function changedKeys(prev, next) {
  const out = [];
  for (const [k, v] of next) if (prev.get(k) !== v || !prev.has(k)) out.push(k);
  for (const k of prev.keys()) if (!next.has(k)) out.push(k);
  return out;
}

/** True when more than 2 frame intervals exceed 50 ms: the device can't afford the fades. */
export function dropFades(intervalsMs) {
  return intervalsMs.filter((ms) => ms > 50).length > 2;
}

export { SLOTS };
