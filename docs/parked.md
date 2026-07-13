# Open questions, parked work, and finished work kept as a record

Part of the [SatisfactoryMcp design spec](../DESIGN.md). Three kinds of thing, in the section
order they were written in rather than sorted by kind:

* **Open questions** — §14, the register, most of it closed and kept closed rather than deleted.
* **Parked** — §15b, §16, §16b: decided in principle and not built, with the measurements that
  would otherwise have to be redone. §16b carries the stage-0 results, which are a GO.
* **Done, kept as a record** — §15 (the build order it was built in) and §19 (the container
  reader and the `gen` extra). Neither is a plan any more; both explain why the tree is shaped
  the way §4 describes.

Section numbers are continuous with the rest of the spec; [DESIGN.md](../DESIGN.md) indexes it.

---

## 14. Open questions

| id | question | impact | how to resolve |
|---|---|---|---|
| ~~OQ1~~ | ~~Can fluids actually be sunk?~~ | **CLOSED** — user confirms fluids cannot be sunk. Hardcoded per §5.6. | — |
| ~~OQ2~~ | ~~Does an unchosen hard-drive option return to the pool, and is the forfeit permanent?~~ | **CLOSED** — player confirms the unchosen option returns to the pool; only the drive is spent. Picking is **low-stakes**, which inverts the advice the tools used to imply. See §9.3. | — |
| ~~OQ3~~ | ~~Are `mNumSchematicsPerHardDrive = 2` / `mNumRerollsPerHardDrive = 1` overridden by a packaged ini?~~ | **CLOSED for practical purposes** — the constants are confirmed by *observation* rather than by source: all 25 offers on the reference save carry exactly 2 options, and 24 of 25 exactly 1 reroll (the 25th has spent it). Player confirms one reroll. Whether some other install could override them is unanswerable from here and no longer matters. | — |
| ~~OQ4~~ | ~~Runtime property names for installed somersloops.~~ | **CLOSED** — resolved exactly as proposed: researched Production Amplifier, slotted one, re-saved, diffed. The sloop is in `InventoryPotential` beside the shards (`potential_slots`), and the actor carries `mPendingProductionBoost` = the resulting multiplier. See §6.9. | — |
| OQ5 | ~~Water pump -> water volume mapping~~ **PARTLY CLOSED** — pumps DO map to a named `FGWaterVolume` (3 bodies here, 13/6/4) and sea level is measured at −17.4 m. What stays unknown is a volume's SHAPE and capacity: the object is level geometry and is not in the save. See §6.10. | Remaining half needs map data no input carries. | Accept unknown. |
| ~~OQ6~~ | ~~Regenerate the node/purity table independently of SCIM.~~ | **CLOSED** — merged with an MIT, game-asset-derived set; 0 purity/resource mismatches, and a missing node recovered. See §3.4. | — |
| ~~OQ7~~ | ~~How many somersloops does the user actually hold?~~ | **CLOSED by OQ4** — free and committed are both read, so owned is exact: **14 free + 1 slotted = 15**, plus 10 Mercer Spheres counted separately. Only the free pool can fund a plan; the committed one is reported so a player knows there is something to pull out. | — |

---

## 15. Build order

All steps below are **done**; kept as a record of dependency order.

1. **`docs/`** — loader, `uestruct.py`, normalize, invariant assertions. 0 warnings on v1.2.2.1.
2. **`sidecar/` + `save/projection.py`** — projection, world grouping, two-tier cache, committed fixture.
3. **`render.py` + game-data tools** — compact TSV, schema-capped limits, honest truncation.
4. **Save-state tools** — unlocks, power, progression, sites.
5. **`spatial/`** — exact geometry, generated node table, region-name layer, selector language.
6. **`planning/optimize.py`** — equality balance, both guards, two-phase solve, grid-import model.
7. **`planning/advisor.py`** — hard-drive counterfactuals incl. an own-output objective.

Everything in the spec is now built, including §10.3's resources and prompts and
`rank_build_sites`.

`rank_build_sites` scores `1.00·throughput − 0.35·spread − 0.25·distance + 0.20·purity`, min-max
normalised **across the candidates in that query only**, and returns every raw component so a caller can
re-weight. Four behaviours are pinned by tests because getting them wrong would produce confident
nonsense: fully-tapped fields are not candidates; unreachable capacity (well satellites behind a locked
Pressurizer) is excluded from throughput; a missing infrastructure distance stays `None` rather than
scoring as adjacent; and the altitude delta is **positive when the field sits above the consumer**, since
that means fluid flows downhill and needs no pipeline pumps.

---

## 15b. Parked: derive the map tables from the installed game, on game update

**Decided in principle, not built.** Every artifact under `data/` that describes map placements is a
pinned snapshot with a shelf life, and the shelf life is a game update: *"in updates, resource nodes
tend to move if the map gets changed"* (Lukas, 2026-07-30). That is not an anomaly to document once,
it is the normal lifecycle, and it is already visible in the tree.

Measured against the installed build, `data/world_resource_nodes.mit.json` — pinned to an older
one — has **25 `BP_ResourceNode` rows moved 9.5–80.4 cm vertically**, and one renamed:
`BP_ResourceNode11` on all 25 saveVersion-52 saves became
`BP_ResourceNode20_UAID_04D9F5D42711A7C902_1245462149` on all 6 saveVersion-60 saves, 150 cm away.
`data/resource_nodes.json`'s `_meta.cross_validation.positions` itemises every one.

**Two silent failure modes, which is why this is worth doing rather than merely noting.** A join by
instance name simply *misses* after a rename — and a per-kind count check cannot see it, because
459 == 459 across a rename. And a position can be a metre out while the answer stays confident.
The **node-table skew gate** — `domain/spatial/nodes.py`, pinned by `tests/test_node_table_skew.py` —
makes the skew visible; deriving on update would remove it.

**Why it is now cheap.** The collectibles work built a reader for the game's own IoStore container:
4,521 `GameLevel01` `.umap` packages decompress in ~3 s with zero failures, positions agree with
live save actors to a **median 0.0000 cm**, and the extraction reproduces the deleted GPL tables
class-for-class. So `resource_nodes.json` and `world_collectibles.json` could become **caches keyed
on the game's `build_version`** rather than committed snapshots, regenerated when a save reports a
build the cache does not know.

**What has to be settled before building it**, and none of it is hard, only unexamined:

* **Oodle — and this bullet used to give the wrong reason.** It read that `pyooz` is GPL-3.0, that
  it must never enter `pyproject.toml` or `src/`, and that regenerating at runtime "would undo the
  licence position this project spent months reaching". That is not a licence question and never
  was: **using** a library imposes no obligations, copyleft attaches on **distribution**, this
  project is not distributed, and no copyleft code is in the tree — what the parser work removed
  was a *vendored* GPL file, which is a different thing from an installed dependency. Since
  [§19](#19-one-container-reader-and-the-gen-extra-2026-07-31) `pyooz` **is** in `pyproject.toml`,
  as the optional `gen` extra, and `core.gameassets.iostore` imports `ooz` inside one function.
  What actually stands is operational, and it is enough on its own: the extra is optional **at
  import time**, so the server, the parser, the domain and the whole test suite run on a machine
  that has none of the three installed. Making a *request* depend on `ooz` would make a
  generation-time decoder mandatory for every reader who never generates anything — a native
  extension in the path of a tool call, for the sake of a table that changes once a patch. So the
  cache is refreshed by an explicit `tools/` run the user invokes after a patch — the conservative
  answer, on optionality and on the cost below, not on a licence.
* **Cost.** ~3 s of extraction is fine for a build-time step and not for a tool call, so the
  refresh must be explicit or lazily cached, never per-request.
* **What survives a refresh.** `data/world_collectibles.json` carries per-instance `state`
  (`collected` / `present` / `unknown`) folded from the save history. A regenerated placement set
  must re-fold that rather than reset it, and instance renames mean the fold cannot key on the name
  alone — position within a tolerance is the fallback, and the skew gate shows the tolerance has to
  be chosen against the rounding floor rather than guessed.
* ~~**No first-party escape for the region layer.** The game ships no biome geometry, so
  `data/satisfactory_regions.json` stays a CC BY-SA trace whatever happens here.~~ **Wrong, and
  fixed.** The game ships `FGMapAreaTexture` — a 4096² map-area raster at 1.83 m with a
  `UFGMapArea` object per palette index, each stating its own display name. `data/region_names.json`
  is derived from it, the trace is deleted, and the share-alike obligation went with it. Left
  struck through rather than removed because the failure is the instructive part: this bullet was
  a *conclusion from not having looked*, written beside a note in the same document that had
  already seen the asset and called it "low prior, custom serialisation".

## 16. Parked: site outlines and visualisation

Deliberately out of scope for now. Recorded because the enabling investigation was
non-obvious and would be expensive to redo.

**The idea.** A website that visualises a plan per floor, plus a way to mark on the map
where a factory floor actually has room -- an outline you declare, against which a plan can be
fit-checked. Read-only; no editing.

**The finding that makes it feasible.** Existing foundations are fully readable from the save.
`FGLightweightBuildableSubsystem.actorSpecificInfo` is shaped
`[count, [classPath, [instance, ...]], ...]`, and each instance is a 12-field list where
`[0]` is a rotation quaternion and **`[1]` is an exact world position**. On the reference save
that is **5,105 foundation pieces** (4,626 `Build_Foundation_8x1_01_C` + 479 polished),
occupying **2,844 distinct 8 m cells = 182,016 m²**, which flood-fill into 8 contiguous
platforms of 20+ cells:

| cells | area | extent | centre (m) | altitude |
|---|---|---|---|---|
| 504 | 32,256 m² | 168×440 m | 103, −2819 | −18 m |
| 326 | 20,864 m² | 216×192 m | −474, −1505 | +46 m |
| 287 | 18,368 m² | 688×112 m | −1513, −1206 | +20 m |

So "where do I already have flat space" needs no marking at all — it is derivable. Only
unbuilt ground needs a declared outline.

**What it would enable.** Build sites as first-class objects, derived from foundations or
declared as rectangles. A fit check against a plan's peak floor: the Spire Coast plan needs
**1,632 foundations** while the largest existing platform is **504**, so it does not fit on
anything currently built. And a floor view that packs blocks into a *real* boundary rather
than an arbitrary square, which is the difference between a meaningless arrangement and
"it fits with 18% slack".

**Terrain is partially derivable too.** There is no heightmap, but Z is known at ~8,500
points (foundations, buildings, nodes, slugs, crash sites), so an outline can report
elevation spread and sample density — "varies 40 m across 12 samples, expect heavy
foundation work" — rather than either silence or invention.

**Prerequisite: plan persistence.** A visualisation needs a stable target, and a plan cannot
be re-derived later because the *save is an input to the solve* — unlocked recipes, node
occupancy, buildings available. Re-solving next month against a drifted save gives a
different answer with no record of the old one. A stored plan would need the scenario
arguments, the solved processes, the layout, the save header it was computed against, and
`docs_sha256` so it stays interpretable across a game patch. Agreed location if built:
a user data directory, not the repo.

**Still not derivable, and would be invention:** world placement, belt routing, terrain
fitting, foundation alignment to the world grid.

## 16b. Parked: floor-wise factory view (Lukas, 2026-07-31 — "only think about that idea")

Show what is *built*, one floor at a time — the save-side twin of §16's plan-per-floor view.
The two should share a renderer, which argues for doing the groundwork once, for both.

**Why it is feasible.** The save carries everything vertical. Foundations have exact Z, and
players build floors at discrete, consistent heights, so a Z-histogram over one platform's
foundations should decompose into floor bands (4 m wall heights make floors cluster at ~4 m
spacings). Machines assign to the band their base Z sits on. The part that would make it
genuinely good: the parser already decodes **225,686 belt spline points** — belts filtered to
a floor's Z band render as real routing polylines, a blueprint rather than a scatter of
rectangles.

**Obstacles, in order of pain:**

1. **Rotation.** The projection drops the placement quaternion, so everything renders
   axis-aligned. A top-down world map survives that; a *floor plan* does not — angled rows of
   machines come out as staircases and defeat the point. Yaw-through-projection (a schema
   bump) is a hard prerequisite, and independently valuable: it fixes the staircase artefact
   already visible on the world map.
2. **Floor membership edge cases.** Ramps and lifts span bands; tall machines poke through a
   low ceiling but belong to their base floor while occluding the one above; mezzanines and
   half-height spacing blur the histogram. Needs a tolerant band-assignment rule, not an
   exact one.
3. **Scope boundary with §16.** Same renderer, two data sources (a solved plan vs the save's
   actual placements). Build the renderer against the save first — it has ground truth to
   check against; a plan view has none.

**Natural staging:** quaternion through the projection first (small, self-contained), then
Z-band floor detection as a domain service (input: one platform or label bbox; output: floor
bands with member foundations/machines/belts), then the floor picker in the web UI reusing
the existing footprint renderer.

### The in-depth pass (2026-07-31)

**Detection.** A floor is not in the save; it is recovered from geometry. Cluster foundation
*top-surface* Z (piece Z + thickness — 1/2/4 m variants) per platform, single-linkage at
~0.5 m tolerance, weighted by count. Every band is reported with its cell area and ordered —
a mezzanine shows as a visibly minor floor rather than being merged or dropped. Ramps and
conveyor lifts span bands by design: identify by class, assign to both floors as connectors.
Lifts double as free ground truth — their endpoints say which bands the player actually moves
between. Factories on bare terrain degrade to one ground floor at sampled terrain Z, never an
error.

**Assignment.** Machine base Z vs band top within a small epsilon — but first MEASURE whether
projection `pos` is base or pivot; if pivot, derive the per-class Z offset empirically by
diffing machine Z against the foundation beneath, across a whole save, and pin it in a test.
Belts: a spline segment belongs to the band containing both endpoints; band-crossing segments
render as paired port glyphs, like lifts. Tall machines: solid on the base floor, ghost
outline on any floor they pierce.

**The hidden ripple.** Yaw-through-projection is schema 12, and adding fields changes the
digests in `tests/fixtures/vendor_parity.json` — the banked agreement with the deleted
oracle. Resolution, decided now: the parity test digests a projection FILTERED to the
schema-11 fields, with a comment saying new fields are legitimately outside the oracle's
scope. Never re-bank. Yaw alone suffices (top-down plans need no pitch; ramps are identified
by class), so the projection grows one float per placed thing.

**Interaction.** Not a separate page. The factory card gains a "floors" action: fly to the
site, dim the base layer, swap the world layers for a floor picker — same Leaflet, same
popups, ESC returns to world mode. Picker rows in table voice: `Floor 2 · +8 m · 214 cells ·
31 machines`.

**Validation before pixels** (fixture-runnable, no UI): (1) band cleanliness — fraction of
foundations within epsilon of a band; below ~95% on real platforms the premise is wrong and
the feature stops; (2) orphan machines — everything assigns to one band or is explicitly
on-terrain; (3) lift consistency — endpoints land on two distinct bands.

**Stage 0 is the kill-switch:** run the Z-histogram against the reference world's 8
platforms from raw projection data in a scratch script, before any schema work. Either clean
bands emerge or the idea dies for the cost of an afternoon. Open input from Lukas before
stage 2: are his multi-floor factories uniform-height stacks or mixed heights (4 m logistics
under 8 m machine floors)? That decides how clever band-height inference must be.

### Stage 0 ran, 2026-07-30: GO, with corrections

**99.87%** of foundations on real platforms sit within 5 cm of a detected band (threshold
was ~95%), and the bands are exact — eps 5 cm and 50 cm give the same answer. Confirmed on
the oldest lightweight-capable save (Oct 2025): 99.82%. What the measurement corrects:

* **A lightweight's `z` is its vertical CENTRE**: `top = z + thickness/2`. Machines' pivot
  is their base. Verified by four independent piece families.
* **The storey module is 12 m** (three wall courses), not 4 m — mixed with 1-2 m
  half-steps that must stay separate bands carrying their cell area (a 6-cell mezzanine
  must not read as a floor). Band inference is per-platform, no fixed pitch.
* **No per-class offset table.** Production buildings sit at zero above their deck (93.2%
  within 5 cm, n=441); belt attachments at +100 cm. Exemptions, not fits: miners (on
  nodes, up to 28 m off-deck) and water pumps (+20 cm, on water).
* **Orphans are on terrain, measurably**: no-band things sit a median 0.59 m above the
  derived heightfield (89% within 2 m); band-assigned things average +26 m. "On terrain"
  is a measurement, not a bucket.
* **Validation check 3 was wrong as written.** 24% of lift chains are same-deck jogs
  (belt-height hops), not floor connectors. The pinnable claims: consumers of `belts`
  group by chain FIRST (pieces join at median 0.00 cm), and NO chain rising >=6 m lands
  both ends on one band (0/89 in 2026, 0/75 in 2025).
* **Floor filtering will be clean**: 84.8% of belt runs are same-deck, 93.3% never leave
  one deck's column; endpoint height above deck has its own legal set (100/300/500 cm).
  Pipes: 50.7% same-deck, 44.5% on terrain (plumbing hugs the ground).
* **Unit of decomposition**: plain 4-connected XY flood fill of 8 m cells (132 platforms,
  17 real ones). `build_structures`' slabs weld distant platforms via ramp chains (one
  slab spans 287 m of Z) — use slabs for NAMING, flood-fill for floors.
* **Pre-U8 saves emit zero `structures`**: the feature says "this save is too old", never
  "this world has no floors".

Stage 1 was already shipped by the map work (yaw + belts + pipes + attachments, schemas
12-14); the stage-0 slice image was drawn from the projection alone. Remaining: stage 2
(the `floor_decomposition` domain service + the corrected checks as tests) and stage 3
(the floor view-state in the frontend).

### Schema 12 landed the geometry (2026-07-31)

The prerequisite above is done, and the floor view is still parked — this was worth doing on
its own, because the world map drew angled platforms as staircases and drew no belts at all.

* **Yaw**, one float in degrees, on every lightweight buildable (a fifth column on a
  `structures` row) and on every machine / extractor / generator record. Positive turns +X
  towards +Y, directly comparable with `atan2(dy, dx)` over `pos`; range `(-180, 180]`.
  Verified against the layout rather than against the formula: on five angled platforms the
  bearing between tile-spaced foundations equals their yaw modulo 90 to within 0.002°.
  **Z only** — of 9,153 `Build_*` actors just 396 carry any pitch or roll, all of them
  wall-mounted pipe and ceiling parts, and no lightweight buildable carries any.
* **Belts**, a new `belts` key: `[chainIndex, classIndex, [[x, y, z], …]]` per belt piece, in
  travel order, world centimetres. Conveyor **lifts** are in it — same records, told apart by
  class — which is what §16b needs them for. The splines are stored in the chain actor's
  frame and are translated back; all 51,200 chains on this disk carry an identity rotation, so
  nothing has to be un-rotated *yet*.
* **Cost, measured on the reference save:** the projection grew 938,012 → 1,209,739 bytes
  (+29.0%), the pickle 824,652 → 1,091,625 (+32.4%), and the parse 2.41 s → 2.87 s (+19%,
  the lazy chain trailers). **Not thinned, and that was measured too:** the spline points are
  already only the bends — 2,237 of 3,085 pieces are 2-point straight lines — so
  Douglas-Peucker at 1 cm drops 8% of points to save 1.2% of the projection, and at a lossy
  100 cm still saves only 3.2%. There is nothing there to win.
* **The parity ripple resolved as planned.** `tests/test_savparse_parity.py` filters the
  projection back to the schema-11 shape through an explicit list of what 12 added, and
  `vendor_parity.json` is untouched.

### Schema 13 landed the plumbing, and the junctions (2026-07-31)

The other half of "draw what the player built", in two pieces that shipped together because
they answer the same complaint about the same map. Belts came out of a trailer and cost a lazy
decode; a pipe's route was in reach the whole time and nothing asked for it, and a splitter's
placement was being *built and then dropped on the floor*.

* **Pipes**, a new `pipes` key: `{classes, networks, segments}`, with a segment
  `[networkIndex, classIndex, [[x, y, z], …]]` per pipe, world centimetres. The spline is an
  ordinary **property** — `mSplineData`, an array of structs whose `Location` is a control
  point — on `Build_Pipeline_C` / `Build_PipelineMK2_C` and their two `NoIndicator` variants.
  503 pipes and 1,987 points on the reference save.
* **The frame is the actor's, translated and not rotated**, and that is measured rather than
  assumed. Every `Location` is actor-local (the first point of all 503 is exactly `(0,0,0)`,
  which proves the frame is local and nothing about the correction), so the check is against
  things the pipes did not write: translated, 294 of the world's 306 pipeline **flow
  indicators** sit within 10 cm of a pipe polyline (median 0.0, p95 4.7 cm) where raw not one
  does, and pipe endpoints are a median 6.0 m from the nearest junction or pump against 1.7 km
  raw. All **18,069 pipeline actors across the 66 saves on this disk** carry an identity
  rotation, the same statement `belts` makes about chains.
* **The fluid comes with it**, which is what a belt cannot say: `FGPipeNetwork` carries the
  fluid descriptor and lists its members, so every one of the reference world's 503 pipes is
  claimed by one of its 19 networks — 215 crude oil, 198 water, 55 fuel, 31 heavy oil residue,
  4 alumina solution.
* **No flow direction, and that is a refusal rather than an omission.** A belt has an input
  and an output end and `belts` is emitted in travel order; a pipe has `PipelineConnection0`
  and `PipelineConnection1`, an `mFluidBox` that is one float of contents, and a flow
  indicator actor carrying nothing but its paint. Direction is decided at runtime by head lift
  and demand and reverses when they do, so the points stay in file order and no consumer is
  told they mean travel. `/api/pipes` ships no arrows. **Superseded four days later by schema
  14 below** — not because that finding was wrong, but because it was answering about the
  pipe when the question was about the plumbing.
* **Cost, measured on the reference save:** the projection grew 1,209,739 → 1,258,597 bytes
  (+4.0%), of which `pipes` is 48 KB. **The time did not move** — best of 5 interleaved runs,
  3.03 s either way (medians 3.08 and 3.09) — which is the difference between this and the
  belts: those cost +19% because a chain trailer had to be decoded, and these were already
  being parsed as ordinary properties, so `_pipes` only reads what the walk had in hand.
* **Attachments**, a new `attachments` key, and the answer to the question `belts` left open:
  the splitters and mergers a run passes THROUGH. They carry no spline — a splitter is a point
  with a facing, not a route — so they are a row shape of their own, the machine record minus
  the recipe and clock a splitter has no business having. 848 on the reference world: 481
  splitters, 364 mergers, 3 smart splitters. Without them a belt-only view had a four-metre
  hole at every junction, and a run that visibly stops and starts again is a run a reader has
  to guess is one run.
* **`Build_ConveyorCeilingAttachment_C` is deliberately excluded**, and that exclusion is
  pinned by a test. It shares the word and is a different thing — a pole a belt hangs from,
  not a piece items pass through — so a filter matching `ConveyorAttachment` would swallow all
  95 of them and draw them as the same square. The hint list names the two families it wants
  rather than matching a prefix, for exactly this reason.
* **Their own list, and exactly one list.** An attachment is in the `elif` chain beside the
  machines, extractors and generators, so a class lands in one of them or none — which is what
  makes "the map has them" and "the map has them once" the same claim, and it is asserted at
  the projection rather than at the endpoint for that reason. They ride out on `/api/belts`
  rather than `/api/machines`: a splitter runs no recipe, draws no power, and is meaningless
  without the runs either side of it, so it travels with the runs and is drawn by the layer
  that draws them.
* **Not in it:** pumps, junctions, valves and fluid buffers. They carry no spline, only a
  header position — the same shape `attachments` uses, so the road is open; what is missing is
  a reason to draw a valve that the belt junctions did not also have.

### Schema 14: the plumbing knew all along (2026-07-31)

Schema 13 said flow direction is not in the save, and that is still true **of a pipe**. It was
never asked of the network. Prompted by the pipe next to a pump, where "direction is not
recorded" reads as obtuseness rather than as honesty.

* **What was there the whole time.** Every fluid connection is a component carrying
  `mConnectedComponent`, which the walk has been folding into `graph["material"]` since schema
  11 — **1,560 directed couplings on the reference save, total, symmetric, none crossing an
  `mPipeNetworkID`**. And the component's *name* types a machine's port: `PipeInputFactory`,
  `PipeOutputFactory`, `ConnectionAny0`/`1` — the game's own Consumer / Producer / Any,
  surviving into the file. So the graph and its typed ends were already in the projection.
* **One integer is the whole schema change.** A fourth column on a pipe segment, the index of
  its own actor in `graph["actors"]`, because the one thing missing was the *join* between a
  drawn pipe and the graph entry that owns it. **+2,481 bytes, +0.18%**, and every schema-13
  key is leaf-identical in the regenerated fixture. `PipelineConnection0` is `points[0]`:
  measured over 221 pipe-to-pipe couplings at a **median 0.06 cm**, against 52.5 m the other
  way round, with all 559 pipe-to-machine couplings agreeing.
* **The inference lives in `domain/world/flow.py`, not in the extractor**, which keeps emitting
  only what the save states. Two conservative models — a cut argument over the typed ports, and
  a pump being one-way — plus conservation propagation with a guard against inventing flow into
  a dead stub. **365 of 503 resolved**, each labelled `machine port`, `pump` or `propagated`,
  and the other 138 keep the old honest `unknown`: a pipe in a loop, or a trunk with producers
  and consumers on both sides, genuinely has two legal answers without the rates.
* **Checked four ways, none of them the inference marking its own homework.** The two models
  overlap on 118 pipes and **agree on all 118**. A conservation audit finds **no node among 293
  where fluid appears from nowhere**. Following the flow to its end from all 365 **never
  arrives at a producer or leaves a consumer**. And water extractor → coal generator is known a
  priori: **39 right, 0 wrong**. The pump convention has its own oracle — this world's
  unfinished 696 m oil lift, 9 pumps climbing 240 m with neither end plumbed, comes out running
  **191 m uphill**, which is the only reading under which nine pumps make sense.
* **Chevrons at factory zoom, and nothing at world zoom.** 400 marks over 337 pipes, one per
  24 m with a 4 m floor, geometry in metres so they scale with the map, drawn only once a
  chevron clears 5 px (zoom 1, which is `FACTORY_MAX_ZOOM`). Frame band unmoved: 16.7 ms
  median and 16.8 ms p95 with them and without, interleaved over 720 frames each.
* **Belts are deliberately NOT marked**, though their points are already in travel order. The
  chevron function takes a polyline and a flag precisely so `ROUTE_CHEVRONS.belts = true` is
  the whole of the work; it stays false because 3,085 belt runs is a decision about the map
  that nobody asked for.

### Schema 15: the curve was in the file, and so was the stock (2026-07-31)

Two owner-reported gaps, one schema bump, and both of them the same shape of mistake as
schemas 12 and 13: the parser had already decoded the data and the projection was throwing it
away. "Belts aren't curved where they should be" and "a toggle to show storage — including
what's in it".

* **The curve. A spline point is three vectors, not one.** `pioneersav.trailers` has decoded a
  chain segment's points as `location, arriveTangent, leaveTangent` since it could read a chain
  at all, and `_belts` read `point[0]`. A pipe's `mSplineData` struct has carried
  `ArriveTangent` and `LeaveTangent` beside its `Location` the whole time, and schema 13 dropped
  them on the stated grounds that "the game builds pipes out of straight runs and elbows" —
  which is true, and does not follow: the six points of an elbow are its corners, not its curve.
* **What the tangents mean is measured, not assumed, and the save states the answer.** A chain
  segment separately records where it starts and ends **in centimetres along the chain**, so
  `end - start` minus the spline-less part is that segment's true arc length — a number the
  geometry did not write. Reconstructing each segment as a cubic Hermite through these tangents
  and integrating: **3,079 of 3,085 segments land within 1 cm** of the declared length, worst
  case 2.4 cm. The chord polyline the projection used to emit manages 2,336, is out by 46.8 cm
  at p95 and by **16.4 m** at its worst; over the 848 segments that actually bend the split is
  **843 against 208**. One measurement settles both the Hermite basis and which stored vector is
  the arrive and which the leave. Pinned as an integration test over the whole save folder.
* **A fourth belt column and a fifth pipe column, per SPAN and only where it bends.** A span
  needs the leave tangent behind it and the arrive tangent ahead, so the pair is the unit — which
  also drops the two vectors nothing can use (the first point's arrive and the last's leave, the
  only place the save stores a bare unit vector). A flat span stores `0`; a route with no bend
  anywhere gets **no column at all**, so **2,119 of 3,085 belt pieces and 207 of 503 pipes are
  byte-identical to what schema 14 emitted**.
* **"Flat" is a bound, computed in 7.5 ms.** A span is dropped when the curve provably cannot
  leave its chord by a whole centimetre — the control points' own rounding resolution, so what
  is dropped is finer than the geometry it is drawn through records. `_bulge` bounds the
  sideways offset by 4/27 of the tangents' perpendicular parts (both Hermite basis functions
  peak there) and solves the along-chord overshoot **exactly**, as a quadratic, because bounding
  that one crudely costs the whole optimisation: the game's commonest tangent is half the chord,
  for which the loose bound reads 29 cm on a 4 m belt and the true overshoot is zero. Verified
  against a 512-point tessellation over all 6,691 spans: **never smaller than the truth, never
  more than 3.08x it**. Sampling instead would have cost 485 ms against a 2.19 s parse.
* **Storage, a new `storage` key**: 151 rows — 61 Storage Containers, 44 Industrial, 6 Personal
  Storage Boxes, 33 Dimensional Depot uploaders, the HUB's own container and the Blueprint
  Designer's, and 5 fluid buffers. Solids join their own `StorageInventory` component to the
  actor that owns it; buffers carry `mFluidBox` cubic metres and take the fluid off the
  `FGPipeNetwork` that claims them — the same join `pipes` uses, so it is the game's answer
  rather than an inference from what the buffer is plugged into.
* **`inventories["storage"]` could say what the player owns and never where any of it was**, and
  the new rows reconcile against it with a remainder that is itself a finding: the old bucket
  rule names only `StorageContainer`, `CentralStorage` and `FreightWagon`, so the Personal
  Storage Boxes, the HUB's container and the Designer's have never been in that sum. The excess
  is **exactly** their contents, across 31 item classes.
* **The splitters are excluded, and that is the trap this key exists to avoid.** Every one of
  the world's 848 splitters and mergers owns a component literally named `StorageInventory`,
  741 of them non-empty, holding the one to three items physically inside the junction. Matching
  that name would report 848 phantom containers, draw them again over the belt layer that
  already has them, and count items in transit as stock. Machine input/output/fuel buffers are
  out for the same reason and are not lost — they are on their own machine's record.
* **Cost, measured on the reference save:** the projection grew 1,414,894 → 1,492,138 bytes
  (**+5.46%**) — `belts` +33,456 (+15.4%), `pipes` +11,205 (+21.8%), `storage` 32,572 — and the
  **time did not move**: 3,200 ms against 3,202 ms, medians of three interleaved runs, because
  `_bulge` is 7.5 ms and the storage join is a dictionary lookup per container. Carrying every
  tangent unconditionally would have been +112 KB on the belts alone; the flat-span drop is what
  makes it +33 KB.
* **The parity ripple needed one line.** `storage` joins `POST_11_ADDITIONS["keys"]`; the
  tangents need no entry at all, because they ride inside `belts` and `pipes`, which are already
  dropped whole — the same case schema 14's fourth column made, and worth writing down rather
  than leaving to be rederived. `vendor_parity.json` is untouched.

**On the page.** Tessellation is **zoom-dependent**, which is the whole reason it is
affordable: the map asks how far each curve is from its chord *in pixels right now*, so at
zoom -6 to -1 the network is **the identical geometry it drew before this existed** — no extra
points at the one zoom where all 3,085 pieces are on screen — and even at maxZoom the whole
network grows from 6,691 line points to 8,712. Frame band unmoved: **16.7 ms median, 16.7 ms
p95** at world and factory zoom with belts, pipes, machines and storage all on. A straight run
at factory zoom renders **byte-for-byte identically** to the old code — 0 differing pixels over
a 1080x840 crop — while the world's most curved belt differs across 16,350.

**Storage is its own layer, off by default, and deliberately NOT in `FACTORY_LAYERS`.** The
mechanics were checked first and do not block — `reveal()` builds "machines, belts, pipes and
storage" correctly for four names — so the reason is what the layer means. Those three are what
a factory is *made of*; containers are what is *standing in it*, and "where is my steel" is a
question a player asks on purpose. The owner asked for a toggle, and a layer four other gestures
turn on for you is not one. Colour picked by the method the pipe rust was: `#ad4f96` is dE 51.8
in CIE Lab from its nearest filled footprint box (the generator red) and 48.5 from the nearest
biome ground, with the fluid buffers one house-sized value step down at dE 16.7.

### Schema 16: two answers that were confidently wrong (2026-07-30)

The first bump that CORRECTS keys instead of adding them, which is also why it needed a decision
about the parity bank rather than a line in a list.

* **`inventories["storage"]` was matching three substrings where it meant a class list.** The
  rule read "does the owner's name contain `StorageContainer`, `CentralStorage` or
  `FreightWagon`", and `STORAGE_CLASSES` four screens above it in the same file names six
  classes. Three of them contain none of those words — the Personal Storage Box, the HUB's
  built-in container and the Blueprint Designer's — so everything in those 8 containers was
  bucketed as a MACHINE BUFFER, which `stock()` deliberately will not spend. **10,667 units
  across 31 item classes** on the reference save: 2,309 Wire, 1,445 Concrete, 1,353 Steel Beams,
  440 Gifts, 125 SAM Fluctuators, and the alien remains a MAM node costs — 16 Hog, 5 Stinger, 2
  Spitter. Every affordability answer in the server reads `stock()`, so all 31 were understated
  by exactly their contents; **two came out at zero** with the player standing next to a box of
  them — 12 Wood and 34 Copper Ingot — and the Dimensional Depot is what kept the other 29 from
  reading as nothing at all, which is luck rather than a design.
* **It had already been written down as tolerable.** Schema 15's reconciliation test asserted
  `excess == outside`, and its note called the remainder "the reason that older sum was quietly
  short". Same eight containers; it was the bug, not a finding. That test now asserts the two
  counts are equal item for item, with the 31 classes and 10,667 units pinned separately so the
  equality cannot pass by both sides being empty.
* **The rule is now membership in `STORAGE_CLASSES`, and the `FreightWagon` word survives
  untouched.** A wagon is a vehicle, has no `Build_` class, and no save in the reference
  directory holds one — so its exact class name cannot be read off anything here, and changing
  a test that has stood since schema 11 on a guess would risk moving cargo nobody can check.
* **`yaw_of` returned 0.0 when a quaternion would not decode**, and 0.0 is the measurement for
  *axis-aligned*, which most of this world genuinely is. A failure was therefore published as a
  bearing, mixed in with 17,500 real ones and indistinguishable afterwards. It returns `null`
  now — the claim `api.py`'s `_yaw` and the map have handled since schema 12, drawn
  axis-aligned and labelled "facing: unknown" — and `extract` counts the nulls into `warnings`,
  so a header decode going wrong stops looking like a world built on the grid. **Zero on the
  reference save**, which is why the fixture's placement bytes did not move.
* **The parity bank keeps the key rather than losing it.** `inventories` is one of the twenty
  the deleted oracle was measured against, and the bank is never re-recorded, so a corrected
  banked key had to give somewhere. The rejected option was a documented per-field exception —
  one line, at the price of never checking the player bucket, the machine bucket's other 6,500
  stacks or any of the 52 item classes again. Instead `as_schema_11` **reconstructs the old
  split**: only three classes moved, the `storage` key carries those same containers' contents
  per instance and is dropped whole anyway, so subtracting them from `storage` and adding them
  back to `machine` lands on the old numbers exactly, in integers. The cost, stated in the
  test: a fault this parser made in one of those eight containers would now move both sides
  together and cancel. That is eight containers of one key against the whole key for ever, and
  it is the limit an oracle always had. **All 31 banked saves still digest to the vendored
  parser's values on every key they were banked for.**
* **Cost:** the projection grew 1,492,138 → 1,492,188 bytes (**+50** — the net of ten item
  classes appearing in `storage`, which had 42 and now has 52, against seven leaving `machine`,
  which had 67 and now has 60). The only two keys that differ between the old fixture and the
  new are `schema_version` and `inventories`, and the move is conserved to the unit in both
  directions: what `storage` gained is exactly what `machine` lost, and `player` and `depot`
  are untouched.

---

## 19. One container reader, and the `gen` extra (2026-07-31)

Four generators read the game's own IoStore container, and until this work there was one copy of
the reader: the opening ~800 lines of `tools/gen_world_collectibles.py`. The other three got at it
by `importlib`-ing that 3,700-line script **by absolute file path** — which runs a collectibles
generator's module body to obtain a `.utoc` parser, makes the import graph invisible to every tool
that reads imports, and means a fix to the reader arrives in whichever caller happens to be run
next. The decoders those generators need were installed in three throwaway venvs and reached with
`--pyooz-path <venv>/Lib/site-packages`, inserted at the front of `sys.path` at runtime.

Both are now gone, and nothing an artifact says about the world changed with them: **every byte
of all six generators' output is identical across the move**, which is the claim the work was
organised around rather than a hope it ended with (see *How it was proved*). The only artifact
bytes this work changed at all are the four provenance sentences that described the old
arrangement, and they are enumerated below.

### What moved, and where it lives

`src/satisfactory_mcp/core/gameassets/` — generation-time code in `core` because four callers
need it, not because the server does. Nothing the server answers a request with comes through it;
the artifacts under `data/` do, and these are what cuts them.

| module | lines | what it is | used by |
|---|---|---|---|
| `iostore.py` | 217 | `.utoc`/`.ucas` reader, `ContainerError`, `oodle_decompress` | all four |
| `packages.py` | 680 | a cooked package's exports, names, property tags, transform chain | all four |
| `provenance.py` | 149 | which build an artifact was cut from; the staged rename that stops a directory saying two things at once | all four |
| `textures.py` | 52 | mip-chain arithmetic, BC1 → RGBA | map image, heightmap |
| `pyramid.py` | 253 | `tiles/{z}/{x}_{y}.png`, cut and renamed into place | map image, renders |

`gen_world_collectibles.py` lost 904 lines and is 2,880; the three importers lost their loaders.
`tools/` gained a package marker and `tools/_common.py` — `DEFAULT_GAME`, the shared `--game`
parser, and `require_gen`. The test suite reaches the generators the same way anything else does,
`from tools import gen_map_image`, and no test loads a `tools/*.py` by path any more.

### The seam: the decoders are arguments

`IoStore(paks, name, decompress)`, `ScriptObjects(paks, decompress)`,
`textures.decode_bc1_rgba(decoder, image_mod, raw, px)`, `pyramid.install_pyramid(sheet, image_mod,
…)`. Every module that needs a decoder takes it as a callable or a module rather than importing
one, which is what lets the suite drive them with stand-ins and lets the whole package be imported
on a machine that has none of the decoders installed. `iostore.oodle_decompress` is the real one,
ready to be handed in, and its `import ooz` is inside the function body.

Three rules are enforced by reading the source in `tests/test_architecture.py`, not by intention:

* `test_the_gen_extra_is_optional_at_import_time` — outside `core.gameassets`, no module under
  `satisfactory_mcp` or `pioneersav` has an edge to `ooz`, `pyooz`, `texture2ddecoder` or `PIL` at
  any depth; inside it, such an import may sit only in a function body.
* `test_gameassets_never_imports_dynamically` — no `importlib`, no `__import__`, no `sys.path`
  mutation. This is what makes the rule above checkable by AST at all.
* `test_gameassets_imports_nothing_but_the_stdlib_and_core` — the stdlib, `satisfactory_mcp.core.*`
  and `config`, and the extra lazily. `core` still knows nothing about anything above it.

### The environment: three side venvs became one extra

`[project.optional-dependencies] gen = ["pyooz==0.0.8", "texture2ddecoder==1.0.6",
"pillow==12.3.0"]`, and every invocation is `uv run --extra gen python tools/gen_X.py`. The pins
are exact because these three decide the **bytes** a generator writes: a silent upgrade is a
silent redraw of a map, or a silently different table, with nothing in the output to say so. They
are not chosen — they are the versions every previous run used, read off those venvs before they
were retired, which agreed on all three.

`--pyooz-path`, `load_oodle`, `load_imaging`, `MissingOodle` and every `sys.path.insert` that
reached a side venv are deleted. One environment holds numpy and scipy now, which removes a real
failure rather than a hypothetical one: the venv the map generator used carried **its own numpy**,
so `--enhance` — which imports scipy from the project — had a scipy compiled against a different
numpy, and the tool refused with `rc=6` rather than segfault. The check survives as an invariant
that should never fire.

A missing extra is answered in one place and in one sentence. `require_gen` try-imports what a
generator needs, prints the line that installs it and exits 2; `iostore.MissingGenExtra` carries
the same sentence for a caller that got past that. Neither reports `No module named 'ooz'`, which
says what happened and not what to do about it.

**This is not a licence position** and the earlier framing of it as one was wrong; see the
correction in [§15b](#15b-parked-derive-the-map-tables-from-the-installed-game-on-game-update).
Optional at import time is an operational property: the server, the parser, the domain and the
test suite run with none of the three installed, and only generation needs them.

### How it was proved

A baseline was banked first — every artifact of all six generators, plus a **snapshot of the save
directory** (which rotates every few minutes while the world is being played, so a live directory
is not a fixture). Each step then re-ran the generators into fresh directories and compared:
trees as sorted `(relpath, sha256, bytes)` manifests, JSON sidecars deep-compared leaf by leaf
with a mask for the fields that are *supposed* to differ between two runs — `transcribed`, which
is a date, and the measured `timings_s` / `seconds_to_draw` / `seconds_to_cut` clocks. Everything
else had to match exactly, and did, at every step.

The full acceptance run compares 4,103 files / 185 MB of `map.png`, artwork pyramid, heightfield
and both render pyramids, plus the two fixed-destination tables under `data/` (run, copied out,
`git checkout --` restored), plus the 21,845-tile `--enhance` pyramid — which is worth stating
because Real-ESRGAN on the GPU turned out to be **bit-stable**: two runs from identical code
produced 21,845 identical tiles, so the enhanced levels could be compared by hash like everything
else, on this machine and this driver.

### The sidecars used to assert the old arrangement, twice over

Each decoder block said its decompressor was *"not a dependency of this project, never imported
from `src/` or `sidecar/`"*, and the heightfield's said its container reader was
*"`tools/gen_world_collectibles.py`'s IoStore reader, imported by path"*. All four sentences are
now false, and a provenance block that describes a mechanism the artifact was not produced by is
worse than one that says nothing. They now name the extra, the pin, the invocation and
`core.gameassets.iostore` — four leaves, enumerated so the byte-identity harness could fail both
on one of them not changing and on anything else changing:

```
map.json                  _meta.decoders.oodle.role          (plain and --enhance)
heightmap/meta.json       decoders.oodle.role
heightmap/meta.json       decoders.container
world_collectibles.json   _meta.source.placements.decompressor.role
```

`data/world_collectibles.json`, the one committed artifact of the four, keeps the old wording
because **it is a record of the run that cut it**, at which the old wording was true; editing a
generated table by hand would make it describe a mechanism that did not produce it, which is the
defect being fixed. The next regeneration replaces it.

### What deliberately did not move

The questions. `gen_map_image.py` still owns the map frame — those corners were *measured* by that
tool against the artwork, and re-typing them elsewhere is exactly how three pyramids come to
disagree about where the world is. The refusals, the `--force` semantics, the validation gates and
every per-tool constant stayed with their tool. What moved is only what four callers were already
sharing badly.

