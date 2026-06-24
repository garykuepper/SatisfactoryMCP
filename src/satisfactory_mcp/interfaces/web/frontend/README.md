# The map page

TypeScript, built by Vite into `../static/`, which is the directory `app.py` mounts at `/`.

**The built bundle is committed.** `uv run satisfactory-mcp-web` works from a fresh clone on
a machine with no Node at all; Node is needed only to *change* the frontend. That is the
whole reason `static/` is in git, and the reason `tests/test_architecture.py` insists every
file in it carries the build banner: a committed artifact is exactly the kind of file
someone edits in place, and the next build silently throws that edit away.

## Changing anything

```
cd src/satisfactory_mcp/interfaces/web/frontend
npm ci            # exact versions from package-lock.json
npm run dev       # Vite on :5173, hot reload, /api proxied to :8712
npm run build     # refresh the committed bundle in ../static/
npm run check     # tsc --noEmit
npm run typegen   # regenerate src/api-schema.d.ts from a running server
```

`npm run build` is not optional before committing. The source and the artifact are reviewed
in the same diff, and a change to `src/` without the rebuilt `../static/` beside it is a
change nobody is running.

## The dev loop

`npm run dev` serves the page from source with hot module replacement and proxies `/api` to
whatever `satisfactory-mcp-web` is already running on `127.0.0.1:8712` — so the dev server
needs no save files, no game install and no Python of its own. Start the real server first:

```
uv run satisfactory-mcp-web      # terminal 1, :8712
npm run dev                      # terminal 2, :5173
```

**`/api/events` is Server-Sent Events, and the proxy passes it through.** This was measured
rather than assumed, because a proxy that buffers a response body turns a live stream into a
page that hangs on the first event: with the dev server up, `curl -N
http://127.0.0.1:5173/api/events` delivers `event: save` frames as the watcher emits them,
the `content-type` stays `text/event-stream`, and the live dot in the header goes green.
Vite's proxy is `http-proxy` in streaming mode and adds no compression, which is what makes
that work; if a compression plugin is ever added here, this is the thing it will break.

The page reads `/api` at absolute paths, so nothing has to be configured for dev versus
production — in production it is the same origin, in dev the proxy makes it look like one.

## Layout

| file | what it is | lines |
| --- | --- | --- |
| `index.html` | the page. Vite's entry; the built copy lands in `../static/` | 40 |
| `src/main.ts` | the entry: which map events are listened for, in what order, and what happens on load | 90 |
| `src/leaflet.ts` | the one `import`, and where the private-field declarations attach | 19 |
| `src/state.ts` | the current selection, the epoch, the layer registry. Imports nothing | 92 |
| `src/dom.ts` | `el`, and the escaping `popup()` every popup builder goes through | 67 |
| `src/toast.ts` | the message strip: failures, and the one non-failure note | 68 |
| `src/format.ts` | resource short name, region line, phase name | 37 |
| `src/palette.ts` | every colour chosen against the others, in one table | 89 |
| `src/api.ts` | `get()`, and the two query parameters every endpoint takes | 45 |
| `src/api-types.ts` | the response shapes, hand-written from observed payloads | 276 |
| `src/api-schema.d.ts` | generated from `/openapi.json`; paths and query parameters | 1058 |
| `src/leaflet-private.d.ts` | the fields this page hangs off Leaflet objects | 74 |
| `src/map.ts` | the map, the CRS, the panes, and the `[-y, x]` rule | 184 |
| `src/layers.ts` | named layer groups, kept across a refetch | 75 |
| `src/layercontrol.ts` | the folded control: legend, filter, tri-state families, focus | 395 |
| `src/regions.ts` | the biome raster, and how it shares the screen with a render | 126 |
| `src/tiles.ts` | the optional map render: pyramid, single overlay, or nothing | 187 |
| `src/routes.ts` | belts and pipes: runs, lifts, junctions, chevrons | 563 |
| `src/placements.ts` | the floor plan and the machines standing on it | 106 |
| `src/markers.ts` | nodes, pickups, the player — and the node-dot raise | 175 |
| `src/labels.ts` | factory labels, the flight, and the declutter pass | 277 |
| `src/inspector.ts` | the right-click answer: the one thing here that is not a layer | 126 |
| `src/load.ts` | who fetches what and when; the epoch guard | 192 |
| `src/worlds.ts` | the two pickers, and keeping a selection through a rescan | 174 |
| `src/sse.ts` | one EventSource, and what a save write means | 61 |
| `src/style.css` | the page's own stylesheet, imported after Leaflet's so it wins on order | 424 |
| `public/vendor/LEAFLET-LICENSE` | copied verbatim into the build; BSD-2-Clause requires it | |
| `vite.config.ts` | where the build writes, the banner it stamps, the dev proxy | |
| `scripts/stamp-schema.mjs` | re-applies the generated schema's provenance header | |

Two things about the graph are deliberate and easy to undo by accident.

`state.ts` imports nothing. `map.ts` reads `BOOT` while it is building the map, so anything
`state.ts` imported would have to be evaluated before the map exists.

`layercontrol.ts` does not import `labels.ts`. `batch()` used to end by calling `declutter()`
by name, which put three files in a ring — control imports labels imports layers imports
control — to say "the list has stopped changing". It now offers `onSettled`, and `main.ts`
registers the pass.

Leaflet is the `leaflet` npm package pinned to **1.9.4** — the exact version that used to sit
in `static/vendor/leaflet.js` — and it is compiled into the bundle together with its own
stylesheet. Its three icon PNGs are inlined as data URIs, so the built page makes no network
request to anything but this server: no CDN, no fonts, no map tiles anyone else hosts. That
is a licence and privacy posture, not a preference, and `npm run build` is expected to keep
it. If a dependency ever emits an `assets/` directory of its own, check what is in it.

## Types

`npm run check` is `tsc --noEmit` and it is clean under **full `strict`**, plus
`noUncheckedIndexedAccess`, `noUnusedLocals` and `noUnusedParameters`. It needs no running
server: everything it reads is committed.

`noUncheckedIndexedAccess` is the one worth calling out, because it is the setting most
projects leave off. It cost five call sites here, and each was a real "this index can miss"
that the old code happened to answer correctly: a fragment with no `z`, a section key nobody
has folded yet, a session name seen once. They are now written down as answers rather than
left as luck.

Two files carry the API, and they are authoritative for different halves.

- **`src/api-schema.d.ts` is generated** by `npm run typegen` from the server's own
  `/openapi.json`, and committed. It is the authority for which paths exist, which query
  parameters each takes, and what a validation error looks like — `get()` only accepts a path
  the server actually serves. It says **nothing** about response bodies: every endpoint in
  `api.py` is annotated `-> dict`, so FastAPI publishes no response schema and all sixteen
  `200`s come out as `unknown`. Regenerating rewrites the file whole, so its provenance header
  is re-stamped by `scripts/stamp-schema.mjs`, which `typegen` chains.
- **`src/api-types.ts` is hand-written**, from payloads observed against a real save. It is the
  frontend's claim about the API, not the API's claim about itself, and it says so at the top.
  The proper fix is response models on `api.py`, which would make this file generated too —
  that is a change to the server's public surface and belongs in its own commit.

What is still `any`, in full:

- `L.Class.extend()` returns `any` in `@types/leaflet`, so `PyramidLayer` in `tiles.ts` is
  asserted back to a `new (url, options) => TileLayer` at the point of definition. That
  assertion is the only place the tile layer's shape is stated.
- `L.Control.Layers.sortFunction` and a few Leaflet option bags are typed by `@types/leaflet`
  as loosely as Leaflet itself defines them; nothing here widens them further.
- `/api/summary` is typed for the four branches this page reads and no further. Typing the
  other twenty would be inventing a contract for data nothing looks at.

`src/leaflet-private.d.ts` declares the fields this page hangs off Leaflet objects. It keeps
two kinds apart on purpose: the page's own marks (`_rank`, `_chevron`, `_labelWeight`), and
three real Leaflet internals it deliberately uses (`_handlingClick`, `_update`, `layerId`).
The second list is what to read before upgrading Leaflet.
