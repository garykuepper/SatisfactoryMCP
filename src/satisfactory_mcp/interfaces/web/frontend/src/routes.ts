/* Both networks: the belts and the pipes, drawn as the routes they actually take.
 *
 * One module for two layers on purpose. They share a grammar -- one colour family each, tier
 * as a value step inside it, width physical and floored at a hairline -- and three passes run
 * over both of them by name: the pixel restyle on zoom, the sink that keeps a run from
 * stealing a machine's click, and the width table that is the only thing telling them apart.
 * Two files would be two copies of each pass, and they would drift the first time a third
 * network arrives.
 *
 * A THIRD NETWORK HAS ARRIVED, and it is drawn by `power.ts` rather than here -- but by these
 * same three passes, which is what that paragraph was written to buy. `power` is in
 * ROUTE_LAYERS and in ROUTE_WIDTH_M at the bottom of this file, so a wire is floored at the
 * same hairline a belt and a pipe are and is sunk under the machines by the same rule. What
 * lives in `power.ts` is only what is specific to it: two colours, a pole glyph, and a popup.
 * The import goes one way -- power.ts reads `sinkRoutes` from here and nothing here reads
 * power.ts -- so the shared passes stay in the file that owns them.
 */

import { code, popup } from "./dom";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { footprintCorners, map, pixelsPerMetre } from "./map";
import { raiseNodeDots } from "./markers";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";
import { state } from "./state";

import type { Row } from "./dom";

import type {
  BeltRow,
  BeltsResponse,
  PipeFlowBasis,
  PipeRow,
  PipesResponse,
  Point3M,
  PointM,
  RouteCurveM,
  SpanCurveM,
} from "./api-types";

/* The row that names the thing the reader just clicked, for the three route popups whose
 * subject is a resolved class.
 *
 * `name || cls` was the whole of this, and it has a hole at the bottom: both are nullable --
 * `saveio.rows` types a piece's `cls` as `str | None` and `building_name` is `None` in, `None`
 * out -- so a torn row evaluates to null, popup() drops a null row, and the card loses its
 * TITLE while keeping every coordinate under it. A reader gets a belt popup that does not say
 * "belt".
 *
 * The row is therefore always emitted, and the last branch is a statement rather than a
 * fallback name: "the class was not recorded" is what the projection actually said, and
 * inventing a name here would be indistinguishable from a resolved one -- the same rule the
 * server follows when it sends null instead of guessing. */
function titleRow(key: string, name: string | null, cls: string | null): Row {
  return [key, name || cls || "class not recorded in this projection"];
}

/* The curve, drawn: how a spline becomes a polyline, and how many pieces that is worth.
 *
 * The routes on this map were the shape the save records, joined by straight lines, and those
 * two are not the same thing. A belt or pipe spline stores a tangent either side of every
 * control point, so a run the player laid as an arc is an arc; the projection dropped the
 * tangents until schema 15, so the map drew the chords between the corners -- out by up to
 * 16.4 m of arc on a single belt piece, measured server-side against the length the save
 * itself states for it. `curve_m` carries them now and this is where they become pixels.
 *
 * THE SUBDIVISION IS ZOOM-DEPENDENT, and that is the whole of why this is affordable.
 * Tessellating to a fixed quality would put the maximum number of points on the canvas at the
 * whole-world view, which is precisely the zoom where all 3,085 belt pieces are on screen at
 * once and the frame budget is tightest. Asking instead "how far is this curve from its chord
 * IN PIXELS, right now" collapses every one of them at world scale: measured over the
 * reference world's 6,691 spans, zoom -6 to -1 add **no points at all** -- the layer is the
 * identical geometry it was before this existed -- and even at maxZoom the whole network grows
 * from 6,691 line points to 8,712, a third more.
 *
 * A SPAN WITH NO CURVE IS NEVER TOUCHED. The server sends null for a straight span and null
 * for a route with no bend anywhere in it, so 2,119 of the 3,085 belt pieces and 207 of the 503
 * pipes take exactly the code path they always took, and a straight run is the same two points
 * it has always been at every zoom. That is a structural guarantee rather than a tolerance
 * that happens to round the right way.
 */

/* Half a pixel: below this a bend and the line through it land on the same pixels, so
 * subdividing further buys nothing that can be seen. Not a quarter pixel -- the canvas is not
 * drawing sub-pixel geometry to that accuracy anyway, and the error halves the step count. */
var CURVE_TOLERANCE_PX = 0.5;

/* And a ceiling, because a bound on the work has to come from somewhere other than the data.
 * Eight is generous rather than tight: at maxZoom, cutting the cap from 16 to 8 changes the
 * whole world's line-point count by 58 out of 8,712, because the flatness that drives the step
 * count is a median of 3.9 cm and only 5% of spans exceed 69 cm. It is here for the one
 * pathological span rather than for the common case. */
var CURVE_MAX_STEPS = 8;

/* How far one span's curve can leave the straight line between its ends, in metres.
 *
 * The classic cubic flatness bound, via the Bezier form: a Hermite span's inner control points
 * are `p0 + leave/3` and `p1 - arrive/3`, and the curve stays within three quarters of the
 * further one's distance from the chord. An upper bound, so it can only ever over-subdivide.
 *
 * Measured IN THE PLAN, x and y only, because that is what this map draws -- a belt's climb is
 * not something a top-down view has to resolve, and a conveyor lift is exactly the case where
 * including z would demand eight subdivisions of a run that occupies one pixel. */
function spanFlatnessM(p0: Point3M, p1: Point3M, span: SpanCurveM): number {
  var ax = p0[0];
  var ay = p0[1];
  var vx = p1[0] - ax;
  var vy = p1[1] - ay;
  var chord = Math.sqrt(vx * vx + vy * vy);
  var b1x = ax + span[0][0] / 3;
  var b1y = ay + span[0][1] / 3;
  var b2x = p1[0] - span[1][0] / 3;
  var b2y = p1[1] - span[1][1] / 3;
  if (!(chord > 0)) {
    // Coincident ends -- the joint where a lift meets its belt. There is no chord to measure
    // against, so the control points' own offset is the whole of the departure.
    var d1 = Math.hypot(b1x - ax, b1y - ay);
    var d2 = Math.hypot(b2x - ax, b2y - ay);
    return 0.75 * Math.max(d1, d2);
  }
  var off1 = Math.abs((b1x - ax) * vy - (b1y - ay) * vx) / chord;
  var off2 = Math.abs((b2x - ax) * vy - (b2y - ay) * vx) / chord;
  return 0.75 * Math.max(off1, off2);
}

/* How many straight pieces one span is worth at this scale. 1 means "draw the chord".
 *
 * A cubic subdivided into n uniform pieces has an error of about `flatness / n^2`, so the n
 * that puts that under the tolerance is the square root of the ratio -- which is why a curve
 * ten times bigger costs three times the points and not ten. */
function spanSteps(flat_m: number, ppm: number): number {
  var px = flat_m * ppm;
  if (!(px > CURVE_TOLERANCE_PX)) return 1;
  return Math.min(CURVE_MAX_STEPS, Math.ceil(Math.sqrt(px / CURVE_TOLERANCE_PX)));
}

/* One point along a Hermite span, in game metres.
 *
 * The tangents arrive in the same space and the same units as the points, so this is the plain
 * basis with nothing to correct -- which is exactly what `/api/belts` promises about `curve_m`,
 * and the reason the y-flip below can be applied to the RESULT rather than to the inputs. */
function hermite(p0: Point3M, p1: Point3M, span: SpanCurveM, t: number): PointM {
  var t2 = t * t;
  var t3 = t2 * t;
  var h00 = 2 * t3 - 3 * t2 + 1;
  var h10 = t3 - 2 * t2 + t;
  var h01 = -2 * t3 + 3 * t2;
  var h11 = t3 - t2;
  return [
    h00 * p0[0] + h10 * span[0][0] + h01 * p1[0] + h11 * span[1][0],
    h00 * p0[1] + h10 * span[0][1] + h01 * p1[1] + h11 * span[1][1],
  ];
}

/* A route as the latlngs Leaflet draws, tessellated for the scale given, plus the step counts.
 *
 * `[-y, x]` per point, which is this map's one coordinate convention and is applied here to
 * the tessellated result rather than to the spline -- so the curve is computed in game metres
 * and flipped once, in the same place a straight route's points are flipped. */
function routeLatLngs(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number
): { latlngs: L.LatLngTuple[]; steps: number[] } {
  var latlngs: L.LatLngTuple[] = [[-points_m[0]![1], points_m[0]![0]]];
  var steps: number[] = [];
  for (var i = 0; i < points_m.length - 1; i++) {
    var a = points_m[i]!;
    var b = points_m[i + 1]!;
    var span = curve_m ? curve_m[i] : null;
    var n = span ? spanSteps(spanFlatnessM(a, b, span), ppm) : 1;
    steps.push(n);
    for (var k = 1; k < n; k++) {
      var q = hermite(a, b, span!, k / n);
      latlngs.push([-q[1], q[0]]);
    }
    latlngs.push([-b[1], b[0]]);
  }
  return { latlngs: latlngs, steps: steps };
}

/* One route as a drawn polyline, carrying the spline it was tessellated from.
 *
 * The pair is the point: `setLatLngs` replaces a path's geometry, so re-tessellating at a new
 * zoom needs the SOURCE, and the drawn latlngs are not it -- they are already an approximation,
 * and subdividing them again would converge on that approximation rather than on the curve.
 *
 * A route with no curve at all still gets a `_route`, and it costs nothing: `steps` comes back
 * all ones, so the zoom pass below finds nothing to change and never touches it again. */
function routePolyline(
  points_m: Point3M[],
  curve_m: RouteCurveM,
  ppm: number,
  options: L.PolylineOptions
): L.Polyline {
  var shape = routeLatLngs(points_m, curve_m, ppm);
  var piece = L.polyline(shape.latlngs, options);
  piece._route = { points_m: points_m, curve_m: curve_m, steps: shape.steps };
  return piece;
}

/* Redraw one route for a new scale, and say whether it actually moved.
 *
 * Guarded on the step counts rather than on the zoom, because most pieces do not change at
 * most zoom steps: a run with no bend never changes at all, and a gentle one holds the same
 * subdivision across several steps. On the reference world at world zoom that guard skips
 * every one of the 3,588 routes, which is the pass this has to stay cheap for. */
function retessellate(piece: L.Polyline, ppm: number): boolean {
  var route = piece._route;
  if (!route || !route.curve_m) return false;
  var shape = routeLatLngs(route.points_m, route.curve_m, ppm);
  var same = shape.steps.length === route.steps.length;
  for (var i = 0; same && i < shape.steps.length; i++) same = shape.steps[i] === route.steps[i];
  if (same) return false;
  route.steps = shape.steps;
  piece.setLatLngs(shape.latlngs);
  return true;
}

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
 * A class the docs dump has no entry for gets a `lift` of NULL rather than false, and the
 * page owes it the same ring on the same evidence: not "the dump says it is vertical", but
 * "this route covers no ground, and only a ring can draw that". The popup says which of the
 * two it was, because a reader cannot tell a measured ring from an inferred one by looking.
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
/* The three tier tones are one step of value either side of the middle one, which stays the
 * network's colour and the swatch in the layer control. The step is dE 15.6 between slowest
 * and fastest and it is the page's house step -- the pipes' 15.7 below, the storage pair's
 * 16.7 and the poles' 16.2 all match it. */
var BELTS = declareColours("routes", {
  belts: "#93a5b4",
  "belt slow": "#7f8f9d",
  "belt fast": "#a7b9c7",
  "lift fill": "#252a30", // the hole the ring is drawn around.
});
var BELT_COLOUR = BELTS.belts;
var LIFT_FILL = BELTS["lift fill"];
var BELT_SLOW = BELTS["belt slow"];
var BELT_FAST = BELTS["belt fast"];

/* Tier as value. `items_per_min` is the dump's own figure for the class -- 60, 120, 270,
 * 480, 780 -- so this is a banding of a measurement rather than a parse of "Mk3" out of a
 * display name, and it is the same banding the width step used to make. An unknown tier
 * draws at the middle tone: the darkest would read as Mk1. */
function beltColour(items_per_min: number | null | undefined): string {
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

/* A route's stroke width, in pixels, from its width in the world. The one place the three
 * network layers agree completely: a belt is two metres, a pipe is 1.3 and a wire is 0.2, and
 * past the floor each is drawn at whatever that is worth on screen right now.
 *
 * Exported for `power.ts`, together with the width table below, so that the wires are drawn at
 * their first width by the same expression the zoom pass restyles them with. Two copies of it
 * would be a layer that changed thickness the first time anybody touched the map. */
export function routeWeight(width_m: number, ppm: number): number {
  return Math.max(ROUTE_MIN_PX, width_m * ppm);
}

function beltWeight(ppm: number): number {
  return routeWeight(BELT_WIDTH_M, ppm);
}

function liftRadius(ppm: number): number {
  return Math.max(LIFT_MIN_RADIUS_PX, (BELT_WIDTH_M / 2) * ppm);
}

/* Does this route go anywhere seen from above? The only evidence left when the dump has no
 * entry for a class, and the same evidence the `lift` flag stands on: a lift's points share
 * their x and y exactly — measured server-side, maximum horizontal extent 0.0 m over all 302
 * on the reference world — so a polyline through them draws nothing at all.
 *
 * NOT a point count. A lift arrives as TWO points, one at each end of its rise, so counting
 * them says "this is an ordinary run" about the one shape that cannot be drawn as one.
 *
 * A tenth of a metre because that is what the payload is rounded to: below it there is no
 * horizontal extent left in the numbers to draw with. */
var FLAT_ROUTE_M = 0.1;

function coversGround(points: Point3M[]): boolean {
  var first = points[0];
  if (!first) return false;
  for (var i = 1; i < points.length; i++) {
    var p = points[i]!;
    if (Math.abs(p[0] - first[0]) >= FLAT_ROUTE_M) return true;
    if (Math.abs(p[1] - first[1]) >= FLAT_ROUTE_M) return true;
  }
  return false;
}

/* What the glyph means, and it has three answers because `lift` has three.
 *
 * `true` is a measurement: the server read the docs dump's own native class. `false` is also a
 * measurement, and needs no line — a belt drawn as a line is what a reader already assumes.
 *
 * `null` is the server declining to guess for a class the dump has no entry for, and it says
 * so in `api.py`: "not a lift would be a guess, and the map draws a lift and a belt as
 * different things". The page has to make a drawing decision anyway, so it makes the one the
 * geometry supports, and then SAYS that the shape came from the geometry rather than from a
 * classification. Silence would hand a reader a ring indistinguishable from a measured one. */
function beltKind(b: BeltRow, ring: boolean): string | null {
  if (b.lift === true) return "conveyor lift — vertical, so drawn as a ring";
  if (b.lift === false || b.lift === undefined) return null;
  return ring
    ? "the dump has no entry for this class, so whether it is a lift is unknown — drawn as a ring because this route covers no ground"
    : "the dump has no entry for this class, so whether it is a lift is unknown — drawn as a line because this route covers ground";
}

function beltPopup(b: BeltRow, kind: string | null, first: Point3M, last: Point3M): string {
  return popup([
    titleRow("belt", b.name, b.cls),
    // Said out loud, because the glyph is the one encoding on this map that exists
    // because of a measurement rather than because of a preference.
    ["kind", kind],
    ["rate", b.items_per_min ? b.items_per_min + " items/min at 100%" : null],
    // Travel order, input to output: the projection reverses the save's own output-first
    // storage, so these two rows mean what they say.
    ["from", first[0] + ", " + first[1] + " m"],
    ["to", last[0] + ", " + last[1] + " m"],
    ["rise", Math.round((last[2] - first[2]) * 10) / 10 + " m"],
    ["chain", "#" + b.chain],
  ]);
}

export function drawBelts(data: BeltsResponse): void {
  // Off by default at the whole-world zoom, exactly like `machines` and for the same
  // reason: 3,085 routes across 7 km is a smear. See reveal() in labels.ts.
  // The networks, immediately over the concrete they run on and in the order a reader names
  // them: belts, then pipes, then power. See power.ts for the third.
  var group = layer("belts", false, BELT_COLOUR, [BAND.built, 10, "belts"]);
  var ppm = pixelsPerMetre();
  data.belts.forEach(function (b) {
    var first = b.points_m[0]!;
    var last = b.points_m[b.points_m.length - 1]!;
    var piece: L.Path;
    // An unknown class draws on its own geometry: no horizontal extent means a ring, because
    // a polyline through coincident points draws nothing at all, which is the same reasoning
    // the `lift` flag carries for the classes the dump does know. Without this such a piece
    // was drawn as a zero-length line and was simply absent from the map.
    var ring = b.lift === true || (b.lift === null && !coversGround(b.points_m));
    if (ring) {
      piece = L.circleMarker([-first[1], first[0]], {
        radius: liftRadius(ppm),
        color: beltColour(b.items_per_min),
        weight: 1.5,
        fillColor: LIFT_FILL,
        fillOpacity: 0.9,
      });
    } else if (b.points_m.length < 2) {
      return; // a route with one point is not a route, and this is not a lift
    } else {
      piece = routePolyline(b.points_m, b.curve_m, ppm, {
        color: beltColour(b.items_per_min),
        weight: beltWeight(ppm),
        opacity: 0.85,
      });
    }
    // The join the floor filter uses: a belt is keyed by its CHAIN, which is the unit
    // `/api/floors` reasons about -- consecutive pieces of a chain join at a median 0.00 cm,
    // so a chain is one run and a piece is a fragment of one. The two ends ride along so a
    // connector's glyph can be put on the end that is actually on the floor being looked at.
    piece._floor = { run: { kind: "belt", key: b.chain }, ends: [first, last] };
    piece.bindPopup(beltPopup(b, beltKind(b, ring), first, last)).addTo(group);
  });
  (data.attachments || []).forEach(function (a) {
    if (a.x_m === null || a.y_m === null) return;
    var w = (a.w_m || ATTACHMENT_FALLBACK_M) / 2;
    var l = (a.l_m || ATTACHMENT_FALLBACK_M) / 2;
    var junction = L.polygon(footprintCorners(a.x_m, a.y_m, w, l, a.yaw), {
      color: BELT_COLOUR,
      weight: 1,
      fillColor: BELT_COLOUR,
      fillOpacity: 0.85,
    }).bindPopup(
      popup([
        titleRow("belt part", a.name, a.cls),
        ["facing", a.yaw === null || a.yaw === undefined ? null : Math.round(a.yaw) + "°"],
        ["at", a.x_m + ", " + a.y_m + " m"],
        ["instance", code(a.instance_leaf)],
      ])
    );
    // A splitter is placed like a machine and is listed like one: by instance id, in the
    // band it stands on. It rides in the belts layer and is joined the machines' way, which
    // is exactly the split `/api/floors` makes between its runs and its placements.
    junction._floor = { id: a.instance_leaf, z_m: a.z_m === null ? undefined : a.z_m };
    junction.addTo(group);
  });
  sinkRoutes();
}

/* Static, and next to the pipes on purpose: a belt changes when the player builds one, not
 * when the game autosaves, so both networks are refetched on a switch and left alone in
 * between. The two sit adjacent in the wave because they share the overlay canvas and the
 * sink pass that decides what a click lands on. */
registerFetch<BeltsResponse>({
  wave: "static",
  rank: 30,
  path: "/api/belts",
  label: "belts",
  clears: ["belts"],
  refilters: true,
  draw: drawBelts,
});

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
 * on the width the animation was already showing is what removes that snap.
 *
 * A LAYER NOBODY IS LOOKING AT IS SKIPPED, and that is most zoomends: both of these start
 * unticked, so the measurements above -- 3,933 pieces, 0.6 to 0.7 ms, up to 3 ms -- were being
 * paid on every zoom step of the whole-world view where neither layer was drawn at all, and
 * `retessellate` was rebuilding the geometry of curves nobody could see. The work is not
 * merely deferred: a layer's pieces are styled for the scale they are DRAWN at, so a pass
 * skipped at a zoom the layer was off for is a pass with no output to be wrong.
 *
 * What that costs is one call at the other end -- ticking a layer on at a zoom it was not
 * drawn at has to restyle it, or a belt turned on at the factory view would arrive at the
 * world view's hairline. main.ts's `overlayadd` handler already ran `sinkRoutes` for exactly
 * these two layers, so it is the same handler and the same test, one line longer. */
export function styleRoutes() {
  var ppm = pixelsPerMetre();
  var radius = liftRadius(ppm);
  var alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    var weight = routeWeight(ROUTE_WIDTH_M[name]!, ppm);
    group.eachLayer(function (layer) {
      // Not everything in these groups is drawn GEOMETRY any more: floor mode puts a
      // connector's up/down glyph in the layer its run belongs to, and that glyph is a
      // marker with an icon rather than a path. It has no stroke to size, and asking it for
      // one threw -- so the test is what a piece IS rather than what it is not.
      if (!(layer instanceof L.Path)) return;
      var piece = layer as L.Path & { setRadius?: (r: number) => void };
      // Four kinds of piece share these layers now and only two of them are sized in pixels:
      // a lift's ring by its radius, a run by its weight. A splitter is a polygon in map
      // units and is already the right size at every zoom -- exactly like a machine, which is
      // the whole reason it is drawn as one. A chevron is map units too, so what changes for
      // it is not its size but whether it is drawn at all: it is the one piece here that has
      // a zoom BELOW which it is noise rather than information.
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      // A round glyph, and the branch has two answers now. A lift's ring is a two-metre belt
      // seen end-on, so it is sized from the scale like everything else here. A power pole's
      // disc is not a size at all -- it is a MARK, the same grammar the node dots use, drawn
      // at a fixed pixel radius that says which mark it is -- so it is left exactly as it was
      // created. `_fixed` is the piece saying which of the two it is, rather than this pass
      // guessing from a layer name.
      else if (piece.setRadius) {
        if (!piece._fixed) piece.setRadius(radius);
      } else if (!(piece instanceof L.Polygon)) {
        piece.setStyle({ weight: weight });
        // And the geometry, not just the stroke: a curve is subdivided for the scale it is
        // seen at, so the zoom that changes the width is the zoom that changes how many
        // pieces the bend is worth. Skipped for everything straight and for everything whose
        // subdivision has not moved, which at world zoom is the entire layer -- see
        // retessellate.
        retessellate(piece as L.Polyline, ppm);
      }
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
var PIPE_COLOUR = declareColours("routes", { pipes: "#a8613c" }).pipes;

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
var PIPE_TIER = declareColours("routes", { "pipe mk1": "#944d28", "pipe mk2": "#bc7550" });
var PIPE_MK1 = PIPE_TIER["pipe mk1"];
var PIPE_MK2 = PIPE_TIER["pipe mk2"];

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
function pipeColour(flow_m3_min: number | null | undefined): string {
  if (!flow_m3_min) return PIPE_COLOUR;
  return flow_m3_min >= 600 ? PIPE_MK2 : PIPE_MK1;
}

/* A pipe is 1.3 m across, and like the belt's two metres it is a CONSTANT rather than a
 * field: the save carries a centre line and no bore, and both tiers ride the same frame.
 * Narrower than a belt, so it reaches ROUTE_MIN_PX about a zoom step sooner -- which is the
 * true picture, a pipe IS thinner than a belt, and the floor is what keeps it from being
 * nothing at all at the whole-world view. */
var PIPE_WIDTH_M = 1.3;

function pipeWeight(ppm: number): number {
  return routeWeight(PIPE_WIDTH_M, ppm);
}

/* What the popup says about a direction, keyed by what the server based it on. Short on
 * purpose: the row it replaces was a sentence explaining why there was nothing to say, and
 * the whole point of the change is that on 365 of 503 pipes there now is. The `from` and
 * `to` rows carry the direction itself, exactly as they do on a belt, so this row only has
 * to carry the WARRANT.
 *
 * EXHAUSTIVE, which is what `Record<PipeFlowBasis, …>` buys and `Record<string, …>` did not.
 * The fourth key is `unresolved`, and it is in the table rather than left to fall through a
 * `|| "→ inferred"`: that fallback was the one place a basis nobody had thought about could
 * arrive and be printed as a claim about the network. Now a fifth basis in
 * `domain/world/flow.py` is a missing key here and a compile error, and `unresolved` says the
 * honest thing -- which is the sentence below, kept for the 138 the network genuinely does
 * not settle: a pipe in a loop, or a trunk with producers and consumers on both sides. */
var PIPE_FLOW_UNKNOWN = "not recorded, and the network does not imply it";

var PIPE_FLOW_BASIS: Record<PipeFlowBasis, string> = {
  "machine port": "→ a typed machine port at one end",
  pump: "→ pump orientation",
  propagated: "→ inferred from the network",
  unresolved: PIPE_FLOW_UNKNOWN,
};

function pipePopup(p: PipeRow, first: Point3M, last: Point3M): string {
  var known = p.direction === "forward" || p.direction === "reverse";
  var head = p.direction === "reverse" ? last : first;
  var tail = p.direction === "reverse" ? first : last;
  return popup([
    titleRow("pipe", p.name, p.cls),
    // The thing a belt cannot say. It comes off the game's own FGPipeNetwork rather than
    // from what the pipe is plugged into, which is why it can be stated flatly.
    ["fluid", p.fluid_name],
    ["capacity", p.flow_m3_min ? p.flow_m3_min + " m³/min at 100%" : null],
    // The table answers for all four bases now, so `known` decides only whether a resolved
    // basis is trusted -- a `forward` with an `unresolved` basis cannot happen (the resolver
    // sends the two together) and would print the refusal rather than an invented warrant.
    ["flow", known ? PIPE_FLOW_BASIS[p.basis] : PIPE_FLOW_UNKNOWN],
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
var CHEVRON_COLOUR = declareColours("routes", { chevrons: "#e8cbb4" }).chevrons;
var CHEVRON_OPACITY = 0.7;

function chevronOpacity(ppm: number): number {
  return CHEVRON_LENGTH_M * ppm >= CHEVRON_MIN_PX ? CHEVRON_OPACITY : 0;
}

/* Where the chevrons go on one route, in world metres, as [[x, y], [x, y], [x, y]] apexes.
 *
 * Takes a polyline and a flag rather than a pipe, so it knows nothing about plumbing: a belt
 * has a direction too -- its points are already in travel order -- and could be marked by
 * this same function tomorrow. See ROUTE_CHEVRONS for why it is not being marked today. */
/** One straight leg of a route, with where along the whole route it starts. */
interface Run {
  x: number;
  y: number;
  ux: number;
  uy: number;
  d: number;
  at: number;
}

function routeChevrons(points_m: Point3M[], reverse: boolean): PointM[][] {
  var pts = reverse ? points_m.slice().reverse() : points_m;
  var runs: Run[] = [];
  var total = 0;
  for (var i = 1; i < pts.length; i++) {
    var dx = pts[i]![0] - pts[i - 1]![0];
    var dy = pts[i]![1] - pts[i - 1]![1];
    var d = Math.sqrt(dx * dx + dy * dy);
    if (!(d > 0)) continue;
    runs.push({ x: pts[i - 1]![0], y: pts[i - 1]![1], ux: dx / d, uy: dy / d, d: d, at: total });
    total += d;
  }
  if (!runs.length || total < CHEVRON_MIN_RUN_M) return [];
  var marks: PointM[][] = [];
  var n = Math.max(1, Math.floor(total / CHEVRON_SPACING_M));
  for (var k = 0; k < n; k++) {
    var along = ((k + 0.5) / n) * total;
    var run = runs[runs.length - 1]!;
    for (var j = 0; j < runs.length; j++) {
      if (along <= runs[j]!.at + runs[j]!.d) {
        run = runs[j]!;
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

export function drawPipes(data: PipesResponse): void {
  // Off by default at the whole-world zoom, exactly like `belts` and `machines`. See reveal() in labels.ts.
  // Directly under the belts, which is the pair they are: two networks in one grammar. See
  // drawBelts for the band.
  var group = layer("pipes", false, PIPE_COLOUR, [BAND.built, 20, "pipes"]);
  var ppm = pixelsPerMetre();
  var alpha = chevronOpacity(ppm);
  data.pipes.forEach(function (p) {
    if (p.points_m.length < 2) return; // a route with one point is not a route
    var first = p.points_m[0]!;
    var last = p.points_m[p.points_m.length - 1]!;
    var run = routePolyline(p.points_m, p.curve_m, ppm, {
      color: pipeColour(p.flow_m3_min),
      weight: pipeWeight(ppm),
      opacity: 0.85,
    }).bindPopup(pipePopup(p, first, last));
    // `row`, not this list's index: it is the pipe's position in the RAW table, which is
    // what `/api/floors` keys a pipe run by and what stays right when a row is torn.
    run._floor = { run: { kind: "pipe", key: p.row }, ends: [first, last] };
    run.addTo(group);
    if (!ROUTE_CHEVRONS.pipes) return;
    if (p.direction !== "forward" && p.direction !== "reverse") return;
    routeChevrons(p.points_m, p.direction === "reverse").forEach(function (mark) {
      var piece = L.polyline(
        mark.map(function (q): L.LatLngTuple {
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
      // Marked with its pipe's key and with no ends, so a floor filter keeps a direction
      // mark exactly when it keeps the pipe under it -- and never mistakes the mark for a
      // run whose end could carry a connector glyph.
      piece._floor = { run: { kind: "pipe", key: p.row } };
      piece.addTo(group);
    });
  });
  sinkRoutes();
}

/** The belts' twin; see the note on that registration for why both are static. */
registerFetch<PipesResponse>({
  wave: "static",
  rank: 40,
  path: "/api/pipes",
  label: "pipes",
  clears: ["pipes"],
  refilters: true,
  draw: drawPipes,
});

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
 *
 * A POWER POLE IS THE SAME CASE AS A SPLITTER and is in that same middle bucket, which is
 * what `_fixed` buys here beyond the restyle above: a pole is a 3 px disc sitting where four
 * wires meet, so a pole sunk with the runs would be a mark under every line it terminates and
 * a popup nobody can open. Partitioned on the mark rather than on the draw order, because
 * draw order is `power.ts`'s business and this rule is not.
 */
export var ROUTE_LAYERS = ["belts", "pipes", "power"];

/* What each route layer is worth in metres. The one place the three differ, so the one place
 * the shared passes above have to look.
 *
 * A power wire's 0.2 m is the thinnest thing on this map and is a real measurement rather than
 * a taste: a wire is a cable, not a conveyor. It reaches ROUTE_MIN_PX two zoom steps before a
 * pipe does, which is the whole reason the floor exists -- past it every wire in the world is
 * the same hairline, and inside a factory it is visibly finer than the belts it runs beside. */
export var ROUTE_WIDTH_M: Record<string, number> = {
  belts: BELT_WIDTH_M,
  pipes: PIPE_WIDTH_M,
  power: 0.2,
};

/* Which route layers carry direction chevrons. A BELT HAS A DIRECTION TOO -- its points are
 * in travel order, which is the one thing the belts have always been able to say and the
 * pipes could not -- and `routeChevrons` above takes a polyline and a flag precisely so that
 * turning this to `true` is the whole of the work. It stays `false` because that is a
 * decision about the map, not about the data: 3,085 belt runs would put some 2,500 more
 * marks on the same canvas as these 412, and the owner asked for the pipes. A flag rather
 * than an absence, so the next person finds a switch instead of a rewrite. */
var ROUTE_CHEVRONS: Record<string, boolean> = { belts: false, pipes: true };

export function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var chevrons: L.Path[] = [];
    var glyphs: L.Path[] = [];
    var runs: L.Path[] = [];
    group.eachLayer(function (layer) {
      // Paths only, for the reason styleRoutes states: a floor connector's glyph is a marker
      // and lives in the marker pane, which is above this canvas and is not part of the
      // stacking question this pass answers.
      if (!(layer instanceof L.Path)) return;
      var piece = layer as L.Path;
      (piece._chevron
        ? chevrons
        : piece instanceof L.Polygon || piece._fixed
          ? glyphs
          : runs
      ).push(piece);
    });
    // Sunk FIRST is left highest, per the note above, so the chevrons go before the glyphs
    // and the runs: a direction mark under the line it marks would not be a mark.
    chevrons
      .concat(glyphs)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}
