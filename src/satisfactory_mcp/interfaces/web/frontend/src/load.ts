/* When the page fetches, and what a reply is allowed to do when it lands.
 *
 * WHAT is fetched is not here any more: each feature declares its own entry in registry.ts,
 * and this file runs the list without knowing a single one of the names in it. What is left
 * is the machinery every entry shares and no entry should be spelling for itself -- the two
 * waves, the epoch guard that drops a reply nobody is waiting for, the clear a failure owes
 * the layers it was going to draw into, and the mark on screen while a switch is in flight.
 *
 * THIS FILE IMPORTS NO MODULE THAT REGISTERS A FETCH, and that is a ratchet rather than a
 * habit: it used to import all seven of them, and stripping those imports is what makes the
 * registrations reachable only through main.ts's FEATURES block. See registry.ts.
 *
 * The one fetch that is not in the registry is the first one below. /api/regions is geography
 * -- no world to scope it to, no epoch to guard it against, fetched once for the life of the
 * page -- so it is neither wave, and it is the reason `regions.ts` is the single feature
 * module this file still names.
 */

import { get } from "./api";
import { el } from "./dom";
import { inFloorMode, leaveFloors, refilterFloors } from "./floors";
import { clearPrefixed } from "./layers";
import { L } from "./leaflet";
import { map, writeHash } from "./map";
import { fetcherFor, fetchersOf } from "./registry";
import { drawRegions } from "./regions";
import { state } from "./state";
import { fail, friendly } from "./toast";

import type { ApiError, ApiUrl } from "./api";
import type { Registered } from "./registry";

export function loadRegions() {
  // Geography, not save state: no world parameter, fetched once, never refetched.
  return fetch("/api/regions")
    .then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok || body.error) throw new Error(body.error || r.status + " /api/regions");
        return body;
      });
    })
    .then(drawRegions)
    .catch(function (e) {
      fail("regions: " + friendly(e));
    });
}

/* One registered fetch, from the request to whatever the reply is allowed to do.
 *
 * THE EPOCH GUARD is the older half and the reason this is one function rather than ten. A
 * switch bumps `state.epoch`, and a reply that comes back for an earlier epoch is dropped
 * instead of drawn -- without it, whichever world answered LAST owned the map, so switching
 * away from a slow world let its late reply silently repaint everything under the new world's
 * name. It is checked in both places a reply can arrive, and the catch checks it BEFORE
 * clearing anything: a failure belonging to a world nobody is looking at must not empty the
 * layers the current world has just filled.
 *
 * The catch clears before it toasts, for the mirror reason: a failed switch must leave those
 * layers empty rather than showing the previous world under the new world's header.
 *
 * `after` runs inside the same guarded block as the draw rather than in a `.then` of its own,
 * which is where /api/machines' floor refresh used to sit. A second `.then` is a microtask
 * later and so genuinely needed a second guard; here nothing can run between the two lines.
 * The only thing that bumps the epoch is `reload`, and `reload` leaves floor mode on its way
 * past, so the hook's own "am I still in a view" check is the same question anyway. */
function run(fetcher: Registered): void {
  var epoch = state.epoch;
  var live = function () {
    return epoch === state.epoch;
  };
  get<ApiError>(fetcher.path)
    .then(function (body) {
      if (!live()) return;
      if (fetcher.settles) busy(false);
      fetcher.draw(body);
      // A redraw replaces a layer's CONTENTS, and the floor filter is a fact about contents,
      // so a layer refetched during floor mode would arrive holding every storey at once.
      // Which entries do this and which do not is stated per entry; see `refilters`.
      if (fetcher.refilters) refilterFloors();
      if (fetcher.after) fetcher.after();
    })
    .catch(function (e) {
      if (!live()) return;
      if (fetcher.settles) busy(false);
      clearPrefixed(fetcher.clears);
      if (fetcher.failed) fetcher.failed();
      fail(fetcher.label + ": " + friendly(e));
    });
}

/* Nodes and factory shapes change only when the player builds, so they are refetched on a
 * world switch rather than on every save write. */
export function loadStatic(): void {
  fetchersOf("static").forEach(run);
}

/* What an autosave can have changed: the machines standing on the plan, what has been picked
 * up, and the header's own reading of the save. */
export function loadLive(): void {
  fetchersOf("live").forEach(run);
}

/** One registered fetch on its own, guarded and cleared exactly as its wave would have done
 *  it. For the caller that wants a single layer outside both waves: worlds.ts draws the node
 *  table on a page with no readable save at all, and geography needs no save. Nothing happens
 *  if no feature claimed this path -- which is what main.ts's FEATURES block is there to
 *  prevent, and what test_architecture.py checks. */
export function loadOne(path: ApiUrl): void {
  var fetcher = fetcherFor(path);
  if (fetcher) run(fetcher);
}

/* A switch in progress is marked on screen -- header says so, map dims -- because the
 * old world's layers stay visible until the new responses land, and an unmarked blend of
 * two worlds reads as data. Cleared when this epoch's settling fetch lands either way; which
 * one that is is declared by the fetcher, and it is the summary, because the summary is what
 * replaces the header text this turned on. */
function busy(on: boolean): void {
  var container = el("map");
  if (on) L.DomUtil.addClass(container, "busy");
  else L.DomUtil.removeClass(container, "busy");
}

export function reload(note?: string): void {
  state.epoch += 1;
  map.closePopup(); // an open card is a claim about the previous world/save
  // ...and so is an open floor view, twice over: a platform index is what ONE decomposition
  // handed out, and the ids on its bands name machines in the save being left. Leaving the
  // mode is the honest move; re-entering on the new world is one click and one link.
  if (inFloorMode()) leaveFloors();
  // Same reason the popup is closed one line up, and the same reason both branches of the
  // header's own fetch set both: a tooltip is a claim about the previous world too, and this
  // one outlives the switch by the whole length of a 3 s parse if it is not replaced here.
  var loading = note || "loading…";
  el("summary").textContent = loading;
  el("summary").title = loading;
  busy(true);
  writeHash();
  loadStatic();
  loadLive();
}
