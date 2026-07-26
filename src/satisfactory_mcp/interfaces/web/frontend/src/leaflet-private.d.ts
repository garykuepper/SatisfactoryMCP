/* The fields this page hangs off Leaflet objects, declared so that `L` can be typed.
 *
 * Two different things are in here and it is worth keeping them apart.
 *
 * THIS PAGE'S OWN MARKS. `_rank`, `_chevron`, `_labelWeight`, `_section`, `_part` and
 * `_inspected` are not Leaflet's -- they are set by this code, on objects Leaflet owns,
 * because the alternative is a parallel WeakMap per mark and a lookup at every read. They
 * are named with a leading underscore for the same reason they always were: to say at the
 * point of use that the field is not part of the object's published surface. Optional, all
 * of them, because a Leaflet object that never passed through the code that sets one does
 * not have it.
 *
 * LEAFLET'S OWN INTERNALS. `_handlingClick`, `_update` and `layerId` are real Leaflet, just
 * not published in `@types/leaflet`, and the page uses them deliberately: `_handlingClick`
 * is the library's own flag for "I am mid-batch, do not re-render", `_update` is how a
 * decorator forces the one render at the end, and `layerId` is the stamp Leaflet writes onto
 * the checkbox it builds, which is what maps a control row back to its layer. Declaring them
 * is a statement that this page is coupled to Leaflet 1.9.4's internals in exactly three
 * places -- which is a thing worth being able to grep for before an upgrade.
 */

import type * as L from "leaflet";
import "leaflet";

/* What the floor view needs to know about one drawn piece, hung on the piece.
 *
 * A mark rather than a lookup table, for the reason every other mark here is one: the filter
 * walks tens of thousands of drawn pieces and the alternative is a WeakMap probe apiece. What
 * is in it is the JOIN, never the answer -- which floor a piece is on is `/api/floors`'
 * business, and this is only enough to ask it.
 *
 * Every field is optional and the four are alternatives, because the four layers this filters
 * are joined four different ways: a machine by its instance id, a run by the chain or row
 * `/api/floors` keys it with, a foundation piece by its position in `/api/structures` (the
 * only name a lightweight buildable has), and the two layers the decomposition says nothing
 * about at all by where they stand. */
export interface FloorMark {
  /** An instance leaf: how a band lists its machines and its belt attachments. */
  id?: string;
  /** A belt CHAIN or a pipe row, and which of the two number spaces it is in. */
  run?: { kind: "belt" | "pipe"; key: number };
  /** The two ends of this piece in game metres, so a connector's glyph can be put on the
   *  end that is actually on this floor. `[x, y, z]`, the payload's own order. */
  ends?: [import("./geometry").Point3M, import("./geometry").Point3M];
  /** A piece's position in `/api/structures`, which is what `deck_rows` indexes. */
  row?: number;
  /** Where it stands, in game metres. For storage, which no band lists, and for the
   *  height a machine occupies above its own deck. */
  x_m?: number;
  y_m?: number;
  z_m?: number;
  /** How tall it is, from the same clearance box as its footprint. Absent where the docs
   *  dump carries none, which is where no claim about piercing a ceiling can be made. */
  h_m?: number | null;
}

declare module "leaflet" {
  interface Layer {
    /** The ROW RANK: where this layer's row sits in the control, as [band, slot, name].
     *  Declared at the `layer()` call that creates the group; see BAND in layers.ts, and
     *  the note there on why this is neither the fetch rank nor draw order. */
    _rank?: [number, number, string];
    /** What the floor filter joins this piece by. See FloorMark. */
    _floor?: FloorMark;
    /** Everything a LayerGroup held before the floor filter took some of it away.
     *
     * On the GROUP, not on a piece: the filter replaces a group's contents and leaving is
     * putting them back, so the undo has to live where the contents do. Cleared by
     * `layer()` along with the contents themselves -- a snapshot of data that has been
     * refetched is a claim about a world that is gone. */
    _floorAll?: L.Layer[];
  }

  interface Path {
    /** A direction mark rather than a route: styled by opacity, never by weight. */
    _chevron?: boolean;
    /** A glyph whose radius is a fixed pixel size rather than one derived from the scale.
     *
     * Set on the power poles, which are marks in the node dots' grammar and not objects drawn
     * at their footprint. Read twice in routes.ts: styleRoutes leaves such a piece's radius
     * alone, and sinkRoutes puts it above the runs it terminates rather than under them. */
    _fixed?: boolean;
    /** How this path was drawn before it was ghosted, so unghosting is exact rather than
     *  a second guess at the drawing module's own options. Its presence IS "this path is
     *  ghosted right now". See ghost() in floors.ts. */
    _floorStyle?: L.PathOptions;
    /** ...and the CONTENT of the card it was carrying, for the same reason and at the same
     *  time: a ghost says something different about the same machine, and a machine that
     *  stops being a ghost must stop saying it.
     *
     *  The content and not the popup, and that is the whole of the bug this fixes: Leaflet's
     *  `bindPopup` REUSES an existing `L.Popup` when it is handed a string, so keeping the
     *  popup object keeps a reference to the very thing the ghost is about to overwrite.
     *
     *  Narrower than Leaflet's own `Content`, which also allows a FUNCTION of the layer:
     *  every popup on this page is a string built by `popup()` or -- for the factory card --
     *  an element, and declaring a case the page cannot produce would put an untestable
     *  branch in the one place that has to put a card back exactly as it found it. */
    _floorCard?: string | HTMLElement | null;
    /** The route this polyline was tessellated FROM, kept so it can be tessellated again.
     *
     * A curved route is drawn at whatever subdivision the current scale earns, so the piece
     * has to remember the curve it came from -- the drawn latlngs are an output and cannot be
     * re-subdivided from themselves. See routeShape and styleRoutes in routes.ts. */
    _route?: import("./geometry").RouteShape;
  }

  interface Marker {
    /** Declutter priority. A factory's machine count: big factories win. */
    _labelWeight?: number;
  }

  namespace Control {
    interface Layers {
      /** Leaflet's own re-render suppressor, borrowed by batch(). */
      _handlingClick: boolean;
      /** Leaflet's own list render. Wrapped by this page, and called once per batch. */
      _update(): void;
    }
  }
}

/* Leaflet's own "am I on a map", which `@types/leaflet` declares `protected` on `Layer`
 * and this page reads from outside. It cannot go in the augmentation above -- redeclaring a
 * protected member as public is an error -- so it is a view type, applied at the one place
 * that asks. `map.hasLayer` is NOT the same question: these layers are inside a LayerGroup,
 * so the map's own registry holds the group and not them. */
export interface OnMap {
  _map?: L.Map;
}

/** A checkbox Leaflet built for a control row: it carries the layer's stamp. */
export interface LayerInput extends HTMLInputElement {
  layerId: number;
}

/** Half of a section head: which family it belongs to, and which of its two controls it is. */
export interface SectionPart extends HTMLElement {
  _section?: string;
  _part?: "box" | "fold";
}

/** A DOM mouse event that has already opened an inspector card. See inspect(). */
export interface InspectedEvent extends MouseEvent {
  _inspected?: boolean;
}
