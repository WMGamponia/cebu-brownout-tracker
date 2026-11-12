// Fixed page copy (Phase 4 spec 4.6). Pure: plain strings, inserted with textContent only.

const KINDS = ["street", "sitio_purok", "subdivision", "establishment", "other"];

/** "A", "A and B", "A, B and C". */
export function joinAnd(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

function joinOr(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}

/** Why an outage's area isn't on the map, in words. */
export function reasonText(item) {
  switch (item.why) {
    case "other_town":
      if (!item.towns.length) return `VECO's town for ${item.name} has no barangay of that name`;
      return (
        `VECO listed ${item.name} under ${joinAnd(item.towns)}; ` +
        `no ${joinOr(item.towns)} barangay has that name`
      );
    case "unmatched":
      return `We couldn't match ${item.name} to a barangay`;
    case "not_parsed":
      return "Areas not processed yet";
    case "no_barangay":
      return "VECO named places but no barangay";
    default:
      return "Not on the map";
  }
}

/** "VECO lists Tayud under Consolacion and Liloan." when the name fits several towns. */
export function ambiguousText(name, town, ambiguousWith) {
  if (!ambiguousWith.length) return null;
  return `VECO lists ${name} under ${joinAnd([town, ...ambiguousWith].sort())}.`;
}

/** "N outages no longer in VECO's post", or null for none. */
export function removedText(n) {
  if (!n) return null;
  return `${n} ${n === 1 ? "outage" : "outages"} no longer in VECO's post`;
}

/** Streets, sitios and places in VECO's order, the first `limit` of them and how many more. */
export function entityList(entities, limit) {
  const all = KINDS.flatMap((k) => entities[k] || []);
  return { shown: all.slice(0, limit), more: Math.max(0, all.length - limit) };
}
