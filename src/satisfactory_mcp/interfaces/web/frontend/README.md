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

| file | what it is |
| --- | --- |
| `index.html` | the page. Vite's entry; the built copy lands in `../static/` |
| `src/main.ts` | the whole page, ported as-is from the old `static/app.js` |
| `src/style.css` | the page's own stylesheet, imported after Leaflet's so it wins on order |
| `public/vendor/LEAFLET-LICENSE` | copied verbatim into the build; BSD-2-Clause requires it |
| `vite.config.ts` | where the build writes, the banner it stamps, the dev proxy |

Leaflet is the `leaflet` npm package pinned to **1.9.4** — the exact version that used to sit
in `static/vendor/leaflet.js` — and it is compiled into the bundle together with its own
stylesheet. Its three icon PNGs are inlined as data URIs, so the built page makes no network
request to anything but this server: no CDN, no fonts, no map tiles anyone else hosts. That
is a licence and privacy posture, not a preference, and `npm run build` is expected to keep
it. If a dependency ever emits an `assets/` directory of its own, check what is in it.

## Types

Not yet. `main.ts` is a straight port of a 2,600-line `var`-and-callback file and `tsconfig.json`
is set loose to match — `strict: false`, `noImplicitAny: false`, and Leaflet cast to `any` in
one place at the top of `main.ts`. `npm run typegen` and a strict `npm run check` land with the
typed fetch layer; the migration order is the port first, then the module split, then the types,
so that a behaviour regression can never hide inside a wave of type errors.
