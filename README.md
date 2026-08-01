# SatisfactoryMCP

An MCP server and a local web map for [Satisfactory](https://www.satisfactorygame.com/). The
server plans factories against your actual save files — it reads the game's own data dump for
recipes and rates, reads your saves for progress and unlocks, and runs a real LP/MILP optimizer
over the result. The map renders your world in the browser: terrain, factories, belts and pipes,
power wires, floors, crates. Everything runs locally and everything is derived from your own
game install; nothing is hardcoded and nothing is fetched from the network.

[DESIGN.md](DESIGN.md) is the spine — scope, decisions, data sources, architecture — and it
indexes the deeper documents in [`docs/`](docs/).

![The world map: factories, power wiring, resource nodes and region names over the game's own artwork](docs/media/map-overview.webp)

| | |
| --- | --- |
| ![A factory platform up close: machines, storage, belt and pipe runs](docs/media/factory-closeup.webp) *One platform up close — machines, storage, belts, pipes, wires* | ![Floor view: one storey of a multi-floor factory, with per-floor machine counts](docs/media/floor-view.webp) *Floor view — pick a storey, see what stands on it* |
| ![Terrain mode: hillshaded relief from the 1 m heightfield](docs/media/terrain-mode.webp) *Terrain mode — hillshade from a 1 m heightfield read out of the game* | ![A death crate's contents as an icon grid with game-extracted item icons](docs/media/crate-popup.webp) *A crate's contents, with icons extracted from the game's assets* |

## Highlights

- **Planning that survives byproducts.** Every item balances as an equality, so a plan that
  strands Heavy Oil Residue is reported infeasible instead of silently overstated. Recipe
  2-cycles (Recycled Plastic ↔ Recycled Rubber) are handled by the LP, not dodged by a tree walk.
- **Answers about *your* world.** Which recipes you have unlocked, where your factories are and
  how healthy they run, what a pending hard-drive choice is actually worth — all read from the
  save, with every response naming the file it read and how old it is.
- **A map of your game, in your browser.** Base layers built from the installed game
  (the game's own map artwork, plus terrain renders drawn from a 1 m heightfield), and on top of
  them your machines and floors, belt and pipe runs, power wiring, storage crates, resource
  nodes, region names, and a live you-are-here dot that follows your saves as you play.
- **A save parser of its own.** `src/pioneersav` is a standalone, first-party package that reads
  the save format across six `saveVersion`s; the server talks to it through one subprocess
  boundary, so a torn autosave or a format change cannot take the server down.

### The MCP tools

| Area | Tools |
| --- | --- |
| Game data | `search_items`, `search_recipes`, `recipe_detail`, `alternates_for_item`, `list_buildings` |
| Your world | `list_worlds`, `world_summary`, `unlocked_recipes`, `power_report`, `factory_sites`, `whereami`, `phase_requirements`, `power_shards`, `collected_from_world`, `mam_research`, `somersloops` |
| Your factories | `list_factories`, `name_factory`, `forget_factory`, `factory_health`, `factory_map`, `factory_query`, `propose_factories`, `select_machines`, `trace_upstream` |
| Map | `list_regions`, `describe_location`, `search_resource_nodes`, `rank_build_sites`, `show_on_map` |
| Planning | `plan_factory`, `plan_layout`, `commission_plan`, `diff_vs_save`, `bom`, `explain_byproducts`, `compare_recipe_options`, `rank_unlocks`, `list_plans`, `forget_plan` |
| Hard drives | `list_pending_hard_drive_choices`, `advise_hard_drive_pick` |

Plus MCP resources (`satisfactory://docs/summary`, `satisfactory://save/current`,
`satisfactory://map/regions`) and three prompts that surface as slash commands:
`design_factory`, `plan_power_plant`, `pick_hard_drive`. The full surface, argument by
argument, is in [docs/mcp-surface.md](docs/mcp-surface.md).

### Try asking

With the server registered, these are the kinds of questions it answers — phrased however you
like; the model picks the tools:

- *"Plan a factory for 20 Modular Frames per minute using only recipes I've actually unlocked —
  what do I build, and how much power will it draw?"*
- *"Which of my pending hard drives should I bank first, and why?"*
- *"How healthy is my steel factory right now? Anything idle or starved?"*
- *"Where am I standing, and what's the best spot near me for an aluminium setup?"*
- *"What's still missing for Phase 3, and which factory is the bottleneck?"*
- *"Trace my Reinforced Iron Plates upstream and tell me where the chain is thinnest."*
- *"Compare the alternate recipes for Computers against what I'm running today."*
- *"Show the coal powerplant on the map."* — answers with a link that opens the web map
  zoomed to it.

## Requirements

- **Python ≥ 3.11** and [uv](https://docs.astral.sh/uv/).
- **A local Satisfactory installation** (Steam or Epic). The server reads recipes and rates from
  the game's own `CommunityResources/Docs/en-US.json`, so game data stays correct when the game
  patches. The world tables in `data/` are committed and work out of the box — the game install
  is *also* the source for the optional data generators, but you never need to run those.
- **Node.js** — only to build the web map's frontend, and not needed at all if you only want the
  MCP server.
- Developed and tested on **Windows**. The save-directory and install auto-detection assume
  Windows paths; both can be pointed elsewhere via environment variables (below).

## Quick start

```bash
git clone https://github.com/lukszi/SatisfactoryMCP.git
cd SatisfactoryMCP
uv sync                # the MCP server
```

Extras are opt-in and combine: `uv sync --extra web` for the web map,
`--extra dev` for the test suite, `--extra gen` for the data generators.

### The web map

The page is TypeScript, built by Vite, and the built bundle is deliberately not committed — a
fresh clone answers `/` with HTTP 503 and the build instruction until you have built it once
(the JSON API under `/api` works regardless):

```bash
cd src/satisfactory_mcp/interfaces/web/frontend
npm ci
npm run build
```

Then, from the repository root:

```bash
uv sync --extra web
uv run satisfactory-mcp-web
```

The map is at <http://127.0.0.1:8712>. It binds to localhost on purpose: the API answers with
the contents of your save directory and has no authentication, so it is a local tool.
[The frontend README](src/satisfactory_mcp/interfaces/web/frontend/README.md) covers the dev
loop, the layer modules, and the type story.

### The MCP server

The console entry point is `satisfactory-mcp` (stdio). With Claude Code, register it at user
scope so it loads in any directory — you will usually be asking about the game, not about this
code:

```bash
claude mcp add --scope user satisfactory -- uv run --directory "/path/to/SatisfactoryMCP" satisfactory-mcp
```

For any other MCP client, the equivalent JSON configuration:

```json
{
  "mcpServers": {
    "satisfactory": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/SatisfactoryMCP", "satisfactory-mcp"]
    }
  }
}
```

It runs from source via `uv run`, so edits take effect on the next server start; there is
nothing to reinstall after a change.

## Where your saves come from

The save directory and the game install are auto-detected:

- **Saves**: `%LOCALAPPDATA%\FactoryGame\Saved\SaveGames` — the game's own location, one folder
  per account. Override with `SATISFACTORY_SAVES`.
- **Game data**: `CommunityResources/Docs/en-US.json` under a handful of common Steam and Epic
  install paths. Override with `SATISFACTORY_DOCS` (pointing at the `en-US.json` file itself).

Saves are grouped into **worlds** by the header's `saveIdentifier`; within a world the newest
save is used by default, and every response says which file it read and how old it is. The
server only ever reads your saves — it never writes them.

## Regenerating world data (optional)

You do not need any of this: every world table the server uses is committed under `data/`.
The generators exist so the tables can be rebuilt from your own installed game after a map
update, and so the map's imagery — which is the game's artwork and is therefore **never
committed** — can be produced locally. They need `uv sync --extra gen` and a Satisfactory
install. Approximate runtimes on one mid-range machine:

| Generator | Produces | Runtime |
| --- | --- | --- |
| `tools/gen_world_heightmap.py` | 1 m heightfield → `data/local/heightmap/` | ~54 s |
| `tools/gen_map_renders.py` | terrain/biome base-map renders → `data/local/` | ~17 min |
| `tools/gen_map_image.py --enhance` | the game's map artwork as tiles (upscaled on a GPU) → `data/local/` | ~13.7 min |
| `tools/gen_item_icons.py` | one PNG per item → `data/local/icons/` | ~14 s |
| `tools/gen_world_resource_nodes.py` | the node table → `data/world_resource_nodes.json` | ~4 s |

Run them as `uv run --extra gen python tools/<name>.py`. Imagery and the heightfield land in
`data/local/`, which is gitignored and stays that way; the committed tables in `data/` only
change when the game's map does. `tools/gen_world_collectibles.py`, `gen_region_names.py` and
`gen_resource_nodes.py` rebuild the remaining committed tables the same way.

## Development

```bash
uv sync --extra dev
uv run pytest -q                   # the default run: needs nothing but this checkout
uv run pytest -q -m integration    # the other half: needs the game and at least one save
```

The default run reads committed fixtures only, so a clone with no game install passes it in
seconds. Frontend checks are `npm run check` (strict `tsc --noEmit`) and `npm run build` in the
frontend directory. Architecture is enforced, not reviewed: `tests/test_architecture.py` reads
the AST of every module to prove imports run one way — `core` knows nothing, `domain` knows
`core`, `presenters` know `domain`, `interfaces` know everything.

```
src/satisfactory_mcp/
  core/       Docs.json loading, the save seam, num/plural
  domain/     world state, progression, power, factories, spatial, the LP planner
  presenters/ all response formatting
  interfaces/ mcp/ (the FastMCP surface) and web/ (FastAPI + Leaflet map)
  server.py   the console entry point; no logic
  config.py   paths and environment
src/pioneersav/  the save parser, a standalone package
tools/           data generators
```

## Data provenance

Every world table under `data/` is a first-party extraction: facts, coordinates and identifiers
read out of a locally installed copy of the game by the generators in `tools/`, with no artwork
shipped in this repository as data — the map's imagery is generated locally into a gitignored
directory. (The README's screenshots above show that imagery through the running tool; they are
documentation of this project, and the game content visible in them remains Coffee Stain's.) Two third-party sources were used earlier and both retirements are
kept on the record rather than tidied away:

- The region layer was once traced from satisfactory.wiki.gg's Biome Map (CC BY-SA 4.0). It is
  now read from the game's own `FGMapAreaTexture` and `UFGMapArea` assets — boundaries and names
  alike — so no share-alike obligation reaches this repository.
- The resource-node table was once vendored from the MIT-licensed
  [rockfactory/satisfactory-logistics](https://github.com/rockfactory/satisfactory-logistics)
  node set. It is now read from the node actors of the game's own `Persistent_Level.umap`; the
  parity record of that replacement lives in `_meta.retired_mit_table` inside
  `data/world_resource_nodes.json`, pinned by tests.

The save parser has the same history: a GPL-3.0 library was vendored here until `pioneersav`
replaced it and it was deleted; the measured agreement between the two is banked in
`tests/fixtures/vendor_parity.json` and replayed by the test suite. No copyleft licence reaches
this repository.

The game-derived data describes Coffee Stain Studios' content. Coffee Stain retains all rights
to Satisfactory and its assets; this project is not affiliated with or endorsed by them.

## Licence

**[PolyForm Noncommercial 1.0.0](LICENSE).** Free to use, modify and share for any
noncommercial purpose, provided the required notice travels with copies:

> Required Notice: Copyright Lukas Szimtenings (https://github.com/lukszi/SatisfactoryMCP)

Commercial use requires a separate licence from the owner — get in touch via GitHub
([@lukszi](https://github.com/lukszi)). The licence covers this repository's code, tooling and
extracted tables; it cannot and does not grant anyone rights over the game's content.

The web map compiles [Leaflet](https://leafletjs.com/) (BSD-2-Clause) into its bundle at build
time from the npm package. The bundle is not committed, so the repository redistributes no
compiled dependency; every build copies Leaflet's licence text to
`static/vendor/LEAFLET-LICENSE` beside the bundle, so a built page carries its own notices.
