/* What the API actually sends, as the frontend's claim rather than the API's.
 *
 * `api-schema.d.ts` is generated from the server's own `/openapi.json` and is the authority
 * for the things FastAPI knows: which paths exist, which query parameters each takes, and
 * what a validation error looks like. It is NOT the authority for most response bodies,
 * because almost every endpoint in `api.py` is annotated `-> dict`, and a bare dict carries
 * no schema at all -- so the generated file types those responses as `unknown`.
 *
 * The interfaces below fill that gap, and it matters where they came from: they were read
 * off real payloads from a real save, not from the server's source. That makes them an
 * observation, and an observation can be wrong in one specific direction -- a field that is
 * always populated in the world it was read from can be null in another. So every field the
 * drawing code already guards against is declared nullable here even where the sample had a
 * value, because the guard IS the evidence: `m.clock === null ? null : ...` in placements.ts
 * is the page saying it has seen a null clock, and this file should not contradict it.
 *
 * That rule has a second edge, and it is the one this file kept losing: a `| null` nobody can
 * produce is as wrong as a missing one. It makes the drawing code carry a branch for a value
 * the server has no way to send, and the branch is then untestable and untested -- so it is
 * where the wrong fallback hides. Every nullable below has now been read back against the
 * expression in `api.py` that fills it, and the ones that could not be null say WHICH
 * expression, so the next reader checks the server rather than guessing from a sample again.
 *
 * They are deliberately not exhaustive. `/api/summary` returns a large object of which this
 * page reads four branches, and typing the other twenty would be inventing a contract for
 * data nothing here looks at. What is declared is what is read.
 *
 * The right fix is response models on `api.py`, which would make this file generated too.
 * That is a change to the server's public surface and belongs in its own commit -- but it
 * has started. `/api/floors` declares one, so its body is in `api-schema.d.ts` as
 * `FloorsResponse` and there is deliberately NO floors interface below: a hand-written copy
 * of a generated type is the second place to update, and it is always the one that goes
 * stale. Anything converted after it should leave this file the same way.
 */

/** A point in game metres, `[x, y]`. Latitude is `-y`; see `xy` in map.ts. */
export type PointM = [number, number];

/** A point with its height, `[x, y, z]` -- what the route splines carry. */
export type Point3M = [number, number, number];

/** `[x_min, y_min, x_max, y_max]`, game axes. The y ends swap on the way to Leaflet. */
export type BboxM = [number, number, number, number];

/** A region lookup. `confidence` is never dropped: the raster is 256 m per cell. */
export interface Region {
  name: string;
  confidence: string;
  accuracy_m: number;
  certain: boolean;
  text: string;
}

/* ------------------------------------------------------------------- rows */

export interface NodeRow {
  id: string;
  resource: string;
  name: string;
  kind: string;
  purity: string;
  x_m: number;
  y_m: number;
  z_m: number;
  occupied: boolean;
  occupant_cls: string | null;
  occupant_name: string | null;
  region: Region | null;
}

export interface StructureRow {
  /* Nullable, and it always was: `saveio.rows` types every piece's `cls` as `str | None`, and
   * `/api/structures` passes `piece.cls` straight through. A torn row is still a real piece at
   * a real place -- it is drawn, because the only thing this layer needs is the position. */
  cls: string | null;
  // Nullable because drawStructures skips on it: a placement whose transform did not
  // decode has no position, and the projection sends the row anyway.
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  // Null means the projection predates schema 12 and the facing was never recorded, which
  // is a different claim from a recorded facing of zero. See footprintCorners in map.ts.
  yaw: number | null;
}

/** A machine, an extractor or a generator: one row shape, three layers. */
export interface PlacementRow {
  instance_leaf: string;
  cls: string;
  name: string;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  recipe: string | null;
  recipe_name: string | null;
  clock: number | null;
  paused: boolean;
  yaw: number | null;
  // Null for the classes the docs dump carries no clearance data for; the page falls back
  // to MACHINE_FALLBACK_M and says so. All three go null TOGETHER -- one clearance box,
  // read whole or not at all -- which is what lets the floor view read a null `h_m` as
  // "never recorded" rather than as "not tall".
  w_m: number | null;
  l_m: number | null;
  /** How tall it stands above its own deck. The one dimension a top-down map cannot draw,
   *  and the evidence behind the floor view's ghost outlines. */
  h_m: number | null;
}


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

/* `cls` and `name`, on the four rows the server resolves a class for.
 *
 * Both are nullable and both for one reason: `_belt_class`, `_pipe_class` and the two
 * placement builders in `api.py` all take `row.get("cls")`, which is `str | None` in
 * `saveio.rows`, and hand it to `GameData.building_name`, which is `None` in, `None` out --
 * in as many words: "an occupant that is not there is not a building with an unknown name".
 *
 * So a torn row reaches the page with no title at all, and `name || cls` is then `null`.
 * popup() drops a null row, which would silently delete the one row naming the thing the
 * reader just clicked -- see `titleRow` in routes.ts for what is printed instead. */
export interface BeltRow {
  chain: number;
  cls: string | null;
  name: string | null;
  /** True: vertical, so its top-down polyline is one point and it is drawn as a ring.
   *
   * `null` is a third answer and not a false one. The server reads this off the docs dump's
   * own native class (`api.py`, `_belt_class`) and refuses to guess for a class the dump has
   * no entry for, in as many words: "not a lift would be a guess, and the map draws a lift and
   * a belt as different things". Typing it `boolean` made the page collapse that refusal into
   * "belt" at the first `if`, which is the one thing the server declined to say. */
  lift: boolean | null;
  items_per_min: number | null;
  points_m: Point3M[];
  curve_m: RouteCurveM;
}

/** A splitter or a merger: a piece of the belt network, drawn by the belt layer. */
export interface AttachmentRow {
  instance_leaf: string;
  /** Nullable on the same terms as BeltRow's; see the note above it. */
  cls: string | null;
  name: string | null;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  yaw: number | null;
  w_m: number | null;
  l_m: number | null;
}

/** Which way the fluid goes, where the network settles it. */
export type PipeDirection = "forward" | "reverse" | "unknown";

/* What the direction was inferred from: the four values `domain/world/flow.py` defines, and
 * there is no fifth. `/api/pipes` writes `flow.get("basis", "unresolved")`, so the field is
 * always one of these and never null -- the missing-flow case defaults to the same
 * `unresolved` the resolver itself sends when it declines.
 *
 * A closed union rather than `string | null`, because the page has to MAP it: PIPE_FLOW_BASIS
 * in routes.ts is keyed by these, and typing that record by this union is what makes a fifth
 * basis on the server a compile error here instead of an "→ inferred" that says nothing. */
export type PipeFlowBasis = "machine port" | "pump" | "propagated" | "unresolved";

export interface PipeRow {
  /** This pipe's position in the RAW segments table, and the join `/api/floors` keys a pipe
   *  run by. Sent rather than counted: a torn row leaves a gap here that this list's own
   *  index would silently close, which is the case the field exists for. */
  row: number;
  direction: PipeDirection;
  basis: PipeFlowBasis;
  network: number | null;
  fluid: string | null;
  fluid_name: string | null;
  /** Nullable on the same terms as BeltRow's; see the note above it. */
  cls: string | null;
  name: string | null;
  flow_m3_min: number | null;
  points_m: Point3M[];
  curve_m: RouteCurveM;
}

/** One kind of thing in a container, resolved to a display name by the server. */
export interface StoredItem {
  cls: string;
  name: string;
  count: number;
}

/** A storage container or a fluid buffer. Two record shapes behind one row, keyed by `kind`.
 *
 * The fields of the other kind are ABSENT rather than null -- a box has no fluid level, it does
 * not have an empty one -- so the optional markers below are the type saying which half of the
 * union it is looking at, and `kind` is what a reader should branch on. */
export interface StorageRow {
  instance_leaf: string;
  cls: string;
  name: string;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  yaw: number | null;
  /** Null for the classes the docs dump carries no clearance for -- the HUB's own container,
   * the Blueprint Designer's, the Dimensional Depot uploader. The page falls back and says so,
   * exactly as it does for a machine. */
  w_m: number | null;
  l_m: number | null;
  kind: "solid" | "fluid";
  /** Solid containers: the biggest few kinds, with `more` counting what was left off. */
  items?: StoredItem[];
  more?: number;
  item_kinds?: number;
  total?: number;
  slots?: number | null;
  /** Fluid buffers: what is in it, how much it holds, and the fraction those two make. */
  fluid?: string | null;
  fluid_name?: string | null;
  stored_m3?: number | null;
  capacity_m3?: number | null;
  fill?: number | null;
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

export interface NodesResponse extends ApiError {
  nodes: NodeRow[];
  /** Present and non-null when the save could not be read: nodes drawn, occupancy unknown. */
  save_error: string | null;
}

export interface StructuresResponse extends ApiError {
  structures: StructureRow[];
  /** The grid edge every one of these classes snaps to; the page paints one tile per piece.
   *  Never null: `/api/structures` sends the `FOUNDATION_M` constant, which exists precisely
   *  so the page does not hardcode 8. */
  tile_m: number;
}

export interface BeltsResponse extends ApiError {
  belts: BeltRow[];
  attachments?: AttachmentRow[];
}

export interface PipesResponse extends ApiError {
  pipes: PipeRow[];
}

export interface StorageResponse extends ApiError {
  storage: StorageRow[];
}

export interface FactoriesResponse extends ApiError {
  labels: FactoryRow[];
  proposals: ProposalRow[];
}

export interface MachinesResponse extends ApiError {
  machines: PlacementRow[];
  extractors: PlacementRow[];
  generators: PlacementRow[];
}

export interface CollectiblesResponse extends ApiError {
  rows: CollectibleRow[];
}

/** The four branches of `/api/summary` this page reads. The rest is not typed; see above. */
export interface SummaryResponse extends ApiError {
  header: { session_name: string };
  age_note: string;
  power: {
    generation_mw: number;
    draw_mw: number;
    /* Never null. `PowerReport` starts this at 0.0 and only ever adds to it, and a machine
     * with no usable monitor is charged in FULL rather than skipped -- "no monitor is not
     * evidence of idleness", so the figure can only be conservative, never absent. A save
     * with nothing built reports 0.0, which is a measurement and not a missing one. */
    measured_draw_mw: number;
  };
  progression: { game_phase: string | null };
  /* The object is always there; its three fields are what go null. `/api/summary` sends
   * `_xyz(player_position())`, and `_xyz` answers `{x_m: null, y_m: null, z_m: null}` for a
   * save with no pawn rather than dropping the branch -- so "no position" is three nulls, not
   * a missing player. */
  player: { x_m: number | null; y_m: number | null; z_m: number | null };
}

/** What the right-click inspector lays out. Every field here is read by elevationRows. */
export interface Elevation {
  radius_m: number;
  terrain_m: number | null;
  terrain_source: string | null;
  terrain_accuracy_m: number | null;
  terrain_water_m: number | null;
  /** Null wherever the ground under the water was not measured well enough to subtract. */
  terrain_water_depth_m: number | null;
  terrain_water_note: string | null;
  /** Why `terrain_m` is null, when it is. The server has exactly two answers -- no field on
   * this machine (with the generator to run), or a coordinate the field has no data for --
   * and it sends whichever applied. Same job as `fill_note` next door. */
  terrain_note: string | null;
  ground_m: number | null;
  ground_spread_m: number | null;
  ground_count: number;
  built_m: number | null;
  built_count: number;
  fill_m: number | null;
  fill_note: string | null;
}

/* One of the five nodes nearest a right-clicked point.
 *
 * `id` and `name` were left out of this interface, not out of the payload: `_nearest_nodes`
 * in api.py has always sent both -- the full instance path, and the leaf that IS the `node:`
 * selector every dot's own popup prints. Declaring them is what lets the inspector offer the
 * copyable selector too, so the two surfaces answering "what is here" answer it in the same
 * spellable form. See markers.ts, which builds the same row from the same field. */
export interface NearestNode {
  id: string;
  /** The instance's leaf, which is what `node:<name>` selects. */
  name: string;
  resource: string;
  purity: string;
  distance_m: number;
  occupied: boolean;
}

export interface InspectResponse extends ApiError {
  at: { x_m: number; y_m: number };
  region: Region | null;
  elevation: Elevation;
  nearest: NearestNode[];
  save_error: string | null;
}

export interface RegionsResponse extends ApiError {
  /** One string per raster row, one character per 256 m cell; "." is void. */
  grid: string[];
  cell_m: number;
  x0_m: number;
  y0_m: number;
  /* `label_m` is never null: `_label_anchor` returns the centroid when the centroid's own cell
   * carries the region's letter, the centre of the nearest cell that does when it does not,
   * and the centroid again when the search finds nothing -- three branches, two floats each.
   * What it can be is DIFFERENT from `centroid_m`, which is the whole reason it exists. */
  regions: Record<string, { centroid_m: PointM; bbox_m: BboxM; label_m: PointM }>;
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
