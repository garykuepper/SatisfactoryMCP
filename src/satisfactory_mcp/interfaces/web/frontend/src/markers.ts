/* The three things drawn as points rather than as shapes: resource nodes, pickups, and
 * where the player last stood.
 *
 * `raiseNodeDots` is the reason they share a file with each other rather than one apiece.
 * Everything clickable is on one canvas, hit-testing is draw order, and an extractor is
 * drawn exactly on the node it drains -- so the node dots have to be raised after ANY draw,
 * and the pass that does it belongs with the layers it raises.
 */

import { code, popup } from "./dom";
import { regionLine, shortResource } from "./format";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { xy } from "./map";
import {
  PICKUP_COLOUR,
  PICKUP_FALLBACK,
  PLAYER_COLOUR,
  PURITY_RADIUS,
  RESOURCE_COLOUR,
} from "./palette";
import { registerFetch } from "./registry";
import { state } from "./state";
import { fail } from "./toast";

import type { OnMap } from "./leaflet-private";

import type {
  CollectibleRow,
  CollectiblesResponse,
  NodeRow,
  NodesResponse,
  SummaryResponse,
} from "./api-types";

/* Everything shares one canvas, so hit-testing is draw order: last drawn wins the click.
 * An extractor is drawn exactly on the node it drains, and whichever of /api/nodes and
 * /api/machines resolved last used to decide -- usually making all 44 occupied nodes
 * unclickable. The node dots are raised explicitly after either draw, so the dot (the
 * card with purity, region and the node: selector) always takes the click; the extractor
 * keeps the rest of its rectangle. */
export function raiseNodeDots() {
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    state.layers[name]!.eachLayer(function (dot) {
      var path = dot as L.Path & OnMap;
      if (path.bringToFront && path._map) path.bringToFront();
    });
  });
}

export function drawNodes(data: NodesResponse): void {
  var byResource: Record<string, NodeRow[]> = {};
  data.nodes.forEach(function (n) {
    (byResource[n.resource] = byResource[n.resource] || []).push(n);
  });
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    var still = Object.keys(byResource).some(function (resource) {
      return "node: " + shortResource(resource) === name;
    });
    if (!still) state.layers[name]!.clearLayers();
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      var short = shortResource(resource);
      var colour = RESOURCE_COLOUR[resource] || "#888";
      // Slot 0 for every member, so the band's whole ordering is the name: these rows are
      // DATA -- one per resource this world has -- and there is no editorial order to give
      // fourteen of them that a reader could predict. Alphabetical is predictable.
      var name = "node: " + short;
      var group = layer(name, true, colour, [BAND.node, 0, name]);
      byResource[resource]!.forEach(function (n) {
        L.circleMarker(xy(n), {
          radius: PURITY_RADIUS[n.purity] || 4,
          color: colour,
          weight: n.occupied ? 2 : 1,
          opacity: 1,
          fillOpacity: n.occupied ? 0.15 : 0.75,
        })
          .bindPopup(
            popup([
              ["node", short + " (" + n.purity + ")"],
              // Joined server-side: the raster and its orientation trap stay on one side.
              ["region", regionLine(n.region)],
              // Always present, because the absence of a row cannot be told apart from a
              // broken join -- and "no extractor known" is the join's own honest limit:
              // it resolves extractors targeting a node key, never proves a node free.
              [
                "occupancy",
                n.occupied
                  ? "occupied by " + (n.occupant_name || n.occupant_cls)
                  : data.save_error
                    ? "unknown — save could not be read"
                    : "no extractor known here",
              ],
              ["selector", code("node:" + n.name)],
              ["at", n.x_m + ", " + n.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
  raiseNodeDots();
  if (data.save_error) {
    fail("nodes: " + data.save_error + " — nodes drawn, occupancy unknown");
  }
}

/* First of the static wave, which is where it was when the wave was a list of calls: the node
 * dots are the layer every other placement is read against, and the extractors drawn on top
 * of them arrive with the live wave. `clears` carries the trailing space because the layer
 * names are data -- one per resource -- and "node:" alone is a prefix of more than this.
 *
 * FETCH RANK, not row rank: this is the first request of the wave and its rows are the
 * second-to-last band in the control. See layers.ts for why neither follows from the other. */
registerFetch<NodesResponse>({
  wave: "static",
  rank: 10,
  path: "/api/nodes",
  label: "nodes",
  clears: ["node: "],
  refilters: true,
  draw: drawNodes,
});

/* The player's last known position: the map's only you-are-here, and the reference every
 * "is this near me" judgement needs. Ring-styled so it reads as a position, not a node.
 *
 * NO ROW WHEN THERE IS NO POSITION, and the two lines that arrange it are not the same line.
 *
 * `layer()` both creates the control row and clears the group, so asking for the layer before
 * the guard gave every save a "player" checkbox -- including a dedicated-server save, which
 * has no pawn at all. That box ticked and unticked nothing, which is worse than a missing row:
 * the control is the map's legend, and a legend entry is a claim that the thing exists.
 *
 * Moving the call below the guard is only half of it, and the missing half is why this is a
 * branch rather than a reordering. The clear was riding on that same call, so a switch FROM a
 * world with a pawn TO one without would have left the previous world's dot on the map under
 * the previous world's row -- a you-are-here pointing at a place in a different save. So the
 * empty case reaches the registry directly: it clears a group that exists and creates nothing
 * if one does not.
 */
export function drawPlayer(p: SummaryResponse["player"]): void {
  // The object is always sent; its fields are what go null on a save with no pawn.
  if (p.x_m === null || p.y_m === null) {
    var stale = state.layers["player"];
    if (stale) stale.clearLayers();
    return;
  }
  // Chrome, not a placement: where you last stood is part of the frame the built world is
  // read against, which is why it sits with the biomes and the labels rather than with the
  // machines. Third of that band, under the two region rows it is a position within.
  var group = layer("player", true, PLAYER_COLOUR, [BAND.chrome, 20, "player"]);
  L.circleMarker(xy(p as { x_m: number; y_m: number }), {
    radius: 7,
    color: PLAYER_COLOUR,
    weight: 2,
    fillColor: "#4aa3df",
    fillOpacity: 0.9,
  })
    .bindPopup(
      popup([
        ["player", "where you last stood (as of this save)"],
        ["at", p.x_m + ", " + p.y_m + " m"],
      ])
    )
    .addTo(group);
}

export function drawCollectibles(data: CollectiblesResponse): void {
  var byCategory: Record<string, CollectibleRow[]> = {};
  data.rows.forEach(function (r) {
    (byCategory[r.category] = byCategory[r.category] || []).push(r);
  });
  Object.keys(state.layers).forEach(function (name) {
    // A category this world has none of (all collected, or never present) must not keep
    // showing another world's markers under a still-ticked box.
    if (name.indexOf("pickup: ") === 0 && !byCategory[name.slice("pickup: ".length)]) {
      state.layers[name]!.clearLayers();
    }
  });
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      var colour = PICKUP_COLOUR[category] || PICKUP_FALLBACK;
      // Slot 0 and alphabetical for the same reason the node rows are, one band lower: ten
      // categories of thing lying on the ground, in no order anyone could guess at.
      var name = "pickup: " + category;
      var group = layer(name, false, colour, [BAND.pickup, 0, name]);
      byCategory[category]!.forEach(function (r) {
        var here = xy(r);
        var mark: L.Path = r.collected
          ? L.polyline(
              [
                [
                  [here[0] - 4, here[1] - 4],
                  [here[0] + 4, here[1] + 4],
                ],
                [
                  [here[0] - 4, here[1] + 4],
                  [here[0] + 4, here[1] - 4],
                ],
              ],
              { color: "#6b7078", weight: 1 }
            )
          : L.circleMarker(here, { radius: 4, color: colour, weight: 1, fillOpacity: 0.7 });
        mark
          .bindPopup(
            popup([
              ["pickup", category],
              ["name", code(r.name)],
              ["state", r.collected ? "collected" : r.observed || "unknown"],
              ["at", r.x_m + ", " + r.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
}

/* The live wave, because a pickup is collected between one autosave and the next, and
 * `mode=remaining` because the question the layer answers is "what is left". The query string
 * is spelled at the registration rather than plumbed through `get`, which is what the two
 * callers that take one have always done. */
registerFetch<CollectiblesResponse>({
  wave: "live",
  rank: 20,
  path: "/api/collectibles?mode=remaining",
  label: "collectibles",
  clears: ["pickup: "],
  refilters: true,
  draw: drawCollectibles,
});
