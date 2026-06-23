/* The biome raster: the ground everything else stands on, and how it shares the screen
 * with a real map render when there is one.
 *
 * Two things in one file because they are one decision. The cells are painted OPAQUE and the
 * transparency is applied once at the pane, and the reason -- 768 rectangles sharing borders
 * would blend twice at every seam and draw a 256 m grid -- is the same fact stated from both
 * ends. Splitting the fill from the blend would leave each half looking arbitrary.
 */

import { esc } from "./dom";
import { L } from "./leaflet";
import { layer } from "./layers";
import { map } from "./map";
import { REGION_COLOUR } from "./palette";
import { state } from "./state";

import type { RegionsResponse } from "./api-types";

var REGION_FILL = 1; // see REGION_COLOUR in palette.ts: opaque cells, or the shared borders become a grid.

/* How much of the map render shows through the region fill when BOTH are drawn.
 *
 * The two used to be alternatives -- a render arriving unticked the region box (see
 * baseImageryShown in tiles.ts) and that was the end of it. It still does, because a photograph of the
 * world is the better picture of the world; but ticking the box back on is now a legitimate
 * thing to want -- "which biome is this?" answered OVER the artwork rather than instead of
 * it -- and at REGION_FILL that answer is a wall of flat colour hiding what it annotates.
 *
 * The alpha goes on the PANE, not on the cells, and that distinction is the whole fix. A
 * per-cell fillOpacity of 0.45 blends 768 rectangles against the picture ONE AT A TIME, and
 * every shared border -- where a cell's antialiased edge and its neighbour's overlap -- is
 * blended twice. That is a visible 256 m grid, which is exactly why the cells are opaque and
 * their strokes are their own fill colour. Painted opaque into the pane's own canvas they
 * still merge into one shape; the browser then composites that finished shape over the tiles
 * once, at this alpha, and a border is no different from a cell interior.
 *
 * 0.45 by eye over both extremes of the render -- the pale sand of the Dune Desert, where a
 * heavier fill turns the dunes to mud, and the near-black canopy of the Northern Forest,
 * where a lighter one leaves the region tint invisible. Region NAMES are unaffected: they
 * are tooltips, and tooltips live in Leaflet's tooltipPane. */
var REGION_BLEND = 0.45;

/* Whether a map render is actually on screen right now: the layer exists, has something in
 * it, and its box is ticked. All three matter -- the group is created empty by the probe
 * and the box is the player's to untick. */
function baseImageryOn() {
  var group = state.layers["map image"];
  return !!(group && map.hasLayer(group) && group.getLayers().length);
}

/* Applied on every layer change, because every path into "both are drawn" is one: the
 * render finishing, the render failing back, and either box being ticked by hand.
 *
 * Guarded against its own no-ops rather than debounced. Drawing a world adds thousands of
 * layers to the map, each of which fires this, and the guard turns all but the two that
 * change anything into three property reads. */
var regionBlend = "";

export function updateRegionBlend() {
  var pane = map.getPane("regions");
  if (!pane) return;
  var want = baseImageryOn() ? String(REGION_BLEND) : "";
  if (want === regionBlend) return;
  regionBlend = want;
  pane.style.opacity = want;
}

/* The base map: one flat rectangle per 256 m raster cell, plus a name per region.
 *
 * Orientation is the whole trap here and the API's docstring spells it out: grid row 0 is
 * the NORTH edge because y0_m is the smallest y and game +Y is south. Cell (i, j) spans
 * y in [y, y+cell], which is latitude [-(y+cell), -y] once the page's [-y, x] convention
 * is applied -- so the y bounds swap, and only here. Void cells are left unpainted: the
 * sea colour showing through them is the coastline.
 *
 * Names print at `label_m`, not the centroid: a concave region's centroid can sit on a
 * neighbour's ground, and a name printed there contradicts the same page's right-click
 * inspector. The server moves those anchors onto the region's own cells.
 */
export function drawRegions(data: RegionsResponse): void {
  // "regions" and "region names": one row for the fill, one for the labels over it, named
  // as the pair they are. The fill was called "terrain" until there was a real terrain
  // render to be confused with -- what it draws is biome regions, and always was.
  var regions = layer("regions", true);
  var names = layer("region names", true);
  var cell = data.cell_m;
  data.grid.forEach(function (row, j) {
    for (var i = 0; i < row.length; i++) {
      var letter = row.charAt(i);
      if (letter === ".") continue;
      var colour = REGION_COLOUR[letter] || "#3f4640";
      var x = data.x0_m + i * cell;
      var y = data.y0_m + j * cell;
      L.rectangle(
        [
          [-(y + cell), x],
          [-y, x + cell],
        ],
        {
          // Stroked in its own fill colour so neighbouring cells of one biome merge into
          // a shape instead of showing a grid; interactive:false so the region fill never
          // eats a click meant for a node sitting on top of it.
          color: colour,
          weight: 1,
          opacity: REGION_FILL,
          fillColor: colour,
          fillOpacity: REGION_FILL,
          interactive: false,
          pane: "regions",
        }
      ).addTo(regions);
    }
  });

  Object.keys(data.regions).forEach(function (name) {
    // A standalone tooltip, not a zero-opacity marker: a marker would drag Leaflet's
    // default icon (and its two image requests) into the page for a label that is meant
    // to be text and nothing else.
    var here = data.regions[name]!;
    var at = here.label_m || here.centroid_m;
    L.tooltip({ permanent: true, direction: "center", className: "region-label" })
      .setLatLng([-at[1], at[0]])
      .setContent(esc(name))
      .addTo(names);
  });
}
