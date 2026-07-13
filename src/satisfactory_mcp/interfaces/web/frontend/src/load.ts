/* Who fetches what, and when: the two waves, the epoch guard, and the redraw.
 *
 * The split between `loadStatic` and `loadLive` is a claim about the data rather than about
 * the code -- nodes, concrete and routes change when the player builds, machines and
 * collectibles change on every autosave -- so a save event refetches one wave and a world
 * switch refetches both. This module is where every draw function is finally called, which
 * makes it the one file that knows the whole page, and the reason every other module can
 * afford not to.
 */

import { get } from "./api";
import { el } from "./dom";
import { inFloorMode, leaveFloors, refilterFloors, refreshFloors } from "./floors";
import { phaseText } from "./format";
import { drawFactories } from "./labels";
import { clearPrefixed } from "./layers";
import { L } from "./leaflet";
import { map, writeHash } from "./map";
import { drawCollectibles, drawNodes, drawPlayer } from "./markers";
import { drawMachines, drawStorage, drawStructures } from "./placements";
import { drawPower } from "./power";
import { drawRegions } from "./regions";
import { drawBelts, drawPipes } from "./routes";
import { state } from "./state";
import { fail, friendly } from "./toast";

import type {
  BeltsResponse,
  CollectiblesResponse,
  FactoriesResponse,
  MachinesResponse,
  NodesResponse,
  PipesResponse,
  PowerResponse,
  StorageResponse,
  StructuresResponse,
  SummaryResponse,
} from "./api-types";

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

/* Every loader below is epoch-guarded: a switch bumps `state.epoch`, and a reply that
 * comes back for an earlier epoch is dropped instead of drawn. Without this, whichever
 * world answered LAST owned the map -- switch away from a slow world and its late reply
 * silently repainted everything under the new world's name.
 *
 * The catch paths clear their layers before tosting: a failed switch must leave those
 * layers empty, not showing the previous world under the new world's header. */

/* Every draw goes through here, and it says two things at once.
 *
 * The epoch guard is the older one: a reply that comes back for a world nobody is looking at
 * any more is dropped rather than drawn. What is new is the line after the draw -- a redraw
 * replaces a layer's CONTENTS, and the floor filter is a fact about contents, so a layer
 * refetched during floor mode would arrive holding every storey at once. Spelled once here
 * rather than eight times below, because eight copies is eight chances to forget the second
 * half in the ninth. */
function drew<T>(live: () => boolean, draw: (data: T) => void): (data: T) => void {
  return function (data) {
    if (!live()) return;
    draw(data);
    refilterFloors();
  };
}

export function loadStatic() {
  // Nodes and factory shapes change only when the player builds, so they are refetched
  // on a world switch rather than on every save write.
  var epoch = state.epoch;
  var live = function () {
    return epoch === state.epoch;
  };
  get<NodesResponse>("/api/nodes")
    .then(drew(live, drawNodes))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["node: "]);
      fail("nodes: " + friendly(e));
    });
  get<StructuresResponse>("/api/structures")
    .then(drew(live, drawStructures))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["foundations"]);
      fail("structures: " + friendly(e));
    });
  get<BeltsResponse>("/api/belts")
    .then(drew(live, drawBelts))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["belts"]);
      fail("belts: " + friendly(e));
    });
  get<PipesResponse>("/api/pipes")
    .then(drew(live, drawPipes))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["pipes"]);
      fail("pipes: " + friendly(e));
    });
  // Static, and it belongs in this wave rather than the live one for the reason the belts do:
  // a wire changes when the player builds, not when the game autosaves.
  get<PowerResponse>("/api/power")
    .then(function (d) {
      if (live()) drawPower(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["power"]);
      fail("power: " + friendly(e));
    });
  get<StorageResponse>("/api/storage")
    .then(drew(live, drawStorage))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["storage"]);
      fail("storage: " + friendly(e));
    });
  get<FactoriesResponse>("/api/factories")
    .then(drew(live, drawFactories))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["factory labels", "proposals"]);
      fail("factories: " + friendly(e));
    });
}

export function loadLive() {
  var epoch = state.epoch;
  var live = function () {
    return epoch === state.epoch;
  };
  get<MachinesResponse>("/api/machines")
    .then(drew(live, drawMachines))
    .then(function () {
      // A save write changes what is BUILT, so it changes the decomposition -- and the ids a
      // band lists are what the floor filter runs on. Without this a machine placed since the
      // view was opened would be drawn by /api/machines, listed by no band, and therefore
      // silently missing from every floor rather than visibly new on one.
      if (live()) refreshFloors();
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["machines", "extractors", "generators"]);
      fail("machines: " + friendly(e));
    });
  get<CollectiblesResponse>("/api/collectibles?mode=remaining")
    .then(drew(live, drawCollectibles))
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["pickup: "]);
      fail("collectibles: " + friendly(e));
    });
  get<SummaryResponse>("/api/summary")
    .then(function (s) {
      if (!live()) return;
      busy(false);
      drawPlayer(s.player);
      var power = s.power;
      // No fallback to the nameplate any more, because there was never a case to fall back
      // FROM: `PowerReport` starts the measured figure at 0.0 and charges an unmonitored
      // machine in full, so it is always a number. The old `?? draw_mw` would have printed
      // the nameplate total under the word "drawn" on the one save it could ever have fired
      // for, which is the opposite of what the split exists to say.
      var measured = power.measured_draw_mw;
      var parts = [s.header.session_name];
      var phase = phaseText(s.progression.game_phase);
      if (phase) parts.push(phase);
      // The measured figure, labelled: the nameplate total alone reads as "one factory
      // from a brown-out" on a base that is mostly idle. Both live in the tooltip.
      parts.push(Math.round(measured) + " MW drawn / " + Math.round(power.generation_mw) + " MW capacity");
      parts.push(s.age_note);
      var span = el("summary");
      span.textContent = parts.join(" — ");
      // Set in the same breath as the text, and so are the two branches that replace this
      // one below. `title` is a property of the element, not of the string just written to
      // it, so a branch that only touches `textContent` leaves the PREVIOUS world's tooltip
      // hanging off the new world's header -- and this tooltip is the only place the
      // measured/nameplate split is spelled out, so what survives is three specific power
      // figures presented as this world's. worlds.ts does both together for the same reason.
      span.title =
        "power: " +
        Math.round(measured) +
        " MW measured draw; " +
        Math.round(power.draw_mw) +
        " MW nameplate if every machine ran at once; " +
        Math.round(power.generation_mw) +
        " MW generation capacity";
    })
    .catch(function (e) {
      if (!live()) return;
      busy(false);
      clearPrefixed(["player"]);
      // The header is the page's identity line; a failure leaves a statement, not a
      // blank that reads as "everything is fine, there is just nothing here". Tooltip
      // included: leaving the previous world's power figures hovering over the words
      // "could not be read" is worse than the blank, because it is an answer.
      var failed = "this world's save could not be read";
      el("summary").textContent = failed;
      el("summary").title = failed;
      fail("summary: " + friendly(e));
    });
}

/* A switch in progress is marked on screen -- header says so, map dims -- because the
 * old world's layers stay visible until the new responses land, and an unmarked blend of
 * two worlds reads as data. Cleared when this epoch's summary settles either way. */
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
  // Same reason the popup is closed one line up, and the same reason the two branches in
  // loadLive() set both: a tooltip is a claim about the previous world too, and this one
  // outlives the switch by the whole length of a 3 s parse if it is not replaced here.
  var loading = note || "loading…";
  el("summary").textContent = loading;
  el("summary").title = loading;
  busy(true);
  writeHash();
  loadStatic();
  loadLive();
}
