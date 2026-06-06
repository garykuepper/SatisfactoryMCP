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
 */

"use strict";

var BOUND = 5000; // metres; the playable world is ~7 km across, so this frames it loosely.

function xy(row) {
  return [-row.y_m, row.x_m];
}

function el(id) {
  return document.getElementById(id);
}

function fail(message) {
  var box = el("err");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(fail.t);
  fail.t = setTimeout(function () {
    box.hidden = true;
  }, 6000);
}

function get(path) {
  var q = state.world ? (path.indexOf("?") < 0 ? "?" : "&") + "world=" + encodeURIComponent(state.world) : "";
  return fetch(path + q).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
      return body;
    });
  });
}

function popup(pairs) {
  return (
    "<table>" +
    pairs
      .filter(function (p) {
        return p[1] !== null && p[1] !== undefined && p[1] !== "";
      })
      .map(function (p) {
        return '<tr><td class="popup-key">' + p[0] + "</td><td>" + p[1] + "</td></tr>";
      })
      .join("") +
    "</table>"
  );
}

/* ------------------------------------------------------------------ palette */

// Ore colours follow the in-game item tints closely enough to be recognisable without
// shipping a single game asset: they are six hex strings, not textures.
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
};

var PURITY_RADIUS = { impure: 3, normal: 4.5, pure: 6 };

var KIND_COLOUR = { machines: "#4aa3df", extractors: "#e0a33f", generators: "#d9534f" };

// One muted colour per biome letter, keyed exactly like /api/regions' legend. Hand-picked
// to read as terrain at a glance -- sand for the deserts, greens for the forests, teal
// along the coast, murk for the swamp -- and, like the ore palette above, they are 21 hex
// strings rather than a single pixel of anyone's artwork.
//
// Dark on purpose, and painted at full opacity: a translucent cell has to blend against
// the sea colour at its edges too, and 768 of them sharing borders turns that blend into
// a visible 256 m grid. These are the blended values, baked in, so the cells of one
// region merge into one shape.
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

var state = { world: "", layers: {}, control: null, map: null };

/* --------------------------------------------------------------------- map */

var map = L.map("map", {
  crs: L.CRS.Simple,
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
map.setView([0, 0], -3);
map.attributionControl.setPrefix("").addAttribution("map data from your save &middot; Leaflet");

/* One flat 10 km square of "land" used to be drawn here, in the overlay pane. It was a
 * stand-in for terrain and it is gone: the biome raster is the real thing, and an opaque
 * square in the overlay pane would sit on top of it. What was inside that square is now
 * painted per biome, and what is outside is the page's sea colour -- which is the whole
 * trick behind the coastline. */

/* The ground everything else stands on gets its own pane, below overlayPane (400), so
 * terrain can never end up in front of a node the player is trying to click. The
 * stacking is decided here, once, instead of by the order things happen to be drawn. */
map.createPane("regions");
map.getPane("regions").style.zIndex = 350;

/* The player's own concrete, in its own pane between the biome raster (350) and the
 * overlay pane (400): a floor plan has to cover the terrain it was poured on and sit
 * under every machine, node and label that stands on it. Leaflet builds one canvas per
 * pane, so this is also what keeps 8,000 rectangles off the same canvas as the terrain. */
map.createPane("foundations");
map.getPane("foundations").style.zIndex = 360;

var control = L.control.layers(null, {}, { collapsed: false }).addTo(map);
state.control = control;
L.control.scale({ imperial: false }).addTo(map);

/* A named layer that can be replaced wholesale on refetch without the checkbox
 * forgetting whether it was ticked -- that is why the LayerGroup identity is kept and
 * only its contents are cleared. */
function layer(name, on) {
  if (!state.layers[name]) {
    var group = L.layerGroup();
    state.layers[name] = group;
    control.addOverlay(group, name);
    if (on) group.addTo(map);
  }
  return state.layers[name].clearLayers();
}

/* ------------------------------------------------------------------ drawing */

var REGION_FILL = 1; // see REGION_COLOUR: opaque cells, or the shared borders become a grid.

/* The base map: one flat rectangle per 256 m raster cell, plus a name at each centroid.
 *
 * Orientation is the whole trap here and the API's docstring spells it out: grid row 0 is
 * the NORTH edge because y0_m is the smallest y and game +Y is south. Cell (i, j) spans
 * y in [y, y+cell], which is latitude [-(y+cell), -y] once the page's [-y, x] convention
 * is applied -- so the y bounds swap, and only here. Void cells are left unpainted: the
 * sea colour showing through them is the coastline.
 */
function drawRegions(data) {
  var terrain = layer("terrain", true);
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
          // a shape instead of showing a grid; interactive:false so the terrain never
          // eats a click meant for a node sitting on top of it.
          color: colour,
          weight: 1,
          opacity: REGION_FILL,
          fillColor: colour,
          fillOpacity: REGION_FILL,
          interactive: false,
          pane: "regions",
        }
      ).addTo(terrain);
    }
  });

  Object.keys(data.regions).forEach(function (name) {
    // A standalone tooltip, not a zero-opacity marker: a marker would drag Leaflet's
    // default icon (and its two image requests) into the page for a label that is meant
    // to be text and nothing else.
    var centre = data.regions[name].centroid_m;
    L.tooltip({ permanent: true, direction: "center", className: "region-label" })
      .setLatLng([-centre[1], centre[0]])
      .setContent(name)
      .addTo(names);
  });
}

/* The player's floor plan: one 8 m tile per placed foundation, ramp, wall or catwalk.
 *
 * Everything about the shape of these is decided by what the projection does NOT carry.
 *
 *   * No rotation. The instance quaternion is dropped at extraction, so every tile is
 *     drawn AXIS-ALIGNED. A slab the player laid at an angle -- and this world has
 *     several -- comes out as a staircase of squares rather than a tilted rectangle.
 *     That is the honest drawing; guessing a yaw from the neighbours would invent one.
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
  var group = layer("foundations", true);
  var half = (data.tile_m || 8) / 2;
  data.structures.forEach(function (s) {
    if (s.x_m === null || s.y_m === null) return;
    L.rectangle(
      [
        [-s.y_m - half, s.x_m - half],
        [-s.y_m + half, s.x_m + half],
      ],
      {
        color: STRUCTURE_COLOUR,
        weight: 1,
        opacity: 0.9,
        fillColor: STRUCTURE_COLOUR,
        fillOpacity: 0.9,
        interactive: false,
        pane: "foundations",
      }
    ).addTo(group);
  });
}

function drawNodes(data) {
  var byResource = {};
  data.nodes.forEach(function (n) {
    (byResource[n.resource] = byResource[n.resource] || []).push(n);
  });
  Object.keys(byResource)
    .sort()
    .forEach(function (resource) {
      var short = resource.replace(/^Desc_/, "").replace(/_C$/, "");
      var group = layer("node: " + short, true);
      byResource[resource].forEach(function (n) {
        L.circleMarker(xy(n), {
          radius: PURITY_RADIUS[n.purity] || 4,
          color: RESOURCE_COLOUR[resource] || "#888",
          weight: n.occupied ? 2 : 1,
          opacity: 1,
          fillOpacity: n.occupied ? 0.15 : 0.75,
        })
          .bindPopup(
            popup([
              ["node", short + " (" + n.purity + ")"],
              ["selector", "<code>node:" + n.name + "</code>"],
              ["at", n.x_m + ", " + n.y_m + " m"],
              ["occupied by", n.occupant_cls],
            ])
          )
          .addTo(group);
      });
    });
}

/* Machines at their real size: `w_m`/`l_m` are the building's own footprint, so a
 * Manufacturer (18x20 m) reads as the eight-times-larger thing it is next to a
 * Constructor (8x10 m). Null for the classes the docs dump gives no clearance data --
 * both biomass burners here -- and those fall back to the 6 m square every machine used
 * to get. Axis-aligned, and for the same reason the foundations are: the projection
 * carries no yaw, so a machine the player rotated 90 degrees draws at its unrotated
 * extent rather than at a guessed one. */
var MACHINE_FALLBACK_M = 6;

function drawMachines(data) {
  ["machines", "extractors", "generators"].forEach(function (kind) {
    var group = layer(kind, kind !== "machines");
    data[kind].forEach(function (m) {
      if (m.x_m === null) return;
      var w = (m.w_m || MACHINE_FALLBACK_M) / 2;
      var l = (m.l_m || MACHINE_FALLBACK_M) / 2;
      L.rectangle(
        [
          [-m.y_m - l, m.x_m - w],
          [-m.y_m + l, m.x_m + w],
        ],
        {
          color: KIND_COLOUR[kind],
          weight: 1,
          fillOpacity: m.paused ? 0.15 : 0.65,
          dashArray: m.paused ? "2,2" : null,
        }
      )
        .bindPopup(
          popup([
            ["building", m.name],
            ["recipe", m.recipe],
            ["clock", m.clock === null ? null : Math.round(m.clock * 100) + "%"],
            ["paused", m.paused ? "yes" : null],
            ["footprint", m.w_m && m.l_m ? m.w_m + " x " + m.l_m + " m" : null],
            ["at", m.x_m + ", " + m.y_m + " m"],
            ["instance", "<code>" + m.instance_leaf + "</code>"],
          ])
        )
        .addTo(group);
    });
  });
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

// Below this, proposal labels are hidden. They are the noisy half -- one per unnamed
// cluster, all reading "#7 Concrete (5)" -- and zoomed out they overlap each other and
// the named labels into an unreadable pile. The named ones stay: they are the player's
// own words and the reason to look at the map zoomed out at all.
var PROPOSAL_LABEL_ZOOM = -2;

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
  marker.bindTooltip(text, {
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
      ["selector", "<code>label:" + f.name + "</code>"],
    ]).addTo(named);
  });
  var proposed = layer("proposals", false);
  data.proposals.forEach(function (p) {
    var title = "#" + p.index + " " + p.label;
    factoryAnchor(p, title + " (" + p.machines + ")", "factory-label proposal", [
      ["proposal", title],
      ["machines", p.machines],
      ["cohesion", p.score],
      ["spread", p.spread_m + " m"],
      ["selector", "<code>proposal:" + p.index + "</code>"],
    ]).addTo(proposed);
  });
}

/* One class on the map container drives the declutter, so hiding 14 proposal labels is a
 * single CSS rule rather than 29 layer add/removes that would also fight the checkbox. */
function decluttered() {
  var container = map.getContainer();
  var hide = map.getZoom() < PROPOSAL_LABEL_ZOOM;
  if (hide) L.DomUtil.addClass(container, "hide-proposal-labels");
  else L.DomUtil.removeClass(container, "hide-proposal-labels");
}

map.on("zoomend", decluttered);
decluttered();

function drawCollectibles(data) {
  var byCategory = {};
  data.rows.forEach(function (r) {
    (byCategory[r.category] = byCategory[r.category] || []).push(r);
  });
  Object.keys(byCategory)
    .sort()
    .forEach(function (category) {
      // One toggleable group per category, because "show me every hard drive" and "show
      // me everything" are different questions and the second one is unreadable.
      var group = layer("pickup: " + category, false);
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
          : L.circleMarker(here, { radius: 4, color: "#7fd1b9", weight: 1, fillOpacity: 0.7 });
        mark
          .bindPopup(
            popup([
              ["pickup", category],
              ["name", "<code>" + r.name + "</code>"],
              ["state", r.collected ? "collected" : r.observed || "unknown"],
              ["at", r.x_m + ", " + r.y_m + " m"],
            ])
          )
          .addTo(group);
      });
    });
}

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
      fail("regions: " + e.message);
    });
}

/* The optional half of the base map: a render the user dropped at data/local/map.png.
 * Nothing is shipped, so 404 is the ordinary answer and is not an error worth showing --
 * the endpoint's own message says where the file goes. */
function loadMapImage() {
  return fetch("/api/mapimage", { method: "HEAD" })
    .then(function (r) {
      if (!r.ok) return;
      var raw = (r.headers.get("X-Map-Bounds-M") || "").split(",").map(Number);
      var b = raw.length === 4 && raw.every(isFinite) ? raw : [-3247, -3750, 4253, 3750];
      L.imageOverlay(
        "/api/mapimage",
        [
          [-b[3], b[0]],
          [-b[1], b[2]],
        ],
        { pane: "regions", interactive: false }
      ).addTo(layer("map image", true));
      // A real render beats the cell fill it covers, so the fill steps aside -- by
      // unticking its box, so one click brings it back.
      if (state.layers.terrain) map.removeLayer(state.layers.terrain);
    })
    .catch(function () {
      /* the probe failing means no picture, which is the default state anyway */
    });
}

function loadStatic() {
  // Nodes and factory shapes change only when the player builds, so they are refetched
  // on a world switch rather than on every save write.
  get("/api/nodes").then(drawNodes).catch(function (e) {
    fail("nodes: " + e.message);
  });
  get("/api/structures").then(drawStructures).catch(function (e) {
    fail("structures: " + e.message);
  });
  get("/api/factories").then(drawFactories).catch(function (e) {
    fail("factories: " + e.message);
  });
}

function loadLive() {
  get("/api/machines").then(drawMachines).catch(function (e) {
    fail("machines: " + e.message);
  });
  get("/api/collectibles?mode=remaining")
    .then(drawCollectibles)
    .catch(function (e) {
      fail("collectibles: " + e.message);
    });
  get("/api/summary")
    .then(function (s) {
      var power = s.power;
      el("summary").textContent =
        s.header.session_name +
        " — phase " +
        s.progression.game_phase +
        " — " +
        Math.round(power.draw_mw) +
        "/" +
        Math.round(power.generation_mw) +
        " MW — " +
        s.age_note;
    })
    .catch(function (e) {
      el("summary").textContent = "";
      fail("summary: " + e.message);
    });
}

function loadWorlds() {
  return fetch("/api/worlds")
    .then(function (r) {
      return r.json();
    })
    .then(function (body) {
      if (body.error) throw new Error(body.error);
      var picker = el("world");
      picker.innerHTML = "";
      body.worlds.forEach(function (w) {
        var option = document.createElement("option");
        option.value = w.world_id;
        option.textContent = w.session_name + " (" + w.saves.length + " saves)";
        picker.appendChild(option);
      });
      if (body.worlds.length) state.world = body.worlds[0].world_id;
      picker.onchange = function () {
        state.world = picker.value;
        loadStatic();
        loadLive();
      };
    })
    .catch(function (e) {
      fail("worlds: " + e.message);
    });
}

/* The live loop. One EventSource for the process; a save write is an edge trigger and
 * the response is a refetch of the two things a save can change. Cheap enough to do on
 * every write: the endpoints are a projection read, and the projection is cached. */
function listen() {
  var source = new EventSource("/api/events");
  var dot = el("live");
  source.onopen = function () {
    dot.className = "dot on";
  };
  source.onerror = function () {
    dot.className = "dot";
  };
  source.addEventListener("save", function () {
    dot.className = "dot hit";
    setTimeout(function () {
      dot.className = "dot on";
    }, 800);
    loadLive();
  });
}

loadRegions().then(loadMapImage);

loadWorlds().then(function () {
  loadStatic();
  loadLive();
  listen();
});
