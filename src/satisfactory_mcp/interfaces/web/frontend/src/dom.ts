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
