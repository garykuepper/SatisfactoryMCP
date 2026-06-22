/* The map. Reads /api, draws markers, and refetches when the game writes a save.
 *
 * Two coordinate facts drive everything below.
 *
 *   1. The API already speaks metres. Nothing here divides by 100 -- if a number looks
 *      like centimetres, the bug is server-side.
 *   2. Satisfactory is +X east and +Y SOUTH, while Leaflet's CRS.Simple is +lat north.
 *      So a point is plotted at [-y, x], and that negation is the only place the two
 *      conventions meet. Doing it anywhere else produces a map that is correct until
 *      someone compares it with an in-game coordinate.
 *
 * And one content fact: every string that reaches a popup or a label is DATA -- factory
 * names and notes from the player's label file, class ids from the save, region names
 * from a JSON file. popup() escapes everything by default; the few rows that need markup
 * say so explicitly with html(). A factory named "<b>x</b>" prints as its own text.
 */

"use strict";

var BOUND = 5000; // metres; the playable world is ~7 km across, so this frames it loosely.

var HOME_VIEW = { centre: [0, 0], zoom: -3 }; // the whole-world framing every load starts from.

function xy(row) {
  return [-row.y_m, row.x_m];
}

function el(id) {
  return document.getElementById(id);
}

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

function toast(message, kind, ms) {
  var box = el("err");
  var rows = Array.prototype.slice.call(box.children);
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

function fail(message) {
  toast(message, "fail", FAIL_MS);
}

function note(message) {
  toast(message, "note", NOTE_MS);
}

/* Browser-internal error phrases, translated to what they mean HERE. "Failed to fetch"
 * is Chrome for "the server you started is gone", and that is the actionable sentence. */
function friendly(error) {
  var text = error && error.message ? error.message : String(error);
  if (/Failed to fetch|NetworkError|Load failed/i.test(text)) {
    return "the server is not answering — is it still running?";
  }
  if (/Unexpected token|not valid JSON/i.test(text)) {
    return "the server answered with something that is not JSON";
  }
  return text;
}

function get(path) {
  var q = "";
  var sep = path.indexOf("?") < 0 ? "?" : "&";
  if (state.world) {
    q += sep + "world=" + encodeURIComponent(state.world);
    sep = "&";
  }
  if (state.save) {
    q += sep + "save=" + encodeURIComponent(state.save);
  }
  return fetch(path + q).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
      return body;
    });
  });
}

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* A pre-built fragment for the rows that genuinely need markup. Everything interpolated
 * into it still goes through esc() at the call site -- html() only marks the result as
 * finished, it does not bless its inputs. */
function html(markup) {
  return { html: markup };
}

function code(text) {
  return html("<code>" + esc(text) + "</code>");
}

function popup(pairs) {
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

/* A resource class as the short name the whole page uses: Desc_OreIron_C -> OreIron. */
function shortResource(resource) {
  return String(resource || "")
    .replace(/^Desc_/, "")
    .replace(/_C$/, "");
}

/* A region lookup as one line: "Northern Forest, interior".
 *
 * The confidence word is never dropped, not even for an interior hit. The raster is 256 m
 * per cell, so the name and how far it can be trusted are one claim -- and a name printed
 * bare next to a MEASURED elevation would borrow that measurement's authority. `null` is
 * the ocean-or-off-map answer, and it is said plainly rather than softened into the
 * nearest bit of land. */
function regionLine(region) {
  return region ? region.name + ", " + region.confidence : "off the map";
}

/* The engine's phase asset name as words: GP_Project_Assembly_Phase_3 ->
 * "Project Assembly phase 3". Null for the pre-1.0 saves that carry no phase at all,
 * so the header can omit the segment instead of printing "phase " and a hole. */
function phaseText(raw) {
  if (!raw) return null;
  var match = /^GP_(.+)_Phase_(\d+)$/.exec(raw);
  if (match) return match[1].replace(/_/g, " ") + " phase " + match[2];
  return raw;
}

/* ------------------------------------------------------------------ palette */

// Ore colours follow the in-game item tints closely enough to be recognisable without
// shipping a single game asset: they are hex strings, not textures.
var RESOURCE_COLOUR = {
  Desc_OreIron_C: "#c8b6a6",
  Desc_OreCopper_C: "#e08a4b",
  Desc_Stone_C: "#cfcfcf",
  Desc_Coal_C: "#4c4c4c",
  Desc_OreGold_C: "#e3c74a",
  Desc_Sulfur_C: "#e8e35c",
  Desc_RawQuartz_C: "#e59ce0",
  Desc_OreBauxite_C: "#b06a4a",
  Desc_OreUranium_C: "#7ce07c",
  Desc_LiquidOil_C: "#6b4bb0",
  Desc_NitrogenGas_C: "#6ec5e0",
  Desc_Water_C: "#3f8fd0",
  Desc_SAM_C: "#b04bd0",
  Desc_Geyser_C: "#d97b4f", // synthetic label; a geyser is a placement target, not an item
};

var PURITY_RADIUS = { impure: 3, normal: 4.5, pure: 6 };

var KIND_COLOUR = { machines: "#4aa3df", extractors: "#e0a33f", generators: "#d9534f" };

// One colour per pickup category, so ten separate checkboxes stop drawing one
// indistinguishable teal dot. Unlisted categories share the old teal as the fallback.
var PICKUP_COLOUR = {
  somersloop: "#e05c5c",
  mercer_sphere: "#b06ae0",
  hard_drive: "#6ea8d8",
  loot_cache: "#d8b46e",
  crashed_drop_pod: "#9aa8b8",
  power_slug_blue: "#5cc8e8",
  power_slug_yellow: "#e8d55c",
  power_slug_purple: "#c85ce8",
  mushroom: "#a8c86e",
  tape_pickup: "#e09a6e",
};
var PICKUP_FALLBACK = "#7fd1b9";

var PLAYER_COLOUR = "#f5f0e8";

// One muted colour per biome letter, keyed exactly like /api/regions' legend. Hand-picked
// to read as ground at a glance -- sand for the deserts, greens for the forests, teal
// along the coast, murk for the swamp -- and, like the ore palette above, they are 21 hex
// strings rather than a single pixel of anyone's artwork.
//
// Dark on purpose, and every CELL is painted at full opacity, always: a translucent cell
// has to blend against whatever is at its edges too, and 768 of them sharing borders turns
// that blend into a visible 256 m grid. These are the blended values, baked in, so the
// cells of one region merge into one shape.
//
// Transparency over the map render is therefore NOT done here -- see REGION_BLEND, which
// fades the finished composite once, at the pane.
var REGION_COLOUR = {
  A: "#3e3e3c", // Abyss Cliffs
  B: "#284e5a", // Blue Crater
  C: "#2e5348", // Crater Lakes
  D: "#654e37", // Desert Canyons
  E: "#726443", // Dune Desert
  F: "#4e5c3d", // Eastern Dune Forest
  G: "#3b5a3b", // Grass Fields
  H: "#294834", // Jungle Spires
  I: "#32544d", // Lake Forest
  J: "#594a37", // Maze Canyons
  K: "#2e4637", // Northern Forest
  L: "#65423b", // Red Bamboo Fields
  M: "#4e3937", // Red Jungle
  N: "#5c5b4e", // Rocky Desert
  O: "#415037", // Snaketree Forest
  P: "#335041", // Southern Forest
  Q: "#295258", // Spire Coast
  R: "#374232", // Swamp
  S: "#294233", // Titan Forest
  T: "#736d56", // Western Beaches
  U: "#585d40", // Western Dune Forest
};

var state = {
  world: "",
  save: "", // a pinned save's path; "" means "the newest, refetched on save events"
  worlds: [],
  layers: {},
  layerName: {}, // Leaflet's layer stamp -> the name its control row carries
  control: null,
  map: null,
  epoch: 0, // bumped on every world/save switch; a reply from an older epoch is dropped
  opened: Date.now(),
};

/* ----------------------------------------------------------------- URL state */

/* The selection lives in the URL fragment (#world=…&save=…&z=…&c=x,y) so a reload, a
 * bookmark or a pasted link lands on the same world, save and viewport instead of
 * silently teleporting to the newest world at the whole-world zoom. replaceState, not
 * assignment: panning must not grow the browser history by one entry per drag. */
var BOOT = (function () {
  var out = {};
  location.hash
    .replace(/^#/, "")
    .split("&")
    .forEach(function (piece) {
      var eq = piece.indexOf("=");
      if (eq > 0) out[piece.slice(0, eq)] = decodeURIComponent(piece.slice(eq + 1));
    });
  return out;
})();

function writeHash() {
  var parts = [];
  if (state.world) parts.push("world=" + encodeURIComponent(state.world));
  var pinned = pinnedFilename();
  if (pinned) parts.push("save=" + encodeURIComponent(pinned));
  var c = map.getCenter();
  parts.push("z=" + map.getZoom());
  parts.push("c=" + Math.round(c.lng * 10) / 10 + "," + Math.round(-c.lat * 10) / 10);
  history.replaceState(null, "", "#" + parts.join("&"));
}

function currentWorld() {
  var found = null;
  state.worlds.forEach(function (w) {
    if (w.world_id === state.world) found = w;
  });
  return found;
}

function pinnedFilename() {
  var name = "";
  var w = currentWorld();
  if (!state.save || !w) return name;
  (w.saves || []).forEach(function (s) {
    if ((s.path || s.filename) === state.save) name = s.filename;
  });
  return name;
}

/* --------------------------------------------------------------------- map */

/* The in-game map square, in metres, and the sheet the generator cuts from it. These two
 * numbers set the map's pixel unit below, and they are the same square the server pins by
 * default -- a render pinned anywhere else is drawn as one overlay instead (see
 * loadMapImage), because the tile grid below is anchored on THIS square. */
var MAP_SQUARE_M = { x_min: -3247, x_max: 4253, y_min: -3750, y_max: 3750 };
var MAP_SHEET_PX = 8192;
var MAP_M_PER_SHEET = MAP_SQUARE_M.x_max - MAP_SQUARE_M.x_min; // 7500 m, and square.
var MAP_PX_PER_M = MAP_SHEET_PX / MAP_M_PER_SHEET; // 1.0923 sheet pixels to the metre.

/* CRS.Simple with one change: a pixel is a pixel OF THE MAP SHEET, not a metre.
 *
 * This is what lets the base map be a tile pyramid at all, and it is not a preference.
 * Leaflet lays a tile grid out in the CRS's pixel space, from its origin, in whole tiles.
 * The pyramid's grid is the sheet cut into 2^z squares -- so the sheet's north-west corner
 * has to BE the pixel origin and the sheet has to be a power-of-two count of tiles across.
 * Under plain CRS.Simple the sheet starts at x = -3247 m and spans 7500, and no tile size
 * makes both of those land on a tile boundary: the grid would be offset from the picture
 * at every level -- by 0.43 of a tile at z0. Anchoring the pixel space on the sheet instead
 * makes zoom 0 exactly one screen pixel per sheet pixel, which is also what makes
 * Leaflet's own choice of tile level the right one -- at map zoom Z it draws level Z + 5,
 * whose 256 * 2^(Z+5) pixels are precisely the 8192 * 2^Z the view has room for.
 *
 * Everything else on the page is unaffected: coordinates are still latlng in metres and
 * still plotted at [-y, x]. The only visible consequence is that a given zoom number now
 * frames 9% less ground -- 8192/7500 -- which is a twelfth of one zoom step. */
var CRS_SHEET_PX = L.extend({}, L.CRS.Simple, {
  transformation: new L.Transformation(
    MAP_PX_PER_M,
    -MAP_SQUARE_M.x_min * MAP_PX_PER_M,
    -MAP_PX_PER_M,
    -MAP_SQUARE_M.y_min * MAP_PX_PER_M
  ),
});

var map = L.map("map", {
  crs: CRS_SHEET_PX,
  preferCanvas: true, // thousands of markers: canvas, not one SVG node each.
  minZoom: -6,
  maxZoom: 3,
  attributionControl: true,
  maxBounds: [
    [-BOUND, -BOUND],
    [BOUND, BOUND],
  ],
  maxBoundsViscosity: 0.6,
});
state.map = map;

(function () {
  // The viewport the page opens on: the URL's, if the fragment carries one.
  var zoom = isFinite(+BOOT.z) ? +BOOT.z : HOME_VIEW.zoom;
  var centre = HOME_VIEW.centre;
  if (BOOT.c) {
    var raw = BOOT.c.split(",");
    if (raw.length === 2 && isFinite(+raw[0]) && isFinite(+raw[1])) centre = [-+raw[1], +raw[0]];
  }
  map.setView(centre, zoom);
})();

map.on("moveend zoomend", writeHash);

/* How many screen pixels one metre of ground is worth at a given zoom.
 *
 * Asked of the map rather than restated as MAP_PX_PER_M * 2^zoom, so it cannot drift away
 * from the CRS above: project() runs the very transformation Leaflet draws with, and the
 * gap between two points one metre apart IS the answer. Two calls, once per zoom change --
 * never once per layer.
 *
 * This is what lets anything sized in PIXELS -- a polyline's weight, a circle's radius --
 * be given a size in metres instead. Polygons never need it: they are already in map units
 * and scale for free, which is exactly why the machines and the floor plan have been the
 * right size at every zoom since they were drawn, and the belts were not. */
function pixelsPerMetre(zoom) {
  var z = zoom === undefined ? map.getZoom() : zoom;
  return map.project([0, 1], z).x - map.project([0, 0], z).x;
}

// The prefix is the one piece of always-visible chrome, so it carries the one gesture the
// map has that nothing on screen hints at.
map.attributionControl
  .setPrefix("right-click: inspect a point")
  .addAttribution("map data from your save &middot; Leaflet");

/* One flat 10 km square of "land" used to be drawn here, in the overlay pane. It was a
 * stand-in for ground and it is gone: the biome raster is the real thing, and an opaque
 * square in the overlay pane would sit on top of it. What was inside that square is now
 * painted per biome, and what is outside is the page's sea colour -- which is the whole
 * trick behind the coastline. */

/* The optional map render -- tile pyramid or single overlay -- gets the bottom pane of
 * the three, so the region fill can be drawn OVER it rather than instead of it. It used
 * to share the regions pane, which was harmless only for as long as the two were mutually
 * exclusive; combining them made the stacking a question of which loader finished first. */
map.createPane("basemap");
map.getPane("basemap").style.zIndex = 340;

/* The biome regions everything else stands on get their own pane, below overlayPane (400),
 * so the region fill can never end up in front of a node the player is trying to click. The
 * stacking is decided here, once, instead of by the order things happen to be drawn.
 *
 * The pane is also the unit of transparency: see REGION_BLEND. */
map.createPane("regions");
map.getPane("regions").style.zIndex = 350;

/* The player's own concrete, in its own pane between the biome raster (350) and the
 * overlay pane (400): a floor plan has to cover the ground it was poured on and sit
 * under every machine, node and label that stands on it. Leaflet builds one canvas per
 * pane, so this is also what keeps 8,000 rectangles off the region cells' canvas. */
map.createPane("foundations");
map.getPane("foundations").style.zIndex = 360;

/* The layer control doubles as the map's legend and only filter, so its order has to
 * survive a reload: overlays used to be appended in whatever order six parallel fetches
 * resolved, which shuffled 32 rows between page loads. sortLayers pins the order to the
 * rank each group is given when it is created -- chrome first, then machine layers, then
 * nodes alphabetically, then pickups alphabetically. */
var LAYER_ORDER = [
  "regions",
  "map image",
  "region names",
  "player",
  "factory labels",
  "proposals",
  "foundations",
  "belts",
  "pipes",
  "machines",
  "extractors",
  "generators",
];

function layerRank(name) {
  var fixed = LAYER_ORDER.indexOf(name);
  if (fixed >= 0) return [0, fixed, name];
  if (name.indexOf("node: ") === 0) return [1, 0, name];
  if (name.indexOf("pickup: ") === 0) return [2, 0, name];
  return [3, 0, name];
}

var control = L.control.layers(
  null,
  {},
  {
    collapsed: false,
    sortLayers: true,
    sortFunction: function (a, b) {
      var ra = a._rank || [9, 0, ""];
      var rb = b._rank || [9, 0, ""];
      if (ra[0] !== rb[0]) return ra[0] - rb[0];
      if (ra[1] !== rb[1]) return ra[1] - rb[1];
      return ra[2] < rb[2] ? -1 : ra[2] > rb[2] ? 1 : 0;
    },
  }
).addTo(map);
state.control = control;
L.control.scale({ imperial: false }).addTo(map);

/* Thirty-three rows on the reference world -- 291x690 px, 12.5% of a 1600x1000 viewport
 * and a great deal more of a laptop -- permanently, because the control was built with
 * `collapsed: false` and nothing else could fold it.
 *
 * `collapsed: true` is not the fix. This control is the map's legend (every swatch) and
 * its only filter, so hiding it behind Leaflet's own hover toggle would make the page's
 * one index invisible until the pointer happened to cross a 36 px square -- a square drawn
 * from `vendor/images/layers.png`, which this project does not vendor and which would
 * therefore be the page's only 404. Both folds below are the page's own.
 *
 *   * The head row folds the whole list to one labelled strip that still says how many
 *     layers exist and how many are drawn, so "there ARE layers here" survives folding.
 *   * A section head folds one data-driven family -- the `node:` rows, the `pickup:` rows
 *     -- and those two start folded, because they are the families that grow with the
 *     world and that turned a nine-row legend into thirty-three. Their heads carry the
 *     same "n of m" count, which is what lets a folded section still answer "are the ore
 *     dots on?" without unfolding it.
 *
 * A section head also OWNS its family: the checkbox on it ticks or unticks all fourteen
 * node rows at once, in the three states such a box can honestly be in -- see sectionBox.
 * That is a second gesture on one row, so the two are kept on separate elements rather
 * than separated by guesswork about where inside the row the click landed.
 *
 * The choices persist across a world switch the same way the checkboxes do, and for the
 * same reason: both live in objects built once at module scope, and a switch replaces
 * layer CONTENTS without rebuilding the control, the layer groups or these flags.
 */
var SECTIONS = [
  { key: "nodes", prefix: "node: ", title: "resource nodes" },
  { key: "pickups", prefix: "pickup: ", title: "pickups" },
];

state.panel = { open: true, sections: { nodes: false, pickups: false } };

function sectionFor(name) {
  var found = null;
  SECTIONS.forEach(function (section) {
    if (name.indexOf(section.prefix) === 0) found = section;
  });
  return found;
}

/* A control row back to the layer it toggles. Leaflet stamps the layer's id onto the
 * checkbox it builds, and layer() files the name under that same stamp, so the mapping
 * survives every re-render of the list without parsing the row's text back. */
function rowName(row) {
  var input = row.querySelector("input");
  return (input && state.layerName[input.layerId]) || "";
}

function rowOn(row) {
  var input = row.querySelector("input");
  return !!(input && input.checked);
}

/* A control row back to the LayerGroup itself, for the one caller that has to toggle a
 * layer without a human clicking its box -- see setSection. */
function rowLayer(row) {
  var input = row.querySelector("input");
  return (input && state.layers[state.layerName[input.layerId]]) || null;
}

function fold(element, folded) {
  if (!element) return;
  if (folded) L.DomUtil.addClass(element, "layer-folded");
  else L.DomUtil.removeClass(element, "layer-folded");
}

function foldHead(element, open, title, count, total) {
  element.setAttribute("role", "button");
  element.setAttribute("tabindex", "0");
  element.setAttribute("aria-expanded", open ? "true" : "false");
  element.innerHTML =
    '<span class="layer-caret">' +
    (open ? "&#9662;" : "&#9656;") +
    "</span>" +
    esc(title) +
    '<span class="layer-count">' +
    count +
    " of " +
    total +
    "</span>";
  element.title =
    (open ? "hide " : "show ") + title + " — " + count + " of " + total + " drawn right now";
}

/* Both heads say `role="button"`, so both have to answer a keyboard the way a button
 * does. Every checkbox in this control is already reachable by Tab; a fold that could only
 * be opened with a pointer would put those checkboxes behind a mouse. */
function onActivate(element, action) {
  L.DomEvent.on(element, "click", function (event) {
    L.DomEvent.stop(event);
    action();
  });
  L.DomEvent.on(element, "keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    L.DomEvent.stop(event);
    action();
  });
}

/* Ticking a family of fourteen is fourteen layer events, and Leaflet re-renders the whole
 * list on each one -- measured on the reference world, one click on "resource nodes" cost
 * 28 full control renders and 14 ms. 14 ms is not a freeze, and this is not really a speed
 * fix: every intermediate render also DESTROYED the checkbox the pointer was on and re-ran
 * the focus restore against a half-toggled family, so the tri-state flickered through
 * thirteen wrong values and the focus this control is careful about was rebuilt thirteen
 * times for nothing.
 *
 * `_handlingClick` is Leaflet's own flag for exactly this -- its `_onLayerChange` skips the
 * re-render while it is set, which is how its own checkboxes stay sane. The two decorators
 * this file adds take the same hint, and one render happens at the end. */
var batching = false;

function batch(action) {
  batching = true;
  control._handlingClick = true;
  try {
    action();
  } finally {
    control._handlingClick = false;
    batching = false;
  }
  control._update(); // one render, which re-runs decorateControl with the settled state
  declutter();
}

/* Every layer of one family at once. The layers are toggled directly rather than by
 * clicking their boxes: Leaflet's own `_onInputClick` would do the adding, but it ends by
 * calling `_refocusOnMap`, and a keyboard user who just pressed Space on the family box
 * would find focus on the map. */
function setSection(rows, on) {
  batch(function () {
    rows.forEach(function (row) {
      var group = rowLayer(row);
      if (!group) return;
      if (on) map.addLayer(group);
      else map.removeLayer(group);
    });
  });
}

/* The family's own checkbox, and its third state.
 *
 * `indeterminate` is not decoration: a family with one member ticked would otherwise draw
 * an empty box, which is the same picture as a family with none -- and the count beside it
 * ("3 of 14") would then be contradicting its own checkbox. Mixed has to LOOK like mixed.
 *
 * What a click means is decided from the MEMBERS, never from the box's own post-click
 * state: a click on an indeterminate box lands on a different `checked` value in different
 * engines, and "some are on, so turn them all on" is the rule regardless. The box is not
 * the state; it is a picture of the rows, redrawn from them on every render.
 */
function sectionBox(section, rows) {
  var on = rows.filter(rowOn).length;
  var box = L.DomUtil.create("input", "layer-section-box");
  box.type = "checkbox";
  box._section = section.key;
  box._part = "box";
  box.checked = on === rows.length;
  box.indeterminate = on > 0 && on < rows.length;
  box.title =
    (on === rows.length ? "hide" : "show") + " all " + rows.length + " " + section.title;
  box.setAttribute("aria-label", section.title + ", all " + rows.length);
  L.DomEvent.on(box, "click", function (event) {
    // stopPropagation, not stop(): preventDefault would cancel the native tick, and the
    // native result already agrees with what setSection is about to do in all three cases.
    L.DomEvent.stopPropagation(event);
    setSection(rows, on !== rows.length);
  });
  return box;
}

/* A section head is two controls on one row, and keeping them apart IS the grammar: the
 * BOX toggles the family, the caret and title fold it. One click can only ever do one of
 * them -- which is why the fold listener sits on the text span rather than on the row, as
 * it used to. A fold handler on the row would also fire for a click on the box, so ticking
 * "pickups" would fold the section shut under the pointer in the same gesture. */
function sectionHead(section, rows) {
  var head = L.DomUtil.create("div", "layer-section");
  head.appendChild(sectionBox(section, rows));
  var text = L.DomUtil.create("span", "layer-fold", head);
  text._section = section.key;
  text._part = "fold";
  var open = state.panel.sections[section.key];
  foldHead(text, open, section.title, rows.filter(rowOn).length, rows.length);
  onActivate(text, function () {
    state.panel.sections[section.key] = !state.panel.sections[section.key];
    decorateControl();
  });
  return head;
}

/* The top head stays fold-only: it gets no master checkbox, on purpose.
 *
 * A family box is undoable -- untick "pickups", tick it again, and the ten rows are back
 * where they were, because they were all on or all off either way. A master box is not:
 * this control's rows are deliberately NOT uniform (regions on, machines off, nine of ten
 * pickup families off), and one click that unticked all 34 would throw that selection away.
 * Re-ticking would not restore it -- it would turn all 34 ON, which is a different map than
 * the one the player had. So the one gesture whose undo does not undo is the one gesture
 * this head does not offer. */
function panelHead(rows) {
  var container = control.getContainer();
  var head = container.querySelector(".layers-head");
  if (!head) {
    head = L.DomUtil.create("div", "layers-head");
    onActivate(head, function () {
      state.panel.open = !state.panel.open;
      decorateControl();
    });
    // First child, ahead of Leaflet's own (permanently hidden) toggle anchor: the head is
    // what stays on screen when the list folds, so it has to be the top of the box.
    container.insertBefore(head, container.firstChild);
  }
  foldHead(head, state.panel.open, "layers", rows.filter(rowOn).length, rows.length);
  fold(head, false);
  if (state.panel.open) L.DomUtil.removeClass(head, "shut");
  else L.DomUtil.addClass(head, "shut");
  return head;
}

/* Which half of which section head holds the keyboard, as a value that can outlive the
 * element holding it.
 *
 * Reading `document.activeElement` inside the decorator is enough when the decorator is
 * the one doing the removing -- a fold click goes that way. It is NOT enough on the path a
 * family box takes: Leaflet's `_update` empties the whole overlays list first, the section
 * heads live inside that list, and so by the time the decorator runs the focused box is
 * already gone and activeElement is <body>. Every family toggle would drop the keyboard on
 * the floor. The mark is therefore taken BEFORE the wipe and parked here. */
function focusMark() {
  var active = document.activeElement;
  return active && active._section ? { key: active._section, part: active._part } : null;
}

var pendingFocus = null;

/* Re-applied after every render of the list, and idempotent: Leaflet empties the overlay
 * list on each `_update`, so the section heads are rebuilt rather than moved. */
function decorateControl() {
  if (batching) return; // one render at the end of the batch, not one per member layer
  var container = control.getContainer();
  if (!container) return;
  var list = container.querySelector(".leaflet-control-layers-overlays");
  if (!list) return;
  // A section head is replaced, not updated, so keyboard focus would land on a removed
  // node and the NEXT Enter would go to the document. Restored below -- and which HALF of
  // the head held it matters now that a head is a box plus a fold.
  var focused = focusMark() || pendingFocus;
  pendingFocus = null;
  Array.prototype.slice.call(list.querySelectorAll(".layer-section")).forEach(function (head) {
    head.parentNode.removeChild(head);
  });
  var rows = Array.prototype.slice.call(list.querySelectorAll("label"));
  var grouped = {};
  rows.forEach(function (row) {
    fold(row, false);
    var section = sectionFor(rowName(row));
    if (section) (grouped[section.key] = grouped[section.key] || []).push(row);
  });
  SECTIONS.forEach(function (section) {
    var members = grouped[section.key];
    if (!members || !members.length) return;
    var open = state.panel.sections[section.key];
    members.forEach(function (row) {
      fold(row, !open);
    });
    var head = sectionHead(section, members);
    list.insertBefore(head, members[0]);
    if (focused && focused.key === section.key) {
      var again = head.querySelector(focused.part === "box" ? ".layer-section-box" : ".layer-fold");
      if (again) again.focus();
    }
  });
  panelHead(rows);
  fold(container.querySelector(".leaflet-control-layers-list"), !state.panel.open);
}

(function () {
  var update = control._update;
  control._update = function () {
    pendingFocus = focusMark() || pendingFocus; // before the wipe; see focusMark
    var result = update.apply(this, arguments);
    decorateControl();
    return result;
  };
  // A checkbox click does not re-render the list, so the "n of m" counts would go stale
  // the moment anyone used the thing they are counting.
  map.on("overlayadd overlayremove", decorateControl);
  decorateControl();
})();

/* Flying to a factory label is one click; getting back out was zoom-out spam. One
 * house-shaped button under the zoom control reframes the whole world. */
(function () {
  var home = L.control({ position: "topleft" });
  home.onAdd = function () {
    var bar = L.DomUtil.create("div", "leaflet-bar");
    var a = L.DomUtil.create("a", "", bar);
    a.href = "#";
    a.innerHTML = "&#8962;";
    a.title = "whole world";
    a.setAttribute("role", "button");
    L.DomEvent.on(a, "click", function (event) {
      L.DomEvent.preventDefault(event);
      map.setView(HOME_VIEW.centre, HOME_VIEW.zoom);
    });
    return bar;
  };
  home.addTo(map);
})();

/* A named layer that can be replaced wholesale on refetch without the checkbox
 * forgetting whether it was ticked -- that is why the LayerGroup identity is kept and
 * only its contents are cleared. `colour` puts a swatch in the control row, which is
 * what makes the control readable as a legend: "node: Coal" next to its actual grey. */
function layer(name, on, colour) {
  if (!state.layers[name]) {
    var group = L.layerGroup();
    group._rank = layerRank(name);
    state.layers[name] = group;
    // Keyed by the same stamp Leaflet writes onto the row's checkbox, so decorateControl
    // can read a row's name back without parsing the swatch markup out of its text.
    state.layerName[L.Util.stamp(group)] = name;
    var title = colour
      ? '<i class="swatch" style="background:' + colour + '"></i>' + esc(name)
      : esc(name);
    control.addOverlay(group, title);
    if (on) group.addTo(map);
  }
  return state.layers[name].clearLayers();
}

/* Layers whose names are data-driven (one per resource, one per pickup category) can go
 * stale on a world switch: a category the new world does not return would otherwise keep
 * the previous world's markers under a still-ticked checkbox. */
function clearPrefixed(prefixes) {
  Object.keys(state.layers).forEach(function (name) {
    prefixes.forEach(function (prefix) {
      if (name.indexOf(prefix) === 0) state.layers[name].clearLayers();
    });
  });
}

/* ------------------------------------------------------------------ drawing */

var REGION_FILL = 1; // see REGION_COLOUR: opaque cells, or the shared borders become a grid.

/* How much of the map render shows through the region fill when BOTH are drawn.
 *
 * The two used to be alternatives -- a render arriving unticked the region box (see
 * baseImageryShown) and that was the end of it. It still does, because a photograph of the
 * world is the better picture of the world; but ticking the box back on is now a legitimate
 * thing to want -- "which biome is this?" answered OVER the artwork rather than instead of
 * it -- and at REGION_FILL that answer is a wall of flat colour hiding what it annotates.
 *
 * The alpha goes on the PANE, not on the cells, and that distinction is the whole fix. A
 * per-cell fillOpacity of 0.45 blends 768 rectangles against the picture ONE AT A TIME, and
 * every shared border -- where a cell's antialiased edge and its neighbour's overlap -- is
 * blended twice. That is a visible 256 m grid, which is exactly why the cells are opaque and
 * their strokes are their own fill colour. Painted opaque into the pane's own canvas they
 * still merge into one shape; the browser then composites that finished shape over the tiles
 * once, at this alpha, and a border is no different from a cell interior.
 *
 * 0.45 by eye over both extremes of the render -- the pale sand of the Dune Desert, where a
 * heavier fill turns the dunes to mud, and the near-black canopy of the Northern Forest,
 * where a lighter one leaves the region tint invisible. Region NAMES are unaffected: they
 * are tooltips, and tooltips live in Leaflet's tooltipPane. */
var REGION_BLEND = 0.45;

/* Whether a map render is actually on screen right now: the layer exists, has something in
 * it, and its box is ticked. All three matter -- the group is created empty by the probe
 * and the box is the player's to untick. */
function baseImageryOn() {
  var group = state.layers["map image"];
  return !!(group && map.hasLayer(group) && group.getLayers().length);
}

/* Applied on every layer change, because every path into "both are drawn" is one: the
 * render finishing, the render failing back, and either box being ticked by hand.
 *
 * Guarded against its own no-ops rather than debounced. Drawing a world adds thousands of
 * layers to the map, each of which fires this, and the guard turns all but the two that
 * change anything into three property reads. */
var regionBlend = "";

function updateRegionBlend() {
  var pane = map.getPane("regions");
  if (!pane) return;
  var want = baseImageryOn() ? String(REGION_BLEND) : "";
  if (want === regionBlend) return;
  regionBlend = want;
  pane.style.opacity = want;
}

map.on("layeradd layerremove", updateRegionBlend);

/* The base map: one flat rectangle per 256 m raster cell, plus a name per region.
 *
 * Orientation is the whole trap here and the API's docstring spells it out: grid row 0 is
 * the NORTH edge because y0_m is the smallest y and game +Y is south. Cell (i, j) spans
 * y in [y, y+cell], which is latitude [-(y+cell), -y] once the page's [-y, x] convention
 * is applied -- so the y bounds swap, and only here. Void cells are left unpainted: the
 * sea colour showing through them is the coastline.
 *
 * Names print at `label_m`, not the centroid: a concave region's centroid can sit on a
 * neighbour's ground, and a name printed there contradicts the same page's right-click
 * inspector. The server moves those anchors onto the region's own cells.
 */
function drawRegions(data) {
  // "regions" and "region names": one row for the fill, one for the labels over it, named
  // as the pair they are. The fill was called "terrain" until there was a real terrain
  // render to be confused with -- what it draws is biome regions, and always was.
  var regions = layer("regions", true);
  var names = layer("region names", true);
  var cell = data.cell_m;
  data.grid.forEach(function (row, j) {
    for (var i = 0; i < row.length; i++) {
      var letter = row.charAt(i);
      if (letter === ".") continue;
      var colour = REGION_COLOUR[letter] || "#3f4640";
      var x = data.x0_m + i * cell;
      var y = data.y0_m + j * cell;
      L.rectangle(
        [
          [-(y + cell), x],
          [-y, x + cell],
        ],
        {
          // Stroked in its own fill colour so neighbouring cells of one biome merge into
          // a shape instead of showing a grid; interactive:false so the region fill never
          // eats a click meant for a node sitting on top of it.
          color: colour,
          weight: 1,
          opacity: REGION_FILL,
          fillColor: colour,
          fillOpacity: REGION_FILL,
          interactive: false,
          pane: "regions",
        }
      ).addTo(regions);
    }
  });

  Object.keys(data.regions).forEach(function (name) {
    // A standalone tooltip, not a zero-opacity marker: a marker would drag Leaflet's
    // default icon (and its two image requests) into the page for a label that is meant
    // to be text and nothing else.
    var at = data.regions[name].label_m || data.regions[name].centroid_m;
    L.tooltip({ permanent: true, direction: "center", className: "region-label" })
      .setLatLng([-at[1], at[0]])
      .setContent(esc(name))
      .addTo(names);
  });
}

/* The four corners of a footprint placed at (x, y) and turned by `yaw`, as latlngs.
 *
 * `w` and `l` are HALF-extents along the building's own X and Y, so a rotated Manufacturer
 * stays 18 x 20 m instead of growing into the bounding box of its turned self -- which is
 * exactly what an L.rectangle around a rotated thing would have drawn.
 *
 * The API sends yaw in degrees about world Z, positive turning +X towards +Y, so a local
 * offset (dx, dy) lands at (x + dx*cos - dy*sin, y + dx*sin + dy*cos). The page's one
 * coordinate rule then turns each corner into [-y, x], in the same single place it always
 * was: nothing here knows about latitude except the last line.
 *
 * A null yaw is not a zero yaw. Null means the projection predates schema 12 and this
 * placement's facing was never recorded; zero means it was recorded and points east. Both
 * come out of here as the same axis-aligned box -- cos 0 = 1, sin 0 = 0 reproduces exactly
 * the four corners the L.rectangle here used to build -- so a world with no yaw at all
 * draws precisely as it did before, and the difference between the two claims stays in the
 * popup where it can be read rather than in the drawing where it cannot.
 */
function footprintCorners(x, y, w, l, yaw) {
  var a = ((yaw || 0) * Math.PI) / 180;
  var cos = Math.cos(a);
  var sin = Math.sin(a);
  return [
    [-w, -l],
    [w, -l],
    [w, l],
    [-w, l],
  ].map(function (d) {
    return [-(y + d[0] * sin + d[1] * cos), x + d[0] * cos - d[1] * sin];
  });
}

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
var STRUCTURE_COLOUR = "#3a4148"; // concrete, cool enough to read as built against the biomes.

function drawStructures(data) {
  var group = layer("foundations", true, STRUCTURE_COLOUR);
  var half = (data.tile_m || 8) / 2;
  data.structures.forEach(function (s) {
    if (s.x_m === null || s.y_m === null) return;
    L.polygon(footprintCorners(s.x_m, s.y_m, half, half, s.yaw), {
      color: STRUCTURE_COLOUR,
      weight: 1,
      opacity: 0.9,
      fillColor: STRUCTURE_COLOUR,
      fillOpacity: 0.9,
      interactive: false,
      pane: "foundations",
    }).addTo(group);
  });
}

/* The conveyor network, drawn as the routes it actually takes.
 *
 * New with schema 12, which is the whole reason this layer did not exist: the splines were
 * decoded by the parser and thrown away at the projection, so the map could show the
 * concrete and the machines and nothing about what joined them.
 *
 * ONE colour family for the whole network, and tier as a VALUE step inside it. There is a
 * real base map underneath now, and five hues of belt over a photographic terrain would be
 * the loudest thing on the page for information a player reads off a popup anyway. The step
 * is deliberately small -- three tones, twenty points of each channel apart -- so a trunk
 * line reads as heavier than a feeder without either of them shouting.
 *
 * Tier used to be a WIDTH step, and it could not stay one once width became physical: in
 * game every belt is the same two metres across, Mk1 to Mk5, so a map that drew a Mk5 wider
 * would be contradicting the same map's machines, which are drawn at their measured
 * footprint. Value is the quietest channel that was still free.
 *
 * A LIFT gets a ring instead of a line, because a line is not available: a lift is exactly
 * vertical (measured, server-side -- all 302 on this world have zero horizontal extent),
 * so its top-down polyline is one point and a polyline through it draws nothing at all.
 * The ring is also the right picture: seen from above, a lift is a hole in the floor.
 *
 * A SPLITTER or MERGER gets a square, in this layer and in no other. It is a piece of the
 * belt network -- no recipe, no power, meaningless without the runs either side of it -- so
 * it is drawn by the layer that draws them, which is also what keeps it from being drawn
 * twice: it is in no other payload, and the machines layer has never had it. Without them a
 * belt-only view had a four-metre hole at every one of the world's 848 junctions, and a run
 * that visibly stopped and started again is a run a reader has to guess is one run.
 */
/* Mid steel, and mid on purpose: this is the one layer with no ground of its own, so it has
 * to read over the dark concrete it mostly runs on AND over pale sand where it crosses
 * country. A near-white line does the first and vanishes into the second -- and at 3,085
 * routes it was also, briefly, the loudest thing on a page that now has real terrain under
 * it. A mid tone is darker than the sand and lighter than the concrete, which is the only
 * value that has contrast both ways without being bright. */
var BELT_COLOUR = "#93a5b4";
var LIFT_FILL = "#252a30"; // the hole the ring is drawn around.

/* The three tier tones, one step of value either side of BELT_COLOUR -- which stays the
 * middle one, so the swatch in the layer control is still the network's own colour. */
var BELT_SLOW = "#7f8f9d";
var BELT_FAST = "#a7b9c7";

/* Tier as value. `items_per_min` is the dump's own figure for the class -- 60, 120, 270,
 * 480, 780 -- so this is a banding of a measurement rather than a parse of "Mk3" out of a
 * display name, and it is the same banding the width step used to make. An unknown tier
 * draws at the middle tone: the darkest would read as Mk1. */
function beltColour(items_per_min) {
  if (!items_per_min) return BELT_COLOUR;
  if (items_per_min >= 480) return BELT_FAST;
  if (items_per_min >= 270) return BELT_COLOUR;
  return BELT_SLOW;
}

/* How wide a belt is, in metres, and the floor that keeps one visible.
 *
 * Two metres is the game's own belt width, and it is a CONSTANT here rather than a field
 * because the save does not carry one: a spline is a centre line, and every tier rides the
 * same two-metre frame. Stating it makes the width a claim a reader can check against the
 * scale bar, which is what "1.8 px at every zoom" never was.
 *
 * The floor is where honesty stops paying. At the whole-world zoom a metre is 0.14 px, so a
 * true-size belt would be a quarter-pixel of nothing; 1.5 px is a hairline that still draws.
 * Above about zoom -2 the physical width wins and the floor is never reached, which is the
 * whole point -- the floor is a lower bound on visibility, not a second styling rule.
 *
 * The floor is ROUTE_MIN_PX and not BELT_MIN_PX because it is not a statement about belts:
 * it is the width below which a stroked line stops being drawn at all, and the pipes next
 * door -- narrower in the world, so floored a zoom step sooner -- reach it for the same
 * reason. One hairline for both, or the two networks fade out at different zooms and the
 * page invents a difference the world does not have. */
var BELT_WIDTH_M = 2;
var ROUTE_MIN_PX = 1.5;

/* A lift is the same two metres seen end-on, so its ring is that circle: radius half the
 * belt width, floored a little higher than the line is, because a ring has to enclose
 * something to read as a ring rather than as a dot. */
var LIFT_MIN_RADIUS_PX = 2;

/* A splitter or merger with no measured footprint. The docs dump carries clearance for none
 * of these four classes, so the server sends null and this is the page's own stand-in --
 * the same arrangement, and the same reason, as MACHINE_FALLBACK_M below: a number invented
 * server-side would arrive indistinguishable from a measurement. Four metres is the square
 * the pieces snap to, which is also what makes a run read as continuous through one. */
var ATTACHMENT_FALLBACK_M = 4;

/* A route's stroke width, in pixels, from its width in the world. The one place the two
 * network layers agree completely: a belt is two metres and a pipe is 1.3, and past the
 * floor each is drawn at whatever that is worth on screen right now. */
function routeWeight(width_m, ppm) {
  return Math.max(ROUTE_MIN_PX, width_m * ppm);
}

function beltWeight(ppm) {
  return routeWeight(BELT_WIDTH_M, ppm);
}

function liftRadius(ppm) {
  return Math.max(LIFT_MIN_RADIUS_PX, (BELT_WIDTH_M / 2) * ppm);
}

function beltPopup(b, first, last) {
  return popup([
    ["belt", b.name || b.cls],
    // Said out loud, because the glyph is the one encoding on this map that exists
    // because of a measurement rather than because of a preference.
    ["kind", b.lift ? "conveyor lift — vertical, so drawn as a ring" : null],
    ["rate", b.items_per_min ? b.items_per_min + " items/min at 100%" : null],
    // Travel order, input to output: the projection reverses the save's own output-first
    // storage, so these two rows mean what they say.
    ["from", first[0] + ", " + first[1] + " m"],
    ["to", last[0] + ", " + last[1] + " m"],
    ["rise", Math.round((last[2] - first[2]) * 10) / 10 + " m"],
    ["chain", "#" + b.chain],
  ]);
}

function drawBelts(data) {
  // Off by default at the whole-world zoom, exactly like `machines` and for the same
  // reason: 3,085 routes across 7 km is a smear. See reveal().
  var group = layer("belts", false, BELT_COLOUR);
  var ppm = pixelsPerMetre();
  data.belts.forEach(function (b) {
    var points = b.points_m.map(function (p) {
      return [-p[1], p[0]];
    });
    var first = b.points_m[0];
    var last = b.points_m[b.points_m.length - 1];
    var piece;
    if (b.lift) {
      piece = L.circleMarker(points[0], {
        radius: liftRadius(ppm),
        color: beltColour(b.items_per_min),
        weight: 1.5,
        fillColor: LIFT_FILL,
        fillOpacity: 0.9,
      });
    } else if (points.length < 2) {
      return; // a route with one point is not a route, and this is not a lift
    } else {
      piece = L.polyline(points, {
        color: beltColour(b.items_per_min),
        weight: beltWeight(ppm),
        opacity: 0.85,
      });
    }
    piece.bindPopup(beltPopup(b, first, last)).addTo(group);
  });
  (data.attachments || []).forEach(function (a) {
    if (a.x_m === null || a.y_m === null) return;
    var w = (a.w_m || ATTACHMENT_FALLBACK_M) / 2;
    var l = (a.l_m || ATTACHMENT_FALLBACK_M) / 2;
    L.polygon(footprintCorners(a.x_m, a.y_m, w, l, a.yaw), {
      color: BELT_COLOUR,
      weight: 1,
      fillColor: BELT_COLOUR,
      fillOpacity: 0.85,
    })
      .bindPopup(
        popup([
          ["belt part", a.name || a.cls],
          ["facing", a.yaw === null || a.yaw === undefined ? null : Math.round(a.yaw) + "°"],
          ["at", a.x_m + ", " + a.y_m + " m"],
          ["instance", code(a.instance_leaf)],
        ])
      )
      .addTo(group);
  });
  sinkRoutes();
}

/* The pixels half of both route layers, re-derived whenever the scale changes.
 *
 * A polyline's weight and a circleMarker's radius are the two sizes on this page that are
 * given in screen pixels, so they are the two that do NOT follow the map on their own -- a
 * belt drawn 1.8 px wide was 1.8 px wide at 7 km across and 1.8 px wide standing inside a
 * factory, which is why it read as a thread beside machines drawn at their true footprint.
 * Everything else here is a polygon in map units and needs none of this.
 *
 * Restyling on zoomend, rather than drawing the routes as thin polygons in map units:
 *
 *   * a polygon cannot have a floor. Below zoom -2 a true two-metre belt is a fraction of a
 *     pixel, and the floor is what keeps the network visible at the world view at all --
 *     it is a pixel statement, so it needs a pixel size to make it in;
 *   * a polygon would also change what a click means. A belt is hit-tested as a line plus
 *     Leaflet's tolerance today, and a 3,085-piece layer of two-metre ribbons would be
 *     unclickable at exactly the zooms where the popup is worth opening;
 *   * and it costs nothing to keep. Measured over the whole layer at factory zoom -- 3,933
 *     pieces, splitters included -- 0.6 to 0.7 ms median for the pass and 3 ms at its
 *     worst, once per zoom step, against a 16.7 ms frame. Two runs of 300 frames sampled
 *     across twelve zoom steps sat at a 16.7 ms median and a 16.8 ms p95; one of the two
 *     dropped a single frame (33 ms), which is one canvas redraw and not a stutter.
 *
 * There is no pop to debounce away, either -- the opposite. Leaflet scales the whole canvas
 * as one image during a zoom animation, so a belt already grows with the map mid-flight and
 * used to SNAP BACK to its fixed pixel width when the canvas was redrawn at the end. Landing
 * on the width the animation was already showing is what removes that snap. */
function styleRoutes() {
  var ppm = pixelsPerMetre();
  var radius = liftRadius(ppm);
  var alpha = chevronOpacity(ppm);
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var weight = routeWeight(ROUTE_WIDTH_M[name], ppm);
    group.eachLayer(function (piece) {
      // Four kinds of piece share these layers now and only two of them are sized in pixels:
      // a lift's ring by its radius, a run by its weight. A splitter is a polygon in map
      // units and is already the right size at every zoom -- exactly like a machine, which is
      // the whole reason it is drawn as one. A chevron is map units too, so what changes for
      // it is not its size but whether it is drawn at all: it is the one piece here that has
      // a zoom BELOW which it is noise rather than information.
      if (piece._chevron) piece.setStyle({ opacity: alpha });
      else if (piece.setRadius) piece.setRadius(radius);
      else if (!(piece instanceof L.Polygon)) piece.setStyle({ weight: weight });
    });
  });
}

map.on("zoomend", styleRoutes);

/* The fluid network, drawn the same way and told apart by colour.
 *
 * New with schema 13, and the belts' other half: `mSplineData` sat on every pipeline actor
 * the whole time and the projection dropped it, so the map could show a base's belts and
 * nothing about its plumbing.
 *
 * The same posture as the belts next door, deliberately -- one colour family for the whole
 * network, tier as a VALUE step inside it, width physical, no second palette -- because a
 * page that encodes two networks by two different grammars makes the reader learn twice.
 *
 * NO GLYPH, and that is a measurement rather than an omission. A conveyor lift gets a ring
 * because its top-down polyline is a single point; the server measured the same question for
 * pipes and the answer is that there is no such piece -- the tightest of this world's 503
 * spans 11.6 cm horizontally and only one is under 50 cm. Every pipe draws as a line. The
 * one-point guard below is still there, because "none on this world" is not "none ever".
 *
 * ARROWS ON 365 OF THE 503, and a stated refusal on the rest. The save still does not record
 * a pipe's flow direction -- that never changes -- but it does record the whole plumbing
 * graph and it TYPES a machine's ports, so `/api/pipes` now infers a direction where the
 * network admits only one and says `unknown` where it admits two. A pipe hanging off a water
 * extractor had an obvious answer and printing "not recorded" at it read as obtuseness rather
 * than as honesty. So: chevrons on the resolved ones, nothing at all on the rest, and a popup
 * that names which of the two a reader is looking at.
 */
/* Rust, and picked by measuring rather than by taste. This layer has to separate from three
 * things at once: the belts it runs beside, the terrain it crosses, and the amber the
 * extractors are already drawn in -- and the last is the trap, because a water extractor is
 * where pipes and extractors physically meet. Measured in CIE Lab against the base map under
 * all 503 routes and against every colour already on the page: #a8613c is dE 34 from the
 * terrain and 22 from its nearest neighbour on the page (the copper-ore dot), where the
 * saturated amber a pipe suggests first, #d99a3e, is dE 4.5 from the extractors and would
 * have been indistinguishable from them. Warm where the belts are cool, and dark enough not
 * to shout over a photographic map. */
var PIPE_COLOUR = "#a8613c";

/* The two tier tones, one step of value either side of PIPE_COLOUR -- which stays the middle
 * one, so the swatch in the layer control is still the network's own colour. The step is the
 * belts' step, twenty points of each channel, and it lands where the belts' does: dE 15.7
 * between Mk1 and Mk2 against the belts' 15.6 between their slowest and fastest.
 *
 * Re-measured rather than assumed safe, because PIPE_COLOUR was itself chosen by measurement
 * and a ramp can walk a colour into a neighbour. In CIE Lab both tones stay clear of every
 * other colour on the page -- nearest is the generator red at dE 28.9 and 28.3, where the
 * base sits at 27.6, so the ramp moves away from it rather than toward -- and the extractor
 * amber that ruled out a brighter pipe stays 40.9 and 32.4 away, against the dE 4.5 that
 * disqualified #d99a3e. */
var PIPE_MK1 = "#944d28";
var PIPE_MK2 = "#bc7550";

/* Tier as value, the same banding the belts use and for the same reason -- and for one more.
 * `flow_m3_min` is the dump's own figure for the class -- 300 on Mk1, 600 on Mk2 -- so this
 * is a banding of a measurement rather than a parse of "MK2" out of a display name.
 *
 * It could not have stayed a width step once the belts' became physical. A Pipeline Mk.2 is
 * not a wider pipe: it is the same 1.3 m bore with a better pump rating, and it snaps to the
 * same holes in the same supports. A map that drew it wider would be contradicting its own
 * scale bar, and contradicting the belts beside it, which gave up exactly this encoding for
 * exactly this reason. Two tiers, so two tones and the middle for the unknown: the darker
 * would read as Mk1. */
function pipeColour(flow_m3_min) {
  if (!flow_m3_min) return PIPE_COLOUR;
  return flow_m3_min >= 600 ? PIPE_MK2 : PIPE_MK1;
}

/* A pipe is 1.3 m across, and like the belt's two metres it is a CONSTANT rather than a
 * field: the save carries a centre line and no bore, and both tiers ride the same frame.
 * Narrower than a belt, so it reaches ROUTE_MIN_PX about a zoom step sooner -- which is the
 * true picture, a pipe IS thinner than a belt, and the floor is what keeps it from being
 * nothing at all at the whole-world view. */
var PIPE_WIDTH_M = 1.3;

function pipeWeight(ppm) {
  return routeWeight(PIPE_WIDTH_M, ppm);
}

/* What the popup says about a direction, keyed by what the server based it on. Short on
 * purpose: the row it replaces was a sentence explaining why there was nothing to say, and
 * the whole point of the change is that on 365 of 503 pipes there now is. The `from` and
 * `to` rows carry the direction itself, exactly as they do on a belt, so this row only has
 * to carry the WARRANT. */
var PIPE_FLOW_BASIS = {
  "machine port": "→ a typed machine port at one end",
  pump: "→ pump orientation",
  propagated: "→ inferred from the network",
};

/* And the honest refusal, kept for the 138 the network genuinely does not settle -- a pipe
 * in a loop, or a trunk with producers and consumers on both sides. Shorter than the
 * sentence it replaces, because it is now the exception rather than the rule. */
var PIPE_FLOW_UNKNOWN = "not recorded, and the network does not imply it";

function pipePopup(p, first, last) {
  var known = p.direction === "forward" || p.direction === "reverse";
  var head = p.direction === "reverse" ? last : first;
  var tail = p.direction === "reverse" ? first : last;
  return popup([
    ["pipe", p.name || p.cls],
    // The thing a belt cannot say. It comes off the game's own FGPipeNetwork rather than
    // from what the pipe is plugged into, which is why it can be stated flatly.
    ["fluid", p.fluid_name],
    ["capacity", p.flow_m3_min ? p.flow_m3_min + " m³/min at 100%" : null],
    ["flow", known ? PIPE_FLOW_BASIS[p.basis] || "→ inferred" : PIPE_FLOW_UNKNOWN],
    // `from`/`to` where the direction is known, which is the belt popup's own wording and
    // means the same thing there; `ends` where it is not, so the two are never confused.
    ["from", known ? head[0] + ", " + head[1] + " m" : null],
    ["to", known ? tail[0] + ", " + tail[1] + " m" : null],
    ["ends", known ? null : first[0] + ", " + first[1] + " m and " + last[0] + ", " + last[1] + " m"],
    // Signed once there is a direction to sign it against -- a pipe that climbs 6 m is a
    // different fact from one that drops 6 m, and until now neither could be said.
    [
      "rise",
      known
        ? Math.round((tail[2] - head[2]) * 10) / 10 + " m"
        : Math.abs(Math.round((last[2] - first[2]) * 10) / 10) + " m",
    ],
    ["network", p.network === null ? null : "#" + p.network],
  ]);
}

/* Chevrons: the direction, drawn.
 *
 * Geometry in METRES, like the machines and the foundations and unlike the line it sits on,
 * so it scales with the map for free and stays the same size relative to the plumbing at
 * every zoom. Three metres long and 2.4 across -- a little under twice the 1.3 m bore, which
 * is what makes it read as a mark ON the pipe rather than as a kink IN it.
 *
 * Placed by ARC LENGTH, not per corner: one every 24 m with a minimum of one per pipe, so a
 * 9 m elbow gets a single mark and the 56 m trunk gets two, and a run reads as a dotted line
 * of them rather than as a cluster at every bend. Counted in the PLAN, because that is what
 * the map shows: a pipe's climb is not length it has anywhere to put a mark. 400 chevrons
 * over 337 of this world's 365 resolved pipes; the 4 m floor drops the other 28, because a
 * mark three quarters as long as the piece carrying it is not a mark, it is the piece.
 *
 * HIDDEN AT WORLD ZOOM, and by the same grammar the hairline floor uses -- a pixel statement,
 * made in pixels. Below 5 px a chevron has no discernible apex; it is a dash, and a dash on a
 * line says nothing about direction at all. Three metres reaches 5 px at zoom 1, which is
 * FACTORY_MAX_ZOOM, i.e. exactly the scale the "show me this factory" flight lands at. At the
 * whole-world zoom -3 a metre is 0.14 px and 412 of these would be 412 specks of noise.
 *
 * Not interactive: a chevron sits on top of its own pipe, and a mark that stole the pipe's
 * popup would make the direction unreadable by making the piece unclickable. */
var CHEVRON_LENGTH_M = 3;
var CHEVRON_SPAN_M = 2.4;
var CHEVRON_SPACING_M = 24;
var CHEVRON_MIN_RUN_M = 4;
var CHEVRON_MIN_PX = 5;
var CHEVRON_WEIGHT_PX = 1.5;

/* A value step far above both pipe tones, so it reads against the line it is drawn on, and
 * measured like every other colour here. At its 0.7 opacity the composite over the three
 * pipe tones is dE 26.1 to 36.7 from the pipe underneath -- unmistakably a separate mark --
 * while the nearest colour anywhere else on the page is the iron-ore dot at dE 11.9 to 13.0,
 * which is a filled disc on terrain rather than a thin V on a line. The extractor amber that
 * ruled out a brighter pipe in the first place stays dE 45.5 away from the swatch. */
var CHEVRON_COLOUR = "#e8cbb4";
var CHEVRON_OPACITY = 0.7;

function chevronOpacity(ppm) {
  return CHEVRON_LENGTH_M * ppm >= CHEVRON_MIN_PX ? CHEVRON_OPACITY : 0;
}

/* Where the chevrons go on one route, in world metres, as [[x, y], [x, y], [x, y]] apexes.
 *
 * Takes a polyline and a flag rather than a pipe, so it knows nothing about plumbing: a belt
 * has a direction too -- its points are already in travel order -- and could be marked by
 * this same function tomorrow. See ROUTE_CHEVRONS for why it is not being marked today. */
function routeChevrons(points_m, reverse) {
  var pts = reverse ? points_m.slice().reverse() : points_m;
  var runs = [];
  var total = 0;
  for (var i = 1; i < pts.length; i++) {
    var dx = pts[i][0] - pts[i - 1][0];
    var dy = pts[i][1] - pts[i - 1][1];
    var d = Math.sqrt(dx * dx + dy * dy);
    if (!(d > 0)) continue;
    runs.push({ x: pts[i - 1][0], y: pts[i - 1][1], ux: dx / d, uy: dy / d, d: d, at: total });
    total += d;
  }
  if (!runs.length || total < CHEVRON_MIN_RUN_M) return [];
  var marks = [];
  var n = Math.max(1, Math.floor(total / CHEVRON_SPACING_M));
  for (var k = 0; k < n; k++) {
    var along = ((k + 0.5) / n) * total;
    var run = runs[runs.length - 1];
    for (var j = 0; j < runs.length; j++) {
      if (along <= runs[j].at + runs[j].d) {
        run = runs[j];
        break;
      }
    }
    var t = along - run.at;
    var tx = run.x + run.ux * (t + CHEVRON_LENGTH_M / 2);
    var ty = run.y + run.uy * (t + CHEVRON_LENGTH_M / 2);
    var bx = tx - run.ux * CHEVRON_LENGTH_M;
    var by = ty - run.uy * CHEVRON_LENGTH_M;
    var nx = -run.uy * (CHEVRON_SPAN_M / 2);
    var ny = run.ux * (CHEVRON_SPAN_M / 2);
    marks.push([
      [bx + nx, by + ny],
      [tx, ty],
      [bx - nx, by - ny],
    ]);
  }
  return marks;
}

function drawPipes(data) {
  // Off by default at the whole-world zoom, exactly like `belts` and `machines`. See reveal().
  var group = layer("pipes", false, PIPE_COLOUR);
  var ppm = pixelsPerMetre();
  var alpha = chevronOpacity(ppm);
  data.pipes.forEach(function (p) {
    if (p.points_m.length < 2) return; // a route with one point is not a route
    var points = p.points_m.map(function (q) {
      return [-q[1], q[0]];
    });
    L.polyline(points, {
      color: pipeColour(p.flow_m3_min),
      weight: pipeWeight(ppm),
      opacity: 0.85,
    })
      .bindPopup(pipePopup(p, p.points_m[0], p.points_m[p.points_m.length - 1]))
      .addTo(group);
    if (!ROUTE_CHEVRONS.pipes) return;
    if (p.direction !== "forward" && p.direction !== "reverse") return;
    routeChevrons(p.points_m, p.direction === "reverse").forEach(function (mark) {
      var piece = L.polyline(
        mark.map(function (q) {
          return [-q[1], q[0]];
        }),
        {
          color: CHEVRON_COLOUR,
          weight: CHEVRON_WEIGHT_PX,
          opacity: alpha,
          interactive: false,
        }
      );
      piece._chevron = true;
      piece.addTo(group);
    });
  });
  sinkRoutes();
}

/* Both route layers share the overlay canvas with the machines and the node dots, so the
 * rule raiseNodeDots exists for applies to them too: hit-testing is draw order and the LAST
 * match wins. A belt run crosses every machine it feeds and a pipe run crosses every
 * refinery it feeds, and a polyline's hit area is its width plus Leaflet's click tolerance
 * -- so a route layer added after the machines would quietly take the click on every machine
 * it passes over. Pushed to the back instead: under the machines, under the node dots, still
 * over the foundations (a separate pane, so a separate canvas, so unaffected either way).
 *
 * Their own pane would be the tidier answer and is not one: Leaflet gives every pane its own
 * canvas, the DOM delivers a click to the topmost element under the pointer, and the overlay
 * pane's canvas covers the entire viewport -- so a clickable layer below it is not clickable
 * at all. That is also why this is safe to call whenever: `bringToBack` is a no-op on a path
 * whose group is not on the map, which is the state both layers start in.
 *
 * One function over both, rather than one per layer: it is one rule, and two copies of it
 * would drift the moment a third route layer arrives. Same for styleRoutes above, and for
 * ROUTE_WIDTH_M, which is the only thing either function needs to tell the two apart.
 *
 * Order INSIDE a layer matters too, and it is decided by the order of the calls: each
 * `bringToBack` puts its caller below everything already sunk, so the piece sunk LAST ends
 * up at the very bottom. The junction squares therefore go first and the runs after them,
 * which leaves a splitter sitting on the lines it joins rather than under them -- a 4 m
 * square hidden beneath a 2 m line is still visible at its corners and is not CLICKABLE,
 * and the popup naming the piece is the whole reason it has one. The pipes layer holds no
 * polygons, so for it the partition is a no-op and the loop is the same loop.
 */
var ROUTE_LAYERS = ["belts", "pipes"];

/* What each route layer is worth in metres. The one place the two differ, so the one place
 * the shared passes above have to look. */
var ROUTE_WIDTH_M = { belts: BELT_WIDTH_M, pipes: PIPE_WIDTH_M };

/* Which route layers carry direction chevrons. A BELT HAS A DIRECTION TOO -- its points are
 * in travel order, which is the one thing the belts have always been able to say and the
 * pipes could not -- and `routeChevrons` above takes a polyline and a flag precisely so that
 * turning this to `true` is the whole of the work. It stays `false` because that is a
 * decision about the map, not about the data: 3,085 belt runs would put some 2,500 more
 * marks on the same canvas as these 412, and the owner asked for the pipes. A flag rather
 * than an absence, so the next person finds a switch instead of a rewrite. */
var ROUTE_CHEVRONS = { belts: false, pipes: true };

function sinkRoutes() {
  ROUTE_LAYERS.forEach(function (name) {
    var group = state.layers[name];
    if (!group) return;
    var chevrons = [];
    var squares = [];
    var runs = [];
    group.eachLayer(function (piece) {
      (piece._chevron ? chevrons : piece instanceof L.Polygon ? squares : runs).push(piece);
    });
    // Sunk FIRST is left highest, per the note above, so the chevrons go before the squares
    // and the runs: a direction mark under the line it marks would not be a mark.
    chevrons
      .concat(squares)
      .concat(runs)
      .forEach(function (piece) {
        if (piece.bringToBack) piece.bringToBack();
      });
  });
  raiseNodeDots();
}

// A layer added long after both fetches landed is appended to the canvas' draw list, i.e.
// on top of everything -- so the sink has to run again when the player ticks the box.
map.on("overlayadd", function (event) {
  if (ROUTE_LAYERS.some(function (n) { return state.layers[n] === event.layer; })) sinkRoutes();
});

/* Everything shares one canvas, so hit-testing is draw order: last drawn wins the click.
 * An extractor is drawn exactly on the node it drains, and whichever of /api/nodes and
 * /api/machines resolved last used to decide -- usually making all 44 occupied nodes
 * unclickable. The node dots are raised explicitly after either draw, so the dot (the
 * card with purity, region and the node: selector) always takes the click; the extractor
 * keeps the rest of its rectangle. */
function raiseNodeDots() {
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    state.layers[name].eachLayer(function (dot) {
      if (dot.bringToFront && dot._map) dot.bringToFront();
    });
  });
}

function drawNodes(data) {
  var byResource = {};
  data.nodes.forEach(function (n) {
    (byResource[n.resource] = byResource[n.resource] || []).push(n);
  });
  Object.keys(state.layers).forEach(function (name) {
    if (name.indexOf("node: ") !== 0) return;
    var still = Object.keys(byResource).some(function (resource) {
      return "node: " + shortResource(resource) === name;
    });
    if (!still) state.layers[name].clearLayers();
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      var short = shortResource(resource);
      var colour = RESOURCE_COLOUR[resource] || "#888";
      var group = layer("node: " + short, true, colour);
      byResource[resource].forEach(function (n) {
        L.circleMarker(xy(n), {
          radius: PURITY_RADIUS[n.purity] || 4,
          color: colour,
          weight: n.occupied ? 2 : 1,
          opacity: 1,
          fillOpacity: n.occupied ? 0.15 : 0.75,
        })
          .bindPopup(
            popup([
              ["node", short + " (" + n.purity + ")"],
              // Joined server-side: the raster and its orientation trap stay on one side.
              ["region", regionLine(n.region)],
              // Always present, because the absence of a row cannot be told apart from a
              // broken join -- and "no extractor known" is the join's own honest limit:
              // it resolves extractors targeting a node key, never proves a node free.
              [
                "occupancy",
                n.occupied
                  ? "occupied by " + (n.occupant_name || n.occupant_cls)
                  : data.save_error
                    ? "unknown — save could not be read"
                    : "no extractor known here",
              ],
              ["selector", code("node:" + n.name)],
              ["at", n.x_m + ", " + n.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
  raiseNodeDots();
  if (data.save_error) {
    fail("nodes: " + data.save_error + " — nodes drawn, occupancy unknown");
  }
}

/* Machines at their real size AND their real facing: `w_m`/`l_m` are the building's own
 * footprint, so a Manufacturer (18x20 m) reads as the eight-times-larger thing it is next
 * to a Constructor (8x10 m), and `yaw` turns that footprint the way the player placed it.
 * Null footprints for the classes the docs dump gives no clearance data -- both biomass
 * burners here -- and those fall back to the 6 m square every machine used to get, which
 * rotates to itself. A null yaw draws axis-aligned; see footprintCorners for why that is
 * not the same statement as a yaw of zero. */
var MACHINE_FALLBACK_M = 6;

function drawMachines(data) {
  ["machines", "extractors", "generators"].forEach(function (kind) {
    var group = layer(kind, kind !== "machines", KIND_COLOUR[kind]);
    data[kind].forEach(function (m) {
      if (m.x_m === null) return;
      var w = (m.w_m || MACHINE_FALLBACK_M) / 2;
      var l = (m.l_m || MACHINE_FALLBACK_M) / 2;
      L.polygon(footprintCorners(m.x_m, m.y_m, w, l, m.yaw), {
        color: KIND_COLOUR[kind],
        weight: 1,
        fillOpacity: m.paused ? 0.15 : 0.65,
        dashArray: m.paused ? "2,2" : null,
      })
        .bindPopup(
          popup([
            ["building", m.name],
            ["recipe", m.recipe_name || m.recipe],
            ["clock", m.clock === null ? null : Math.round(m.clock * 100) + "%"],
            ["paused", m.paused ? "yes" : null],
            ["footprint", m.w_m && m.l_m ? m.w_m + " x " + m.l_m + " m" : null],
            // Degrees about world Z, positive turning +X towards +Y -- the same number
            // the drawing is turned by, so a reader can check the picture against it.
            // Absent, not "0", when the projection carries no facing at all.
            ["facing", m.yaw === null || m.yaw === undefined ? null : Math.round(m.yaw) + "°"],
            ["at", m.x_m + ", " + m.y_m + " m"],
            ["instance", code(m.instance_leaf)],
          ])
        )
        .addTo(group);
    });
  });
  raiseNodeDots();
}

/* `machines`, `belts` and `pipes` are all off at the whole-world zoom on purpose: 438
 * rectangles and 3,588 routes across 7 km are a smear, and unticking them is the right
 * default. What
 * was wrong is what happened next -- clicking a factory label flew the map to that
 * factory's own extent and landed on bare concrete, with the reason eight unfolded rows
 * down a control the player had not opened.
 *
 * So the click that changes the SCALE turns those layers on, once, and says so. Not zoom:
 * a layer that ticked and unticked itself as the map moved would be the only control on
 * this page the player does not own, and the checkbox would be lying about who decided.
 * This is the same grammar as everything else here -- a ticked box, unticked by whoever
 * wants it unticked -- reached by the one gesture that means "show me this factory".
 *
 * One function for the whole set, rather than one per layer, so the grammar cannot drift:
 * a layer that is factory-scale information is off at world scale and arrives with the
 * flight. One NOTE for the whole set too -- two toasts for one click would read as two
 * events, and the player made one gesture. */
function reveal(names) {
  var turned = [];
  names.forEach(function (name) {
    var group = state.layers[name];
    if (!group || map.hasLayer(group)) return;
    group.addTo(map);
    turned.push(name);
  });
  if (!turned.length) return;
  note(
    // "a and b" for two, "a, b and c" for three -- an Oxford-less list rather than
    // "a and b and c", which is what a plain join gives once there are three of these.
    (turned.length > 1
      ? turned.slice(0, -1).join(", ") + " and " + turned[turned.length - 1]
      : turned[0]) +
      " turned on — untick " +
      (turned.length > 1 ? "those layers" : "the " + turned[0] + " layer") +
      " to hide them again"
  );
}

// What "show me this factory" means, in layers. A factory at factory scale is its machines
// and the routes between them -- belts AND pipes, because a refinery block is half plumbing
// and a view that showed only the belts would read as a factory with pieces missing. All
// three are unreadable at the zoom the click starts from.
var FACTORY_LAYERS = ["machines", "belts", "pipes"];

/* The player's last known position: the map's only you-are-here, and the reference every
 * "is this near me" judgement needs. Ring-styled so it reads as a position, not a node. */
function drawPlayer(p) {
  var group = layer("player", true, PLAYER_COLOUR);
  if (!p || p.x_m === null || p.y_m === null) return;
  L.circleMarker(xy(p), {
    radius: 7,
    color: PLAYER_COLOUR,
    weight: 2,
    fillColor: "#4aa3df",
    fillOpacity: 0.9,
  })
    .bindPopup(
      popup([
        ["player", "where you last stood (as of this save)"],
        ["at", p.x_m + ", " + p.y_m + " m"],
      ])
    )
    .addTo(group);
}

/* Factory labels, and the two things they used to get wrong.
 *
 * A permanent tooltip has to hang off SOMETHING, and that something used to be
 * `L.marker(pos, {opacity: 0})`. An invisible marker is still a marker: Leaflet builds it
 * from the default Icon, which requests `vendor/images/marker-icon.png` and
 * `marker-shadow.png` -- two files nobody ever vendored, so every page load logged two
 * 404s -- and it appends those <img> elements to the marker pane, at zIndex 600, above
 * the canvas everything clickable is drawn on. At opacity 0 they are invisible and still
 * 25x41 px of pointer target, so each of the 15 labels punched a hole in the map: a click
 * on a node under one hit the transparent image instead. A divIcon fetches no image and
 * is sized 0x0 here, which closes both holes with one change.
 *
 * The tooltip is then made `interactive`, which is what turns a label from decoration
 * into the map's index: click it and the map flies to the factory's own extent -- the
 * server's `bbox_m`, because the client is sent a machine COUNT and never the machines --
 * and opens the card. Zooming to a bounding box rather than to a fixed zoom at the
 * centroid is what makes one click work for both a 40 m outpost and a 600 m base.
 */

// Breathing room around a factory's extent, metres. A one-machine factory has a
// zero-size box, and flying to a zero-size box means flying to maxZoom on top of it.
var FACTORY_PAD_M = 40;

// Never closer than this when flying to a factory: a small cluster filling the screen
// loses the surroundings that say where it is.
var FACTORY_MAX_ZOOM = 1;

function anchorMarker(centroid_m) {
  // divIcon, not the default icon: no image request, and iconSize [0,0] means the anchor
  // occupies no pointer area at all. The tooltip is the whole visible and clickable body.
  return L.marker([-centroid_m[1], centroid_m[0]], {
    icon: L.divIcon({ className: "factory-anchor", iconSize: [0, 0] }),
  });
}

/* A server bbox_m ([x_min, y_min, x_max, y_max], game axes) as Leaflet bounds. The y ends
 * swap, exactly as they do for the biome cells, because latitude is -y. */
function factoryBounds(bbox_m) {
  if (!bbox_m) return null;
  return L.latLngBounds(
    [-(bbox_m[3] + FACTORY_PAD_M), bbox_m[0] - FACTORY_PAD_M],
    [-(bbox_m[1] - FACTORY_PAD_M), bbox_m[2] + FACTORY_PAD_M]
  );
}

function factoryAnchor(row, text, className, rows) {
  var marker = anchorMarker(row.centroid_m);
  marker._labelWeight = row.machines || 0; // declutter priority: big factories win
  marker.bindTooltip(esc(text), {
    permanent: true,
    direction: "center",
    interactive: true, // the point of the whole function: a label you can click
    className: className,
  });
  // autoPan off: the card would otherwise shove the map sideways mid-flight, and the
  // flight already puts the factory in view.
  marker.bindPopup(popup(rows), { autoPan: false });
  var bounds = factoryBounds(row.bbox_m);
  if (bounds) {
    marker.on("click", function () {
      // A factory at factory scale IS its machines and the routes between them; see reveal.
      reveal(FACTORY_LAYERS);
      map.flyToBounds(bounds, { maxZoom: FACTORY_MAX_ZOOM });
    });
  }
  return marker;
}

function drawFactories(data) {
  var named = layer("factory labels", true);
  data.labels.forEach(function (f) {
    factoryAnchor(f, f.name, "factory-label", [
      ["factory", f.name],
      ["machines", f.machines],
      ["notes", f.notes],
      ["at", f.centroid_m[0] + ", " + f.centroid_m[1] + " m"],
      ["selector", code("label:" + f.name)],
    ]).addTo(named);
  });
  var proposed = layer("proposals", false);
  data.proposals.forEach(function (p) {
    var title = "#" + p.index + " " + p.label;
    // No cohesion row: the clusterer does not compute the score yet (every proposal
    // reports 0.0), and a constant 0 reads as "this cluster scored zero".
    factoryAnchor(p, title + " (" + p.machines + ")", "factory-label proposal", [
      ["proposal", title],
      ["machines", p.machines],
      ["spread", p.spread_m + " m"],
      ["selector", code("proposal:" + p.index)],
    ]).addTo(proposed);
  });
  declutter();
}

/* Labels are the map's index, so a pile of them is a broken index: at the whole-world
 * zoom the base's labels overlap in dozens of pairs and whichever tooltip was added last
 * takes every click -- the player's largest factory used to open a 2-machine outpost.
 *
 * The rule: show every label that fits, hide what it covers. Named labels outrank
 * proposals, bigger factories outrank smaller, and the test is the labels' actual screen
 * rectangles, re-run whenever zoom or the ticked layers change. A hidden label reappears
 * the moment there is room, and every label that IS visible is clickable -- no
 * dead-looking clickables, no invisible click thieves.
 *
 * The rule is right; it used to be applied in silence. At the home view 8 of 15 labels
 * are display:none, and a player who NAMED a factory could not tell hidden from lost --
 * the map said nothing about the eight, and the player has no way to know the pass exists.
 * So every label that covered something wears a "+n" badge: the count of names folded
 * under it, drawn only when this pass actually hid that many at this view, and gone the
 * moment a zoom-in makes room. Clicking it steps the map toward the group it names, which
 * keeps the page's one rule about labels -- visible means clickable. */
function declutter() {
  var entries = [];
  ["factory labels", "proposals"].forEach(function (name, groupRank) {
    var group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (marker) {
      var tip = marker.getTooltip && marker.getTooltip();
      var node = tip && tip.getElement && tip.getElement();
      if (node) {
        entries.push({
          node: node,
          marker: marker,
          rank: groupRank,
          weight: marker._labelWeight || 0,
        });
      }
    });
  });
  entries.forEach(function (entry) {
    L.DomUtil.removeClass(entry.node, "label-hidden");
    var old = entry.node.querySelector(".label-more");
    if (old) old.parentNode.removeChild(old);
  });
  entries.sort(function (a, b) {
    return a.rank - b.rank || b.weight - a.weight;
  });
  var kept = [];
  entries.forEach(function (entry) {
    var r = entry.node.getBoundingClientRect();
    var covered = null;
    kept.forEach(function (k) {
      if (covered) return; // the highest-ranked cover owns the badge
      var b = k.rect;
      if (r.left < b.right && b.left < r.right && r.top < b.bottom && b.top < r.bottom) covered = k;
    });
    if (covered) {
      L.DomUtil.addClass(entry.node, "label-hidden");
      covered.hidden.push(entry);
    } else {
      kept.push({ rect: r, entry: entry, hidden: [] });
    }
  });
  kept.forEach(function (k) {
    if (k.hidden.length) badgeHidden(k.entry, k.hidden);
  });
}

/* One click on a badge is a STEP toward the group, not a teleport. Two labels 40 m apart
 * do not separate until zoom 3, and flying six levels in one go from the whole-world view
 * loses every landmark on the way; three levels always moves the map and stays legible.
 * If the group is still covered when the flight ends the badge is still there -- the
 * declutter pass reruns on zoomend -- so the step simply repeats. */
var LABEL_STEP_ZOOM = 3;

function badgeHidden(entry, hidden) {
  // Absolutely positioned, so it hangs off the label's corner without changing the
  // rectangle this same pass just measured -- a badge that grew the box would make the
  // next run hide a label because of the badge on the one before it.
  var badge = L.DomUtil.create("span", "label-more", entry.node);
  badge.textContent = "+" + hidden.length;
  badge.title =
    hidden.length === 1
      ? "1 more factory label is hidden under this one — click to zoom in"
      : hidden.length + " more factory labels are hidden here — click to zoom in";
  var points = [entry.marker.getLatLng()];
  hidden.forEach(function (other) {
    points.push(other.marker.getLatLng());
  });
  L.DomEvent.on(badge, "click", function (event) {
    // Without this the label's own click wins and flies to the covering factory's extent,
    // which is the one place the hidden names are guaranteed still to be hidden.
    L.DomEvent.stop(event);
    var bounds = L.latLngBounds(points);
    var fit = map.getBoundsZoom(bounds, false, L.point(80, 80));
    var zoom = Math.min(fit, map.getZoom() + LABEL_STEP_ZOOM);
    zoom = Math.min(Math.max(zoom, map.getZoom() + 1), map.getMaxZoom());
    map.flyTo(bounds.getCenter(), zoom);
  });
}

/* Same batch guard as the control decorator, and for a heavier reason: this pass measures
 * every label's screen rectangle, so running it once per member of a fourteen-layer family
 * is fourteen forced layouts to reach one answer. batch() runs it once at the end. */
map.on("zoomend overlayadd overlayremove", function () {
  if (!batching) declutter();
});

function drawCollectibles(data) {
  var byCategory = {};
  data.rows.forEach(function (r) {
    (byCategory[r.category] = byCategory[r.category] || []).push(r);
  });
  Object.keys(state.layers).forEach(function (name) {
    // A category this world has none of (all collected, or never present) must not keep
    // showing another world's markers under a still-ticked box.
    if (name.indexOf("pickup: ") === 0 && !byCategory[name.slice("pickup: ".length)]) {
      state.layers[name].clearLayers();
    }
  });
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      var colour = PICKUP_COLOUR[category] || PICKUP_FALLBACK;
      var group = layer("pickup: " + category, false, colour);
      byCategory[category].forEach(function (r) {
        var here = xy(r);
        var mark = r.collected
          ? L.polyline(
              [
                [
                  [here[0] - 4, here[1] - 4],
                  [here[0] + 4, here[1] + 4],
                ],
                [
                  [here[0] - 4, here[1] + 4],
                  [here[0] + 4, here[1] - 4],
                ],
              ],
              { color: "#6b7078", weight: 1 }
            )
          : L.circleMarker(here, { radius: 4, color: colour, weight: 1, fillOpacity: 0.7 });
        mark
          .bindPopup(
            popup([
              ["pickup", category],
              ["name", code(r.name)],
              ["state", r.collected ? "collected" : r.observed || "unknown"],
              ["at", r.x_m + ", " + r.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
}

/* --------------------------------------------------------- point inspector */

/* Right-click anywhere: "what is here?" answered where the question is asked.
 *
 * The map is drawn from a save and a node table, and until now it could show WHERE things
 * are and nothing about the ground they stand on. /api/inspect answers the three things a
 * site starts with -- the named region, the measured elevation, the nearest nodes -- and
 * all three come from the domain layer, so the only work below is turning a latlng back
 * into game coordinates and laying the answer out.
 *
 * The inverse of the page's one coordinate rule: a point plotted at [-y, x] reads back as
 * x = lng, y = -lat. Rounded to a decimetre because the popup prints the same numbers it
 * asked with, and a coordinate you cannot retype is not a copyable coordinate.
 */

function elevationRows(e) {
  // Unsurveyed ground gets one line, not three saying the same nothing. There is no
  // heightmap anywhere in this project's inputs, so "no samples" is a real answer.
  if (!e.ground_count && !e.built_count) {
    return [["elevation", "nothing known within " + e.radius_m + " m"]];
  }
  // Ground and built stay apart, exactly as the server sends them: a node rests on
  // terrain and a foundation is wherever the player put it, so one median labelled
  // "elevation" would be the platform's height on any developed site.
  var rows = [
    [
      "ground",
      e.ground_count
        ? e.ground_m + " m (median of " + e.ground_count + ", spread " + e.ground_spread_m + " m)"
        : "no ground samples within " + e.radius_m + " m",
    ],
  ];
  if (e.built_count) {
    rows.push(["built", e.built_m + " m (median of " + e.built_count + ")"]);
  }
  // A missing fill is printed as the REASON it is missing, never as 0: zero fill is a
  // real and different measurement, and a blank row reads as a bug in the map.
  rows.push(["fill", e.fill_m === null ? e.fill_note : e.fill_m + " m"]);
  return rows;
}

function inspectHtml(d) {
  var rows = [["region", regionLine(d.region)]].concat(elevationRows(d.elevation));
  d.nearest.forEach(function (n, i) {
    rows.push([
      i ? "" : "nearest",
      html(
        esc(shortResource(n.resource) + " " + n.purity) +
          " &middot; " +
          esc(n.distance_m + " m") +
          (n.occupied ? " (occupied)" : "")
      ),
    ]);
  });
  // Said out loud rather than left to be inferred: with no save there is no built
  // population and no occupancy, so every node above reads as free whether it is or not.
  if (d.save_error) rows.push(["save", d.save_error + " — nodes only, occupancy unknown"]);
  // The one row built to be copied into an MCP tool call, so the unit -- the same " m"
  // every other coordinate row on the map ends with -- must ride along.
  rows.push(["at", html("<code>" + esc(d.at.x_m + ", " + d.at.y_m) + "</code> m")]);
  return popup(rows);
}

map.on("contextmenu", function (e) {
  // One right-click can reach this twice -- Leaflet fires at the layer under the cursor
  // and the event propagates to the map -- so the DOM event carries a mark. Two fetches
  // and two popups for one click is the bug this one line removes.
  if (e.originalEvent) {
    if (e.originalEvent._inspected) return;
    e.originalEvent._inspected = true;
  }
  var x = Math.round(e.latlng.lng * 10) / 10;
  var y = Math.round(-e.latlng.lat * 10) / 10;
  // Opened before the fetch, so the click has a visible effect on a slow answer and the
  // popup lands exactly where the pointer was rather than where the map has drifted to.
  var card = L.popup({ maxWidth: 340 })
    .setLatLng(e.latlng)
    .setContent("inspecting " + x + ", " + y + " m&hellip;")
    .openOn(map);
  get("/api/inspect?x_m=" + x + "&y_m=" + y)
    .then(function (d) {
      if (map.hasLayer(card)) card.setContent(inspectHtml(d));
    })
    .catch(function (err) {
      if (map.hasLayer(card)) card.setContent(popup([["inspect failed", friendly(err)]]));
    });
});

/* ------------------------------------------------------------------ loading */

function loadRegions() {
  // Geography, not save state: no world parameter, fetched once, never refetched.
  return fetch("/api/regions")
    .then(function (r) {
      return r.json().then(function (body) {
        if (!r.ok || body.error) throw new Error(body.error || r.status + " /api/regions");
        return body;
      });
    })
    .then(drawRegions)
    .catch(function (e) {
      fail("regions: " + friendly(e));
    });
}

/* The corners a base-map probe answered with, as [x_min, y_min, x_max, y_max] metres. */
function mapImageBounds(response) {
  var raw = (response.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
  if (raw.length === 4 && raw.every(isFinite)) return raw;
  return [MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max];
}

/* Those corners as Leaflet bounds -- the [-y, x] flip, so the y ends swap. */
function mapImageLatLngBounds(b) {
  return L.latLngBounds([
    [-b[3], b[0]],
    [-b[1], b[2]],
  ]);
}

/* A real render beats the flat cell fill over it, so the fill steps aside -- by unticking
 * its box, so one click brings it back.
 *
 * Still an untick and not something cleverer, now that the two CAN be shown together: the
 * render is the better answer to "what is here" and a page that opened with a biome wash
 * over it would be hiding its own best picture. What changed is only what the click back on
 * gets you -- REGION_BLEND, rather than the wash. */
function baseImageryShown() {
  if (state.layers.regions) map.removeLayer(state.layers.regions);
  updateRegionBlend();
}

/* ...and back, if the render turns out not to draw. Full opacity comes back with it: with
 * no picture underneath there is nothing to see through to, and REGION_BLEND against the
 * page's sea colour would only wash the biomes out. */
function baseImageryFailed(group, message) {
  group.clearLayers();
  map.removeLayer(group);
  if (state.layers.regions) state.layers.regions.addTo(map);
  updateRegionBlend();
  fail(message);
}

/* The optional half of the base map, in the order of preference the server can answer.
 *
 *   1. the tile pyramid at data/local/tiles/, if gen_map_image.py cut one -- the same
 *      picture at one resolution per zoom, so a whole-world view costs the sixteen tiles
 *      of z2 rather than 16 MB of 8192x8192 that decodes to 268 MB of RGBA;
 *   2. the single data/local/map.png overlay, which is what the pyramid falls back to and
 *      what a render someone else made still gets;
 *   3. nothing, which is the shipped state and not an error.
 *
 * Nothing is shipped, so both probes' 204 is the ordinary answer. A file that EXISTS but
 * does not decode -- a truncated download, an error page saved as .png -- must not cost
 * the biome base map: the error event puts the region fill back and says what happened,
 * instead of leaving a silent sea-coloured page. */
function loadMapImage() {
  return fetch("/api/maptiles/0/0/0", { method: "HEAD" })
    .then(function (r) {
      if (r.status === 200 && addTilePyramid(r)) return;
      return loadMapImageOverlay();
    })
    .catch(function () {
      /* the probe failing means no picture, which is the default state anyway */
    });
}

/* A TileLayer that knows how many tiles its pyramid actually has.
 *
 * `bounds` alone does not, and the difference is a 404 on every page load. Leaflet culls
 * tiles by intersecting their bounds with the layer's, in floating-point coordinates --
 * and the sheet's east edge, unprojected as 8192 px over 1.0922666... px per metre, comes
 * back as 4252.999999999999. The column that starts exactly AT the edge therefore
 * "overlaps" the map by a rounding error, gets fetched, and 404s. The grid, on the other
 * hand, is 2^z tiles a side exactly, in integers, with nothing to round: a tile outside it
 * is asked for from the one transparent pixel Leaflet keeps for the purpose, so a pan and
 * zoom session's network log has no red in it at all. */
var PyramidLayer = L.TileLayer.extend({
  getTileUrl: function (coords) {
    var span = 1 << (coords.z + this.options.zoomOffset);
    if (coords.x < 0 || coords.y < 0 || coords.x >= span || coords.y >= span) {
      return L.Util.emptyImageUrl;
    }
    return L.TileLayer.prototype.getTileUrl.call(this, coords);
  },
});

/* The pyramid, wired to the pixel space CRS_SHEET_PX set up: Leaflet's tile level Z + 5
 * is the pyramid's z, because 256 * 2^(Z+5) is 8192 * 2^Z, and 8192 sheet pixels are one
 * screen pixel each at map zoom 0. So the level Leaflet asks for is the level whose pixels
 * match the view, which is the whole point of cutting a pyramid.
 *
 * Returns false -- fall back to the single overlay -- when the server describes a pyramid
 * this grid cannot draw: corners that are not the square the CRS is anchored on, or a tile
 * size that is not a power-of-two fraction of the sheet. Both are drawable as one image,
 * and a tile grid quietly offset from its own picture is worse than a big picture. */
function addTilePyramid(response) {
  var b = mapImageBounds(response);
  var anchored = [
    MAP_SQUARE_M.x_min,
    MAP_SQUARE_M.y_min,
    MAP_SQUARE_M.x_max,
    MAP_SQUARE_M.y_max,
  ];
  var moved = b.some(function (v, i) {
    return Math.abs(v - anchored[i]) > 1;
  });
  if (moved) return false;

  var tilePx = +response.headers.get("X-Map-Tile-Px") || 256;
  var maxZ = +response.headers.get("X-Map-Tile-Max-Z");
  if (!isFinite(maxZ) || maxZ < 0) maxZ = 5;
  var top = Math.log2(MAP_SHEET_PX / tilePx); // the pyramid z that IS the sheet: 5.
  if (!isFinite(top) || top !== Math.round(top)) return false;

  // The build tag makes every URL change when the pyramid is recut, which is what lets
  // the server mark a tile immutable: a pan that comes back over old ground refetches
  // nothing at all, and a regenerated map is picked up on the next load rather than a
  // day later.
  var tag = response.headers.get("X-Map-Build");
  var group = layer("map image", true);
  var url = "/api/maptiles/{z}/{x}/{y}" + (tag ? "?v=" + encodeURIComponent(tag) : "");
  var tiles = new PyramidLayer(url, {
    pane: "basemap",
    tileSize: tilePx,
    noWrap: true,
    // Clamped to the world the tiles cover, so a pan out into the sea beyond it asks for
    // nothing. This is the coarse half of it -- see PyramidLayer for the exact half.
    bounds: mapImageLatLngBounds(b),
    minZoom: map.getMinZoom(),
    maxZoom: map.getMaxZoom(),
    // Below z0 there is nothing smaller to fetch and above the top nothing sharper: both
    // ends reuse the level they have, scaled, instead of asking for a level that is not
    // there.
    minNativeZoom: -top,
    maxNativeZoom: maxZ - top,
    zoomOffset: top,
    updateWhenZooming: false,
  });
  var broken = false;
  tiles.on("tileerror", function () {
    if (broken) return;
    broken = true;
    baseImageryFailed(
      group,
      "map tiles: data/local/tiles/ is there but a tile would not load — showing the biome map instead"
    );
  });
  tiles.addTo(group);
  baseImageryShown();
  return true;
}

/* The whole sheet as one imageOverlay: the fallback, and what any render that is not this
 * generator's -- other corners, no pyramid -- is still drawn as. */
function loadMapImageOverlay() {
  return fetch("/api/mapimage", { method: "HEAD" }).then(function (r) {
    if (r.status !== 200) return; // 204: no local render, which is the default state
    var b = mapImageBounds(r);
    var group = layer("map image", true);
    var image = L.imageOverlay("/api/mapimage", mapImageLatLngBounds(b), {
      pane: "basemap",
      interactive: false,
    });
    image.on("error", function () {
      baseImageryFailed(
        group,
        "map image: data/local/map.png exists but could not be decoded — showing the biome map instead"
      );
    });
    image.addTo(group);
    baseImageryShown();
  });
}

/* Every loader below is epoch-guarded: a switch bumps `state.epoch`, and a reply that
 * comes back for an earlier epoch is dropped instead of drawn. Without this, whichever
 * world answered LAST owned the map -- switch away from a slow world and its late reply
 * silently repainted everything under the new world's name.
 *
 * The catch paths clear their layers before tosting: a failed switch must leave those
 * layers empty, not showing the previous world under the new world's header. */

function loadStatic() {
  // Nodes and factory shapes change only when the player builds, so they are refetched
  // on a world switch rather than on every save write.
  var epoch = state.epoch;
  var live = function () {
    return epoch === state.epoch;
  };
  get("/api/nodes")
    .then(function (d) {
      if (live()) drawNodes(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["node: "]);
      fail("nodes: " + friendly(e));
    });
  get("/api/structures")
    .then(function (d) {
      if (live()) drawStructures(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["foundations"]);
      fail("structures: " + friendly(e));
    });
  get("/api/belts")
    .then(function (d) {
      if (live()) drawBelts(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["belts"]);
      fail("belts: " + friendly(e));
    });
  get("/api/pipes")
    .then(function (d) {
      if (live()) drawPipes(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["pipes"]);
      fail("pipes: " + friendly(e));
    });
  get("/api/factories")
    .then(function (d) {
      if (live()) drawFactories(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["factory labels", "proposals"]);
      fail("factories: " + friendly(e));
    });
}

function loadLive() {
  var epoch = state.epoch;
  var live = function () {
    return epoch === state.epoch;
  };
  get("/api/machines")
    .then(function (d) {
      if (live()) drawMachines(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["machines", "extractors", "generators"]);
      fail("machines: " + friendly(e));
    });
  get("/api/collectibles?mode=remaining")
    .then(function (d) {
      if (live()) drawCollectibles(d);
    })
    .catch(function (e) {
      if (!live()) return;
      clearPrefixed(["pickup: "]);
      fail("collectibles: " + friendly(e));
    });
  get("/api/summary")
    .then(function (s) {
      if (!live()) return;
      busy(false);
      drawPlayer(s.player);
      var power = s.power;
      var measured =
        power.measured_draw_mw === null || power.measured_draw_mw === undefined
          ? power.draw_mw
          : power.measured_draw_mw;
      var parts = [s.header.session_name];
      var phase = phaseText(s.progression.game_phase);
      if (phase) parts.push(phase);
      // The measured figure, labelled: the nameplate total alone reads as "one factory
      // from a brown-out" on a base that is mostly idle. Both live in the tooltip.
      parts.push(Math.round(measured) + " MW drawn / " + Math.round(power.generation_mw) + " MW capacity");
      parts.push(s.age_note);
      var span = el("summary");
      span.textContent = parts.join(" — ");
      span.title =
        "power: " +
        Math.round(measured) +
        " MW measured draw; " +
        Math.round(power.draw_mw) +
        " MW nameplate if every machine ran at once; " +
        Math.round(power.generation_mw) +
        " MW generation capacity";
    })
    .catch(function (e) {
      if (!live()) return;
      busy(false);
      clearPrefixed(["player"]);
      // The header is the page's identity line; a failure leaves a statement, not a
      // blank that reads as "everything is fine, there is just nothing here".
      el("summary").textContent = "this world's save could not be read";
      fail("summary: " + friendly(e));
    });
}

/* A switch in progress is marked on screen -- header says so, map dims -- because the
 * old world's layers stay visible until the new responses land, and an unmarked blend of
 * two worlds reads as data. Cleared when this epoch's summary settles either way. */
function busy(on) {
  var container = el("map");
  if (on) L.DomUtil.addClass(container, "busy");
  else L.DomUtil.removeClass(container, "busy");
}

function reload(note) {
  state.epoch += 1;
  map.closePopup(); // an open card is a claim about the previous world/save
  el("summary").textContent = note || "loading…";
  busy(true);
  writeHash();
  loadStatic();
  loadLive();
}

/* ------------------------------------------------------------ world picker */

function worldOption(w, dupes) {
  var option = document.createElement("option");
  option.value = w.world_id;
  var hours = Math.round((w.play_duration_s || 0) / 3600);
  var label = w.session_name + " (" + w.saves.length + " saves, " + hours + " h)";
  // Two worlds can share a session name -- one id-keyed, one a legacy grouping of saves
  // too old to carry a world id. A save count alone cannot tell them apart.
  if (dupes[w.session_name] > 1 && w.world_id.indexOf("session:") === 0) {
    label += " — old saves without a world id";
  }
  option.textContent = label;
  option.title = "world id: " + w.world_id;
  return option;
}

function fillWorldPicker(preserve) {
  var picker = el("world");
  var dupes = {};
  state.worlds.forEach(function (w) {
    dupes[w.session_name] = (dupes[w.session_name] || 0) + 1;
  });
  picker.innerHTML = "";
  state.worlds.forEach(function (w) {
    picker.appendChild(worldOption(w, dupes));
  });
  if (preserve && currentWorld()) picker.value = state.world;
  picker.disabled = !state.worlds.length;
}

function fillSavePicker() {
  var picker = el("save");
  var w = currentWorld();
  picker.innerHTML = "";
  var newest = document.createElement("option");
  newest.value = "";
  newest.textContent = "newest save";
  newest.title = "follow the newest save, refetching as the game writes new ones";
  picker.appendChild(newest);
  var saves = ((w && w.saves) || []).slice().sort(function (a, b) {
    return (b.mtime_ns || 0) - (a.mtime_ns || 0);
  });
  saves.forEach(function (s) {
    var option = document.createElement("option");
    option.value = s.path || s.filename;
    option.textContent = s.filename;
    picker.appendChild(option);
  });
  if (state.save) {
    picker.value = state.save;
    if (picker.value !== state.save) {
      // The pinned save is no longer in the listing (deleted, or the world changed
      // under it). Keep the pin visible rather than silently unpinning.
      var pinned = document.createElement("option");
      pinned.value = state.save;
      pinned.textContent = "(pinned save no longer listed)";
      picker.appendChild(pinned);
      picker.value = state.save;
    }
  } else {
    picker.value = "";
  }
  picker.disabled = !saves.length;
  picker.onchange = function () {
    state.save = picker.value;
    var chosen = picker.selectedOptions[0];
    reload(state.save ? "opening " + (chosen ? chosen.textContent : "save") + "…" : "back to the newest save…");
  };
}

function loadWorlds() {
  return fetch("/api/worlds")
    .then(function (r) {
      return r.json();
    })
    .then(function (body) {
      if (body.error) throw new Error(body.error);
      state.worlds = body.worlds || [];
      var picker = el("world");
      fillWorldPicker(false);
      picker.onchange = function () {
        state.world = picker.value;
        state.save = "";
        fillSavePicker();
        var chosen = picker.selectedOptions[0];
        reload("switching to " + (chosen ? chosen.textContent : "world") + "…");
      };

      if (!state.worlds.length) {
        // The one state that must NOT end as a healthy-looking blank page: no world at
        // all. The server may still know exactly why each file was rejected, and that
        // diagnosis belongs on screen, permanently -- not in a toast that self-erases.
        var reasons = (body.unsupported || [])
          .map(function (u) {
            return u.filename + ": " + u.reason;
          })
          .join(" · ");
        var text =
          "no readable saves found" +
          (reasons ? " — " + reasons : "") +
          " (set SATISFACTORY_SAVES if they live elsewhere)";
        el("summary").textContent = text;
        el("summary").title = text; // the span ellipsises; the full diagnosis survives hover
        // Geography needs no save. The node table still draws -- the same table the
        // right-click inspector reads, so the two surfaces agree even with no world.
        get("/api/nodes").then(drawNodes).catch(function () {});
        return;
      }

      state.world =
        BOOT.world && state.worlds.some(function (w) { return w.world_id === BOOT.world; })
          ? BOOT.world
          : state.worlds[0].world_id;
      picker.value = state.world;
      if (BOOT.save) {
        var w = currentWorld();
        ((w && w.saves) || []).forEach(function (s) {
          if (s.filename === BOOT.save) state.save = s.path || s.filename;
        });
      }
      fillSavePicker();
      writeHash();
    })
    .catch(function (e) {
      el("summary").textContent = "the world list could not be loaded";
      fail("worlds: " + friendly(e));
    });
}

/* The picker is not a snapshot: a session started or a save written while the tab is
 * open updates the counts and can add a world. Selection and pin are preserved; a scan
 * hiccup (transient error, empty answer) must never wipe a working picker mid-session. */
function refreshWorlds() {
  fetch("/api/worlds")
    .then(function (r) {
      return r.json();
    })
    .then(function (body) {
      if (body.error || !body.worlds || !body.worlds.length) return;
      state.worlds = body.worlds;
      fillWorldPicker(true);
      if (!state.world) {
        // The page opened with no world at all and one has appeared: adopt it.
        state.world = state.worlds[0].world_id;
        el("world").value = state.world;
        fillSavePicker();
        reload("world found — loading…");
        return;
      }
      if (!currentWorld()) return; // the selected world vanished; keep showing it as-is
      fillSavePicker();
    })
    .catch(function () {
      /* refreshed on the next save event */
    });
}

/* The live loop. One EventSource for the process; a save write is an edge trigger and
 * the response is a refetch of the two things a save can change.
 *
 * The grey dot used to mean three different things (connecting, retrying, dead) with one
 * constant title. The title now says which, and losing an ESTABLISHED connection also
 * says so in a toast -- a page quietly presenting stale data as live is the failure mode
 * this block exists to prevent. */
function listen() {
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

loadRegions().then(loadMapImage);

loadWorlds().then(function () {
  // With no world there is nothing to fetch: firing the loaders anyway would bury the
  // persistent "no readable saves" line under six toasts and a fake header.
  if (state.worlds.length) {
    loadStatic();
    loadLive();
  }
  listen();
});
