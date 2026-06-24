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

/* How much of the base map shows through the region fill when BOTH are drawn.
 *
 * The two used to be alternatives -- a render arriving unticked the region box and that was
 * the end of it. The tint is an OVERLAY now and works over all four base-map modes, because
 * a photograph of the world is the better picture of the world but "which biome is this?"
 * answered OVER that picture rather than instead of it is a legitimate thing to want -- and
 * at REGION_FILL that answer is a wall of flat colour hiding what it annotates.
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

/* Applied on every layer change, because every path into "both are drawn" is one: a mode
 * switch, a mode's tiles failing back to plain, and the region box being ticked by hand.
 *
 * `state.imagery` rather than a question asked of tiles.ts: that module already imports this
 * one, so the arrow can only point this way, and what the blend needs is one boolean rather
 * than the mode's name -- plain and a mode whose picture is not on the map are the same
 * thing to a compositor.
 *
 * Guarded against its own no-ops rather than debounced. Drawing a world adds thousands of
 * layers to the map, each of which fires this, and the guard turns all but the two that
 * change anything into two property reads. */
var regionBlend = "";

export function updateRegionBlend() {
  var pane = map.getPane("regions");
  if (!pane) return;
  var want = state.imagery ? String(REGION_BLEND) : "";
  if (want === regionBlend) return;
  regionBlend = want;
  pane.style.opacity = want;
}

/* Whether the region tint is on: the mode's business until the player says otherwise.
 *
 * The old rule was an EVENT -- a render arriving unticked the box, once, and whatever
 * happened afterwards was whatever happened. With four modes that stops being expressible:
 * "arriving" happens on every switch, so the same heuristic would untick the box each time
 * the player looked at the terrain and back, quietly throwing away a choice they had made
 * in between.
 *
 * So it is stated as a rule about states instead of a reaction to a transition. OFF under
 * any imagery mode, because a real picture of the world is the better answer to "what is
 * here" and a page that opened with a biome wash over its own best picture would be hiding
 * it. ON under plain, because there is then nothing to hide and nothing to blend against --
 * which is the same map the old heuristic left you on, now said as a rule.
 *
 * And the rule stops applying the moment the player disagrees with it. One tick of that box
 * is a decision about this session, and every mode switch after it leaves the box alone: the
 * default is what the page does when it has not been told, not what it does instead of being
 * told. */
var chosen = false;

/* Programmatic ticks are not decisions, and there is no way to tell them apart afterwards:
 * Leaflet fires `overlayadd` from the LAYER's own add event, so `map.addLayer(group)` is
 * indistinguishable from a click by the time the event arrives. The flag is the same trick
 * `setSection` uses in layercontrol.ts for the same reason. */
var applying = false;

/* ...and nothing at all counts before the modes exist. `drawRegions` adds the group to the
 * map as it creates it, which fires `overlayadd` on a page where nobody has clicked
 * anything -- and that one event would otherwise be read as the player's word forever. */
var armed = false;

export function regionsUnderMode(imagery: boolean): void {
  armed = true;
  var group = state.layers["regions"];
  if (!group || chosen) return;
  var want = !imagery;
  if (map.hasLayer(group) === want) return;
  applying = true;
  try {
    if (want) group.addTo(map);
    else map.removeLayer(group);
  } finally {
    applying = false;
  }
}

/** Registered in main.ts with the rest of the map listeners, so the order it runs in is
 *  written down in one place rather than decided by the import graph. */
export function noteRegionChoice(event: L.LeafletEvent): void {
  if (!armed || applying) return;
  if ((event as L.LayersControlEvent).layer === state.layers["regions"]) chosen = true;
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
