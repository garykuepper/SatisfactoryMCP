# Space, and the map drawn on it

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §7 is the coordinate frame, the region
layer and the selector language every spatial and planning tool shares; §17 and §18 are the web
map's base layers and the mode model that lets a reader pick one; §19 is the water channel every
one of those layers draws. Section numbers are continuous with the rest of the spec;
[DESIGN.md](../DESIGN.md) indexes it.

---

## 7. Spatial model


**Slugs are latent shards.** A shard pool counted only from crafted Power Shards
understates what a player can overclock with. On the reference save the Dimensional
Depot holds **93 Blue, 58 Yellow and 39 Purple slugs — 404 shards — against 22 already
crafted**, a 19x understatement.

The 1/2/5 ratios are *derived*, never listed: `GameData.slug_yields()` reads every
single-ingredient part recipe that produces a Power Shard, which is exactly
`Power Shard (1)`, `(2)` and `(5)`. Restricting to one ingredient also excludes
`Synthetic Power Shard`, which makes shards from Time Crystal, Dark Matter Crystal,
Quartz and Photonic Matter — a production chain, not something lying in a crate.

`craftable` is reported **apart from** `free`, because crafting is a manual step:
folding it in would produce a number the player reads as available now. The
affordability check uses both — "SHORT by 158, but 404 more are craftable from slugs you
already hold".

Slugs are found wherever `stock()` looks: carried, in crates, or in the Depot. The output
also names *where*, since "is that the Depot?" is otherwise a question the player has to
ask.

### 7.1 Coordinate frame — settled

| axis | meaning |
|---|---|
| **−Y** | **north** |
| +X | east |
| +Z | up |
| scale | 1 m = 100 cm exactly |

Established four independent ways, strongest first: the wiki `Crash_Site` table gives 118 pods with a
Region column *and* save coordinates — `Northern Forest` mean Y = −81,078 vs `Southern Forest`
+205,386; joining those rows to `crashSites.py` matches **117/118 to sub-centimetre**, which also proves
the wiki's coordinates *are* raw save coordinates and pins cm-per-metre at 100.

Content extents (2,688 static objects): X −298,838…406,564; Y −314,104…304,196; Z −16,827…46,942.

Biome grid `[WIKI]`: `GRID_CELL = 102400`, `GRID_X0 = -319600` (west edge of X0),
`GRID_Y0_SOUTH = 302800` (Y0 is southernmost). Emit the cell (`"X3Y4"`) on every node/site response —
it's exact and needs no interpolation.

**Compass bearing = `atan2(x - ox, -(y - oy))`.** The negated Y is the thing every naive implementation
gets wrong.

### 7.2 Regions: exact geometry, advisory names

**Decision: separate the two concerns.** All *computation* uses exact geometry — grid cells, cones,
radii, clusters. Region *names* are a separate, independently queryable, explicitly approximate dataset
used only for human-readable labelling and name lookup. A name never feeds a calculation.

**Layer 1 — exact geometry (authoritative, zero maintenance).**
Biome grid cell from §7.1's closed-form transform; cone/hemisphere direction tests; radius queries;
200 m single-linkage clustering. All derived, all exact, nothing hand-authored.

**Layer 2 — `data/region_names.json` (advisory). BUILT.**
21 biomes as a 256 m label raster plus a parallel *confidence* raster, `accuracy_m: 256`, and an
authoritative per-node override table. Exposed as `label_for(x, y)`, `label_for_node(node)`,
`filter_nodes(nodes, name)` and `resolve(name)`. The optimizer and site ranking never consult it.

Regenerate with `uv run python tools/gen_region_names.py`, which derives it from
`data/satisfactory_regions.json` and fixes the four defects that made the source unusable:

1. **No void/ocean class** — all 900 cells carried a land label, so open ocean north-west of the map
   came back as "Rocky Desert". Fixed with a **land mask built from 2,669 static world objects** (nodes,
   crash sites, power slugs, somersloops, Mercer shrines and spheres): a cell whose centre is >1000 m
   from all of them is void. Threshold picked from the measured bimodal distribution — median 123 m on
   land vs p90 1212 m. Result: **590 land / 178 sparse / 132 void** cells.
2. **Raster spilling outside its own bboxes** (11 of 21 regions), which made the recommended
   `bbox AND raster` test return `False` for points the raster itself assigned. Fixed by recomputing
   every bbox **from** the raster, so containment holds by construction — asserted in the test suite.
3. **Boundary mislabels presented as certain.** Fixed two ways: a per-cell confidence from 8-neighbour
   agreement (`interior` / `boundary` / `sparse` / `void`; **333 boundary cells**, honestly reported),
   and a 48-entry override table from the hand-verified oil clusters. That recovers exactly the known
   failure — `BP_ResourceNode86/88` read `Jungle Spires (boundary)` from the raster and
   `Western Beaches (verified)` after the override, so Western Beaches now returns 10 of 10 oil nodes
   instead of 8.
4. **A second implementation contradicting the prose.** `data/geo_reference.py` is **deleted**;
   `spatial/regions.py` is the only implementation, so there is nothing left to disagree.

Validation: **13 of 14** hand-verified oil clusters agree with the raster, 0 land on void. The single
mismatch is defect 3's cluster, which the override table now handles. Per-crash-site validation would
need the wiki Region column, which isn't shipped locally — recorded as a limitation in `_meta`.

Also: 200 m single-linkage recovers the real oil fields, but one cluster merges 6 well satellites with a
standalone node 85 m away — so **node kind must never be inferred from one cluster member**.

### 7.2a Node lookup — one tool, three modes

`search_resource_nodes` answers three different questions through a `mode` parameter,
rather than splitting into separate tools that share 90% of their body:

| mode | ranks by | answers |
|---|---|---|
| `fields` (default) | yield | "where is there a lot of iron" — 200 m clusters |
| `nodes` | yield | "which individual nodes", with ids reusable as selectors |
| `nearest` | **distance** | "what is closest" — requires `near` |

`near` takes a coordinate in metres, `me` for the player pawn, or **the name of a labelled
factory**. The last is the reason the mode has this shape: "the nearest free coal to the
coal powerplant" is the question actually asked, and hand-copying a centroid out of another
tool's output is how the wrong coordinate gets used. Supplying `near` in any mode adds a
distance column headed with the origin's name, so the number is never ambiguous.

`group="node"` predates `mode` and still resolves — a stored call must not break.

`mode="nearest"` without `near` is an **error**, not a silent fall back to yield order:
answering a different question than the one asked is worse than refusing.

### 7.2b Map deep links

`show_on_map(target)` builds a satisfactory-calculator.com interactive-map link centred on
a coordinate, `me`, a named factory, a node id, or a resource name, with the relevant
overlays switched on.

Fragment format, read off a working link the player supplied:

```
#4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;oilImpure;...
 ^zoom ^x    ^y     ^group    ^sublayers, semicolon-separated
```

**Coordinates are save centimetres.** Corroborated rather than stated by the site: the
supplied coordinate falls inside the measured content bbox (§7.1) and resolves to the
northern oil region, which is what its oil layers show. Every other tool quotes metres, so
the conversion lives in `maplink.map_url` and nowhere else; a metre value passed by mistake
lands 1/100th of the way across the map, near the origin, which looks plausible and is
wrong.

**Every layer token was read from the page, not inferred.** `WebFetch` gets **403** from
this host, but `curl` from the user's own machine returns the 1.5 MB page with the
identifiers in it. That distinction earned its keep — inferring from the single oil example
got **two of fourteen wrong**:

| guessed | actual | why the guess failed |
|---|---|---|
| `nitrogenWell*` | **`nitrogenGasWell*`** | the stem is the item name, not the resource word |
| `geyser` (bare) | **`geyser{Impure,Normal,Pure}`** | our node table gives geysers no purity; this map does |

Both would have opened the map at the right place with the overlay **silently missing** —
the failure mode hardest to notice, and the reason a guess was not good enough here.

Structure, all read from the page: nodes are `<stem><Purity>`; wells are
`<stem>Well<Purity>` and exist only for `oil`, `nitrogenGas` and `water`; nitrogen and
water are **well-only**, so a bare node token does not exist for them; oil is both.
Collectibles are single tokens — `greenSlugs`, `yellowSlugs`, `purpleSlugs`, `hardDrives`,
`mercerSpheres`, `somersloops`. There are 51 layer *groups*, of which `gameLayer` carries
the resource markers.

### 7.2c A miner is never valid on a liquid node

`search_resource_nodes` reported **every oil node at double its real rate** — a pure node
read 480 m³/min where an Oil Extractor gives 240.

`node_rate` picks the best extractor for the node's kind, filtered by `mAllowedResources`.
That field is only populated when `mOnlyAllowCertainResources` is **True**, which is
`False` on every miner — so miners looked unrestricted, and Miner Mk.3 (base 240) out-bid
the Oil Extractor (base 120) on crude.

`mAllowedResourceForms` is the field that actually encodes it, and it was **already parsed
and simply never consulted**:

| building | `mAllowedResourceForms` | `mOnlyAllowCertainResources` |
|---|---|---|
| Miner Mk1/2/3 | `RF_SOLID` | False → `mAllowedResources` empty |
| Oil / Water Extractor | `RF_LIQUID` | True |
| Resource Well Extractor | `RF_LIQUID, RF_GAS` | True |

Corrected: oil reads 60/120/240 by purity, confirmed **three independent ways**.

1. Our building model, `extract_rate(purity, clock)`.
2. The dump's own cycle fields — `mItemsPerCycle / mExtractCycleTime × 60`, litres to m³
   for fluids — which reproduces every parsed `base_extract_rate` exactly, including the
   Oil Extractor's 2000 L/s → 120 m³/min.
3. The wiki's Crude Oil "Resource acquisition" table (supplied by the user; the site is
   behind a Cloudflare 403 to automated fetches):

| node purity | m³/min at 100% | m³/min at 250% |
|---|---|---|
| Impure | 60 | 150 |
| Normal | 120 | 300 |
| Pure | 240 | 600 |

All three agree cell-for-cell, and `node_rate` now joins them; before the fix it
disagreed with all three by exactly 2×. Tests pin both the derivation and the published
table, including the **250% column** — that is the figure a plan is actually built
against, and a pure node overclocked is 600 m³/min, not 1,200.

**Only the node search was affected.** `extractor_processes` builds its columns from
`building.extract_rate(purity, clock)` with the actual building, so the LP and
`plan_layout` were always right — which is how the discrepancy was spotted: the same world
read 480 in one tool and 240 in the other.

### 7.3 Source selectors

**Decision: one selector language, used by every spatial and planning tool.** `plan_factory` takes no
`direction` parameter; it takes `sources`, a list of selectors that say which nodes may feed the plan.
Implemented in `spatial/select.py`.

| selector | meaning |
|---|---|
| `north`, `northeast`, … | compass hemisphere, or a 60° cone when an origin is supplied |
| `region:Northern Forest` | named region (layer 2; a bare name also works) |
| `grid:X3Y4` | exact 1.024 km biome grid cell |
| `node:BP_ResourceNode30_103` | one specific node, repeatable |
| `near:<x_m>,<y_m>,<radius_m>` | circle, metres |
| `bbox:<x1>,<y1>,<x2>,<y2>` | rectangle, metres |
| `resource:` / `purity:` / `kind:` | filters, not locations |
| `all` | every node |

**Locations union; filters intersect.** So `["north", "resource:Crude Oil"]` is "crude oil in the
northern half", and `["region:Spire Coast", "near:120,-2020,1500"]` is the union of two areas.

Two rules that matter more than the syntax:

- **A failed location selector returns nothing, not the whole map.** A typo'd region name that quietly
  widened the scope to the entire map would answer a completely different question and produce a
  confidently wrong plan. Filters with no location *are* a legitimate whole-map query, so the two cases
  are tracked separately (`location_attempted` vs `location_seen`).
- **Every unmatched selector is reported** in the response's notes, never silently dropped.

Underlying primitives, all exact: `grid_cell(x, y)`; `distance_m` (XY only — Z spans just 0.64 km and
matters for pipe head, not proximity); `in_direction(dir, origin, half_angle)`; `cluster(nodes, 200 m)`
with fields named by *content* rather than biome.

The earlier plan of unioning a cone with a curated `NORTH_REGIONS` set is dropped. It existed to patch a
too-narrow cone, and it silently dropped the best-purity northern field because that field's biome wasn't
in the hand-written list. Named regions are now first-class selectors instead, so no curated direction
list is needed.

> **Aggregates must be scoped.** "1,740 m³/min free in the north" is true and useless: free capacity
> within 1,000 m of the user's generator farm is **0**, and the free nodes are 1,515–2,215 m away across
> two separate fields. Always report free capacity per cluster with a distance, never as a bare total.

Altitude has a **sign** that matters: a field 268 m above the refineries feeds them without pumps.
Report `dz` relative to the consumer, not absolute altitude.

---

## 17. Two more base layers, drawn rather than found (2026-07-31)

The page has had one base map since there was a base map: the game's own artwork, cut out of
the reader's install by `tools/gen_map_image.py`. The 1 m heightfield made a second kind
possible and the game turned out to ship the ingredient for a third, so there are now three,
and `/api/maptiles/{layer}/{z}/{x}/{y}` is how a client asks for one.

**`terrain`** is the hypsometric preview the heightmap workflow produced, at full
resolution and unretuned: a green→olive→tan→rock→snow ramp over the 1st..99.5th height
percentile, a north-west hillshade at 45°, water tinted by its own depth, and the page's own
`--sea` where the field knows nothing. It is a cartographer's map — the colour is the height
and nothing else.

**`satellite`** is the same relief with the colour coming from somewhere real.

### The game ships biome geometry after all

`data/region_names.json` says, in its own `known_limitations`, that "the game ships no biome
geometry, so this cannot be regenerated from game data". That is now false and worth
correcting rather than quietly working around.
`/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel`
is a `FGMapAreaTexture`: `mDataWidth` 4096, `mAreaData` a 4096² array of palette indices, and
`mColorToArea` resolving each index to a `UFGMapArea` object with its bounding box. 37
indices, 17 distinct named areas plus `Area_NoMansLand`. A 2026-07 sweep saw the asset,
called it "low prior, custom serialisation" and deferred it; it decodes in twenty lines
through the reader this repository already has.

**Its corners are measured, and the region grid is not what measured them.** Nothing in the
asset says where those 4096 texels go. Four statistics were tried against the world box and
three of them are unusable: least squares on per-area centroids, IoU against the
heightfield's landscape extent, and IoU against the artwork's ocean colour all have optima
hundreds of metres wide and drift to the edge of whatever sweep contains them, because the
landscape extent is a rectangle rather than a shore and `NoMansLand` is a scope difference
rather than a coastline. The one that works asks a sharper question: **does an area boundary
land on something the map draws?** `calibrate_biome` takes the artwork sheet's edge strength
averaged over the raster's boundary texels, divided by its edge strength everywhere. At the
in-game map square that ratio is **1.97**; at ±600 m in any direction the best rival is
**1.33**, at 5% larger **1.28** and 5% smaller **1.24**. So the biome raster spans exactly
the square the artwork does — 4096 texels over 7500 m, 1.831 m to the texel, row 0 north —
and every run re-measures it and refuses to claim the pin if the margin goes.

The check by NAME is reported and is deliberately not the pin. Of the 768 non-void cells of
the hand-traced region grid, **481 (62.6%) land on a named game area**; the other 287 are the
outer coast, which the wiki names and the game leaves as no-man's-land. Of the **401** that
land on a named area and whose wiki region has a one-to-one counterpart among the game's,
**273 agree (68.1%)**. The residual is one disagreement, not noise: the wiki's Spire Coast is
a coastal ring the game divides between four areas, and it alone accounts for 62 of the 128
misses. A 68% agreement could not tell 100 m from 400 m; the edge ratio can, which is why
they are two separate records in `_meta` rather than one number.

### The palette is designed, and the shipped one is not used

`mColorPalette` has 37 RGBA entries and they are a minimap legend: flat primaries, cyan,
magenta, pure white. Drawing them would produce a highlighter sketch of a world. So they are
decoded, recorded in `_meta` for a reader to see, and ignored; `BIOME_COLOURS` is a table
written by eye against crops, desaturated and capped well below white, and a test holds it to
both of those. Over it go four rules in an order that is the argument: the biome says what
grows there, the **slope** overrules it because nothing grows on a cliff face, the
**altitude** bleaches what is left, the **hillshade** lights rock and canopy alike, and the
water goes on top because it is a different surface rather than a different ground. Two
octaves of fixed-seed value noise keep the flats from being flat fills.

Two things had to be softened before it read as imagery rather than as a choropleth. The
area polygons meet along a mathematical line — they exist to name a place on a minimap, not
to draw one — so the colour field is Gaussian-blurred by 24 texels (≈44 m) before it is
sampled. And the shoreline is feathered twice: over 0.9 m of depth, which handles a beach,
and by 0.73 m of blur, which handles the great deal of this world's water that sits in
box-shaped bodies against a cliff and has no depth band to blend in. (That second one was
0.8 output *pixels* until the sheet doubled, at which point the same constant would have
quietly meant half as much ground; it is in metres now.)

### Layers on the serving side

* **`/api/maptiles/{z}/{x}/{y}` is unchanged and is an alias for `map`.** Not a redirect and
  not a deprecation: the live page addresses the base map there, every cached tile is keyed
  on it, and the layered route is four segments where that one is three, so they cannot
  collide. Both go through one `_serve_tile`.
* **A layer is a directory.** `map` keeps `data/local/tiles/`; renders live at
  `data/local/renders/{layer}/tiles/`, cut on the same frame at the same 256 px into the same
  `{z}/{x}_{y}.png`. Switching layers is switching one path segment — not the CRS, not the
  bounds, not the zoom range.
* **Every header is that layer's own**, read from the sidecar beside its own tiles: depth,
  tile size, corners, build. They are generated by different tools at different times, the
  artwork can be two levels deeper if it was `--enhance`d, and the build tag folds the
  layer's NAME in so two pyramids that agree on every recorded number still cannot share a
  cache key and serve each other's `immutable` tiles.
* **An unknown layer is a 404 listing the ones there are**, not a 422 about a path parameter,
  and it never becomes a filename: the string is looked up in `_layer_dir`, which answers
  `None` for anything that is not a name this module wrote down.

### 16384 px, z6, and no further (2026-07-31)

The renders were 8192 px — 0.92 m to the pixel — and stopped at z5 on the argument that a
1 m field cannot support more. That argument was half right and the wrong half was the
sampler. Bilinear is **C0**: its derivative jumps at every texel boundary, and the hillshade
is a function of the derivative, so sampling the field below its own spacing ruled the relief
into 1 m squares. Swapping to **Catmull-Rom** (cubic convolution, a = −1/2), which is C1, is
what makes a finer grid mean anything: the surface has a continuous gradient and the shading
computed on it does too. Where the 4×4 stencil is not whole — a fifth of this field is
no-data, so that boundary is long — the 2×2 answer is used instead, because a cubic kernel
has negative lobes and one straddling a hole overshoots.

So the renders are now **16384², z0..z6**, 0.458 m to the pixel: 2.18 samples per metre of
source, past Nyquist.

**And 32768 was measured and refused.** On four windows — an offshore cliff island, a
mainland cliff face, the dune field, the Spire Coast — in both layers, the change one
doubling makes (the finer render box-filtered onto the coarser grid, minus the coarser
render, mean absolute per channel) is **3.34 levels of 255 for 4096→8192, 2.69 for
8192→16384, and 1.38 for 16384→32768** — at 3.6× the drawing time and 4× the bytes. More
telling: the **high-frequency energy per pixel falls at every doubling**, in all eight
window-and-layer pairs (15.34 → 14.32 → 13.57 on the mainland cliff). That is a sampler
resolving an interpolant, not a picture finding new world in the source. What 32768 buys is
antialiasing on cliff facets. z6 is the last level with a measurement behind it.

The artwork pyramid can still invent z6 and z7 with an upscaler, because a drawn map has
strokes a model understands. These layers have no such licence.

### Borrowing the artwork's shading where the field's province is coarse (2026-07-31)

The 1 m grid is one resolution and **not one accuracy**, and rendering as though it were is
what made the offshore islands look like melted wax. Measured on the shipped field: 45.3% is
landscape — continuous geometry the game evaluates itself, which genuinely sharpens — 21.3%
is cliff, rasterised low-poly collision hulls whose facets resampling can only polish, and
14.0% is fill, a 3.9 m-quantised block raster where resampling does nothing at all.

Over the last two provinces, and only over them, the render borrows the artwork sheet's
**luminance high pass**: the game's own 8192 px map, decoded from its four BC1 slices in the
same run, minus its own Gaussian blur at σ = 8 px, multiplied into the shading and faded out
on the provenance byte over 6 m. It is light, never colour — an ocean drawn blue contributes
its brightness and nothing else — and where the provenance says landscape, the shading is the
field's own and nothing is borrowed.

Two things about it are findings rather than settings. **The drawn map has strokes**: every
rock formation is outlined in hard dark ink, and multiplied straight in they come out as
exactly what they are, a line drawing laid over terrain. So the high pass is softened by
1.6 px and squashed through `tanh` at 1.2σ — a soft clip, so the mid-tones (the shading) pass
almost linearly and the outliers (the ink) saturate. And **the gain was picked by looking**:
at 0.17 an offshore cliff island is still eight flat plates with a hint of something on them,
at 0.50 the artwork's contour rings read as rings; 0.30 is where a collision hull becomes
rock and the dune field — landscape province, untouched — still looks exactly as it did.

The same run now reads that sheet for the biome calibration too, which removes a skip path:
the pin used to go unmeasured when `data/local/map.png` was absent, and it is scored against
the container every run.

### Two tile trees, and the second one is the same grid

`tiles@2x/` holds the identical tile **grid** at 512 px a tile: level z is still 2^z tiles a
side over the identical squares of the world. A hi-DPI client asks for the same
`{z}/{x}/{y}`, adds `?px=512`, and draws the answer into the same CSS box — twice the pixels
in each direction, no change to the CRS, the bounds, the zoom range or the tile size the
layer is configured with. It is exactly one level shallower by arithmetic (512·2^z runs out
of sheet before 256·2^z does), so past its top the two carry identical information.

The probe advertises both depths (`X-Map-Tile-2x-Px`, `X-Map-Tile-2x-Max-Z`), absent when a
layer has no such tree, and a request for a density a layer does not have falls back to the
1x tile — which every client can draw at any density. Both trees' numbers ride in one cache
tag, so recutting either changes every URL of that layer.

The client picks it at `devicePixelRatio >= 1.5` rather than `> 1`: a 125% Windows scale
reports 1.25, where the @2x tile is 60% more pixels than the screen can show. It is read once
at probe time and not watched — a window dragged to another monitor is a reload, the same
bargain the CRS already makes.

### Cutting in parallel, and the proof that it is the same bytes

Cutting 5,461 tiles at `optimize=True` is minutes of one core doing nothing but deflate. The
**resampling stays serial** — level z is one Lanczos downscale of the whole sheet, in the
parent, exactly as before — and only the per-tile encode is spread over processes, with the
level published once into a `shared_memory` block every worker maps and one task per row of
tiles. Because no worker resamples anything, no worker can disagree about a filter tap at a
strip boundary, which is why this is byte-identical rather than merely equivalent.

`--check-parallel` proves it rather than asserting it: one level cut both ways, SHA-256 of
every tile compared name for name. On the reference machine, z5 of the artwork sheet, 1,024
tiles: **5.43 s serial against 1.34 s on 16 workers, 4.05×, byte_identical true**, recorded
in the sidecar.

### And a swap Windows can refuse

`install_pyramid` renames the finished tree over the old one so a reader meets a whole
pyramid or none. **Windows will not rename a directory anything has open** — an Explorer
window sitting in `tiles/`, the search indexer, a backup agent — and that is the ordinary
state of a directory a person has been looking at, not a rare one: it ended a ten-minute run
with `Access is denied` on the machine this was written on. So there is a second-best, and it
says it is second-best: the swap is done **one level at a time**, each level renamed
atomically over its predecessor, and a reader who catches the middle sees every level present
with some still the old cut rather than a level missing. Which of the two happened is
recorded in the sidecar as `tiles.installed_by`.

### Measured on the reference machine

| | terrain | satellite |
|---|---|---|
| draw 16384² | 65 s | 74 s |
| cut both trees, 16 workers | 46 s | 42 s |
| `tiles/` z0..z6 | 5,461 tiles, 237.5 MB | 5,461 tiles, 229.2 MB |
| `tiles@2x/` z0..z5 | 1,365 tiles, 237.2 MB | 1,365 tiles, 228.6 MB |

227 s for both layers end to end, against 91 s for both at 8192² with no @2x tree and a
serial cutter. Banded at 256 rows with an **8**-row halo — up from 4, because the cubic
stencil reaches two texels either side instead of one and the water blur's three sigma is
five pixels at this resolution rather than two — so no pixel is computed from a one-sided
gradient or a truncated kernel.

### Why a new tool rather than a stage on the heightmap generator

`gen_world_heightmap.py`'s job is to get a field *out of the game* — sweep cooked packages,
rasterise collision meshes, validate against 626 nodes. `gen_map_renders.py` reads that
finished field back through the same public codec any consumer would, adds an input the
heightmap generator has never heard of, and writes pictures. They share an input and nothing
else: no stage, no constant, no intermediate array. Bolting them together would have coupled
a six-minute extraction to a ninety-second render and given one `--force` two meanings. What
*is* shared is shared by import — the codec from `domain.spatial.heightfield`, and the
pyramid cutter, its staging rename and its refusals from `core.gameassets.pyramid`, which
grew one optional `source=` so a level record can say what actually drew it. The frame
itself — the corners, the sheet size, the artwork the biome pin is scored against — still
comes from `gen_map_image.py`, because that is the tool that *measured* it.

Measured when this first shipped at 8192²: 12 s to draw terrain and 15 s satellite, 32 s each
to cut, 91 s for both layers end to end, 60.1 and 60.2 MB of PNG per pyramid over 1,365
tiles. The 16384² numbers that replaced them are in the table above.

## 18. One base map at a time, and the page says which (2026-07-31)

Three pyramids on the server were three pictures the page could not ask for: the client
addressed the artwork alias and nothing else. What it now has is the Google Earth split —
**modes**, which are one question with one answer, kept apart from **overlays**, which are
thirty-five independent yes/nos.

**The four modes are `artwork`, `terrain`, `satellite`, `plain`**, as radios in their own
section at the top of the layer control, above a rule and above every checkbox. `plain` is a
real mode rather than the absence of one: it is no base imagery, which is the shipped state,
what a fresh clone with no generated renders looks like, and — since it is the mode the biome
tint is designed for — a thing a reader may prefer.

**A mode is one `L.TileLayer` and a mode switch swaps it.** Nothing else moves: not the CRS,
not the three panes, not one data overlay, not the region blend's rule. That is the serving
design of §17 arriving on the client exactly as intended — same frame, same tile size, same
grid, so the client changes one path segment. The one per-mode difference that survives is
depth: `maxNativeZoom` comes from that layer's own `X-Map-Tile-Max-Z`, so the renders stop at
z6 (z5 when the client is fetching @2x tiles, which are one level shallower) and Leaflet
upscales past it while the artwork runs to its own z7 if it was `--enhance`d.

**Measured, at 1600×1000 on the reference world:** the frame is unmoved by a switch — map
rect `[0, 40, 1600, 960]`, pane transform `translate3d(0,0,0)`, scale bar `500 m`, `z=-3
c=0,0` — identical across all four; and the network log for a switch contains that mode's
tiles and no other layer's.

### The rule that replaced the auto-untick

A render arriving used to *untick* the region box, once, as an event. With four modes that
stops being expressible: "arriving" now happens on every switch, so the same heuristic would
throw away a choice the reader had made in between. So it is stated as a rule about states:
**the region tint defaults OFF under any imagery mode and ON under plain** — which is the map
the old heuristic left you on, said as a rule — **and a reader's own tick of that box wins for
the rest of the session.** The default is what the page does when it has not been told, not
what it does instead of being told.

Programmatic ticks are told from real ones by a flag, because they cannot be told apart
afterwards: Leaflet fires `overlayadd` from the layer's own `add` event, so `map.addLayer` and
a click arrive identically.

### Resolution order, and what a link pins

`#world=…&save=…&mode=…&z=…&c=…` — subject, then picture, then viewport. An absent or unknown
`mode` resolves **artwork → plain**, and so does a mode this machine has never generated: a
link to someone else's terrain render should land on a map rather than on an error. Terrain
and satellite are never chosen *for* you even when they are the only pictures on disk, because
they are interpretations of this world rather than the map of it and the page should not have
an opinion about which. The fragment omits `mode` entirely until the probes have answered, so
a pan in the first fifty milliseconds cannot pin a mode nobody chose.

### A mode that is not there stays on screen

An ungenerated pyramid greys its row out rather than removing it, with the generator named in
the row's `title` — the same sentence `/api/maptiles`' GET 404 carries, repeated in the client
because the page probes with **HEAD** and HEAD answers 204 with no body on purpose. Asking for
the message would mean asking for the error the server went out of its way not to raise. A
pyramid whose tiles turn out not to *draw* is treated as the same thing plus a toast: the mode
greys out with the reason in place of the generator, and the page falls back to `plain` rather
than to another render, because silently substituting a different picture of the same world is
the one answer that could be mistaken for success.

The artwork's single-image `/api/mapimage` fallback survives as a detail of the artwork mode
rather than a stage of a loader: probed only when its pyramid does not answer, and drawn as the
`imageOverlay` it always was.

---

## 19. The water channel, rebuilt out of the game's own water (2026-07-30)

The heightmap's fifth stage used to be a flatness detector over the 2048 px interface raster.
It found **20.4%** of the sheet as water against the artwork's 39.0% — 46.7% recall overall,
**35.8% over Spire Coast** — and invented plateau lakes on flat mesas. The failures were
structural rather than tunable: the raster's quantisation step is 3.9 m and the water it was
asked to find is 2.1 m deep, a river is a metre of water in a groove it cannot resolve at all,
and over the fill province the "terrain" the detector compared against **is** the water
surface, so the depth it needed was identically zero.

**Two sources, each asked only what it knows.** The four `SlicedMap` BC1 slices are the game's
own drawing of its own world and its water is drawn blue, so `B - R >= 25` gives the plan
shape: bimodal with nothing between the modes, 3 of the 626 static resource nodes called water
(0.48%), registration measured at exactly (0, 0) sheet pixels by a ±2 px sweep. The 849 cooked
water actors give the level: **837 carry a world AABB** — `BoxComponent.BoxExtent` (130),
`BrushBodySetup.AggGeom` (270), `InstancedStaticMeshComponent.CachedBounds` (222) and, for the
plane-backed blueprints whose cooked instance names no mesh, `WaterPlane`'s own
`ExtendedBounds` (215) — each taken to world space through the composed `AttachParent` chain
eight corners at a time, because 486 of them are rotated. **A box's top is the surface**: the
save's 23 water extractors all sit inside a volume and stand on its box top to within
**0.005 cm**.

The channel goes from **9.589 km² to 18.248 km²**, against the artwork's 18.288. Spire Coast
recall goes from 35.8% to **99.85%**, the invented plateau lakes are gone, and the ocean sits
at −16.994 m, which is where all 31 ocean-spline boxes put it.

### The level is per texel, not per body

The literal recipe was one median per drawn body. It was rejected by measurement: the ocean
and every river running into it are **one connected shape** in the artwork, spanning 141 m of
box top, and a single median over that invents up to **157 m of depth** across 0.06 km². The
level is therefore the *highest* box top standing over each texel — a box top is a surface, so
where several overlap in plan the highest is the one visible from above. That is not rough:
0.017% of neighbouring wet texel pairs step past 0.5 m, and those are river mouths, where a
step is what is really there. The body median survives as the fallback for the **17 texels**
of drawn water no box stands over at all.

### `waterq.u8.z`, and the one arithmetic nothing may do

A level and a **depth** are different claims. The level comes from a box and is good to
centimetres wherever there is water; the depth is that level minus the ground, and over the
fill layer the ground is a 3.9 m raster that routinely rounds *above* a sea surface 17 m down.
So the field gained a fourth raster — `0` dry, `1` water with a depth measured against 1 m
terrain, `2` water whose level is known and whose depth is not — and 7.39 km² of the 18.25
takes value 2.

**Nothing may decide submersion with `water_m > z_m` any more.** That test reads the open
ocean as dry, and it is what `Reading.submerged` used to do; it now asks the quality byte and
falls back to the comparison only for a field written before that byte existed. `Reading.
water_depth_m` is `None` where the depth is unknown rather than `max(…, 0)`, and the inspector
prints the reason instead of a plausible `0 m deep`.

The same rule binds `tools/gen_map_renders.py`, which reads the rasters directly, and
**2026-07-31 it does**: submersion is the coverage of `waterq.u8.z != 0` sampled onto the
output grid, the depth feather applies only to the share whose depth was measured, and a
level-only texel is drawn at full alpha. Measured on the shipped field, the comparison it
replaced read **3.572 km² of ocean out of 18.248 as dry** — that is what used to be missing
from both renders.

Level-only water is also tinted at the **deep** end of the ramp rather than the shallow one,
and that is a measurement rather than a preference: 95.2% of it stands over the fill province
and 98% of its surface levels lie in a 0.7 m band around the ocean's own −16.99 m. It is the
ocean. Running the ramp on `water_m − z_m` there would paint it the pale green of an
ankle-deep sheet, because the number being subtracted is a 3.9 m raster's rounding error.

One thing the channel still cannot fix: beyond the landscape's own extent the field has no
height *and* no water volume, so those texels stay the page's `--sea`. Against bright water
that edge is visible in the corners of the frame. It is the render saying nothing, which is
the intended behaviour, and filling it would mean inventing ocean.

### Four gates, each aimed at a specific silent failure

Dry-node false positives over 1% mean the colour classifier drifted or the sheet moved.
Spire Coast recall under 95%, measured against the artwork over the `Spire Coast` cells of
`data/region_names.json` — a hand trace of the wiki's map, so not this pipeline marking its
own homework — means the region that exposed the old detector is being missed again. An ocean
level more than 0.5 m from the median ocean-spline box top means the level is coming from the
wrong volumes. And artwork water standing over no box at all, past 1%, means the mask and the
volumes have stopped describing the same world, which is what a misregistration looks like
from here. The run refuses to write if any of them fails. On build 495413 they measure
0.48%, 99.85%, 0.000 m and 0.0001%.

