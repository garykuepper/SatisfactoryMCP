# SatisfactoryMcp

An MCP server for planning Satisfactory factories. It reads the game's own data dump for recipes and
rates, reads your save for progress and unlocks, and runs a real LP/MILP optimizer over the result.

Nothing is hardcoded and nothing is fetched from the network — game data comes from
`CommunityResources/Docs/en-US.json`, which updates itself when the game patches.

See [DESIGN.md](DESIGN.md) for the full design and the evidence behind every number. It is the
spine — scope, decisions, data sources, architecture, the normalization contract — and it indexes
the rest, which lives in [`docs/`](docs/): the save projection and the parser under it, the spatial
model and the map, planning, the MCP surface, and what is parked.

## Setup

```bash
uv sync --extra dev
uv run python tools/gen_resource_nodes.py   # builds data/resource_nodes.json (607 nodes)
uv run python tools/gen_region_names.py     # builds data/region_names.json (21 regions)
uv run pytest -q
```

`pytest` runs the 616 tests that need nothing but this checkout — they read a committed save
projection and committed game-data slices, so a clone with no Satisfactory install and no saves
passes them. The other 803 are marked `integration` and are deselected by default; run them with
`uv run pytest -q -m integration` on a machine that has the game and at least one save, and
individual tests will still skip if the world they measure is not the one you are playing.

Register with Claude Code at **user scope**, so it loads in any directory rather than only inside
this repo — you will usually be asking about the game, not about this code:

```bash
claude mcp add --scope user satisfactory -- uv run --directory "E:/development/Hobby Projekte/SatisfactoryMcp" satisfactory-mcp
```

It runs from source via `uv run`, so edits take effect on the next server start; there is nothing to
reinstall after a change. Check it with `claude mcp list`.

The game install and save directory are auto-detected. Override with `SATISFACTORY_DOCS` and
`SATISFACTORY_SAVES` if they live somewhere unusual.

## Tools

**Game data** — `search_items`, `search_recipes`, `recipe_detail`, `alternates_for_item`, `list_buildings`
**Your world** — `list_worlds`, `world_summary`, `unlocked_recipes`, `power_report`, `factory_sites`, `whereami`, `phase_requirements`, `power_shards`
**Map** — `list_regions`, `describe_location`, `search_resource_nodes`, `rank_build_sites`
**Planning** — `plan_factory`, `plan_layout`, `diff_vs_save`, `bom`, `explain_byproducts`, `compare_recipe_options`
**MAM** — `list_pending_hard_drive_choices`, `advise_hard_drive_pick`

Saves are grouped into **worlds** by the header's `saveIdentifier`; within a world the newest save is
used by default, and every response says which file it read and how old it is.

### Choosing sources

`search_resource_nodes` and `plan_factory` both take `sources`, a list of selectors. Locations union,
filters intersect:

```
["north"]                              northern half of the map
["region:Spire Coast"]                 a named region (a bare name works too)
["near:120,-2020,1500"]                within 1500 m of (120, -2020) metres
["grid:X3Y4", "grid:X3Y5"]             specific 1.024 km grid cells
["node:BP_ResourceNode30_103"]         one exact node, repeatable
["bbox:-500,-2500,600,-1800"]          a rectangle, metres
["north", "resource:Crude Oil"]        narrow a location to one resource
["near:me,600"]                        within 600 m of where you are standing
```

Call `search_resource_nodes(..., group="node")` to get node ids you can feed straight back in.
A selector that fails to resolve returns **nothing** and says why — it never silently widens to the
whole map, because that would answer a different question.

### Banning routes

Every planning tool takes `exclude_recipes`. Patterns match a class id, an exact display name, or any
substring — and a substring takes **every** match, so `["Recycled"]` drops both Recycled Plastic and
Recycled Rubber. Banning half a two-recipe loop would leave the loop intact.

A pattern that matches nothing is reported rather than ignored, because a silently dropped ban returns a
plan using the very recipe you forbade.

## What makes it different

**Byproducts are modelled correctly.** Every item is balanced as an *equality*. A byproduct with no
consumer does not vanish — it fills a pipe and stalls the line — so a plan that produces one is
reported as infeasible rather than silently overstated. Concretely, for 300 m³/min of crude into
plastic:

| formulation | plastic/min | machines |
|---|---|---|
| treat residue as exportable (what naive calculators do) | 200 | **10.0** |
| consume the residue for real | 200 | **11.7** |
| route it to a solid AWESOME Sink | 200 | **12.5** |

The naive answer understates the build by 17–25% and leaves 100 m³/min of Heavy Oil Residue with
nowhere to go.

**Fluids cannot be sunk.** `Docs.json` claims Heavy Oil Residue has 30 sink points and
`mCanBeDiscarded = True`, but the AWESOME Sink has a conveyor-only input. This is one of exactly four
values not taken from game data; see `docs/constants.py`.

**An LP, not a recipe tree.** Recycled Plastic and Recycled Rubber form a genuine 2-cycle, so
depth-limited chain expansion has no correct answer. The LP handles it and finds plans no tree walk
can reach.

**Hard-drive advice by counterfactual.** `advise_hard_drive_pick` reads the *actual* pending offers
from your save, then solves your objective with and without each option and reports the delta —
including one measured on the candidate's own output, so a cable recipe isn't judged on plastic.

**Node data is cross-validated, not trusted.** The table is merged from two independent extractions —
one from the game's packaged `Persistent_Level.umap`, one community-traced — which agree on purity for
every shared node. That merge recovered a pure Limestone node the single-source table was missing, found
because the completeness check now counts save actors against table rows in *both* directions.

**Layouts are schematics, honestly.** `plan_layout` turns a plan into blocks, buses and floors with a
space budget — machine footprints come from `mClearanceData`, so a Refinery is 10×22×15 m and 6
foundations, derived not hardcoded. Blocks are split by throughput (46 Refineries needing 1,380 m³/min of
crude are 3 blocks, because a Mk2 pipe carries 600), and floors follow chain depth with a logistics deck
between. It gives no world coordinates and no belt routing: there is no terrain data here, so those would
be invented.

**Whole machines at a derived clock.** A 52.8 machine-equivalent result is reported as **53 Blenders at
99.6%** — exact, always a clean ratio, and provably the power-optimal way to run that throughput, since
`c^1.32` is convex so a uniform clock beats any mix. Ratio underclocking is therefore automatic and needs
no parameter. Passing `clocks=[0.5, 1.0]` asks a *different* question — spend buildings to save power —
which is allowed but priced at `machine_cost_mw` (default 5 MW/machine, just above the 2.58 MW/machine
that trade was measured to be worth) and announced in the warnings.

**Logistics are reported, not capped.** Every plan lists each flow with the belts or pipes it implies,
plus the water-extractor count. Capping throughput would be wrong — parallel lines are legal — but a plan
that silently needs 7 Mk2 water pipes is not a plan.

**Region names are advisory, and say so.** The boundaries are the game's own — `FGMapAreaTexture`, a
4096² map-area raster at 1.83 m — and so are the names, read out of each `UFGMapArea` asset's
`mDisplayName`. What ships is a 256 m grid to draw and a 64 m one to look names up in, so every lookup
carries a confidence that means something measured: `interior` (one region fills the cell), `boundary`
(an exact boundary runs through it), `unnamed` (the game names nothing here, so the label is its own **No
Man's Land**), `void` (no name at all — ocean and off-map, decided by a land mask of 2,688 static world
objects). All computation uses exact geometry — grid cells, cones, radii — never a name.

## Resources and prompts

Both are client-pulled, so they cost nothing until used.

Resources: `satisfactory://docs/summary` (game-data census + content hash),
`satisfactory://save/current` (which file would be read, and its headline state),
`satisfactory://map/regions` (region names usable as selectors).

Prompts, which surface as slash commands and carry the multi-step procedure so tool descriptions stay one
line: `design_factory`, `plan_power_plant`, `pick_hard_drive`.

## Layout

```
src/satisfactory_mcp/
  core/       knows nothing above it: Docs.json loading, the save seam, num/plural
  domain/     returns dataclasses and dicts, never text: world state, progression,
              power, factories, spatial, collectibles, the LP planner
  presenters/ ALL response formatting (context budget is the binding constraint)
  interfaces/ mcp/ (the FastMCP surface) and web/ (an optional FastAPI + Leaflet map)
  server.py   the console entry point; no logic
  config.py   paths and environment
src/pioneersav/
              our save parser, a standalone package: only the extractor subprocess
              imports it
tools/        one-off data generators
```

Imports run one way — `core` knows nothing, `domain` knows `core`, `presenters` know `domain`,
`interfaces` know everything — and `tests/test_architecture.py` reads the AST of every module to
prove it, rather than trusting review.

Save parsing lives behind one subprocess boundary. The parser refuses an unrecognised
`saveHeaderType` rather than guessing, so a game patch breaks exactly one module; a torn autosave or
parser crash cannot take down the server; and the ~130 kB JSON projection is the committed test
fixture, so the suite runs with no game install. `src/pioneersav` is ours, derived from the bytes,
and reads all 66 saves on the author's disk back to 2021 — six `saveVersion`s, 100% of every body.

## Licence

None — this is a private project, all rights reserved by default. **No copyleft licence reaches this
repository.** A GPL-3.0 save parser was vendored here until it was replaced by `pioneersav` and
deleted; the agreement between the two, measured leaf for leaf across every projection key of all 31
saves it could read, is banked as digests in `tests/fixtures/vendor_parity.json` and replayed by
`tests/test_savparse_parity.py`, because deleting the library destroyed the ability to re-run the diff.

That was not the only borrowing, and the other one is settled too. The region layer's geometry used to
be traced from [satisfactory.wiki.gg](https://satisfactory.wiki.gg)'s Biome Map image, **CC BY-SA 4.0**,
on the belief that the game shipped no biome geometry. It does:
`Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel` is an `FGMapAreaTexture` whose
4096² of palette indices resolve to `UFGMapArea` assets, each stating its own display name.
`data/region_names.json` is derived from that, `data/satisfactory_regions.json` is deleted, and no
share-alike obligation reaches this repository. Same posture as every other derived table here: facts,
coordinates and identifiers read out of the reader's own install, and no artwork shipped.

`data/resource_nodes.json` is merged from two sources, both recorded in the file's `_meta`: an
MIT-licensed set extracted from the game's own map assets
([rockfactory/satisfactory-logistics](https://github.com/rockfactory/satisfactory-logistics)) for
resource, purity and position, plus the satellite→core link it lacks — which is now read from the
game's own `Persistent_Level.umap`, where every `BP_FrackingSatellite` export carries an `mCore`
reference to its core, 118 of 118. That replaced a GPL table, and the regenerated file is
byte-identical to the one that table produced, which is what proves the replacement complete.
