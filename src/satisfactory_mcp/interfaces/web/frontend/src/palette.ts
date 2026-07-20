/* Where a colour is DECLARED, so that the claim that the colours were chosen against each
 * other has one place to be checked from.
 *
 * Every value used to be here, on the argument that a table of colours picked against each
 * other only holds together while it is in one place to be compared. Half of that was right,
 * and the half it got wrong is the half that mattered: what has to be in one place is the
 * COMPARISON, not the values. Keeping the values here as well left every measured warrant --
 * "dE 34 from the terrain", "dE 4.5 from the extractors, which is why that one was rejected"
 * -- in a comment no build has ever read, and put the pipe rust and the storage magenta in a
 * different file from the width, the tier ramp and the reasoning that produced them. The file
 * even had to carve out an exception for the two networks that had done it the other way.
 *
 * So the values go to the features and the comparison stays. Every colour on this page is now
 * declared by the module that draws with it, beside its warrant, through `declareColours`
 * below; this file holds no hex at all, which `test_architecture.py` checks. What it holds is
 * the table -- every declared colour with the module that chose it -- which is what the next
 * commit needs to make the CIE Lab discipline in those warrants something a build can run.
 *
 * A new layer's colour is still a decision about the whole table. What this file is for is
 * making sure the whole table is still a thing that exists.
 */

/** One declared colour: who draws with it, what it is called there, and its value. */
interface Declared {
  owner: string;
  name: string;
  hex: string;
}

/* Dev-mode only, and deliberately. The registry exists to be checked and has no other reader,
 * so in a production build `declareColours` is a function that hands its argument straight
 * back, the array folds out of the bundle, and nothing here costs the page anything. */
var declared: Declared[] = [];

/* Record a feature's colours and hand them straight back, so that the declaration IS the
 * assignment and there is no second way for a colour to reach the page:
 *
 *   var RESOURCE_COLOUR: Record<string, string> = declareColours("markers", { … });
 *   var STORAGE_COLOUR = declareColours("placements", { storage: "#…" }).storage;
 *
 * `owner` is the drawing MODULE, not the layer, because that is the line a comparison needs.
 * Colours are to be compared across owners and never within one: a step inside a single family
 * is deliberate and small -- the belts' 15.6 between slowest and fastest, the pipes' 15.7
 * between Mk1 and Mk2, the storage pair's 16.7, the poles' 16.2, the grounds' 17.1 where No
 * Man's Land borders the Rocky Desert -- and a rule that flagged those is a rule everybody
 * switches off. Sharing an owner is what says "these two are meant to look related". Not
 * sharing one is what says "these two must never be confused".
 *
 * There is no registration order to get right. Every module that declares is imported by
 * main.ts and an import graph is evaluated synchronously, so by the time anything on this page
 * has drawn a single pixel the table is complete.
 */
export function declareColours<T extends Record<string, string>>(owner: string, colours: T): T {
  if (import.meta.env.DEV) {
    var table = colours as Record<string, string>;
    Object.keys(table).forEach(function (name) {
      var hex = table[name]!;
      // Six hex digits and nothing else. A short form or a named CSS colour would drop
      // silently out of every comparison, which is the one failure a colour registry must
      // not have -- it would look exactly like a colour nobody needed to check.
      if (!/^#[0-9a-f]{6}$/i.test(hex)) {
        console.error(owner + "/" + name + ' is "' + hex + '", not a #rrggbb — it is not compared');
      }
      // Two colours under one name is one of them missing from the table, and the pair it
      // would have been compared against is the pair the second one was added for.
      if (
        declared.some(function (other) {
          return other.owner === owner && other.name === name;
        })
      ) {
        console.error(owner + "/" + name + " is declared twice");
      }
      declared.push({ owner: owner, name: name, hex: hex });
    });
  }
  return colours;
}
