// Small DOM helpers shared by the map page's modules. Data reaches the DOM only via textContent
// and setAttribute (never HTML strings).

export const $ = (id) => document.getElementById(id);

/** A DOM element with text set via textContent and plain attributes. */
export function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.class) node.className = opts.class;
  if (opts.text != null) node.textContent = opts.text;
  for (const [k, v] of Object.entries(opts.attrs || {})) node.setAttribute(k, v);
  for (const c of children) if (c) node.append(c);
  return node;
}
