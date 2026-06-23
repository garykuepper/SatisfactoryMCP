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
 * READ WHAT THIS DOES AND DOES NOT SAY. Every endpoint in \`interfaces/web/api.py\` is
 * annotated \`-> dict\`, so FastAPI publishes no response schema and every \`200\` below is
 * \`unknown\`. What this file is authoritative for is the other half: which paths exist,
 * which query parameters each takes, and what a validation error looks like. Response
 * bodies are declared by hand in \`api-types.ts\`, from observed payloads, and that file
 * says so at the top.
 */
`;

const body = readFileSync(FILE, "utf8");
if (body.includes(MARK)) {
  console.log("api-schema.d.ts: header already present");
} else {
  writeFileSync(FILE, HEADER + body, "utf8");
  console.log("api-schema.d.ts: header stamped");
}
