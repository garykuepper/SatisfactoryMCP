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
 * `ApiError` is the one thing joined on here that the server does not describe, and it lives
 * in api.ts -- the module that turns it into a throw -- with the reason it can never come
 * from the schema. `get()` there is generic over `T extends ApiError`, and `ApiError` has
 * only optional members, so a body type with no `error` field at all is not assignable to
 * it: the intersection below is what makes these usable as response types rather than
 * decoration.
 *
 * EVERYTHING IS HERE NOW, which is a change of tense and the end of the conversion.
 * `api-types.ts` is gone: every endpoint that had a hand-written payload in it declares a
 * `response_model` and appears below instead. The one exception is `/api/worlds`, which is
 * DEFERRED rather than converted -- it forwards opaque save headers, so a response model is
 * either useless or lossy -- and its rows are the page's own claim, declared in state.ts
 * where they are stored and wrapped in worlds.ts where they are fetched.
 *
 * WHAT IS NOT HERE, and is not missing. The route SHAPES -- `PointM`, `Point3M`, `BboxM`,
 * `SpanCurveM`, `RouteCurveM`, `RouteShape` -- are in geometry.ts, which imports nothing:
 * the server describes the same structures inside the payloads below, but hermite() takes a
 * point rather than a payload and `RouteShape` is a thing the page builds. And `/api/crates`,
 * which the server describes in full, has no line here because no module draws it yet -- a
 * re-export nobody imports is a name to keep in step for no reader, and the first crate popup
 * adds it as one line.
 */

import type { components } from "./api-schema";
import type { ApiError } from "./api";

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

/* -------------------------------------------- /api/machines and /api/structures */

export type PlacementRow = Schema["PlacementRow"];
export type MachinesResponse = Body<"MachinesResponse">;
export type StructureRow = Schema["StructureRow"];
export type StructuresResponse = Body<"StructuresResponse">;

/* ------------------------------------------------ /api/belts and /api/pipes */

export type BeltRow = Schema["BeltRow"];
export type AttachmentRow = Schema["AttachmentRow"];
export type BeltsResponse = Body<"BeltsResponse">;

export type PipeRow = Schema["PipeRow"];
export type PipesResponse = Body<"PipesResponse">;

/** The two closed vocabularies `/api/pipes` publishes, read off the row's own fields rather
 *  than restated. `PIPE_FLOW_BASIS` in routes.ts is a `Record` keyed by the second, so a
 *  fifth basis in `domain/world/flow.py` is still a missing key and a compile error here --
 *  which is what these being unions rather than `string` is for, and why the server
 *  declares them as `Literal`s. */
export type PipeDirection = PipeRow["direction"];
export type PipeFlowBasis = PipeRow["basis"];

/* --------------------------------------------------------------- /api/storage */

export type StoredItem = Schema["StoredItem"];

/** A container or a fluid buffer, discriminated by `kind`. TWO shapes on the server and
 *  two here: the other kind's fields are ABSENT rather than null, so a reader branches on
 *  `kind` and gets the half it is looking at with every field required -- see the module
 *  docstring in routers/storage.py for why that is not one model with optional halves. */
export type StorageRow = Schema["StorageSolid"] | Schema["StorageFluid"];
export type StorageResponse = Body<"StorageResponse">;

/* ----------------------------------------------------------------- /api/power */

/** `cls` and `name` are nullable and the three coordinates are not, which is the server's
 *  own reading of the two halves: a pole is decoded out of an INTERNED table, so its class is
 *  an index into a legend that can point past the end, while `iter_power_poles` DROPS a row
 *  whose position will not read. popup() drops a null title row -- see `titleRow` in
 *  routes.ts for what is printed instead. */
export type PoleRow = Schema["PoleRow"];

/** Its ends are `[number, number, number]` rather than `number[]`, because the router spells
 *  them as tuples and typegen carries `prefixItems` through -- so `w.a_m[2]` needs no length
 *  guard. `from`/`to` are null for the 40 endpoints of 2,594 that land on an actor no record
 *  list names. */
export type WireRow = Schema["WireRow"];

export type PowerResponse = Body<"PowerResponse">;

/* ------------------------------------------------------------- /api/factories */

export type FactoryRow = Schema["FactoryRow"];
export type ProposalRow = Schema["ProposalRow"];
export type FactoriesResponse = Body<"FactoriesResponse">;

/* ---------------------------------------------------------- /api/collectibles */

export type CollectibleRow = Schema["CollectibleRow"];

/** Seven keys, where the hand-written twin declared one. `rows` is still all markers.ts
 *  reads, but a response model FILTERS, so the server had to declare the whole payload --
 *  and `mode` arrived a closed union of the four `collect_view` accepts. */
export type CollectiblesResponse = Body<"CollectiblesResponse">;

/* ------------------------------------------------------------------ both, and shared */

/** A region lookup, hung on a node row and answered for an inspected point. Declared once
 *  on the server too -- in `serial.py`, for the same reason it is one name here. */
export type Region = Schema["Region"];
