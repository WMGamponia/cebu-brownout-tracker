// The one place the map tiles come from (Phase 6 spec 4.3), under the OSMF tile usage policy:
// normal interactive viewing, visible attribution, no prefetch, no restrictive Referrer-Policy.

export const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const TILE_ATTRIBUTION = "© OpenStreetMap contributors";

/** Add the OSM tile layer to `map`; a failed tile is silent (polygons still draw on a blank
 *  background when offline or if access is withdrawn). */
export function addTiles(L, map) {
  const tiles = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTRIBUTION });
  tiles.on("tileerror", () => {});
  tiles.addTo(map);
  return tiles;
}
