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
 */

export var state = {
  world: "",
  save: "", // a pinned save's path; "" means "the newest, refetched on save events"
  worlds: [],
  layers: {},
  layerName: {}, // Leaflet's layer stamp -> the name its control row carries
  control: null,
  map: null,
  epoch: 0, // bumped on every world/save switch; a reply from an older epoch is dropped
  opened: Date.now(),
};

/* The selection lives in the URL fragment (#world=…&save=…&z=…&c=x,y) so a reload, a
 * bookmark or a pasted link lands on the same world, save and viewport instead of
 * silently teleporting to the newest world at the whole-world zoom. replaceState, not
 * assignment: panning must not grow the browser history by one entry per drag. */
export var BOOT = (function () {
  var out = {};
  location.hash
    .replace(/^#/, "")
    .split("&")
    .forEach(function (piece) {
      var eq = piece.indexOf("=");
      if (eq > 0) out[piece.slice(0, eq)] = decodeURIComponent(piece.slice(eq + 1));
    });
  return out;
})();

export function currentWorld() {
  var found = null;
  state.worlds.forEach(function (w) {
    if (w.world_id === state.world) found = w;
  });
  return found;
}

export function pinnedFilename() {
  var name = "";
  var w = currentWorld();
  if (!state.save || !w) return name;
  (w.saves || []).forEach(function (s) {
    if ((s.path || s.filename) === state.save) name = s.filename;
  });
  return name;
}
