# The MCP surface, the context budget, caching and tests

Part of the [SatisfactoryMcp design spec](../DESIGN.md) — §10 is the tool surface with its
transcripts and the context budget that shapes every response, §11 is caching and §12 is the
testing contract. Section numbers are continuous with the rest of the spec;
[DESIGN.md](../DESIGN.md) indexes it.

---

## 10. MCP surface

`mcp` **1.28.1**, `FastMCP`, stdio, protocol `2025-11-25`.

> **`structured_output=False` on every text tool.** A tool annotated `-> str` gets an `outputSchema`
> *and* has its payload echoed into `structuredContent` — a measured **1.96×** wire-size tax for zero
> benefit (580 → 1,136 bytes on the real Plastic response). Enforce via a shared decorator.

### 10.1 Tools

**Game data:** `search_items`, `search_recipes`, `recipe_detail`, `alternates_for_item`, `list_buildings`
**Save state:** `list_worlds`, `world_summary`, `unlocked_recipes`, `power_report`, `node_occupancy`, `factory_sites`, `phase_requirements`, `power_shards`, `collected_from_world`
**Factories:** `factory_map`, `propose_factories`, `factory_query`, `factory_health`, `select_machines`, `name_factory`, `list_factories`, `forget_factory`
**Spatial:** `list_regions`, `describe_location`, `search_resource_nodes`, `rank_build_sites`
**Layout:** `plan_layout`
**Planning:** `plan_factory`, `plan_layout`, `diff_vs_save`, `bom`, `list_plans`, `forget_plan`, `explain_byproducts`, `compare_recipe_options`
**Hard drives:** `list_pending_hard_drive_choices`, `advise_hard_drive_pick`

```
plan_factory(objective="max_mw", target_item=None, sources=[...],
             exports=["__MW__"], export_minimums={}, only_free_nodes=False,
             allow_sinks=True, save=None, world=None, limit=15,
             logistics_items=None)
  -> summary (net_MW, machines, grid_import, exports, raw, sunk)
     + warnings/binding first, then a process table with a BUILD marker
     + a logistics table of `limit` flows, plus any logistics_items pinned

search_resource_nodes(sources=[...], resource=None, purity=None, kind=None,
                      only_free=False, group="field"|"node", limit=25)
  -> per-field clusters (region, grid, centre, purity mix, total/free, spread)
     or per-node rows whose ids feed straight back in as node: selectors

search_recipes(query="", consumes=None, produces=None, kind="part"|"building"|"manual"|"all",
               only_alternates=False, include_events=False, save=None, limit=10, offset=0)
  -> a census over ALL 872 recipes broken down by kind and HAVE/LOCKED, then rows

bom(item, qty=60, allow_sinks=True, outlets=[], exclude_recipes=[], only_recipes=[], limit=20)
  -> raw totals + one row per item: made/min, used/min, the recipe chosen, machines, building
```

`warnings` and `binding_constraints` come **first** — that's where the insight is ("Blender unlocked but
0 built"; "920/min resin needs an outlet or the line stalls").

**No `plan_chain` by recursive expansion.** It is unsound on this user's own recipe set: Recycled Plastic
(30 Rubber + 30 Fuel → 60 Plastic) and Recycled Rubber (30 Plastic + 30 Fuel → 60 Rubber) form a genuine
2-cycle and **both are unlocked**. There is no correct depth limit. The LP is the only engine.
`bom` ([§10.1c](#101c-bom--the-flattened-bill)) is the sanctioned answer to the question `plan_chain`
was meant to answer, and it is a presentation layer over `solve`.

Also required and absent from the first draft: a **power target** must be expressible (the driving use
case is MW, not an item), and generator fuel throughput needs `mEnergyValue` with its per-litre /
per-item split.

### 10.1a Plan persistence

`diff_vs_save` re-solves rather than taking a plan handle, which means retyping fifteen
arguments to ask "how far along am I". Plans are now nameable:

```
plan_factory(..., save_as="north oil", plan_notes_text="...", for_factory="oil setup")
plan_factory(plan="north oil")            # recall and re-solve
diff_vs_save(plan="north oil")            # diff without retyping
plan_layout(plan="north oil")
list_plans() / forget_plan(name)
```

**The request is stored, never the solution.** A solve depends on the unlocked recipe
set, which nodes are free and which buildings exist — all of which move as the game is
played. A stored solution would keep answering about a world that no longer exists, and
would do it silently. Storing arguments and re-solving on recall always answers about the
world as it is now.

That gives `plan_id` a second job. It already hashes the arguments *together with* the
save-derived solve inputs (§ scenario), so recording it at save time and comparing on
recall detects exactly the interesting case: **the plan did not change, the world did.**
`list_plans` reports that as `world moved`.

**The defaults trap.** MCP fills declared defaults in before a tool sees them, so
`objective` always arrives as `"max_mw"` and a naive merge would clobber every recalled
plan with it. `PLAN_DEFAULTS` records each argument's declared default, and a supplied
value counts as an override only when it *differs* from it. The honest cost: a recall
cannot explicitly reset an argument back to its default — re-save the plan for that.

Overrides are applied but **not persisted**, and the response says so. Stored args are
filtered to those that shape the solve — not `limit` (presentation) or `save`/`world`
(which file was read, not what was asked) — and defaults are dropped, so a stored plan
reads as the request that was made. `Plan.kwargs()` filters unknown keys so a plan saved
by an older build cannot break a newer `build_scenario`.

Stored per world under `saveIdentifier` in `user_data_dir/plans/`, beside the labels and
for the same reason.

**Scoping.** `diff_vs_save(factory=...)` — or a plan saved with `for_factory` — limits
what counts as *already built* to that factory's machines. Unscoped, "you already have 12
of these" counts constructors on the far side of the map that are busy doing something
else, which is the wrong answer to "how far along is the aluminium setup". On the
reference save, scoping an aluminium plan moves `to_place` from 156–167 to 188.

Node handling is the subtle part. A node tapped by a *different* factory drops out of
**both** the reusable and the free set:

- left in `tapped` it would read as already built for this plan;
- moved to `free` it would plan a second miner onto an occupied node.

`extractor_on` is deliberately left un-scoped, because occupancy is a fact about the world
rather than about the factory being asked.

**`plan_layout(factory=...)` scopes differently, because a layout has no coordinates.**
`build_layout` is abstract on purpose — blocks, buses and floors with sizes in metres —
since a player places machines themselves and a solver inventing positions would be both
wrong and unwelcome. So scoping cannot mean placing blocks. It answers the two questions
the abstract layout leaves open once you know *where* it goes:

- **Does it fit?** The structure layer knows the slab's tile count and extent; the layout
  knows its peak-floor footprint. The gap is foundations to pour. A shortfall is reported
  as a number, not a failure — floors stack, so building up may resolve it, and the note
  says so when the factory is already multi-storey.
- **What already stands there?** A block matched by (building, recipe) against machines in
  that factory is not work. On the reference save an aluminium layout reads *"106 tiles
  across 1 platform, 135×135m; layout needs 338 at its widest floor — needs 232 more tiles,
  or a floor above. 1 block standing, 36 to build."*

Two honesty constraints. The standing count is **consumed as it matches**, or one smelter
would satisfy every Iron Ingot block in a split process. And a standing machine is reported
as *present*, never as *correct* — it may be on a different clock or feeding something
else.

### 10.1b Reverse recipe lookup — "what consumes X", and proving the list is closed

`search_recipes` matched recipe **names** only, so "what eats Rubber" had no answer. What actually
happened was that candidate consumers were recalled from memory and checked one at a time — roughly
eight speculative calls, and at the end of them still no way to say the list was complete. The worry
was specific and correct: *"if some Tier 7-9 building eats rubber, I'd have missed it."*

**A parameter on `search_recipes`, not a new tool**, for the same reason `search_resource_nodes` took a
`mode` instead of splitting ([§7.2a](spatial-and-map.md#72a-node-lookup--one-tool-three-modes)): the body is ~90 % shared —
filter, sort, page, render, HAVE/LOCKED — and only the predicate differs. §10.3's rule against duplicate
surfaces applies with more force here, since a `consumers_of_item` tool would sit directly beside
`alternates_for_item` and make tool selection worse. `produces=` comes along free and is the only way to
ask which **build-gun** recipe makes a Blender, which `alternates_for_item` is part-only by construction.

Completeness is bought with one rule: **the census is counted over all 872 recipes, never over the page.**
`kind`, `include_events`, `limit` and `offset` decide what is *shown*; they never move the header counts.
So the default part-only view of Rubber still opens with

```
# 26 recipe(s) consume Rubber: 15 part [5 HAVE, 10 LOCKED], 7 building [6 HAVE, 1 LOCKED],
  4 manual [3 HAVE, 1 LOCKED]. Counted over all 872 recipes; kind/limit change the rows, never these totals.
! kind='part' hides 7 building and 4 manual recipe(s) that also consume Rubber -- pass kind='all'
```

Measured on the reference save, and this is exactly the case the worry named: the seven building
recipes eating Rubber are the **Fuel-Powered Generator (50/build)**, **Resource Well Pressurizer (100)**,
**Blueprint Designer Mk.2 (100)**, **Fluid Truck Station (20)**, **Packager (10)**, **Valve (4)** and
**Power Pole Mk.3 (3)**. A part-only answer misses eleven of twenty-six consumers. Plastic is worse:
10 part against **10 building**, all ten unlocked.

**Build costs must never render as rates.** `mManufactoringDuration` is 1.0 on all 547 building recipes,
so `amount × 60 / duration` turns the Fuel-Powered Generator's 50 Rubber into 3,000/min and The HUB's
20 Iron Ore into 1,200/min. Part rows carry `/min`, building rows `/build`, manual rows `/craft`, and the
suffix is on the cell rather than the header because `kind="all"` mixes them in one table.

FICSMAS recipes stay hidden by default and are **counted anyway** — 12 event recipes consume a FICSMAS
Gift, and a total that quietly dropped them is a total nobody can rely on.

### 10.1c `bom` — the flattened bill

`bom(item, qty)` gives the total raw and intermediate rates for `qty` per minute of an item. It was hand-
multiplied off a recipe tree before, which is precisely what an LP does better.

**It is a presentation layer over the existing solve, not a second engine**, and the choice is forced
rather than aesthetic: §10.1's ban on recursive expansion applies verbatim, because Recycled Plastic and
Recycled Rubber are a real 2-cycle and both unlocked. `build_scenario` builds the request exactly as
`plan_factory` would, then `min_raw` runs with **`extractor_nodes={}`** and every resource given an
unlimited raw cap — a bill is the chain, not the mine, and charging extraction would make it depend on
which nodes happen to be free. That is the same construction `compare_recipe_options` uses.

The cycle is not merely survived, it is **reported**. For 60 Plastic/min the bill builds 75.56 Plastic of
capacity and recirculates 15.56 back through Recycled Rubber; a Plastic line reading 75.56 for a 60 export
looks like an error until the response says `production loop: Plastic <-> Rubber`. Detection is mutual
reachability over the chosen processes only, which is cheap at a couple of dozen items.

**Water gets a documented lexicographic tie-break**, which §8.7 demands as the alternative to labelling
a degenerate vector. A bare `min_raw` sums every resource with weight one and therefore trades crude
against water. Measured on 60 Plastic/min:

| | Crude Oil | Water |
|---|---|---|
| bare `min_raw` | **56.25** m³/min | 0 |
| water priced last | **20.00** m³/min | 66.67 m³/min |

Water is effectively unlimited on this map, so the unweighted answer overstates the scarce input by
**2.8×**. `Scenario.raw_weights` exists for this: phase 1 minimises every other resource with water free,
phase 2 pins those and minimises water alone. Two solves. Whatever degeneracy survives is labelled in the
response rather than presented as the number, and `only_recipes` / `exclude_recipes` let a caller pin the
chain and get arithmetic instead of an optimum.

Two traps, both bugs first:

- **The phase-2 caps need 5e-5 of headroom.** `Solution.raw_used` is rounded to 4 dp, so a draw of
  13.33333 is reported as 13.3333 and a cap derived from it sits *below* what the chain needs. Phase 2
  went infeasible on Reinforced Iron Plate and silently threw the tie-break away. Same rounding, same
  fix as `compare_recipe_options`.
- **A column at 1e-6 machine-equivalents is not a building.** `ceil` turns one into a whole Smelter with
  a recipe name against it, so the bill claimed two routes to Iron Ingot where the flow ran entirely
  through one, and named two alternates carrying no flow. Processes below 1e-4 of the plan's largest are
  dropped; the threshold is relative so a bill for 0.1/min is not filtered away.

**Verified by hand.** Pinned to the base chain, 10 Reinforced Iron Plate/min:

```
raw 120 Iron Ore -- 14 machines, 78 MW
Iron Ingot  120  (4 Smelters)   Screws 120 (3)   Iron Plate 60 (3)   Iron Rod 30 (2)   RIP 10 (2 Assemblers)
```

6 Plate + 12 Screws per plate → 60 Plate + 120 Screws → 90 + 30 = 120 Iron Ingot → **120 Iron Ore**, i.e.
12 ore per plate; power is 4×4 + 8×4 + 2×15 = 78 MW. Solver and paper agree exactly. Left to choose,
this save's alternates route the same 10 plates through Stitched Iron Plate and the Pure ingot recipes for
**26.92 Iron Ore + 13.33 Copper Ore + 24.27 Water** — a 4.5× swing on iron, which is why every row names
its recipe.

### 10.1d One module per concern

`server.py` reached **3,467 lines and 36 tools** before being split. It is now 143 lines
that import and re-export; the tools live in `tools/`, one module per concern:

| module | tools | lines |
|---|---|---|
| `planning` | 11 | 1,240 |
| `factories` | 8 | 818 |
| `spatial` | 6 | 577 |
| `gamedata` | 5 | 240 |
| `world` | 5 | 198 |
| `progression` | 2 | 205 |
| `harddrives` | 2 | 116 |
| `resources` / `prompts` | 4 / 3 | 102 / 69 |

Three rules hold it together, each with a test:

- **`tools/__init__.py` imports every module for its side effects.** The decorators run on
  import, and that is what attaches a tool to the shared `mcp`. Those imports look unused
  and are not — a module dropped from that list would leave the server starting cleanly and
  simply not offering its tools. A test walks the directory and asserts nothing is missing.
- **Tool modules never import each other.** Shared resolvers live with their domains —
  `graph.resolve.resolve_factory`, `spatial.origin.resolve_origin` — because more than one
  group needs each, and a resolver is a domain decision rather than an app detail. `app`
  keeps the old private names bound so `server`'s re-exports still resolve. A sibling
  import is the first step back toward one file, so a test forbids it.
- **`server` re-exports every public name.** Tests and scripts reach for
  `server.plan_factory`, and a caller should not need to know which module a tool landed in.

The move was mechanical — every tool body is byte-identical — but two classes of breakage
were invisible to the linter and only showed at runtime: relative imports written for the
package root resolve one level too shallow inside `tools/` (`from .render` became
`satisfactory_mcp.tools.render`), and the **indented** lazy imports inside function bodies
escaped a line-anchored fix, so `factory_labels` failed only when called. `ruff` passed
clean in both states; the test suite caught them.

`progression` also corrects a mislabel: `phase_requirements` and `power_shards` had been
spliced under the *resources* banner during a parallel merge, and are tools.

### 10.1e Tools call services; they do not contain them

The domain already lived in `docs/`, `graph/`, `planning/` and `spatial/`. What a tool
module holds after the split is argument marshalling, orchestration and rendering — of
`plan_factory`'s 252 lines, roughly 20 marshal arguments, 10 orchestrate and 200 present.
Presentation belongs in the tool. **Orchestration did not**, and the evidence is a bug.

`plan_factory`, `plan_layout` and `diff_vs_save` each wrote out the same seven steps:
recall a saved plan → merge overrides → build a scenario → reject an empty source
selection → reject an unusable export → solve → explain a failure. Three copies drift, and
these did: `plan_layout` stopped accepting `extractor_clocks` and `water_extractors`, so it
silently re-solved at defaults and schematised a different plan than the one it was asked
to draw — **15,043 MW against 83,737**. Nothing in its output said arguments had been
dropped, because from its own point of view none had.

`planning/prepare.py` is that sequence, once. It returns a `PreparedPlan` carrying either a
solution or a `PlanFailure` of headline plus notes.

**It renders nothing**, and a test asserts so. Wording stays with the tool because the three
genuinely differ — `plan_factory` explains byproduct balance, while the other two defer to
it rather than repeating a diagnosis they did not run. Sequence is shared; voice is not.

Tests pin the shape rather than the behaviour alone: no planning tool may call
`build_scenario` or `solve` directly, `prepare` may not mention `render`, and all three
must report a bad request identically. There is also a test that `prepare` works with no
MCP layer at all, which is the point of the extraction — a script or a batch planner gets
the same guards.

**Not everything was extracted, deliberately.** `search_resource_nodes` (177 lines) and
`factory_query` (185) are long but their length is filtering and table-building against a
domain call that already exists; there is no second copy to drift from. Extracting those
would add indirection and remove nothing. The rule applied was: extract where logic is
*duplicated* or *unreachable without the MCP layer*, not wherever a function is long.

### 10.2 Context budget

The binding constraint. All 291 automatable recipes in optimal TSV = 25,313 chars (~7k tokens). **No tool
may be able to return its full table.**

1. Compact **TSV, not JSON** — the win is dropping repeated keys and braces.
2. **Schema-enforced caps**: `Annotated[int, Field(ge=1, le=25)]`, default 10. The model *cannot* ask for
   291 rows.
3. Names in rows, **IDs once in a footer**.
4. Summary/detail split; never ship cycle time + power + unlock in a list.
5. Precompute `/min`; pre-divide fluids.
6. **Truncation envelope counting data rows only** — a header/footer miscount produced "showing 7" for 5
   recipes, which actively misleads the model.
7. Scoped aggregates before rows ([§7.3](spatial-and-map.md#73-source-selectors)).
8. Round coordinates to metres.

Worked example, `alternates_for_item("Plastic")` — 498 chars, vs 4,893 for raw Docs subtrees:

```
# 3 automatable recipes make Plastic (1 alternate). rates=/min at 100% clock, one machine.
recipe	building	in/min	out/min	source
Alternate: Recycled Plastic	Refinery 30MW	30 Rubber + 30 Fuel	60 Plastic	ALT
Plastic	Refinery 30MW	30 Crude Oil	20 Plastic + 10 Heavy Oil Residue	tier5
Residual Plastic	Refinery 30MW	60 Polymer Resin + 20 Water	20 Plastic	tier5
# ids: Alternate: Recycled Plastic=Recipe_Alternate_Plastic_1_C Plastic=Recipe_Plastic_C …
```

15–20 tools ≈ 6–8 kB always-resident schema; keep descriptions to one line and push procedure into prompts.

### 10.3 Resources and prompts

**Resources (2–3, static).** Client-pulled, so zero context until requested:
`satisfactory://docs/summary`, `satisfactory://save/current`. Do **not** expose
`satisfactory://recipe/{id}` — it duplicates `recipe_detail`, and duplicate surfaces degrade tool
selection.

**Prompts (3):** `design_factory(target_item, rate)`, `debug_power(save)`,
`expand_here(resource, region)`. Zero cost until invoked, surface as slash commands, and keep multi-step
procedure out of tool descriptions.

### 10.4 Registration

```json
{"mcpServers": {"satisfactory": {
  "type": "stdio", "command": "uv",
  "args": ["run", "--directory", "E:/development/Hobby Projekte/SatisfactoryMcp", "satisfactory-mcp"],
  "env": {"SATISFACTORY_DOCS": "G:/SteamLibrary/steamapps/common/Satisfactory/CommunityResources/Docs/en-US.json"}
}}}
```

---

## 11. Caching

**Docs.json: no disk cache.** 50 ms to load and normalize. Build lazily into a module global; a disk cache
would add invalidation bugs to save 50 ms. Optionally persist the normalized snapshot (925 KiB raw /
68 KiB gzip) keyed `sha256(Docs.json)[:16]` purely so game updates are detectable.

**The save projection is where caching matters: 3.7 s → 1 ms.** Cache the *projection*, not the parse
tree. Key: `sha256(abspath | st_mtime_ns | st_size | schema_version)[:16]`. Two-tier: process LRU(3) in
front of a pickle at `%LOCALAPPDATA%\satisfactory-mcp\cache\` (via `platformdirs.user_cache_dir` —
`LOCALAPPDATA`, not `APPDATA`; regenerable data must not roam).

**Pruned on write, not on startup.** Autosaves rotate every ~5 minutes and each rotation is a new
cache key, so a long session grows the directory by ~500 kB per autosave — and startup pruning would
never fire during the session causing the growth. Globbing a dozen files costs nothing next to the 4 s
parse that just completed. Keeps the 12 newest.

---

## 12. Testing

- **Don't commit the 2.9 MB `.sav`.** Commit the ~9 kB sidecar projection as
  `fixtures/save_projection.json` — the only save-derived thing the server consumes.
- Commit small hand-checked Docs slices, not 10.6 MB.
- **Golden-file tests on `render.py`**, asserting `len(response) < budget` per tool. Context regressions
  are otherwise invisible.
- Build-time invariant assertions: `872 == 547 + 291 + 34`; purity table joins 100%; belt/pipe rates match
  their `mDescription` prose; all production buildables map `Desc_ ↔ Build_`.
- Optimizer: the free-lunch audit and duplicate-pid assertion as permanent tests, plus the crude→plastic
  byproduct table in [§8.2](planning.md#82-the-byproduct-rule--the-crux) as a regression fixture.
- Mark real-file tests `@pytest.mark.integration`, skipped when `SATISFACTORY_DOCS` is unset, so CI is
  green with no game install.

Deps: `mcp[cli]>=1.28`, `pydantic>=2.13`, `platformdirs`, `scipy>=1.11`, `numpy`; dev `pytest`,
`pytest-cov`, `ruff`. `requires-python = ">=3.11"`.

