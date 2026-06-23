/* Both networks: the belts and the pipes, drawn as the routes they actually take.
 *
 * One module for two layers on purpose. They share a grammar -- one colour family each, tier
 * as a value step inside it, width physical and floored at a hairline -- and three passes run
 * over both of them by name: the pixel restyle on zoom, the sink that keeps a run from
 * stealing a machine's click, and the width table that is the only thing telling them apart.
 * Two files would be two copies of each pass, and they would drift the first time a third
 * network arrives.
 */

import { code, popup } from "./dom";
import { L } from "./leaflet";
import { layer } from "./layers";
import { footprintCorners, pixelsPerMetre } from "./map";
import { raiseNodeDots } from "./markers";
import { state } from "./state";

/* The conveyor network, drawn as the routes it actually takes.
 *
 * New with schema 12, which is the whole reason this layer did not exist: the splines were
 * decoded by the parser and thrown away at the projection, so the map could show the
 * concrete and the machines and nothing about what joined them.
 *
 * ONE colour family for the whole network, and tier as a VALUE step inside it. There is a
 * real base map underneath now, and five hues of belt over a photographic terrain would be
 * the loudest thing on the page for information a player reads off a popup anyway. The step
 * is deliberately small -- three tones, twenty points of each channel apart -- so a trunk
 * line reads as heavier than a feeder without either of them shouting.
 *
 * Tier used to be a WIDTH step, and it could not stay one once width became physical: in
 * game every belt is the same two metres across, Mk1 to Mk5, so a map that drew a Mk5 wider
 * would be contradicting the same map's machines, which are drawn at their measured
 * footprint. Value is the quietest channel that was still free.
 *
 * A LIFT gets a ring instead of a line, because a line is not available: a lift is exactly
 * vertical (measured, server-side -- all 302 on this world have zero horizontal extent),
 * so its top-down polyline is one point and a polyline through it draws nothing at all.
 * The ring is also the right picture: seen from above, a lift is a hole in the floor.
 *
 * A SPLITTER or MERGER gets a square, in this layer and in no other. It is a piece of the
 * belt network -- no recipe, no power, meaningless without the runs either side of it -- so
 * it is drawn by the layer that draws them, which is also what keeps it from being drawn
 * twice: it is in no other payload, and the machines layer has never had it. Without them a
 * belt-only view had a four-metre hole at every one of the world's 848 junctions, and a run
 * that visibly stopped and started again is a run a reader has to guess is one run.
 */
/* Mid steel, and mid on purpose: this is the one layer with no ground of its own, so it has
 * to read over the dark concrete it mostly runs on AND over pale sand where it crosses
 * country. A near-white line does the first and vanishes into the second -- and at 3,085
 * routes it was also, briefly, the loudest thing on a page that now has real terrain under
 * it. A mid tone is darker than the sand and lighter than the concrete, which is the only
 * value that has contrast both ways without being bright. */
var BELT_COLOUR = "#93a5b4";
var LIFT_FILL = "#252a30"; // the hole the ring is drawn around.

/* The three tier tones, one step of value either side of BELT_COLOUR -- which stays the
 * middle one, so the swatch in the layer control is still the network's own colour. */
var BELT_SLOW = "#7f8f9d";
var BELT_FAST = "#a7b9c7";

/* Tier as value. `items_per_min` is the dump's own figure for the class -- 60, 120, 270,
 * 480, 780 -- so this is a banding of a measurement rather than a parse of "Mk3" out of a
 * display name, and it is the same banding the width step used to make. An unknown tier
 * draws at the middle tone: the darkest would read as Mk1. */
function beltColour(items_per_min) {
  if (!items_per_min) return BELT_COLOUR;
  if (items_per_min >= 480) return BELT_FAST;
  if (items_per_min >= 270) return BELT_COLOUR;
  return BELT_SLOW;
}

/* How wide a belt is, in metres, and the floor that keeps one visible.
 *
 * Two metres is the game's own belt width, and it is a CONSTANT here rather than a field
 * because the save does not carry one: a spline is a centre line, and every tier rides the
 * same two-metre frame. Stating it makes the width a claim a reader can check against the
 * scale bar, which is what "1.8 px at every zoom" never was.
 *
 * The floor is where honesty stops paying. At the whole-world zoom a metre is 0.14 px, so a
 * true-size belt would be a quarter-pixel of nothing; 1.5 px is a hairline that still draws.
 * Above about zoom -2 the physical width wins and the floor is never reached, which is the
 * whole point -- the floor is a lower bound on visibility, not a second styling rule.
 *
 * The floor is ROUTE_MIN_PX and not BELT_MIN_PX because it is not a statement about belts:
 * it is the width below which a stroked line stops being drawn at all, and the pipes next
 * door -- narrower in the world, so floored a zoom step sooner -- reach it for the same
 * reason. One hairline for both, or the two networks fade out at different zooms and the
 * page invents a difference the world does not have. */
var BELT_WIDTH_M = 2;
var ROUTE_MIN_PX = 1.5;

/* A lift is the same two metres seen end-on, so its ring is that circle: radius half the
 * belt width, floored a little higher than the line is, because a ring has to enclose
 * something to read as a ring rather than as a dot. */
var LIFT_MIN_RADIUS_PX = 2;

/* A splitter or merger with no measured footprint. The docs dump carries clearance for none
 * of these four classes, so the server sends null and this is the page's own stand-in --
 * the same arrangement, and the same reason, as MACHINE_FALLBACK_M in placements.ts: a number invented
 * server-side would arrive indistinguishable from a measurement. Four metres is the square
 * the pieces snap to, which is also what makes a run read as continuous through one. */
var ATTACHMENT_FALLBACK_M = 4;

/* A route's stroke width, in pixels, from its width in the world. The one place the two
 * network layers agree completely: a belt is two metres and a pipe is 1.3, and past the
 * floor each is drawn at whatever that is worth on screen right now. */
function routeWeight(width_m, ppm) {
  return Math.max(ROUTE_MIN_PX, width_m * ppm);
}

function beltWeight(ppm) {
  return routeWeight(BELT_WIDTH_M, ppm);
}

function liftRadius(ppm) {
  return Math.max(LIFT_MIN_RADIUS_PX, (BELT_WIDTH_M / 2) * ppm);
}

function beltPopup(b, first, last) {
  return popup([
    ["belt", b.name || b.cls],
    // Said out loud, because the glyph is the one encoding on this map that exists
    // because of a measurement rather than because of a preference.
    ["kind", b.lift ? "conveyor lift — vertical, so drawn as a ring" : null],
    ["rate", b.items_per_min ? b.items_per_min + " items/min at 100%" : null],
    // Travel order, input to output: the projection reverses the save's own output-first
    // storage, so these two rows mean what they say.
    ["from", first[0] + ", " + first[1] + " m"],
    ["to", last[0] + ", " + last[1] + " m"],
    ["rise", Math.round((last[2] - first[2]) * 10) / 10 + " m"],
    ["chain", "#" + b.chain],
  ]);
}

export function drawBelts(data) {
  // Off by default at the whole-world zoom, exactly like `machines` and for the same
  // reason: 3,085 routes across 7 km is a smear. See reveal() in labels.ts.
  var group = layer("belts", false, BELT_COLOUR);
  var ppm = pixelsPerMetre();
  data.belts.forEach(function (b) {
    var points = b.points_m.map(function (p) {
      return [-p[1], p[0]];
    });
    var first = b.points_m[0];
    var last = b.points_m[b.points_m.length - 1];
    var piece;
    if (b.lift) {
      piece = L.circleMarker(points[0], {
        radius: liftRadius(ppm),
        color: beltColour(b.items_per_min),
        weight: 1.5,
        fillColor: LIFT_FILL,
        fillOpacity: 0.9,
      });
    } else if (points.length < 2) {
      return; // a route with one point is not a route, and this is not a lift
    } else {
      piece = L.polyline(points, {
        color: beltColour(b.items_per_min),
        weight: beltWeight(ppm),
        opacity: 0.85,
      });
    }
    piece.bindPopup(beltPopup(b, first, last)).addTo(group);
  });
  (data.attachments || []).forEach(function (a) {
    if (a.x_m === null || a.y_m === null) return;
    var w = (a.w_m || ATTACHMENT_FALLBACK_M) / 2;
    var l = (a.l_m || ATTACHMENT_FALLBACK_M) / 2;
    L.polygon(footprintCorners(a.x_m, a.y_m, w, l, a.yaw), {
      color: BELT_COLOUR,
      weight: 1,
      fillColor: BELT_COLOUR,
      fillOpacity: 0.85,
    })
      .bindPopup(
        popup([
          ["belt part", a.name || a.cls],
          ["facing", a.yaw === null || a.yaw === undefined ? null : Math.round(a.yaw) + "°"],
          ["at", a.x_m + ", " + a.y_m + " m"],
          ["instance", code(a.instance_leaf)],
        ])
      )
      .addTo(group);
  });
  sinkRoutes();
}

/* The pixels half of both route layers, re-derived whenever the scale changes.
 *
 * A polyline's weight and a circleMarker's radius are the two sizes on this page that are
 * given in screen pixels, so they are the two that do NOT follow the map on their own -- a
 * belt drawn 1.8 px wide was 1.8 px wide at 7 km across and 1.8 px wide standing inside a
 * factory, which is why it read as a thread beside machines drawn at their true footprint.
 * Everything else here is a polygon in map units and needs none of this.
 *
 * Restyling on zoomend, rather than drawing the routes as thin polygons in map units:
 *
 *   * a polygon cannot have a floor. Below zoom -2 a true two-metre belt is a fraction of a
 *     pixel, and the floor is what keeps the network visible at the world view at all --
 *     it is a pixel statement, so it needs a pixel size to make it in;
 *   * a polygon would also change what a click means. A belt is hit-tested as a line plus
 *     Leaflet's tolerance today, and a 3,085-piece layer of two-metre ribbons would be
 *     unclickable at exactly the zooms where the popup is worth opening;
 *   * and it costs nothing to keep. Measured over the whole layer at factory zoom -- 3,933
 *     pieces, splitters included -- 0.6 to 0.7 ms median for the pass and 3 ms at its
 *     worst, once per zoom step, against a 16.7 ms frame. Two runs of 300 frames sampled
 *     across twelve zoom steps sat at a 16.7 ms median and a 16.8 ms p95; one of the two
 *     dropped a single frame (33 ms), which is one canvas redraw and not a stutter.
 *
 * There is no pop to debounce away, either -- the opposite. Leaflet scales the whole canvas
 * as one image during a zoom animation, so a belt already grows with the map mid-flight and
 * used to SNAP BACK to its fixed pixel width when the canvas was redrawn at the end. Landing
 * on the width the animation was already showing is what removes that snap. */
export function styleRoutes() {
  var ppm = pixelsPerMetre();
  var radius = liftRadius(ppm);
  var alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var weight = routeWeight(ROUTE_WIDTH_M[name], ppm);
    group.eachLayer(function (piece) {
      // Four kinds of piece share these layers now and only two of them are sized in pixels:
      // a lift's ring by its radius, a run by its weight. A splitter is a polygon in map
      // units and is already the right size at every zoom -- exactly like a machine, which is
      // the whole reason it is drawn as one. A chevron is map units too, so what changes for
      // it is not its size but whether it is drawn at all: it is the one piece here that has
      // a zoom BELOW which it is noise rather than information.
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      else if (piece.setRadius) piece.setRadius(radius);
      else if (!(piece instanceof L.Polygon)) piece.setStyle({ weight: weight });
    });
  });
}

/* The fluid network, drawn the same way and told apart by colour.
 *
 * New with schema 13, and the belts' other half: `mSplineData` sat on every pipeline actor
 * the whole time and the projection dropped it, so the map could show a base's belts and
 * nothing about its plumbing.
 *
 * The same posture as the belts next door, deliberately -- one colour family for the whole
 * network, tier as a VALUE step inside it, width physical, no second palette -- because a
 * page that encodes two networks by two different grammars makes the reader learn twice.
 *
 * NO GLYPH, and that is a measurement rather than an omission. A conveyor lift gets a ring
 * because its top-down polyline is a single point; the server measured the same question for
 * pipes and the answer is that there is no such piece -- the tightest of this world's 503
 * spans 11.6 cm horizontally and only one is under 50 cm. Every pipe draws as a line. The
 * one-point guard below is still there, because "none on this world" is not "none ever".
 *
 * ARROWS ON 365 OF THE 503, and a stated refusal on the rest. The save still does not record
 * a pipe's flow direction -- that never changes -- but it does record the whole plumbing
 * graph and it TYPES a machine's ports, so `/api/pipes` now infers a direction where the
 * network admits only one and says `unknown` where it admits two. A pipe hanging off a water
 * extractor had an obvious answer and printing "not recorded" at it read as obtuseness rather
 * than as honesty. So: chevrons on the resolved ones, nothing at all on the rest, and a popup
 * that names which of the two a reader is looking at.
 */
/* Rust, and picked by measuring rather than by taste. This layer has to separate from three
 * things at once: the belts it runs beside, the terrain it crosses, and the amber the
 * extractors are already drawn in -- and the last is the trap, because a water extractor is
 * where pipes and extractors physically meet. Measured in CIE Lab against the base map under
 * all 503 routes and against every colour already on the page: #a8613c is dE 34 from the
 * terrain and 22 from its nearest neighbour on the page (the copper-ore dot), where the
 * saturated amber a pipe suggests first, #d99a3e, is dE 4.5 from the extractors and would
 * have been indistinguishable from them. Warm where the belts are cool, and dark enough not
 * to shout over a photographic map. */
var PIPE_COLOUR = "#a8613c";

/* The two tier tones, one step of value either side of PIPE_COLOUR -- which stays the middle
 * one, so the swatch in the layer control is still the network's own colour. The step is the
 * belts' step, twenty points of each channel, and it lands where the belts' does: dE 15.7
 * between Mk1 and Mk2 against the belts' 15.6 between their slowest and fastest.
 *
 * Re-measured rather than assumed safe, because PIPE_COLOUR was itself chosen by measurement
 * and a ramp can walk a colour into a neighbour. In CIE Lab both tones stay clear of every
 * other colour on the page -- nearest is the generator red at dE 28.9 and 28.3, where the
 * base sits at 27.6, so the ramp moves away from it rather than toward -- and the extractor
 * amber that ruled out a brighter pipe stays 40.9 and 32.4 away, against the dE 4.5 that
 * disqualified #d99a3e. */
var PIPE_MK1 = "#944d28";
var PIPE_MK2 = "#bc7550";

/* Tier as value, the same banding the belts use and for the same reason -- and for one more.
 * `flow_m3_min` is the dump's own figure for the class -- 300 on Mk1, 600 on Mk2 -- so this
 * is a banding of a measurement rather than a parse of "MK2" out of a display name.
 *
 * It could not have stayed a width step once the belts' became physical. A Pipeline Mk.2 is
 * not a wider pipe: it is the same 1.3 m bore with a better pump rating, and it snaps to the
 * same holes in the same supports. A map that drew it wider would be contradicting its own
 * scale bar, and contradicting the belts beside it, which gave up exactly this encoding for
 * exactly this reason. Two tiers, so two tones and the middle for the unknown: the darker
 * would read as Mk1. */
function pipeColour(flow_m3_min) {
  if (!flow_m3_min) return PIPE_COLOUR;
  return flow_m3_min >= 600 ? PIPE_MK2 : PIPE_MK1;
}

/* A pipe is 1.3 m across, and like the belt's two metres it is a CONSTANT rather than a
 * field: the save carries a centre line and no bore, and both tiers ride the same frame.
 * Narrower than a belt, so it reaches ROUTE_MIN_PX about a zoom step sooner -- which is the
 * true picture, a pipe IS thinner than a belt, and the floor is what keeps it from being
 * nothing at all at the whole-world view. */
var PIPE_WIDTH_M = 1.3;

function pipeWeight(ppm) {
  return routeWeight(PIPE_WIDTH_M, ppm);
}

/* What the popup says about a direction, keyed by what the server based it on. Short on
 * purpose: the row it replaces was a sentence explaining why there was nothing to say, and
 * the whole point of the change is that on 365 of 503 pipes there now is. The `from` and
 * `to` rows carry the direction itself, exactly as they do on a belt, so this row only has
 * to carry the WARRANT. */
var PIPE_FLOW_BASIS = {
  "machine port": "→ a typed machine port at one end",
  pump: "→ pump orientation",
  propagated: "→ inferred from the network",
};

/* And the honest refusal, kept for the 138 the network genuinely does not settle -- a pipe
 * in a loop, or a trunk with producers and consumers on both sides. Shorter than the
 * sentence it replaces, because it is now the exception rather than the rule. */
var PIPE_FLOW_UNKNOWN = "not recorded, and the network does not imply it";

function pipePopup(p, first, last) {
  var known = p.direction === "forward" || p.direction === "reverse";
  var head = p.direction === "reverse" ? last : first;
  var tail = p.direction === "reverse" ? first : last;
  return popup([
    ["pipe", p.name || p.cls],
    // The thing a belt cannot say. It comes off the game's own FGPipeNetwork rather than
    // from what the pipe is plugged into, which is why it can be stated flatly.
    ["fluid", p.fluid_name],
    ["capacity", p.flow_m3_min ? p.flow_m3_min + " m³/min at 100%" : null],
    ["flow", known ? PIPE_FLOW_BASIS[p.basis] || "→ inferred" : PIPE_FLOW_UNKNOWN],
    // `from`/`to` where the direction is known, which is the belt popup's own wording and
    // means the same thing there; `ends` where it is not, so the two are never confused.
    ["from", known ? head[0] + ", " + head[1] + " m" : null],
    ["to", known ? tail[0] + ", " + tail[1] + " m" : null],
    ["ends", known ? null : first[0] + ", " + first[1] + " m and " + last[0] + ", " + last[1] + " m"],
    // Signed once there is a direction to sign it against -- a pipe that climbs 6 m is a
    // different fact from one that drops 6 m, and until now neither could be said.
    [
      "rise",
      known
        ? Math.round((tail[2] - head[2]) * 10) / 10 + " m"
        : Math.abs(Math.round((last[2] - first[2]) * 10) / 10) + " m",
    ],
    ["network", p.network === null ? null : "#" + p.network],
  ]);
}

/* Chevrons: the direction, drawn.
 *
 * Geometry in METRES, like the machines and the foundations and unlike the line it sits on,
 * so it scales with the map for free and stays the same size relative to the plumbing at
 * every zoom. Three metres long and 2.4 across -- a little under twice the 1.3 m bore, which
 * is what makes it read as a mark ON the pipe rather than as a kink IN it.
 *
 * Placed by ARC LENGTH, not per corner: one every 24 m with a minimum of one per pipe, so a
 * 9 m elbow gets a single mark and the 56 m trunk gets two, and a run reads as a dotted line
 * of them rather than as a cluster at every bend. Counted in the PLAN, because that is what
 * the map shows: a pipe's climb is not length it has anywhere to put a mark. 400 chevrons
 * over 337 of this world's 365 resolved pipes; the 4 m floor drops the other 28, because a
 * mark three quarters as long as the piece carrying it is not a mark, it is the piece.
 *
 * HIDDEN AT WORLD ZOOM, and by the same grammar the hairline floor uses -- a pixel statement,
 * made in pixels. Below 5 px a chevron has no discernible apex; it is a dash, and a dash on a
 * line says nothing about direction at all. Three metres reaches 5 px at zoom 1, which is
 * labels.ts' FACTORY_MAX_ZOOM, i.e. exactly the scale the "show me this factory" flight lands at. At the
 * whole-world zoom -3 a metre is 0.14 px and 412 of these would be 412 specks of noise.
 *
 * Not interactive: a chevron sits on top of its own pipe, and a mark that stole the pipe's
 * popup would make the direction unreadable by making the piece unclickable. */
var CHEVRON_LENGTH_M = 3;
var CHEVRON_SPAN_M = 2.4;
var CHEVRON_SPACING_M = 24;
var CHEVRON_MIN_RUN_M = 4;
var CHEVRON_MIN_PX = 5;
var CHEVRON_WEIGHT_PX = 1.5;

/* A value step far above both pipe tones, so it reads against the line it is drawn on, and
 * measured like every other colour here. At its 0.7 opacity the composite over the three
 * pipe tones is dE 26.1 to 36.7 from the pipe underneath -- unmistakably a separate mark --
 * while the nearest colour anywhere else on the page is the iron-ore dot at dE 11.9 to 13.0,
 * which is a filled disc on terrain rather than a thin V on a line. The extractor amber that
 * ruled out a brighter pipe in the first place stays dE 45.5 away from the swatch. */
var CHEVRON_COLOUR = "#e8cbb4";
var CHEVRON_OPACITY = 0.7;

function chevronOpacity(ppm) {
  return CHEVRON_LENGTH_M * ppm >= CHEVRON_MIN_PX ? CHEVRON_OPACITY : 0;
}

/* Where the chevrons go on one route, in world metres, as [[x, y], [x, y], [x, y]] apexes.
 *
 * Takes a polyline and a flag rather than a pipe, so it knows nothing about plumbing: a belt
 * has a direction too -- its points are already in travel order -- and could be marked by
 * this same function tomorrow. See ROUTE_CHEVRONS for why it is not being marked today. */
function routeChevrons(points_m, reverse) {
  var pts = reverse ? points_m.slice().reverse() : points_m;
  var runs = [];
  var total = 0;
  for (var i = 1; i < pts.length; i++) {
    var dx = pts[i][0] - pts[i - 1][0];
    var dy = pts[i][1] - pts[i - 1][1];
    var d = Math.sqrt(dx * dx + dy * dy);
    if (!(d > 0)) continue;
    runs.push({ x: pts[i - 1][0], y: pts[i - 1][1], ux: dx / d, uy: dy / d, d: d, at: total });
    total += d;
  }
  if (!runs.length || total < CHEVRON_MIN_RUN_M) return [];
  var marks = [];
  var n = Math.max(1, Math.floor(total / CHEVRON_SPACING_M));
  for (var k = 0; k < n; k++) {
    var along = ((k + 0.5) / n) * total;
    var run = runs[runs.length - 1];
    for (var j = 0; j < runs.length; j++) {
      if (along <= runs[j].at + runs[j].d) {
        run = runs[j];
        break;
      }
    }
    var t = along - run.at;
    var tx = run.x + run.ux * (t + CHEVRON_LENGTH_M / 2);
    var ty = run.y + run.uy * (t + CHEVRON_LENGTH_M / 2);
    var bx = tx - run.ux * CHEVRON_LENGTH_M;
    var by = ty - run.uy * CHEVRON_LENGTH_M;
    var nx = -run.uy * (CHEVRON_SPAN_M / 2);
    var ny = run.ux * (CHEVRON_SPAN_M / 2);
    marks.push([
      [bx + nx, by + ny],
      [tx, ty],
      [bx - nx, by - ny],
    ]);
  }
  return marks;
}

export function drawPipes(data) {
  // Off by default at the whole-world zoom, exactly like `belts` and `machines`. See reveal() in labels.ts.
  var group = layer("pipes", false, PIPE_COLOUR);
  var ppm = pixelsPerMetre();
  var alpha = chevronOpacity(ppm);
  data.pipes.forEach(function (p) {
    if (p.points_m.length < 2) return; // a route with one point is not a route
    var points = p.points_m.map(function (q) {
      return [-q[1], q[0]];
    });
    L.polyline(points, {
      color: pipeColour(p.flow_m3_min),
      weight: pipeWeight(ppm),
      opacity: 0.85,
    })
      .bindPopup(pipePopup(p, p.points_m[0], p.points_m[p.points_m.length - 1]))
      .addTo(group);
    if (!ROUTE_CHEVRONS.pipes) return;
    if (p.direction !== "forward" && p.direction !== "reverse") return;
    routeChevrons(p.points_m, p.direction === "reverse").forEach(function (mark) {
      var piece = L.polyline(
        mark.map(function (q) {
          return [-q[1], q[0]];
        }),
        {
          color: CHEVRON_COLOUR,
          weight: CHEVRON_WEIGHT_PX,
          opacity: alpha,
          interactive: false,
        }
      );
      piece._chevron = true;
      piece.addTo(group);
    });
  });
  sinkRoutes();
}

/* Both route layers share the overlay canvas with the machines and the node dots, so the
 * rule markers.ts' raiseNodeDots exists for applies to them too: hit-testing is draw order and the LAST
 * match wins. A belt run crosses every machine it feeds and a pipe run crosses every
 * refinery it feeds, and a polyline's hit area is its width plus Leaflet's click tolerance
 * -- so a route layer added after the machines would quietly take the click on every machine
 * it passes over. Pushed to the back instead: under the machines, under the node dots, still
 * over the foundations (a separate pane, so a separate canvas, so unaffected either way).
 *
 * Their own pane would be the tidier answer and is not one: Leaflet gives every pane its own
 * canvas, the DOM delivers a click to the topmost element under the pointer, and the overlay
 * pane's canvas covers the entire viewport -- so a clickable layer below it is not clickable
 * at all. That is also why this is safe to call whenever: `bringToBack` is a no-op on a path
 * whose group is not on the map, which is the state both layers start in.
 *
 * One function over both, rather than one per layer: it is one rule, and two copies of it
 * would drift the moment a third route layer arrives. Same for styleRoutes above, and for
 * ROUTE_WIDTH_M, which is the only thing either function needs to tell the two apart.
 *
 * Order INSIDE a layer matters too, and it is decided by the order of the calls: each
 * `bringToBack` puts its caller below everything already sunk, so the piece sunk LAST ends
 * up at the very bottom. The junction squares therefore go first and the runs after them,
 * which leaves a splitter sitting on the lines it joins rather than under them -- a 4 m
 * square hidden beneath a 2 m line is still visible at its corners and is not CLICKABLE,
 * and the popup naming the piece is the whole reason it has one. The pipes layer holds no
 * polygons, so for it the partition is a no-op and the loop is the same loop.
 */
export var ROUTE_LAYERS = ["belts", "pipes"];

/* What each route layer is worth in metres. The one place the two differ, so the one place
 * the shared passes above have to look. */
var ROUTE_WIDTH_M = { belts: BELT_WIDTH_M, pipes: PIPE_WIDTH_M };

/* Which route layers carry direction chevrons. A BELT HAS A DIRECTION TOO -- its points are
 * in travel order, which is the one thing the belts have always been able to say and the
 * pipes could not -- and `routeChevrons` above takes a polyline and a flag precisely so that
 * turning this to `true` is the whole of the work. It stays `false` because that is a
 * decision about the map, not about the data: 3,085 belt runs would put some 2,500 more
 * marks on the same canvas as these 412, and the owner asked for the pipes. A flag rather
 * than an absence, so the next person finds a switch instead of a rewrite. */
var ROUTE_CHEVRONS = { belts: false, pipes: true };

export function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var chevrons = [];
    var squares = [];
    var runs = [];
    group.eachLayer(function (piece) {
      (piece._chevron ? chevrons : piece instanceof L.Polygon ? squares : runs).push(piece);
    });
    // Sunk FIRST is left highest, per the note above, so the chevrons go before the squares
    // and the runs: a direction mark under the line it marks would not be a mark.
    chevrons
      .concat(squares)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}
