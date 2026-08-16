# Backlog: what is broken, what is missing, in the order to fix it

From three audits on 2026-08-02 (tool-chain coherence, map/text seam, unexposed domain
knowledge) plus the field report an LLM client filed after playing with the surface.
Deduplicated: where two audits found the same thing from different sides it appears once, with
both citations. Numbers are stable and are cited elsewhere, so a closed item keeps its number
rather than being deleted. Parked FEATURES are [parked.md](parked.md) §21 and §22; work that is
not a defect is [roadmap.md](roadmap.md).

**State, 2026-08-06: 36 of 40 closed.** Everything in P0, P1 and P5 is done, and everything in
P2 but the power half of item 15. What is left is item 15's power topology, item 36's one
remaining ablation knob, and five of the thirteen P3/P4 bullets — three of which are deliberate
and are marked so.

**Added 2026-08-10: P6, items 37–42** — in-game measurements the fluid model is waiting on.
They are not code defects and are not counted in the 36 of 40 above; they are here because each
one is a concrete thing to build, and the alternative is that they are remembered rather than
findable.

Each closed row names the commit that closed it. The claims below were re-verified against the
code on 2026-08-06 rather than taken from the commit titles, and two of them turned out to have
been wrong when they were written; those are marked.

**The one structural finding, which explained a third of this list, is closed.** The
projection's richest recent additions — storage (15), floors, crates (18), the crate inventory
bucket (19) — had gone to the web map and never to the LLM, so an assistant could not ask what
the map drew. The general fix was one habit rather than one commit: **a projection key is not
finished until both interfaces read it.** It held. The habit is the part that has to survive,
and the two modules landed yesterday — `domain/world/logistics.py` and `domain/world/timeline.py`
— are already the same shape from the other direction: correct, cached, and read by nobody.

---

## P0 — answers that were wrong or crashed

All closed. These produced a false belief in the reader, so nothing outranked them.

| # | What it was | Closed by |
|---|---|---|
| 1 | `trace_upstream` raised `TypeError` for every factory label and every selector — its own parameter help listed labels FIRST — because it indexed `resolve_factory`'s `list[str]` as dicts. | `6918c57` |
| 2 | `show_on_map` handed the player a satisfactory-calculator.com link for every target except a sited plan, so the README's flagship example opened a site that cannot draw their world. `local_map_url` is now unconditional and comes first; the README says the public map comes second. | `0e9730b`, `6d3fdae` |
| 3 | `describe_location` stated "there is no heightmap in any data this reads" while `/api/inspect` printed terrain from the 1 m heightfield on the same machine. It now passes `terrain_field` through the seam that was always there and prints `terrain_m`. | `25147cb` |
| 4 | `machine:<id>` accepted any string and answered "0 machines" for machines that exist; printed ids were truncated to their last 10 chars and resolved to nothing. Unknown ids now raise `SelectorError` naming them, and instances print in full. | `32274b9`, `986d513` |
| 5 | `commission_plan(limit=60)` sat outside its own published schema, so a client echoing the default got a hard error. | `fc59db9` |
| 6 | `search_recipes` and `alternates_for_item` took `save` but not `world` while computing HAVE/LOCKED from world state, so a multi-world install was answered against the default world. | `e164ca2` |
| 7 | Belt length disagreed between halves — the map drew the true Hermite spline, `search_conduits` summed chords, up to 16.4 m out on one piece. `_length_m` now integrates the span and falls back to the chord only where no tangents exist. | `4af96e3` |
| 8 | `list_regions` printed a raw centroid, which for a concave region lands in its neighbour. `label_anchor()` is now the single implementation, called by both sides. | `e588fcb`, `4a26bfd` |
| 9 | Three different "already named" filters over one clusterer. `LabelStore.covers()` is now the one predicate, called by the router and the tool. | `1ba254e`, `a5714ca` |
| 9b | `/api/factories` published `score: 0.0` for every proposal: the merge loop computed real per-cluster cohesion and the constructor hardcoded zero. Keyed by `frozenset` beside `seeds` and looked up by `held`, exactly as `seeded_by` does. | `ddd6c30` |
| 9c | `mam_research` said cost was checked against "carried, crates and the Dimensional Depot" when schema 19 deliberately took crates OUT of spendable stock. Both the tool note and the same stale sentence in `save-projection.md` now say storage containers, and name what does not count. | `a0a2ec9` |
| 9d | `mam_research` ignored `research["unlocked_trees"]` and `research["ongoing"]`, so a node in an unopened tree read `READY` and research already underway read `todo`. Now `TREE SHUT` and `RUNNING {n}s`, both excluded from `startable=`. | `a0a2ec9` |
| 9e | `trace_upstream` carried `Trace.ambiguous` and `Trace.truncated` and reported a possibly over-counted, depth-truncated walk as complete. Both print, and a truncated walk says it is a FLOOR. | `43fac87` |

## P1 — instructions the client could not follow, and dead ends

All closed.

| # | What it was | Closed by |
|---|---|---|
| 10 | 15 tools printed "call again with `offset=N`" and had no `offset` parameter; only 5 of 44 accepted one. 23 of 50 tools take one now, `search_resource_nodes` among them, and the sentence is gated: passing `offset` to `render.table` is the caller's DECLARATION that it pages, and everything else says "narrow the query, or raise limit". | `91c4d90` |
| 11 | `search_conduits` printed `connects: pipe:333 -> pipe:335` and told the reader to follow it while taking no run id. `resolve_origin` now accepts `chain:`/`pipe:` for every tool that resolves a place. The spelling was settled on the tool's ident; the map's belt popup keeps `chain #n` as a caption of the same number. | `8f6f03e` |
| 12 | `diff_vs_save` ordered actions and dropped the instance ids it had already computed. Each actionable row carries `act_instances` — the verb's own machines, deliberately not the first three of `have_instances` — rendered as reusable `machine:` selectors. | `ce2ba66`, `ae87521` |
| 13 | Bare platforms were listed by an index no tool accepted. `slab:<n>` now resolves for `describe_location`, `show_on_map` and `plan_factory(site_at=)`, and closes item 28 with it. | `1b6b242`, `56abf09`, `70df1ae` |
| 14 | `diff_vs_save` documented a plan-id cross-check that `plan_factory` never printed for an unsaved plan. | `7d063c9`, `97ce1f6` |

## P2 — the two halves disagree, or one half is blind

One item open, and only half of it.

**15 — OPEN, power only.** Floors, storage contents and crates are closed: `factory_floors`
(`interfaces/mcp/tools/floors.py`), `storage` and `crates`
(`interfaces/mcp/tools/inventory.py`), landed by `4f47e33`, `571a59a` and `3678718`.

The power half was less blind than this row claimed, and the correction matters because the
remedy is different. Power ISLANDS already reach text: `domain/factories/identity.py::bases`
calls `graph.machine_components("power", skip=graph.towers())` and `factory_map show=candidates`
prints them under `## power islands (bases)`. What no MCP tool exposes is the pole and wire
graph itself — spans, and generation and draw per circuit — read today only by
`interfaces/web/routers/power.py`.

Before building that, read [roadmap.md](roadmap.md) §5. The per-circuit ledger is **measured
false**, not merely unbuilt: `machine_components("power")` with the towers left IN returns three
components of 563 / 5 / 5, which is one grid and two strays. The six "islands" are an artifact of
the towers-REMOVED clustering above, which is a factory-identity heuristic and not an electrical
fact. The salvage worth having is one line in `factory_health` — "these 8 machines are wired to
nothing" — because `stalled` today says "power, or a monitor that lies" and cannot check which.

| # | What it was | Closed by |
|---|---|---|
| 16 | The map said `OreIron`, the assistant said `Iron Ore`; `/api/nodes` was the only payload that did not resolve a display name. `NodeRow.resource_name` now does. | `838d8ab`, `bc2aaa8` |
| 17 | The map had no `LOCKED` state: a node the text surface excluded as unreachable was drawn as an ordinary free dot. `NodeRow.reachable` carries it, `None` where the unlock set is unknown. | `839d389` |
| 18 | `name_factory` and `site_plan` wrote to the labels and plans directories while the watcher globbed `*.sav` only, so the one collaborative moment was the one the map missed. Both directories are watched and publish a second event kind. | `5b694d1` |
| 19 | Every popup printed an MCP selector and nothing on the page could copy one. Click-to-copy in `dom.ts`/`copy.ts` upgraded every popup at once. | `27c5f66` |
| 20 | No tool could name an item you own or where it is: 151 container rows, crate contents and four inventory buckets were read by the web routers and by zero MCP tools. `stock(item=, where=)` prints spendable, carried, storage, depot, buffers and crates, and with `where=True` joins to region names. `machine_buffers()` has a reader at last. | `571a59a`, `3678718` |
| 21 | The only measured numbers in the project survived as two scalars: every per-machine figure weighted by its own 300 s monitor was destroyed at the moment it was computed. `FactoryView.measured_draw_mw` accumulates in the loop that was already running; it prints in `factory_query(of="power")` and as a `measured MW` column in `factory_health`. | `c39a391` |
| 22 | `phase_requirements` printed what a phase still needed and stopped, while `mam_research` did the stock join 150 lines away in the same file. `have` and `short by` columns, plus a deliverable verdict. | `2acc502` |
| 23 | `tapped_by` and `tapped_clock` were computed for every node on every call and rendered as the bare word "tapped". The occupant now prints as `Miner Mk2 @250% OFF`, with z. | `f205ade` |
| 24 | Truncation without an envelope, in four places: `somersloops`, `diff_vs_save`'s cost table, and `factory_health`'s `blocked_on` and `starved_of`. All four pass `total`. | `91c4d90`, `8f37f21`, `26da417` |
| 25 | 3-D positions rendered as 2-D throughout: `MachineRow.pos` never printed, `factory_sites` dropping centroid z, occupied slabs losing the bbox and z-span that bare slabs got. All print. | `4fa18da`, `85a3776` |

## P3 — convention drift

Three closed, three narrowed by an alias rather than settled, two open.

- **Two radius grammars — OPEN.** `near:0,-2000,900` for node selectors
  (`domain/spatial/select.py`) against `near:-1069,-1273@200` for machine selectors
  (`domain/factories/select.py::_by_near`). Untouched.
- **`describe_location`'s two floats — narrowed.** It still declares `x_m`/`y_m`, but now also
  takes `at=`, which accepts `"x,y"`, `me`, a factory, `slab:<n>` or `chain:7` like every other
  tool. What remains is demoting the float pair, which is a compatibility decision rather than a
  fix.
- **Five spellings of "which view" — narrowed.** Every one gained a `show=` alias (`294c820`).
  The four originals — `of=`, `detail=`, `mode=`, `status=` — are still the declared primaries,
  so a client reading the schema still meets five spellings and a client writing `show=` always
  succeeds.
- **`kind=` means four vocabularies — OPEN, and it is five now.** `search_recipes`
  (`part|building|manual|all`), `list_buildings` (eleven values), `storage` (`solid|fluid|all`),
  `search_conduits` (`belt|pipe|all`), and `search_resource_nodes`, which forwards a `kind:`
  selector term that still errors with `kind must be node|well_sat|geyser, got 'all'` — the one
  that rejects `"all"`. Deliberately left alone; settling it is a surface break, not a bug fix.
- **Name filters: `query=`, `search=`, `with_resource=`, `group=` — OPEN.** All four survive.
  `query=` is now an alias for `search=` in `planning.py` and `progression.py` but stays primary
  in `gamedata.py`; `with_resource=` is still the only spelling in `list_regions`; and `group=`
  carries two unrelated meanings — a real category filter in `collected_from_world`, and a
  deprecated alias for `mode=` in `search_resource_nodes`. Deliberately left alone, same reason.
- **`rank_build_sites(top=)` unbounded — DONE.** It takes `Limit`; `top=` is a deprecated alias.
- **`render.table`'s `limit=` a documented no-op — DONE** (`91c4d90`). It truncates.
- **Three off-map spellings — DONE.** One `OFF_MAP` constant in `domain/spatial/regions.py`.

## P4 — missing round-trips

- **`flow.py` did not know the T junction — DONE.** `_BODIES` listed the cross and both tanks
  by build class, so a `Build_PipelineJunction_T_C` was not one volume: its three ports stayed
  three unjoined nodes and the pipe network was CUT at every T. Measured on the owner's newest
  save, which holds 12 of them: 92 of 657 pipes could not be oriented that now can, and **no
  confident answer changed** — it cost coverage, not correctness. Invisible until now because
  the reference fixture contains no T junction. `domain/world/headlift.py` avoids the whole
  class of bug by matching on `Building.native`, where the T and the cross are one class.
- **Plans: no rename, no way to read a stored plan's arguments — DONE** (`0dd05e4`).
  `rename_plan`, and a detail view that reads one stored plan in full without solving it.
- **Labels: no rename; cannot add or drop one machine — OPEN.** `LabelStore` still exposes only
  `put`, `remove`, `find`, `label_for` and `review`, and `name_factory`'s own docstring says
  calling it again with the same name re-anchors the label to the current selection. Re-anchoring
  a whole selection to correct one machine is the workflow this is meant to remove.
- **Sitings: `list_plans` showed `x,y` only — DONE.** Yaw and footprint are in the row.
- **`factory_sites` rows carried no identifier — DONE** (`85a3776`), with altitude back.
- **`maplink.COLLECTIBLES` is dead code — OPEN, unchanged.** Its only reference anywhere is an
  assertion in `tests/test_maplink.py`; `layers_for()` reads `LAYERS`, `WELL_STEMS` and
  `WELL_ONLY` and never touches it. "Where are the hard drives" still gets a map link from
  neither surface.

## P5 — capability already built, not yet reachable: WIRE IT UP

Lukas's decision, 2026-08-02: **wire these up, do not delete them.** All closed but item 36,
which was misfiled from the start.

| # | What was unreachable | Closed by |
|---|---|---|
| 26 | `UnlockDelta.unlocked_by` and `.ok` — `rank_unlocks` said an alternate was worth 14,540 MW and never said which schematic granted it, and an infeasible solve reported `gain=0`, indistinguishable from worthless. A `granted by` column and an explicit `INFEASIBLE:` note. The highest-value wiring on this list: the tool's whole purpose is deciding what to research, and it withheld the name of the thing to research. | `b251b2b`, `3ffc3cf` |
| 27 | `flow.py`'s `basis` — the *why* behind every inferred pipe direction — reached `/api/pipes` and nothing else. A `basis` column on `search_conduits`. | `73e0ccb` |
| 28 | `Structures.machines_on()` and `.summary()` had no consumer in `src/`. Both are now the machinery behind `slab:<n>`, which closes item 13 with it. | `1b6b242`, `56abf09` |
| 29 | `FactoryView.internal()` — "the mark of a self-contained line", per its own docstring — was rendered by no `factory_query` aspect. Now an aspect. | `f78d0a7` |
| 30 | Top-level `pipe_networks`, 19 rows, no consumer outside one test. `search_conduits show="networks"`. | `5e1c06d` |
| 31 | `list_pending_hard_drive_choices` held each option's granted recipe list and used it only to decide whether to append "(nothing new)". It prints them. | `4222b21`, `6133e5e` |
| 32 | `progression["last_active_schematic"]` and `research["last_used_hard_drive_id"]` had no consumers. "What you were last working on" is in `world_summary`; the last drive spent is context in both hard-drive tools. | `889fec4` |
| 33 | `somersloops` read `boost_in_save` so a caller could cross-check the computed boost, then never printed it. It prints, with a note when the two disagree. | `8f37f21` |
| 34 | `research["ongoing"]`. Closed as part of 9d. | `a0a2ec9` |
| 35 | `load_collectibles(strict=True)` and `CollectiblesUnreadable` existed to tell "you never ran the generator" from "what it wrote is broken", and zero callers passed `strict=True`. The collectibles service now does, and the error path says which. | `68ddd8c` |

**36 — PARTIAL, and this row was wrong when it was written.** The `_solve(drop_actor=, cuts=,
one_way=)` ablation knobs in `domain/world/flow.py` were filed here as "never passed by
anything". Two of the three already were:
`tests/test_pipe_flow.py::test_the_two_models_never_disagree_where_both_of_them_speak` passes
`one_way=False` and `cuts=False` and asserts the 118-pipe overlap agrees, and it has done since
`ef59c62`, which predates this backlog. The audit that filed the row searched `src/` and read
the absence as proof.

What is genuinely left is `drop_actor=`, which nothing passes anywhere, and the assertion this
row asked for: that ablating each knob degrades the riser-violation count. Today the test proves
the two models agree, not that either is doing work. It is still **not a surface feature** — its
honest home is that test file.

## P6 — measurements the model is waiting on

Rigs to build in the owner's world, parked with the fluid experiment. [fluids_model.md](fluids_model.md)
is the authority for every constant and every rule these would move, and rows 37–39 and 41 name
the section that holds the design rather than repeating it; rows 40 and 42 are written out in
full because that document does not carry them yet. **The save file is the instrument** — the
method, including the two ways this experiment has already lost a measurement, is that
document's *How to measure this again*.

Order matters at the top of the list. **37 and 38 both unblock 39, and 39 must not be run before
one of them lands.** 40, 41 and 42 are independent of all three and of each other.

**37 — The dense series on the suction rig. OPEN, and the cheapest thing on this list.** The rig
is already standing: `HL_BUFFER` column, tank drained to ≈19%, feed severed, one Mk1 at centre
−9.591176. Change nothing, and save every ≈30 s for ≈5 minutes, copying each autosave to a stable
name as it appears. Read the suction piece `…2147203939` in each. Two saves 299 s apart show that
piece full and then empty, and cannot tell a line that filled once and drained from one that
fills and empties on a cycle — the whole reading of the rig turns on which it is. See
*A running pump fills its own suction line* and the discharge bullet in *Open*.

**38 — The discharge sink. OPEN.** The same rig, given somewhere to put fluid: a descending Mk2
pipe from the pump's outlet into a spare 400 m³ Fluid Buffer, **every crest at or below
`pump centre + 22.801` = +13.21 m** and never above the column's +23.0 cap, so the sink is inside
the pump's measured reach by construction and a failure cannot be blamed on height. This tests
the leading candidate for why a pump drawing 4.0 MW for 497 s moved 11.6 millilitres: that a
capped dead end with no consumer is not a discharge at all. **Cost to weigh first:** reusing the
`BUF_OUT` buffers would end that closed pair, which is the control the model document leans on
for what a pumpless, flat, closed system does on its own.

**39 — `SUCK_HIGH`. BLOCKED on 37 or 38.** The second half of the suction rig, designed and
needing no construction: move the Mk1 to a centre of **+10.000 m**, which is 25.25 m above the
tank connector — 2.24 m past the 23.006 estimator and 1.01 m past the assumption-free 24.244
ceiling — with the column already reaching +23.0. It decides whether a pump's inlet is ungated or
bounded by its own reach, which is the only open question in the model that could turn a silence
into a fault. **It cannot be read on a rig that passes nothing**: "the column above the pump is
dry" says nothing about suction until the rig is shown to move fluid at all.

**40 — Flow versus height, with a Fluid Buffer as the flow meter. OPEN, and not yet in the model
document.** The rating behind `machine_head_lift_m` is the one constant on that page taken purely
from the game's own prose: a dead-end column measures the **ceiling** and says nothing about the
10 m rating. **The rig:** one Water Extractor, one pipe, one **Fluid Buffer** — no power on the
line, no solids, no recipe, nothing that can throttle. `Δstored_m3` between two saves ≥60 s apart
is the delivered flow, and the buffer is passive as long as it is read between **20% and 80%**
fill (above 100% it transmits head and stops being a sink). Three climbs, rebuilding only the top
of the run: **2 m** as the control, which must read the nameplate; **10.5 m**, which is the
question; **11.5 m**, which must read zero and so proves the rig can fail. Buffer base =
extractor connector + climb − 1.75000. **This supersedes an earlier four-machine design that
could not have worked:** one extractor at 120 m³/min feeding one Coal Generator at 45 has 2.7×
headroom, so the flow could halve twice and the generator would still read 100% uptime.

**41 — The 8 m trapped-air rig. OPEN.** Whether the deficit below a waterline is a fixed **volume**
per piece or a fixed **fraction** of its capacity — the one question `HL_FINE` could not answer,
because the 7.0 m³ capacity floor makes a 2 m piece hold nearly as much as a 4 m one. **The rig
uses LONGER pieces, not shorter**, which is the opposite of the standing assumption: an 8 m column
has capacity 14.87 m³, safely above the knee, and the two hypotheses predict estimator gaps of
≈1.85 m and ≈0.92 m — 18× the noise apart. Design and arithmetic are in *Open* in
[fluids_model.md](fluids_model.md).

**42 — The free control: dismantle two pumps on one coal network. OPEN, not yet in the model
document, and it costs about thirty seconds in-game.** Thirty-two Coal Generators in the owner's
base sit **8.150 m above their extractors** at 100% uptime and 96% supply utilisation, which is
the most flow-sensitive load he has — but every one of those networks has pumps on it, so none of
them is currently evidence about machine head lift. Two of the networks carry only **two pumps
each** (numbered components 0 and 18 by the walk that found them, so re-derive the numbering
rather than trusting the index). Dismantling both pumps on **one** of them leaves 8 generators
machine-fed over 81.5% of the stated 10 m rating, with the other three coal plants standing as
untouched controls. If those eight keep running, the rating carries a real load at 8.15 m; if
they starve, the model has its first measurement of the rating rather than the ceiling. This is
the only item on this list that needs no new construction at all.

## Not doing, and why

- **Region tint 256 m vs lookups 64 m** (14.1% vs 5.3% mislabel): known, documented, and the fix
  is a different grid, not a bug fix. Leave it, or print the confidence word on the tint.
- **A "copy question" affordance on the map**: the LLM does not read the page and a generated
  English sentence guesses at the human's question. A selector and a coordinate are enough, and
  since item 19 they can be copied.
- **Coordinate rounding drift** (`serial._m` rounds, text emitters truncate): sub-metre and
  cosmetic. Worth one sweep if someone is already in those files.
