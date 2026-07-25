/* What the player built and where it stands: the floor plan, the machines on it, and the
 * containers among them.
 *
 * Together because they are the same drawing problem -- a rotated footprint at a measured
 * size -- and because all three fall back to a stated stand-in when the docs dump carries no
 * clearance for a class. The difference is only where the size comes from: a foundation
 * takes the grid's own tile, a machine and a container take their own w_m and l_m.
 *
 * The storage layer at the bottom is the one that is not only a placement: a container's
 * CONTENTS are the point of drawing it, so it is the one thing here with a popup built from
 * data rather than from a fixed set of rows.
 */

import { CONTENTS_POPUP_PX, code, contentsRows, count, popup } from "./dom";
import type { Row } from "./dom";
import { refreshFloors } from "./floors";
import { L } from "./leaflet";
import { BAND, layer } from "./layers";
import { footprintCorners } from "./map";
import { raiseNodeDots } from "./markers";
import { declareColours } from "./palette";
import { registerFetch } from "./registry";

import type {
  MachinesResponse,
  StorageResponse,
  StorageRow,
  StructuresResponse,
} from "./api-types";

/* The player's floor plan: one 8 m tile per placed foundation, ramp, wall or catwalk.
 *
 *   * Drawn at its real yaw, as of schema 12. Until then the instance quaternion was
 *     dropped at extraction and every tile was axis-aligned, so a slab the player laid at
 *     an angle -- this world has several, over 1,800 pieces of them -- came out as a
 *     staircase of squares. Polygons rather than rectangles is the whole change: an
 *     L.rectangle IS a polygon built from two corners, so this costs four points per piece
 *     and nothing else.
 *   * No per-class size. None of these eighteen classes has clearance data, so there is
 *     no footprint to ask for. They all snap to the same grid, whose edge the server
 *     reports as `tile_m`, so a wall paints the tile it stands on rather than its own
 *     thin volume -- it straddles two tiles and fringes a walled platform by half a tile,
 *     which at any zoom where the platform is legible is not visible.
 *
 * Stroked in its own fill colour, the trick the biome cells already use: no stroke at all
 * leaves hairline seams between neighbouring tiles at low zoom, and a stroke in any other
 * colour draws an 8 m grid. Same colour, weight 1, and a slab reads as one platform.
 */
/* Concrete, meant to be cool enough to read as built against the biomes -- and that sentence
 * is the one claim in this codebase the executable version of the discipline contradicts
 * outright: it is dE 6.6 from Abyss Cliffs and under 15 from three more grounds. Listed in
 * STANDING in palette.ts rather than quietly fixed, because which of the two colours should
 * move is a decision to make against the map. */
var STRUCTURE_COLOUR = declareColours("placements", { foundations: "#3a4148" }).foundations;

export function drawStructures(data: StructuresResponse): void {
  // First of the built band, because the concrete is what everything else in it stands on
  // or runs over -- the legend reads a base bottom-up, exactly as the player laid it.
  var group = layer("foundations", true, STRUCTURE_COLOUR, [BAND.built, 0, "foundations"]);
  // No `|| 8`: `tile_m` is the server's FOUNDATION_M constant and is always sent, and the
  // fallback was a second copy of the number the field exists to stop the page hardcoding.
  var half = data.tile_m / 2;
  data.structures.forEach(function (s, row) {
    if (s.x_m === null || s.y_m === null) return;
    var piece = L.polygon(footprintCorners(s.x_m, s.y_m, half, half, s.yaw), {
      color: STRUCTURE_COLOUR,
      weight: 1,
      opacity: 0.9,
      fillColor: STRUCTURE_COLOUR,
      fillOpacity: 0.9,
      interactive: false,
      pane: "foundations",
    });
    // The only name a lightweight buildable has is its place in this list, so that is what
    // the floor filter joins on -- `deck_rows` in `/api/floors` indexes exactly this. The
    // index is taken from the payload rather than from a counter, so a row skipped by the
    // guard above does not shift every piece after it by one. See floors.ts.
    piece._floor = { row: row, x_m: s.x_m, y_m: s.y_m, z_m: s.z_m === null ? undefined : s.z_m };
    piece.addTo(group);
  });
}

/* Static, and early in the wave: the concrete is what floor mode measures a platform's extent
 * from, so the opening flight cannot happen until these pieces are on the map. */
registerFetch<StructuresResponse>({
  wave: "static",
  rank: 20,
  path: "/api/structures",
  label: "structures",
  clears: ["foundations"],
  refilters: true,
  draw: drawStructures,
});

/* Machines at their real size AND their real facing: `w_m`/`l_m` are the building's own
 * footprint, so a Manufacturer (18x20 m) reads as the eight-times-larger thing it is next
 * to a Constructor (8x10 m), and `yaw` turns that footprint the way the player placed it.
 * Null footprints for the classes the docs dump gives no clearance data -- both biomass
 * burners here -- and those fall back to the 6 m square every machine used to get, which
 * rotates to itself. A null yaw draws axis-aligned; see footprintCorners in map.ts for why that is
 * not the same statement as a yaw of zero. */
var MACHINE_FALLBACK_M = 6;

/* Blue, amber, red -- the page's oldest three colours, and the ones every measured warrant
 * since has had to get out of the way of: the pipe rust was chosen against this amber, the
 * storage magenta against this red, and the wire violet against all three. Not themselves
 * measured against anything, which is what the audit has to say about the water node dot: a
 * water extractor stands exactly on one, and the blue is dE 8.6 away. */
var KIND_COLOUR: Record<string, string> = declareColours("placements", {
  machines: "#4aa3df",
  extractors: "#e0a33f",
  generators: "#d9534f",
});

/* The three layers /api/machines answers with, and the one row shape all three carry.
 * Spelled as a tuple rather than inferred, so `data[kind]` is a PlacementRow[] rather than
 * an index into an object with a string. */
const MACHINE_KINDS = ["machines", "extractors", "generators"] as const;

/* Where each of the three sits in the built band, spelled rather than taken from the loop's
 * own index: the order these are FETCHED and drawn in is one payload's field order, and the
 * order they are LISTED in is an editorial choice about a legend. They agree today. Reading
 * the second off the first would make the day they stop agreeing a silent one. */
const MACHINE_SLOT: Record<(typeof MACHINE_KINDS)[number], number> = {
  machines: 40,
  extractors: 50,
  generators: 60,
};

export function drawMachines(data: MachinesResponse): void {
  MACHINE_KINDS.forEach(function (kind) {
    var group = layer(kind, kind !== "machines", KIND_COLOUR[kind], [
      BAND.built,
      MACHINE_SLOT[kind],
      kind,
    ]);
    data[kind].forEach(function (m) {
      // One guard, on x only, exactly as it always was. The row type says y_m can be null
      // too and the assertion below is that claim being deliberately not acted on: widening
      // this to `|| m.y_m === null` would be a behaviour change smuggled in as a type fix,
      // and if the projection ever sends half a position it should be visible, not silently
      // skipped by a guard nobody decided to add.
      if (m.x_m === null) return;
      var w = (m.w_m || MACHINE_FALLBACK_M) / 2;
      var l = (m.l_m || MACHINE_FALLBACK_M) / 2;
      var piece = L.polygon(footprintCorners(m.x_m, m.y_m!, w, l, m.yaw), {
        color: KIND_COLOUR[kind],
        weight: 1,
        fillOpacity: m.paused ? 0.15 : 0.65,
        dashArray: m.paused ? "2,2" : undefined,
      }).bindPopup(
        popup([
          ["building", m.name],
          ["recipe", m.recipe_name || m.recipe],
          ["clock", m.clock === null ? null : Math.round(m.clock * 100) + "%"],
          ["paused", m.paused ? "yes" : null],
          // All three sides of the clearance box, because the third is now sent: a
          // Refinery being 15 m tall is why a floor view can say it comes through the
          // ceiling, and a reader looking at the ghost should find the number here.
          ["footprint", m.w_m && m.l_m ? m.w_m + " x " + m.l_m + " m" : null],
          ["height", m.h_m ? m.h_m + " m" : null],
          // Degrees about world Z, positive turning +X towards +Y -- the same number
          // the drawing is turned by, so a reader can check the picture against it.
          // Absent, not "0", when the projection carries no facing at all.
          ["facing", m.yaw === null || m.yaw === undefined ? null : Math.round(m.yaw) + "°"],
          ["at", m.x_m + ", " + m.y_m + " m"],
          ["instance", code(m.instance_leaf)],
        ])
      );
      // What the floor filter joins a machine by, and what it needs to know to tell whether
      // one on a lower deck comes up through this floor. See floors.ts.
      piece._floor = {
        id: m.instance_leaf,
        z_m: m.z_m === null ? undefined : m.z_m,
        h_m: m.h_m,
      };
      piece.addTo(group);
    });
  });
  raiseNodeDots();
}

/* The live wave's first entry, and the only one on the page with a post-draw hook.
 *
 * A save write changes what is BUILT, so it changes the decomposition -- and the ids a band
 * lists are what the floor filter runs on. Without `after` a machine placed since the view
 * was opened would be drawn by /api/machines, listed by no band, and therefore silently
 * missing from every floor rather than visibly new on one. It is a hook rather than a line at
 * the end of `drawMachines` because it is a refetch and not a draw: it asks /api/floors the
 * same question again, which is load.ts's kind of work, not this file's. */
registerFetch<MachinesResponse>({
  wave: "live",
  rank: 10,
  path: "/api/machines",
  label: "machines",
  clears: ["machines", "extractors", "generators"],
  refilters: true,
  draw: drawMachines,
  after: refreshFloors,
});


/* Storage: the boxes, and what is in them.
 *
 * Here rather than in a file of its own because it is the same drawing problem this module
 * already solves twice -- a rotated footprint at a measured size, with a stated stand-in where
 * the dump has no clearance -- and a third copy of `footprintCorners` plus a third fallback
 * constant would be three places to keep in step instead of one. What is genuinely new is the
 * popup, because a container is the first thing on this map whose CONTENTS are the point.
 *
 * The map could already say what the player owns -- the header's totals come from the same
 * stacks -- and could never say where any of it was. That is the gap: "have I got enough
 * steel" and "where did I put the steel" are different questions, and a base with 105
 * containers spread over 7 km only ever raises the second one.
 *
 * ONE LAYER, TWO KINDS, told apart by a value step in one hue -- see STORAGE_COLOUR. A fluid
 * buffer and a storage container are both boxes the player put things in, so they belong to one
 * checkbox; they hold different sorts of thing and read differently in a popup, so they are not
 * the same tone. That is the belts' and pipes' grammar borrowed for a distinction that is not a
 * tier, and it is deliberate: a second hue would make the legend claim these are two networks.
 *
 * OFF BY DEFAULT, and NOT part of the reveal a factory label triggers -- see FACTORY_LAYERS in
 * labels.ts for that decision and its reasoning.
 */

/* Storage, and picked the way the pipe rust was: by measuring, not by taste.
 *
 * A container is drawn as a filled footprint box, so the colours it has to separate from are
 * the other filled boxes -- the three machine kinds, the belt attachments, and the concrete it
 * stands on -- and then, more weakly, everything else on the page. Magenta is what is left: the
 * page already spends blue on machines, amber on extractors, red on generators, steel on belts
 * and rust on pipes, and the whole warm half is taken.
 *
 * In CIE Lab, this is dE 51.8 from its nearest filled box (the generator red) and 48.5 from
 * the nearest biome ground, which are the two comparisons that decide whether a box reads. Its
 * nearest neighbour ANYWHERE on the page is the raw-quartz node dot at dE 27.4 -- a small disc
 * on open terrain rather than a rectangle inside a factory, so the two are never asked to be
 * told apart in the same square metre. That last sentence is the axis palette.ts's STANDING
 * list is organised along, and this is the one place on the page where it is backed by a
 * measurement rather than assumed. The alternatives measured beside it were all worse on one
 * of the two: a lighter magenta (#c76bb0) lands dE 17.7 from that same quartz dot, a violet
 * (#8c72c4) dE 19.1 from the crude-oil dot and only 37.3 from the machine blue, and a sea
 * green dE 10.5 from the pickup teal.
 *
 * And the fluid buffers, one value step down the same hue -- the grammar the belts and pipes
 * use for their tiers, borrowed for a distinction that is not a tier: a tank and a box are two
 * kinds of container rather than two grades of one, and one family with a step inside it says
 * "same layer, different thing" without spending a second hue on it.
 *
 * The step is the house step: dE 16.7, against the belts' 15.6 between their slowest and
 * fastest and the pipes' 15.7 between Mk1 and Mk2. Re-measured rather than assumed safe,
 * because a ramp can walk a colour into a neighbour -- this one moves AWAY from everything,
 * ending dE 34.0 from its nearest colour on the page (the crude-oil dot) and 37.6 from the
 * nearest ground, both further off than the box tone above. Both steps are inside one owner,
 * which is exactly why palette.ts compares across owners and never within one.
 */
var STORAGE = declareColours("placements", {
  storage: "#ad4f96",
  "storage fluid": "#7f3169",
});
var STORAGE_COLOUR = STORAGE.storage;
var STORAGE_FLUID_COLOUR = STORAGE["storage fluid"];

/* A container the docs dump carries no clearance for: the HUB's own box, the Blueprint
 * Designer's, and the Dimensional Depot uploader. The same arrangement, and the same reason, as
 * MACHINE_FALLBACK_M above -- the server sends null rather than a number invented there, because
 * an invented one would arrive indistinguishable from a measurement. Four metres is half a
 * foundation tile, which is small enough not to overstate an uploader and big enough to be
 * clickable at the zoom the layer is meant to be read at. */
var STORAGE_FALLBACK_M = 4;

/* What is in one container, as popup rows.
 *
 * TWO KINDS, AND ONLY ONE OF THEM GETS A GRID. A solid container holds stacks of things you
 * would recognise by their pictures, which is exactly what `contentsRows` draws; a fluid
 * buffer holds ONE fluid and a level, and a grid of a single tile would be a picture claiming
 * to be a set. Worse, the tile has nowhere to put the reading: what a reader wants off a tank
 * is "1,441 m³ of 2,400 — 60% full", and a corner badge cannot say a denominator. So the
 * fluid branch stays the sentence it always was, and the split is on `kind`, which is what
 * the server says to branch on.
 */
function storageContents(s: StorageRow): Row[] {
  if (s.kind === "fluid") {
    var stored = s.stored_m3;
    if (stored === null || stored === undefined) return [["contents", "not recorded"]];
    var level = count(Math.round(stored * 10) / 10) + " m³";
    // The capacity is what turns a level into a reading, and it comes from the docs dump
    // rather than the save -- so where the dump is silent the row says the level alone
    // instead of inventing a denominator.
    if (s.capacity_m3) {
      level += " of " + count(s.capacity_m3) + " — " + Math.round((s.fill || 0) * 100) + "% full";
    }
    return [
      ["fluid", s.fluid_name || (s.fluid ? null : "empty")],
      ["level", level],
    ];
  }
  // ...and a solid container is an inventory, so it is drawn as one -- the same grid a crate
  // gets, out of the same helper, because "what is in it" is one question wherever it is
  // asked. The truncation is the server's: `/api/storage` sends the biggest six kinds and
  // counts the rest, and `more` is what the grid's last tile stands for.
  return contentsRows(s.items || [], s.more || 0);
}

/* One container's whole card: what it is, what is in it, and where it stands.
 *
 * The contents come FIRST, above the placement rows every other popup on this page leads with,
 * because they are the reason this layer exists -- a reader who clicks a box is asking what is
 * in it, not where it is, and where it is was answered by the click.
 */
function storagePopup(s: StorageRow): Row[] {
  var rows: Row[] = [["storage", s.name]];
  storageContents(s).forEach(function (row) {
    rows.push(row);
  });
  rows.push(["slots", s.slots ? s.slots + " slots" : null]);
  rows.push(["footprint", s.w_m && s.l_m ? s.w_m + " x " + s.l_m + " m" : null]);
  rows.push(["facing", s.yaw === null || s.yaw === undefined ? null : Math.round(s.yaw) + "°"]);
  rows.push(["at", s.x_m + ", " + s.y_m + " m"]);
  rows.push(["instance", code(s.instance_leaf)]);
  return rows;
}

export function drawStorage(data: StorageResponse): void {
  // Off at the whole-world zoom, exactly like the machines and the routes: 151 boxes across
  // 7 km is a scatter of specks, and the owner asked for a toggle.
  // Last of the built band: a container is the thing a base is built AROUND, and the row is
  // off by default, so the bottom of the list is where a reader who wants it goes looking.
  var group = layer("storage", false, STORAGE_COLOUR, [BAND.built, 70, "storage"]);
  data.storage.forEach(function (s) {
    if (s.x_m === null || s.y_m === null) return;
    var colour = s.kind === "fluid" ? STORAGE_FLUID_COLOUR : STORAGE_COLOUR;
    var w = (s.w_m || STORAGE_FALLBACK_M) / 2;
    var l = (s.l_m || STORAGE_FALLBACK_M) / 2;
    var box = L.polygon(footprintCorners(s.x_m, s.y_m, w, l, s.yaw), {
      color: colour,
      weight: 1,
      fillColor: colour,
      // A container the player has not filled is drawn hollow, which is the one thing about a
      // warehouse a reader wants at a glance and the map can say without being asked: an empty
      // box is a place with room in it. Same device the machines use for `paused`.
      fillOpacity: s.kind === "fluid" ? (s.fill ? 0.7 : 0.15) : s.total ? 0.7 : 0.15,
    })
      // Wider than the page's other cards, because this one lists item names against counts
      // and a name is not broken across lines. See CONTENTS_POPUP_PX in dom.ts.
      .bindPopup(popup(storagePopup(s)), { maxWidth: CONTENTS_POPUP_PX });
    // WHERE it stands, and deliberately no instance id: `/api/floors` does not decompose
    // storage, so there is no band listing this box and a mark carrying an id would be a
    // join that always misses. Position is the honest one, and floors.ts says so.
    box._floor = { x_m: s.x_m, y_m: s.y_m, z_m: s.z_m === null ? undefined : s.z_m };
    box.addTo(group);
  });
  raiseNodeDots();
}

/* Static rather than live, which is a claim about the CONTAINER and not about its contents:
 * the boxes move when the player builds. What is in them changes on every autosave and is not
 * refetched until the next switch, which is the same bargain the machines' clock speeds make
 * -- and the reason it is bearable is that the popup is opened by a click, on demand. */
registerFetch<StorageResponse>({
  wave: "static",
  rank: 60,
  path: "/api/storage",
  label: "storage",
  clears: ["storage"],
  refilters: true,
  draw: drawStorage,
});
