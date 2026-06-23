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
import { layer } from "./layers";
import { xy } from "./map";
import {
  PICKUP_COLOUR,
  PICKUP_FALLBACK,
  PLAYER_COLOUR,
  PURITY_RADIUS,
  RESOURCE_COLOUR,
} from "./palette";
import { state } from "./state";
import { fail } from "./toast";

/* Everything shares one canvas, so hit-testing is draw order: last drawn wins the click.
 * An extractor is drawn exactly on the node it drains, and whichever of /api/nodes and
 * /api/machines resolved last used to decide -- usually making all 44 occupied nodes
 * unclickable. The node dots are raised explicitly after either draw, so the dot (the
 * card with purity, region and the node: selector) always takes the click; the extractor
 * keeps the rest of its rectangle. */
export function raiseNodeDots() {
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    state.layers[name].eachLayer(function (dot) {
      if (dot.bringToFront && dot._map) dot.bringToFront();
    });
  });
}

export function drawNodes(data) {
  var byResource = {};
  data.nodes.forEach(function (n) {
    (byResource[n.resource] = byResource[n.resource] || []).push(n);
  });
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    var still = Object.keys(byResource).some(function (resource) {
      return "node: " + shortResource(resource) === name;
    });
    if (!still) state.layers[name].clearLayers();
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      var short = shortResource(resource);
      var colour = RESOURCE_COLOUR[resource] || "#888";
      var group = layer("node: " + short, true, colour);
      byResource[resource].forEach(function (n) {
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

/* The player's last known position: the map's only you-are-here, and the reference every
 * "is this near me" judgement needs. Ring-styled so it reads as a position, not a node. */
export function drawPlayer(p) {
  var group = layer("player", true, PLAYER_COLOUR);
  if (!p || p.x_m === null || p.y_m === null) return;
  L.circleMarker(xy(p), {
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

export function drawCollectibles(data) {
  var byCategory = {};
  data.rows.forEach(function (r) {
    (byCategory[r.category] = byCategory[r.category] || []).push(r);
  });
  Object.keys(state.layers).forEach(function (name) {
    // A category this world has none of (all collected, or never present) must not keep
    // showing another world's markers under a still-ticked box.
    if (name.indexOf("pickup: ") === 0 && !byCategory[name.slice("pickup: ".length)]) {
      state.layers[name].clearLayers();
    }
  });
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      var colour = PICKUP_COLOUR[category] || PICKUP_FALLBACK;
      var group = layer("pickup: " + category, false, colour);
      byCategory[category].forEach(function (r) {
        var here = xy(r);
        var mark = r.collected
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
