/* Named layer groups: the registry the drawing modules add to and the control lists.
 *
 * `layer()` is the page's one way of getting a group, and the identity it preserves is the
 * point -- a refetch replaces a group's CONTENTS so the checkbox does not forget whether it
 * was ticked. Kept apart from the control that displays them because everything that draws
 * needs this and nothing that draws needs the folds.
 */

import { esc } from "./dom";
import { L } from "./leaflet";
import { control } from "./layercontrol";
import { map } from "./map";
import { state } from "./state";

/* The layer control doubles as the map's legend and only filter, so its order has to
 * survive a reload: overlays used to be appended in whatever order six parallel fetches
 * resolved, which shuffled 32 rows between page loads. sortLayers pins the order to the
 * rank each group is given when it is created -- chrome first, then machine layers, then
 * nodes alphabetically, then pickups alphabetically. */
var LAYER_ORDER = [
  "regions",
  "map image",
  "region names",
  "player",
  "factory labels",
  "proposals",
  "foundations",
  "belts",
  "pipes",
  "machines",
  "extractors",
  "generators",
];

type Rank = [number, number, string];

function layerRank(name: string): Rank {
  var fixed = LAYER_ORDER.indexOf(name);
  if (fixed >= 0) return [0, fixed, name];
  if (name.indexOf("node: ") === 0) return [1, 0, name];
  if (name.indexOf("pickup: ") === 0) return [2, 0, name];
  return [3, 0, name];
}

/* A named layer that can be replaced wholesale on refetch without the checkbox
 * forgetting whether it was ticked -- that is why the LayerGroup identity is kept and
 * only its contents are cleared. `colour` puts a swatch in the control row, which is
 * what makes the control readable as a legend: "node: Coal" next to its actual grey. */
export function layer(name: string, on?: boolean, colour?: string): L.LayerGroup {
  if (!state.layers[name]) {
    var group = L.layerGroup();
    group._rank = layerRank(name);
    state.layers[name] = group;
    // Keyed by the same stamp Leaflet writes onto the row's checkbox, so decorateControl
    // can read a row's name back without parsing the swatch markup out of its text.
    state.layerName[L.Util.stamp(group)] = name;
    var title = colour
      ? '<i class="swatch" style="background:' + colour + '"></i>' + esc(name)
      : esc(name);
    control.addOverlay(group, title);
    if (on) group.addTo(map);
  }
  return state.layers[name]!.clearLayers();
}

/* Layers whose names are data-driven (one per resource, one per pickup category) can go
 * stale on a world switch: a category the new world does not return would otherwise keep
 * the previous world's markers under a still-ticked checkbox. */
export function clearPrefixed(prefixes: string[]): void {
  Object.keys(state.layers).forEach(function (name) {
    prefixes.forEach(function (prefix) {
      if (name.indexOf(prefix) === 0) state.layers[name]!.clearLayers();
    });
  });
}
