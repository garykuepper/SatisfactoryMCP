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

import type { WorldRow } from "./api-types";

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
