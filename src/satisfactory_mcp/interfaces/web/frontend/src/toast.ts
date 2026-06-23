/* The message strip: what the page says when something failed, and the one thing it says
 * when something went right without being asked.
 *
 * Separated from the drawing because every loader needs it and none of them should have to
 * know how it is drawn -- and because the two kinds of message are one mechanism with two
 * lifetimes and two colours, which is a decision worth keeping in one place.
 */

import { el } from "./dom";

/* Errors stack instead of overwriting each other: six endpoints failing together used to
 * collapse into whichever message landed last, gone six seconds later. Each failure gets
 * its own row, stays up long enough to read, and a click dismisses it -- so the toast is
 * never an undismissable patch of dead map. */
var FAIL_MS = 12000;

/* The same strip carries the page's one non-failure message: "I turned a layer on for
 * you". One mechanism, so a note cannot end up somewhere a reader has not learned to
 * look -- and a different colour, because a note the eye reads as an error is worse than
 * no note. Shorter-lived too: a failure has to survive being read twice, a note describes
 * something the reader can already see on the map. */
var NOTE_MS = 6000;

export function toast(message: string, kind: "fail" | "note", ms: number): void {
  var box = el("err");
  var rows: Element[] = Array.prototype.slice.call(box.children);
  rows.forEach(function (row) {
    // The same message twice is one problem, not two rows.
    if (row.textContent === message) row.remove();
  });
  var row = document.createElement("div");
  row.className = "err-row " + kind;
  row.textContent = message;
  row.title = "click to dismiss";
  row.onclick = function () {
    row.remove();
  };
  box.appendChild(row);
  setTimeout(function () {
    row.remove();
  }, ms);
}

export function fail(message: string): void {
  toast(message, "fail", FAIL_MS);
}

export function note(message: string): void {
  toast(message, "note", NOTE_MS);
}

/* Browser-internal error phrases, translated to what they mean HERE. "Failed to fetch"
 * is Chrome for "the server you started is gone", and that is the actionable sentence. */
export function friendly(error: unknown): string {
  // Read structurally rather than with `instanceof Error`, which is what the untyped version
  // did: everything this catches today is a real Error, but a rejected fetch in one more
  // browser being a DOMException with a message would silently start printing "[object
  // DOMException]" if this asked about the constructor instead of about the field.
  var message = (error as { message?: unknown } | null | undefined)?.message;
  var text = error && message ? String(message) : String(error);
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "the server is not answering — is it still running?";
  }
  if (/Unexpected token|not valid JSON/i.test(text)) {
    return "the server answered with something that is not JSON";
  }
  return text;
}
