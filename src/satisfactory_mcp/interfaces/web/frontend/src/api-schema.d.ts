/**
 * GENERATED from the server's own /openapi.json. Regenerate with:
 *
 *     uv run satisfactory-mcp-web          # terminal 1
 *     npm run typegen                      # terminal 2, in this directory
 *
 * Committed on purpose: it is the checked-in record of the API this page was built
 * against, so a diff here is the server's surface changing and is worth reading. It is
 * also why `npm run check` needs no running server.
 *
 * READ WHAT THIS DOES AND DOES NOT SAY. Every endpoint in `interfaces/web/api.py` is
 * annotated `-> dict`, so FastAPI publishes no response schema and every `200` below is
 * `unknown`. What this file is authoritative for is the other half: which paths exist,
 * which query parameters each takes, and what a validation error looks like. Response
 * bodies are declared by hand in `api-types.ts`, from observed payloads, and that file
 * says so at the top.
 */
export interface paths {
    "/api/worlds": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Worlds
         * @description Every world the save directory holds, newest first.
         */
        get: operations["worlds_api_worlds_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/summary": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Summary */
        get: operations["summary_api_summary_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/nodes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Nodes
         * @description The resource node table, joined to what this save has built on it.
         *
         *     The join is deliberately partial and says so: ``occupancy`` resolves only the
         *     extractors whose target is a node key, so ``occupied`` false means "no extractor
         *     known here", never "free". The map draws it as unknown-or-free and the popup
         *     carries the node id, which doubles as a ``node:`` selector for the MCP tools.
         *
         *     The region name is joined here rather than in the browser because the raster lives on
         *     this side: sending 608 rows and then a 30x30 grid for the page to index into would put
         *     the orientation trap (row 0 is the NORTH edge) in two places. ``label_for_node`` rather
         *     than ``label_for`` -- it prefers the hand-verified override table, so the nodes someone
         *     actually checked come back as ``verified`` instead of as a 256 m cell's best guess.
         *     ``null`` for a node the raster calls void, which is the honest answer for the handful
         *     that sit on islands off the grid.
         *
         *     **A failed save is not a failed answer** -- the same rule ``/api/inspect`` already
         *     follows, because the two used to disagree: the node table is static and needs no
         *     ``.sav``, so a world whose save will not load still gets its geography. What it loses
         *     is the occupancy join, and ``save_error`` says so out loud (with ``occupied`` null at
         *     the top, since "0 of them occupied" would be a claim no one measured).
         */
        get: operations["nodes_api_nodes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/inspect": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Inspect
         * @description What is at a coordinate: the region, the measured ground, and the nearest nodes.
         *
         *     The three answers a site starts with, and none of them was on the map before. Every
         *     one comes straight out of ``domain.spatial`` -- this endpoint converts metres to the
         *     save's centimetres, calls three functions, and rounds.
         *
         *     **A failed save is not a failed answer.** The node table is static, covers the whole
         *     map and needs no ``.sav`` at all, so a world whose save will not load still gets its
         *     region, its ground elevation and its nearest nodes; what it loses is the built
         *     population and the occupancy join, and ``save_error`` says so out loud rather than
         *     letting "no extractor here" quietly mean "no save here".
         *
         *     **And it prefers the extracted terrain when there is any.** On a machine where
         *     ``tools/gen_world_heightmap.py`` has been run, the 1 m field answers "how high is it
         *     here" for unexplored ground with one number at the coordinate asked about, instead of a
         *     population of things standing near it -- and it says which layer of itself answered, so
         *     a 0.2 m landscape reading and a 3.9 m fill reading are told apart. Where there is no
         *     field, or the field has no data there, this is exactly the endpoint it was before.
         *
         *     Not cached, deliberately and by measurement: ``sample_points`` over the 320-hour
         *     reference world builds 9,525 samples in 2.0 ms and ``probe`` scans them in 0.8 ms, so
         *     a per-(world, save) cache would add an invalidation bug to save ~3 ms on a click. The
         *     field is cached, because it is 0.45 s of zlib and 170 MB either way -- but by the
         *     loader, keyed on its own sidecar's mtime, so this endpoint stays a caller.
         */
        get: operations["inspect_api_inspect_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/regions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Regions
         * @description The biome raster: a 30x30 character grid, its legend, and each region's extent.
         *
         *     No ``?save``/``?world``: this is the world's own geography, identical for every save,
         *     which is why it is cacheable and fetched once per page load.
         *
         *     The raster comes through ``domain.spatial.regions``, which reads
         *     ``data/region_names.json``. Two committed files carry a 30x30 grid and they differ:
         *     this one is the file whose per-region bounding boxes are derived from its own grid, so
         *     every cell provably lies inside the box of the region it names -- which is exactly
         *     what the drawing client is checked against. ``satisfactory_regions.json``'s boxes come
         *     from a coarser 1.024 km grid and do not agree with its raster cell for cell, so
         *     painting from it would leave nothing to verify orientation with.
         *
         *     The one thing a drawing client gets wrong is orientation, so it is stated here rather
         *     than left to be inferred. Game +X is east and game **+Y is south**; ``y0_m`` is the
         *     smallest y, so **grid row 0 is the northern edge** and column 0 the western one. Cell
         *     ``(i, j)`` spans x ``[x0_m + i*cell_m, x0_m + (i+1)*cell_m]`` and y ``[y0_m + j*cell_m,
         *     ...]``, and a page that plots ``[-y, x]`` has to flip those y bounds to draw it. The
         *     ``.`` cells are ocean or off-map and carry no name: left unpainted they are the
         *     coastline.
         */
        get: operations["regions_api_regions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/mapimage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Mapimage
         * @description A map render the *user* dropped in, if they dropped one in. Never shipped.
         *
         *     HEAD is routed alongside GET on purpose: the page probes with HEAD before it builds
         *     an ``imageOverlay``, and FastAPI -- unlike bare Starlette -- does not add HEAD to a
         *     GET route by itself, so a probe would come back 405 and read as "no image".
         *
         *     An absent file is the *expected* state, so the HEAD probe answers **204**, not 404:
         *     a 404 is logged red in every devtools console on every clean page load, which trains
         *     the reader to ignore console errors on this page. The GET keeps its 404 with the
         *     where-to-put-it message -- anything actually fetching the bytes deserves the reason.
         *
         *     The corners travel with the file in ``X-Map-Bounds-M`` (``x_min,y_min,x_max,y_max``,
         *     metres, game axes) so the one probe the page already makes answers both questions.
         */
        get: operations["mapimage_api_mapimage_head"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Mapimage
         * @description A map render the *user* dropped in, if they dropped one in. Never shipped.
         *
         *     HEAD is routed alongside GET on purpose: the page probes with HEAD before it builds
         *     an ``imageOverlay``, and FastAPI -- unlike bare Starlette -- does not add HEAD to a
         *     GET route by itself, so a probe would come back 405 and read as "no image".
         *
         *     An absent file is the *expected* state, so the HEAD probe answers **204**, not 404:
         *     a 404 is logged red in every devtools console on every clean page load, which trains
         *     the reader to ignore console errors on this page. The GET keeps its 404 with the
         *     where-to-put-it message -- anything actually fetching the bytes deserves the reason.
         *
         *     The corners travel with the file in ``X-Map-Bounds-M`` (``x_min,y_min,x_max,y_max``,
         *     metres, game axes) so the one probe the page already makes answers both questions.
         */
        head: operations["mapimage_api_mapimage_head"];
        patch?: never;
        trace?: never;
    };
    "/api/maptiles/{z}/{x}/{y}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Maptiles
         * @description One tile of the pyramid ``tools/gen_map_image.py`` cuts beside ``map.png``.
         *
         *     The same picture as ``/api/mapimage``, at one resolution per zoom instead of all of it
         *     at once, and the same posture: nothing is shipped, this is a loader.
         *
         *     **HEAD 204 for an absent pyramid, like the image probe next door.** The page probes
         *     ``0/0/0`` to decide between the pyramid and the single overlay, and an absent optional
         *     file is the ordinary answer -- a 404 on every clean page load teaches the reader to
         *     ignore red lines in the console. GET keeps its 404 and names the tool that would write
         *     the tree.
         *
         *     **Off the pyramid is 404, and cannot be anything else.** ``z``, ``x`` and ``y`` are
         *     typed ``int``, so a path segment that is not one never reaches this function -- FastAPI
         *     answers 422 -- and ``map_tile_path`` range-checks the three against the level's own
         *     grid before building a name. The client is bounds-clamped as well, so in practice this
         *     404 fires for a hand-typed URL rather than for the map.
         *
         *     **Cached hard, and stamped with the build.** A tile is immutable for a given cut of the
         *     game's artwork, so the page asks for it with ``?v=`` the build tag this endpoint hands
         *     out on the probe: regenerating the pyramid changes the tag, which changes every URL,
         *     which is what makes ``immutable`` safe to send. The ETag carries the same tag for
         *     anything that revalidates instead.
         */
        get: operations["maptiles_api_maptiles__z___x___y__head"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        /**
         * Maptiles
         * @description One tile of the pyramid ``tools/gen_map_image.py`` cuts beside ``map.png``.
         *
         *     The same picture as ``/api/mapimage``, at one resolution per zoom instead of all of it
         *     at once, and the same posture: nothing is shipped, this is a loader.
         *
         *     **HEAD 204 for an absent pyramid, like the image probe next door.** The page probes
         *     ``0/0/0`` to decide between the pyramid and the single overlay, and an absent optional
         *     file is the ordinary answer -- a 404 on every clean page load teaches the reader to
         *     ignore red lines in the console. GET keeps its 404 and names the tool that would write
         *     the tree.
         *
         *     **Off the pyramid is 404, and cannot be anything else.** ``z``, ``x`` and ``y`` are
         *     typed ``int``, so a path segment that is not one never reaches this function -- FastAPI
         *     answers 422 -- and ``map_tile_path`` range-checks the three against the level's own
         *     grid before building a name. The client is bounds-clamped as well, so in practice this
         *     404 fires for a hand-typed URL rather than for the map.
         *
         *     **Cached hard, and stamped with the build.** A tile is immutable for a given cut of the
         *     game's artwork, so the page asks for it with ``?v=`` the build tag this endpoint hands
         *     out on the probe: regenerating the pyramid changes the tag, which changes every URL,
         *     which is what makes ``immutable`` safe to send. The ETag carries the same tag for
         *     anything that revalidates instead.
         */
        head: operations["maptiles_api_maptiles__z___x___y__head"];
        patch?: never;
        trace?: never;
    };
    "/api/machines": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Machines */
        get: operations["machines_api_machines_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/structures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Structures
         * @description Every lightweight buildable the player placed: foundations, ramps, walls, catwalks.
         *
         *     These are the only record of what was physically BUILT -- they appear in no actor
         *     header, which is why the projection interns them separately as
         *     ``{"classes": [...], "instances": [[class_index, x, y, z, yaw], ...]}`` in centimetres.
         *     Read guarded field by field, exactly as ``domain.spatial.elevation`` reads them: this
         *     is raw projection data and a malformed row should cost one piece, not the endpoint.
         *
         *     **Rotation is carried, as of schema 12**, and this docstring used to say the opposite
         *     -- the instance transform's quaternion was dropped at extraction and a client could
         *     only draw these axis-aligned, which is why an angled slab came out of the map as a
         *     staircase of squares. ``yaw`` is now the fifth column of a row and comes out as a
         *     ``yaw`` field: degrees about world Z, positive turning +X towards +Y. ``null`` for a
         *     projection cut before 12, which a client must keep drawing axis-aligned rather than
         *     reading as zero.
         *
         *     One thing the projection still does not carry, and it is not invented here:
         *
         *     * **Per-class size.** None of these classes has clearance data, so ``footprint`` is
         *       ``None`` for all eighteen of them. They are all built on the same grid instead,
         *       whose edge ``tile_m`` reports from ``FOUNDATION_M`` so the page does not hardcode 8.
         *
         *     Positions are piece centres: on the reference save consecutive foundations of one
         *     slab sit exactly ``tile_m`` apart.
         *
         *     A world with nothing built answers ``{"structures": [], "count": 0}`` -- an empty
         *     list is a real answer here, unlike a save that could not be read at all.
         *
         *     Sent one row per piece, ungrouped. Measured on the reference world -- 8,347 pieces,
         *     708 KB (610 KB of it before the yaw column) -- which is the same order as
         *     ``/api/collectibles`` already ships (3,455 rows, 547 KB). Grouping into grid cells would halve a
         *     payload that is not the bottleneck and would cost the per-piece class the popup and
         *     the point inspector read.
         */
        get: operations["structures_api_structures_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/belts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Belts
         * @description Every conveyor belt and lift, as the polyline it was actually built along.
         *
         *     New in schema 12, and the reason the map drew no belts at all until now: the splines
         *     were decoded by the parser and thrown away at the projection. They arrive interned the
         *     way the structures next door are -- ``{"classes": [...], "segments": [[chain_index,
         *     class_index, [[x, y, z], ...]], ...]}``, world centimetres -- and, like that endpoint,
         *     the legend is resolved here so the page does not have to carry it, and every field is
         *     read guarded so that a malformed segment costs that segment rather than the network.
         *
         *     **Points are in travel order, input to output.** The save stores them output-first and
         *     the projection reverses them, so a client can draw direction along a run without
         *     knowing that. ``chain`` is the belt chain a piece belongs to -- 1,909 chains over 3,085
         *     pieces on the reference world -- so "the whole run" is a group-by rather than a
         *     geometry problem.
         *
         *     **A lift is a belt whose top-down polyline is a single point.** All 302 lifts on the
         *     reference save have exactly zero horizontal extent (measured: median *and* maximum
         *     horizontal span 0.0 cm, median rise 4 m), so a map that draws them as lines draws
         *     nothing at all where they are. ``lift`` says which ones, and the client owes them a
         *     glyph instead.
         *
         *     Sent one row per piece, ungrouped, the same posture ``/api/structures`` takes and for
         *     the same reason: the per-piece class is what a popup reads. Measured on the reference
         *     world -- 3,085 pieces, 8,292 points, 562 KB -- which is the same order as the floor
         *     plan beside it (8,347 pieces, 708 KB). The geometry is already only the bends: 2,237 of
         *     the 3,085 pieces are two-point straight lines, 2.7 points per piece overall.
         *
         *     **``attachments`` rides along, and belongs here rather than with the machines.** A
         *     splitter or a merger is a piece of the belt network -- it runs no recipe, draws no power
         *     and is meaningless without the runs either side of it -- so it travels with the runs and
         *     is drawn by the layer that draws them. That is also what keeps it from being drawn twice:
         *     it is in no other payload, so a map with the machines layer on and the belts layer off
         *     shows no splitters at all, which is the honest picture of "these are belt parts".
         *
         *     They carry no spline -- a splitter is a point with a facing, not a route -- so they are a
         *     row shape of their own: where it stands, which way it faces, and what it is. 848 of them
         *     on the reference world -- 481 splitters, 364 mergers, 3 smart splitters -- 170 KB against
         *     the 562 KB of runs they join.
         */
        get: operations["belts_api_belts_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/pipes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Pipes
         * @description Every fluid pipe, as the polyline it was actually built along, and what it carries.
         *
         *     New in schema 13, and the belts' other half. The geometry was never hidden the way the
         *     belts' was -- a pipe's spline is an ordinary ``mSplineData`` PROPERTY on the pipeline
         *     actor, stored in the actor's own frame and translated back at the projection -- so this
         *     is the same shape one layer down: ``{"classes": [...], "networks": [...], "segments":
         *     [[network_index, class_index, [[x, y, z], ...]], ...]}`` in world centimetres, resolved
         *     here so the page carries no legend, every field read guarded so a malformed segment costs
         *     that segment.
         *
         *     **Each pipe says which fluid it carries**, which is the thing a belt cannot say: the game
         *     keeps an ``FGPipeNetwork`` per connected plumbing system with the fluid on it and its
         *     members listed, so ``fluid`` is the world's own answer rather than an inference from what
         *     the pipe is plugged into. All 503 pipes on the reference world are claimed by one of its
         *     19 networks -- 215 crude oil, 198 water, 55 fuel, 31 heavy oil residue, 4 alumina
         *     solution. ``null`` for a pipe no network claims, which happens on none of them here but
         *     is what an empty or half-built network would give.
         *
         *     **``direction`` is INFERRED, and ``basis`` says from what.** Nothing on a pipe records
         *     which way the fluid goes -- that much of the old refusal stands, and the points are still
         *     in the order the file stores them. But the plumbing AROUND it records a great deal: the
         *     save serialises every fluid coupling, and names a machine's port ``PipeInputFactory`` or
         *     ``PipeOutputFactory``. ``domain/world/flow.py`` reads that graph and declines wherever
         *     more than one answer is consistent. So ``direction`` is ``forward`` along ``points_m``,
         *     ``reverse`` against it, or ``unknown`` -- 365, and 138 unknown, on the reference world --
         *     and ``basis`` is one of:
         *
         *     * ``machine port`` -- this very pipe ends at a port the save TYPES. Barely an inference.
         *     * ``pump`` -- a pump or valve at one end, one-way by construction.
         *     * ``propagated`` -- only the shape of the wider network settles it.
         *     * ``unresolved`` -- and then ``direction`` is ``unknown``. A pipe in a loop, or a trunk
         *       with producers and consumers on both sides, genuinely has no fixed direction.
         *
         *     A client may draw an arrow on the first three and must not on the fourth.
         *
         *     Sent one row per piece, ungrouped, the posture ``/api/belts`` and ``/api/structures`` both
         *     take. Measured on the reference world -- 503 pipes, 1,987 points, 48 KB -- an order
         *     smaller than the 562 KB of belts beside it, because there are six times fewer of them.
         *     Nothing to thin: 3.9 points a pipe, and they are already only the corners.
         *
         *     Not in here: pumps, junctions, valves and fluid buffers. They carry no spline at all, only
         *     a header position, so they are a different row shape and a different question -- the same
         *     question the belts key leaves open about splitters and mergers.
         */
        get: operations["pipes_api_pipes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/factories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Factories
         * @description Named factories and the coherence-scored proposals for the unnamed rest.
         *
         *     Each row carries ``bbox_m`` -- ``[x_min, y_min, x_max, y_max]`` in metres, game axes
         *     -- alongside its centroid, because a centroid alone cannot frame a viewport. The map
         *     turns a label into a button that flies to its factory, and "fly to the mean of 50
         *     machines" is not the same request as "show me all 50": the first picks a zoom out of
         *     the air, the second is decided by the extent. Computed here rather than client-side
         *     because the client is never sent the anchor machines, only their count.
         *
         *     ``null`` when nothing in the set is still standing -- ``geo.bbox`` refuses to invent
         *     a zero box at the world centre, and so does this. A label whose machines were all
         *     demolished keeps its name and its remembered centroid; what it loses is the ability
         *     to be flown to, which is the honest report.
         *
         *     A proposal whose machines the player has already named is not a proposal: the
         *     clusterer runs over the whole world, so it re-discovers every named factory, and
         *     sending those rows lets a machine-generated recipe string draw itself exactly on
         *     top of the player's own label. Any proposal in which named anchors are the majority
         *     is dropped here; ``index`` stays the position in the full proposal list, so a
         *     ``proposal:N`` selector still resolves to the same cluster in the MCP tools.
         */
        get: operations["factories_api_factories_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/collectibles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Collectibles
         * @description Map placements, filtered exactly the way the MCP tool filters them.
         *
         *     ``collect_view`` owns every refusal -- unknown mode, retired group, and the one
         *     that matters here: ``mode=remaining`` needs the generated placement table, and
         *     without it the honest answer is the refusal rather than a shorter list.
         */
        get: operations["collectibles_api_collectibles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Events
         * @description Server-sent events: one ``save`` event per observed write, plus keepalives.
         *
         *     The stream carries the trigger, never the payload. A save event says which file
         *     moved and when; the page decides what to refetch. That keeps this endpoint O(1) in
         *     the size of the world and means a browser that missed an event is one refetch, not
         *     one resync, behind.
         */
        get: operations["events_api_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    worlds_api_worlds_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    summary_api_summary_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    nodes_api_nodes_get: {
        parameters: {
            query?: {
                resource?: string | null;
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    inspect_api_inspect_get: {
        parameters: {
            query: {
                x_m: number;
                y_m: number;
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    regions_api_regions_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    mapimage_api_mapimage_head: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    mapimage_api_mapimage_head: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    maptiles_api_maptiles__z___x___y__head: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                z: number;
                x: number;
                y: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    maptiles_api_maptiles__z___x___y__head: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                z: number;
                x: number;
                y: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    machines_api_machines_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    structures_api_structures_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    belts_api_belts_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    pipes_api_pipes_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    factories_api_factories_get: {
        parameters: {
            query?: {
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    collectibles_api_collectibles_get: {
        parameters: {
            query?: {
                group?: string | null;
                mode?: string;
                near?: string | null;
                save?: string | null;
                world?: string | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    events_api_events_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
