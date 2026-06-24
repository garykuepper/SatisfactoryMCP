/* The map. Reads /api, draws markers, and refetches when the game writes a save.
 *
 * Two coordinate facts drive everything below.
 *
 *   1. The API already speaks metres. Nothing here divides by 100 -- if a number looks
 *      like centimetres, the bug is server-side.
 *   2. Satisfactory is +X east and +Y SOUTH, while Leaflet's CRS.Simple is +lat north.
 *      So a point is plotted at [-y, x], and that negation is the only place the two
 *      conventions meet. Doing it anywhere else produces a map that is correct until
 *      someone compares it with an in-game coordinate. It lives in map.ts.
 *
 * And one content fact: every string that reaches a popup or a label is DATA -- factory
 * names and notes from the player's label file, class ids from the save, region names
 * from a JSON file. popup() escapes everything by default; the few rows that need markup
 * say so explicitly with html(). A factory named "<b>x</b>" prints as its own text.
 *
 * This file is the entry point and holds no drawing of its own. What it does hold is the
 * ORDER of two things, and both are here because neither is visible from inside a module:
 * which map events are listened for, and what happens on load.
 */

import "leaflet/dist/leaflet.css";
import "./style.css";

import { inspect } from "./inspector";
import { declutter } from "./labels";
import { isBatching, onSettled } from "./layercontrol";
import { loadLive, loadRegions, loadStatic } from "./load";
import { map, writeHash } from "./map";
import { noteRegionChoice, updateRegionBlend } from "./regions";
import { ROUTE_LAYERS, sinkRoutes, styleRoutes } from "./routes";
import { listen } from "./sse";
import { state } from "./state";
import { loadBaseMap } from "./tiles";
import { loadWorlds } from "./worlds";

/* ------------------------------------------------------------------- wiring */

/* Every map listener the page adds, in one block and in this order on purpose.
 *
 * Leaflet fires listeners in registration order, and when this was one file that order was
 * simply where each `map.on` happened to sit -- readable, because there was one file to read.
 * Split across modules it would instead be a consequence of the import graph: reordering two
 * imports in this file, or adding an import to a module that already had one, would silently
 * reorder the handlers. Three of these events have more than one listener, so that is a real
 * consequence and not a hypothetical one.
 *
 *   zoomend            writeHash, styleRoutes, declutter
 *   overlayadd         (the control's own decorator), noteRegionChoice, sinkRoutes, declutter
 *   overlayremove      (the control's own decorator), noteRegionChoice, declutter
 *
 * The control's decorator is not in this list because it is registered while the control is
 * being built, which is the only moment it can be, and it therefore always comes first --
 * exactly as it did before.
 */
map.on("moveend zoomend", writeHash);
map.on("layeradd layerremove", updateRegionBlend);
// Which of the region box's ticks were the player's, which is what makes the base map's
// default for it a default rather than an override. See regionsUnderMode.
map.on("overlayadd overlayremove", noteRegionChoice);
map.on("zoomend", styleRoutes);

// A layer added long after both fetches landed is appended to the canvas' draw list, i.e.
// on top of everything -- so the sink has to run again when the player ticks the box.
map.on("overlayadd", function (event) {
  if (ROUTE_LAYERS.some(function (n) { return state.layers[n] === event.layer; })) sinkRoutes();
});

/* Same batch guard as the control decorator, and for a heavier reason: this pass measures
 * every label's screen rectangle, so running it once per member of a fourteen-layer family
 * is fourteen forced layouts to reach one answer. batch() runs it once at the end -- through
 * onSettled, so that the control can say "the list has stopped changing" without importing
 * the module that knows what a label is. */
map.on("zoomend overlayadd overlayremove", function () {
  if (!isBatching()) declutter();
});
onSettled(declutter);

map.on("contextmenu", inspect);

/* -------------------------------------------------------------------- boot */

/* In this order and not in parallel: the base map's mode decides whether the region tint
 * starts on, so the group it decides about has to exist by then. */
loadRegions().then(loadBaseMap);

loadWorlds().then(function () {
  // With no world there is nothing to fetch: firing the loaders anyway would bury the
  // persistent "no readable saves" line under six toasts and a fake header.
  if (state.worlds.length) {
    loadStatic();
    loadLive();
  }
  listen();
});
