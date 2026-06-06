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

L.rectangle(
  [
    [-BOUND, -BOUND],
    [BOUND, BOUND],
  ],
  { color: "#2b3a44", weight: 1, fill: true, fillColor: "#1b2a22", fillOpacity: 1, interactive: false }
).addTo(map);

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

function drawMachines(data) {
  ["machines", "extractors", "generators"].forEach(function (kind) {
    var group = layer(kind, kind !== "machines");
    data[kind].forEach(function (m) {
      if (m.x_m === null) return;
      L.rectangle(
        [
          [-m.y_m - 3, m.x_m - 3],
          [-m.y_m + 3, m.x_m + 3],
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
            ["at", m.x_m + ", " + m.y_m + " m"],
            ["instance", "<code>" + m.instance_leaf + "</code>"],
          ])
        )
        .addTo(group);
    });
  });
}

function drawFactories(data) {
  var named = layer("factory labels", true);
  data.labels.forEach(function (f) {
    L.marker([-f.centroid_m[1], f.centroid_m[0]], { opacity: 0 })
      .bindTooltip(f.name, { permanent: true, direction: "center", className: "factory-label" })
      .addTo(named);
  });
  var proposed = layer("proposals", false);
  data.proposals.forEach(function (p) {
    L.marker([-p.centroid_m[1], p.centroid_m[0]], { opacity: 0 })
      .bindTooltip("#" + p.index + " " + p.label + " (" + p.machines + ")", {
        permanent: true,
        direction: "center",
        className: "factory-label proposal",
      })
      .addTo(proposed);
  });
}

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

function loadStatic() {
  // Nodes and factory shapes change only when the player builds, so they are refetched
  // on a world switch rather than on every save write.
  get("/api/nodes").then(drawNodes).catch(function (e) {
    fail("nodes: " + e.message);
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

loadWorlds().then(function () {
  loadStatic();
  loadLive();
  listen();
});
