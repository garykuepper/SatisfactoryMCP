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
import { registerSection } from "./layercontrol";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { xy } from "./map";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";
import { state } from "./state";
import { fail } from "./toast";

import type { OnMap } from "./leaflet-private";

import type { NodeRow, NodesResponse, SummaryResponse } from "./api-shapes";
import type { CollectibleRow, CollectiblesResponse } from "./api-types";

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

// Ore colours follow the in-game item tints closely enough to be recognisable without
// shipping a single game asset: they are hex strings, not textures. Which is also why they are
// the one family here that cannot simply be moved when the audit objects -- an ore's colour is
// the ore's, and the page borrowed it rather than chose it. Four of them are in STANDING in
// palette.ts, coal against eight grounds among them.
var RESOURCE_COLOUR: Record<string, string> = declareColours("markers", {
  Desc_OreIron_C: "#c8b6a6",
  Desc_OreCopper_C: "#e08a4b",
  Desc_Stone_C: "#cfcfcf",
  Desc_Coal_C: "#4c4c4c",
  Desc_OreGold_C: "#e3c74a",
  Desc_Sulfur_C: "#e8e35c",
  Desc_RawQuartz_C: "#e59ce0",
  Desc_OreBauxite_C: "#b06a4a",
  Desc_OreUranium_C: "#7ce07c",
  Desc_LiquidOil_C: "#6b4bb0",
  Desc_NitrogenGas_C: "#6ec5e0",
  Desc_Water_C: "#3f8fd0",
  Desc_SAM_C: "#b04bd0",
  Desc_Geyser_C: "#d97b4f", // synthetic label; a geyser is a placement target, not an item
});

/* How big a node dot is, in pixels, by purity -- the grammar POLE_RADIUS_PX in power.ts calls
 * "is there one here": a fixed size, because the question a dot answers is whether there is a
 * node, not how much room it takes up. */
var PURITY_RADIUS: Record<string, number> = { impure: 3, normal: 4.5, pure: 6 };

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

/* The `node: ` rows as a family: one fold, one tri-state box, one "n of 14".
 *
 * Declared here rather than in the control because this is the file that makes those rows --
 * `layer("node: " + short, …)` a few lines up is the only thing that ever will -- and a
 * family whose prefix is spelled in one file and created in another is two edits for one
 * feature. Shut by default: fourteen rows that grow with the world are what turned a nine-row
 * legend into thirty-seven, and the head's own count answers "are the ore dots on?" without
 * opening it.
 *
 * NOT the same statement as the row rank above, and the two are independent on purpose. The
 * rank puts these rows together and in order; the section puts a head on them. A family with
 * no rank would still fold -- its rows would just be scattered through the list, which is
 * what the head would then be a head OF. */
registerSection({ key: "nodes", prefix: "node: ", title: "resource nodes", startOpen: false });

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
/* Near-white and warm, the one thing on the page that is not a colour ABOUT anything: it is
 * not an ore, not a tier and not a biome, so it is the value nothing else on the map spends. */
var PLAYER_COLOUR = declareColours("markers", { player: "#f5f0e8" }).player;

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

// One colour per pickup category, so ten separate checkboxes stop drawing one
// indistinguishable teal dot. Unlisted categories share the old teal as the fallback below.
//
// Handed out one per kind rather than measured against the page, and the audit in palette.ts
// says what that cost: the crashed drop pod is dE 2.1 from the belt steel, which is the closest
// pair anywhere on this map.
var PICKUP_COLOUR: Record<string, string> = declareColours("markers", {
  somersloop: "#e05c5c",
  mercer_sphere: "#b06ae0",
  hard_drive: "#6ea8d8",
  loot_cache: "#d8b46e",
  crashed_drop_pod: "#9aa8b8",
  power_slug_blue: "#5cc8e8",
  power_slug_yellow: "#e8d55c",
  power_slug_purple: "#c85ce8",
  mushroom: "#a8c86e",
  tape_pickup: "#e09a6e",
});

/* The old single teal, kept for the categories the table above does not name -- and DECLARED
 * rather than left as a bare literal, because a world with a category nobody has coloured yet
 * draws this one for real: `customization_unlock_pickup` is a row on the reference save. A
 * stand-in that reaches the screen is a colour on the page and belongs in the comparison. */
var PICKUP_FALLBACK = declareColours("markers", { "pickup fallback": "#7fd1b9" })[
  "pickup fallback"
];

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

/* The `pickup: ` rows as a family, on the same terms as the node one above and shut for the
 * same reason -- ten rows, nine of them normally off, and a count that says so folded. */
registerSection({ key: "pickups", prefix: "pickup: ", title: "pickups", startOpen: false });

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
