/* The other half of the page's address: reading a fragment somebody typed.
 *
 * `writeHash` in map.ts has always written `#world=…&save=…&mode=…&z=…&c=x,y`, and `BOOT` in
 * state.ts has always parsed it -- ONCE, at load. So the fragment was a link format and not
 * an address: paste it into a new tab and it worked, edit it in the tab already open and
 * nothing whatsoever happened. The page went on drawing the old world under a URL that said
 * the new one, which is the one failure worse than ignoring the edit, because the address bar
 * is then a caption on the map and it is wrong.
 *
 * WHY THIS IS ITS OWN MODULE. Applying a fragment means reaching the world picker, the base
 * map and the viewport, and no existing file can reach all three: state.ts deliberately
 * imports nothing, map.ts is below tiles.ts in the graph, and worlds.ts is imported BY the
 * loader it would have to call. This file sits above all of them and is imported only by
 * main.ts, which is what keeps the graph acyclic -- the same reason `onSettled` exists in
 * layercontrol.ts, arrived at from the other direction.
 *
 * WHAT IT DOES NOT DO is decide anything. Every branch below ends in a call that already
 * existed and is already the page's one way of doing that thing: `reload()` for a world or
 * save change (epoch bump, busy marker, both waves), `setMode()` for the picture,
 * `map.setView` for the viewport. A fragment is a request to press the buttons the page
 * already has, not a second way of changing the page.
 */

import { applyFloorFragment } from "./floors";
import { reload } from "./load";
import { map, writeHash, writtenHash } from "./map";
import { parseHash, pinnedPath, state } from "./state";
import { setMode } from "./tiles";
import { syncPickers } from "./worlds";

import type { BaseMode } from "./state";

/** The four modes, as the set a typed fragment is checked against. A fragment naming
 *  something else is ignored rather than resolved to plain: `mode=terrian` is a typo, and
 *  silently switching the base map off is a strange answer to one. */
var MODES: BaseMode[] = ["artwork", "terrain", "satellite", "plain"];

function askedMode(raw: string | undefined): BaseMode | null {
  var found: BaseMode | null = null;
  MODES.forEach(function (mode) {
    if (mode === raw) found = mode;
  });
  return found;
}

/* The subject half: which world and which save.
 *
 * Both are checked against what the page actually has rather than trusted. A world id the
 * scan never returned is a stale bookmark, and adopting it would give the header a name with
 * no saves under it; a filename this world does not hold resolves to "" through `pinnedPath`,
 * which is the page's existing spelling of "follow the newest save".
 *
 * Returns whether anything moved, because the caller owes a `reload()` if so and must not
 * fire one if not -- a reload is a visible event (the map dims, the header says "loading")
 * and firing it for a fragment that changed only `z` would make every pan look like a switch.
 */
function applySubject(asked: Record<string, string>): boolean {
  var world = asked.world;
  var known = !!world && state.worlds.some(function (w) { return w.world_id === world; });
  var nextWorld = known ? world! : state.world;
  // Resolved against the world being switched TO, not the one on screen: the two have
  // different save lists, and a filename is only meaningful inside one of them.
  var target = state.worlds.filter(function (w) { return w.world_id === nextWorld; })[0] || null;
  var nextSave = pinnedPath(asked.save || "", target);
  if (nextWorld === state.world && nextSave === state.save) return false;
  state.world = nextWorld;
  state.save = nextSave;
  syncPickers();
  return true;
}

/** The viewport half. Both numbers or neither: a fragment with a `z` and no `c` moves the
 *  zoom and leaves the centre, which is what `map.setView` would do anyway. */
function applyView(asked: Record<string, string>): void {
  var zoom = isFinite(+asked.z!) ? +asked.z! : map.getZoom();
  var centre = map.getCenter();
  var lat = centre.lat;
  var lng = centre.lng;
  if (asked.c) {
    var raw = asked.c.split(",");
    if (raw.length === 2 && isFinite(+raw[0]!) && isFinite(+raw[1]!)) {
      lng = +raw[0]!;
      lat = -+raw[1]!; // the page's one coordinate rule, inverted; see map.ts
    }
  }
  if (zoom === map.getZoom() && lat === centre.lat && lng === centre.lng) return;
  map.setView([lat, lng], zoom);
}

/* One fragment, applied in the order the fragment itself is written: subject, then picture,
 * then viewport. That order is not cosmetic -- a world switch closes the popup and dims the
 * map, and doing it after the flight would throw the flight's own settling away.
 *
 * It always ENDS in a write, and that is the point of the last two lines. What was typed may
 * be a shorthand (`#z=0`), or may name a mode this machine cannot draw and got `plain`
 * instead; either way the address bar would go on asserting something the page is not doing.
 * `reload()` writes the fragment itself, so the two branches are one write between them --
 * and that write is what `writtenHash` recognises when the browser fires the event for it.
 */
function apply(hash: string): void {
  if (hash === writtenHash()) return; // the page's own handwriting; see writtenHash
  var asked = parseHash(hash);
  var moved = applySubject(asked);
  // How much of the subject, before the picture and before the viewport: entering floor mode
  // flies the map, so a `#floor=…&z=…&c=…` that applied the viewport first would have its own
  // viewport thrown away by the flight. Same reasoning, one level down, as the world switch
  // coming before the flight.
  var floored = applyFloorFragment(asked.floor);
  var mode = askedMode(asked.mode);
  // `false`: not because a typed mode is unpinned, but because the write it would do here is
  // the write two lines down, and one normalising write beats two.
  if (mode && mode !== state.mode) setMode(mode, false);
  // Not while the floor half is still moving: `enterFloors` is a fetch and a flight, and it
  // writes the fragment itself when it lands. Applying a stale `z` and `c` over it would
  // undo the flight the same request just asked for.
  if (!floored) applyView(asked);
  if (moved) reload("following the address bar…");
  else if (!floored) writeHash();
}

export function listenToFragment(): void {
  window.addEventListener("hashchange", function () {
    apply(location.hash);
  });
}
