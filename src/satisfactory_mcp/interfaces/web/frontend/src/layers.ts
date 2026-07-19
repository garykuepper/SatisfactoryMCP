/* Named layer groups: the registry the drawing modules add to and the control lists.
 *
 * `layer()` is the page's one way of getting a group, and the identity it preserves is the
 * point -- a refetch replaces a group's CONTENTS so the checkbox does not forget whether it
 * was ticked. Kept apart from the control that displays them because everything that draws
 * needs this and nothing that draws needs the folds.
 *
 * THIS FILE IMPORTS NOTHING THAT IMPORTS IT, and that is a ratchet rather than a habit --
 * see test_architecture.py. Every drawing module on the page reaches this one, so anything
 * it reached back would be evaluated before all of them, and the ring would be invisible in
 * both files. The rule is wider than the registry's: not "no module that fetches" but "no
 * module that draws", derived from the source as "no module that imports ./layers".
 */

import { esc } from "./dom";
import { L } from "./leaflet";
import { control } from "./layercontrol";
import { map } from "./map";
import { state } from "./state";

/* THE PAGE HAS TWO RANKS. THEY DECIDE DIFFERENT THINGS AND NEITHER DRIVES THE OTHER.
 *
 *   FETCH RANK -- `Fetcher.rank` in registry.ts, one number, per wave. WHEN a request goes
 *   out. It decides which reply lands first, which matters to floor mode's opening flight
 *   and to what is on the canvas when.
 *
 *   ROW RANK -- the `rank` below, three parts, per layer. WHERE a layer's row sits in the
 *   control. It decides nothing but the order of a list of checkboxes.
 *
 * A feature declares both, side by side, and they are free to disagree: the node dots are
 * fetched FIRST (fetch rank 10 of the static wave, because everything else is read against
 * them) and listed LATE (row band 2, because fourteen data-driven rows at the top would bury
 * the nine that are always there). Reading one off the other would be wrong in both
 * directions.
 *
 * ROW RANK IS NOT DRAW ORDER either, which is the reading most worth refusing. Everything
 * clickable shares one canvas, and that canvas draws in the order paths were ADDED to it --
 * a list Leaflet maintains and this rank appears nowhere in. `sortFunction` below is handed
 * to `L.control.layers` and is read by the control when it rebuilds its list, and by nothing
 * else. That is why `raiseNodeDots` in markers.ts still exists and why no band ordering here
 * could replace it: a rank of any shape would still be sorting rows, not pixels.
 */

/* The bands, which are the coarse half of the row rank and the only half worth naming: they
 * are the sections of the legend as a reader sees it, top to bottom.
 *
 * The layer control doubles as the map's legend and its only filter, so its order has to
 * survive a reload: overlays used to be appended in whatever order six parallel fetches
 * resolved, which shuffled 32 rows between page loads. `sortLayers` pins the order to the
 * rank each group is given when it is created.
 *
 * The base map is not in this list and no longer has a row at all: it is the MODE radios at
 * the top of the control, which are one choice among four rather than one more checkbox in a
 * list of thirty-five, and tiles.ts puts its layer on the map directly. `plain` is what the
 * unticked "map image" box used to mean, and it is now a state with a name rather than the
 * absence of a tick.
 */
export var BAND = {
  /** The frame the world is read against: the biomes, their names, where you last stood,
   *  and the labels naming the places on it. Not things the player built. */
  chrome: 0,
  /** What the player built -- the networks and the placements, in the order a base is read
   *  in: the concrete, then what runs over it, then what stands on it. */
  built: 1,
  /** `node: `, one row per resource. Data-driven and alphabetical within the band. */
  node: 2,
  /** `pickup: `, one row per category. Data-driven and alphabetical within the band. */
  pickup: 3,
  /** Nothing declared a rank. A resting place rather than a category -- the control sorts
   *  such a row to the bottom rather than dropping it, and dev mode says so out loud. */
  unknown: 9,
};

/* [band, slot, name]. The slot orders one band's fixed members; the name breaks the tie
 * inside a slot, which is the whole of the ordering for the two data-driven bands, where
 * every member shares slot 0 and the rows are alphabetical by construction.
 *
 * Slots are spaced by TEN, the same convention and for the same reason as the fetch ranks
 * they are declared beside: a layer can be inserted between two others without renumbering
 * the band. Only the ORDER they produce was ever a fact -- the numbers this replaced were
 * `LAYER_ORDER.indexOf(name)`, an array position that never appeared anywhere a reader could
 * see it -- so the order is what was carried over verbatim, row for row.
 */
export type Rank = [number, number, string];

/* A named layer that can be replaced wholesale on refetch without the checkbox
 * forgetting whether it was ticked -- that is why the LayerGroup identity is kept and
 * only its contents are cleared. `colour` puts a swatch in the control row, which is
 * what makes the control readable as a legend: "node: Coal" next to its actual grey.
 *
 * `rank` is optional in the type and required in practice: a layer that declares none sorts
 * into BAND.unknown at the bottom of the list, which is a place to land rather than a
 * decision. Dev mode names it, because a row quietly at the end of thirty-seven is not a
 * thing anyone notices. The name is repeated inside the rank so that the sort key reads as
 * one whole value at the call site; dev mode checks the two agree.
 */
export function layer(name: string, on?: boolean, colour?: string, rank?: Rank): L.LayerGroup {
  if (!state.layers[name]) {
    if (import.meta.env.DEV) {
      if (!rank) {
        console.error('layer "' + name + '" declares no rank — it sorts to the bottom');
      } else if (rank[2] !== name) {
        console.error('layer "' + name + '" ranks itself as "' + rank[2] + '"');
      }
    }
    var group = L.layerGroup();
    group._rank = rank || [BAND.unknown, 0, name];
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
  var group = state.layers[name]!;
  // The floor filter's undo goes with the contents it is an undo OF. `_floorAll` is a
  // snapshot of what this group held, and a refetch is exactly the moment that snapshot
  // stops being about anything -- keeping it would let a later exit restore a world that
  // has been replaced. floors.ts takes a fresh one on its next pass.
  delete group._floorAll;
  return group.clearLayers();
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
