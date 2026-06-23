/* Factory labels: the map's index, the click that flies to one, and the pass that stops a
 * pile of them from being a broken index.
 *
 * `reveal` is here rather than with the layers because it is not a fact about layers -- it is
 * what the one gesture that changes the SCALE means, and the only gesture that does is a
 * click on a label. Keeping the two together is what stops "show me this factory" from
 * drifting away from the layers a factory is made of.
 */

import { code, esc, popup } from "./dom";
import { L } from "./leaflet";
import { layer } from "./layers";
import { map } from "./map";
import { state } from "./state";
import { note } from "./toast";

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
export function reveal(names) {
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
      // A factory at factory scale IS its machines and the routes between them; see reveal above.
      reveal(FACTORY_LAYERS);
      map.flyToBounds(bounds, { maxZoom: FACTORY_MAX_ZOOM });
    });
  }
  return marker;
}

export function drawFactories(data) {
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
export function declutter() {
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
