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
 * READ WHAT THIS DOES AND DOES NOT SAY, because the answer is changing endpoint by
 * endpoint. Everything under \`interfaces/web/routers/\` that declares a \`response_model\`
 * has its whole body described below and is authoritative for it. Everything still
 * annotated \`-> Any\` publishes no response schema at all, so its \`200\` is \`unknown\` here
 * and its shape is declared by hand in \`api-types.ts\`, from observed payloads -- which
 * that file says at the top, along with what such an observation is worth.
 *
 * Converted so far: \`/api/floors\` (\`FloorsResponse\` and the six schemas under it),
 * \`/api/nodes\`, \`/api/inspect\`, \`/api/regions\`, \`/api/summary\`, \`/api/machines\`,
 * \`/api/structures\`, \`/api/belts\`, \`/api/pipes\` and \`/api/storage\`. Still \`unknown\`:
 * \`/api/worlds\`, \`/api/power\`, \`/api/factories\`, \`/api/collectibles\`, \`/api/crates\`.
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
