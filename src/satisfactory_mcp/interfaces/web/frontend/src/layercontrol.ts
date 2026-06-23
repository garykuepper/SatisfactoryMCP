/* The layer control: the map's legend, its only filter, and the two folds that make
 * thirty-three rows fit on a laptop.
 *
 * The largest module here, and it stays one file because it is one widget: the folds, the
 * tri-state family boxes, the focus that has to survive Leaflet emptying the list, and the
 * batching that stops fourteen layer events becoming twenty-eight renders are four halves of
 * the same problem, and each of them is written the way it is because of the other three.
 *
 * It knows about layers and nothing about what is drawn in them. `onSettled` is why -- see
 * the note there.
 */

import { esc } from "./dom";
import { L } from "./leaflet";
import { HOME_VIEW, map } from "./map";
import { state } from "./state";

import type { LayerInput, SectionPart } from "./leaflet-private";

export var control = L.control.layers(
  undefined,
  {},
  {
    collapsed: false,
    sortLayers: true,
    sortFunction: function (a: L.Layer, b: L.Layer) {
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
/** One data-driven family of control rows, folded together and toggled together. */
interface Section {
  key: string;
  prefix: string;
  title: string;
}

var SECTIONS: Section[] = [
  { key: "nodes", prefix: "node: ", title: "resource nodes" },
  { key: "pickups", prefix: "pickup: ", title: "pickups" },
];

state.panel = { open: true, sections: { nodes: false, pickups: false } };

function sectionFor(name: string): Section | null {
  var found: Section | null = null;
  SECTIONS.forEach(function (section) {
    if (name.indexOf(section.prefix) === 0) found = section;
  });
  return found;
}

/* A control row back to the layer it toggles. Leaflet stamps the layer's id onto the
 * checkbox it builds, and layer() files the name under that same stamp, so the mapping
 * survives every re-render of the list without parsing the row's text back. */
function rowName(row: HTMLElement): string {
  var input = row.querySelector<LayerInput>("input");
  return (input && state.layerName[input.layerId]) || "";
}

function rowOn(row: HTMLElement): boolean {
  var input = row.querySelector("input");
  return !!(input && input.checked);
}

/* A control row back to the LayerGroup itself, for the one caller that has to toggle a
 * layer without a human clicking its box -- see setSection. */
function rowLayer(row: HTMLElement): L.LayerGroup | null {
  var input = row.querySelector<LayerInput>("input");
  return (input && state.layers[state.layerName[input.layerId]!]) || null;
}

function fold(element: HTMLElement | null, folded: boolean): void {
  if (!element) return;
  if (folded) L.DomUtil.addClass(element, "layer-folded");
  else L.DomUtil.removeClass(element, "layer-folded");
}

function foldHead(
  element: HTMLElement,
  open: boolean,
  title: string,
  count: number,
  total: number
): void {
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
function onActivate(element: HTMLElement, action: () => void): void {
  L.DomEvent.on(element, "click", function (event) {
    L.DomEvent.stop(event);
    action();
  });
  L.DomEvent.on(element, "keydown", function (event) {
    var key = (event as KeyboardEvent).key;
    if (key !== "Enter" && key !== " ") return;
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

/* Read through a function rather than exported as a variable, because the one caller
 * outside this file -- the label declutter pass, which is heavier than a control render and
 * has the same reason to run once -- needs the value at the moment it asks, not the value at
 * the moment it imported. */
export function isBatching() {
  return batching;
}

/* What runs once a batched change has settled, registered rather than imported.
 *
 * `batch()` used to end by calling `declutter()` by name. That is the right thing to happen
 * and the wrong way round to say it: it makes the layer control import the module that draws
 * factory labels, which imports the module that creates layers, which imports this one --
 * three files in a ring to express "the list has stopped changing". The control's claim is
 * only that; who cares about it is main.ts's business, and main.ts registers the pass. */
var settled: Array<() => void> = [];

export function onSettled(pass: () => void): void {
  settled.push(pass);
}

function batch(action: () => void): void {
  batching = true;
  control._handlingClick = true;
  try {
    action();
  } finally {
    control._handlingClick = false;
    batching = false;
  }
  control._update(); // one render, which re-runs decorateControl with the settled state
  settled.forEach(function (pass) {
    pass();
  });
}

/* Every layer of one family at once. The layers are toggled directly rather than by
 * clicking their boxes: Leaflet's own `_onInputClick` would do the adding, but it ends by
 * calling `_refocusOnMap`, and a keyboard user who just pressed Space on the family box
 * would find focus on the map. */
function setSection(rows: HTMLElement[], on: boolean): void {
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
function sectionBox(section: Section, rows: HTMLElement[]): HTMLInputElement {
  var on = rows.filter(rowOn).length;
  var box = L.DomUtil.create("input", "layer-section-box") as HTMLInputElement & SectionPart;
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
function sectionHead(section: Section, rows: HTMLElement[]): HTMLElement {
  var head = L.DomUtil.create("div", "layer-section");
  head.appendChild(sectionBox(section, rows));
  var text = L.DomUtil.create("span", "layer-fold", head) as HTMLSpanElement & SectionPart;
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
function panelHead(rows: HTMLElement[]): HTMLElement {
  var container = control.getContainer()!;
  var head = container.querySelector<HTMLElement>(".layers-head");
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
/** Which half of which section head held the keyboard, as a value, not an element. */
interface FocusMark {
  key: string;
  part: "box" | "fold" | undefined;
}

function focusMark(): FocusMark | null {
  var active = document.activeElement as SectionPart | null;
  return active && active._section ? { key: active._section, part: active._part } : null;
}

var pendingFocus: FocusMark | null = null;

/* Re-applied after every render of the list, and idempotent: Leaflet empties the overlay
 * list on each `_update`, so the section heads are rebuilt rather than moved. */
function decorateControl(): void {
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
  var heads: Element[] = Array.prototype.slice.call(list.querySelectorAll(".layer-section"));
  heads.forEach(function (head) {
    head.parentNode!.removeChild(head);
  });
  var rows: HTMLElement[] = Array.prototype.slice.call(list.querySelectorAll("label"));
  var grouped: Record<string, HTMLElement[]> = {};
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
    list!.insertBefore(head, members[0]!);
    if (focused && focused.key === section.key) {
      var again = head.querySelector<HTMLElement>(
        focused.part === "box" ? ".layer-section-box" : ".layer-fold"
      );
      if (again) again.focus();
    }
  });
  panelHead(rows);
  fold(container.querySelector<HTMLElement>(".leaflet-control-layers-list"), !state.panel.open);
}

(function () {
  var update = control._update;
  control._update = function (this: L.Control.Layers, ...args: unknown[]) {
    pendingFocus = focusMark() || pendingFocus; // before the wipe; see focusMark
    var result = (update as (...a: unknown[]) => unknown).apply(this, args);
    decorateControl();
    return result as void;
  };
  // A checkbox click does not re-render the list, so the "n of m" counts would go stale
  // the moment anyone used the thing they are counting.
  map.on("overlayadd overlayremove", decorateControl);
  decorateControl();
})();

/* Flying to a factory label is one click; getting back out was zoom-out spam. One
 * house-shaped button under the zoom control reframes the whole world. */
(function () {
  var home = new L.Control({ position: "topleft" });
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
