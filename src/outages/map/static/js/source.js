// Where the pages get their data and their "now" (Phase 6 spec 4.3). Pure: no DOM, no browser
// zone. Live mode is the local server (`outages.geojson`, `stats.json`, the clock); snapshot mode
// is a static folder written by `python -m outages snapshot` (`weeks/<from>.geojson`,
// `stats/<id>.json`, time pinned to `taken_at`).
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
    refetch: true,
    ranges: [],
    weeks: [],
    now: () => Date.now(),
    mapUrl: (params) => withParams("outages.geojson", params, ["from", "days"]),
    statsUrl: (params) => withParams("stats.json", params, ["from", "to"]),
    weekStep: (from, dir) => shiftDate(from, 7 * dir),
    snapWeek: (date) => date,
    rangeId: () => null,
  };
}

function snapshotSource(snap) {
  const weeks = snap.weeks; // sorted, oldest first; the last is the snapshot week
  const ids = new Set(snap.ranges.map((r) => r.id));
  const takenMs = parseIso(snap.taken_at);

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
    refetch: false,
    ranges: snap.ranges,
    weeks,
    now: () => takenMs,
    mapUrl: (params) => `weeks/${snapWeek(params.get("from"))}.geojson`,
    statsUrl: (params) => `stats/${rangeId(params)}.json`,
    weekStep(from, dir) {
      const i = weeks.indexOf(snapWeek(from)) + dir;
      return i >= 0 && i < weeks.length ? weeks[i] : null;
    },
    snapWeek,
    rangeId,
  };
}

/** The data source for `mode` ("live" or "snapshot"); `snap` is the parsed snapshot.json. */
export function makeSource(mode, snap) {
  return mode === "snapshot" ? snapshotSource(snap) : liveSource();
}

/** "Thu 12 Nov 2026, 20:05" in Manila. */
export function snapshotLabel(iso) {
  const m = manila(parseIso(iso));
  const [y, mo, d] = m.date.split("-").map(Number);
  return `${DOW[m.dow]} ${d} ${MON[mo - 1]} ${y}, ${pad(m.hour)}:${pad(m.minute)}`;
}
