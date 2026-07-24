/* What the API sends, under the names the page already used -- one line per shape.
 *
 * THE POINT OF THIS FILE IS THAT IT HAS NO FIELDS IN IT. Every type below resolves to a
 * component of `api-schema.d.ts`, which is generated from the server's own `/openapi.json`,
 * which is generated from the `response_model` TypedDicts on the routers. A field spelled
 * here would be a hand-written copy of a generated type -- the second place to update, and
 * always the one that goes stale, which is exactly what `api-types.ts` was and why it is
 * being emptied.
 *
 * SO WHY NOT IMPORT `components` DIRECTLY, as floors.ts does? Because the page's names are
 * the page's. `components["schemas"]["NodesResponse"]` in fifteen call sites would put the
 * generator's addressing scheme into every drawing module, and the next endpoint converted
 * would be a rename across all of them. One indirection means a converted endpoint changes
 * one line in one file. floors.ts predates this and reaches into the schema itself; it is
 * the older arrangement rather than the intended one.
 *
 * `ApiError` is the one thing here the server does not describe. FastAPI publishes no schema
 * for the `{"error": "..."}` a 4xx carries -- an endpoint that fails returns a JSONResponse
 * and skips its own response model -- so the error branch is the frontend's claim, declared
 * in `api-types.ts` beside the other claims, and joined on here. `get()` in api.ts is
 * generic over `T extends ApiError`, and `ApiError` has only optional members, so a body
 * type with no `error` field at all is not assignable to it: the intersection is what makes
 * these usable as response types rather than decoration.
 *
 * NOT EVERYTHING IS HERE YET. `api-types.ts` still declares the payloads of the endpoints
 * that have no response model, and the page still imports those from there. This file grows
 * by one line per endpoint converted; that file shrinks by one block.
 */

import type { components } from "./api-schema";
import type { ApiError } from "./api-types";

type Schema = components["schemas"];

/** A response BODY: the server's own schema for it, plus the error branch any reply may
 *  carry instead. Used for the shapes a `get()` or a `registerFetch()` is typed by. */
type Body<K extends keyof Schema> = Schema[K] & ApiError;

/* ----------------------------------------------------------------- /api/nodes */

export type NodeRow = Schema["NodeRow"];
export type NodesResponse = Body<"NodesResponse">;

/* --------------------------------------------------------------- /api/inspect */

export type Elevation = Schema["Elevation"];
export type NearestNode = Schema["NearestNode"];
export type InspectResponse = Body<"InspectResponse">;

/* --------------------------------------------------------------- /api/regions */

export type RegionsResponse = Body<"RegionsResponse">;

/* --------------------------------------------------------------- /api/summary */

/** Its `header` is an open map, deliberately: see `SummaryResponse` in routers/world.py.
 *  `player` is always sent and its three fields are what go null; markers.ts branches on
 *  that, and reads the branch off this type as `SummaryResponse["player"]`. */
export type SummaryResponse = Body<"SummaryResponse">;

/* ------------------------------------------------------------------ both, and shared */

/** A region lookup, hung on a node row and answered for an inspected point. Declared once
 *  on the server too -- in `serial.py`, for the same reason it is one name here. */
export type Region = Schema["Region"];
