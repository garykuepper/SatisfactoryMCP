/* What the page fetches, declared by the module that draws it.
 *
 * load.ts used to hold the whole list: ten `get()` calls, ten epoch guards, ten catch blocks
 * naming ten layer prefixes, and an import of every drawing module on the page. That made it
 * the one file that knew everything -- readable, and also the file every new feature had to
 * be threaded through in three places. This registry inverts it. A feature says what it wants
 * fetched and what to do with the answer; load.ts runs the list and knows none of the names.
 *
 * THIS MODULE IMPORTS NOTHING AT RUNTIME, exactly like state.ts, and for the same reason:
 * everything that draws imports it, so anything it imported would be evaluated before all of
 * them. Both imports below are `import type`, which is erased.
 *
 * The registrations only exist if the modules holding them are in the bundle, and load.ts no
 * longer imports any of them -- so main.ts names each one in its FEATURES block, and
 * test_architecture.py checks that block against the set of modules that call `registerFetch`.
 * Without that pair, deleting a feature's last named import is a silent removal of its layer:
 * the build succeeds, the page loads, and the layer is simply never fetched.
 */

import type { ApiError, ApiUrl } from "./api";

/* The two waves, which are a claim about the DATA rather than about the code: nodes,
 * concrete and routes change when the player builds; machines, pickups and the header change
 * on every autosave. A save event refetches `live`, a world switch refetches both. */
export type Wave = "static" | "live";

export interface Fetcher<T extends ApiError> {
  wave: Wave;

  /* Where this fetch sits in its wave, spelled as a number rather than taken from the order
   * the modules happen to be imported in. Two things downstream are decided by which reply
   * lands first -- floor mode's once-only opening flight measures whatever deck is drawn when
   * it runs, and everything clickable shares one canvas where draw order is hit-testing -- so
   * "the order the requests went out in" is a decision, and a decision made by the import
   * graph is one nobody made. Tens, so a feature can be inserted without renumbering. */
  rank: number;

  /** The path, compile-checked against the server's own OpenAPI document; see ApiUrl. */
  path: ApiUrl;

  /** What the toast calls this on failure: "belts: …", "summary: …". */
  label: string;

  /* Layer-name PREFIXES to empty when this fetch fails, because a failed switch must leave
   * those layers empty rather than showing the previous world under the new world's header.
   * Prefixes and not names: `clearPrefixed` matches on indexOf === 0, so "node: " and
   * "pickup: " cover a family whose members are data, and their trailing space is load-bearing
   * -- "node:" would also match a layer called "node:-something-else". */
  clears: string[];

  /* Whether the floor filter runs again after this draw. A redraw replaces a layer's
   * CONTENTS and the filter is a fact about contents, so a layer refetched during floor mode
   * would otherwise arrive holding every storey at once.
   *
   * Stated per fetch rather than done for all of them, and the set is the one load.ts had:
   * everything except /api/power and /api/summary. It is not quite floors.ts's own FILTERED
   * list -- the node dots and the factory labels are not filtered either, yet they are in
   * here -- so this is history rather than a rule, which is exactly why it is written down
   * per entry instead of inferred. Turning the two false ones true would be safe (the pass
   * early-returns outside floor mode and is idempotent inside it) and would cost two more
   * full passes over seven layer groups per world switch, for nothing. */
  refilters: boolean;

  /** What to do with the body. The one place a response type is fixed; see `Registered`. */
  draw: (data: T) => void;

  /* Whether this fetch is the one that ends the switch: the dimmed map and the "loading…"
   * header are cleared when it settles, either way. Exactly one fetch may say so -- the
   * summary, because it is the reply that replaces the header text and there is nothing left
   * to mark as in progress once it has. */
  settles?: boolean;

  /* A hook after a successful draw, for the one thing that is not a draw: /api/machines has
   * to refresh the floor decomposition, because a save write changes what is BUILT and the
   * ids a band lists are what the filter runs on. */
  after?: () => void;

  /* ...and its mirror, run after the layers are cleared and before the toast, for the one
   * failure that has to say something outside a layer: the header's own text. */
  failed?: () => void;
}

/* The list is heterogeneous by construction -- ten response shapes with nothing in common but
 * the optional `error` field -- so what it stores is a fetcher whose response type has been
 * erased to that common part. The erasure happens once, here, at the moment of registration,
 * and the call site keeps the type it declared: `draw: drawNodes` fixes T to NodesResponse,
 * so a fetcher pointing /api/nodes at the pickup drawer is a compile error. */
export type Registered = Fetcher<ApiError>;

var entries: Registered[] = [];

export function registerFetch<T extends ApiError>(fetcher: Fetcher<T>): void {
  if (import.meta.env.DEV) {
    /* Two entries for one path would be two requests for one answer, which is the specific
     * thing that made splitting /api/summary interesting: its reply feeds the header AND the
     * player dot, and the obvious split -- one entry per consumer -- doubles a request on
     * every save event. header.ts owns both consumers instead. Said out loud in dev because
     * the symptom of getting it wrong is a duplicate line in the network panel and nothing
     * else at all. */
    var clash = entries.filter(function (other) {
      return other.path === fetcher.path;
    });
    if (clash.length) {
      console.error("two fetchers registered for " + fetcher.path + " — that is two requests");
    }
  }
  entries.push(fetcher as Registered);
}

/** One wave, in the order it is to be issued in. A copy: the caller iterates it while the
 *  draws it triggers are free to do anything at all. */
export function fetchersOf(wave: Wave): Registered[] {
  return entries
    .filter(function (fetcher) {
      return fetcher.wave === wave;
    })
    .sort(function (a, b) {
      return a.rank - b.rank;
    });
}

/** One fetch by path, for the caller that wants a single layer refreshed outside its wave.
 *  There is exactly one -- worlds.ts, drawing the node table on a page with no world at all
 *  -- and it goes through here so that its epoch guard, its clears and its toast are the
 *  same ones the wave would have given it. */
export function fetcherFor(path: ApiUrl): Registered | undefined {
  return entries.filter(function (fetcher) {
    return fetcher.path === path;
  })[0];
}
