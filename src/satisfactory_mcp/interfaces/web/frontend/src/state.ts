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
};

/* The selection lives in the URL fragment (#world=…&save=…&z=…&c=x,y) so a reload, a
 * bookmark or a pasted link lands on the same world, save and viewport instead of
 * silently teleporting to the newest world at the whole-world zoom. replaceState, not
 * assignment: panning must not grow the browser history by one entry per drag. */
export var BOOT: Record<string, string> = (function () {
  var out: Record<string, string> = {};
  location.hash
    .replace(/^#/, "")
    .split("&")
    .forEach(function (piece) {
      var eq = piece.indexOf("=");
      if (eq > 0) out[piece.slice(0, eq)] = decodeURIComponent(piece.slice(eq + 1));
    });
  return out;
})();

export function currentWorld(): WorldRow | null {
  var found: WorldRow | null = null;
  state.worlds.forEach(function (w) {
    if (w.world_id === state.world) found = w;
  });
  return found;
}

export function pinnedFilename(): string {
  var name = "";
  var w = currentWorld();
  if (!state.save || !w) return name;
  (w.saves || []).forEach(function (s) {
    if ((s.path || s.filename) === state.save) name = s.filename;
  });
  return name;
}
