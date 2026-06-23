/* The optional map render: a tile pyramid if one was cut, one big overlay if not, and
 * nothing at all as the shipped default.
 *
 * Its own module because "is there a picture of this world on disk" is a question with three
 * answers and two failure modes, and the probing is the bulk of it. Nothing here draws the
 * world; it decides which of two Leaflet layers to hand the basemap pane, and puts the biome
 * fill back if the file it found turns out not to decode.
 */

import { L } from "./leaflet";
import { layer } from "./layers";
import { MAP_SHEET_PX, MAP_SQUARE_M, map } from "./map";
import { updateRegionBlend } from "./regions";
import { state } from "./state";
import { fail } from "./toast";

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
 * no picture underneath there is nothing to see through to, and regions.ts' REGION_BLEND against the
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
export function loadMapImage() {
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

/* The pyramid, wired to the pixel space map.ts' CRS_SHEET_PX set up: Leaflet's tile level Z + 5
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
