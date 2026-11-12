// The history page's pure helpers (Phase 5 spec 4.8): quantile bins, legend text, month bars.
// No DOM, no browser zone.

/** Upper edges of `n` bins over the non-zero values, by nearest-rank quantiles; ties collapse,
 *  so there may be fewer than `n`. All zero (or empty) gives []. */
export function quantileEdges(values, n = 5) {
  const v = values.filter((x) => x > 0).sort((a, b) => a - b);
  if (!v.length) return [];
  const edges = [];
  for (let k = 1; k <= n; k++) {
    const e = v[Math.ceil((k * v.length) / n) - 1];
    if (!edges.length || e > edges[edges.length - 1]) edges.push(e);
  }
  return edges;
}

/** 0 for zero hours (outline only), else the 1-based bin; above the top edge is the top bin. */
export function binOf(value, edges) {
  if (!(value > 0) || !edges.length) return 0;
  const i = edges.findIndex((e) => value <= e);
  return i === -1 ? edges.length : i + 1;
}

const fmt = (x) => String(Number(x.toFixed(1)));

/** "up to 20 h", "20–40 h", …: the legend with real hour values. */
export function legendLabels(edges) {
  return edges.map((e, i) => (i === 0 ? `up to ${fmt(e)} h` : `${fmt(edges[i - 1])}–${fmt(e)} h`));
}

/** SVG geometry for one bar per month: equal slots over `width`, bars 80 % of a slot, heights
 *  scaled to the largest month. */
export function monthBars(months, width, height) {
  if (!months.length) return [];
  const slot = width / months.length;
  const max = Math.max(...months.map((m) => m.hours));
  return months.map((m, i) => {
    const h = max > 0 ? (m.hours / max) * height : 0;
    return { x: i * slot + slot * 0.1, y: height - h, w: slot * 0.8, h, label: m.month };
  });
}
