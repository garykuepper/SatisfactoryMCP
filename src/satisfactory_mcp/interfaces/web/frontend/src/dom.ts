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

/* One row of a popup table: a key, and a value that is either data (escaped) or finished
 * markup (not). `undefined` is in the union because half the rows are conditional
 * expressions that evaluate to null when there is nothing to say, and those rows are
 * dropped rather than printed empty. */
export type Row = [string, string | number | Markup | null | undefined];

/* A pre-built fragment for the rows that genuinely need markup. Everything interpolated
 * into it still goes through esc() at the call site -- html() only marks the result as
 * finished, it does not bless its inputs. */
export function html(markup: string): Markup {
  return { html: markup };
}

export function code(text: unknown): Markup {
  return html("<code>" + esc(text) + "</code>");
}

export function popup(pairs: Row[]): string {
  return (
    "<table>" +
    pairs
      .filter(function (p) {
        return p[1] !== null && p[1] !== undefined && p[1] !== "";
      })
      .map(function (p) {
        var cell = p[1];
        var value = cell && (cell as Markup).html !== undefined ? (cell as Markup).html : esc(cell);
        return '<tr><td class="popup-key">' + esc(p[0]) + "</td><td>" + value + "</td></tr>";
      })
      .join("") +
    "</table>"
  );
}

/* ------------------------------------------------- what is in a container, as the game draws it */

/* THE INVENTORY GRID: a container's contents as tiles of the reader's own game artwork with
 * the quantity in the corner, rather than as a table of names.
 *
 * Here rather than in either feature because there are two callers -- a crate and a storage
 * box -- and "what is in it" has to be one idea on this map rather than two that resemble
 * each other. It was a list of `[picture + name | count]` rows first, which was readable and
 * was also the wrong shape: a reader who opens a container is scanning for a THING, and a
 * thing is recognised by its picture in about the time it takes to read one word of its name.
 * Twelve names down the left edge of a card is a list to be read; twelve tiles is an
 * inventory to be looked at, and the game has already taught everyone who will ever open one
 * of these popups to read exactly that.
 *
 * THE NAMES DO NOT GO AWAY, which is the one thing a grid must not get wrong. Every tile
 * carries its name three times over: `title` for a pointer, `alt` for a screen reader and for
 * the tile whose picture never arrives, and once more as text in the caption line under the
 * grid -- which is the copy that needs no pointer, no hover and no working icon directory.
 * The caption is why this returns ROWS rather than one lump of markup: the grid and the names
 * are one answer, and a caller assembling them itself could leave one out.
 *
 * THE URL IS UNTAGGED, and that is a decision about caching rather than an oversight.
 * `/api/icons/{desc}` will serve a `?v=<build>` request `immutable` for a year and an
 * untagged one `no-cache` with an ETag, so this page could have been the client that carries
 * the tag -- and to do it, it would have to learn the tag from a probe, which means naming a
 * descriptor class up front and hoping the reader's own generated directory happens to hold
 * it. There is no such class: the directory is optional, and three of the game's 750 item
 * classes ship no picture at all, so a probe is a guess whose failure mode is silently
 * unversioned URLs. The endpoint's own note says the rest -- caching untagged answers hard is
 * how a regenerated directory stayed invisible behind a stale probe -- so untagged plus an
 * ETag is the path it documents as correct, and a revalidation is a 304 rather than 50 KB.
 *
 * NOTHING IS FETCHED UNTIL A POPUP OPENS. Leaflet holds a bound popup as a STRING and builds
 * its DOM on the click, so these <img> tags are markup rather than requests for as long as
 * the card is shut. That is what makes the lazy half free: a world with 151 containers and 2
 * crates on it loads exactly zero icons, and a reader who opens one crate loads twelve.
 */

/** One kind of thing in a container: the class its picture is named by, the name a reader
 *  reads, and how many. The record `/api/crates` and `/api/storage` both send. */
export interface Stack {
  cls: string;
  name: string;
  count: number;
}

/* How wide a card carrying an inventory grid may get, in pixels, handed to `bindPopup` by the
 * two layers that draw one.
 *
 * Leaflet's default is 300, and it is right for every other popup on this page: those are
 * short keys against short values, and a card wider than it needs to be is a card covering
 * more of the map than it has to. A grid is a different shape, and this number is arithmetic
 * rather than taste -- at 380 the value cell fits SEVEN 38 px tiles to a row, which makes the
 * twelve kinds `/api/crates` sends two rows and the six `/api/storage` sends one. Six to a row
 * would leave the twelve as a ragged 6+6; eight would need 424 px and start covering the thing
 * that was clicked. Measured with the widest card either layer can produce, not derived.
 */
export var CONTENTS_POPUP_PX = 380;

/** Thousands separators, because these are counts of things and they get large: a full
 *  Industrial Storage Container holds 24,000 Wire, and a badge whose digits have to be counted
 *  is not a reading. Shared, so a tile's badge and the total under the grid cannot disagree. */
export function count(n: number): string {
  return n.toLocaleString("en-GB");
}

/* One tile: the picture, the quantity over its bottom-right corner, and the name underneath
 * all of it -- underneath literally. `.item-abbr` sits in the tile the whole time and is
 * revealed when the <img> stacked over it gives up.
 *
 * THAT IS THE MISSING-ICON ANSWER, and it is a tile rather than a hole on purpose. Six of the
 * game's 750 classes ship no picture, and a reader who never ran the generator has none at
 * all -- so a failed icon is the ordinary state here rather than the edge case, and a grid
 * with gaps in it would be a grid lying about how many kinds are in the box. The name takes
 * the tile instead, clipped to fit, and the count stays exactly where it was.
 *
 * `onerror` is inline because the failure has to be handled by the element that failed, inside
 * markup that is a string until Leaflet inserts it: there is no node to attach a listener to
 * at the moment this is built, and a card-opened hook would put the page's most ordinary state
 * behind a second mechanism. It ADDS a class rather than assigning one, so a tile that grows a
 * second class later cannot be silently undressed by this line.
 */
function tile(item: Stack): string {
  return (
    '<span class="item-tile" title="' +
    esc(item.name + " — " + count(item.count)) +
    '"><img class="item-icon" src="/api/icons/' +
    encodeURIComponent(item.cls) +
    '" alt="' +
    esc(item.name) +
    "\" onerror=\"this.parentNode.classList.add('item-tile-bare');this.remove()\">" +
    '<span class="item-abbr">' +
    esc(item.name) +
    '</span><b class="item-count">' +
    esc(count(item.count)) +
    "</b></span>"
  );
}

/* What is in one container, as the two popup rows that say it: the grid, and the names under
 * it.
 *
 * `more` is the SERVER's truncation and not this file's. `/api/crates` sends the biggest
 * twelve kinds and `/api/storage` the biggest six, each with a count of what it left off, and
 * a grid cannot show what it was never sent. It is drawn as a tile of its own rather than
 * dropped, because a grid that simply stops is a container that looks emptier than it is --
 * the same reason the list this replaced ended in "and 26 more". What HAS changed is who the
 * limit is for: twelve was chosen server-side as "what a popup can show without scrolling",
 * and a grid makes that sentence false, so the cap is now arithmetic belonging to the router
 * rather than to the page, and raising it is a decision to make over there.
 */
export function contentsRows(items: Stack[], more: number): Row[] {
  var stacks = items || [];
  // Said in words, because an empty grid and a container this page failed to read are the
  // same picture, and one of the two is an answer.
  if (!stacks.length) return [["contents", more ? "not shown" : "empty"]];
  var tiles = stacks.map(tile).join("");
  if (more) {
    tiles +=
      '<span class="item-tile item-tile-more" title="' +
      esc(more + " more kinds — the server sends the biggest few") +
      '">+' +
      esc(String(more)) +
      "</span>";
  }
  /* The caption, and it is the half that keeps a grid honest: a tile says what a thing is to
   * anyone who recognises the picture, and the names say it to everyone else -- including
   * anyone with no pointer to hover with, which on a touch screen is everyone. A middle dot
   * rather than a comma, because several item names have a comma in them and none has this. */
  var names = stacks
    .map(function (s) {
      return s.name;
    })
    .join(" · ");
  if (more) names += " · and " + more + " more";
  return [
    ["contents", html('<span class="item-grid">' + tiles + "</span>")],
    ["", html('<span class="item-names">' + esc(names) + "</span>")],
  ];
}
