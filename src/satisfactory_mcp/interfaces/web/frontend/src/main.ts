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

import { applyFloorFragment, escapeLeavesFloorMode, noteFloorChoice } from "./floors";
import { listenToFragment } from "./fragment";
import { inspect } from "./inspector";
import { declutter } from "./labels";
import { isBatching, onSettled } from "./layercontrol";
import { loadLive, loadRegions, loadStatic } from "./load";
import { map, writeHash } from "./map";
import { noteRegionChoice, updateRegionBlend } from "./regions";
import { ROUTE_LAYERS, sinkRoutes, styleRoutes } from "./routes";
import { listen } from "./sse";
import { BOOT, state } from "./state";
import { loadBaseMap } from "./tiles";
import { loadWorlds } from "./worlds";

/* ---------------------------------------------------------------- features */

/* Every module that declares a fetch, imported for that side effect alone.
 *
 * ORDER IS NOT LOAD-BEARING HERE, unlike the listener block below, and that is a property
 * bought rather than hoped for: `registerFetch` takes an explicit `rank` and `fetchersOf`
 * sorts by it, precisely so that the sequence the requests go out in cannot become a
 * consequence of the import graph. A feature is one appended line, in whatever place keeps
 * this list alphabetical.
 *
 * Bare imports, because there is no name to take: each module registers what it wants fetched
 * as it is evaluated, and load.ts deliberately imports none of them -- it runs the registry
 * and knows none of these names. That is what makes this block load-bearing in a way nothing
 * about it looks: delete a line and its layer is simply never fetched, with no compile error
 * and no runtime one either. `test_architecture.py` checks this list against the set of
 * modules that call `registerFetch`, in both directions.
 *
 * Two of them are imported by name above as well, for something else entirely -- the route
 * layers and the declutter pass. They are repeated here anyway: the set of modules that
 * fetch is a fact worth reading in one place, and a rule with two exceptions in it is a rule
 * nobody can check at a glance. */
import "./header";
import "./labels";
import "./markers";
import "./placements";
import "./power";
import "./routes";

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
 *   overlayadd         (the control's own decorator), noteRegionChoice, noteFloorChoice,
 *                      styleRoutes + sinkRoutes, declutter
 *   overlayremove      (the control's own decorator), noteRegionChoice, noteFloorChoice,
 *                      declutter
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
/* The same question for floor mode, and it runs AFTER the region one because the two are
 * independent and this is the order a reader should find them in: the older rule first.
 * A layer floor mode turned on stops being floor mode's the moment the reader touches its
 * box -- and a layer ticked on mid-mode owes the floor filter a pass, which this does too. */
map.on("overlayadd overlayremove", noteFloorChoice);
map.on("zoomend", styleRoutes);

/* A layer added long after both fetches landed is appended to the canvas' draw list, i.e. on
 * top of everything -- so the sink has to run again when the player ticks the box. And so
 * does the restyle, for the mirror-image reason: styleRoutes skips a layer that is not on the
 * map, so the pixel sizes of a layer ticked on are those of the zoom it was last drawn or
 * styled at, which after a world-view pan is a hairline. Style first, then sink, which is the
 * order both draw functions already end in -- size a piece, then decide what it sits under --
 * and not a dependency: `sinkRoutes` reorders and `styleRoutes` mutates, neither undoes the
 * other. Stated so the pair reads the same way in all three places. */
map.on("overlayadd", function (event) {
  if (!ROUTE_LAYERS.some(function (n) { return state.layers[n] === event.layer; })) return;
  styleRoutes();
  sinkRoutes();
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

/* The two listeners here that are not the map's: the address bar, and the one key this page
 * binds. Registered beside the map's because they are the same kind of fact -- events the
 * page reacts to, wired where a reader can see the whole set -- and the fragment one is
 * registered BEFORE the loaders below, so a fragment edited during the first fetch is not
 * dropped on the floor.
 *
 * ESC on the document rather than on the map, because floor mode is a state of the PAGE: the
 * key has to work with the keyboard in the layer control's floor picker, which is where a
 * reader who has just walked six storeys is most likely to be. */
listenToFragment();
document.addEventListener("keydown", escapeLeavesFloorMode);

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
    /* ...and the fragment's floor half, which is the one part of the address that cannot be
     * applied before there is a world to apply it to. Fired here rather than waiting for the
     * two waves to land: `/api/floors` is its own request and the view owes a flight until
     * the concrete arrives, which the redraw pass then makes for it. */
    applyFloorFragment(BOOT.floor);
  }
  listen();
});
