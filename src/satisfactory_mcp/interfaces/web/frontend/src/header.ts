/* The page's identity line, and the one dot that is not a thing the player built.
 *
 * ITS OWN MODULE BECAUSE /api/summary HAS TWO CONSUMERS. One reply carries the world's name,
 * its phase, its power figures and its age -- all of which are text in the header -- and also
 * the player's last known position, which is a mark on the map. The registry gives one entry
 * one draw, so the obvious split would be two entries for one path, and `get()` has no
 * dedupe: that is two requests for one answer on every autosave, for ever, with nothing on
 * screen to show for it. Grouping entries by path inside the registry would fix that and buy
 * a fan-out, a union of the layer prefixes and an ambiguous toast label, all to serve one
 * endpoint. So the two consumers meet HERE instead, which is the same place they met when
 * load.ts held the block, and the registry stays one entry per request.
 *
 * The header is also what a switch is waiting for. `settles: true` is the whole of that
 * decision: the dimmed map and the "loading…" line are cleared when this reply lands, either
 * way, because this is the reply that replaces the words the switch put there.
 */

import { el } from "./dom";
import { phaseText } from "./format";
import { drawPlayer } from "./markers";
import { registerFetch } from "./registry";

import type { SummaryResponse } from "./api-types";

function drawHeader(s: SummaryResponse): void {
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
  // Set in the same breath as the text, and so is the failure branch below. `title` is a
  // property of the element, not of the string just written to it, so a branch that only
  // touches `textContent` leaves the PREVIOUS world's tooltip hanging off the new world's
  // header -- and this tooltip is the only place the measured/nameplate split is spelled
  // out, so what survives is three specific power figures presented as this world's.
  // worlds.ts and reload() both do the pair together for the same reason.
  span.title =
    "power: " +
    Math.round(measured) +
    " MW measured draw; " +
    Math.round(power.draw_mw) +
    " MW nameplate if every machine ran at once; " +
    Math.round(power.generation_mw) +
    " MW generation capacity";
}

/* The header is the page's identity line; a failure leaves a statement, not a blank that
 * reads as "everything is fine, there is just nothing here". Tooltip included: leaving the
 * previous world's power figures hovering over the words "could not be read" is worse than
 * the blank, because it is an answer. */
var UNREADABLE = "this world's save could not be read";

registerFetch<SummaryResponse>({
  wave: "live",
  rank: 30,
  path: "/api/summary",
  label: "summary",
  // The player dot and nothing else: everything else this draws is text, and text is
  // replaced by `failed` below rather than emptied.
  clears: ["player"],
  // Neither the header nor the you-are-here dot is a thing a storey contains.
  refilters: false,
  settles: true,
  draw: drawHeader,
  failed: function () {
    el("summary").textContent = UNREADABLE;
    el("summary").title = UNREADABLE;
  },
});
