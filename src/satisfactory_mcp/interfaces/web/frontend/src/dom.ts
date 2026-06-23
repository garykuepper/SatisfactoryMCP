/* The page's DOM primitives: finding an element, and turning data into safe markup.
 *
 * `popup()` is the whole reason this file exists. Every string that reaches a popup or a
 * label is DATA -- factory names and notes from the player's label file, class ids from the
 * save, region names from a JSON file -- so escaping is the DEFAULT here and markup is the
 * exception a caller has to ask for by name with html(). A factory called "<b>x</b>" prints
 * as its own text, and it does so because there is one function that all seven of the page's
 * popup builders go through rather than seven places where someone could forget.
 */

export function el(id) {
  return document.getElementById(id);
}

export function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* A pre-built fragment for the rows that genuinely need markup. Everything interpolated
 * into it still goes through esc() at the call site -- html() only marks the result as
 * finished, it does not bless its inputs. */
export function html(markup) {
  return { html: markup };
}

export function code(text) {
  return html("<code>" + esc(text) + "</code>");
}

export function popup(pairs) {
  return (
    "<table>" +
    pairs
      .filter(function (p) {
        return p[1] !== null && p[1] !== undefined && p[1] !== "";
      })
      .map(function (p) {
        var value = p[1] && p[1].html !== undefined ? p[1].html : esc(p[1]);
        return '<tr><td class="popup-key">' + esc(p[0]) + "</td><td>" + value + "</td></tr>";
      })
      .join("") +
    "</table>"
  );
}
