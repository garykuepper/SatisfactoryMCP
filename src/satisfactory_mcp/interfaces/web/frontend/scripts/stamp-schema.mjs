/* Put the provenance header back on top of the generated API schema.
 *
 * `openapi-typescript` rewrites its output file whole, so a header edited in by hand is
 * deleted by the next `npm run typegen` -- silently, which is the worst way for a note about
 * where a file came from to disappear. Chaining this after the generator makes the header
 * part of generating rather than part of remembering.
 *
 * Idempotent: running it twice does not stack two headers.
 */

import { readFileSync, writeFileSync } from "node:fs";

const FILE = new URL("../src/api-schema.d.ts", import.meta.url);
const MARK = "GENERATED from the server's own /openapi.json";

const HEADER = `/**
 * ${MARK}. Regenerate with:
 *
 *     uv run satisfactory-mcp-web          # terminal 1
 *     npm run typegen                      # terminal 2, in this directory
 *
 * Committed on purpose: it is the checked-in record of the API this page was built
 * against, so a diff here is the server's surface changing and is worth reading. It is
 * also why \`npm run check\` needs no running server.
 *
 * READ WHAT THIS DOES AND DOES NOT SAY. Every JSON endpoint under
 * \`interfaces/web/routers/\` is self-typed -- it declares a \`response_model\`, so its whole
 * body is described below and this file is authoritative for it -- EXCEPT \`/api/worlds\`,
 * whose \`200\` is \`unknown\` because it is deferred rather than missed. It forwards the
 * loader's own save headers, so a faithful model is \`dict[str, Any]\` and a useful one
 * deletes eight keys from every row: converting it means changing what it SENDS, which is
 * a commit about the body and not a typing item. The comment above \`worlds()\` in
 * routers/world.py is the long version. \`/api/mapimage\`, \`/api/maptiles/…\`,
 * \`/api/icons/…\` and \`/api/events\` send pictures and a stream and have no JSON body to
 * describe at all.
 *
 * That one endpoint's rows are therefore still the FRONTEND's claim, read off real payloads
 * rather than off the server, and are declared by hand on the page -- with what such an
 * observation is worth stated where they are declared.
 *
 * What this file has always been authoritative for is the other half and still is: which
 * paths exist, which query parameters each takes, and what a validation error looks like.
 *
 * NOTHING IMPORTS THE COMPONENT NAMES FROM HERE DIRECTLY except \`api-shapes.ts\`, which
 * re-exports them under the names the page already used. One indirection, so that a
 * converted endpoint changes one line in one file rather than every module that draws its
 * payload -- and so that the page's names stay the page's while their DEFINITIONS come
 * from the server. \`floors.ts\` predates it and reaches in here itself.
 *
 * Committed on purpose (see above), which is also why regenerating it after a server
 * change is part of the same commit: a checked-in record that lags the server is worse
 * than none, because a diff here is supposed to mean the surface moved.
 */
`;

const body = readFileSync(FILE, "utf8");
if (body.includes(MARK)) {
  console.log("api-schema.d.ts: header already present");
} else {
  writeFileSync(FILE, HEADER + body, "utf8");
  console.log("api-schema.d.ts: header stamped");
}
