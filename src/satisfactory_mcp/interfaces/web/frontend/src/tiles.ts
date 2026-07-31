/* The base map: which picture of this world everything else stands on.
 *
 * Four MODES, radio semantics, exactly one at a time -- the game's own artwork, a
 * hypsometric terrain render, a biome-coloured satellite render, and plain: no imagery at
 * all, which is the shipped state and not an error. A mode is at most one L.TileLayer
 * against `/api/maptiles/{layer}/{z}/{x}/{y}`, so switching modes swaps that one layer and
 * touches nothing else -- not the CRS, not the panes, not a single data overlay, not the
 * region blend's rule. That is the server's design showing through and the reason this file
 * is short: every pyramid is cut on the same frame, at the same tile size, into the same
 * grid, so a client changes one path segment and nothing else.
 *
 * Each pyramid still describes ITSELF, though, and that is the one per-mode difference that
 * matters: each pyramid declares its own depth (renders and artwork both reach z7 today, @2x trees stop earlier), so `maxNativeZoom` comes
 * from that layer's own probe headers and Leaflet upscales past it rather than asking for a
 * level that is not there.
 *
 * Probing is the bulk of it, as it always was, because "is there a picture of this world on
 * disk" is now three questions with a fallback hanging off the first: an absent artwork
 * pyramid still falls back to the single `/api/mapimage` overlay, which is what a render
 * someone else made gets drawn as. That fallback is a detail of the ARTWORK mode now rather
 * than a stage of one loader, which is the whole of what folding it into the mode model
 * means.
 *
 * The radios are drawn by layercontrol.ts, which knows nothing about tiles; this file knows
 * nothing about folds. `onModePick` is the seam, and it points this way because tiles.ts
 * already reaches the control through layers.ts -- an import the other way would be a ring.
 */

import { tilePath } from "./api";
import { onModePick, showModes } from "./layercontrol";
import { L } from "./leaflet";
import { MAP_SHEET_PX, MAP_SQUARE_M, map, writeHash } from "./map";
import { regionsUnderMode, updateRegionBlend } from "./regions";
import { BOOT, state } from "./state";
import { fail } from "./toast";

import type { MapTileLayer } from "./api";
import type { ModeChoice } from "./layercontrol";
import type { BaseMode } from "./state";

/** One base-map mode: a radio in the control, and at most one layer on the map. */
interface ModeSpec {
  key: BaseMode;
  /* The path segment `/api/maptiles/{layer}/` answers on. "" is Plain: no imagery -- the one
   * mode that is not a pyramid, which is why the type is a union with the empty string
   * rather than an optional field. Everything else has to be a layer this server serves; see
   * MapTileLayer in api.ts. */
  layer: MapTileLayer | "";
  label: string;
  /** The row's tooltip when the mode can be picked: what this picture actually is. */
  about: string;
  /* ...and what it says when it cannot: which tool writes that tree.
   *
   * Repeated here rather than read off the wire, and that is deliberate. The API's GET 404
   * carries exactly this sentence, but the page probes with HEAD and HEAD answers 204 with
   * no body -- on purpose, because an absent optional render is the ordinary state and a
   * 404 on every clean load teaches the reader to ignore red lines. Asking for the message
   * would mean asking for the error the server went out of its way not to raise. */
  generator: string;
}

/* A mode that IS a pyramid: the same row with the "no imagery" half of `layer` ruled out.
 * The two functions that build a tile URL take this rather than a `ModeSpec`, so "plain has
 * no tiles" is a thing the compiler knows instead of a thing the call order arranges. */
type PyramidSpec = ModeSpec & { layer: MapTileLayer };

function isPyramid(spec: ModeSpec): spec is PyramidSpec {
  return !!spec.layer;
}

var MODES: ModeSpec[] = [
  {
    key: "artwork",
    layer: "map",
    label: "artwork",
    about: "the game's own map artwork",
    generator: "tools/gen_map_image.py, which cuts it out of your own installed game",
  },
  {
    key: "terrain",
    layer: "terrain",
    label: "terrain",
    about: "a hypsometric relief map of this world, drawn from its own heightfield",
    generator:
      "tools/gen_map_renders.py, which draws a hypsometric relief map of this world from " +
      "the 1 m heightfield in data/local/heightmap/",
  },
  {
    key: "satellite",
    layer: "satellite",
    label: "satellite",
    about: "the same relief, coloured from the game's own biome raster",
    generator:
      "tools/gen_map_renders.py, which draws the same relief coloured from the game's own " +
      "biome raster, from the 1 m heightfield in data/local/heightmap/",
  },
  {
    key: "plain",
    layer: "",
    label: "plain",
    about: "no base imagery — the biome regions on the page's own sea",
    generator: "",
  },
];

/* How to build each mode's layer, decided once by the probes and never again.
 *
 * A function rather than the layer itself, so that a mode nobody looks at costs nothing: a
 * TileLayer constructed and never added still holds its options and its event handlers, and
 * three of these would sit there for the two modes the player did not pick. Building on
 * demand also means a mode that broke can be rebuilt by picking it again after a reload,
 * without this module keeping a corpse.
 *
 * A key that is not here is a mode whose pyramid is not on disk -- or one whose tiles turned
 * out not to draw, which `modeFailed` treats as the same thing for the same reason. */
var makers: Partial<Record<BaseMode, () => L.Layer>> = {};

/** Why a mode cannot be picked, when the reason is not simply "never generated". */
var refusals: Partial<Record<BaseMode, string>> = {};

/** The one layer the active mode has on the map, so a switch can take it off again. */
var drawn: L.Layer | null = null;

function specFor(key: string): ModeSpec | null {
  var found: ModeSpec | null = null;
  MODES.forEach(function (spec) {
    if (spec.key === key) found = spec;
  });
  return found;
}

/* The corners a base-map probe answered with, as [x_min, y_min, x_max, y_max] metres. */
function mapImageBounds(response: Response): number[] {
  var raw = (response.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
  if (raw.length === 4 && raw.every(isFinite)) return raw;
  return [MAP_SQUARE_M.x_min, MAP_SQUARE_M.y_min, MAP_SQUARE_M.x_max, MAP_SQUARE_M.y_max];
}

/* Those corners as Leaflet bounds -- the [-y, x] flip, so the y ends swap. */
function mapImageLatLngBounds(b: number[]): L.LatLngBounds {
  return L.latLngBounds([
    [-b[3]!, b[0]!],
    [-b[1]!, b[2]!],
  ]);
}

/* A picture that turns out not to draw stops being a mode.
 *
 * A truncated download, an error page saved as .png, a pyramid half-deleted under a running
 * server: the file EXISTS, so the probe said yes, and the first tile says otherwise. The
 * honest response is the one an absent pyramid already gets -- the mode greys out, with the
 * reason in its tooltip instead of the generator's name -- plus a toast, because unlike an
 * absent render this one IS a fault and the player asked for it by name.
 *
 * Falling back to plain rather than to another render: the player picked a picture, and
 * silently substituting a different picture of the same world is the one answer that could
 * be mistaken for success. Plain cannot be. */
function modeFailed(spec: ModeSpec, why: string, message: string): void {
  delete makers[spec.key];
  refusals[spec.key] = why;
  if (state.mode === spec.key) setMode("plain", false);
  else showModes(modeChoices(), state.mode || "plain");
  fail(message);
}

/* The two extras every pyramid layer below is built with: how deep its @2x tree goes, in
 * the pyramid's own z, and the query fragment that asks for it -- separator included, ""
 * when this display or this layer has no use for one. Options rather than a second URL
 * template because Leaflet already rebuilds the URL per tile, and the choice is per TILE:
 * one layer spans levels the dense tree has and levels only the 1x tree reaches. */
interface PyramidOptions extends L.TileLayerOptions {
  denseMaxZ?: number;
  denseQuery?: string;
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
 * zoom session's network log has no red in it at all.
 *
 * ...and, since the @2x trees, which DENSITY each level actually has: `?px=` rides on the
 * levels the dense tree reaches and is dropped past its top, so a hi-DPI display keeps
 * zooming into the deep 1x levels instead of stopping where the dense tree does. See
 * pyramidMaker for why that is the right picture and not a compromise. */
var PyramidLayer = L.TileLayer.extend({
  getTileUrl: function (this: L.TileLayer, coords: L.Coords) {
    // Asserted rather than defaulted: this layer is only ever constructed below, with a
    // zoomOffset, and `|| 0` here would be a silently different grid rather than a fix.
    var span = 1 << (coords.z + this.options.zoomOffset!);
    if (coords.x < 0 || coords.y < 0 || coords.x >= span || coords.y >= span) {
      return L.Util.emptyImageUrl;
    }
    var url = L.TileLayer.prototype.getTileUrl.call(this, coords);
    var options = this.options as PyramidOptions;
    // The same arithmetic as `span`: coords.z + zoomOffset is the pyramid's own z, already
    // clamped to maxNativeZoom by Leaflet, so past the dense tree's top this asks for the
    // 1x tile of the SAME level -- the identical square of the world, standard density.
    if (options.denseQuery && coords.z + options.zoomOffset! <= options.denseMaxZ!) {
      url += options.denseQuery;
    }
    return url;
  },
}) as new (url: string, options: PyramidOptions) => L.TileLayer;

/* Whether this display can show more pixels than a 256 px tile carries.
 *
 * Read once, at probe time, and deliberately not watched: a window dragged between a laptop
 * screen and an external monitor changes devicePixelRatio, and rebuilding every tile layer
 * mid-drag to chase it would refetch the whole view for a difference nobody asked for. A
 * reload picks up the new one, which is the same bargain the CRS and the bounds already make.
 *
 * `>= 1.5` rather than `> 1`: a 125% Windows scale factor reports 1.25, and at 1.25 the @2x
 * tile is 60% more pixels than the screen can show -- four times the bytes to be downsampled
 * by the compositor. 150% and up is where the denser tile is nearer the truth than the
 * sparser one. */
function wantsDenseTiles(): boolean {
  return (window.devicePixelRatio || 1) >= 1.5;
}

/* One pyramid, wired to the pixel space map.ts' CRS_SHEET_PX set up: Leaflet's tile level Z
 * + 5 is the pyramid's z, because 256 * 2^(Z+5) is 8192 * 2^Z, and 8192 sheet pixels are one
 * screen pixel each at map zoom 0. So the level Leaflet asks for is the level whose pixels
 * match the view, which is the whole point of cutting a pyramid.
 *
 * A layer may hold that grid TWICE -- `tiles/` at 256 px a tile and `tiles@2x/` at 512 --
 * and this is where the second one is chosen. Nothing about the grid changes: `tileSize`
 * stays the CSS size it always was, the zoom offset stays 5, the same `{z}/{x}/{y}` names
 * the same square of the world. The tile simply arrives with twice the pixels in it and
 * Leaflet draws it into the same box, which on a hi-DPI display is the difference between a
 * resampled map and a sharp one. The @2x tree is SHALLOWER than the 1x tree, though -- one
 * level by arithmetic, since 512 * 2^z runs out of sheet before 256 * 2^z does, and more
 * than one where the deep 1x levels were enhanced past the sheet (the artwork's z5..z7
 * exist only as 1x) -- so the density is chosen per LEVEL rather than per layer:
 * `maxNativeZoom` stays the 1x tree's depth, `?px=` rides on the levels the @2x tree
 * reaches, and past its top the request falls back to the 1x tile of the same z. That
 * fallback is the identical square of the world at standard density -- strictly more to
 * see than stopping at the @2x top and letting Leaflet upscale, which is what a hi-DPI
 * display used to get while a 1x display walked the deep levels it was denied.
 *
 * Returns null -- this mode cannot be drawn as a pyramid -- when the server describes one
 * this grid cannot draw: corners that are not the square the CRS is anchored on, or a tile
 * size that is not a power-of-two fraction of the sheet. For the artwork that means the
 * single-image fallback, which draws either of those correctly; for a render it means the
 * mode is not offered, because a render is only ever cut by the generator that pins this
 * frame, and a tile grid quietly offset from its own picture is worse than no picture. */
function pyramidMaker(spec: PyramidSpec, response: Response): (() => L.Layer) | null {
  var b = mapImageBounds(response);
  var anchored = [
    MAP_SQUARE_M.x_min,
    MAP_SQUARE_M.y_min,
    MAP_SQUARE_M.x_max,
    MAP_SQUARE_M.y_max,
  ];
  var moved = b.some(function (v, i) {
    return Math.abs(v - anchored[i]!) > 1;
  });
  if (moved) return null;

  var tilePx = +response.headers.get("X-Map-Tile-Px")! || 256;
  // Each layer's OWN depth: every pyramid states its own max-z in its sidecar (all three reach z7 today), so
  // this is the one number a mode switch actually has to carry across. Past it Leaflet
  // upscales the deepest level it has instead of asking for one that is not there.
  var maxZ = +response.headers.get("X-Map-Tile-Max-Z")!;
  if (!isFinite(maxZ) || maxZ < 0) maxZ = 5;
  var top = Math.log2(MAP_SHEET_PX / tilePx); // the pyramid z that IS the sheet: 5.
  if (!isFinite(top) || top !== Math.round(top)) return null;

  // ...and, when this layer has a denser tree and this display can use it, that tree's
  // size and depth as well. `tileSize` deliberately stays `tilePx`: it is the CSS size of
  // a tile and the grid must not move. What changes is how many pixels arrive inside it.
  // `maxZ` deliberately stays the 1x tree's depth: the dense tree ends sooner, and where
  // it has ended the 1x tile of the same level is still a level nobody has seen yet.
  var densePx = +response.headers.get("X-Map-Tile-2x-Px")!;
  var denseMaxZ = +response.headers.get("X-Map-Tile-2x-Max-Z")!;
  var dense = wantsDenseTiles() && isFinite(densePx) && densePx > 0 && isFinite(denseMaxZ);
  if (dense) maxZ = Math.max(maxZ, denseMaxZ);

  // The build tag makes every URL change when the pyramid is recut, which is what lets
  // the server mark a tile immutable: a pan that comes back over old ground refetches
  // nothing at all, and a regenerated map is picked up on the next load rather than a
  // day later. It is per layer, so recutting the satellite cannot invalidate the terrain
  // a browser is holding -- and the @2x tree's own numbers are inside the same tag, so
  // recutting either one changes both.
  //
  // `px=` is NOT in this query: it is per tile rather than per layer, because one layer
  // spans levels the dense tree has and levels only the 1x tree reaches. PyramidLayer
  // appends `denseQuery` -- separator and all, which is why it is cut here where the rest
  // of the query is known -- to exactly the levels the dense tree covers.
  var tag = response.headers.get("X-Map-Build");
  var query = [];
  if (tag) query.push("v=" + encodeURIComponent(tag));
  var url =
    tilePath(spec.layer, "{z}", "{x}", "{y}") + (query.length ? "?" + query.join("&") : "");
  var denseQuery = dense ? (query.length ? "&" : "?") + "px=" + densePx : "";
  var bounds = mapImageLatLngBounds(b);

  return function () {
    var tiles = new PyramidLayer(url, {
      pane: "basemap",
      tileSize: tilePx,
      noWrap: true,
      // Clamped to the world the tiles cover, so a pan out into the sea beyond it asks for
      // nothing. This is the coarse half of it -- see PyramidLayer for the exact half.
      bounds: bounds,
      minZoom: map.getMinZoom(),
      maxZoom: map.getMaxZoom(),
      // Below z0 there is nothing smaller to fetch and above the top nothing sharper: both
      // ends reuse the level they have, scaled, instead of asking for a level that is not
      // there.
      minNativeZoom: -top,
      maxNativeZoom: maxZ - top,
      zoomOffset: top,
      updateWhenZooming: false,
      denseMaxZ: denseMaxZ,
      denseQuery: denseQuery,
    });
    var broke = false;
    tiles.on("tileerror", function () {
      if (broke) return;
      broke = true;
      modeFailed(
        spec,
        "the pyramid is on disk but a tile would not load",
        spec.label + " tiles: the pyramid is there but a tile would not load — showing plain instead"
      );
    });
    return tiles;
  };
}

/* The whole sheet as one imageOverlay: the artwork mode's fallback, and what any render
 * that is not this generator's -- other corners, no pyramid -- is still drawn as. */
function overlayMaker(spec: ModeSpec, response: Response): () => L.Layer {
  var bounds = mapImageLatLngBounds(mapImageBounds(response));
  return function () {
    var image = L.imageOverlay("/api/mapimage", bounds, {
      pane: "basemap",
      interactive: false,
    });
    image.on("error", function () {
      modeFailed(
        spec,
        "data/local/map.png exists but could not be decoded",
        "map image: data/local/map.png exists but could not be decoded — showing plain instead"
      );
    });
    return image;
  };
}

/** One HEAD against one pyramid's z0 tile. Never rejects: a probe that fails is a mode
 *  that is not there, which is the ordinary state for all three of them. */
function probePyramid(spec: PyramidSpec): Promise<void> {
  return fetch(tilePath(spec.layer, 0, 0, 0), { method: "HEAD" })
    .then(function (r) {
      if (r.status !== 200) return; // 204: never generated, and that is not an error
      var make = pyramidMaker(spec, r);
      if (make) makers[spec.key] = make;
    })
    .catch(function () {
      /* the probe failing means no picture, which is the default state anyway */
    });
}

/** ...and the artwork's fallback, probed only when its pyramid did not answer. */
function probeMapImage(spec: ModeSpec): Promise<void> {
  return fetch("/api/mapimage", { method: "HEAD" })
    .then(function (r) {
      if (r.status !== 200) return; // 204: no local render, which is the default state
      makers[spec.key] = overlayMaker(spec, r);
    })
    .catch(function () {
      /* same as above: no picture is the shipped answer */
    });
}

/** The rows layercontrol.ts draws, rebuilt from the probes every time anything changes. */
function modeChoices(): ModeChoice[] {
  return MODES.map(function (spec): ModeChoice {
    var ready = spec.key === "plain" || !!makers[spec.key];
    return {
      key: spec.key,
      label: spec.label,
      ready: ready,
      note: ready
        ? spec.about
        : refusals[spec.key] || "not generated yet — written by " + spec.generator,
    };
  });
}

/* Swap the one layer, and nothing else.
 *
 * Everything a mode switch does NOT do is the point of the function being this short: the
 * panes were created once by map.ts, the overlays are the player's and are left where they
 * were, the CRS and the tile grid are the same for every layer the server cuts. What
 * changes is which directory the tiles come from and how deep it goes.
 *
 * A mode that cannot be drawn resolves to plain rather than refusing, because the two
 * callers that can ask for one are a pasted link and a tile that just broke, and both of
 * them deserve a map. `pinned` is what tells a click from a boot: a click is a decision and
 * belongs in the fragment, while the boot resolution is what the fragment is read BY and
 * would otherwise write a mode into the URL of a page nobody chose anything on. */
export function setMode(key: BaseMode, pinned: boolean): void {
  var mode: BaseMode = key === "plain" || makers[key] ? key : "plain";
  if (drawn) {
    map.removeLayer(drawn);
    drawn = null;
  }
  var make = makers[mode];
  if (make) {
    drawn = make();
    drawn.addTo(map);
  }
  state.mode = mode;
  state.imagery = !!drawn;
  regionsUnderMode(state.imagery);
  updateRegionBlend();
  showModes(modeChoices(), mode);
  if (pinned) writeHash();
}

/* Which mode a fresh page opens in.
 *
 * The fragment first, so a pasted link pins the whole view -- and only if that mode can
 * actually be drawn here, because a link to a render this machine never generated should
 * land on a map rather than on an empty one. Otherwise the order this page has always used,
 * now stated as a rule instead of left as the shape of a fallback chain: the artwork if it
 * is there, and plain if it is not.
 *
 * Terrain and satellite are never chosen FOR you, even when they are the only pictures on
 * disk. They are interpretations of this world rather than the map of it, and which one to
 * look at is a question with no default answer -- so the page opens on the one picture that
 * is not an opinion, and the radios say what else there is. */
function bootMode(): BaseMode {
  var asked = specFor(BOOT.mode || "");
  if (asked && (asked.key === "plain" || makers[asked.key])) return asked.key;
  return makers.artwork ? "artwork" : "plain";
}

/* Probe every pyramid once, then open on a mode. Three HEADs in parallel rather than the
 * chain this used to be: they are independent questions about three independent directories,
 * and the artwork's own fallback is the only thing that has to wait for an answer. */
export function loadBaseMap(): Promise<void> {
  onModePick(function (key) {
    setMode(key as BaseMode, true);
  });
  return Promise.all(MODES.filter(isPyramid).map(probePyramid))
    .then(function () {
      var artwork = specFor("artwork");
      if (!artwork || makers.artwork) return;
      return probeMapImage(artwork);
    })
    .then(function () {
      setMode(bootMode(), false);
    });
}
