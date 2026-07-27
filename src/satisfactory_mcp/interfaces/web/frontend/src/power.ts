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
 * `routes.ts` runs by name over ROUTE_LAYERS -- the width restyle on zoom, the sink that
 * keeps a line from stealing a machine's click -- and `power` is in that list, so this file
 * imports `sinkRoutes` and declares no pass of its own. The one thing it no longer shares is
 * the FLOOR those passes size it against: see ROUTE_FLOOR_PX.
 *
 * IT IS ALSO THE ONE NETWORK FLOOR MODE CANNOT LOOK UP. `/api/floors` groups belt chains and
 * pipe rows and has never carried a wire, so `floors.ts` places this layer geometrically off
 * the endpoint heights below. That is why every piece drawn here carries a `_floor` mark and
 * why the fetch says `refilters: true`; both are stated at the bottom of this file.
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
import { BAND, layer } from "./layers";
import { pixelsPerMetre } from "./map";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";
import { ROUTE_FLOOR_PX, ROUTE_WIDTH_M, routeWeight, sinkRoutes } from "./routes";

import type { Row } from "./dom";

import type { Point3M, PoleRow, PowerResponse, WireRow } from "./api-types";

/* CASED LINES: a dark, wider casing under a lighter core, which is the oldest trick on a
 * printed map and is here because the honest version failed.
 *
 * The layer used to be one slate violet hairline, #7d76a8, chosen by measuring it against
 * every colour already on the page -- and every number in that warrant was true. It was dE
 * 23.3 from its nearest neighbour anywhere on the map, dE 24.2 from the nearest belt tone,
 * and dE 26.7 from the nearest tone of the actual artwork it is drawn over. On the evidence
 * the palette can see, that colour was fine. The owner's words for what it looked like were
 * "barely visible", and the screenshots agree: over the game's own map the wires were gone.
 *
 * WHAT THE MEASUREMENT MISSED IS THAT dE IS ABOUT A SWATCH AND A WIRE IS NOT ONE. A 1.5 px
 * stroke at 0.85 opacity puts almost no fully-covered pixel on the screen -- the canvas
 * antialiases it across two or three, so what a reader actually sees is the colour composited
 * over the ground at something like 0.6, at which a dE 26.7 swatch is worth about dE 15 of
 * real contrast, spread over one pixel. Measuring the swatch answered a question nobody was
 * asking.
 *
 * So the fix is not a louder violet. It is the casing, and the casing is what makes the pair
 * work on grounds that have nothing in common: the game's artwork is a pale warm tan with cyan
 * water -- binned to a 16-level cube over the three terrains this was judged on, the six
 * commonest tones are #486878, #888878, #a8a898, #988878, #4898a8 and #989888 -- while `plain`
 * mode is near-black. A single colour cannot be far from both. A dark casing and a light core
 * can, because whichever way the ground goes, one half of the pair separates from it and the
 * other half is the reason the line still has a colour.
 *
 * All three below are measured in CIE Lab against the CURRENT full table -- which is the
 * lesson the ledger's first finding taught, the pipe rust having been measured against a set
 * that was missing the bauxite dot. The distances quoted are cross-owner nearest neighbours;
 * palette.ts checks them at every dev boot and would say so if any of them moved.
 */

/* The casing: a deep indigo, and the furthest any colour on this page sits from all the
 * others. Nearest cross-owner neighbour is the fluid-storage magenta at dE 33.7 and the
 * nearest after that the oil node at 34.6; the dark neighbourhood it might have collided with
 * -- the lift fill at dE 38.8, the concrete at 41.3, the coal dot at 45.4 -- is not close.
 * That is the reward for keeping it violet instead of black: a near-black casing would have
 * landed among four greys that are already the tightest cluster on the map.
 *
 * Against the artwork it is dE 48.4 from the nearest ground tone on the desert and the coast
 * and 54.3 on the forest, and 61.6 to 71.2 on the mean weighted by how much of the map each
 * tone covers -- which is where its whole job is done: the ground under a wire is pale, and
 * this is not. */
var CASING_COLOUR = declareColours("power", { casing: "#1c1550" }).casing;

/* The core: the same violet quarter the layer has always had, taken up to where it reads as a
 * line rather than as a smudge. The violet is still the free hue -- blue is the machines,
 * amber the extractors, red the generators, magenta the storage, steel the belts, rust the
 * pipes -- so this is the old decision at a new value, not a new decision.
 *
 * Nearest cross-owner neighbour is the raw-quartz dot at dE 22.8, then the hard-drive pickup at
 * 25.6, the water dot at 28.2 and the machine blue at 28.3. The belts, which are the comparison
 * that has to hold because a wire and a belt genuinely do run side by side inside a factory, are
 * dE 33.2, 33.5 and 36.5 away across their three tones -- further than the old hairline managed
 * at 24.2. Against the artwork it is dE 39.1 to 40.2 from the nearest ground tone. */
var WIRE_COLOUR = declareColours("power", { wires: "#b8b0f8" }).wires;

/* The poles, one value step up the same hue -- the grammar the belts, the pipes and the
 * storage boxes all use for a distinction inside one family. Here the distinction is not a
 * tier but a KIND: the line and the thing the line ends at.
 *
 * The step is still the house step, dE 16.0, against the belts' 15.6 between their slowest and
 * fastest, the pipes' 15.7 between Mk1 and Mk2 and the storage pair's 16.7 -- the pair moved
 * and the distance between them did not. Lighter rather than darker on purpose, and the
 * original reason has been overtaken by a better one: a pole used to have to out-value the
 * four wires that meet at it, and now it has the casing as a RIM instead (see the disc below),
 * which is a stronger separation than any step. What lighter still buys is the world view,
 * where a pole is the mark that says a base is here.
 *
 * Re-measured rather than assumed safe, because a ramp can walk a colour into a neighbour.
 * Nearest cross-owner neighbour is the fast belt at dE 23.6, then the crashed drop pod at 25.0
 * and the limestone dot at 26.1 -- all further away than the old pole colour's 19.9 -- and the
 * nearest ground tone is dE 28.4 on the forest and 31.7 on the desert and the coast. */
var POLE_COLOUR = declareColours("power", { poles: "#d8c8f8" }).poles;

/* How big a pole's disc is, in PIXELS, by what the pole is.
 *
 * Pixels and not metres, which is the opposite of every other placement on this map and is a
 * decision about what the mark MEANS. A machine is drawn at its measured footprint because the
 * question there is "does this fit"; a pole is drawn at a fixed size because the question is
 * "is there one here", which is the node dots' grammar (PURITY_RADIUS in markers.ts) and the
 * reason `_fixed` exists in routes.ts. A true-size pole would be a fifth of a pixel at the
 * world view and would make the mark disappear at exactly the zoom the layer is most useful.
 *
 * The steps are a MEASUREMENT and not a decoration. A pole's mark is how many wires it can
 * carry, which the reference save states outright: the busiest Mk1 on the world carries
 * exactly 4, the busiest Mk2 exactly 7 and the busiest Mk3 exactly 10, which are the game's
 * own three limits. So the ramp is 3.2 / 4.2 / 5.2 px -- bigger node, more wires -- and the
 * wall outlets sit under Mk1 at 2.5 px because a socket on a wall is the smallest thing here.
 *
 * EVERY ONE OF THOSE WENT UP, by 0.5 px at the bottom and 1.2 at the top, and the step between
 * them went from 0.7-0.8 px to 1.0. The old ramp was drawn before there was a base map: on
 * plain dark ground a 2.5 px disc is a clear mark, and over the game's own artwork it is a
 * speck. The wider step is the same argument one level down -- three sizes 0.7 px apart are
 * three sizes nobody can tell apart at the zoom the difference is supposed to be read at.
 *
 * A class this table has never heard of gets the Mk1 size rather than nothing: an unrecognised
 * pole is still a place where wires end. */
var POLE_RADIUS_PX: Record<string, number> = {
  Build_PowerPoleWall_C: 2.5,
  Build_PowerPoleWall_Mk2_C: 2.5,
  Build_PowerPoleWallDouble_Mk2_C: 2.5,
  Build_PowerPoleMk1_C: 3.2,
  Build_PowerPoleMk2_C: 4.2,
  Build_PowerPoleMk3_C: 5.2,
};
var POLE_FALLBACK_PX = 3.2;

/* A Power Tower is drawn as a RING and every other pole as a filled disc, and the shape is
 * doing the work a size could not.
 *
 * There are 137 of them against 564 poles, they carry the only wires on this world longer than
 * 300 m, and they are physically a different kind of object: a 12 m platform on a pylon, tall
 * enough that its connectors sit 24 m above its own base. Making it merely a bigger dot would
 * say "a pole with more connections", which is what the ramp above already means and is not
 * what a tower is. A ring says "structure, seen from above" -- the same thing the conveyor
 * lift's ring says in the belts layer, and for the same reason: an outline is what a thing you
 * could stand inside looks like from directly overhead.
 *
 * BIGGEST BUMP ON THE LAYER, 5 px to 7.5 and a 1.5 px stroke to 2, because a tower is the one
 * mark here whose job is the whole-world view. There are 137 of them, they carry every wire on
 * this world longer than 300 m, and at 5 px they were the same size as a Mk3 pole plus one --
 * which is to say invisible as a landmark at exactly the zoom where "the long spans start
 * here" is the only thing about this layer worth reading. */
var TOWER_CLASS = "Build_PowerTowerPlatform_C";
var TOWER_RADIUS_PX = 7.5;
var TOWER_WEIGHT_PX = 2;

/* How much wider the casing is than the core it sits under, in SCREEN pixels, so the rim is
 * the same thickness at every zoom -- which is what a casing is. Two, so 1 px shows either
 * side: enough to be a rim on a 2.5 px line and not so much that the pair reads as a road.
 *
 * A pixel constant and not a width in metres, and that is the whole difference between this
 * and ROUTE_WIDTH_M next door. A casing is not part of what a wire IS. It is a drawing
 * technique for making a thin thing survive a busy background, so it is stated in the units
 * the background is measured in. See `_widen` in styleRoutes, which re-adds it on every zoom.
 *
 * The same number widens the ring under a power tower, because it is the same rim. */
var WIRE_CASING_PX = 2;

/* And how thick the rim around a pole's DISC is. Smaller than the wires' casing on purpose: a
 * disc has an outline all the way round rather than two edges, so it needs less of one to read
 * as edged -- and 2 px of rim on a 2.5 px wall socket would be a dark dot with a highlight. */
var POLE_RIM_PX = 1;

/* One opacity for the whole layer, casing and core alike, matching the belts and pipes at
 * 0.85. Two would be a knob: at a 1 px rim the difference between 0.85 and full is a fraction
 * of one pixel's worth of ground showing through, which is not a thing anybody can see and is
 * a thing somebody would later have to explain. */
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
   * discs cluster into the same shape the wires already make, so they cost nothing legible
   * and they are the layer at factory zoom. Splitting them into a second checkbox would be two
   * rows for one idea.
   *
   * THAT PARAGRAPH WAS TRUE AND THE LAYER STILL DID NOT DO IT. Everything above is about what
   * this data is worth at the world view; none of it is about whether the drawing was strong
   * enough to deliver it, and it was not -- 1.5 px of violet at 0.85 over the game's own pale
   * artwork is a rumour of a network rather than a network. What this function draws now is
   * the same geometry cased and widened, which is the difference between the argument being
   * right and the layer being right. See CASING_COLOUR above. */
  // Third of the three networks and last of them, under the belts and the pipes: the grid is
  // what joins this world's bases rather than what moves anything through one.
  var group = layer("power", true, WIRE_COLOUR, [BAND.built, 30, "power"]);
  // The same expression the zoom pass restyles these with, off the same two tables -- see
  // routeWeight in routes.ts, which is exported for exactly this line.
  var weight = routeWeight(ROUTE_WIDTH_M.power!, pixelsPerMetre(), ROUTE_FLOOR_PX.power);

  /* THE CORES FIRST AND THE CASINGS AFTER THEM, WHICH IS WHAT PUTS THE CASINGS UNDERNEATH.
   *
   * That is backwards from how it reads, and it is not a mistake. Draw order on this canvas
   * is ADD order, so adding the casing second would indeed leave it on top -- except that
   * `sinkRoutes` runs at the end of this function and REVERSES the runs: it calls
   * `bringToBack` down the list, and every call puts its caller below everything already
   * sunk, so the piece sunk last ends up at the very bottom. See the note on sinkRoutes.
   *
   * All the cores and then all the casings, rather than a pair at a time. Interleaved, the
   * same reversal would leave each wire's casing above the NEXT wire's core, and a crossing
   * would show one line breaking the other for no reason anyone could name. Two passes make
   * the layer two clean sheets: every casing under every core.
   *
   * The casing is not interactive, so the click-through is exactly what it was before there
   * was one: a wire's popup is on its core, at the core's width, and the 1 px of rim either
   * side belongs to no piece at all. Making it clickable would have put 1,297 more hit-tested
   * paths on the canvas to answer with the popup the core already answers with.
   */
  function chord(w: WireRow): L.LatLngTuple[] {
    return [
      [-w.a_m[1], w.a_m[0]],
      [-w.b_m[1], w.b_m[0]],
    ];
  }

  /* Both endpoints, in the payload's own [x, y, z] order, which is the shape the floor
   * filter reads a two-ended piece by. A wire is the only thing in this layer that can be on
   * two storeys at once, and the z of each end is the whole of the evidence for which. */
  function ends(w: WireRow): [Point3M, Point3M] {
    return [w.a_m, w.b_m];
  }

  data.wires.forEach(function (w) {
    var core = L.polyline(chord(w), {
      color: WIRE_COLOUR,
      weight: weight,
      opacity: WIRE_OPACITY,
    });
    core._floor = { power: "wire", ends: ends(w) };
    core.bindPopup(wirePopup(w)).addTo(group);
  });

  data.wires.forEach(function (w) {
    var cased = L.polyline(chord(w), {
      color: CASING_COLOUR,
      weight: weight + WIRE_CASING_PX,
      opacity: WIRE_OPACITY,
      interactive: false,
    });
    cased._widen = WIRE_CASING_PX;
    // The same ends as the core it sits under, so the filter reaches the same verdict about
    // the two without having to be told they belong together. `casing` rather than `wire`
    // is what keeps it from earning a second connector glyph on top of its core's.
    cased._floor = { power: "casing", ends: ends(w) };
    cased.addTo(group);
  });

  /* A tower's casing is a second RING, for the reason a wire's is a second line, and it is
   * collected here rather than added in place: the same reversal applies inside the glyphs,
   * so the casings have to go in after every disc and every core ring. */
  var towerCasings: L.CircleMarker[] = [];

  data.poles.forEach(function (p) {
    var tower = p.cls === TOWER_CLASS;
    var radius = poleRadius(p.cls);
    var piece = L.circleMarker([-p.y_m, p.x_m], {
      radius: radius,
      // A DISC IS CASED BY ITS OWN OUTLINE. A stroke is already drawn around every one of
      // these; all that changed is that it is now the casing colour instead of the fill's,
      // which turns a flat dot into an edged mark for no extra path at all. A ring cannot do
      // that -- its stroke IS the mark -- so a tower gets the second ring below instead.
      color: tower ? POLE_COLOUR : CASING_COLOUR,
      weight: tower ? TOWER_WEIGHT_PX : POLE_RIM_PX,
      // A ring for a tower and a disc for everything else; see TOWER_CLASS above.
      fillColor: POLE_COLOUR,
      fillOpacity: tower ? 0 : 0.9,
    });
    piece._fixed = true;
    // Where it stands, which is how the floor filter places a thing no band lists -- the
    // storage boxes' own join, and for the same reason: a pole is a placement the floor
    // decomposition never decomposed. See `power` in FloorMark.
    piece._floor = { power: "pole", x_m: p.x_m, y_m: p.y_m, z_m: p.z_m };
    piece.bindPopup(polePopup(p)).addTo(group);
    if (!tower) return;
    var cased = L.circleMarker([-p.y_m, p.x_m], {
      radius: radius,
      color: CASING_COLOUR,
      weight: TOWER_WEIGHT_PX + WIRE_CASING_PX,
      fillOpacity: 0,
      interactive: false,
    });
    cased._fixed = true;
    cased._floor = { power: "casing", x_m: p.x_m, y_m: p.y_m, z_m: p.z_m };
    towerCasings.push(cased);
  });

  towerCasings.forEach(function (piece) {
    piece.addTo(group);
  });

  sinkRoutes();
}

/* Static for the reason the belts are: a wire changes when the player builds, not when the
 * game autosaves. The header's power figures come from a different endpoint on the other
 * wave, which is why a save write updates the number without redrawing the network.
 *
 * `refilters: true`, AND IT USED TO BE FALSE, on the argument that "a circuit is a pool rather
 * than a placement" so floor mode would find nothing of its own to do here. That argument was
 * about the API's decomposition and it was right about it -- `/api/floors` still says nothing
 * about a wire -- but it was answering the wrong question. What a reader in floor mode sees is
 * a factory's storey, and the cables running through that storey are part of it; leaving them
 * unfiltered drew every wire of every floor at once over one deck's plan. floors.ts now places
 * this layer geometrically, off the endpoint heights the payload already carries, so a redraw
 * during floor mode owes the filter a pass exactly as the belts and the pipes do. See `power`
 * in FloorMark and the power branch in applyFilter. */
registerFetch<PowerResponse>({
  wave: "static",
  rank: 50,
  path: "/api/power",
  label: "power",
  clears: ["power"],
  refilters: true,
  draw: drawPower,
});
