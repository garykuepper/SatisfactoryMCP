/* The fetch layer: one function, and the two query parameters every endpoint takes.
 *
 * Worth its own module because `world` and `save` are not per-call arguments -- they are the
 * page's current selection, and a caller that spelled them itself would be a caller that can
 * drift from the picker. The two loaders that deliberately do NOT go through here fetch
 * geography and the world list, neither of which has a world to be scoped to; they call
 * `fetch` directly and say why where they do it.
 */

import { state } from "./state";

export function get(path) {
  var q = "";
  var sep = path.indexOf("?") < 0 ? "?" : "&";
  if (state.world) {
    q += sep + "world=" + encodeURIComponent(state.world);
    sep = "&";
  }
  if (state.save) {
    q += sep + "save=" + encodeURIComponent(state.save);
  }
  return fetch(path + q).then(function (r) {
    return r.json().then(function (body) {
      if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
      return body;
    });
  });
}
