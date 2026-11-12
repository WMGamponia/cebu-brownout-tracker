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

/** Clock text of {hour, minute}: "22:05" or, with fmt "12", "10:05 PM". Hour 24 is the
 *  midnight that ends a day: "24:00", or "12:00 AM". `compact` drops ":00" in 12-hour time. */
export function clockText(m, fmt = "24", compact = false) {
  if (fmt !== "12") return `${pad(m.hour)}:${pad(m.minute)}`;
  const h = m.hour % 12 || 12;
  const half = m.hour % 24 < 12 ? "AM" : "PM";
  return compact && m.minute === 0 ? `${h} ${half}` : `${h}:${pad(m.minute)} ${half}`;
}

/** "22:00–23:00", or with fmt "12" "10:00 PM–11:00 PM". */
export function hourLabel(h, fmt = "24") {
  return `${clockText({ hour: h, minute: 0 }, fmt)}–${clockText({ hour: h + 1, minute: 0 }, fmt)}`;
}

function dateLabel(dateStr) {
  const p = dayParts(dateStr);
  return `${p.dow} ${p.day} ${p.mon}`;
}

const MIDNIGHT = { hour: 24, minute: 0 };

/** True when `endMs` is exactly the 00:00 that ends start day `startDate`. */
const endsAtMidnight = (startDate, endMs) => {
  const e = manila(endMs);
  return e.hour === 0 && e.minute === 0 && manila(endMs - 1).date === startDate;
};

/** "Thu 12 Nov 08:00–17:00"; across midnight "Thu 12 Nov 22:00 – Fri 13 Nov 05:00".
 *  An end at exactly 00:00 the next day reads as 24:00 (12:00 AM) on the start day. */
export function rangeLabel(start, end, fmt = "24") {
  const clock = (m) => clockText(m, fmt);
  const s = manila(parseIso(start));
  const endMs = parseIso(end);
  const e = manila(endMs);
  if (s.date === e.date) return `${dateLabel(s.date)} ${clock(s)}–${clock(e)}`;
  if (endsAtMidnight(s.date, endMs)) return `${dateLabel(s.date)} ${clock(s)}–${clock(MIDNIGHT)}`;
  return `${dateLabel(s.date)} ${clock(s)} – ${dateLabel(e.date)} ${clock(e)}`;
}

/** Times only, for map labels: "22:00–06:00", or compact 12-hour "10 PM–6 AM". */
export function shortRange(start, end, fmt = "24") {
  const clock = (m) => clockText(m, fmt, true);
  const s = manila(parseIso(start));
  const endMs = parseIso(end);
  const e = endsAtMidnight(s.date, endMs) ? MIDNIGHT : manila(endMs);
  return `${clock(s)}–${clock(e)}`;
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

/** "Thu 12 Nov, 20:00" (or "8:00 PM") in Manila for any offset; null stays null. */
export function asOfLabel(iso, fmt = "24") {
  if (!iso) return null;
  const m = manila(parseIso(iso));
  return `${dateLabel(m.date)}, ${clockText(m, fmt)}`;
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
