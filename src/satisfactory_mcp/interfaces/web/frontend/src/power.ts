/* The power network: the wires, and the poles they are strung between.
 *
 * The third network on this map, and the first one whose geometry the projection could not
 * carry until schema 17 -- the save has recorded who is wired to whom since schema 11 and said
 * nothing about where any of it ran, so a base's power was a number in the header and nothing
 * on the map at all.
 *
 * Its own file rather than a third case inside `routes.ts`, because almost nothing is shared
 * at the drawing level: a wire has no spline, no tier, no direction and no curve to
 * tessellate, and a pole is a mark rather than a route. What IS shared is the three passes
 * `routes.ts` runs by name over ROUTE_LAYERS -- the hairline floor on zoom, the sink that
 * keeps a line from stealing a machine's click -- and `power` is in that list, so this file
 * imports `sinkRoutes` and declares no pass of its own.
 *
 * A WIRE IS DRAWN AS A STRAIGHT CHORD, and in game it is a catenary: it sags. Nothing in the
 * save records the sag -- `mCachedLength` is the same straight chord between the two endpoints,
 * so the file has no more to say about the shape than this does -- and seen from directly
 * above, a catenary and its chord are the same line anyway. The only thing lost is the height
 * along it, which a top-down map does not draw. Stated rather than left as an approximation
 * nobody declared.
 *
 * NO DIRECTION MARKS, and unlike the pipes this is not a refusal to guess: power has no
 * direction to draw. A circuit is one connected pool of supply and demand, and a wire between
 * two poles carries whatever the pool needs in whichever direction it needs it. The chevrons
 * `routes.ts` puts on a pipe would be inventing a flow the game does not model.
 */

import { popup } from "./dom";
import { L } from "./leaflet";
import { layer } from "./layers";
import { pixelsPerMetre } from "./map";
import { ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./routes";

import type { Row } from "./dom";

import type { PoleRow, PowerResponse, WireRow } from "./api-types";

/* Slate violet, and picked by measuring in CIE Lab against every colour already on the page,
 * the way the pipe rust and the storage magenta were.
 *
 * A wire has to separate from three things: the ground it crosses for kilometres at a time,
 * the belts it runs beside inside a factory, and the pipes on the other side of them. The
 * violet quarter is what the page had left -- blue is the machines, amber the extractors, red
 * the generators, magenta the storage, steel the belts and rust the pipes -- and it is the
 * best of the free hues on the comparison that matters most: at dE 34.8 from its nearest biome
 * tint it is further from the GROUND than any of the greens and khakis measured beside it,
 * which sat at 25 to 29 and would have read as scrub wherever a line crossed open country.
 *
 * Nearest neighbour anywhere on the page is the water node dot at dE 23.3 -- a filled disc on
 * open terrain against a hairline -- and the nearest belt tone is dE 24.2, which is the
 * separation that has to hold because a wire and a belt genuinely do run side by side. Both
 * are comfortably past the dE 22 the pipe rust was accepted at and nowhere near the dE 4.5
 * that disqualified a brighter pipe. The pipes themselves are dE 58 away and could not be
 * confused with this at any size. */
var WIRE_COLOUR = "#7d76a8";

/* The poles, one value step up the same hue -- the grammar the belts, the pipes and the
 * storage boxes all use for a distinction inside one family. Here the distinction is not a
 * tier but a KIND: the line and the thing the line ends at.
 *
 * The step is the house step, dE 16.2, against the belts' 15.6 between their slowest and
 * fastest, the pipes' 15.7 between Mk1 and Mk2 and the storage pair's 16.7. Lighter rather
 * than darker on purpose: a pole sits on top of the four wires that meet at it, so it has to
 * read against its own layer before it reads against the ground. Re-measured rather than
 * assumed safe, because a ramp can walk a colour into a neighbour -- this one moves away from
 * the ground (dE 45.6, up from 34.8) and its nearest colour on the page is the hard-drive
 * pickup dot at dE 19.9. */
var POLE_COLOUR = "#a8a0d2";

/* How big a pole's disc is, in PIXELS, by what the pole is.
 *
 * Pixels and not metres, which is the opposite of every other placement on this map and is a
 * decision about what the mark MEANS. A machine is drawn at its measured footprint because the
 * question there is "does this fit"; a pole is drawn at a fixed size because the question is
 * "is there one here", which is the node dots' grammar (PURITY_RADIUS in palette.ts) and the
 * reason `_fixed` exists in routes.ts. A true-size pole would be a fifth of a pixel at the
 * world view and would make the mark disappear at exactly the zoom the layer is most useful.
 *
 * The steps are a MEASUREMENT and not a decoration. A pole's mark is how many wires it can
 * carry, which the reference save states outright: the busiest Mk1 on the world carries
 * exactly 4, the busiest Mk2 exactly 7 and the busiest Mk3 exactly 10, which are the game's
 * own three limits. So the ramp is 2.5 / 3.2 / 4 px -- bigger node, more wires -- and the wall
 * outlets sit under Mk1 at 2 px because a socket on a wall is the smallest thing here.
 *
 * A class this table has never heard of gets the Mk1 size rather than nothing: an unrecognised
 * pole is still a place where wires end. */
var POLE_RADIUS_PX: Record<string, number> = {
  Build_PowerPoleWall_C: 2,
  Build_PowerPoleWall_Mk2_C: 2,
  Build_PowerPoleWallDouble_Mk2_C: 2,
  Build_PowerPoleMk1_C: 2.5,
  Build_PowerPoleMk2_C: 3.2,
  Build_PowerPoleMk3_C: 4,
};
var POLE_FALLBACK_PX = 2.5;

/* A Power Tower is drawn as a RING and every other pole as a filled disc, and the shape is
 * doing the work a size could not.
 *
 * There are 137 of them against 564 poles, they carry the only wires on this world longer than
 * 300 m, and they are physically a different kind of object: a 12 m platform on a pylon, tall
 * enough that its connectors sit 24 m above its own base. Making it merely a bigger dot would
 * say "a pole with more connections", which is what the ramp above already means and is not
 * what a tower is. A ring says "structure, seen from above" -- the same thing the conveyor
 * lift's ring says in the belts layer, and for the same reason: an outline is what a thing you
 * could stand inside looks like from directly overhead. */
var TOWER_CLASS = "Build_PowerTowerPlatform_C";
var TOWER_RADIUS_PX = 5;
var TOWER_WEIGHT_PX = 1.5;

/* The wire's own opacity, matching the belts and pipes at 0.85. It is a hairline at world zoom
 * and a hairline reads as lighter than it is, so there is nothing to gain by going lower --
 * and the two networks it has to be told apart from are both drawn at this. */
var WIRE_OPACITY = 0.85;

function poleRadius(cls: string | null): number {
  if (cls === TOWER_CLASS) return TOWER_RADIUS_PX;
  return (cls && POLE_RADIUS_PX[cls]) || POLE_FALLBACK_PX;
}

/* The row that names the thing a reader just clicked. `routes.ts` has one of these and it is
 * deliberately not exported: this file's version has the same shape and the same reason (both
 * `name` and `cls` are nullable server-side, and a popup whose title row was dropped would
 * lose the word "pole" while keeping every coordinate under it), and sharing it would mean an
 * export from routes.ts that exists only for the sake of not repeating four lines. */
function titleRow(key: string, name: string | null, cls: string | null): Row {
  return [key, name || cls || "class not recorded in this projection"];
}

function polePopup(p: PoleRow): string {
  return popup([
    titleRow(p.cls === TOWER_CLASS ? "power tower" : "power pole", p.name, p.cls),
    // A count off the wiring graph, and 0 is one of its answers rather than a missing one:
    // two of this world's tower platforms were built and never strung to anything.
    [
      "connections",
      p.connections === 0 ? "none — nothing is wired to this" : String(p.connections),
    ],
    ["facing", p.yaw === null ? null : Math.round(p.yaw) + "°"],
    ["at", p.x_m + ", " + p.y_m + " m"],
    // The base's elevation, not the pole's height: the projection carries where the actor
    // stands, and the seven metres up to a Mk1's connector are the model's, not the save's.
    ["elevation", p.z_m + " m"],
  ]);
}

function wirePopup(w: WireRow): string {
  var unnamed = "not a building this projection names";
  return popup([
    /* Both ends on the title row, joined by a DASH and not an arrow. A wire has no from and
     * no to: a circuit is one pool of supply and demand and the current goes wherever the
     * pool needs it, so the arrow the pipe popup earns by inference would be a claim about
     * this network that the game does not make. The order is still meaningful -- the server
     * measures which published endpoint belongs to which actor, because the save's own order
     * agrees with the wiring only about half the time -- so this pair lines up with the two
     * coordinates in `ends` below. */
    ["power line", (w.from || unnamed) + " — " + (w.to || unnamed)],
    // Said out loud, because a reader looking at a straight line over 300 m of terrain is
    // owed the difference between what is drawn and what hangs there.
    ["span", w.span_m + " m, straight line — a wire sags and the save records no sag"],
    ["ends", w.a_m[0] + ", " + w.a_m[1] + " m and " + w.b_m[0] + ", " + w.b_m[1] + " m"],
    // Unsigned, and for the reason the pipe popup gives an unsigned rise when it cannot name
    // a direction: a climb is only a climb once there is an end to measure it from.
    ["height difference", Math.abs(Math.round((w.b_m[2] - w.a_m[2]) * 10) / 10) + " m"],
  ]);
}

export function drawPower(data: PowerResponse): void {
  /* ON at the whole-world zoom, and it is the one network layer that is.
   *
   * `machines`, `belts` and `pipes` are all off there because they are a smear at that scale:
   * 438 rectangles and 3,588 routes over 7 km resolve into nothing you can read. The wires are
   * a different picture and the difference is measurable rather than a preference -- 1,297
   * lines against 3,588, and their MEDIAN span is 21 m against a belt piece's few metres, with
   * 1% of them over 291 m. So the layer is mostly long lines between distant places, which is
   * exactly what survives being drawn at 0.14 px to the metre: at world zoom it reads as the
   * spine joining this world's bases, which is a thing about the whole map and not about one
   * factory. Judged on a screenshot at that zoom, not on the counts alone.
   *
   * The poles are the half that does NOT survive it, and they are drawn anyway: 701 fixed
   * 2.5 px discs cluster into the same shape the wires already make, so they cost nothing
   * legible and they are the layer at factory zoom. Splitting them into a second checkbox
   * would be two rows for one idea. */
  var group = layer("power", true, WIRE_COLOUR);
  // The same expression the zoom pass restyles these with, off the same table -- see
  // routeWeight in routes.ts, which is exported for exactly this line.
  var weight = routeWeight(ROUTE_WIDTH_M.power!, pixelsPerMetre());

  data.wires.forEach(function (w) {
    L.polyline(
      [
        [-w.a_m[1], w.a_m[0]],
        [-w.b_m[1], w.b_m[0]],
      ],
      { color: WIRE_COLOUR, weight: weight, opacity: WIRE_OPACITY }
    )
      .bindPopup(wirePopup(w))
      .addTo(group);
  });

  data.poles.forEach(function (p) {
    var tower = p.cls === TOWER_CLASS;
    var piece = L.circleMarker([-p.y_m, p.x_m], {
      radius: poleRadius(p.cls),
      color: POLE_COLOUR,
      weight: tower ? TOWER_WEIGHT_PX : 1,
      // A ring for a tower and a disc for everything else; see TOWER_CLASS above.
      fillColor: POLE_COLOUR,
      fillOpacity: tower ? 0 : 0.9,
    });
    piece._fixed = true;
    piece.bindPopup(polePopup(p)).addTo(group);
  });

  sinkRoutes();
}
