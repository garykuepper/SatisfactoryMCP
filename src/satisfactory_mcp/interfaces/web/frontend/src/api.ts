/* The fetch layer: one function, and the two query parameters every endpoint takes.
 *
 * Worth its own module because `world` and `save` are not per-call arguments -- they are the
 * page's current selection, and a caller that spelled them itself would be a caller that can
 * drift from the picker. The two loaders that deliberately do NOT go through here fetch
 * geography and the world list, neither of which has a world to be scoped to; they call
 * `fetch` directly and say why where they do it.
 *
 * `get` is generic over the response, and the type is supplied by the caller because the
 * server cannot supply it: every endpoint in `api.py` is annotated `-> dict`, so the
 * generated schema says `unknown` for all sixteen. What IS taken from the generated schema
 * is the path -- `ApiPath` below is the union of the paths the server actually serves, so a
 * typo in a URL is a compile error rather than a toast at runtime. See api-types.ts for
 * where the response shapes come from and why they are the frontend's claim.
 */

import type { paths } from "./api-schema";
import type { ApiError } from "./api-types";
import { state } from "./state";

/** Every path the server serves, straight out of its own OpenAPI document. */
export type ApiPath = keyof paths;

/* A path plus a query string, which is what two callers pass: `/api/inspect` takes a
 * coordinate and `/api/collectibles` takes a mode, and both are spelled inline at the call
 * site rather than plumbed through here. The template keeps the path half checked. */
type ApiUrl = ApiPath | `${ApiPath}?${string}`;

export function get<T extends ApiError>(path: ApiUrl): Promise<T> {
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
    return r.json().then(function (body: T) {
      if (!r.ok || body.error) throw new Error(body.error || r.status + " " + path);
      return body;
    });
  });
}
