/* What the player built and where it stands: the floor plan, and the machines on it.
 *
 * Together because they are the same drawing problem -- a rotated footprint at a measured
 * size -- and because both fall back to a stated stand-in when the docs dump carries no
 * clearance for a class. The difference is only where the size comes from: a foundation
 * takes the grid's own tile, a machine takes its own w_m and l_m.
 */

import { code, popup } from "./dom";
import { L } from "./leaflet";
import { layer } from "./layers";
import { footprintCorners } from "./map";
import { raiseNodeDots } from "./markers";
import { KIND_COLOUR } from "./palette";

/* The player's floor plan: one 8 m tile per placed foundation, ramp, wall or catwalk.
 *
 *   * Drawn at its real yaw, as of schema 12. Until then the instance quaternion was
 *     dropped at extraction and every tile was axis-aligned, so a slab the player laid at
 *     an angle -- this world has several, over 1,800 pieces of them -- came out as a
 *     staircase of squares. Polygons rather than rectangles is the whole change: an
 *     L.rectangle IS a polygon built from two corners, so this costs four points per piece
 *     and nothing else.
 *   * No per-class size. None of these eighteen classes has clearance data, so there is
 *     no footprint to ask for. They all snap to the same grid, whose edge the server
 *     reports as `tile_m`, so a wall paints the tile it stands on rather than its own
 *     thin volume -- it straddles two tiles and fringes a walled platform by half a tile,
 *     which at any zoom where the platform is legible is not visible.
 *
 * Stroked in its own fill colour, the trick the biome cells already use: no stroke at all
 * leaves hairline seams between neighbouring tiles at low zoom, and a stroke in any other
 * colour draws an 8 m grid. Same colour, weight 1, and a slab reads as one platform.
 */
var STRUCTURE_COLOUR = "#3a4148"; // concrete, cool enough to read as built against the biomes.

export function drawStructures(data) {
  var group = layer("foundations", true, STRUCTURE_COLOUR);
  var half = (data.tile_m || 8) / 2;
  data.structures.forEach(function (s) {
    if (s.x_m === null || s.y_m === null) return;
    L.polygon(footprintCorners(s.x_m, s.y_m, half, half, s.yaw), {
      color: STRUCTURE_COLOUR,
      weight: 1,
      opacity: 0.9,
      fillColor: STRUCTURE_COLOUR,
      fillOpacity: 0.9,
      interactive: false,
      pane: "foundations",
    }).addTo(group);
  });
}

/* Machines at their real size AND their real facing: `w_m`/`l_m` are the building's own
 * footprint, so a Manufacturer (18x20 m) reads as the eight-times-larger thing it is next
 * to a Constructor (8x10 m), and `yaw` turns that footprint the way the player placed it.
 * Null footprints for the classes the docs dump gives no clearance data -- both biomass
 * burners here -- and those fall back to the 6 m square every machine used to get, which
 * rotates to itself. A null yaw draws axis-aligned; see footprintCorners in map.ts for why that is
 * not the same statement as a yaw of zero. */
var MACHINE_FALLBACK_M = 6;

export function drawMachines(data) {
  ["machines", "extractors", "generators"].forEach(function (kind) {
    var group = layer(kind, kind !== "machines", KIND_COLOUR[kind]);
    data[kind].forEach(function (m) {
      if (m.x_m === null) return;
      var w = (m.w_m || MACHINE_FALLBACK_M) / 2;
      var l = (m.l_m || MACHINE_FALLBACK_M) / 2;
      L.polygon(footprintCorners(m.x_m, m.y_m, w, l, m.yaw), {
        color: KIND_COLOUR[kind],
        weight: 1,
        fillOpacity: m.paused ? 0.15 : 0.65,
        dashArray: m.paused ? "2,2" : null,
      })
        .bindPopup(
          popup([
            ["building", m.name],
            ["recipe", m.recipe_name || m.recipe],
            ["clock", m.clock === null ? null : Math.round(m.clock * 100) + "%"],
            ["paused", m.paused ? "yes" : null],
            ["footprint", m.w_m && m.l_m ? m.w_m + " x " + m.l_m + " m" : null],
            // Degrees about world Z, positive turning +X towards +Y -- the same number
            // the drawing is turned by, so a reader can check the picture against it.
            // Absent, not "0", when the projection carries no facing at all.
            ["facing", m.yaw === null || m.yaw === undefined ? null : Math.round(m.yaw) + "°"],
            ["at", m.x_m + ", " + m.y_m + " m"],
            ["instance", code(m.instance_leaf)],
          ])
        )
        .addTo(group);
    });
  });
  raiseNodeDots();
}
