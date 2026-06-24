/* The map object, the coordinate convention, and the three panes everything is stacked in.
 *
 * The page's one coordinate rule lives here and nowhere else: Satisfactory is +X east and +Y
 * SOUTH while Leaflet is +lat north, so a point is plotted at [-y, x]. `xy` and
 * `footprintCorners` are the two functions that apply it and `writeHash` is the one that
 * inverts it, which is why the third is in this file rather than with the world picker whose
 * selection it also writes: the half of the fragment that can be got wrong is the viewport
 * half.
 *
 * Creating the map is a SIDE EFFECT of importing this module, as it was of loading the old
 * script, and the modules that decorate it (the layer control, the region pane's blend) get
 * their ordering from importing this one.
 */

import { L } from "./leaflet";
import { BOOT, pinnedFilename, state } from "./state";

var BOUND = 5000; // metres; the playable world is ~7 km across, so this frames it loosely.

// The whole-world framing every load starts from.
export var HOME_VIEW: { centre: L.LatLngTuple; zoom: number } = { centre: [0, 0], zoom: -3 };

/** A row with game coordinates, plotted the page's one way round. See the note above. */
export function xy(row: { x_m: number; y_m: number }): L.LatLngTuple {
  return [-row.y_m, row.x_m];
}

/* The in-game map square, in metres, and the sheet the generator cuts from it. These two
 * numbers set the map's pixel unit below, and they are the same square the server pins by
 * default -- a render pinned anywhere else is drawn as one overlay instead (see
 * loadMapImage in tiles.ts), because the tile grid below is anchored on THIS square. */
export var MAP_SQUARE_M = { x_min: -3247, x_max: 4253, y_min: -3750, y_max: 3750 };
export var MAP_SHEET_PX = 8192;
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

export var map = L.map("map", {
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
  // `+undefined` is NaN, which is precisely the "no z in the fragment" branch, so the
  // assertions below are about the type and not about the value.
  var zoom = isFinite(+BOOT.z!) ? +BOOT.z! : HOME_VIEW.zoom;
  var centre = HOME_VIEW.centre;
  if (BOOT.c) {
    var raw = BOOT.c.split(",");
    if (raw.length === 2 && isFinite(+raw[0]!) && isFinite(+raw[1]!)) centre = [-+raw[1]!, +raw[0]!];
  }
  map.setView(centre, zoom);
})();

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
export function pixelsPerMetre(zoom?: number): number {
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
map.getPane("basemap")!.style.zIndex = "340";

/* The biome regions everything else stands on get their own pane, below overlayPane (400),
 * so the region fill can never end up in front of a node the player is trying to click. The
 * stacking is decided here, once, instead of by the order things happen to be drawn.
 *
 * The pane is also the unit of transparency: see REGION_BLEND in regions.ts. */
map.createPane("regions");
map.getPane("regions")!.style.zIndex = "350";

/* The player's own concrete, in its own pane between the biome raster (350) and the
 * overlay pane (400): a floor plan has to cover the ground it was poured on and sit
 * under every machine, node and label that stands on it. Leaflet builds one canvas per
 * pane, so this is also what keeps 8,000 rectangles off the region cells' canvas. */
map.createPane("foundations");
map.getPane("foundations")!.style.zIndex = "360";

/* The fragment is the page's whole address: what is being looked at (world, save), what it
 * is drawn on (mode), and where the eye is (z, c). A link pins all three, in that order --
 * subject, then picture, then viewport -- so the two halves a human might edit by hand stay
 * at the ends.
 *
 * `mode` is omitted while `state.mode` is "", which is the window between the page loading
 * and tiles.ts' probes answering. A pan in that window must not pin a mode the page has not
 * chosen yet: the fragment would then say `plain` on a machine whose artwork was about to
 * load, and the next reload would honour it. */
export function writeHash(): void {
  var parts: string[] = [];
  if (state.world) parts.push("world=" + encodeURIComponent(state.world));
  var pinned = pinnedFilename();
  if (pinned) parts.push("save=" + encodeURIComponent(pinned));
  if (state.mode) parts.push("mode=" + state.mode);
  var c = map.getCenter();
  parts.push("z=" + map.getZoom());
  parts.push("c=" + Math.round(c.lng * 10) / 10 + "," + Math.round(-c.lat * 10) / 10);
  history.replaceState(null, "", "#" + parts.join("&"));
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
export function footprintCorners(
  x: number,
  y: number,
  w: number,
  l: number,
  yaw: number | null | undefined
): L.LatLngTuple[] {
  var a = ((yaw || 0) * Math.PI) / 180;
  var cos = Math.cos(a);
  var sin = Math.sin(a);
  var offsets: [number, number][] = [
    [-w, -l],
    [w, -l],
    [w, l],
    [-w, l],
  ];
  return offsets.map(function (d): L.LatLngTuple {
    return [-(y + d[0] * sin + d[1] * cos), x + d[0] * cos - d[1] * sin];
  });
}
