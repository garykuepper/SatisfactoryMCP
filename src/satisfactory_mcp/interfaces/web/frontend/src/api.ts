/* The fetch layer: one function, and the two query parameters every endpoint takes.
 *
 * Worth its own module because `world` and `save` are not per-call arguments -- they are the
 * page's current selection, and a caller that spelled them itself would be a caller that can
 * drift from the picker.
 *
 * SIX CALLS DO NOT COME THROUGH HERE, and they are listed rather than counted because the
 * count was wrong: this note used to say "two loaders", which was true of the page before it
 * grew a base map. Each one is a request `get` could not have made, for a stated reason:
 *
 *   * `/api/regions` (load.ts) -- geography, not save state. There is no world to scope it
 *     to, and it is fetched once for the life of the page rather than per switch.
 *   * `/api/worlds` (worlds.ts, twice: the first load and every rescan) -- the world LIST is
 *     what decides `state.world`, so scoping it by `state.world` would be circular.
 *   * `/api/maptiles/{layer}/0/0/0` and `/api/mapimage` (tiles.ts, both HEAD) -- probes that
 *     read HEADERS and have no body at all. `get` parses JSON and throws on `body.error`,
 *     and a 204 with nothing in it is this page's ordinary "that picture was never
 *     generated" answer rather than a failure.
 *
 * ...plus one that is not a `fetch` at all: `new EventSource("/api/events")` in sse.ts. A
 * stream is not a request-and-reply, it carries no world parameter, and it is the one
 * connection on the page that outlives every world switch.
 *
 * The two picture URLs Leaflet builds -- the tile pyramid's `{z}/{x}/{y}` template and the
 * single `/api/mapimage` overlay -- are fetched by the BROWSER as images, so they never touch
 * `fetch` either. `tilePath` below is the one place their path is spelled, which is what
 * keeps the probe and the pyramid asking about the same layer.
 *
 * `get` is generic over the response, and the caller supplies the type because only the
 * caller knows which endpoint it asked. Where that type comes FROM is now two places and
 * moving: api-shapes.ts for the endpoints that declare a `response_model` (the server's own
 * schema, generated), api-types.ts for the ones still annotated `-> Any` (the frontend's
 * claim, observed). What has always been taken from the generated schema is the path --
 * `ApiPath` below is the union of the paths the server actually serves, so a typo in a URL
 * is a compile error rather than a toast at runtime.
 */

import type { paths } from "./api-schema";
import type { ApiError } from "./api-types";
import { state } from "./state";

/** Every path the server serves, straight out of its own OpenAPI document. */
export type ApiPath = keyof paths;

/* The base layers `/api/maptiles/{layer}/…` serves, as the frontend's claim.
 *
 * Hand-written for the same reason the response shapes in api-types.ts are: the generated
 * schema cannot supply it. `layer` is a plain `str` path parameter, so `api-schema.d.ts`
 * types it `string` -- the names live in `MAP_LAYERS` in routers/tiles.py, which the document
 * never sees, and an unknown one comes back as a 404 listing the real ones rather than as a
 * 422 about a parameter.
 *
 * So the check is by construction: this union is declared once, `tilePath` is the only place
 * a tile URL is built, and `ModeSpec.layer` in tiles.ts is typed by it -- a mode naming a
 * layer this server does not serve is a compile error rather than a silent 404 per tile.
 * test_architecture.py pins the union against `MAP_LAYERS` itself, which is the half a type
 * cannot do from this side of the wire. */
export type MapTileLayer = "map" | "terrain" | "satellite";

/* One tile's path, or the template Leaflet fills in.
 *
 * The coordinates are `string | number` precisely so that both callers go through here: the
 * probe asks for `0, 0, 0` and the TileLayer asks for `"{z}", "{x}", "{y}"`, and a builder
 * that took only numbers would have left the template spelled by hand somewhere else. */
export function tilePath(
  layer: MapTileLayer,
  z: string | number,
  x: string | number,
  y: string | number
): string {
  return "/api/maptiles/" + layer + "/" + z + "/" + x + "/" + y;
}

/* A path plus a query string, which is what two callers pass: `/api/inspect` takes a
 * coordinate and `/api/collectibles` takes a mode, and both are spelled inline at the call
 * site rather than plumbed through here. The template keeps the path half checked.
 *
 * Exported because the fetch registry stores paths rather than calls: a `Fetcher` names the
 * URL it wants and load.ts is what passes it to `get`, so the type has to travel with it or
 * the compile-time check on every registered path is lost. See registry.ts. */
export type ApiUrl = ApiPath | `${ApiPath}?${string}`;

export function get<T extends ApiError>(path: ApiUrl): Promise<T> {
  var q = "";
  var sep = path.indexOf("?") < 0 ? "?" : "&";
  if (state.world) {
    q += sep + "world=" + encodeURIComponent(state.world);
    sep = "&";
  }
  if (state.save) {
    q += sep + "save=" + encodeURIComponent(state.save);
  }
  return fetch(path + q).then(function (r) {
    return r.json().then(function (body: T) {
      if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
      return body;
    });
  });
}
