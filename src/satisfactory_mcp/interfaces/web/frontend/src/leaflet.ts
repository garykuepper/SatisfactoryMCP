/* Leaflet, imported once so that "which Leaflet, and how typed" has one answer.
 *
 * Every other module takes `L` from here rather than from the package. That is what keeps
 * the cast below a single line instead of nineteen -- and what will make deleting it a
 * single line too, when the private fields this page hangs off Leaflet objects are declared
 * and the real types can be used.
 */

import * as Leaflet from "leaflet";

/* Leaflet, deliberately untyped for now.
 *
 * `@types/leaflet` is installed and the typed layer arrives with the API types; until then
 * this one cast is what keeps the port a MOVE. The page decorates Leaflet objects with its
 * own private fields throughout -- `_rank` on a LayerGroup, `_chevron` on a polyline,
 * `_labelWeight` on a marker, `_handlingClick` on the control -- and every one of those is
 * an error against the published types until it is declared. Declaring them belongs in the
 * commit that types things, not in the one that moves them.
 *
 * The npm package is pinned to 1.9.4, which is the version of the `vendor/leaflet.js` it
 * replaced, so what runs is the same library and not a newer one arriving unannounced. */
export const L: any = Leaflet;
