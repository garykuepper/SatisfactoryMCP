/* What the page is currently showing, and where it was told to show it.
 *
 * One mutable object rather than module-level variables spread over the drawing modules,
 * because a world switch has to change all of it at once: the selection, the epoch that
 * makes a late reply from the old world droppable, and the layer registry the redraw writes
 * into. Modules read and write it directly, exactly as the single-file page did.
 *
 * This module imports NOTHING, and that is load-bearing: `map.ts` reads `BOOT` while it is
 * building the map, so anything this file imported would have to be evaluated before the map
 * exists. Keeping it at the bottom of the graph is what makes that safe rather than lucky.
 * Both imports below are `import type`, which is erased: nothing is evaluated by either.
 */

import type * as L from "leaflet";

/* The one payload on this page the server does not describe, declared where it is stored.
 *
 * `/api/worlds` is DEFERRED, not missed: it forwards the loader's own `World` dataclasses,
 * whose `saves` are the sidecar's thirteen-key save HEADERS, so a faithful response model is
 * `dict[str, Any]` and a useful one DELETES eight keys from every row -- a change to what the
 * endpoint sends, which is a commit about the body rather than a typing one. The comment
 * above `worlds()` in routers/world.py is the long version, and it has an argument the rest
 * of the surface does not: pydantic serialises a TypedDict in declaration order and drops
 * what is absent, so a partial header comes back RE-KEYED rather than passed through.
 *
 * So these two are the frontend's own claim, read off real payloads, and the caveat every
 * such claim carries applies: a field that is always populated in the saves it was read from
 * can be absent in somebody else's. They are here rather than in worlds.ts because this is
 * the module that STORES them -- `state.worlds` is the list, and `currentWorld` and
 * `pinnedPath` below are typed by the row -- and worlds.ts, which fetches them, already
 * imports this file. The response wrapper stays there, with the fetch.
 *
 * Both `import type`s in this module are erased, so the sentence above about importing
 * nothing at runtime still holds; a declaration costs nothing at all. */

/** One save file, as the picker's second dropdown reads it. */
export interface SaveRow {
  path: string;
  filename: string;
  session_name: string;
  play_duration_s: number;
  mtime_ns: number;
}

/** One world: its saves, and the newest one's headline figures hoisted onto it. */
export interface WorldRow {
  world_id: string;
  session_name: string;
  saves: SaveRow[];
  mtime: number;
  newest_filename: string;
  play_duration_s: number;
}

/* The panel's fold state, which layercontrol.ts owns and sets. It is declared here rather
 * than there because it belongs to the same object as everything else that has to survive a
 * world switch -- and it survives one for the same reason the checkboxes do: a switch
 * replaces layer CONTENTS without rebuilding the control or these flags. */
export interface PanelState {
  open: boolean;
  sections: Record<string, boolean>;
}

/* Which picture of this world the base map is: the modes tiles.ts offers, as radio
 * semantics -- exactly one, and `plain` is a real answer rather than the absence of one.
 *
 * The union is declared HERE rather than beside the tile layers that draw it, for the same
 * reason `PanelState` is: it is part of what the page is currently showing, so it belongs to
 * the object a world switch has to change all at once -- and declaring it there would make
 * this file, which imports nothing, import the module that fetches tiles. */
export type BaseMode = "artwork" | "terrain" | "satellite" | "plain";

/* Which storey of which platform the page is slicing, and nothing else about it.
 *
 * The ADDRESS lives here and the floor decomposition itself does not, which is the same
 * split `mode` makes: this file holds what the page is currently showing so that a world
 * switch can change all of it at once, and `map.ts` can write the fragment without importing
 * the module that fetches. Everything else about the view -- which ids are on which band,
 * which runs leave it -- is `floors.ts`'s, because it is a payload rather than a selection.
 *
 * `band` is a band's ordinal as a string, or "ground": the pseudo-floor for what the
 * decomposition measured as standing on no band at all. A string because those are one
 * choice among the picker's rows and the fragment spells both the same way. */
export interface FloorAddress {
  platform: number;
  band: string;
}

export interface PageState {
  world: string;
  /** A pinned save's path; "" means "the newest, refetched on save events". */
  save: string;
  worlds: WorldRow[];
  /** Every named LayerGroup, by the name its control row carries. */
  layers: Record<string, L.LayerGroup>;
  /** Leaflet's layer stamp -> the name its control row carries. */
  layerName: Record<number, string>;
  control: L.Control.Layers | null;
  map: L.Map | null;
  /** Bumped on every world/save switch; a reply from an older epoch is dropped. */
  epoch: number;
  opened: number;
  panel: PanelState;
  /** The base-map mode; "" until the probes have said which ones exist. See tiles.ts. */
  mode: BaseMode | "";
  /** Whether that mode actually has a picture on the map -- which is the one thing the
   *  region tint has to know, and the reason it is a flag here rather than a question
   *  regions.ts asks tiles.ts (which would be a cycle: tiles.ts already imports it). */
  imagery: boolean;
  /** The storey being sliced, or null for the whole world. See FloorAddress. */
  floor: FloorAddress | null;
}

export var state: PageState = {
  world: "",
  save: "", // a pinned save's path; "" means "the newest, refetched on save events"
  worlds: [],
  layers: {},
  layerName: {}, // Leaflet's layer stamp -> the name its control row carries
  control: null,
  map: null,
  epoch: 0,
  opened: Date.now(),
  // Replaced wholesale by layercontrol.ts as it builds the control, which is where the
  // section keys are decided and where the reasoning for them lives. This is a placeholder
  // so that the field is never undefined, not a second declaration of the defaults.
  panel: { open: true, sections: {} },
  // "" and false until loadBaseMap has probed: the page has not chosen a mode yet, and
  // writeHash must not pin one it has not chosen.
  mode: "",
  imagery: false,
  // Whole world until somebody asks for a storey; the fragment can ask for one at boot.
  floor: null,
};

/* The selection lives in the URL fragment (#world=…&save=…&z=…&c=x,y) so a reload, a
 * bookmark or a pasted link lands on the same world, save and viewport instead of
 * silently teleporting to the newest world at the whole-world zoom. replaceState, not
 * assignment: panning must not grow the browser history by one entry per drag.
 *
 * The parse is a FUNCTION and not just the constant below it, because the fragment is read
 * more than once: `BOOT` is the one the page opened on, and fragment.ts re-reads it whenever
 * the address bar changes under an open tab. One parser, so a hand-typed fragment is read
 * exactly the way a bookmarked one is. */
export function parseHash(hash: string): Record<string, string> {
  var out: Record<string, string> = {};
  hash
    .replace(/^#/, "")
    .split("&")
    .forEach(function (piece) {
      var eq = piece.indexOf("=");
      if (eq > 0) out[piece.slice(0, eq)] = decodeURIComponent(piece.slice(eq + 1));
    });
  return out;
}

export var BOOT: Record<string, string> = parseHash(location.hash);

export function currentWorld(): WorldRow | null {
  var found: WorldRow | null = null;
  state.worlds.forEach(function (w) {
    if (w.world_id === state.world) found = w;
  });
  return found;
}

/* The two halves of the same lookup, and they are a pair on purpose: the fragment carries a
 * save's FILENAME (short, readable, and the thing a human editing the address bar would
 * type) while `state.save` is its PATH (unambiguous when two worlds hold a "save 1.sav").
 * Whoever writes the fragment converts one way and whoever reads one converts back. */
export function pinnedFilename(): string {
  var name = "";
  var w = currentWorld();
  if (!state.save || !w) return name;
  w.saves.forEach(function (s) {
    if ((s.path || s.filename) === state.save) name = s.filename;
  });
  return name;
}

/** A filename out of the fragment, as the pin `state.save` holds; "" if this world has no
 *  such save, which is how both callers say "follow the newest" without a second flag. */
export function pinnedPath(filename: string, w: WorldRow | null): string {
  var found = "";
  if (!filename || !w) return found;
  w.saves.forEach(function (s) {
    if (s.filename === filename) found = s.path || s.filename;
  });
  return found;
}
