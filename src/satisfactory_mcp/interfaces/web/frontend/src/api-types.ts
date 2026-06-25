/* What the API actually sends, as the frontend's claim rather than the API's.
 *
 * `api-schema.d.ts` is generated from the server's own `/openapi.json` and is the authority
 * for the things FastAPI knows: which paths exist, which query parameters each takes, and
 * what a validation error looks like. It is NOT the authority for response bodies, because
 * every endpoint in `api.py` is annotated `-> dict`, and a bare dict carries no schema at
 * all -- so the generated file types all sixteen responses as `unknown`.
 *
 * The interfaces below fill that gap, and it matters where they came from: they were read
 * off real payloads from a real save, not from the server's source. That makes them an
 * observation, and an observation can be wrong in one specific direction -- a field that is
 * always populated in the world it was read from can be null in another. So every field the
 * drawing code already guards against is declared nullable here even where the sample had a
 * value, because the guard IS the evidence: `m.clock === null ? null : ...` in placements.ts
 * is the page saying it has seen a null clock, and this file should not contradict it.
 *
 * They are deliberately not exhaustive. `/api/summary` returns a large object of which this
 * page reads four branches, and typing the other twenty would be inventing a contract for
 * data nothing here looks at. What is declared is what is read.
 *
 * The right fix is response models on `api.py`, which would make this file generated too.
 * That is a change to the server's public surface and belongs in its own commit.
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
  cls: string;
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
  // to MACHINE_FALLBACK_M and says so.
  w_m: number | null;
  l_m: number | null;
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

export interface BeltRow {
  chain: number;
  cls: string;
  name: string | null;
  /** Vertical, so its top-down polyline is one point and it is drawn as a ring. */
  lift: boolean;
  items_per_min: number | null;
  points_m: Point3M[];
  curve_m: RouteCurveM;
}

/** A splitter or a merger: a piece of the belt network, drawn by the belt layer. */
export interface AttachmentRow {
  instance_leaf: string;
  cls: string;
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

export interface PipeRow {
  direction: PipeDirection;
  /** What the direction was inferred from; keys PIPE_FLOW_BASIS in routes.ts. */
  basis: string | null;
  network: number | null;
  fluid: string | null;
  fluid_name: string | null;
  cls: string;
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
  notes: string | null;
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
  /** The grid edge every one of these classes snaps to; the page paints one tile per piece. */
  tile_m: number | null;
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
    /** Null on a save with no monitored buildings; the header falls back to the nameplate. */
    measured_draw_mw: number | null;
  };
  progression: { game_phase: string | null };
  player: { x_m: number | null; y_m: number | null; z_m: number | null } | null;
}

/** What the right-click inspector lays out. Every field here is read by elevationRows. */
export interface Elevation {
  radius_m: number;
  terrain_m: number | null;
  terrain_source: string | null;
  terrain_accuracy_m: number | null;
  terrain_water_m: number | null;
  ground_m: number | null;
  ground_spread_m: number | null;
  ground_count: number;
  built_m: number | null;
  built_count: number;
  fill_m: number | null;
  fill_note: string | null;
}

export interface NearestNode {
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
  regions: Record<string, { centroid_m: PointM; bbox_m: BboxM; label_m: PointM | null }>;
}

export interface WorldsResponse extends ApiError {
  worlds?: WorldRow[];
  unsupported?: { filename: string; reason: string }[];
}
