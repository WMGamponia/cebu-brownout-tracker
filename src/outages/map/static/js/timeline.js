// Manila time for the map page (Phase 4 spec 4.6). Pure: no DOM, no browser zone.
// Manila wall time = epoch + 8 h read with UTC getters. The Philippines has no DST, so a fixed
// offset is exact. Never read local-zone getters or locale formatting here.

export const STALE_AFTER_H = 15;

const OFFSET_MS = 8 * 3600 * 1000;
const HOUR_MS = 3600 * 1000;
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const VECO = "https://www.visayanelectric.com/";

const pad = (n) => String(n).padStart(2, "0");

/** Epoch ms of an ISO string with an offset (`2026-11-12T22:00:00+08:00`). */
export function parseIso(s) {
  return Date.parse(s);
}

/** Manila wall-clock parts of an epoch. */
export function manila(ms) {
  const d = new Date(ms + OFFSET_MS);
  return {
    date: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`,
    hour: d.getUTCHours(),
    minute: d.getUTCMinutes(),
    dow: d.getUTCDay(),
  };
}

/** Epoch ms of 00:00 Manila on `YYYY-MM-DD`. */
export function dayStartMs(dateStr) {
  return Date.parse(`${dateStr}T00:00:00+08:00`);
}

/** True when the outage overlaps hour `h` (0–23) of Manila day `dateStr`. */
export function activeInHour(o, dateStr, h) {
  const from = dayStartMs(dateStr) + h * HOUR_MS;
  return parseIso(o.start) < from + HOUR_MS && parseIso(o.end) > from;
}

/** True when the outage overlaps Manila day `dateStr`. */
export function onDay(o, dateStr) {
  const from = dayStartMs(dateStr);
  return parseIso(o.start) < from + 24 * HOUR_MS && parseIso(o.end) > from;
}

function dayParts(dateStr) {
  const d = new Date(dayStartMs(dateStr) + OFFSET_MS);
  return { dow: DOW[d.getUTCDay()], day: d.getUTCDate(), mon: MON[d.getUTCMonth()] };
}

/** "Today", else "Fri 13". */
export function dayLabel(dateStr, todayStr) {
  if (dateStr === todayStr) return "Today";
  const p = dayParts(dateStr);
  return `${p.dow} ${p.day}`;
}

/** "22:00–23:00". */
export function hourLabel(h) {
  return `${pad(h)}:00–${pad(h + 1)}:00`;
}

function dateLabel(dateStr) {
  const p = dayParts(dateStr);
  return `${p.dow} ${p.day} ${p.mon}`;
}

const clock = (m) => `${pad(m.hour)}:${pad(m.minute)}`;

/** "Thu 12 Nov 08:00–17:00"; across midnight "Thu 12 Nov 22:00 – Fri 13 Nov 05:00".
 *  An end at exactly 00:00 the next day reads as 24:00 on the start day. */
export function rangeLabel(start, end) {
  const s = manila(parseIso(start));
  const endMs = parseIso(end);
  const e = manila(endMs);
  if (s.date === e.date) return `${dateLabel(s.date)} ${clock(s)}–${clock(e)}`;
  if (e.hour === 0 && e.minute === 0 && manila(endMs - 1).date === s.date) {
    return `${dateLabel(s.date)} ${clock(s)}–24:00`;
  }
  return `${dateLabel(s.date)} ${clock(s)} – ${dateLabel(e.date)} ${clock(e)}`;
}

/** `[id, outage]` pairs by start time, then id. Object key order is numeric, not the server's. */
export function byStart(entries) {
  return [...entries].sort(
    ([a, oa], [b, ob]) => parseIso(oa.start) - parseIso(ob.start) || Number(a) - Number(b),
  );
}

export const REFETCH_AFTER_MS = 30 * 60 * 1000;

/** True when data fetched at `fetchedAtMs` is due for a refetch. */
export function needsRefetch(fetchedAtMs, nowMs) {
  return nowMs - fetchedAtMs > REFETCH_AFTER_MS;
}

/** "Thu 12 Nov, 20:00" in Manila for any offset; null stays null. */
export function asOfLabel(iso) {
  if (!iso) return null;
  const m = manila(parseIso(iso));
  return `${dateLabel(m.date)}, ${clock(m)}`;
}

/** Today's tab at the current Manila hour; the first day at 00:00 if today isn't shown. */
export function defaultSelection(nowMs, days) {
  const now = manila(nowMs);
  if (days.includes(now.date)) return { day: now.date, hour: now.hour };
  return { day: days[0], hour: 0 };
}

/** Hours since the last check, or null when there has never been one. */
export function checkAgeHours(lastCheckIso, nowMs) {
  if (!lastCheckIso) return null;
  return (nowMs - parseIso(lastCheckIso)) / HOUR_MS;
}

/** `YYYY-MM-DD` moved by `days` whole days (for the week picker). */
export function shiftDate(dateStr, days) {
  return manila(dayStartMs(dateStr) + days * 24 * HOUR_MS).date;
}

/** The Monday on or before Manila date `dateStr`. */
export function mondayOf(dateStr) {
  const dow = new Date(dayStartMs(dateStr) + OFFSET_MS).getUTCDay(); // 0 = Sunday
  return shiftDate(dateStr, -((dow + 6) % 7));
}

/** "History: 3–9 Nov 2025"; months or years named on both ends only when they differ. */
export function historyLabel(days) {
  const a = new Date(dayStartMs(days[0]) + OFFSET_MS);
  const b = new Date(dayStartMs(days[days.length - 1]) + OFFSET_MS);
  const [ad, am, ay] = [a.getUTCDate(), MON[a.getUTCMonth()], a.getUTCFullYear()];
  const [bd, bm, by] = [b.getUTCDate(), MON[b.getUTCMonth()], b.getUTCFullYear()];
  if (days.length === 1 || days[0] === days[days.length - 1]) return `History: ${ad} ${am} ${ay}`;
  if (ay !== by) return `History: ${ad} ${am} ${ay} – ${bd} ${bm} ${by}`;
  if (am !== bm) return `History: ${ad} ${am} – ${bd} ${bm} ${by}`;
  return `History: ${ad}–${bd} ${bm} ${by}`;
}

/** The URL if it is an https link on VECO's own site, else null. */
export function safePostUrl(url) {
  if (typeof url !== "string" || !url.startsWith(VECO)) return null;
  let u;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" || u.hostname !== "www.visayanelectric.com") return null;
  if (u.port || u.username || u.password) return null;
  return url;
}
