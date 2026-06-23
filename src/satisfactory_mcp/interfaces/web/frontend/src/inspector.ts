/* The right-click inspector: "what is here?", answered where the question is asked.
 *
 * Its own module because it is the one thing on this page that is not a layer. It draws
 * nothing, owns no group, appears in no checkbox, and answers about a point the player picked
 * rather than about anything the save contains -- so it has no business inside a drawing
 * module, and every drawing module would otherwise be a plausible home for it.
 */

import { get } from "./api";
import { esc, html, popup } from "./dom";
import { regionLine, shortResource } from "./format";
import { L } from "./leaflet";
import { map } from "./map";
import { friendly } from "./toast";

import type { Elevation, InspectResponse } from "./api-types";
import type { Row } from "./dom";
import type { InspectedEvent } from "./leaflet-private";

/* Right-click anywhere: "what is here?" answered where the question is asked.
 *
 * The map is drawn from a save and a node table, and until now it could show WHERE things
 * are and nothing about the ground they stand on. /api/inspect answers the three things a
 * site starts with -- the named region, the measured elevation, the nearest nodes -- and
 * all three come from the domain layer, so the only work below is turning a latlng back
 * into game coordinates and laying the answer out.
 *
 * The inverse of the page's one coordinate rule: a point plotted at [-y, x] reads back as
 * x = lng, y = -lat. Rounded to a decimetre because the popup prints the same numbers it
 * asked with, and a coordinate you cannot retype is not a copyable coordinate.
 */

function elevationRows(e: Elevation): Row[] {
  // The extracted heightfield goes first when there is one, because it is the only answer
  // measured AT the point rather than near it. Which layer of the field answered rides
  // along with it: a landscape texel and a fill texel are both "the terrain" and they are
  // a metre and four metres good respectively, so quoting one number for both would be
  // the same overclaim as one median over nodes and foundations.
  var rows: Row[] = [];
  if (e.terrain_m !== null && e.terrain_m !== undefined) {
    var acc = e.terrain_accuracy_m === null ? "" : " ±" + e.terrain_accuracy_m + " m";
    rows.push(["terrain", e.terrain_m + " m (" + e.terrain_source + acc + ")"]);
    // Water is information, never a correction: the field's own generator measured that
    // gating terrain on it makes the terrain worse, so it is shown beside the ground and
    // never instead of it.
    if (e.terrain_water_m !== null && e.terrain_water_m !== undefined) {
      rows.push(["water", e.terrain_water_m + " m surface, above this ground"]);
    }
  }
  // Unsurveyed ground gets one line, not three saying the same nothing. With no field and
  // nothing standing nearby, "nothing known" is a real answer and the honest one.
  if (!e.ground_count && !e.built_count) {
    if (rows.length) return rows;
    return [["elevation", "nothing known within " + e.radius_m + " m"]];
  }
  // Ground and built stay apart, exactly as the server sends them: a node rests on
  // terrain and a foundation is wherever the player put it, so one median labelled
  // "elevation" would be the platform's height on any developed site.
  rows.push([
    "ground",
    e.ground_count
      ? e.ground_m + " m (median of " + e.ground_count + ", spread " + e.ground_spread_m + " m)"
      : "no ground samples within " + e.radius_m + " m",
  ]);
  if (e.built_count) {
    rows.push(["built", e.built_m + " m (median of " + e.built_count + ")"]);
  }
  // A missing fill is printed as the REASON it is missing, never as 0: zero fill is a
  // real and different measurement, and a blank row reads as a bug in the map.
  rows.push(["fill", e.fill_m === null ? e.fill_note : e.fill_m + " m"]);
  return rows;
}

function inspectHtml(d: InspectResponse): string {
  var rows: Row[] = ([["region", regionLine(d.region)]] as Row[]).concat(
    elevationRows(d.elevation)
  );
  d.nearest.forEach(function (n, i) {
    rows.push([
      i ? "" : "nearest",
      html(
        esc(shortResource(n.resource) + " " + n.purity) +
          " &middot; " +
          esc(n.distance_m + " m") +
          (n.occupied ? " (occupied)" : "")
      ),
    ]);
  });
  // Said out loud rather than left to be inferred: with no save there is no built
  // population and no occupancy, so every node above reads as free whether it is or not.
  if (d.save_error) rows.push(["save", d.save_error + " — nodes only, occupancy unknown"]);
  // The one row built to be copied into an MCP tool call, so the unit -- the same " m"
  // every other coordinate row on the map ends with -- must ride along.
  rows.push(["at", html("<code>" + esc(d.at.x_m + ", " + d.at.y_m) + "</code> m")]);
  return popup(rows);
}

/* The right-click handler itself, named rather than registered here: main.ts wires every
 * map listener in one block, in the order the single-file page registered them, because
 * Leaflet fires listeners in registration order and that order is now the only thing a
 * reader cannot see by looking at one module. */
export function inspect(e: L.LeafletMouseEvent): void {
  // One right-click can reach this twice -- Leaflet fires at the layer under the cursor
  // and the event propagates to the map -- so the DOM event carries a mark. Two fetches
  // and two popups for one click is the bug this one line removes.
  var dom = e.originalEvent as InspectedEvent | undefined;
  if (dom) {
    if (dom._inspected) return;
    dom._inspected = true;
  }
  var x = Math.round(e.latlng.lng * 10) / 10;
  var y = Math.round(-e.latlng.lat * 10) / 10;
  // Opened before the fetch, so the click has a visible effect on a slow answer and the
  // popup lands exactly where the pointer was rather than where the map has drifted to.
  var card = L.popup({ maxWidth: 340 })
    .setLatLng(e.latlng)
    .setContent("inspecting " + x + ", " + y + " m&hellip;")
    .openOn(map);
  get<InspectResponse>(("/api/inspect?x_m=" + x + "&y_m=" + y) as `/api/inspect?${string}`)
    .then(function (d) {
      if (map.hasLayer(card)) card.setContent(inspectHtml(d));
    })
    .catch(function (err) {
      if (map.hasLayer(card)) card.setContent(popup([["inspect failed", friendly(err)]]));
    });
}
