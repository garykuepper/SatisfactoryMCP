/* The live loop: one EventSource, and what a save write means.
 *
 * Small and separate because the failure it exists to prevent is specific -- a page quietly
 * presenting stale data as live.
 */

import { el } from "./dom";
import { loadLive } from "./load";
import { state } from "./state";
import { fail } from "./toast";
import { refreshWorlds } from "./worlds";

/* One EventSource for the process; a save write is an edge trigger and the response is a
 * refetch of the two things a save can change. The grey dot means connecting, retrying or
 * dead, so its title says which, and losing an ESTABLISHED connection also says so in a
 * toast. */
export function listen() {
  var source = new EventSource("/api/events");
  var dot = el("live");
  var wasOpen = false;
  dot.title = "connecting to the save watcher…";
  source.onopen = function () {
    wasOpen = true;
    dot.className = "dot on";
    dot.title = "live: watching for save writes";
  };
  source.onerror = function () {
    var lost = wasOpen;
    wasOpen = false;
    dot.className = "dot";
    dot.title = lost
      ? "live connection lost — is the server still running? Retrying…"
      : "connecting to the save watcher…";
    if (lost) fail("live updates lost — what is on screen may be stale");
  };
  source.addEventListener("save", function (event) {
    // The stream replays the newest save to every new subscriber, so the first event
    // usually describes a write that happened BEFORE this page opened: not news, and
    // refetching it would double-load the whole page.
    var payload = null;
    try {
      payload = JSON.parse(event.data);
    } catch (ignored) {
      /* a malformed event is treated as news, the safe direction */
    }
    if (payload && payload.mtime && payload.mtime * 1000 < state.opened - 2000) return;
    dot.className = "dot hit";
    setTimeout(function () {
      dot.className = "dot on";
    }, 800);
    refreshWorlds();
    // A pinned save is pinned: the point of the picker is to hold a view while the game
    // autosaves over the newest. The dot still blinks so the write is not invisible.
    if (!state.save) loadLive();
  });
}
