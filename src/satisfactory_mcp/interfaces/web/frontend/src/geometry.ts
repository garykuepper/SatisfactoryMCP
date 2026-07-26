/* The page's own words for the things it draws with. Not payloads, and that is the point.
 *
 * These six outlived `api-types.ts`, which was an observation of endpoints that published
 * no schema and died when the last of them published one. They were never in that file for
 * the same reason as the rest of it: the server does describe the same STRUCTURES inside its
 * payloads -- a `points_m` is a `[number, number, number][]` either way, because the routers
 * spell those tuples as tuples -- but a type is not only its shape. `hermite()` takes a
 * POINT rather than a payload; `spanFlatnessM()` measures a span rather than a response; and
 * `RouteShape` is a thing the page BUILDS and hangs on a polyline, which no endpoint sends
 * at all. Naming them off the wire would say they came from there.
 *
 * THIS MODULE IMPORTS NOTHING, and unlike `state.ts` and `registry.ts` -- which say the same
 * sentence about runtime evaluation order -- here it is the claim itself. A module with no
 * imports cannot be a description of somebody else's data. Everything in it is a type, so
 * the transpiler erases the file whole and it is in no bundle.
 */

/** A point in game metres, `[x, y]`. Latitude is `-y`; see `xy` in map.ts. */
export type PointM = [number, number];

/** A point with its height, `[x, y, z]` -- what the route splines carry. */
export type Point3M = [number, number, number];

/** `[x_min, y_min, x_max, y_max]`, game axes. The y ends swap on the way to Leaflet. */
export type BboxM = [number, number, number, number];

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
