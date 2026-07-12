/* Floor mode: the same map, one storey at a time.
 *
 * WHAT THIS IS NOT is a second renderer. Everything on screen in floor mode was drawn by the
 * module that always drew it -- `placements.ts` for the concrete and the machines, `routes.ts`
 * for the belts and the pipes -- and this file only decides which of those pieces are on the
 * floor being looked at. That is the whole design: a factory's storeys are not a different
 * picture of a factory, they are the same picture with four fifths of it taken away, and a
 * second set of drawing code would be four more places for the map to disagree with itself.
 *
 * WHAT A FLOOR IS is not in the save, and is not decided here either. `/api/floors` recovers
 * it -- 4-connected platforms of 8 m foundation cells, then a per-platform cluster of deck
 * heights, every constant measured before it was written -- and answers with the one thing a
 * client cannot derive: WHICH floor each thing is on. So this module holds no geometry rule
 * of its own except the one the payload does not cover; see `standsOn`, which is the only
 * place a height is compared with a height, and which exists for exactly one layer.
 *
 * THE FOUR JOINS, because there are four and they are all different:
 *
 *   * a machine, extractor, generator or belt attachment -- BY INSTANCE ID. A band lists them.
 *   * a belt run BY CHAIN, a pipe run BY ITS ROW. `/api/floors` groups runs by what they do
 *     to a floor (`same-deck`, `connector`, `terrain`, `mixed`) and keys them by the number
 *     the belt and pipe payloads already carry.
 *   * a foundation piece -- BY POSITION in `/api/structures`. A lightweight buildable has no
 *     instance name at all, so `deck_rows` is the only name a deck can be listed by.
 *   * storage -- BY WHERE IT STANDS, because the decomposition does not decompose it. That is
 *     the one geometric rule here, and it is written down as one rather than buried in a filter.
 *
 * THE GROUND is a pseudo-floor and not a band, and that follows from the data: the API's own
 * `exempt`, `terrain` and `off-deck` groups plus the runs that never reach a deck. A miner
 * stands on a resource node up to 28 m off any deck and plumbing hugs the ground -- neither is
 * on floor zero, and putting them there would be the map claiming a measurement it does not
 * have. The row says which claim it is, because without a heightfield "on terrain" degrades to
 * "off-deck" and those are different sentences.
 */

import { get } from "./api";
import { code, popup } from "./dom";
import { batch, hideFloors, onFloorExit, onFloorPick, showFloors } from "./layercontrol";
import { L } from "./leaflet";
import { map, writeHash } from "./map";
import { state } from "./state";
import { friendly, note } from "./toast";

import type { components } from "./api-schema";
import type { Point3M } from "./api-types";
import type { Row } from "./dom";
import type { FloorMark } from "./leaflet-private";
import type { FloorChoice } from "./layercontrol";

type FloorsResponse = components["schemas"]["FloorsResponse"];
type FloorPlatform = components["schemas"]["FloorPlatform"];
type FloorBand = components["schemas"]["FloorBand"];
type FloorDeck = components["schemas"]["FloorDeck"];
type FloorRun = components["schemas"]["FloorRun"];

/** The pseudo-floor's key, in the picker and in the fragment alike. */
export var GROUND = "ground";

/* What "show me this factory" already means, borrowed rather than restated: a factory at
 * factory scale IS its machines and the routes between them. `labels.ts` says why, and says
 * why storage is deliberately not the fourth. Storage is still FILTERED here when the reader
 * has ticked it, which is the difference between what a gesture turns on and what a mode is
 * about. */
var FLOOR_LAYERS = ["machines", "belts", "pipes"];

/** Every layer floor mode has an opinion about, which is every layer it filters. */
var FILTERED = ["foundations", "machines", "extractors", "generators", "belts", "pipes", "storage"];

/* Breathing room around a platform, metres, and how close the flight may get -- the same two
 * numbers a factory-label flight already uses, for the same reason: a deck that exactly fills
 * the screen loses the surroundings that say where it is. */
var FLOOR_PAD_M = 40;
var FLOOR_MAX_ZOOM = 1;

/* How deep the concrete under a deck is, metres.
 *
 * The one number in this file that is not the server's, and it is used for one layer --
 * storage, which the decomposition does not decompose. Two metres is half the thickest
 * foundation the game builds (`8x4`), so it is the depth of the deck rather than a tolerance:
 * a thing lower than this is under the floor rather than on it. Everything else here is
 * joined by a name the server sent and needs no such number. */
var DECK_DEPTH_M = 2;

/* The connector glyph's box, in screen pixels, which is both how big the arrow is and how
 * big a target it is. Small, because a floor plan can carry twenty of them; not smaller,
 * because it has to stay hittable -- and it is in pixels rather than metres precisely so it
 * does not shrink to nothing when the reader zooms out to see the whole deck. */
var GLYPH_PX = 14;

/* ------------------------------------------------------------------ the view */

/** One floor mode session: what was asked for, what came back, and what it changed. */
interface FloorView {
  /** The query string `/api/floors` was asked with, kept so a save write can re-ask it. */
  query: string;
  /** The selected platform, or null when the answer was a sentence instead of a list. */
  platform: FloorPlatform | null;
  body: FloorsResponse;
  /** What the picker is called: the factory's own name where the player gave one. */
  title: string;
  /** What could not be shown, as the API said it. Empty when there is nothing to say. */
  message: string;
  /** Layers this mode turned on, minus any the reader has since taken ownership of. */
  turned: string[];
  /** Whether the map has been taken to the platform yet.
   *
   * Not a formality: on a pasted `#floor=` link the decomposition can land BEFORE the
   * concrete does, and the flight is computed from the drawn deck. So the flight is owed
   * until it can be made, and is made by whichever pass first has the pieces to make it. */
  flown: boolean;
}

var view: FloorView | null = null;

/* Programmatic layer ticks are not the reader's decisions, and Leaflet gives no way to tell
 * them apart afterwards -- `overlayadd` fires from the layer's own `add` event, so
 * `map.addLayer` and a click arrive identically. Same flag, same reason, as `applying` in
 * regions.ts. */
var applying = false;

/** Whether the page is currently slicing a factory. */
export function inFloorMode(): boolean {
  return view !== null;
}

/* ------------------------------------------------------------- reading the answer */

function bandOf(platform: FloorPlatform | null, key: string): FloorBand | null {
  if (!platform || key === GROUND) return null;
  var found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (String(band.ordinal) === key) found = band;
  });
  return found;
}

/* A band's ceiling: the next deck up, or nothing at all above the top floor.
 *
 * Open at the top on purpose. A roof, a wall and a lookout on the highest deck are all ON the
 * highest deck -- there is no floor above them to belong to instead -- so the top band's slab
 * runs to infinity and every piece over the platform lands on exactly one floor. */
function ceilingOf(platform: FloorPlatform, band: FloorBand): number {
  var top = Number.POSITIVE_INFINITY;
  var here = band.top_m === null ? 0 : band.top_m;
  platform.bands.forEach(function (other) {
    var level = other.top_m;
    if (level !== null && level > here && level < top) top = level;
  });
  return top;
}

/* The one geometric rule in this file: does a thing at `z` stand on this band?
 *
 * From this deck's top surface up to the next deck's, each less the concrete it is poured
 * into. Used for storage, which `/api/floors` says nothing about, and for nothing else. */
function standsOn(platform: FloorPlatform, band: FloorBand, z_m: number): boolean {
  var top = band.top_m;
  if (top === null) return false;
  return z_m >= top - DECK_DEPTH_M && z_m < ceilingOf(platform, band) - DECK_DEPTH_M;
}

/** Every instance id this floor lists, as a lookup. */
function idsOn(band: FloorBand | null, body: FloorsResponse): Record<string, boolean> {
  var out: Record<string, boolean> = {};
  if (band) {
    band.machines.forEach(function (id) {
      out[id] = true;
    });
    band.attachments.forEach(function (id) {
      out[id] = true;
    });
    return out;
  }
  // The ground: the three honest ways of not being on a floor, exactly as the API groups
  // them. `exempt` is a miner on a node or a pump on water, `terrain` is measured against
  // the heightfield, and `off-deck` is what `terrain` degrades to where there is no field.
  ["exempt", "terrain", "off-deck"].forEach(function (group) {
    (body.placements[group] || []).forEach(function (row) {
      out[row.instance_leaf] = true;
    });
  });
  return out;
}

/** Which foundation rows this floor's deck is made of. The ground gets none: it is the
 *  ground, and drawing the storey above it underneath it would be an invention. */
function deckRows(band: FloorBand | null): Record<number, boolean> {
  var out: Record<number, boolean> = {};
  if (band) {
    band.deck_rows.forEach(function (row) {
      out[row] = true;
    });
  }
  return out;
}

/** Is this end of a run this floor? */
function isHere(end: FloorDeck | null, platform: number, band: FloorBand): boolean {
  return !!end && end.platform === platform && end.ordinal === band.ordinal;
}

/* Which runs belong on this floor, keyed the way the drawn pieces are marked.
 *
 * Same-deck runs on this band, plus every CONNECTOR with an end on it -- a connector is drawn
 * on every floor it touches, which is the whole point of it. The ground takes the runs that
 * never reach a deck (`terrain`) and the ones with only one end on one (`mixed`).
 *
 * The runs themselves come back rather than a set of keys, because the glyph on a connector
 * has to say where the other end goes and only the run knows. */
function runsOn(
  body: FloorsResponse,
  band: FloorBand | null,
  platform: number
): Record<string, FloorRun> {
  var out: Record<string, FloorRun> = {};
  function put(run: FloorRun): void {
    out[run.kind + ":" + run.key] = run;
  }
  if (!band) {
    ["terrain", "mixed"].forEach(function (membership) {
      (body.runs[membership] || []).forEach(put);
    });
    return out;
  }
  (body.runs["same-deck"] || []).forEach(function (run) {
    if (isHere(run.ends[0] || null, platform, band)) put(run);
  });
  (body.runs["connector"] || []).forEach(function (run) {
    if (run.ends.some(function (end) {
        return isHere(end || null, platform, band);
      })) {
      put(run);
    }
  });
  return out;
}

/* --------------------------------------------------------------- the filtering */

/** Everything a group holds, snapshotted once per redraw so that leaving can put it back. */
function snapshot(group: L.LayerGroup): L.Layer[] {
  if (!group._floorAll) {
    var all: L.Layer[] = [];
    group.eachLayer(function (piece) {
      all.push(piece);
    });
    group._floorAll = all;
  }
  return group._floorAll;
}

/* Ghosting, and why it is a restyle rather than a second polygon.
 *
 * A Refinery is 15 m tall on this world's 12 m storey module, so it is physically through the
 * deck above and in the way of anything built there -- a fact about the floor above that only
 * the floor below records. The machine is already drawn at its real footprint and its real
 * yaw; what changes is how it is stroked, so there is no second shape to keep in step with
 * the first and no chance of an outline being somewhere the machine is not.
 *
 * The original options are parked on the path, because unghosting has to be exact: guessing
 * the drawing module's colours back would put this file in the business of knowing what a
 * Constructor is painted. */
function ghost(piece: L.Path, rows: Row[]): void {
  if (!piece._floorStyle) {
    var was = piece.options;
    piece._floorStyle = {
      color: was.color,
      weight: was.weight,
      opacity: was.opacity,
      fillOpacity: was.fillOpacity,
      dashArray: was.dashArray,
    };
    // The card goes with the paint. A ghost says something different about the same machine
    // -- where it really stands, and how far through this floor it comes -- and a machine
    // that stops being a ghost on the next storey must stop saying it.
    //
    // The CONTENT, not the popup object. `bindPopup` with a string REUSES the popup a layer
    // already has, so keeping the popup would be keeping a reference to the very thing the
    // next line overwrites -- and the machine would carry the ghost's card for ever.
    // Asserted, not widened: `getContent` is typed for Leaflet's function form as well, and
    // no popup on this page is built that way -- see the note on `_floorCard`.
    var card = piece.getPopup();
    piece._floorCard = card ? (card.getContent() as string | HTMLElement) : null;
  }
  piece.setStyle({ weight: 1, opacity: 0.6, fillOpacity: 0.06, dashArray: "3,4" });
  piece.setPopupContent(popup(rows));
}

function unghost(piece: L.Path): void {
  var was = piece._floorStyle;
  if (!was) return;
  var card = piece._floorCard;
  delete piece._floorStyle;
  delete piece._floorCard;
  // `dashArray: undefined` does not clear a dash: Leaflet's setStyle copies the options it
  // is given and an absent key changes nothing. The one option that has to be UN-set is
  // therefore spelled empty rather than left out.
  piece.setStyle({ dashArray: "" });
  piece.setStyle(was);
  if (card !== null && card !== undefined) piece.setPopupContent(card);
}

/** Which band of this platform an id stands on, if any. */
function bandOfId(platform: FloorPlatform, id: string): FloorBand | null {
  var found: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (band.machines.indexOf(id) >= 0) found = band;
  });
  return found;
}

/* One machine, seen from a floor it is not on: does it come up through this one?
 *
 * `h_m` is the clearance box's third side, and it is null for the buildings the docs dump
 * carries no clearance for. A null height is not a short machine -- it is a machine whose
 * height was never recorded -- so it draws nothing rather than a ghost that would be
 * indistinguishable from a measured one. */
function piercesFloor(platform: FloorPlatform, band: FloorBand, mark: FloorMark): boolean {
  if (mark.z_m === undefined || mark.h_m === undefined || mark.h_m === null) return false;
  if (mark.id === undefined) return false;
  var deck = band.top_m;
  if (deck === null) return false;
  var stands = bandOfId(platform, mark.id);
  if (!stands || stands.top_m === null || stands.top_m >= deck) return false;
  return mark.z_m + mark.h_m > deck;
}

function ghostRows(platform: FloorPlatform, band: FloorBand, mark: FloorMark): Row[] {
  var stands = mark.id === undefined ? null : bandOfId(platform, mark.id);
  var through = (mark.z_m || 0) + (mark.h_m || 0) - (band.top_m || 0);
  return [
    ["ghost", "not on this floor — it comes up through it"],
    ["stands on", stands ? floorName(stands) + ", " + metres(stands.top_m) : null],
    ["height", mark.h_m + " m above its own deck"],
    ["through this floor", Math.round(through * 10) / 10 + " m"],
    ["instance", code(mark.id)],
  ];
}

/* The up/down glyph a connector gets on every floor it touches.
 *
 * A conveyor lift is already drawn as a ring, which is the right picture -- seen from above,
 * a lift is a hole in the floor -- and a ring says nothing about which way it goes. The glyph
 * does, and the popup says where: the two ends `/api/floors` measured, named as FLOORS rather
 * than as heights, because "it goes to floor 4" is the sentence a reader is after.
 *
 * A divIcon rather than a path, so the arrow is a character instead of a triangle this file
 * has to build -- and a REAL size, unlike the factory anchors next door, which are 0x0
 * because their tooltip is the clickable body. Here the arrow IS the body: an icon sized 0x0
 * with the character overflowing it looks identical and cannot be clicked at all, so the
 * popup saying where the connector goes would be unreachable. `GLYPH_PX` is therefore both
 * the mark and its pointer target, anchored on its own centre so it sits on the run's end. */
function otherEnd(run: FloorRun, platform: number, band: FloorBand): FloorDeck | null {
  var found: FloorDeck | null = null;
  run.ends.forEach(function (end) {
    if (end && !isHere(end, platform, band)) found = end;
  });
  return found;
}

function connectorGlyph(run: FloorRun, platform: number, band: FloorBand, at: Point3M): L.Marker {
  var away = otherEnd(run, platform, band);
  var up = !!away && (away.top_m || 0) > (band.top_m || 0);
  var marker = L.marker([-at[1], at[0]], {
    icon: L.divIcon({
      className: "floor-connector" + (up ? " floor-connector-up" : " floor-connector-down"),
      html: up ? "&#9650;" : "&#9660;",
      iconSize: [GLYPH_PX, GLYPH_PX],
      iconAnchor: [GLYPH_PX / 2, GLYPH_PX / 2],
    }),
  });
  marker.bindPopup(
    popup([
      [
        run.lift ? "conveyor lift" : run.kind === "pipe" ? "pipe riser" : "belt riser",
        up ? "goes up from this floor" : "goes down from this floor",
      ],
      ["to", away ? floorName(away) + ", " + metres(away.top_m) : "no deck — the ground"],
      ["rise", run.rise_m === null ? null : run.rise_m + " m"],
      // Which of the two claims this is. A lift is a class the docs dump names; a riser is a
      // run that climbs six metres or more -- and stage 0 measured that a quarter of lifts
      // are belt-height jogs on one deck, which is why the two are not the same word.
      ["kind", run.lift ? "a conveyor lift, by class" : "a run that climbs a storey or more"],
      [run.kind === "pipe" ? "pipe row" : "chain", "#" + run.key],
    ])
  );
  return marker;
}

/** The end of one drawn piece nearest this floor's deck, in game metres. */
function endNearest(mark: FloorMark, deck: number): Point3M | null {
  if (!mark.ends) return null;
  var head = mark.ends[0];
  var tail = mark.ends[1];
  return Math.abs(head[2] - deck) <= Math.abs(tail[2] - deck) ? head : tail;
}

/* One pass over every filtered layer. Idempotent, and re-run after every redraw.
 *
 * The order INSIDE the loop is not arbitrary: `FILTERED` starts with the concrete because the
 * storage rule needs this deck's own 8 m cells, and the only place those exist on this side
 * of the wire is the foundation pieces this pass has just decided to keep. */
function applyFilter(): void {
  if (!view || !view.platform || !state.floor) return;
  var platform = view.platform;
  var band = bandOf(platform, state.floor.band);
  var ids = idsOn(band, view.body);
  var rows = deckRows(band);
  var runs = runsOn(view.body, band, platform.index);
  var tile = view.body.rules.tile_m || 8;
  var cells: Record<string, boolean> = {};
  var glyphs: Record<string, boolean> = {};

  function cellKey(x_m: number, y_m: number): string {
    return Math.floor(x_m / tile) + "," + Math.floor(y_m / tile);
  }

  applying = true;
  try {
    FILTERED.forEach(function (name) {
      var group = state.layers[name];
      if (!group) return;
      var all = snapshot(group);
      var keep: L.Layer[] = [];
      var ghosts: L.Path[] = [];
      var extra: L.Layer[] = [];
      all.forEach(function (piece) {
        var mark = piece._floor;
        if (!mark) return; // a piece nothing marked is a piece nothing can place
        if (mark.row !== undefined) {
          if (!rows[mark.row]) return;
          if (mark.x_m !== undefined && mark.y_m !== undefined) {
            cells[cellKey(mark.x_m, mark.y_m)] = true;
          }
          keep.push(piece);
          return;
        }
        if (mark.run) {
          var key = mark.run.kind + ":" + mark.run.key;
          var run = runs[key];
          if (!run) return;
          keep.push(piece);
          // One glyph per RUN and not per piece: a chain is several drawn pieces, and three
          // arrows stacked on one lift would be three claims about one thing.
          if (!band || band.top_m === null || glyphs[key]) return;
          if (!run.riser && !run.lift) return;
          var at = endNearest(mark, band.top_m);
          if (!at) return;
          glyphs[key] = true;
          extra.push(connectorGlyph(run, platform.index, band, at));
          return;
        }
        if (mark.id !== undefined) {
          if (ids[mark.id]) {
            keep.push(piece);
            return;
          }
          if (band && piece instanceof L.Path && piercesFloor(platform, band, mark)) {
            ghost(piece, ghostRows(platform, band, mark));
            ghosts.push(piece);
            keep.push(piece);
          }
          return;
        }
        // Storage: the one layer joined by where it stands, because the decomposition never
        // claimed it. Both halves have to hold -- over this deck's own concrete, and between
        // this deck and the next.
        if (mark.z_m !== undefined && mark.x_m !== undefined && mark.y_m !== undefined) {
          if (band && cells[cellKey(mark.x_m, mark.y_m)] && standsOn(platform, band, mark.z_m)) {
            keep.push(piece);
          }
        }
      });
      all.forEach(function (piece) {
        if (piece instanceof L.Path && ghosts.indexOf(piece) < 0) unghost(piece);
      });
      group.clearLayers();
      // A `for` rather than a `forEach`, and it is not a style choice: `group` is narrowed
      // by the guard above and TypeScript drops that narrowing inside a closure, so the
      // loop that re-adds has to stay in the same scope as the check that it exists.
      var drawn = keep.concat(extra);
      for (var i = 0; i < drawn.length; i++) group.addLayer(drawn[i]!);
    });
  } finally {
    applying = false;
  }
}

/** Put every layer back the way its drawing module left it. */
function clearFilter(): void {
  applying = true;
  try {
    FILTERED.forEach(function (name) {
      var group = state.layers[name];
      if (!group || !group._floorAll) return;
      var all = group._floorAll;
      delete group._floorAll;
      group.clearLayers();
      for (var i = 0; i < all.length; i++) {
        var piece = all[i]!;
        if (piece instanceof L.Path) unghost(piece);
        group.addLayer(piece);
      }
    });
  } finally {
    applying = false;
  }
}

/** Re-apply the filter to whatever was just redrawn -- called by load.ts after every draw,
 *  because a refetch replaces a layer's CONTENTS and the filter is a fact about contents.
 *  It is also where a flight the view still owes finally becomes possible; see `flown`. */
export function refilterFloors(): void {
  if (!view) return;
  applyFilter();
  flyToPlatform();
}

/* A save write changes what is built, so it changes the decomposition, and the ids a band
 * lists are what this whole filter runs on. Without this a machine placed since the view was
 * opened would be on no floor at all -- drawn by `/api/machines`, listed by no band, and
 * therefore silently missing rather than visibly new.
 *
 * Same query, same band, no flight: this is a refresh of the answer and not a new question. */
export function refreshFloors(): void {
  if (!view) return;
  var was = view;
  get<FloorsResponse & { error?: string }>(url(was.query))
    .then(function (body) {
      if (view !== was) return; // left, or moved on, while this was in the air
      view.body = body;
      view.platform = (body.platforms || [])[0] || null;
      applyFilter();
      showPicker();
    })
    .catch(function () {
      /* the view on screen stays the view on screen; the next save write tries again */
    });
}

/* -------------------------------------------------------------------- the picker */

function metres(value: number | null): string {
  if (value === null) return "height not recorded";
  return (value >= 0 ? "+" : "") + value + " m";
}

function floorName(band: { ordinal: number }): string {
  return "Floor " + band.ordinal;
}

/* The ground row's own sentence, and it depends on whether the ground was MEASURED.
 *
 * `terrain_measured` says whether a heightfield was there to compare against. Without one the
 * API's honest answer is `off-deck` rather than `terrain`, and a picker that called both "on
 * the ground" would be quietly upgrading the weaker claim to the stronger one. */
function groundDetail(body: FloorsResponse): string {
  var placed = groundPlacements(body);
  var runs = groundRuns(body);
  var how = body.terrain_measured
    ? "on terrain, measured"
    : "off-deck — no heightfield here to measure against";
  return how + " · " + placed + " placed · " + runs + " runs";
}

function groundPlacements(body: FloorsResponse): number {
  var counts = body.counts.placements;
  return (counts["exempt"] || 0) + (counts["terrain"] || 0) + (counts["off-deck"] || 0);
}

function groundRuns(body: FloorsResponse): number {
  var counts = body.counts.membership;
  return (counts["terrain"] || 0) + (counts["mixed"] || 0);
}

function hasGround(body: FloorsResponse): boolean {
  return groundPlacements(body) + groundRuns(body) > 0;
}

function choices(body: FloorsResponse, platform: FloorPlatform | null): FloorChoice[] {
  var rows: FloorChoice[] = [];
  if (!platform) return rows;
  if (hasGround(body)) {
    rows.push({
      key: GROUND,
      label: "Ground",
      detail: groundDetail(body),
      // Not minor: it is a different KIND of answer rather than a small one, and dimming it
      // would say "mezzanine" about the one row that is not a band at all.
      minor: false,
      note:
        "everything over this factory's footprint that the decomposition put on no band: a " +
        "miner stands on a resource node and a pump on water, and plumbing hugs the ground",
    });
  }
  platform.bands.forEach(function (band) {
    rows.push({
      key: String(band.ordinal),
      label: floorName(band),
      detail: metres(band.top_m) + " · " + band.cells + " cells · " + band.machine_count + " machines",
      minor: band.minor,
      note: band.minor
        ? "a mezzanine: " +
          Math.round(band.share * 100) +
          "% of this platform's largest deck, so a ledge rather than a storey"
        : band.area_m2 +
          " m² of deck, " +
          band.pieces +
          " foundation pieces, " +
          band.attachment_count +
          " belt junctions",
    });
  });
  return rows;
}

/* Which floor to open on: the busiest real storey, so the first thing shown is a factory.
 *
 * Not floor zero. On the reference world the tallest platform's lowest deck holds no machines
 * at all -- it is the concrete the tower stands on -- and opening there would answer "show me
 * the floors of this factory" with an empty slab. */
function busiestBand(platform: FloorPlatform): FloorBand | null {
  var best: FloorBand | null = null;
  platform.bands.forEach(function (band) {
    if (best === null || band.machine_count > best.machine_count) best = band;
  });
  return best;
}

function busiest(platform: FloorPlatform): string {
  var band = busiestBand(platform);
  return band ? String(band.ordinal) : GROUND;
}

function showPicker(): void {
  if (!view) return;
  showFloors(
    "floors — " + view.title,
    choices(view.body, view.platform),
    state.floor ? state.floor.band : "",
    view.message
  );
}

/* --------------------------------------------------------------- entering, leaving */

/* A platform's own extent, from the decks this page has actually drawn.
 *
 * From the pieces rather than from `centre_m` and `extent_m`, and that is a correction rather
 * than a preference: the centre the API sends is the MEAN of a platform's pieces, which on
 * the reference world's tower sits 30 m from the middle of its own bounding box. A mean and a
 * span do not make a box, and flying to one made of them clips the deck at one edge. */
function platformBounds(platform: FloorPlatform): L.LatLngBounds | null {
  var group = state.layers["foundations"];
  if (!group) return null;
  var rows: Record<number, boolean> = {};
  platform.bands.forEach(function (band) {
    band.deck_rows.forEach(function (row) {
      rows[row] = true;
    });
  });
  var lat: number[] = [];
  var lng: number[] = [];
  snapshot(group).forEach(function (piece) {
    var mark = piece._floor;
    if (!mark || mark.row === undefined || !rows[mark.row]) return;
    if (mark.x_m === undefined || mark.y_m === undefined) return;
    lat.push(-mark.y_m);
    lng.push(mark.x_m);
  });
  if (!lat.length) return null;
  // Padded in METRES, which under this CRS is what a latlng unit is -- `pad` takes a fraction
  // of the box's own size, so a 600 m platform and a 40 m one would get different margins.
  return L.latLngBounds(
    [Math.min.apply(null, lat) - FLOOR_PAD_M, Math.min.apply(null, lng) - FLOOR_PAD_M],
    [Math.max.apply(null, lat) + FLOOR_PAD_M, Math.max.apply(null, lng) + FLOOR_PAD_M]
  );
}

/** Take the map to the platform, once, as soon as there is a drawn deck to measure it from. */
function flyToPlatform(): void {
  if (!view || view.flown || !view.platform) return;
  var bounds = platformBounds(view.platform);
  if (!bounds) return;
  view.flown = true;
  map.flyToBounds(bounds, { maxZoom: FLOOR_MAX_ZOOM });
}

/* Turn on what a floor is made of, once, and remember what this mode turned on.
 *
 * Often nothing, and that is the point of returning a list rather than a count. The card is
 * reached by clicking a factory LABEL, and that click has already revealed exactly these three
 * layers with its own toast -- so entering from the card usually turns on nothing and leaving
 * therefore turns off nothing, which is right: the layers are the reader's from the label
 * click onwards. The list is only ever non-empty on the path that skips the label, which is a
 * pasted `#floor=` link. */
function revealFor(): string[] {
  var turned: string[] = [];
  applying = true;
  try {
    batch(function () {
      FLOOR_LAYERS.forEach(function (name) {
        var group = state.layers[name];
        if (!group || map.hasLayer(group)) return;
        group.addTo(map);
        turned.push(name);
      });
    });
  } finally {
    applying = false;
  }
  return turned;
}

/* A tick the reader makes during floor mode is theirs, and leaving must not undo it.
 *
 * Exactly the rule regions.ts states for the base map's default: the default is what the page
 * does when it has not been told, not what it does instead of being told. So a layer this mode
 * turned on stops being this mode's the moment the reader touches its box, and leaving puts
 * back only what is still on loan.
 *
 * Registered in main.ts with the rest of the map listeners, so the order it runs in is written
 * down in one place rather than decided by the import graph. */
export function noteFloorChoice(event: L.LeafletEvent): void {
  if (!view || applying) return;
  var touched = (event as L.LayersControlEvent).layer;
  view.turned = view.turned.filter(function (name) {
    return state.layers[name] !== touched;
  });
  // A layer ticked ON mid-mode arrives holding every piece in it, so it owes the filter a pass.
  refilterFloors();
}

/** `/api/floors` with a query, spelled once so the template type stays checked. */
function url(query: string): `/api/floors?${string}` {
  return ("/api/floors?" + query) as `/api/floors?${string}`;
}

/* Enter floor mode for one factory or one platform.
 *
 * `band` is what the fragment asked for, where it asked for one; without it the busiest storey
 * is opened, because "show me the floors of this factory" should land on a factory.
 *
 * A 4xx is not a failure to be toasted away here. `/api/floors` answers a factory standing on
 * no platform with a sentence saying so, and a save too old to record foundations with a 200
 * and a note. Both are ANSWERS, and both are shown in the picker -- the mode is entered either
 * way, so a reader gets the sentence and a way out rather than a toast that disappears over a
 * map that did not change. */
export function enterFloors(query: string, title: string, band?: string): void {
  get<FloorsResponse & { error?: string }>(url(query))
    .then(function (body) {
      open(query, body, title, band);
    })
    .catch(function (error) {
      // `get` throws with the server's own `error` string, which for a selection that matched
      // nothing is "no platform matches factory 'x'" -- the sentence to show, not to hide.
      open(query, { platforms: [], note: friendly(error) } as unknown as FloorsResponse, title, band);
    });
}

function open(query: string, body: FloorsResponse, title: string, band?: string): void {
  var platform = (body.platforms || [])[0] || null;
  view = {
    query: query,
    platform: platform,
    body: body,
    title: platform && platform.label ? platform.label : title,
    message: platform ? "" : body.note || "no floors here",
    turned: [],
    flown: false,
  };
  if (!platform) {
    // Nothing to slice, and a reason for it. `state.floor` stays null: there is no storey
    // being looked at, so the fragment must not claim one.
    state.floor = null;
    showPicker();
    return;
  }
  view.turned = revealFor();
  var wanted = band || busiest(platform);
  if (wanted === GROUND ? !hasGround(body) : !bandOf(platform, wanted)) wanted = busiest(platform);
  state.floor = { platform: platform.index, band: wanted };
  showPicker();
  applyFilter();
  flyToPlatform();
  writeHash();
}

/* Leave, putting back exactly what this mode took -- and nothing the reader has since claimed.
 *
 * The viewport is deliberately left where it is. The reader may have panned somewhere on
 * purpose, and flying back would be this mode having the last word about where to look; the
 * house button under the zoom control is the page's existing way of asking for the world. */
export function leaveFloors(): void {
  if (!view) return;
  var turned = view.turned;
  clearFilter();
  view = null;
  state.floor = null;
  hideFloors();
  applying = true;
  try {
    batch(function () {
      turned.forEach(function (name) {
        var group = state.layers[name];
        if (group && map.hasLayer(group)) map.removeLayer(group);
      });
    });
  } finally {
    applying = false;
  }
  writeHash();
}

/** Switch storey. Neither the layers nor the map move: one floor of a factory is the same
 *  place as the next one, and re-flying between them would be motion for its own sake. */
export function pickBand(key: string): void {
  if (!view || !view.platform || !state.floor) return;
  state.floor = { platform: state.floor.platform, band: key };
  applyFilter();
  showPicker();
  writeHash();
}

/* ---------------------------------------------------------------- the fragment */

/** `floor=<platform>/<band>`, where band is an ordinal or `ground`. Anything else is ignored
 *  rather than resolved to a guess -- the same refusal `askedMode` makes about a mode this
 *  server does not serve. */
export function parseFloorFragment(raw: string | undefined): { platform: number; band: string } | null {
  if (!raw) return null;
  var parts = raw.split("/");
  if (parts.length !== 2) return null;
  var platform = +parts[0]!;
  var band = parts[1]!;
  if (!isFinite(platform) || platform < 0 || Math.floor(platform) !== platform) return null;
  if (band !== GROUND && !/^\d+$/.test(band)) return null;
  return { platform: platform, band: band };
}

/* One fragment's floor half, applied. Returns whether anything moved, because the caller owes
 * a write if nothing else did -- the same contract `applySubject` has next to it.
 *
 * A platform index rather than a factory name in the address, deliberately: the index is what
 * `/api/floors` hands out and promises to be stable over one save, and a factory name would
 * make the link depend on the player not renaming anything. */
export function applyFloorFragment(asked: string | undefined): boolean {
  var want = parseFloorFragment(asked);
  var have = state.floor;
  if (!want) {
    if (!view) return false;
    leaveFloors();
    return true;
  }
  if (have && have.platform === want.platform && have.band === want.band) return false;
  if (have && have.platform === want.platform) {
    pickBand(want.band);
    return true;
  }
  enterFloors("platform=" + want.platform, "platform " + want.platform, want.band);
  return true;
}

/* ------------------------------------------------------------------- the card */

/* The action on a factory card, and the page's required way in.
 *
 * An HTMLElement rather than a string of markup, because the card now has to carry a LISTENER
 * and Leaflet keeps the element it is handed -- so the handler is bound once, at build time,
 * instead of being re-bound and stacked on every `popupopen`. The rows themselves still go
 * through `popup()`, which escapes everything; nothing here puts data in the DOM by hand. */
export function cardWithFloors(rows: Row[], factory: string): HTMLElement {
  var card = document.createElement("div");
  card.innerHTML = popup(rows);
  var action = document.createElement("button");
  action.type = "button";
  action.className = "card-action";
  action.textContent = "floors";
  action.title =
    "show this factory one storey at a time — its decks are recovered from the geometry, " +
    "not read off the save";
  L.DomEvent.on(action, "click", function (event) {
    L.DomEvent.stop(event);
    map.closePopup();
    enterFloors("factory=" + encodeURIComponent("label:" + factory), factory);
  });
  card.appendChild(action);
  return card;
}

/* ------------------------------------------------------------------- the wiring */

/** ESC leaves. Registered in main.ts beside the map's own listeners, because it is the same
 *  kind of fact -- an event the page reacts to, wired where a reader can see the whole set.
 *
 *  A popup first: ESC over an open card closes the card, which is what a reader who just
 *  opened one expects, and only a second press leaves the mode. */
export function escapeLeavesFloorMode(event: KeyboardEvent): void {
  if (event.key !== "Escape" || !view) return;
  if (document.querySelector(".leaflet-popup")) {
    map.closePopup();
    return;
  }
  leaveFloors();
  note("left floor mode — the whole world again");
}

onFloorPick(pickBand);
onFloorExit(leaveFloors);
