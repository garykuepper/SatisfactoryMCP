/* The page's DOM primitives: finding an element, and turning data into safe markup.
 *
 * `popup()` is the whole reason this file exists. Every string that reaches a popup or a
 * label is DATA -- factory names and notes from the player's label file, class ids from the
 * save, region names from a JSON file -- so escaping is the DEFAULT here and markup is the
 * exception a caller has to ask for by name with html(). A factory called "<b>x</b>" prints
 * as its own text, and it does so because there is one function that all seven of the page's
 * popup builders go through rather than seven places where someone could forget.
 */

/* Non-null, and asserted rather than checked: every id this is called with is written in
 * index.html, so a miss is a broken page rather than a case to handle -- and the old script
 * would have thrown on the next line anyway. The type parameter is what lets the two callers
 * that need a <select> get one without a cast at each use. */
export function el<T extends HTMLElement = HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

export function esc(value: unknown): string {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* A value that has already been made safe, and is therefore allowed through popup()
 * without escaping. The wrapper object IS the type: there is no way to reach the unescaped
 * branch except by calling html(), which is what makes "escaped by default" checkable rather
 * than a convention. */
export interface Markup {
  html: string;
}

/* One row of a popup table: a key, and a value. Either cell is data (escaped) or finished
 * markup (not). `undefined` is in the value's union because half the rows are conditional
 * expressions that evaluate to null when there is nothing to say, and those rows are
 * dropped rather than printed empty -- a KEY is never absent, which is why the two unions
 * are not the same one.
 *
 * The key was a plain string until the item icons arrived, and it is widened rather than
 * worked around because of where a picture belongs: an icon is part of naming the thing,
 * not part of counting it, so "[img] Iron Plate | 15" is the row and "Iron Plate | [img] 15"
 * would be a picture filed with the number. Nothing is loosened by the widening -- Markup is
 * still only reachable through html(), so escaping stays the default in both columns. */
export type Row = [string | Markup, string | number | Markup | null | undefined];

/* A pre-built fragment for the rows that genuinely need markup. Everything interpolated
 * into it still goes through esc() at the call site -- html() only marks the result as
 * finished, it does not bless its inputs. */
export function html(markup: string): Markup {
  return { html: markup };
}

export function code(text: unknown): Markup {
  return html("<code>" + esc(text) + "</code>");
}

/** One cell's finished HTML: markup passes through, everything else is escaped. The whole
 *  of the "escaped by default" rule, in one place now that both columns can hold either. */
function cell(value: string | number | Markup | null | undefined): string {
  return value && (value as Markup).html !== undefined ? (value as Markup).html : esc(value);
}

export function popup(pairs: Row[]): string {
  return (
    "<table>" +
    pairs
      .filter(function (p) {
        return p[1] !== null && p[1] !== undefined && p[1] !== "";
      })
      .map(function (p) {
        return '<tr><td class="popup-key">' + cell(p[0]) + "</td><td>" + cell(p[1]) + "</td></tr>";
      })
      .join("") +
    "</table>"
  );
}

/* An item's own picture, beside its own name -- the one thing on this page that is the
 * reader's game rather than this repository's data.
 *
 * WHY IT IS ONE FUNCTION AND NOT TWO. The picture and the word are a single label: the `alt`
 * IS the name, a class the icon directory has no file for has to leave the name standing on
 * its own, and no caller has ever wanted one without the other. Splitting them would make
 * every call site compose two halves and get the fallback right by hand, which is the
 * arrangement in which a popup eventually loses a name to a missing PNG.
 *
 * THE URL IS UNTAGGED, and that is a decision about caching rather than an oversight.
 * `/api/icons/{desc}` will serve a `?v=<build>` request `immutable` for a year and an
 * untagged one `no-cache` with an ETag, so this page could have been the client that carries
 * the tag -- and to do it, it would have to learn the tag from a probe, which means naming a
 * descriptor class up front and hoping the reader's own generated directory happens to hold
 * it. There is no such class: the directory is optional, and six of the game's 750 item
 * classes ship no picture at all, so a probe is a guess whose failure mode is silently
 * unversioned URLs. The endpoint's own note says the rest -- caching untagged answers hard is
 * how a regenerated directory stayed invisible behind a stale probe -- so untagged plus an
 * ETag is the path it documents as correct, and a revalidation is a 304 rather than 50 KB.
 *
 * NOTHING IS FETCHED UNTIL A POPUP OPENS. Leaflet holds a bound popup as a STRING and builds
 * its DOM on the click, so these <img> tags are markup rather than requests for as long as
 * the card is shut. That is what makes the lazy half free: a world with 151 containers and 2
 * crates on it loads exactly zero icons, and a reader who opens one crate loads twelve.
 *
 * A MISSING ICON REMOVES ITSELF. `onerror` is inline because the failure has to be handled by
 * the element that failed, inside markup that is a string until Leaflet inserts it -- there is
 * no node to attach a listener to at the moment this is built, and a card-opened hook would
 * put the page's most ordinary state (no icons generated at all) behind a second mechanism.
 */
/* How wide a card carrying a contents list may get, in pixels, handed to `bindPopup` by the
 * two layers that draw one.
 *
 * Leaflet's default is 300, and it is right for every other popup on this page: those are
 * short keys against short values, and a card wider than it needs to be is a card that covers
 * more of the map than it has to. A contents list is a different shape -- an item's NAME in
 * the key column, which `.item-name` refuses to break across lines, plus a 21 px picture in
 * front of it -- and at 300 the longest names in the game push the table past the cap Leaflet
 * clamps the content box to, which does not wrap them, it spills them out of the card.
 *
 * 380 is measured against the widest thing that can appear: "Assembly Director System" and
 * "Magnetic Field Generator" are about 190 px with their icon, and the count column beside
 * them is at most "24,000". Here rather than in either feature because the two must agree,
 * and because it is the same fact as the CSS rule it pays for.
 */
export var CONTENTS_POPUP_PX = 380;

export function icon(desc: string, name: string): Markup {
  // The wrapper is not decoration: it is what keeps a picture attached to the first line of
  // the name it belongs to. "Encased Industrial Beam" wraps over three lines in a popup cell,
  // and inline text flow puts the image at the start of the RUN rather than beside the word,
  // so without this the icon sits alone on line one. See .item-name in style.css.
  return html(
    '<span class="item-name"><img class="item-icon" src="/api/icons/' +
      encodeURIComponent(desc) +
      '" alt="' +
      esc(name) +
      '" onerror="this.remove()">' +
      esc(name) +
      "</span>"
  );
}
