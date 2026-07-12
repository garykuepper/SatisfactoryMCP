/* Factory labels: the map's index, the click that flies to one, and the pass that stops a
 * pile of them from being a broken index.
 *
 * `reveal` is here rather than with the layers because it is not a fact about layers -- it is
 * what the one gesture that changes the SCALE means, and the only gesture that does is a
 * click on a label. Keeping the two together is what stops "show me this factory" from
 * drifting away from the layers a factory is made of.
 */

import { code, esc, popup } from "./dom";
import { batch } from "./layercontrol";
import { L } from "./leaflet";
import { layer } from "./layers";
import { map } from "./map";
import { state } from "./state";
import { note } from "./toast";

import type { BboxM, FactoriesResponse, FactoryRow, PointM, ProposalRow } from "./api-types";
import type { Row } from "./dom";

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
 * events, and the player made one gesture.
 *
 * ...and ONE RENDER for the whole set, which is what `batch` is doing here. Three
 * `addTo(map)` calls outside it are three `overlayadd` events, and every one of them
 * re-rendered the layer control (twice: Leaflet's own `_onLayerChange` and this page's
 * decorator) and re-ran the declutter pass over every label on the map. Six renders and three
 * full-layout passes, for one click, to reach a state that could be described once -- and the
 * two intermediate declutters were measuring labels against a half-revealed map, so the "+n"
 * badges were computed twice from views nobody was ever shown. `batch` ends with a single
 * render and the settled passes, which is where the one declutter this gesture owes belongs. */
export function reveal(names: string[]): void {
  var turned: string[] = [];
  batch(function () {
    names.forEach(function (name) {
      var group = state.layers[name];
      if (!group || map.hasLayer(group)) return;
      group.addTo(map);
      turned.push(name);
    });
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

/* What "show me this factory" means, in layers. A factory at factory scale is its machines
 * and the routes between them -- belts AND pipes, because a refinery block is half plumbing
 * and a view that showed only the belts would read as a factory with pieces missing. All
 * three are unreadable at the zoom the click starts from.
 *
 * STORAGE IS DELIBERATELY NOT THE FOURTH, and the reason is not the mechanics.
 *
 * The mechanics were checked first, because they were the obvious thing to be blocked by and
 * they do not block: reveal() builds its list with `slice(0, -1).join(", ") + " and " + last`,
 * so four names come out as "machines, belts, pipes and storage" -- a correct list, not the
 * "a and b and c" a plain join would give -- and the sentence after it already says "those
 * layers" for any count above one. The grammar scales.
 *
 * The reason is what the layer MEANS. These three are what a factory is made of: take the
 * belts away and the machines are a scatter of rectangles, take the pipes away and a refinery
 * block is half missing. Containers are not what a factory is made of -- they are what is
 * standing in it, and "where is my steel" is a question a player asks on purpose rather than
 * one implied by "show me this factory". The owner asked for this as a TOGGLE, and a layer
 * that four other gestures turn on for you is not one.
 *
 * There is a cost argument too and it is the weaker one, so it is second: revealing a fourth
 * layer means a click the reader did not make changes four things, and the toast that has to
 * list them gets longer than the note it is trying to be.
 *
 * POWER IS NOT THE FOURTH EITHER, and for the opposite reason to storage's: it is already on.
 * `reveal` turns on what is off, so a layer that starts ticked would either be a no-op here or
 * -- in the one case where it is not, a reader who unticked it -- would re-tick a box the
 * reader had just turned off, which is the one thing this function must never do. The wires
 * are on at world scale because unlike the three above they READ at world scale: 1,297 lines
 * with a median span of 21 m draw the spine joining this world's bases, where 3,588 routes and
 * 438 rectangles draw a smear. See drawPower in power.ts.
 */
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

function anchorMarker(centroid_m: PointM): L.Marker {
  // divIcon, not the default icon: no image request, and iconSize [0,0] means the anchor
  // occupies no pointer area at all. The tooltip is the whole visible and clickable body.
  return L.marker([-centroid_m[1], centroid_m[0]], {
    icon: L.divIcon({ className: "factory-anchor", iconSize: [0, 0] }),
  });
}

/* A server bbox_m ([x_min, y_min, x_max, y_max], game axes) as Leaflet bounds. The y ends
 * swap, exactly as they do for the biome cells, because latitude is -y. */
function factoryBounds(bbox_m: BboxM | null | undefined): L.LatLngBounds | null {
  if (!bbox_m) return null;
  return L.latLngBounds(
    [-(bbox_m[3] + FACTORY_PAD_M), bbox_m[0] - FACTORY_PAD_M],
    [-(bbox_m[1] - FACTORY_PAD_M), bbox_m[2] + FACTORY_PAD_M]
  );
}

function factoryAnchor(
  row: FactoryRow | ProposalRow,
  text: string,
  className: string,
  rows: Row[]
): L.Marker {
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
    var to = bounds;
    marker.on("click", function () {
      // A factory at factory scale IS its machines and the routes between them; see reveal above.
      reveal(FACTORY_LAYERS);
      map.flyToBounds(to, { maxZoom: FACTORY_MAX_ZOOM });
    });
  }
  return marker;
}

export function drawFactories(data: FactoriesResponse): void {
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
/* One label, measured. `node` is the tooltip's own element -- the thing with a screen
 * rectangle -- and `marker` is what a badge click has to fly to. */
interface Entry {
  node: HTMLElement;
  marker: L.Marker;
  rank: number;
  weight: number;
}

/** A label that survived the pass, its rectangle, and everything it covered. */
interface Kept {
  rect: DOMRect;
  entry: Entry;
  hidden: Entry[];
}

/** One label and the rectangle it was measured at, before anything was hidden. */
interface Measured {
  entry: Entry;
  rect: DOMRect;
}

/* READ EVERYTHING, THEN WRITE, and the two halves below are separated for that alone.
 *
 * `getBoundingClientRect` is a synchronous question about layout, so it returns the geometry
 * the browser would draw right now -- which means it cannot be answered while a style change
 * is pending. Deciding and hiding inside the measuring loop therefore made every rectangle
 * after the first hidden label cost a forced reflow: the previous iteration's
 * `display: none` invalidated layout, and the next `getBoundingClientRect` had to flush it.
 *
 * On this world that is up to one reflow per label on every zoomend -- the pass runs on every
 * zoom step and on every layer tick, and at the home view eight of fifteen labels are hidden,
 * so eight of the fifteen reads were paying for the seven writes before them. Measuring all
 * fifteen first costs exactly one flush (the class reset above it) and answers the same
 * question with the same numbers: nothing in the overlap test depends on what the loop has
 * already hidden, because a hidden label is never a cover -- only `kept` is, and `kept` holds
 * the rectangles measured here. */
function measureAll(entries: Entry[]): Measured[] {
  return entries.map(function (entry): Measured {
    return { entry: entry, rect: entry.node.getBoundingClientRect() };
  });
}

export function declutter(): void {
  var entries: Entry[] = [];
  ["factory labels", "proposals"].forEach(function (name, groupRank) {
    var group = state.layers[name];
    if (!group || !map.hasLayer(group)) return;
    group.eachLayer(function (layer) {
      var marker = layer as L.Marker;
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
    if (old) old.parentNode!.removeChild(old);
  });
  entries.sort(function (a, b) {
    return a.rank - b.rank || b.weight - a.weight;
  });
  // Sorted before measuring, so `kept` is built in rank order and `find` below can stop at
  // the first overlap; measured before deciding, so the decisions cost no layout. See above.
  var measured = measureAll(entries);
  var kept: Kept[] = [];
  measured.forEach(function (m) {
    var r = m.rect;
    // `find`, because the highest-ranked cover owns the badge and `kept` is already in rank
    // order: it stops at the first overlap, which is what the loop this replaced achieved by
    // testing a flag on every later element and assigning to none of them.
    var covered = kept.find(function (k) {
      var b = k.rect;
      return r.left < b.right && b.left < r.right && r.top < b.bottom && b.top < r.bottom;
    });
    if (covered) {
      L.DomUtil.addClass(m.entry.node, "label-hidden");
      covered.hidden.push(m.entry);
    } else {
      kept.push({ rect: r, entry: m.entry, hidden: [] });
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

function badgeHidden(entry: Entry, hidden: Entry[]): void {
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
