/* Leaflet, imported once so that "which Leaflet, and how typed" has one answer.
 *
 * Every other module takes `L` from here rather than from the package. It is the real
 * namespace with the real types now -- the `any` the port shipped is gone -- which is worth
 * the one indirection: the private fields this page hangs off Leaflet objects are declared
 * in `leaflet-private.d.ts`, once, and the six places that use Leaflet internals are
 * greppable rather than hidden inside an `any`.
 *
 * The npm package is pinned to 1.9.4, which is the version of the `vendor/leaflet.js` it
 * replaced, so what runs is the same library and not a newer one arriving unannounced.
 */

import * as Leaflet from "leaflet";

/* `leaflet-private.d.ts` sits beside this file and is NOT imported: it is a declaration
 * file inside the program's `include`, so its `declare module "leaflet"` augmentation
 * applies everywhere without anyone naming it -- and importing it would ask Rollup to
 * bundle a file that compiles to nothing, which it rightly refuses to do. */
export const L = Leaflet;
