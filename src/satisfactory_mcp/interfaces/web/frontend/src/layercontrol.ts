/* The layer control: the map's legend, its only filter, and the two folds that make
 * thirty-five rows fit on a laptop.
 *
 * The largest module here, and it stays one file because it is one widget: the folds, the
 * tri-state family boxes, the focus that has to survive Leaflet emptying the list, and the
 * batching that stops fourteen layer events becoming twenty-eight renders are four halves of
 * the same problem, and each of them is written the way it is because of the other three.
 *
 * Above all of that sits the MODE section, and it is deliberately a different kind of thing:
 * radios, not checkboxes. "Which picture is the base map" is one question with one answer,
 * and "what is drawn on top of it" is a couple of dozen independent ones -- so the two are
 * different gestures with a rule between them, rather than one more checkbox that happens to
 * turn the others off. That is the split Google Earth makes, and it is the reason a mode
 * switch leaves every overlay exactly as the player left it.
 *
 * It knows about layers and nothing about what is drawn in them, and about modes without
 * knowing what a tile pyramid is. `onSettled` and `onModePick` are why -- see the notes
 * there.
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
    /* The ROW RANK, and the only thing on the page that reads one: [band, slot, name],
     * stamped onto every group by `layer()`. Read when this control rebuilds its list and
     * nowhere else -- it is not draw order, and layers.ts says why at length.
     *
     * `[9, 0, ""]` is `BAND.unknown` written out rather than imported, because layers.ts
     * imports THIS file and importing it back would be the ring the ratchet next door
     * forbids. It is also unreachable through `layer()`, which supplies the same band
     * itself: what it defends against is a group that reached the control another way. */
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

/* Thirty-five overlay rows on the reference world, plus the four modes above them --
 * 306x700 px fully open, 13% of a 1600x1000 viewport and a great deal more of a laptop --
 * permanently, because the control was built with `collapsed: false` and nothing else could
 * fold it. (Thirty-three when this was written; the pickup families grew.)
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
 *     world and that turned a nine-row legend into thirty-five. Their heads carry the
 *     same "n of m" count, which is what lets a folded section still answer "are the ore
 *     dots on?" without unfolding it.
 *   * The MODE head folds the four base-map radios, and starts OPEN: it is four rows that
 *     never grow, and it is the row that answers "why is the map dark?". Its tail is the
 *     active mode's name rather than a count, because one of four is always the answer.
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

/** The MODE section's fold, kept in the same map as the families' so one mechanism folds
 *  all three -- see renderModes for why it is the only section built from a list rather
 *  than discovered from the rows Leaflet drew. */
var MODE_SECTION = "modes";

/** The radio group's name, which is the whole of what makes the four exclusive. */
var MODE_GROUP = "basemap-mode";

state.panel = {
  open: true,
  // `floors` starts open and normally has no section to open: the picker exists only while
  // the page is slicing a factory, and arriving there is a gesture that should show it.
  sections: { floors: true, modes: true, nodes: false, pickups: false },
};

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

/* A fold head, in the one grammar all three of them share: a caret, a title, and a tail at
 * the right edge that says what is inside without opening it.
 *
 * The tail is a STRING rather than the "n of m" it started as, because the MODE head's
 * answer is not a count: one of four is always chosen, so "1 of 4" would be a fact that is
 * true forever and says nothing, where the mode's own name is the whole answer. `note` is
 * the same sentence for the tooltip, which each caller phrases for itself -- "drawn right
 * now" is true of a family of layers and false of a radio. */
function foldHead(
  element: HTMLElement,
  // `boolean | undefined`, because a section key that is not in `state.panel.sections`
  // yet is a section nobody has folded, and that reads as closed here exactly as `false`
  // does. Narrowing this to `boolean` would push a `?? false` up to both callers to say
  // the same thing twice.
  open: boolean | undefined,
  title: string,
  tail: string,
  note: string
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
    esc(tail) +
    "</span>";
  element.title = (open ? "hide " : "show ") + title + " — " + note;
}

/** What both counting heads put in their tail and their tooltip. */
function drawnOf(count: number, total: number): string {
  return count + " of " + total;
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

/* Exported for the one caller outside this file that also changes several layers in one
 * gesture: `reveal` in labels.ts, which turns three layers on for a single click on a factory
 * label. It was doing that outside this guard, so a click cost six control renders and three
 * declutter passes to reach one answer -- the same fourteen-for-one arithmetic the family box
 * above was fixed for, at a fifth of the scale and on the page's most-used gesture.
 *
 * A function rather than a "please batch" flag, because the end of a batch is not just "stop
 * suppressing": it is one `_update` and then the settled passes, and a caller that had to
 * remember both would eventually remember one. */
export function batch(action: () => void): void {
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
  var tail = drawnOf(rows.filter(rowOn).length, rows.length);
  foldHead(text, open, section.title, tail, tail + " drawn right now");
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
 * pickup families off), and one click that unticked all 35 would throw that selection away.
 * Re-ticking would not restore it -- it would turn all 35 ON, which is a different map than
 * the one the player had. So the one gesture whose undo does not undo is the one gesture
 * this head does not offer.
 *
 * The MODE radios are not counted here and never were candidates for it: they are not
 * overlays, they live outside the list this head measures, and "how many of four modes are
 * drawn" has one answer forever. */
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
  var tail = drawnOf(rows.filter(rowOn).length, rows.length);
  foldHead(head, state.panel.open, "layers", tail, tail + " drawn right now");
  fold(head, false);
  if (state.panel.open) L.DomUtil.removeClass(head, "shut");
  else L.DomUtil.addClass(head, "shut");
  return head;
}

/* ------------------------------------------------------------------- the modes */

/** One row of the MODE section: a radio, and why it can or cannot be picked. */
export interface ModeChoice {
  key: string;
  label: string;
  /** false greys the row out; `note` then says which generator would fill it. */
  ready: boolean;
  /** The row's tooltip either way: what this picture is, or what would draw it. */
  note: string;
}

var choices: ModeChoice[] = [];
var activeMode = "";

/* Who to tell when a radio is picked, registered rather than imported -- the same seam as
 * `onSettled` above, and here for the same reason: this control draws the modes and must
 * not know what a tile pyramid is. Importing tiles.ts to call it would also close a ring,
 * because tiles.ts reaches this file through layers.ts already.
 *
 * One owner rather than a list, unlike `onSettled`: "which picture is the base map" has a
 * single answer applied in a single place, and a second listener could only disagree. */
var pickMode: (key: string) => void = function () {};

export function onModePick(pick: (key: string) => void): void {
  pickMode = pick;
}

/** The modes as they now stand, and which one is drawn. Called once when the probes land
 *  and again on every switch -- including the one a failed tile forces. */
export function showModes(rows: ModeChoice[], active: string): void {
  choices = rows;
  activeMode = active;
  renderModes();
}

/* The MODE section is built ONCE and updated in place, which is the opposite of how the
 * family heads above are handled -- and the difference is what the rows are.
 *
 * A family head is rebuilt on every render because Leaflet empties the overlays list it
 * lives in, and decorateControl therefore has to carry keyboard focus across the wipe by
 * hand. These rows are real form controls in a radio group: rebuilding them would drop the
 * keyboard mid-arrow-walk and reset the group's roving tabindex on every switch, for four
 * rows whose set never changes after the probes land. So they are put somewhere `_update`
 * does not reach -- a child of the list ELEMENT, ahead of Leaflet's own base and overlay
 * divs, which are the only two things it empties -- and only their state is written.
 *
 * Inside the list rather than beside it, so the page's one fold puts the modes away with
 * everything else: a folded control is one strip, not one strip and four radios. */
var modeBox: HTMLElement | null = null;
var modeHead: HTMLElement | null = null;
var modeRows: Record<string, HTMLElement> = {};
var modeInputs: Record<string, HTMLInputElement> = {};
var modeBuilt = "";

function buildModes(list: HTMLElement): HTMLElement {
  var box = L.DomUtil.create("div", "layer-modes");
  // The rule under this box is what says "these four are one question" to a reader looking
  // at it; the group and its name are the same sentence for a reader who is not.
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "base map");
  var head = L.DomUtil.create("div", "layer-section", box);
  modeHead = L.DomUtil.create("span", "layer-fold", head);
  onActivate(modeHead, function () {
    state.panel.sections[MODE_SECTION] = !state.panel.sections[MODE_SECTION];
    renderModes();
  });
  modeRows = {};
  modeInputs = {};
  choices.forEach(function (choice) {
    // Leaflet's own row shape -- label > span > (input, span) -- so the radios line up in
    // the same column as the checkboxes below them instead of reading as a second indent.
    var row = L.DomUtil.create("label", "layer-mode", box);
    var holder = L.DomUtil.create("span", "", row);
    var input = L.DomUtil.create("input", "", holder) as HTMLInputElement;
    input.type = "radio";
    input.name = MODE_GROUP; // the whole of what makes the four exclusive
    input.value = choice.key;
    var text = L.DomUtil.create("span", "", holder);
    text.innerHTML = " " + esc(choice.label);
    // `change`, not `click`: inside a radio group an arrow key moves the selection, and
    // that is a pick like any other. A disabled radio fires neither, which is exactly what
    // greying a mode out is supposed to mean.
    L.DomEvent.on(input, "change", function () {
      if (input.checked) pickMode(choice.key);
    });
    modeRows[choice.key] = row;
    modeInputs[choice.key] = input;
  });
  list.insertBefore(box, list.firstChild);
  return box;
}

function modeContainer(): HTMLElement | null {
  var container = control.getContainer();
  if (!container) return null;
  var list = container.querySelector<HTMLElement>(".leaflet-control-layers-list");
  if (!list) return null;
  var keys = choices
    .map(function (choice) {
      return choice.key;
    })
    .join(",");
  if (modeBox && modeBox.parentNode === list && modeBuilt === keys) return modeBox;
  if (modeBox && modeBox.parentNode) modeBox.parentNode.removeChild(modeBox);
  modeBuilt = keys;
  modeBox = buildModes(list);
  return modeBox;
}

function renderModes(): void {
  if (!choices.length) return; // nothing probed yet: no section, rather than four dead rows
  var box = modeContainer();
  if (!box || !modeHead) return;
  var open = state.panel.sections[MODE_SECTION];
  var active = "";
  choices.forEach(function (choice) {
    var row = modeRows[choice.key];
    var input = modeInputs[choice.key];
    if (!row || !input) return;
    input.checked = choice.key === activeMode;
    // Disabled, not hidden: the row is where the page says which tool would draw this
    // picture, and a name that is simply absent asks nobody to go looking for it.
    input.disabled = !choice.ready;
    if (choice.ready) L.DomUtil.removeClass(row, "layer-mode-off");
    else L.DomUtil.addClass(row, "layer-mode-off");
    row.title = choice.note;
    fold(row, !open);
    if (choice.key === activeMode) active = choice.label;
  });
  foldHead(
    modeHead,
    open,
    "base map",
    active,
    active ? "showing " + active : "no base map chosen yet"
  );
}

/* ------------------------------------------------------------------ the floors */

/* The floor picker: the MODE radios' sibling, and deliberately not the same code.
 *
 * It is the same GRAMMAR -- one question with one answer, radios in a folded section at the
 * top of the control, drawn here and decided elsewhere through a registered callback -- and
 * that is why it lives in this file rather than in the module that knows what a storey is.
 * §18's split is the whole reason the modes are radios; which floor to look at is the same
 * kind of question about the same map.
 *
 * What it is not is another `showModes`, and the differences are all in what a row has to
 * say. A mode is a word; a floor is a word plus a measurement -- `+42.2 m · 175 cells · 35
 * machines` -- and a MINOR band has to read as subordinate to the storey it is a mezzanine
 * of, which is a class rather than a label. The section also carries two things the modes
 * have no use for: the name of what is being sliced, and a way out. Folding those into the
 * mode machinery would have meant four optional parameters on every call and two branches
 * in every row, to save a fold head and a radio group that are eight lines each.
 *
 * The section only exists while the page is in floor mode. `hideFloors` removes the box
 * outright rather than emptying it, because an empty floor picker over a world map is a
 * control asking a question that has no subject.
 */
/** One row of the FLOOR section: a storey, and what makes it worth picking. */
export interface FloorChoice {
  key: string;
  label: string;
  /** The measurement under the name: height, area, and what stands on it. */
  detail: string;
  /** A mezzanine rather than a storey, by its share of its platform's largest band. */
  minor: boolean;
  note: string;
}

var FLOOR_SECTION = "floors";
var FLOOR_GROUP = "floor-band";

var floorChoices: FloorChoice[] = [];
var floorTitle = "";
var floorActive = "";
/** What to say instead of rows when there are none. The API's own sentence, never a blank. */
var floorMessage = "";

var pickFloor: (key: string) => void = function () {};
var leaveFloor: () => void = function () {};

export function onFloorPick(pick: (key: string) => void): void {
  pickFloor = pick;
}

export function onFloorExit(leave: () => void): void {
  leaveFloor = leave;
}

var floorBox: HTMLElement | null = null;
var floorHead: HTMLElement | null = null;
var floorRows: Record<string, HTMLElement> = {};
var floorInputs: Record<string, HTMLInputElement> = {};
var floorBuilt = "";

/** The rows, the subject they are of, and which one is drawn. `message` replaces the rows
 *  when the answer is a sentence rather than a list -- a save too old to carry foundations,
 *  or a factory that stands on no deck at all. */
export function showFloors(
  title: string,
  rows: FloorChoice[],
  active: string,
  message: string
): void {
  floorTitle = title;
  floorChoices = rows;
  floorActive = active;
  floorMessage = message;
  state.panel.sections[FLOOR_SECTION] = true; // arriving in floor mode opens the picker
  renderFloors();
}

export function hideFloors(): void {
  floorChoices = [];
  floorMessage = "";
  floorBuilt = "";
  if (floorBox && floorBox.parentNode) floorBox.parentNode.removeChild(floorBox);
  floorBox = null;
  floorHead = null;
  floorRows = {};
  floorInputs = {};
}

function buildFloors(list: HTMLElement): HTMLElement {
  var box = L.DomUtil.create("div", "layer-floors");
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "floor");
  var head = L.DomUtil.create("div", "layer-section", box);
  floorHead = L.DomUtil.create("span", "layer-fold", head);
  onActivate(floorHead, function () {
    state.panel.sections[FLOOR_SECTION] = !state.panel.sections[FLOOR_SECTION];
    renderFloors();
  });
  // The way out, on the head itself: leaving is not one of the floors, so it must not be a
  // row in the group of them -- the same reason the family box is not a member of its own
  // family. ESC does the same thing and is not discoverable, which is why this is here too.
  var out = L.DomUtil.create("button", "layer-floor-exit", head) as HTMLButtonElement;
  out.type = "button";
  out.innerHTML = "&#10005;";
  out.title = "leave floor mode (Esc) — the whole world again";
  out.setAttribute("aria-label", "leave floor mode");
  L.DomEvent.on(out, "click", function (event) {
    L.DomEvent.stop(event);
    leaveFloor();
  });

  floorRows = {};
  floorInputs = {};
  if (floorMessage) {
    // A sentence, not an empty list. The two cases that reach here -- a save too old to
    // record foundations at all, and a factory standing on bare terrain -- are answers,
    // and an empty picker would read as a failure to load.
    var said = L.DomUtil.create("div", "layer-floor-note", box);
    said.textContent = floorMessage;
  }
  floorChoices.forEach(function (choice) {
    var row = L.DomUtil.create("label", "layer-floor", box);
    if (choice.minor) L.DomUtil.addClass(row, "layer-floor-minor");
    var holder = L.DomUtil.create("span", "", row);
    var input = L.DomUtil.create("input", "", holder) as HTMLInputElement;
    input.type = "radio";
    input.name = FLOOR_GROUP;
    input.value = choice.key;
    var text = L.DomUtil.create("span", "", holder);
    text.innerHTML =
      " " +
      esc(choice.label) +
      '<span class="layer-floor-detail">' +
      esc(choice.detail) +
      "</span>";
    // `change` rather than `click`, exactly as the modes do it: inside a radio group an
    // arrow key moves the selection and that is a pick like any other.
    L.DomEvent.on(input, "change", function () {
      if (input.checked) pickFloor(choice.key);
    });
    floorRows[choice.key] = row;
    floorInputs[choice.key] = input;
  });
  list.insertBefore(box, list.firstChild);
  return box;
}

function floorContainer(): HTMLElement | null {
  var container = control.getContainer();
  if (!container) return null;
  var list = container.querySelector<HTMLElement>(".leaflet-control-layers-list");
  if (!list) return null;
  var keys =
    floorTitle +
    "|" +
    floorMessage +
    "|" +
    floorChoices
      .map(function (choice) {
        return choice.key + ":" + choice.detail;
      })
      .join(",");
  if (floorBox && floorBox.parentNode === list && floorBuilt === keys) return floorBox;
  if (floorBox && floorBox.parentNode) floorBox.parentNode.removeChild(floorBox);
  floorBuilt = keys;
  floorBox = buildFloors(list);
  return floorBox;
}

function renderFloors(): void {
  if (!floorChoices.length && !floorMessage) return; // not in floor mode: no section at all
  var box = floorContainer();
  if (!box || !floorHead) return;
  var open = state.panel.sections[FLOOR_SECTION];
  var active = "";
  floorChoices.forEach(function (choice) {
    var row = floorRows[choice.key];
    var input = floorInputs[choice.key];
    if (!row || !input) return;
    input.checked = choice.key === floorActive;
    row.title = choice.note;
    fold(row, !open);
    if (choice.key === floorActive) active = choice.label;
  });
  var said = box.querySelector<HTMLElement>(".layer-floor-note");
  if (said) fold(said, !open);
  foldHead(
    floorHead,
    open,
    floorTitle,
    active,
    active ? "showing " + active : "nothing to show one floor of"
  );
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
  renderFloors();
  renderModes();
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
