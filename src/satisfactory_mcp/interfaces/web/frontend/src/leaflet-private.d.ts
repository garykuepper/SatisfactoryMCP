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

declare module "leaflet" {
  interface Layer {
    /** Sort key for the control's row order: [group, index, name]. See layerRank. */
    _rank?: [number, number, string];
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
    /** The route this polyline was tessellated FROM, kept so it can be tessellated again.
     *
     * A curved route is drawn at whatever subdivision the current scale earns, so the piece
     * has to remember the curve it came from -- the drawn latlngs are an output and cannot be
     * re-subdivided from themselves. See routeShape and styleRoutes in routes.ts. */
    _route?: import("./api-types").RouteShape;
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
