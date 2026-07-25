/* The crates on the ground: what a pioneer dropped, and what is still in it.
 *
 * Its own file for the reason `routers/crates.py` is its own router: A CRATE IS A SITUATION,
 * NOT INFRASTRUCTURE. `placements.ts` next door draws 151 containers the player built and
 * left standing, and the question they answer is "where did I put the steel". These are a
 * handful of actors that did not exist until somebody died or dismantled something with a
 * full inventory, and that DELETE THEMSELVES the moment they are emptied -- so every crate on
 * this map is live information by construction, which is not true of a single storage box.
 * Two marks that are events do not belong in a layer of 151 that are places.
 *
 * It is also not the same drawing problem, which is what settles the file rather than the
 * argument above. Everything in placements.ts is a rotated footprint at a measured size, and
 * a crate has no footprint at all: it is not a buildable, the docs dump carries no clearance
 * for a `BP_Crate_C`, and the server refuses to invent one. It is a 2 m prop, which at this
 * map's factory zoom is a couple of pixels and at world zoom is a hundredth of one. So it is
 * a MARK at a fixed pixel size -- the node dots' and the power poles' grammar -- and the
 * shape has to say "crate" rather than "one more small rectangle".
 */

import { CONTENTS_POPUP_PX, code, icon, popup } from "./dom";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";

import type { Row } from "./dom";

import type { ApiError } from "./api-types";

/* What `/api/crates` sends, as the frontend's claim rather than the server's schema.
 *
 * Declared HERE and not in api-shapes.ts because there is nothing in api-shapes.ts to declare
 * it from: `crates()` is annotated `-> Any`, so its OpenAPI response is `unknown` and there is
 * no `components["schemas"]["CratesResponse"]` to alias. The moment the router grows a
 * `response_model` these move to api-shapes.ts as two `Body<K>` lines and this block is
 * deleted; until then it is an observed payload, and it lives beside the code that observed it
 * rather than in api-types.ts, which is being emptied rather than added to.
 *
 * The absences are as load-bearing as the fields. There is no `w_m`/`l_m` -- see above -- and
 * NO OWNER: `mCrateType` is the actor's only saved property, so a co-op world's crate cannot
 * say whose it is, and the popup below must not put a name on one.
 */
interface CrateItem {
  cls: string;
  name: string;
  count: number;
}

interface CrateRow {
  instance_leaf: string;
  cls: string | null;
  /** `death`, `dismantle`, `none` -- or a word a later extractor learned and this build has
   *  not, which the server forwards unglossed rather than 500ing on. Hence `string`. */
  kind: string;
  /** The server's own sentence for the kind, or null for a kind it has no gloss for. */
  kind_text: string | null;
  x_m: number | null;
  y_m: number | null;
  z_m: number | null;
  yaw: number | null;
  /** The biggest twelve kinds; `more` counts what was left off. Both are of the WHOLE crate. */
  items: CrateItem[];
  more: number;
  item_kinds: number;
  total: number;
  slots: number | null;
}

interface CratesResponse extends ApiError {
  crates: CrateRow[];
  count: number;
  deaths: number;
  items_total: number;
}

/* Spring green, and picked by measuring against every colour already declared on this page,
 * the way the pipe rust, the storage magenta and the wire violet were.
 *
 * The warm half of the wheel is spent -- amber on the extractors, red on the generators, rust
 * on the pipes, cream on the chevrons -- and so is the cool half a network runs through: blue
 * is the machines, violet the wires, magenta the storage, steel the belts. What is left is the
 * green-cyan quarter, and a crate is the right thing to spend it on: it is the only mark on
 * this map that is neither built nor a resource, so it should not be a value step away from
 * anything.
 *
 * Its nearest colours anywhere on the page are the uranium node dot at dE 24.0 and the pickup
 * fallback at dE 24.3, and BOTH of those comparisons are honest ones rather than the storage
 * magenta's "a box in a factory against a dot on open terrain" -- a crate glyph, a node dot
 * and a pickup dot are all small marks lying on the ground, so this is the same square metre
 * and the distance has to carry the whole load. 24 is well past the dE 15 the audit fails
 * under and past the dE 22 the pipe rust was accepted at, and the two marks differ in SHAPE
 * as well: those are discs and this is a box. Nothing else on the page is within dE 34.
 *
 * The nearest ground is Bamboo Fields at dE 50.2, which is the comparison that decides whether
 * a 13 px glyph can be found on open terrain at world zoom -- and that is the zoom this layer
 * has to work at, because "where did I die" is a question about the whole map.
 */
var CRATE_COLOUR = declareColours("crates", { crates: "#3fcc94" }).crates;

/* The glyph's box, in screen PIXELS, and pixels for the reason power.ts gives for its poles:
 * the question a crate mark answers is "is there one here", not "does this fit". A crate is a
 * 2 m prop, so a true-size mark would be 0.28 px at the world view -- invisible at exactly the
 * zoom the layer is most useful, since the whole point is finding the one you left somewhere.
 *
 * 13 px is the size of the floor connectors' arrows next door, which are the page's other
 * fixed glyph that has to be CLICKED rather than merely seen: the popup is the layer, so the
 * mark is also its own pointer target and must not be a 4 px speck. There are 2 of these on
 * the reference world and 170 across every save on this machine, so nothing is crowded by it.
 */
var CRATE_PX = 13;

/* Three kinds, and the glyph tells apart the ONE distinction a reader scans a map for.
 *
 *   death      a filled box.   Somebody died here and their pockets are still on the ground.
 *   dismantle  a hollow box.   Overflow from dismantling with a full inventory.
 *   none       a DASHED box.   The crate predates the game's own death/dismantle property.
 *
 * The third is dashed rather than drawn as one of the other two, and that is the whole care
 * this table needs. `mCrateType` arrived in build 433351 and 125 of the 170 crates on this
 * machine are older than it, so a `none` crate MIGHT be a death -- drawing it hollow would
 * quietly file it as "not a death", which is the one fact the save withheld. A dashed outline
 * is the page's existing word for "this is not a reading": `placements.ts` dashes a machine it
 * cannot vouch for the state of, and the same stroke here says "this box does not say".
 *
 * The fill on a death is 0.55 rather than solid so that the strap still reads across it. A
 * fourth kind a later extractor learns is drawn as `none`, which is honest for the same
 * reason: this build does not know what it is looking at.
 */
function crateGlyph(kind: string): string {
  var death = kind === "death";
  var told = death || kind === "dismantle";
  var box = CRATE_PX;
  return (
    '<svg width="' + box + '" height="' + box + '" viewBox="0 0 ' + box + " " + box + '" ' +
    'aria-hidden="true" focusable="false">' +
    // The box. Inset by 2 so the 1.5 px stroke has room and the glyph's outer edge is its
    // stated size rather than its stated size plus a stroke.
    '<rect x="2" y="2" width="' + (box - 4) + '" height="' + (box - 4) + '" rx="1" ' +
    'fill="' + CRATE_COLOUR + '" fill-opacity="' + (death ? 0.55 : 0) + '" ' +
    'stroke="' + CRATE_COLOUR + '" stroke-width="1.5"' +
    (told ? "" : ' stroke-dasharray="2.2 1.6"') +
    "/>" +
    // The strap across it, which is the whole of what makes this read as a crate rather than
    // as one more small square on a map that already has 438 of them.
    '<path d="M2 ' + box / 2 + " H" + (box - 2) + '" stroke="' + CRATE_COLOUR + '" ' +
    'stroke-width="1.2" stroke-opacity="0.9"/>' +
    "</svg>"
  );
}

/** Thousands separators, for the reason the storage popup has its own: a death crate can hold
 *  a four-figure stack, and a table cell whose digits have to be counted is not a reading. */
function count(n: number): string {
  return n.toLocaleString("en-GB");
}

/* What the title row calls one, and the word for the third kind is the point.
 *
 * "unknown" would be wrong twice over: the save is not unreadable and the crate is not a
 * mystery, it simply predates the property that would have said. So a crate that cannot say
 * is called a CRATE -- which is what it is -- and the sentence under it, the server's own,
 * explains why there is nothing more to call it. Naming the absence is the server's job and
 * it does it; inventing a word for it here would undo that.
 */
function crateTitle(kind: string): string {
  if (kind === "death") return "death crate";
  if (kind === "dismantle") return "dismantle crate";
  return "crate";
}

/* What is in one crate, as popup rows -- the reason a reader clicked it.
 *
 * The server has already taken the biggest twelve and counted what it left off, so this only
 * has to render them and SAY SO. Twelve rather than storage's six because the two hold
 * different sorts of thing: a container holds one or two kinds because the player filled it
 * deliberately, and a death crate holds whatever was in a pioneer's pockets -- 38 kinds in 55
 * slots on the fullest one on this machine. "and 26 more" is a row rather than an ellipsis,
 * because a list that simply stops reads as a crate holding twelve things.
 */
function crateContents(c: CrateRow): Row[] {
  var items = c.items || [];
  // Emptied crates delete themselves, so this is very nearly unreachable -- and it is here
  // rather than assumed away because the server will serve a row whose inventory would not
  // read, and a card with a silent gap in it is worse than one that says "empty".
  if (!items.length) return [["contents", "empty"]];
  var rows: Row[] = items.map(function (item): Row {
    return [icon(item.cls, item.name), count(item.count)];
  });
  if (c.more) rows.push(["", "and " + c.more + " more"]);
  return rows;
}

/* One crate's whole card.
 *
 * Contents first, under the title, on the storage popup's terms exactly: a reader who clicks
 * a crate is asking what is in it, and where it is was answered by the click.
 *
 * NO OWNER ROW, and it is an absence worth stating rather than a field that was forgotten.
 * `mCrateType` is the actor's only saved property -- no player, no timestamp, no cause -- so
 * in a co-op world nothing on this page can say whose death this was, and a row that guessed
 * would arrive looking exactly like a row that knew.
 */
function cratePopup(c: CrateRow): Row[] {
  var rows: Row[] = [[crateTitle(c.kind), c.kind_text || c.kind]];
  crateContents(c).forEach(function (row) {
    rows.push(row);
  });
  // How much is out there, and only when the list did not already show all of it: repeating
  // "4 kinds, 24 items" over a card that lists four kinds is a row that says nothing.
  rows.push([
    "in all",
    c.more ? c.item_kinds + " kinds, " + count(c.total) + " items" : null,
  ]);
  rows.push(["slots", c.slots ? c.slots + " slots" : null]);
  rows.push(["at", c.x_m === null ? null : c.x_m + ", " + c.y_m + " m"]);
  rows.push(["elevation", c.z_m === null ? null : c.z_m + " m"]);
  rows.push(["instance", code(c.instance_leaf)]);
  return rows;
}

export function drawCrates(data: CratesResponse): void {
  /* ON BY DEFAULT, and the only layer added to the built band in years that is.
   *
   * The page's default-off layers are all off for one measured reason: they are a smear at
   * the whole-world zoom. 438 machines, 3,588 routes and 151 containers over 7 km resolve
   * into nothing a reader can use, so each of them is a toggle you reach for once you have
   * zoomed into a base. NONE OF THAT ARITHMETIC APPLIES HERE. There are 2 crates on the
   * reference world and 170 across all 67 saves on this machine -- the layer cannot crowd
   * anything, at any zoom, ever.
   *
   * What decides it is the other half: a crate is the answer to a question a reader does not
   * know to ask. "Where did I die" is asked precisely once, in a hurry, by somebody who has
   * just lost a full inventory somewhere in 7 km of countryside -- and an answer sitting
   * behind a checkbox they have never noticed is not an answer. Every other layer here can
   * wait to be turned on because the thing it draws will still be standing there tomorrow;
   * this one draws actors that DELETE THEMSELVES the moment they are emptied, so what it
   * shows is never stale and never worth hiding.
   *
   * Last of the built band, after the containers, because that is what a crate is next to:
   * the reader has just gone down the list through the concrete, the networks, the machines
   * and the boxes, and a crate is the inventory among them that nobody built.
   */
  var group = layer("crates", true, CRATE_COLOUR, [BAND.built, 80, "crates"]);

  data.crates.forEach(function (c) {
    // A crate whose position would not read is still SENT -- the projection knows it exists,
    // and the server says so rather than dropping the row. Skipping it is this page's call to
    // make, and it is the same one every drawing module here makes: there is nowhere to put a
    // mark with no coordinate.
    if (c.x_m === null || c.y_m === null) return;
    L.marker([-c.y_m, c.x_m], {
      /* A divIcon rather than a path, for the reason floors.ts's connector arrows are one: a
       * crate is a SHAPE at a fixed pixel size, and the canvas renderer this page draws paths
       * on offers a fixed-size circle and nothing else. A square drawn as a polygon would be
       * in world metres and would vanish at world zoom, which is the one zoom this layer has
       * to survive. The icon is anchored on its own centre so the box sits on the coordinate
       * rather than hanging below and right of it.
       *
       * It therefore lives in the marker pane, above the shared canvas, so a crate always
       * takes the click from whatever it is lying on. That is the right way round and not a
       * side effect: a crate is 13 px, it is the smaller and rarer of any two things at one
       * spot, and a mark that cannot be clicked is a layer with no content at all. */
      icon: L.divIcon({
        className: "crate-mark",
        html: crateGlyph(c.kind),
        iconSize: [CRATE_PX, CRATE_PX],
        iconAnchor: [CRATE_PX / 2, CRATE_PX / 2],
      }),
      // What the mark says before it is clicked, which for two marks on a 7 km map is most of
      // what a reader needs: they are hovering it to find out whether it is the death one.
      title: crateTitle(c.kind),
      alt: crateTitle(c.kind),
    })
      // The wider card the storage popup takes, and for the same reason plus one: this list
      // runs to twelve names rather than six. See CONTENTS_POPUP_PX in dom.ts.
      .bindPopup(popup(cratePopup(c)), { maxWidth: CONTENTS_POPUP_PX })
      .addTo(group);
  });
}

/* THE LIVE WAVE, and it is the wave the data picks rather than the one its neighbours use.
 *
 * `/api/storage` is static because the boxes move when the player BUILDS. A crate is created
 * by dying and destroyed by being emptied, and both of those happen between one autosave and
 * the next -- so a crate layer refetched only on a world switch would be showing a reader
 * where they died two sessions ago and nothing about the last five minutes, which is the
 * opposite of what the layer is for.
 *
 * Rank 25, between the pickups and the summary, and the position is about the summary: it is
 * the fetch that clears the dimmed map and rewrites the header, i.e. the one that says the
 * switch has finished, so nothing should be issued after it. Ahead of it and behind the two
 * live layers that actually cover the map, because two marks are the least urgent pixels here.
 *
 * `refilters: false`, which makes it the third entry to say so, after /api/power and
 * /api/summary. The floor filter's FILTERED list is the concrete, the machines, the routes and
 * the storage -- a crate is on none of them. It is not decomposed by /api/floors, it has no
 * instance a band could list, and a crate on the ground outside a factory is the ordinary
 * case, so the pass would find nothing of its own to do. The consequence is stated rather than
 * hidden: a crate stays visible in floor mode, exactly as the power network does, because
 * neither is a thing a storey contains.
 */
registerFetch<CratesResponse>({
  wave: "live",
  rank: 25,
  path: "/api/crates",
  label: "crates",
  clears: ["crates"],
  refilters: false,
  draw: drawCrates,
});
