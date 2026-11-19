// Where the pages get their data and their "now" (Phase 6 spec 4.3). Pure: no DOM, no browser
// zone. Live mode is the local server (`outages.geojson`, `stats.json`, the clock); snapshot mode
// is a static folder written by `python -m outages snapshot` (`<data>weeks/<from>.geojson`,
// `<data>stats/<id>.json`, `<data>` from snapshot.json). Its clock is the real one while the data
// is fresh, else pinned to `taken_at` (live-site spec 5.5).
import { manila, parseIso, shiftDate } from "./timeline.js";

const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const pad = (n) => String(n).padStart(2, "0");

function withParams(base, params, keys) {
  const q = new URLSearchParams();
  for (const k of keys) if (params.get(k)) q.set(k, params.get(k));
  return q.toString() ? `${base}?${q}` : base;
}

function liveSource() {
  return {
    isSnapshot: false,
    snap: null,
    refetch: true,
    ranges: [],
    weeks: [],
    fresh: true,
    now: () => Date.now(),
    boundariesUrl: () => "boundaries.geojson",
    mapUrl: (params) => withParams("outages.geojson", params, ["from", "days"]),
    statsUrl: (params) => withParams("stats.json", params, ["from", "to"]),
    weekStep: (from, dir) => shiftDate(from, 7 * dir),
    snapWeek: (date) => date,
    rangeId: () => null,
  };
}

/** Whether `snap`'s data is recent enough for the real clock: `last_check_at` at most
 *  `fresh_for_h` hours before `nowMs` (a visitor's clock behind it counts as fresh). */
export function isFresh(snap, nowMs) {
  const checked = parseIso(snap.last_check_at);
  const hours = snap.fresh_for_h;
  if (!Number.isFinite(checked) || typeof hours !== "number") return false;
  return nowMs - checked <= hours * 3600 * 1000;
}

function snapshotSource(snap, clock) {
  const weeks = snap.weeks; // sorted, oldest first; the last is the snapshot week
  const ids = new Set(snap.ranges.map((r) => r.id));
  const takenMs = parseIso(snap.taken_at);
  const data = snap.data ?? "";
  const fresh = isFresh(snap, clock()); // decided once, at load

  /** The stored week holding `date`: the last week starting on or before it. Dates before the
   *  first week get the first; a missing or malformed date gets the snapshot week. */
  function snapWeek(date) {
    if (typeof date !== "string" || !ISO_DATE.test(date)) return snap.snapshot_week;
    let found = weeks[0];
    for (const w of weeks) if (w <= date) found = w;
    return found;
  }

  function rangeId(params) {
    const id = params.get("range");
    return ids.has(id) ? id : snap.default_range;
  }

  return {
    isSnapshot: true,
    snap,
    refetch: false,
    ranges: snap.ranges,
    weeks,
    fresh,
    now: fresh ? clock : () => takenMs,
    boundariesUrl: () => `${data}boundaries.geojson`,
    mapUrl: (params) => `${data}weeks/${snapWeek(params.get("from"))}.geojson`,
    statsUrl: (params) => `${data}stats/${rangeId(params)}.json`,
    weekStep(from, dir) {
      const i = weeks.indexOf(snapWeek(from)) + dir;
      return i >= 0 && i < weeks.length ? weeks[i] : null;
    },
    snapWeek,
    rangeId,
  };
}

/** The data source for `mode` ("live" or "snapshot"); `snap` is the parsed snapshot.json,
 *  `clock` the epoch-ms clock (tests pass their own). */
export function makeSource(mode, snap, clock = Date.now) {
  return mode === "snapshot" ? snapshotSource(snap, clock) : liveSource();
}

/** Whether the page may refetch its data: never on a snapshot page (`mode` is the page's
 *  <html data-mode>), including while snapshot.json is still loading and `src` is the live
 *  placeholder; otherwise as the source says. */
export function refetchAllowed(mode, src) {
  return mode !== "snapshot" && src.refetch;
}

/** "Thu 12 Nov 2026, 20:05" in Manila. */
export function snapshotLabel(iso) {
  const m = manila(parseIso(iso));
  const [y, mo, d] = m.date.split("-").map(Number);
  return `${DOW[m.dow]} ${d} ${MON[mo - 1]} ${y}, ${pad(m.hour)}:${pad(m.minute)}`;
}

/** The snapshot page's banner and credits text: `banner` (null when fresh) is followed by a link
 *  to VECO's advisories and "."; `credits` by the VECO link when fresh, then the code and
 *  licence links. Labelled by the last check, else by `taken_at`. */
export function snapshotCopy(snap, fresh) {
  const label = snapshotLabel(snap.last_check_at || snap.taken_at);
  if (fresh) return { banner: null, credits: `Updated ${label} (Manila) from ` };
  return {
    banner: `Last updated ${label} (Manila) and may be out of date. For current schedules see `,
    credits: `Last updated ${label}; `,
  };
}
