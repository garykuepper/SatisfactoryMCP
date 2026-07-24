/* What the API sends, for the endpoints that do not yet say so themselves.
 *
 * A SHRINKING FILE. `api-schema.d.ts` is generated from the server's own `/openapi.json`,
 * and every router that declares a `response_model` has its whole body described there;
 * `api-shapes.ts` re-exports those components under the names this page uses. What is left
 * below is the endpoints still annotated `-> Any`, which publish no response schema at all,
 * so the generated file types their `200` as `unknown` and something has to fill the gap.
 *
 * It matters where these came from: they were read off real payloads from a real save, not
 * from the server's source. That makes them an observation, and an observation can be wrong
 * in one specific direction -- a field that is always populated in the world it was read
 * from can be null in another. So every field the drawing code already guards against is
 * declared nullable here even where the sample had a value, because the guard IS the
 * evidence: `m.clock === null ? null : ...` in placements.ts is the page saying it has seen
 * a null clock, and this file should not contradict it.
 *
 * That rule has a second edge, and it is the one this file kept losing: a `| null` nobody can
 * produce is as wrong as a missing one. It makes the drawing code carry a branch for a value
 * the server has no way to send, and the branch is then untestable and untested -- so it is
 * where the wrong fallback hides. Every nullable below has been read back against the
 * expression in the router that fills it, and the ones that could not be null say WHICH
 * expression -- which is also what the conversion transcribes from, one endpoint at a time.
 *
 * `ApiError` is the exception that will outlive the rest: FastAPI publishes no schema for the
 * `{"error": ...}` a 4xx carries, because a failing handler returns a JSONResponse and skips
 * its own response model. It is the frontend's claim about the whole surface and stays a
 * claim; `api-shapes.ts` joins it onto every generated body.
 *
 * `WorldsResponse` is the awkward one and it is deliberate, not pending. `/api/worlds`
 * forwards the loader's own dicts -- a thirteen-key save header per save -- so a faithful
 * response model there is `dict[str, Any]`, which says nothing, and a useful one deletes
 * eight keys from every row, which is a change to the body. See the comment above `worlds()`
 * in routers/world.py. Converting it means changing what it SENDS.
 *
 * The route SHAPES at the top are the other thing that stays, and they are not payloads at
 * all: `PointM`, `Point3M`, `BboxM`, `SpanCurveM` and `RouteCurveM` are the page's own words
 * for the tuples the server sends -- structurally the same types `api-schema.d.ts` generates,
 * named here so that hermite() and spanFlatnessM() can say what they take -- and `RouteShape`
 * is a thing the page BUILDS and hangs on a polyline, which no server describes. They belong
 * to the drawing code, so they outlive the observations around them.
 */

/** A point in game metres, `[x, y]`. Latitude is `-y`; see `xy` in map.ts. */
export type PointM = [number, number];

/** A point with its height, `[x, y, z]` -- what the route splines carry. */
export type Point3M = [number, number, number];

/** `[x_min, y_min, x_max, y_max]`, game axes. The y ends swap on the way to Leaflet. */
export type BboxM = [number, number, number, number];

/* ---------------------------------------------------- the page's own route shapes */

/** The tangents that bend one span of a route, `[leave, arrive]` in game metres.
 *
 * `leave` is the tangent leaving the point behind the span and `arrive` the tangent arriving
 * at the point ahead of it, which is the pair a cubic Hermite between those two points takes.
 * They are displacements in the same space as `points_m`, so whatever transform a client
 * applies to a point applies to these unchanged -- see hermite() in routes.ts. */
export type SpanCurveM = [Point3M, Point3M];

/** A route's curve, one entry per span, in step with `points_m`.
 *
 * `null` in a slot means that span is straight and is drawn as the line it already was.
 * `null` for the whole field means the route has no bend anywhere in it, or the projection
 * predates schema 15 -- both of which mean the same thing to a client, which is why they are
 * spelled the same way. */
export type RouteCurveM = (SpanCurveM | null)[] | null;

/** What a drawn route was built from: enough to draw it again at a different scale.
 *
 * Hung on the polyline itself, because tessellation is not reversible -- the latlngs on a
 * drawn curve are already subdivided, and subdividing those again would smooth the curve
 * towards its own approximation rather than towards the spline. */
export interface RouteShape {
  points_m: Point3M[];
  curve_m: RouteCurveM;
  /** How many pieces each span was last cut into, so an unchanged zoom step does no work. */
  steps: number[];
}

/* ------------------------------------------------------------------- rows */

/** A power pole, wall outlet or tower platform.
 *
 * `cls` and `name` are nullable for one reason, and it used to be stated on `BeltRow` above --
 * which is now the server's, in `routers/routes_layer.py`. A pole is decoded out of an
 * INTERNED table, so its class is an index into a legend and `saveio.rows` answers `None` for
 * an index past the end; `GameData.building_name` is `None` in, `None` out, so a torn row
 * reaches the page with no title at all and `name || cls` is `null`. popup() drops a null row,
 * which would silently delete the one row naming the thing the reader just clicked -- see
 * `titleRow` in routes.ts for what is printed instead.
 *
 * The three coordinates are NOT nullable: `/api/power` builds them from
 * `saveio.rows.iter_power_poles`, which drops a row whose position will not read, so a pole
 * that reaches this page has one.
 *
 * `connections` is a count off `graph["power"]` and is never null -- a pole nothing is wired to
 * reports 0, which is a measurement: the pole is in the geometry table and in no edge. */
export interface PoleRow {
  cls: string | null;
  name: string | null;
  x_m: number;
  y_m: number;
  z_m: number;
  yaw: number | null;
  connections: number;
}

/** One power wire, as the straight line between the two connectors it is strung between.
 *
 * `from` and `to` are the buildings at each end, in the same order as the two points, and are
 * null where the projection carries no record naming that actor -- 40 of the reference world's
 * 2,594 endpoints, which land on a hypertube entrance, a drop pod or the AWESOME Sink.
 *
 * `span_m` is the three-dimensional CHORD, which is what the save records too: a wire hangs as
 * a catenary and nothing in the file carries its sag. */
export interface WireRow {
  a_m: Point3M;
  b_m: Point3M;
  from: string | null;
  to: string | null;
  span_m: number;
}

export interface FactoryRow {
  name: string;
  centroid_m: PointM;
  bbox_m: BboxM | null;
  machines: number;
  /** Never null: `Label.notes` is `str = ""` in the label store, so an unannotated factory
   *  sends the empty string -- which popup() drops for the same reason it drops a null. */
  notes: string;
}

export interface ProposalRow {
  index: number;
  label: string;
  centroid_m: PointM;
  bbox_m: BboxM | null;
  machines: number;
  score: number;
  spread_m: number;
}

export interface CollectibleRow {
  category: string;
  name: string;
  x_m: number;
  y_m: number;
  z_m: number;
  collected: boolean;
  observed: string | null;
  distance_m: number | null;
}

export interface SaveRow {
  path: string;
  filename: string;
  session_name: string;
  play_duration_s: number;
  mtime_ns: number;
}

export interface WorldRow {
  world_id: string;
  session_name: string;
  saves: SaveRow[];
  mtime: number;
  newest_filename: string;
  play_duration_s: number;
}

/* -------------------------------------------------------------- responses */

/** Every response may carry this instead of its payload; `get()` turns it into a throw. */
export interface ApiError {
  error?: string;
}

/** `edge_count` is the one field here that is not a length of a list beside it.
 *
 * It is how many power EDGES the projection holds, and `wire_count` how many of them published
 * a span. They are equal on every save cut by a sidecar new enough to read the geometry, and a
 * projection from an older one answers with every edge and no wire at all -- so the pair is the
 * page's evidence for "there is nothing to draw" against "there is nothing here". */
export interface PowerResponse extends ApiError {
  poles: PoleRow[];
  pole_count: number;
  wires: WireRow[];
  wire_count: number;
  edge_count: number;
}

export interface FactoriesResponse extends ApiError {
  labels: FactoryRow[];
  proposals: ProposalRow[];
}

export interface CollectiblesResponse extends ApiError {
  rows: CollectibleRow[];
}

/* Both fields are always sent together, exactly like every other response above: `/api/worlds`
 * ends `return {"worlds": rows, "unsupported": list(unsupported)}`, and the only path that
 * omits them is the one that sends `error` instead -- which is the same bargain `nodes`,
 * `belts` and the rest already make by declaring their payload required. Optional markers here
 * bought nothing and cost two `|| []` guards in worlds.ts that read as evidence of a null. */
export interface WorldsResponse extends ApiError {
  worlds: WorldRow[];
  unsupported: { filename: string; reason: string }[];
}
