# SatisfactoryMcp — Design Spec

An MCP server that helps plan Satisfactory factories: recipe/resource lookup, save-file analysis of
progress and unlocks, spatial resource queries, and LP/MILP factory optimization.

**Status:** implemented. 42 tools, 4 resources, 3 prompts, 918 tests passing. See README.md for usage.
**Target game version:** 1.2.2.1 (`saveVersion 60`, `buildVersion 495413`).
**Licence:** none. Private project, all rights reserved by default. See [§13](#13-licence).

Every number in this document was measured from the local game files or the live save unless explicitly
marked `[UNVERIFIED]` or `[WIKI]`. Claims were produced by one agent and independently re-checked by a
second; ~90 corrections from that pass are folded in.

---

## 1. Scope

Driving use case, in the user's words: *"I'm stuck building an oil powerplant in the north of the map.
The MCP should understand what oil sources are in the north, what alternate recipes I have, what
production facilities I need and have unlocked, and be able to do calculations and optimization."*

Plus, added during design: **advise which alternate recipe to take after a hard-drive hunt.**

Five capabilities:

1. **Game data** — items, recipes (including alternates), buildings, rates, power. Never hardcoded.
2. **Save state** — unlocks, built factory, power, node occupancy, progression, MAM/hard-drive state.
3. **Spatial** — which resources are where, what's already tapped, where to build.
4. **Optimization** — LP/MILP over the recipe graph with correct byproduct handling.
5. **Hard-drive advice** — rank the actual pending offers by marginal value.

Explicit non-goals: editing saves (strictly read-only), blueprint generation, in-game overlays,
belt/pipe *routing* geometry (only throughput accounting and altitude deltas).

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Python 3.11**, `uv`-managed | User preference; best MILP ecosystem. |
| D2 | Game data from the **local `Docs/en-US.json`**, normalized in-process | Ships with the game, updates on patch, authoritative. No web API, no hardcoding. **No committed snapshot fallback** — the server is only ever run on a machine with the game installed, so a fallback would be untested weight and a second source of truth to drift. |
| D3 | **`Headers.zip` + `FactoryGame.usmap`** as the authority for save property names | The game ships its own C++ headers — property semantics are quoted, not guessed. |
| D4 | Save parsing via **GreyHak `sav_parse`** behind a **subprocess sidecar** | Supports `saveVersion 60` exactly; parses in 3.7 s. Sidecar for version-fragility isolation, crash isolation, and a tiny test fixture. |
| D5 | **`scipy.optimize.milp`** (HiGHS) | Fastest at scale, ships with scipy, no external binary, BSD/MIT. |
| D6 | **Per-item equality balance** with explicit export/sink whitelists | The only formulation that models stalling byproducts correctly. |
| D7 | **Exact geometry for computation, advisory names for labelling**; sources chosen by a selector language | No biome geometry ships with the game, so names must be hand-authored and cannot be trusted numerically. |
| D8 | **FastMCP over stdio**, compact TSV responses | Context budget is the binding constraint. |
| D9 | Read the **actual pending hard-drive offers** from the save | They are persisted; no need to rank an abstract pool. |

---

## 3. Data sources

### 3.1 `Docs/en-US.json` — the game data dump

`G:\SteamLibrary\steamapps\common\Satisfactory\CommunityResources\Docs\en-US.json`
10,643,994 B, **UTF-16LE with BOM** → `json.load(io.open(path, encoding='utf-16'))`.
Shape: list of 114 groups, each `{"NativeClass": "...FGRecipe'", "Classes": [...]}`. 2,868 classes total.

Load + normalize measured at **~50 ms cold**, so no disk cache is needed (see [§10](#10-caching)).

**Contains no version field of any kind.** `buildVersion` lives in the *save* header, not here. Cache
identity must therefore be `sha256(file)`, never a version number.

### 3.2 `Headers.zip` — C++ ground truth

`CommunityResources/Headers.zip`, 1.95 MB, 1,143 files (`FGResearchManager.h`, `FGSchematic.h`,
`Unlocks/*.h`, …). This is how save property names and their semantics are established — e.g. that
`mSavedOngoingResearch` stores *time remaining*, not an absolute timestamp.

Headers only; **no `.cpp` ships**. Behaviour not stated in a header or doc comment is inference and must
be labelled as such.

Companion: `CommunityResources/FactoryGame.usmap` (2.68 MB) — property-name map, useful for resolving
save properties the headers don't cover.

### 3.3 The save file

`%LOCALAPPDATA%\FactoryGame\Saved\SaveGames\<steamid>\<session>.sav`

**Worlds, not files.** Saves are grouped into worlds by **`saveIdentifier`** from the header — verified
stable across all 28 parseable files in the user's directory (`X2faPVKjX06VaRzClNv5KQ`), with
`sessionName` as the display label. `list_worlds` returns worlds; `select_world` / an explicit path picks
one. Within a world the default is **newest by mtime**, and every response states which file it read and
how old it is.

**Unsupported saves must be bucketed, not fatal.** Of 63 files in this directory, **35 fail to parse** —
pre-1.0 saves with `saveHeaderType` 1, 8 or 9, rejected at the header check. Of the 28 that parse, 25 are
`saveVersion 52` (v1.1.0.4–1.1.2.2) and 3 are `60` (v1.2.2.1). So:

- `list_worlds` reports parseable and unsupported counts separately, with the reason.
- A world may legitimately contain mixed save versions; the projection records the version it read.
- Cross-version diffing is therefore supported and useful ("what changed since December?").

> **Hazard: saves are live and rotate.** Three autosaves overwrite in place every ~5 min while the game
> runs. Between two agent runs 30 min apart the same filename went 44,269 → 44,258 objects, Iron Plate
> 9,837 → 8,855. Consequences, all normative:
> - Cache key **must** include `st_mtime_ns` and `st_size`.
> - A parse may tear mid-write — treat parse failure as expected, not exceptional.
> - Never present save-derived counts as stable facts across turns; re-read.
> - Autosaves can catch the factory mid-restructure, so diagnostics must distinguish "broken" from
>   "under construction".

### 3.4 Resource node type and purity — the one gap, now closed

Node actors serialize **only** `mResourcesLeft` and their transform. Resource type and purity are
**not in the save**, and no biome/region geometry is in `Docs.json`. So a static table is required.

**They are, however, in the game's packaged assets.** `FactoryGame-Windows.utoc`/`.ucas` contain
`Map/GameLevel01/Persistent_Level.umap`, where each node is a placed actor carrying `mResourceClass`
and `mPurity`; `CommunityResources/FactoryGame.usmap` ships specifically so external tools can
deserialize them. An earlier draft of this spec called this data unverifiable from the game files.
That was wrong — it was unextracted, not unverifiable.

`data/resource_nodes.json` is therefore **merged from two independent sources**, because neither alone
is sufficient:

| source | licence | role |
|---|---|---|
| `data/world_resource_nodes.mit.json` — rockfactory/satisfactory-logistics | **MIT** | authoritative node set, resource, purity, position. Derived from an FModel dump of `Persistent_Level.umap`, i.e. from game assets. |
| vendored `sav_data/resourcePurity.py` | none stated; SCIM-derived, pinned to v1.2.0.0 | satellite → fracking-core link only, which the MIT set lacks and the well-rate maths needs. |

Cross-validation at generation time: **607 shared nodes, 0 purity mismatches, 0 resource mismatches,
max position delta 0.69 cm.** Independent agreement that close is strong corroboration of both.

> **The bug this found, and the mistake that hid it.** SCIM was missing `BP_ResourceNode11`, a pure
> Limestone node worth 480/min. The save proves it exists — 459 `BP_ResourceNode_C` actors against
> SCIM's 458. It went unnoticed because the original validation checked the join in **one direction
> only**: every table entry appeared in the save (607/607), which says nothing about save nodes missing
> from the table. `test_node_table_matches_the_save_in_both_directions` now counts save actors against
> table rows per kind. Konsl's world-generator notes a missing limestone node as a known 1.2-build
> discrepancy, consistent with SCIM's v1.2.0.0 pin — exactly the version-drift risk this section
> flagged.

Excluded on purpose: `BP_ResourceDeposit_C` (hand-mineable only, no extractor can be placed) and
`BP_FrackingCore_C` (produces nothing itself; referenced as `well_core` on satellites).

Geysers are labelled `Desc_Geyser_C`. Neither that nor the MIT set's `Desc_GeothermalEnergy_C` exists in
`Docs.json` — a geyser is not an item, it is a placement target for the Geothermal Generator — so the
difference between the two sources there is cosmetic.

**Regenerating from the paks directly** remains possible and is the fully first-party option: CUE4Parse
driven by the usmap, following the recipe in Konsl's MIT-licensed `scripts/extract.cs`. It needs
CUE4Parse ≥ 1.2.2.21 (nuget's 1.2.2 mis-parses UE 5.6, which this build is) and an Oodle *data*
decompressor, which the game does not ship — only OodleNetwork, a different library. Not worth the
second toolchain while two independent sources agree to 0.69 cm.

---

## 4. Architecture

```
SatisfactoryMcp/
  pyproject.toml   README.md   DESIGN.md
  data/
    satisfactory_regions.json     # SOURCE material for the region layer (hand-derived)
    region_names.json             # GENERATED layer 2: label raster + confidence + overrides
    resource_nodes.json           # GENERATED: 607 nodes, type/purity/position, with provenance
  src/satisfactory_mcp/
    server.py          # thin: imports the tool modules, re-exports, main()
    config.py          # env: SATISFACTORY_DOCS, SATISFACTORY_SAVES, cache dir
    core/              # knows nothing about anything above it
      gameassets/      # GENERATION-TIME only, used by tools/gen_*.py and nothing else:
                       # iostore.py (the game's own .utoc/.ucas container, Oodle
                       # decompressor injected) + packages.py (a cooked package's
                       # exports, property tags and transform chain)
                       # provenance.py (which build an artifact was cut from, and the
                       # staged rename that stops one saying two things at once)
      gamedata/        # loader.py (UTF-16 read, NativeClass grouping)
                       # uestruct.py (UE struct-string parser)
                       # normalize.py (-> items / recipes / buildings / schematics)
                       # model.py  search.py  footprint.py  constants.py
      saveio/          # projection.py: spawns the extractor, validates, caches
                       # extract.py: runs IN the child, builds the schema-13 projection
      text.py          # num + plural ONLY — the two helpers domain may reach
    domain/            # returns dataclasses and dicts, NEVER formatted text
      world/           # state.py: WorldState, a thin aggregate over the facets
                       # identity  inventory  census  carriers  water  sites
      progression/     # unlocks  phases  research  harddrives  shards
      power/           # report.py: PowerLedger
      factories/       # model  build  structure  identity  cohere  labels
                       # select  query  health  trace  resolve
      spatial/         # geo  nodes  regions  select  maplink  origin
                       # ranking  elevation
      collectibles/    # table  removed  service
      planning/        # optimize  scenario  prepare  slice  diff  layout
                       # supply  bom  fit  store  byproducts  compare  carrier
                       # + one *_service/report module per tool-sized use case
    presenters/
      text/            # ALL response formatting: primitives.py (TSV, envelopes,
                       # truncation) + one module per concept
    interfaces/
      mcp/
        app.py         # the mcp object + what more than one tool group needs
        tools/         # one module per concern; importing it registers everything
          gamedata.py  world.py  progression.py  factories.py
          spatial.py   planning.py  harddrives.py  resources.py  prompts.py
      web/             # optional [web] extra: app.py (create_app) + api.py
        static/        # index.html, app.js, vendored Leaflet — no game assets
  src/pioneersav/      # our parser: reads all 66 saves, six saveVersions. A standalone
                       # library — it imports nothing from satisfactory_mcp, and only
                       # core/saveio/extract.py imports it, inside the child process
  tests/
    fixtures/          # tiny Docs slice + ~9 kB save projection (committed)
```

**Layout rules.** Imports run one way: `core` knows nothing, `domain` knows `core`, `presenters` know
`domain`, `interfaces` know everything. Only the interface layer may import the MCP SDK. All formatting
lives in `presenters/text/` — context efficiency is cross-cutting and silently regresses if each tool
formats its own output, so a domain function returns a dataclass and a `render_*` function turns it into
the TSV a model reads. The two exceptions are `core/text.py`'s `num` and `plural`, which domain code may
use for note strings it embeds in its own results. `server.py` contains no logic; it stays at the package
root because the console script names `satisfactory_mcp.server:main`.

None of that is checkable at runtime — a lazy `import` three frames deep inside a method body loads fine
and violates the architecture silently. So `tests/test_architecture.py` parses every module with `ast` and
looks at *every* import node at *any* depth. It runs stdlib-only in under a second, and its whitelist of
tolerated violations is empty.

**The shims are gone.** `docs/`, `save/`, `graph/`, `spatial/`, `planning/`, `tools/`, `app.py` and
`render.py` survived the moves for exactly one commit, as alias packages that re-registered each old
submodule name against the module object at its new home. That bought the test suite a gradual move
without a 2,000-line import diff landing on top of the moves themselves. Every caller now spells the
layered home, the old names are deleted, and `test_the_old_paths_stay_deleted` inverts the ratchet: a
path whose only job is to forward an import is a second name for one module, and the forwarding one
always goes stale. `git log --follow` reaches through every move.

**The web adapter.** `interfaces/web/` is a *sibling* of `interfaces/mcp/`, not a layer above it: both are
thin adapters over the same domain, and neither imports the other — the web app duplicates the two-line
`lru_cache` game loader rather than reach into the MCP app for it. It answers JSON instead of TSV, because
its reader is a browser and not a model, and it converts every coordinate from the save's centimetres to
metres on the way out. `create_app(state_loader, game_loader)` takes both loaders as arguments so the whole
HTTP surface is testable against the committed fixture projection with no game install and no `.sav`.
FastAPI and uvicorn live in the optional `web` extra and may be imported only from this package, which is
the same AST-checked rule that confines the MCP SDK to `interfaces/mcp/`. The map ships no game textures
and no map tiles: Leaflet is vendored under `static/vendor/` with its BSD-2-Clause licence so the page
works offline, and everything drawn on it is data the save and the docs dump already contain.

### 4.1 The save seam

`core/saveio/projection.py` is the **only** module that knows a save parser exists. It spawns
`python -m satisfactory_mcp.core.saveio.extract`, which imports `pioneersav` and prints a JSON
projection to stdout. `-m` rather than a constructed file path, with `src/` put in front of the child's
`PYTHONPATH`: the child then resolves the extractor and the parser through the same import machinery
this process used, so it cannot run a copy that has drifted.

Reasons (licensing is *not* one of them — it never was, and the parser is ours now):

- **Version fragility.** The parser refuses an unrecognised `saveVersion` rather than guessing. Every
  game patch can break it until the format is re-derived. One seam = one module to touch.
- **Crash isolation.** A parser crash or a torn autosave can't take down the server.
- **Memory.** A 2.9 MB save inflates to far more while it is walked, and a child process hands every
  byte of it back to the OS on exit. A long-lived stdio server does not otherwise get that.
- **Test seam.** The projection is ~9 kB of JSON. Commit that instead of a 2.9 MB `.sav`; the entire test
  suite then runs with no game install and no parser.

The parser itself is `src/pioneersav`, a standalone package beside the application rather than inside
it: it implements a file format and knows nothing about factories, plans or MCP. `tests/test_architecture.py`
pins both halves — `pioneersav` imports nothing from `satisfactory_mcp`, and `core/saveio/extract.py` is
the only module in the application allowed to import `pioneersav`, because everything else reaches it
through the subprocess.

Subprocess overhead measured at **~60 ms**, and only on cache miss.

> **The sidecar must never inherit the server's stdin.** `subprocess.run(capture_output=True)`
> redirects stdout and stderr but leaves stdin inherited — and the MCP server's stdin *is* the client's
> JSON-RPC pipe. Handing that to a child means anything touching it blocks for ever, and could consume
> bytes the client sent to the server. The symptom was maximally unhelpful: every save-reading tool hung
> until its 180 s timeout, with no error, no log and no partial output, and **only** when launched as a
> real MCP server. Calling the same functions directly always worked, because then stdin is a terminal.
> Fixed with `stdin=subprocess.DEVNULL`; `tests/test_sidecar_spawn.py` pins it, along with the
> neighbouring trap that `sys.executable` must be an interpreter and never the console script, which
> would spawn a second MCP server that waits on stdin and emits nothing.

---

## 5. Normalization contract

### 5.1 UE struct-string parser

Most fields are UE struct strings. Grammar, exhaustive over the dump:

```
value := list | quoted | bare
list  := '(' entry (',' entry)* ')'
entry := (KEY '=')? value
```

Keyed lists → `dict`, positional → `list`, leaves → `str`. Recursive descent, quote-aware —
`split(',')` and regex approaches are wrong because `(`, `)`, `,` occur inside quotes.

Measured: 14,666 struct-shaped fields, **0 parse errors**, 0.15 s total, max depth 5, largest field
6,147 chars.

Six traps, all normative:

1. **Three fields arrive as real JSON, not strings**: `mUnlocks`, `mSchematicDependencies`, `mFuel`.
   Their *inner* fields are still struct strings. The parser must accept `str | list | dict`.
2. **UE omits struct members equal to their default.** `Schematic_Goat_C.mCost` has no `Amount`.
   Default missing `Amount` to 0.
3. **`FullName` is absent on 1,316 of 2,868 classes.** Key on `ClassName` only.
4. **`mProducedIn` has two token shapes** — asset paths (943) and raw native names (51). Take the text
   after the last `.` and strip the `'...'` wrapper.
5. **Single-entry lists**: `((A=1))` → `[{...}]` but bare `(A=1)` → `{...}`. Wrap dicts before iterating.
6. **Skip whitespace after delimiters** — 16 fields use `, `.

### 5.2 Items and fluids

`is_fluid = mForm in {RF_LIQUID, RF_GAS}`. Scan **all 13 native classes carrying `mForm`**, not just
`FGItemDescriptor`/`FGResourceDescriptor` — otherwise `Desc_LiquidBiofuel_C` (biomass) and the nuclear
rods are missed. `RF_INVALID` (555) = building/vehicle descriptors, not items.

15 fluids: 10 `RF_LIQUID` (Crude Oil, Water, Fuel, Turbofuel, Heavy Oil Residue, Nitric Acid, Sulfuric
Acid, Alumina Solution, Dissolved Silica, Liquid Biofuel), 5 `RF_GAS` (Nitrogen, Rocket Fuel, Ionized
Fuel, Dark Matter Residue, Excited Photonic Matter). All `Desc_Packaged*` and `Desc_Fuel_C` are solid.

**Fluid amounts are ×1000** — and not always whole thousands (`Recipe_Battery_C` has Sulfuric Acid 2500).
Divide as **float**, never integer. `mEnergyValue` follows the same rule: MJ **per litre** for fluids,
per item for solids.

### 5.3 Recipe partition

```
kind = 'building' if product[0] in FGBuildingDescriptor      # 547
     else 'part'  if mProducedIn ∩ MANUFACTURERS            # 291
     else 'manual'                                          # 34
```

`MANUFACTURERS` = `FGBuildableManufacturer` (8) **∪ `FGBuildableManufacturerVariablePower` (3)** = 11
classes. Omitting the second group silently loses 43 Particle-Accelerator/Converter/Quantum-Encoder
recipes.

**Assert `872 == 547 + 291 + 34` at build time** — it will break loudly on a game patch.

- `FGCustomizationRecipe` (106) is a **separate NativeClass**, not inside the 872. Filtering is free.
- Do **not** use `mGameplayTags == "Recipe.Part"` — against the correct 11-class set it misses 25 and
  wrongly includes 22.
- No recipe is craftable in more than one machine (verified 0), so `machine` is a scalar.
- Of the 291, 24 are Packager pack/unpack → 267 real transformations.

### 5.4 Alternates and the unlock gate

**`is_alternate` = reachable from a schematic with `mType == 'EST_Alternate'`** (109 schematics →
109 distinct recipes). Never use `"Alternate" in ClassName` — that set is 110 and wrong in both
directions: `Recipe_Alternate_Turbofuel_C` and `_EnrichedCoal_C` come from the Sulfur MAM tree,
`_PolyesterFabric_C` from a Mycelia research, `_Silica_Distilled_C` is `EST_Custom`; meanwhile
`Recipe_PureAluminumIngot_C` *is* a hard-drive alternate with no `Alternate` in its name.

Schematic → recipe: `mUnlocks[] → Class == 'BP_UnlockRecipe_C' → mRecipes` (399 schematics). Handle:

- `BP_UnlockBlueprints_C` also carries `mRecipes`.
- `BP_UnlockInventorySlot_C` — 2 schematics, **zero recipes** (both "Inflated Pocket Dimension").
- `BP_UnlockSchematic_C` (44) — **do not expand recursively.** Doing so pulls in 23 `CBG_*`
  customization schematics and inflates the derived set to 514, of which 113 aren't actually unlocked.
  One deliberate exception: `Quartz_Purified` → `Silica_Distilled`.
- Filter event schematics via `mRelevantEvents` (`(EV_Christmas)`) or 16 FICSMAS recipes leak in.

Recipe count per alternate schematic: `{0 recipes: 2, 1: 105, 3: 2}`. **Two** schematics carry 3
(`Schematic_Alternate_TurboHeavyFuel_C` and `_TurboBlendFuel_C`, each bundling
`Recipe_PackagedTurboFuel_C` + `Recipe_UnpackageTurboFuel_C`).

> **The unlock gate is `FGRecipeManager.mAvailableRecipes`, read directly from the save.** Do not
> reconstruct it from schematics. The intersection method over-reports by 8 on the real save (134 vs 126
> true unlocked part recipes), because recipes can arrive by more than one route — e.g. Charcoal and
> Biocoal are available via `Schematic_Alternate_EnrichedCoal_C` even though their own alternate
> schematics are unpurchased.
>
> **31 of the 405 entries in `mAvailableRecipes` have no `FGRecipe` in `Docs.json`** (24 `Recipe_Swatch_*`,
> 5 `Recipe_Material_*`, 2 skin recipes). Use `.get()` and filter customizations, or lookups `KeyError`.

### 5.5 Rate formulas

All from `Docs.json` fields.

```python
qty = raw / 1000.0 if item.is_fluid else raw
per_min = qty * 60 / recipe.mManufactoringDuration  # typo is in the data
cycle_s = mManufactoringDuration / (clock * building.mManufacturingSpeed)
```

`mManufacturingSpeed` is 1.0 for all 11 machines; keep it in the formula.
`mManualManufacturingMultiplier` must be read **per recipe** — only 219 of 291 are 1.0.

**Extraction:**

```python
base = mItemsPerCycle * 60 / mExtractCycleTime
if RF_LIQUID in mAllowedResourceForms or RF_GAS in mAllowedResourceForms:
    base /= 1000  # fluid-only; applying it to miners is 1000x wrong
actual = base * PURITY_MULT[purity] * clock
```

| building | impure | **normal** | pure | power |
|---|---|---|---|---|
| Miner Mk1 | 30 | **60** | 120 /min | 5 MW |
| Miner Mk2 | 60 | **120** | 240 /min | 15 MW |
| Miner Mk3 *(locked)* | 120 | **240** | 480 /min | 45 MW |
| Oil Extractor | 60 | **120** | 240 m³/min | 40 MW |
| Water Extractor | 60 | **120** | 240 m³/min | 20 MW |
| Well Extractor | 30 | **60** | 120 m³/min | **0 MW** |
| Well Pressurizer | — | — | — | **150 MW** |

There is only **one tier** of Oil Extractor. Wells: `Σ_satellites(60 × purity) × pressurizer_clock`; the
pressurizer produces nothing and costs a flat 150 MW.

**Power:**

```python
consumption = (
    base_MW * clock**mPowerConsumptionExponent * boost**mProductionBoostPowerConsumptionExponent
)
boost = mBaseProductionBoost + n_sloops * mProductionShardBoostMultiplier
```

> **Read `mPowerConsumptionExponent` per building. Never derive it.** It is `1.321929` for
> manufacturers, extractors, water pumps and fracking, but **`1.600000` for generators**, pipeline pumps,
> storage, rail — and for `Build_DroneStation_C`, which is clockable and draws 100 MW yet uses 1.6. Also:
> the shipped literal `1.321929` is **not** `log2(2.5)`; they differ by 9.05e-7. Don't "correct" it.

Underclocking uses the same formula (`0.5**1.321929 = 0.3997`). `mMinPotential = 0.01`.
Only **24** buildings have `mCanChangePotential = True` (62 merely carry the field).

**Somersloops:** `mProductionShardBoostMultiplier` is **per building** — 1.0 (Constructor/Smelter/
Packager, 1 slot), 0.5 (Refinery/Assembler/Foundry, 2 slots), 0.25 (Manufacturer/Blender/Hadron/Quantum
Encoder, 4 slots). Always products to max 2×. Power exponent 2.0, so **output ∝ boost, power ∝ boost²** —
the first sloop in a machine is always the cheapest, and 1 sloop × 36 refineries beats 2 × 18.
`mCanChangeProductionBoost = True` on exactly 10 buildings (the 11 manufacturers minus Packager);
extractors and generators cannot take sloops.

**Variable power** (Particle Accelerator, Converter, Quantum Encoder — `mPowerConsumption = 0`): draw
comes from the *recipe*. `min = mVariablePowerConsumptionConstant`,
`max = const + mVariablePowerConsumptionFactor` (**Factor is a range, not a multiplier** — proven because
building-level `mEstimatedMininumPowerConsumption`/`Maximum` exactly bracket the recipe values).

`mPowerConsumption` is normally a float but **`Desc_Locomotive_C` serializes it as a struct**
(`(Min=25,Max=110)`), so a blanket `float()` crashes.

**Generators:**

```python
fuel_per_min = mPowerProduction / energy_MJ * 60
water_m3_min = (
    mPowerProduction * mSupplementalToPowerRatio * 60 / 1000
)  # iff mRequiresSupplementalResource
```

| generator | MW | fuel | water |
|---|---|---|---|
| Fuel-Powered | **250** | 20 m³/min Fuel · 7.5 Turbofuel · 4.167 Rocket · 3 Ionized | **none** — `mRequiresSupplementalResource = False`, verified |
| Coal | 75 | 15 Coal · 7.143 Compacted · 25 Coke | **45 m³/min** |
| Nuclear | 2500 | 0.2 U-rod → 10 waste/min | **240 m³/min** |
| Biomass (automated) | 30 | Leaves 120 · Wood 18 · Solid Biofuel 4 | none |
| Geothermal | variable | — | none |

Geothermal: `mVariablePowerProductionFactor = 200` is the **normal**-geyser average;
`avg = 200 × purity_mult`, range 0.5–1.5× avg. Not clockable.

> **Docs gap:** `Build_GeneratorBiomass_C` and `Build_GeneratorIntegratedBiomass_C` (10 built in the save)
> **do not exist in `Docs.json`** — only `Build_GeneratorBiomass_Automated_C` does. Needs a documented
> fallback, not a `KeyError`.

Generators are modelled **linear in clock** for both output and fuel (energy-conserving; `Docs.json` has
no production exponent, and the 1.6 there is an inherited default on a zero-consumption building).

**Throughput:** belts `mSpeed / 2` → 60 / 120 / 270 / 480 / 780 / **1200** (Mk6 locked).
Pipes `mFlowLimit × 60` → **300 / 600** m³/min. Both self-corroborated by each building's own
`mDescription` prose — assert that at build time.

### 5.6 Hardcoded constants register

Four values are **not** in `Docs.json`. They live in one module, each with a comment saying so, and each
unit-tested. Nothing else may be hardcoded.

| constant | value | justification |
|---|---|---|
| `PURITY_MULT` | `{impure: 0.5, normal: 1.0, pure: 2.0}` | Every extractor's `mDescription` states the base rate as the *normal* rate, and the computed base matches exactly. |
| `POTENTIAL_SHARD_SLOTS` | `3` → max clock 2.5 | `Desc_CrystalShard_C.mExtraPotential = 0.5` is in Docs; the slot count is not (`mPotentialShardSlots = 0` everywhere). `[WIKI]` for the 3. Corroborated by the save: 447 buildings carry an `InventoryPotential`, 41 are non-empty, and their contents are exactly `{1: 6, 2: 16, 3: 19}` — **none holds 4**, and the highest clock in the world is 2.5. |
| `BELT_SPEED_TO_IPM` | `0.5` | Cross-checked against belt `mDescription` at build time. |
| `FLUIDS_CANNOT_BE_SUNK` | `True` | Physical rule that contradicts the data. **Confirmed by the user.** |

> **The fluid-sink conflict — resolved: fluids cannot be sunk.** `Docs.json` says Heavy Oil Residue has
> `mResourceSinkPoints = 30, mCanBeDiscarded = True`, as do Fuel (75), Water (5), Turbofuel (225), so a
> purely data-derived rule would permit sinking fluids. It is wrong: the AWESOME Sink has a **conveyor**
> input. Fluids must be packaged first (`Desc_PackagedOilResidue_C` is solid, 180 pts). Conversely
> `RF_SOLID` alone is too permissive — 26 solids have 0 sink points and 15 have `mCanBeDiscarded = False`.
>
> **Rule:** `sinkable = is_solid AND mResourceSinkPoints > 0 AND mCanBeDiscarded`.
> The solid half is data-derived; the fluid half is the hardcoded constant above, and it is the
> conservative direction — a plan that never relies on dumping a fluid cannot stall on one.
> The AWESOME Sink itself draws **30 MW** and must be charged in the power balance.

**The shard maths lives with the constant, not next to it.** `max_clock()` and `shards_for_clock()`
are in `constants.py` because they are the only two places `POTENTIAL_SHARD_SLOTS` meets data:
`max_clock = 1 + slots × mExtraPotential` and `shards = min(slots, ceil((clock − 1) / mExtraPotential))`.
Writing `2.5` as a literal anywhere would bury the one game-knowledge input inside a derived number.
Two traps, both found by measurement:

- **Round before ceiling.** Saved clocks are floats and 2.0 arrives as 1.9999999 often enough that a
  bare `ceil()` bills a 200% machine for a third shard.
- **`Desc_WAT1_C` (the Somersloop) shares the `FGPowerShardDescriptor` native class.** Selecting the
  class counts Somersloops as overclocking capacity. It has `mExtraPotential = 0` and
  `mExtraProductionBoost = 1`, so filtering on `extra_potential > 0` separates them without either
  class ever being named in code.

---

## 6. Save projection

The sidecar emits a flat JSON projection — never the parse tree. Verified property names:

| fact | object (`typePath` substring) | property |
|---|---|---|
| unlocked recipes ★ | `FGRecipeManager` | `mAvailableRecipes` (405) |
| purchased schematics | `BP_SchematicManager_C` | `mPurchasedSchematics` (226) |
| game phase ★ | `BP_GamePhaseManager_C` | `mCurrentGamePhase`, `mTargetGamePhase`, `mTargetGamePhasePaidOffCosts`. `mGamePhaseCosts` is **deprecated and frozen** — see [§6.4](#64-space-elevator-phases--two-records-and-only-one-is-alive) |
| hard-drive offers ★ | `BP_ResearchManager_C` | `mUnclaimedHardDriveData`, `mLastUsedHardDriveID` |
| research in progress | `BP_ResearchManager_C` | `mSavedOngoingResearch` — **seconds remaining**, absent when empty |
| misc unlocks | `BP_UnlockSubsystem_C` | `mIsMapUnlocked`, `mIsBuildingOverclockUnlocked`, `mNumTotalInventorySlots`, … |
| depot contents | `FGCentralStorageSubsystem` | `mStoredItems` |
| lifetime stats | `FGStatisticsSubsystem` | `mItemsPickedUp` — **nested**, see below |
| machine recipe | manufacturers | `mCurrentRecipe` |
| clock speed | clockable buildings | `mCurrentPotential`, `mPendingPotential` |
| installed shards ★ | every buildable | `InventoryPotential` component's `mInventoryStacks` — see [§6.5](#65-power-shards--committed-is-read-never-derived) |
| paused | buildings | `mIsProductionPaused` |
| extractor → node | extractors | `mExtractableResource` → node `instanceName` |
| pipe network fluid | `FGPipeNetwork` | `mFluidDescriptor` (19 objects) |

Normative gotchas:

- **`ComponentHeader` has no `typePath`.** Always `getattr(h, 'typePath', '') or ''`.
- **Bools are uint8 and `16` means True.** Observed `{16: 1489, 1: 84, 0: 32}`. Use `bool(v)`, never `v == 1`.
- **UE omits empty `TArray` SaveGame properties** — absent means empty, not missing. Default to `[]`.
- **`mItemsPickedUp` is a `MapProperty` keyed by player state**, whose value is a struct containing an
  inner map. Two levels of nesting; aggregate across players or co-op pickups vanish.
- **`ObjectReference` defines `__str__` but not `__repr__`**, so `repr(o.properties)` renders every
  reference as `<object at 0x...>`. Any substring search over `repr()` silently finds nothing.
- **`mPendingPotential` equals `mCurrentPotential` on all 46 clocked machines** in this save, so the
  applied-vs-slider distinction is *unconfirmed* and must not be asserted.
- `mCurrentProductionBoost` / `mPendingProductionBoost` are **guessed names** — they appear neither in the
  save nor in `Docs.json`. `[UNVERIFIED]`.
- **Extractor→node resolution is partial: 40/66.** Oil pumps 13/13, Miner Mk2 20/21, Miner Mk1 7/9, and
  **all 23 water pumps point at `FGWaterVolume*`**, which aren't purity-table keys. Three miners have no
  `mExtractableResource` at all. A coordinate-proximity fallback is required, and unresolved extractors
  must be reported as unknown rather than dropped.
- **Fluid buffer contents are not readable.** Tank fill level is; the *fluid type* is not (no serialized
  tank→network link). "Crude is backing up" is an inference, never a measurement.

**Building spatial grouping.** Cluster buildings by position (single-linkage) to form "sites", so
"your northern oil site" is addressable. Note `FGLightweightBuildableSubsystem` holds 17 additional
`Build_*` classes that appear in **no** actor header — 103 classes are built, not the 86 visible via
headers alone.

### 6.1 The factory graph — a first-class structure

Schema **6** adds a `graph` block to the projection, interned to keep it small (553 KB total for
6,100 actors / 11,554 material edges / 1,276 power edges):

```json
"graph": {
  "actors":   ["Build_ConstructorMk1_C_2147441119", ...],   // short instance names
  "roles":    ["Output1", "Input1", "ConveyorAny0", ...],
  "material": [[i, j, role_i, role_j], ...],
  "power":    [[i, j], ...]
}
```

Three edge kinds, kept separate because **no single one identifies a factory**:

| layer | count | what it is | why it is not enough |
|---|---|---|---|
| material | 11,554 | belts and pipes; orientable, since the connector role says `Output*` vs `Input*` | a mature base is one belt web → 35 components, 19 of them fragments |
| power | 1,276 | wires, with **poles** and **towers** distinguished (tower wires median 208 m vs 34 m for poles, and carry no machines) | dropping towers separates outposts but leaves the base as **one 476-machine island** |
| transport | 0 here | trains, drones, trucks | modelled from the start: a transport link is a deliberate connection **between** factories, so it belongs on a boundary |
| structure | 8,372 pieces | foundations, ramps, walls, catwalks — full transforms from `FGLightweightBuildableSubsystem` | 128 of 563 machines are ground-built and sit on no slab at all |

**Slabs are the sharpest signal we have** (§6.2a), but only the fourth one — not a replacement.

`build_graph` seeds nodes from `machines`/`extractors`/`generators` as well as from the edge list.
The interned actor list is derived from *edges*, so the **6 of 563** machines wired to nothing would
otherwise be absent from the graph entirely — and an isolated machine is exactly what a coverage
report exists to surface.

### 6.2a Foundation slabs — the fourth signal

A player builds a platform, then fills it. Belts and wires cross between platforms freely
— that is what they are for — but a foundation is only ever placed against another one
deliberately. Snapping is a build-time UI concept and is **not serialized**, so adjacency
is recovered geometrically: two 8 m foundations that touch have centres 800 cm apart.

Three link rules, each measured against the player's twelve named factories. *Purity* is
the share of a label's machines landing on its single dominant slab; a *collision* is one
slab claimed by two different factories.

| bridging rule | slabs | purity | collisions |
|---|---|---|---|
| nothing | 90 | 0.68 | none |
| walls only | 71 | 0.99 | none |
| ramps + stairs | 42 | 0.99 | none |
| **ramps + stairs + walls** | **41** | **0.99** | **none** |
| catwalks only | 85 | 0.78 | none |
| plus catwalks | 46 | 0.90 | tier 1&2 welded to the tor factory |

**Bridges must be chained, not tested pairwise.** Asking whether a *single* piece touches
two slabs finds nothing — the real shape is `slab → wall → wall → slab`. Tested as single
pieces, 0 of 1,937 walls touch two slabs; tested as chains, they join four pairs. Bridging
pieces therefore enter the union as nodes in their own right.

**Catwalks are excluded, and that is the whole trick.** Ramps connect the floors of one
structure; catwalks are the long walkways a player runs *between* distant platforms.
Chaining catwalks scores well and is still wrong, because the one thing it merges is two
genuinely separate factories. An over-segmented slab can be merged by naming; an
over-merged one cannot be split.

`LINK_Z = 1600` cm (four storeys) is a measured knee, not a guess: purity runs 0.84 at
450 cm, 0.85 at 900, 0.94 at 1200, 0.99 at 1600, with no collision at any of them. Past
1600 purity stops improving and only merge risk grows. This is what merges a factory
built on stacked decks — before it, the tor factory read as three platforms that merely
shared a footprint, and the speedwire factory as two.

**`slab:n` means the slab's own index**, the one `factory_map` prints. Slabs are numbered
by tile count while their machine groups sort by machine count; indexing the wrong list
silently returns a different platform.

**Walls are included on the same evidence.** Alone they take 90 slabs to 71; added to
ramps and stairs, 42 to 41, purity unchanged and still no collision. They buy little here
because ramps already cover most of the same joins, but they are structurally the right
kind of edge and cost nothing.

**Slabs are not the arbiter.** 128 of 563 machines stand on no foundation — the concrete
setup and the copper setup are built straight on the ground and have no slab at all.

### 6.2 Identifying factories — measured, not assumed

Checked against the player's own list of what they built on a 316-hour save:

- **material components** → 35 pieces. Splits one Christmas factory into a Tree Branch line and a
  Candy Cane line. Too fine.
- **power islands, towers removed** → 9, one holding 476 machines across 2,603 m. Separates outposts
  cleanly, does not subdivide the base at all. Too coarse.
- **spatial clustering alone** → chains through shared infrastructure; merged an oil plant 900 m out
  into the base. Wrong shape.

What works is the signal the first two lack: **what a machine makes**. Steel (50 machines, 95 m) and
Tier 1&2 (98 machines, 307 m) are one belt-connected mass and one power island, but they sit 600 m
apart and make different things. A grown-together base defeats topology; it does not defeat geometry
plus recipe.

So bases and lines are offered as **candidates**, never as an answer — `factory_map` prints both and
flags where they disagree. Product alone over-collects too: 17 machines make Concrete, 15 of them a
construction feed inside the steel site.

### 6.2b One coherence score over every signal

Each signal fails alone, so combine them: score every machine pair on shared slab,
proximity, belt component, shared product and supply link, then agglomerate. Validated
**leave-one-factory-out** — weights fitted on eleven factories, the twelfth scored:

> **precision 1.000, recall 0.945.** Ten of twelve recovered exactly. Precision was 1.000
> on *every* fold: it never merges two factories, it only splits one.

Clustering all 563 machines rather than only the labelled ones: **precision 1.000, recall
0.972**, 40 proposals, 0.3 s.

**The weights are barely load-bearing, and saying so matters more than the score.**
Ablated on the 382 labelled machines:

| variant | clusters | precision | recall | F1 |
|---|---|---|---|---|
| fitted log-odds | 15 | 1.000 | 0.973 | 0.986 |
| round numbers | 15 | 1.000 | 0.973 | 0.986 |
| every weight = 1 | 20 | 1.000 | 0.926 | 0.961 |
| **slab weight = 0** | 15 | 1.000 | 0.973 | 0.986 |
| random ±50%, worst of 12 | — | — | — | 0.967 |
| no power-island veto | 15 | 1.000 | 0.973 | 0.986 |
| **no distance cap** | 14 | **0.776** | 0.973 | 0.863 |
| **single linkage (same score)** | 8 | **0.353** | 0.995 | **0.521** |

Two results against intuition:

1. Rounding the weights changes nothing, perturbing them 50 % costs 0.02 F1, and deleting
   the *strongest* signal costs nothing — the others separate the same pairs. The weights
   are kept for the evidence report, not because the arithmetic needs them.
2. **The linkage rule and the distance cap are everything.** The identical score under
   single linkage collapses to F1 0.521: one adjacent pair chains a base into a blob.
   Complete linkage requires *every* cross pair to clear the bar. The power-island veto
   was measured redundant and is not applied.

`MAX_SPAN_M = 250` caps *linkage* distance, so a genuinely sprawling factory is proposed
in pieces. That is the deliberate trade for precision 1.000.

**Exclusive dependents are absorbed in a second pass**, because exclusivity is a property
of a cluster and no pairwise score can express it. The coal plant is the proof: its water
pumps sit 21–184 m away, well inside the span cap, and 94 % of everything their pipes
reach is that plant — but the plant runs on **two separate pipe networks**, so every pump
against a generator in the *other* network scores negative, and complete linkage takes the
minimum over cross pairs. One blind pair vetoed the merge.

A cluster is absorbed when ≥ `MIN_EXCLUSIVITY` (0.8) of the machines it reaches over
material edges lie in one other cluster, **and** it is at most `MAX_DEPENDENT_RATIO` (0.5)
of that cluster's size, **and** at most `MAX_DEPENDENT_RECIPES` (2) of its machines run a
recipe at all.

That third guard is what size cannot express. The player's space-elevator-parts area is 15
machines feeding a 110-machine host almost exclusively — inside both the exclusivity and
the size guard — but 3 of those 15 manufacture (Automated Wiring, Computer) and the other
12 are the biomass burners and miners powering them. Infrastructure runs *no* recipe:
every correctly absorbed dependent measured on the reference save has 0 or 1. Something
that manufactures stands on its own.

The size guard is not optional either:

| variant | clusters | precision | recall |
|---|---|---|---|
| no attachment | 40 | 1.000 | 0.972 |
| exclusivity ≥ 0.9, no size guard | 26 | **0.709** | 0.972 |
| exclusivity ≥ 0.8, no size guard | 21 | **0.711** | 0.979 |
| **exclusivity ≥ 0.8, ratio ≤ 0.5** | **30** | **1.000** | 0.972 |

Without it, two large factories that mostly feed each other are welded together. With it,
the coal plant becomes `32 + 6 + 6 + 2 + 1` — its generators, both water-pump farms, its
miners and a stray constructor. Recall against the twelve labels cannot move, because none
of the absorbed machines was ever labelled; the evidence is that precision holds at 1.000
and every merge is qualitatively right.

**Exclusivity alone cannot attribute a remote mine**, so there is a second way to
qualify. The four mines feeding the steel factory reach it in 26–43 hops and the tor
factory in 88–123 — but steel and tor are belt-connected to *each other* downstream, so
counting every reachable machine dilutes exclusivity to 0.55 and the mine is orphaned.
First arrival is unambiguous: a dependent is absorbed by the cluster it reaches first when
that cluster is `NEAREST_MARGIN` (2×) nearer in hops than the runner-up. Measured margins
were 42–69 hops. This takes 31 proposals to 26 with precision still 1.000, the steel
factory reclaiming its 8 miners and the second oil site its pump.

The margin has a readable meaning: it fires only when the two factories are farther from
*each other* than the mine is from the nearer one. A mine genuinely between two consumers
stays unattributed, which is the honest answer — an orphan in the coverage report beats a
wrong attribution.

Attachment runs *after* linkage, so an absorbed dependent may sit beyond the span cap — a
miner feeding a plant from 400 m is still that plant's.

**What stays unattributed is now a real finding, not a gap.** Five extractor clusters
reach *no* machine at all: their belt or pipe ends in a container. Nothing in the material
graph can attribute those, and guessing by proximity would be invention.

**Index selectors are snapshot-scoped.** `base:`, `line:`, `slab:` and `proposal:` are
positions in size-ordered lists rebuilt from the save on every call, so building anything
reshuffles them. A stale index once re-anchored the speedwire factory onto the aluminium
site. Every tool that prints an index now says so. A *label* is durable because it holds
machine ids — the index is only ever a way of pointing at them once.

**`proposal:n` closes the loop.** The workflow is propose-then-name, so a proposal has to
be selectable. Reconstructing one by hand from a centroid and a radius does not work: on
the real save, `near:-442,-1406@120` around a 15-machine proposal picked up **137**
machines, 82 of them belonging to the factory next door.

**A proposal is not a claim that something is a factory.** The space-elevator-parts area is
a temporary setup the player throws up to hand-build parts; it is neither part of tier 1&2
nor a factory in its own right. Keeping it a separate proposal is the correct outcome for
exactly that reason — the tool proposes, the player names, and what goes unnamed stays
visible in the coverage report rather than being silently filed somewhere.

Naming is also how a player records that something is *deliberately* not a factory. The
label carries `notes`, so "kept around to hand-build parts and mess with — idle is expected
here" is a durable statement that survives into every later report. `factory_health` will
need exactly that distinction: 304 of 580 machines are idle on this save, and idleness in a
scratch area is not a fault.

**Caveat no internal cross-validation removes:** one save, one player's building style.
LOO tests generalisation across *that player's* factories, not across players. The
insensitivity to weights is the real reassurance — a result that survives ±50 % on every
parameter is not resting on a fit.

### 6.2c Querying a factory

One tool, not eight: every question shares the same two steps — resolve a machine set,
then read something off it. `factory_query(factory, of=...)` takes a label name, a
selector, or a proposal index, and `of` accepts several aspects at once.

```
factory_query("steel factory", of="summary,balance")
  -> makes: Steel Ingot 405/min, Steel Beam 81/min, Steel Pipe 60/min, EIB 48/min
     needs: Coal 975/min, Iron Ingot 840/min, Concrete 288/min, Iron Ore 135/min
```

The **balance** table is what earns the tool. Production minus consumption across the set,
where the sign is the answer:

- **positive** — surplus: it leaves, or it backs up
- **negative** — has to be fed in from outside
- **zero with non-zero production** — made *and* consumed inside, the signature of a
  self-contained line

A per-machine listing says a Foundry runs Solid Steel Ingot. Only the balance says the
steel factory needs 975 Coal/min fed in.

Aspects: `summary`, `balance`, `inputs`, `outputs`, `machines`, `recipes`, `buildings`,
`power`, `nodes`, `links`, `issues`.

Two implementation notes that were both bugs first:

- **The boundary walk must pass through logistics.** A material edge runs
  machine → belt → machine, so a walk that stops at the first non-machine finds no links
  at all and every factory looks isolated. `links` counts *machines reached on the far
  side*, not edges, and is asymmetric on purpose: from a 15-machine copper setup you reach
  16 tor-factory machines on the shared belt web, but walking back the first copper machine
  blocks the rest.
- **`links` is only as good as the labels.** Before re-anchoring, every factory reported
  its neighbours as `(unlabelled)` — 92 boundary links in total — because the labels
  under-covered their own sites (the steel *label* was 50 machines against a 108-machine
  site). Re-anchoring the eight clean cases to their proposals took coverage from 397 to
  **511 of 563** machines and unlabelled links from 92 to **3**. Three labels were
  deliberately left alone: `biofuel setup` (its proposal lumps 3 biofuel machines with a
  separate 12-machine iron line), `aluminium setup` (split across two proposals whose union
  already equals the label), and the three that already matched exactly.
- **Rates are nameplate at each machine's saved clock**, applied per machine, never to a
  factory total. Paused machines contribute no flow but are still members and are listed
  under `issues`. Anything whose building class cannot be resolved is reported rather than
  silently contributing 0 MW — an understated draw with no explanation is worse than an
  error.

This is explicitly **not** throughput. A starved factory reports its full rate; measuring
what actually flows needs the productivity fields, and conflating the two would make a
starved factory look healthy.

### 6.2d Health — the only measured numbers in this MCP

Every manufacturing buildable keeps a **productivity monitor**. Schema 8 emits it, plus
per-machine input/output/fuel buffers. Field semantics, verified rather than assumed:

| field | finding |
|---|---|
| `mLastProductivityMeasurementDuration` | **300.00 s on all 580** carriers — a fixed window, so the ratio needs no normalisation |
| `mLastProductivityMeasurementProduceDuration` | **absent when zero**; UE omits defaults, so missing is a real zero (377 of 580 idle) |
| `mCurrentProductivityMeasurement*` | a *partial* window still filling — mixing it with the last complete one compares a 3-minute sample to a 5-minute one |
| `mTimeSinceStartStopProducing` | **FLT_MAX on 256 of 580** as a "never flipped" sentinel. Not a duration; averaging it poisons any statistic. Unused |

Uptime says a machine is stopped but never why, and the fixes are opposite. The buffers
settle it. **Every rule below was wrong before it was measured:**

- **Starvation is a missing *ingredient*, not an empty input.** Black Powder takes Coal
  and Sulfur; the assembler that motivated this held 100 Sulfur and no Coal. An
  empty-input test called it well-fed and filed eight machines as unexplained stalls.
  Comparing the buffer against the recipe names the missing item.
- **Blocked is checked before starved.** A blocked machine's input backs up too — the
  sample reads input 100/100 Iron Ingot, output 199/200 Iron Plate. Reading the input
  first calls it well-fed and misses that nothing is taking its plates.
- **A nearly full stack counts as backed up** (`FULL_FRACTION = 0.95`). Demanding exactly
  100% hides a bottleneck that has just ticked one item forward.
- **An absent intake inventory is not an empty one.** A miner draws from its node and has
  no `InputInventory` at all; treating that as "no input items" reported every idle miner
  as starved.
- **`dead node` is its own state.** An extractor whose `mExtractableResource` is *absent*
  is bound to nothing and can never produce — three miners on this save, left behind when
  a game update removed their resource node. Distinct from a water pump, whose node *is*
  set but points at an `FGWaterVolume` that is not a purity-table key; that one works fine.
- **Generators keep a `FuelInventory`, not an `InputInventory`.** Without capturing it a
  starved coal plant shows no evidence either way.

`STACK_SIZE` joins §5.6's register: Docs.json gives only the enum symbol (`SS_BIG`), so
the numbers are game knowledge. Verified against observed buffers — Wire 500 = `SS_HUGE`,
Iron Rod 200 = `SS_BIG`.

**Blocked is not automatically a fault.** 319 of 563 machines on the reference save are
blocked, because a base whose output nobody consumes fills its buffers and stops. That is
what a mature factory at rest looks like. The overview therefore ranks by a `todo` column
counting only `dead node`, `no recipe`, `starved` and `stalled`.

### 6.3 Labels — anchor sets matched by recall

A label stores the **set of machine instance ids** it was created from (verified stable: 365/365 kept
id and position across two saves). Matching is **recall**, `|anchors ∩ candidate| / |anchors|`, not
Jaccard — Jaccard punishes growth, and extending a factory is the most common thing that happens to
one. Match at ≥ 0.5, re-anchor at ≥ 0.8 with no competing label.

Labels attach to **arbitrary machine sets**, not to a base or a line, because a real factory is
sometimes several components (Christmas) and sometimes part of one (steel inside the base).

Persisted per world under `saveIdentifier` in `user_data_dir/labels/`, deliberately **not** under
`cache_dir` (which `cache_prune` wipes) and **not** in the repo.

Selection uses a small query language (`graph/select.py`): `product:`, `recipe:`, `building:`,
`near:x,y@m` or `near:<label>@m`, `base:n`, `line:n`, `slab:n`, `proposal:n`, `label:`, `all`. Terms are ANDed, commas inside
one term are ORed, a leading `-` excludes. Intersection rather than union because carving is
subtractive in practice — the player starts from something too big and narrows it.

Two orthogonal modifiers, because a factory is delimited from either end:

- `split` keeps only the largest spatial cluster — the escape hatch when one product is made in
  several places (17 concrete machines across 3 sites).
- `expand` grows the result to whole material components — the escape hatch when a factory is defined
  by **what feeds it**. The concrete setup is one limestone miner → storage → constructor → storage,
  a self-contained 10-actor component that no product or radius term describes.

Exclusions apply **after** expanding, or `-label:x` would be silently undone by the expansion
following it.


#### The label file is an interface

Labels are the one thing this server holds that a **player authored by hand**, so the
file is published rather than kept private. Location, and the same JSON served as an MCP
resource:

```
user_data_dir/satisfactory-mcp/labels/<saveIdentifier>.json
satisfactory://factories/labels          # same content, plus its own path
```

```json
{ "schema": 1, "world_id": "<saveIdentifier>", "session_name": "Han Solo",
  "labels": [ { "id": "steel-factory", "name": "steel factory",
                "anchors": ["Build_FoundryMk1_C_2147082409", "..."],
                "notes": "...", "centroid": [x_cm, y_cm],
                "signature": {"Build_FoundryMk1_C": 24},
                "created": "<save filename>", "last_matched": "<save filename>" } ] }
```

`anchors` is what makes a label portable: machine instance names were verified stable
across saves (365 of 365 kept id and position between two files), so a consumer joins
them against **its own** read of the same save and needs nothing else from this server.
`schema` is an integer so a reader can refuse a shape it does not know.

`centroid` is in **centimetres** — save units, not the metres every tool prints — because
it is stored data rather than presentation. `signature` is a building-class census kept
as a re-match hint after a full rebuild; it is advisory and never used to match
automatically.

`list_factories` prints the path, because reverse-engineering platformdirs to find it is
not a reasonable ask.

### 6.4 Space Elevator phases — two records, and only one is alive

`phase_requirements` exists because the obvious field is a trap. The save carries **two** accounts of
Space Elevator progress and they disagree.

| record | property | status |
|---|---|---|
| live | `mCurrentGamePhase`, `mTargetGamePhase` (→ `UFGGamePhase` assets), `mTargetGamePhasePaidOffCosts` | authoritative |
| legacy | `mGamePhaseCosts`, keyed by the `EGamePhase` enum | **deprecated and frozen** |

`FGGamePhaseManager.h` is unambiguous about the second one. The enum is *"The old enum that defined the
phases of the game. Replaced by UFGGamePhase. **DEPRECATED Only kept for save compatibility**"*, and the
array is *"**DEPRECATED Only kept for save compatibility**"* too.

> **Measured: the legacy array is not merely deprecated, it is dead.** Parsed across all **29 parseable
> saves of the reference world**, 180 h to 316 h of play, `mGamePhaseCosts` is byte-identical in every
> one — including across the session (between 244.0 h and 251.0 h) where `mCurrentGamePhase` advanced
> `Phase_2 → Phase_3` and `mTargetGamePhase` `Phase_3 → Phase_4`. Completing an entire Space Elevator
> phase moved nothing in it. It still bills the player **500 Modular Engine and 100 Adaptive Control
> Unit** for a phase they finished ~70 hours of play ago.

Deliveries go to the **target** phase, not the current one — `PayOffOnTargetGamePhase`,
`GetTargetGamePhaseCosts` — so "what do I owe" is the target's cost minus `mTargetGamePhasePaidOffCosts`.
On the reference save that array is **absent, i.e. empty**: nothing at all has been delivered toward
Phase 4.

**The EGP_* → GP_Project_Assembly_Phase_N mapping.** The legacy array is still the *only* source of
per-phase item lists, because the `UFGGamePhase` assets that hold `mCosts` do not ship in Docs.json, so
the keys have to be mapped. Establishing that mapping was the hard part:

- **Not in Docs.json.** `"GP_Project"` occurs **0 times** in the 10 MB dump, and the only `"EGP_"` string
  in it is one `EGP_Victory` schematic dependency. The field that *would* join them,
  `UFGGamePhase::mGamePhase` (`Category=Legacy`), lives on those unshipped assets.
- **Not joinable in the save either.** `BP_GamePhaseManager_C` carries exactly three properties. The
  manager's own legacy scalar `mGamePhase` is **absent**, i.e. UE-default `EGP_NA`, whose declaration
  comment reads *"Added N/A to have a state that indicates we have migrated the save"*.
- **But one anchor is measurable.** At 180–244 h the same world reads `mTargetGamePhase = Phase_3` with
  `mTargetGamePhasePaidOffCosts = {Desc_SpaceElevatorPart_2_C: 2500}` — exactly one item settled. The
  `EGP_EndGame` row of the legacy array describes those same three items with that same one at zero
  remaining, and nothing can be paid into a phase that was never the target. **`EGP_EndGame` →
  `GP_Project_Assembly_Phase_3`, from the save.**
- The other three follow **by enum order** (`EarlyGame 0 < MidGame 1 < LateGame 2 < EndGame 3 <
  FoodCourt 4`, declared in the shipped header) anchored on that pin, the four stored keys being
  contiguous in it. Corroborated but not relied on: the vendored wiki-derived `PROJECT_ASSEMBLY_COSTS`
  table lists Phase 1–4 item sets matching these four keys exactly and in order.

So `EGP_MidGame → 1`, `EGP_LateGame → 2`, **`EGP_EndGame → 3` (measured)**, `EGP_FoodCourt → 4`. The
three derived ones are labelled `[UNVERIFIED]` in the tool output, and an unrecognised key is reported
as `unmapped` rather than dropped — a silently missing phase reads as a phase with nothing outstanding.

**Frozen does not mean wrong for every row.** A phase that has never been delivered into cannot have
drifted, so its snapshot still equals its full cost. That is a checkable condition, not an assumption,
and it is what makes the Phase 4 numbers (Assembly Director System 4000, Magnetic Field Generator 4000,
Thermal Propulsion Rocket 1000, Nuclear Pasta 1000) usable while the Phase 3 numbers are not. Every row
is emitted with a `trust` column — `complete` / `usable` / `stale` / `unmapped` — rather than filtered.

### 6.5 Power Shards — committed is read, never derived

Every buildable carries an **`InventoryPotential`** component holding the shards physically slotted into
it. 447 exist on the reference save and 41 are non-empty. That component is the only faithful record of
a spent shard, and schema 9 emits it per machine as `potential_slots`.

> **A shard raises the *maximum* clock; it does not set the clock.** The player slots shards and then
> drags the slider anywhere below the new ceiling, so installed ≥ required. Measured: 39 of the 41
> overclocked buildings hold exactly `ceil((clock − 1) / 0.5)` shards, and **two hold 3 while running at
> clock 2.0** — a filled slot with the slider pulled back. Deriving committed shards from clocks gives
> **95**; reading the slots gives **97**. The 2 are really spent and really unavailable.

> **The 97 shards in `inventories["machine"]` are not shards on hand.** Every one of them is in an
> `InventoryPotential`, i.e. already installed. Reading that bucket as the free pool overstates it by
> more than **4×**: the player can actually spend **22**, all in the Dimensional Depot. `stock()` already
> excludes machine inventories for exactly this class of reason (§ its docstring), so free/committed/owned
> are 22 / 97 / 119.

Cross-check that the two halves agree: 41 buildings have `clock > 1.0`, 41 hold shards, and they are the
same 41 — no underclocked building holds one, and 5 of the 46 clocked buildings are *under*clocked (down
to 0.333), so treating any `clock != 1.0` as an overclock would invent shards for them.

**Why a tool and not an aspect.** `factory_query` is factory-scoped by construction and needs a selector;
"how many shards do I have, and can I afford to overclock twelve machines" is world-scoped and takes a
*hypothetical* (`plan_machines`, `plan_clock`) that no existing tool's signature accommodates, while
`world_summary` and `power_report` are fixed parameterless dashboards. A shard budget is a planning
question, so it gets its own entry point.

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

Content extents (2,371 static objects): X −298,838…406,564; Y −314,104…304,196; Z −16,827…46,942.

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

## 8. The optimizer

### 8.1 Formulation

Variables: `x_p ≥ 0` (machine-equivalents at 100% clock), `n_p ∈ ℤ₊` with `n_p ≥ x_p / clock`, plus
`raw_i`, `out_i`, `snk_i`.

**Power is a pseudo-item `__MW__`** — negative coefficient on machines, positive on generators. This makes
the power balance just another mass-balance row and keeps every objective linear.

**Balance, for every item — equality:**

```
Σ_p x_p·(out_ip − in_ip) + raw_i − out_i − snk_i == 0
```

Objectives: `max_mw`, `max_item`, `min_raw`, `min_machines`, `min_power`.
Extra rows: somersloop budget, belt/pipe throughput caps, machine cap.

**Two-phase lexicographic solve is mandatory.** With only `n_p ≥ x_p`, any larger `n_p` is optimal — an
unguarded run returned machine counts of 1e12. Phase 1 optimises the goal; phase 2 pins that value and
minimises machine count.

### 8.2 The byproduct rule — the crux

1. Every item's balance is `== 0`.
2. `exports` and `sinks` are **explicit whitelists**, never "all items".
3. `sinks` restricted to `is_solid AND mResourceSinkPoints > 0 AND mCanBeDiscarded`; each AWESOME Sink
   draws 30 MW.
4. Export **targets** are first-class: `max MW subject to plastic ≥ X, rubber ≥ Y` — still linear.

Why it matters, measured on crude→plastic from 300 m³/min:

| formulation | plastic | refineries | power |
|---|---|---|---|
| everything exportable (`net ≥ 0` in disguise) | 200/min | **10** | 300 MW |
| **correct:** HOR `== 0`, fuel/coke real exports | 200/min | **11.67** | 350 MW |
| **correct, plastic-only site** | **infeasible** | — | — |

Naive calculators return row 1 — 10 refineries and *no machines consuming the 100 m³/min of Heavy Oil
Residue*. In game the pipe fills and the line stalls. The correct answer needs **17–25% more buildings**,
and it can say "impossible", which a naive model never does.

### 8.2a Naming what an infeasible plan cannot get

`plan_factory` already names buildings the world has not built (`must build first: Blender`). It said
nothing about the resource end, and that asymmetry was expensive: a rocket-fuel plan returned a bare
INFEASIBLE, `explain_byproducts` correctly reported no byproduct was stuck, and the real cause —
**Nitrogen Gas exists only as resource-well satellites, and this world has no Pressurizer** — had to be
recovered by hand by cross-referencing a node scan against a recipe. `planning/supply.py` closes it.

Three things had to be fixed for an INFEASIBLE response to say anything at all:

1. **Every early `Solution("infeasible", …)` filed its reason under the wrong field.** The tenth
   positional field is `machine_penalty_mw`, not `warnings`, so `"phase 1 infeasible: …"` went into a
   float and the caller got an INFEASIBLE with no reason attached. This is most of what "bare INFEASIBLE"
   actually was. Now passed by keyword.
2. **An export or target nothing in scope can make is named without any probe.** An export with no
   producing column cannot be exported at any rate, and the distinction is the one a player acts on:
   *no unlocked recipe makes it* versus *its recipe needs a machine you have not unlocked*.
3. **Missing raw supply is decided by the LP, never a graph walk** — the same rule §8.2 and
   `byproducts.py` follow. A backward walk from the target over-reports (every raw input of every route,
   including routes the plan would never take), and a forward "what can I make" closure under-reports,
   because Recycled Plastic and Recycled Rubber each need the other's product yet the *pair* net-creates
   both from Fuel. So one probe re-solves with a free supply of every resource the scope cannot extract,
   and `raw_used` names the ones the plan actually wanted. **One extra solve, on the infeasible path only.**

Both outcomes are reported, and the negative one is worth as much:

| probe | reported |
|---|---|
| solves | every resource it drew on is *a* cause — the only change was making it available |
| still infeasible | raw supply is **not** the problem; something else is binding |

Measured on the reference save: max Steel Ingot ≥ 100/min scoped to `resource:Iron Ore` is infeasible,
and the probe names **Coal — no node in scope (62 elsewhere on the map)**. Forcing 10⁷ Steel Ingot on
Spire Coast is also infeasible, and there the probe correctly declines to blame supply.

**What it cannot prove.** It does not apportion blame: two missing resources that are jointly needed are
both listed, with no claim about which is *the* blocker, and a resource the probe used because it was
merely cheap is still listed. The probe's own draw rate is deliberately **not** printed — it maximises
against an unlimited supply, so quoting it would read as a requirement it never established.

The *why* beside each resource is a separate, purely factual read of the node table, in this order: no
node in scope (with the map-wide count) → nodes in scope but none reachable → reachable but all tapped
(`only_free_nodes`) → reachable and free but modelled by no extractor (well satellites and geysers,
which `build_scenario` never turns into extractor columns). If none of those hold it says the cause is
not established rather than inventing one.

`nodes.blocking_buildings` supplies the actionable half — *which* building to go and unlock. It is
strictly sharper than the `reachable` flag in one direction that matters: `reachable` asks only whether
an extractor of the right **kind** is unlocked and never whether it can tap that **resource**, so an
unlocked Miner Mk2 makes a crude oil node read as reachable while nothing on the map can pump it. It
also names the Pressurizer, which appears in no extractor table because it extracts nothing, and
without which every satellite of a well yields exactly zero.

### 8.2b Every export needs a balance row

Export **columns** come from what you asked for; balance **rows** came only from items
some process touches. An export that is neither produced nor raw therefore got a column
with nothing tying it to production, and an unconstrained column is wrong in two ways:

| call | before | after |
|---|---|---|
| `max_item` on an unmakeable target | objective pushes the free column up forever → HiGHS **UNBOUNDED** → surfaces as a bare INFEASIBLE | bounded, objective 0, reason stated |
| `export_minimums` floor on an unmakeable export | floor is a lower bound on that free column, so it is met **out of thin air** — `min_power` reported `exports: Nitrogen Gas=100` on a world with no recipe and no reachable node | honestly INFEASIBLE, with the cause named |

The second is the serious one: a confidently wrong plan does more damage than a bare
INFEASIBLE, which is what the whole diagnostic work above was fixing.

`MW` is excluded from the new rows on purpose — it balances on the power row, and a
second row there would force generation to zero.

**The fix needed a companion.** Pinning the export to 0 turns a loud failure into a quiet
one, so `supply.unmakeable()` — previously reachable only on the infeasible path — now
also runs when the plan *succeeds*, appending "…it is pinned to 0 in this plan". That
branch needs no LP probe, so it is free.

Test note: of the three regression tests, two fail without the fix. The third (no floor,
`min_power`) reads 0 either way because nothing rewards raising that column; its docstring
says so rather than implying it catches the bug.

### 8.2c Three gaps a planning session found

**Generator burn had no name to ban.** `exclude_recipes` searches `game.recipes`, but
generator burn and extraction are *synthesised* in `optimize.py` from building data —
they are not recipes and have no entry in Docs.json. So `"Coal-Powered Generator on
Coal"`, the exact string the build table prints, matched nothing. The recourse was
deleting 20 generators by hand, which only worked because coal happened to be a leaf in
that plan.

Patterns are now matched against synthesised processes too — by label, building name, or
consumed item — so `"Coal-Powered Generator on Coal"`, `"Coal-Powered Generator"` and
`"Coal"` all work. **Every pattern is offered to both matchers.** Recipe-first precedence
looked tidier and was wrong: `"Coal"` matches Biocoal/Charcoal/Compacted Coal, so under it
the pattern never reached the generators and "do not burn coal here" silently did the
opposite. A pattern matching neither still refuses, which is the behaviour the reporter
explicitly asked to keep.

**Water was modelled as placeless and unlimited.** It has no node entry, no purity and no
geometry anywhere this project can read — pumps point at `FGWaterVolume` objects. The
count was bounded by a private `_WATER_EXTRACTOR_CAP = 200`, chosen only to keep the
column from being unbounded.

That is not cosmetic. On a measured plan water was **12,400 m³/min across 105 extractors —
the largest fluid in the plant, larger than its Fuel**. It is also the only fluid that must
be sourced at sea level and cannot be gravity-fed, so it drives deck ordering.

> **Corrected 2026-07-28 (player).** This section originally added "on a 138×136 m ocean
> platform whose perimeter fits roughly 27", treating **shoreline as the constraint**. It is
> not. Pumps go on foundation platforms built out over open water, so frontage plays no
> part and only area matters: 105 pumps at 20×18 m occupy 37,800 m², a **194 m square**,
> smaller than that same plan's own 512 m site, and cost 945 foundations / 4,725 Concrete
> against the 32,645 Concrete its deck already needs. The 105-extractor plan was never
> implausible. What binds is vertical, not horizontal — see the sea-level rule above.

Three changes: the constant moves into the §5.6 register as
`WATER_EXTRACTOR_CAP_ASSUMED`, labelled the only entry with no data behind it; a
`water_extractors` parameter lets a player state what their site holds (capping the same
plan to 27 costs 89,712 → 74,016 MW, a 17.5 % difference previously invisible); and any
plan using more than `WATER_EXTRACTOR_WARN_AT` says outright that siting is unmodelled.
Water pump rows also stop reporting `?` for resource and purity — a Water Extractor sits
on a volume, so it now reads `Water / n/a (water volume)` rather than looking broken.

**Degenerate sub-1 % rows were clock modes, not an LP artefact.** Offering a node set at
several clocks creates one column per mode, and the modes share a node cap, so the solver
may split across them arbitrarily — 0.615 machine-equivalents at 100 % plus 0.0201 at
150 %. That printed as two rows with an *identical* label, the second a whole miner at
2 % clock, reading as a real build instruction.

Extractor modes of the same (building, resource, purity) are now folded before read-out.

**The first fold broke the cap**, and the failure is worth recording. Two quantities have
to be carried separately: the node cap constrains **machine count**, `sum(v)`, while
extraction is **node-units**, `sum(v × clock)`. Pooling units and re-expressing them at one
mode's clock preserved the rate and silently inflated the count — `water_extractors=54`
came back as **64 machines at 149.7 %**, because 54 machines' worth of units read at a lower
clock needs more machines. It looked correct at 27 only because that solution happened to
use a single mode. The fold now keeps `sum(v)` as the count and lets the clock absorb the
rate: `built = ceil(sum(v))`, `clock = units / built`.

**Binding also had to move to the group.** It was tested per process against that
process's `max_count`, but grouped modes share one cap, so a solve spreading extractors
over two clocks left every column below its own limit and reported nothing binding — while
the cap was fully consumed.

**A negligible row is not the same problem.** A degenerate basis can leave a *recipe*
column at 0.0001 machine-equivalents making 0.0017/min — one item every ten hours. There is
one clock mode, so there is nothing to fold it into. The row is omitted from the build
table (a whole machine at 0.0087 % clock reads as an instruction) but the machine is
**still counted**: dropping both silently turned a measured "9 buildings" into 8 in
`compare_recipe_options`. Omitting a row is presentation; changing a total is not.

This is the one place the reporter's original instinct — filter below an epsilon — was
right, and it was right for the *opposite* reason to the extractor case. The two look
identical in the table and need opposite treatment: fold one, omit-but-count the other.

### 8.2d Building footprints, exposed

`docs/footprint.py` has always derived an axis-aligned box per building from
`mClearanceData`, and `plan_layout` has always used it for foundation counts — but
nothing surfaced it. `list_buildings` now carries **size** (W×D×H) and **found** (8 m
foundations one machine covers):

| building | size | found |
|---|---|---|
| Smelter | 5×10×4.5 m | 2 |
| Manufacturer | 18×20×11 m | 9 |
| Water Extractor | 20×18×12 m | 9 |
| Nuclear Power Plant | 36×42×10 m | 30 |
| Quantum Encoder | 22×50×14 m | 21 |

Foundations round up **per axis** — a 5×10 m Smelter takes two, not one. The `found`
column is deliberately **per machine and ignoring shared edges**, so `N × found` is an
upper bound: a row of N machines needs fewer, because two 20 m machines side by side span
40 m and want 5 tiles rather than 6.

That caveat used to be merely *stated*, and both `plan_layout` and the water-siting note
had independently grown their own copy of the wrong `N × found` arithmetic. It is now
computed once, by `Footprint.pack(n, columns=)` (§ 8.5g).

The rotation trap is documented in the module and now has a test: the Fuel Generator's
clearance is several thin boxes at 45° increments approximating a round machine, so taking
the largest box naively gives 22×4 m instead of ~20×20 — roughly **1,000 foundations
understated across a 176-generator plan**.

This feeds straight back into §8.2c's water problem. "Siting is not modelled" is abstract;
*"77 of them pack into 11×7 = 220×126 m (448 foundations, 2,240 Concrete), or a single pier
20×1,386 m (522 foundations)"* is something you can hold against a build.

The frontage half of this was **wrong and is gone**. It quoted "about 1,920 m of shoreline
in a single line", which assumed pumps line a shore; they do not, they sit on platforms out
over open water. Quoting both and calling it balanced did not help — one of the two numbers
was answering a question nobody had, and it was the one that made large water plans look
impossible.

**A length is still given, just not that one.** Lukas lays platform modules, so "how long
is this" is a real question — it is the *pier extent*, 1,386 m for 77 pumps, not a stretch
of coast. Both shapes are printed because they answer different halves: the block is the
cheapest way to buy the area, the pier is what you measure modules against.

**And it is packed, not multiplied.** `Footprint.foundations` says outright that it ignores
shared edges, so `n × foundations` is an upper bound and not a build — two pumps side by
side span 40 m and need 5 tiles, not 6. Across 77 pumps that is **448 foundations against
693**, a third of the concrete. `Footprint.pack(n, columns=)` does the arithmetic and the
naive figure now appears only as the thing being corrected.

### 8.2e Plan slices, and the shard bill

Every plan-level question that is not "solve it" is the same operation: take some of the
processes and total their power, flows, shards and sloop slots. `planning/slice.py` is that
operation; the shard bill is one call to it and commissioning will be it in a loop.

**Two power figures, and the difference is not rounding.** `mw_linear` is what the LP
optimised — power proportional to machine-equivalents. `mw` is exact, after whole machines
are placed at a derived clock. `clock**exponent` is convex, so N machines below 100% draw
*less* than the linear estimate. The identity that holds is

```
solution.net_mw == sum(mw_linear) - sink_mw        # exactly
```

and the exact figure is better — 43,101 MW against 43,092 promised on a measured plan.
**Headroom checks must use the exact one**: it is what the machines actually draw, and
erring the other way would reject a slice that fits.

`sink_mw` belongs to the plan, not to any process — an AWESOME Sink is charged per belt
line of sunk material and no column owns it. A partial slice reports 0 rather than a
prorated invention.

**The shard bill.** `plan_factory` now prints it from the clocks the plan already chose:

```
power shards: 109 needed (64x Water Extractor @150% = 64, 7x Oil Extractor @250% = 21, …);
you hold 22 free + 407 craftable = 429 -- affordable after crafting slugs
```

The arithmetic is easy to get wrong by hand and was: a shard raises the **maximum** clock by
0.5, so 150% costs one and only 250% costs three. Assuming three apiece gave 192 where the
answer is 109.

**Somersloops are reported, never spent**, and only where they do something. Generators and
extractors carry slots with `can_boost = False`, so `boost_for` correctly returns 1.0 — the
first version advertised a Fuel Generator block at "1x output", which is nonsense dressed as
a recommendation. Those slots are counted separately as *unboostable* and never presented as
capacity. `Process.sloops` remains 0 everywhere: nothing plans them yet.

### 8.2f Somersloops: spread, never stacked

The solver has carried `sloop_budget`, `Process.sloops` and `boost_for` since the
formulation was written and **nothing ever set them**, so every plan silently ran at zero
sloops while the readout printed how many slots were going spare. `sloops=` is now a plan
argument, persisted with the plan and hashed into `plan_id`.

It is a **budget, not a switch** — how many you will actually commit — and it defaults to
0. Somersloops are the only genuinely finite resource in the game (a fixed number exist on
the whole map), so a plan that quietly assumed them would be unbuildable in a way no other
default is.

**The modelling correction: offer every count, not just full-or-empty.** Output is linear
in sloops and power is quadratic in the boost they produce:

| sloops in a Blender | boost | power |
|---|---|---|
| 0 | 1.00x | 75 MW |
| 1 | 1.25x | 117 MW |
| 2 | 1.50x | 169 MW |
| 4 | 2.00x | 300 MW |

So marginal output per sloop is flat while marginal power rises, and under a binding
budget **spreading strictly dominates**. Offering only 0-or-full made the solver pay the
worst rate on the scarcest resource in the game: at a budget of 16 it built 8 Refineries
at 2 sloops for 113,945 MW where 16 at 1 sloop gives **114,065 MW**. Measured, and a test
pins the comparison rather than the argument.

`PlanSlice` keeps **spent** slots apart from **empty** ones, because one is a bill and the
other is a suggestion — conflated, a plan that spends none would report 662 somersloops
needed. `sloops_used` counts against WHOLE machines, so it can exceed the budget the LP
solved under (the same rounding that turned a 54-extractor cap into 64 machines); the tool
checks and says so rather than quoting a number that is quietly too small.

**Committed sloops are reported as unknown, never as zero.** A slotted Power Shard shows
up in an `InventoryPotential` component; the production-boost equivalent appears nowhere in
this save under any of the three plausible property names, nor in Docs.json. `free` counts
loose sloops only — 16 on the reference save, 1 carried and 15 in the Depot — and the note
says so, because reporting 0 committed as if measured would overstate the pool for anyone
past mid-game. Mercer Spheres share the WAT prefix and are counted separately.

### 8.2g A recycled fluid needs no recycling logic

Aluminium is the canonical loop: **Alumina Solution** drinks 180 Water/min and
**Aluminum Scrap** hands 120 back, and water cannot be sunk (§5.6), so the returned water
has to go somewhere. Nothing in this project recycles it, and nothing needs to.

Water is ONE balance row. The scrap recipe is a producer on that row, the alumina recipe a
consumer, the extractors another producer, and the equality settles it:

```
4x Alumina Solution                 -720.0 m3/min
4x Water Extractor on normal Water  +480.0
2x Aluminum Scrap                   +240.0
                              NET     +0.0
```

The loop is not a loop in the LP — it is two columns touching one row, which is the same
reason § 8.2 gives for equality balances in the first place. A tree walk would have to
decide how many times to go round; the LP has no notion of "round".

**Two bugs fell out of checking it.**

`water_extractors=0` did not mean zero. `int(x) if x else DEFAULT` treated an explicit
zero as absent and substituted the 200-pump assumption, so a plan told it had **no water**
came back making 480 Aluminium Ingots on 480 m³/min of it. Zero is a meaningful answer —
it is exactly what you ask of an inland site — and it is now `is None`, not falsiness.

With that fixed the plan solved to **nothing, and said nothing**: `buildings=0, exports:`
with no complaint, because an all-zero solve is genuinely optimal. Every recipe was present
and unlocked so `unmakeable` had nothing to report either. The supply probe now runs on an
empty solve for the same reason it runs on INFEASIBLE — a bare zero is not a diagnosis:

```
! this plan is EMPTY -- it solved to zero machines. That is optimal, not broken
! missing raw: Water (water comes from water volumes, and no Water Extractor is unlocked)
```

### 8.2h Running a cycle once, without banning it

*"The Recycled recipes are wanted, just not recursively."* `exclude_recipes` cannot say
that — banning the recipe also bans the useful single pass.

`recycle_once` names processes that may run but must not feed each other. For every item
the named set both makes and eats, consumption INSIDE the set is capped by production
OUTSIDE it:

```
Σ(consumed by named)  ≤  Σ(produced by everything else)
```

That is exactly one pass, and there is no pass counting anywhere. On the coupled Spire
plan:

| | free | once |
|---|---|---|
| net MW | 83,471 | **81,254** (−2.7%) |
| plastic eaten inside the loop | 100 | 150 |
| plastic made **outside** | **0** | **150** |
| Residual Plastic | never built | **8** |

Unconstrained, plastic is made *entirely* inside the loop and some goes straight back in
while Residual Plastic never runs. Constrained, Residual Plastic supplies the single pass
and the constraint binds exactly (150 ≤ 150). Both Recycled recipes still run, which is
the whole point of not reaching for `exclude_recipes`. The 2.7% is the price of refusing
to recurse, and quoting it makes this a decision rather than a preference.

**The cycle is NAMED, not detected, and that is the design.** The first attempt detected
cycles automatically and found **24 items** on this recipe set — because every
package/unpackage pair is a cycle (Water → Packaged Water → Water), as are the aluminium
and fuel loops. Constraining all of them made every plan INFEASIBLE. Only the caller knows
which loop they mean, so the argument takes patterns in the same grammar as
`exclude_recipes`, and a pattern matching nothing is refused rather than ignored.

**Not a pass count.** `max_cycle_passes=2` would need the cycle unrolled into indexed
copies with its items split per pass — a different formulation, not a flag. One pass is
exactly expressible; a number that only looks precise is worse than a named mode.

### 8.2i Solving a plant in pieces

*"Three loops instead of one."* A module is a plan with no nodes of its own and declared
inputs: `supplied={item: rate}` hands it what another plan makes, as a free raw input up to
that rate. `Scenario.raw_caps` had always supported this and nothing reached it.

Given the rig's 2,300 Polymer Resin, the resin plant solves to **30 Residual Plastic + 13
Residual Rubber** — the hand-built module, derived. Chained across all three:

| | machines | net MW |
|---|---|---|
| three loops | 765 | 99,386 |
| one solve | 787 | **99,730** |

That looked like decomposition costing **0.35%**. It was not. Chasing where 344 MW went
found the whole of it in **one row**: the same 10,300 m³/min of water, on 31 pumps at 247%
drawing 2,052 MW, where the single plan used 64 at 134% for 1,887.

**A power-blind objective overclocks, and the cost is invisible in its own answer.**
`max_item Fuel` does not price power, and phase 2 breaks ties by minimising **machine
count** — so among all solutions hitting the target it takes the fewest machines, which
means the highest clocks, and power goes as `clock**1.32`:

| pumps | clock | draw for the same water |
|---|---|---|
| 31 | 247% | 2,178 MW |
| 52 | 165% | 2,017 MW |
| 64 | 134% | 1,887 MW |
| 86 | 100% | **1,716 MW** |

Re-solved with `min_power` and `export_minimums`, the rig runs 64 pumps at 120% and the
chain comes to **99,806 MW against the single plan's 99,730** — decomposition *wins* by
77 MW, on 798 machines against 787. So the real rule is not "splitting costs optimality",
it is **a module must price whatever the whole plant cares about**; a power-blind module in
a power plant is the bug. `plan_factory` now says so when an objective that ignores power
produces overclocked rows.

### Solver tolerance at an interface

The rig exports **2299.9998** Polymer Resin. That is not instability and not a real
fraction: the build is 115 refineries at 100% making 20/min each, which is exactly 2,300.
HiGHS returned **114.99999** machine-equivalents — a relative residue of 8.7 × 10⁻⁸, right
at its default tolerance — and the printed clock rounds to `1.000000` and hides it. The
displayed row and the displayed rate simply do not reconcile in the last digit.

It is harmless inside one solve and not harmless **across** two, which is why `raw_caps`
carries the tolerance described below.

**A module needs a selector that selects nothing.** `sources=["bbox:…"]` over open water
gives zero nodes, which is what stops the resin plant tapping crude and re-deriving the
rig; water survives it, because water is placeless and has no node to lose.

**The boundary trap, which is inherent to chaining.** `Solution.exports` is rounded to 4 dp,
so the rig exports **2299.9998** resin while the resin plant's demand needs exactly 2300.
Fed on as an exact cap, the module comes back **INFEASIBLE** — a whole plant lost to two
ten-thousandths. `raw_caps` now carries the same kind of tolerance phase 2 already uses to
pin its objective (1e-6 relative, 0.002/min on 2,300). The `binding` check had to move with
it: a cap nudged up by a relative epsilon and compared against a fixed absolute one stopped
reporting an input consumed to the last drop, so the constraint still bit and the response
stopped saying so.

**Supplied items are free, and the output says so loudly.** `advisor` records what
forgetting this costs — a basket fed in as free raw inflated a northern baseline from
92,269 MW to 171,882. Correct for a module, whose inputs are paid for where they are made;
badly wrong as a whole-plant comparison. So a plan using `supplied` is labelled a MODULE
PLAN, its inputs are named with their rates, and it says outright that whatever produces
them must export at least that much or the chain does not balance.

### 8.3 Guards

Both run on **every** solve:

- **Duplicate-process-id assertion.** A real bug: a process id built from building+purity but omitting the
  *resource* made `MinerMk2:normal` collide between coal and sulfur, so one column produced both. The
  model balanced, passed a mass-balance re-check (which keyed off the same colliding id), and returned
  5,985 MW instead of 8,610.
- **Free-lunch audit.** Strip extractors, zero `raw_caps`, maximise MW; must return exactly 0.

### 8.4 Clocks, machine counts and logistics

**Machine counts are whole buildings at a derived clock.** A solve returns `x` in
machine-equivalents; the readout reports `ceil(x)` machines all clocked to `x / ceil(x)`. So 52.8
becomes **53 Blenders at 99.6%**. That is exact, always a clean ratio, and how the game is actually
played — a fractional count is the ratio, not a rounding error.

It is also provably the best way to run that throughput: power is `c^1.321929`, which is **convex**, so
for a fixed total `Σc` the sum `Σc^k` is minimised by spreading the clock *equally*. A uniform clock
therefore beats any mixed set, and the derivation needs no solver support at all.

Because of that, **ratio underclocking is free and automatic**, and solver-side integrality is off by
default: forcing whole machine-equivalents makes exact ratios unreachable and can turn a feasible plan
infeasible, since every item balance is an equality.

**Sub-100% clock modes are a different question, and are priced not banned.** Offering `clocks=[0.5, 1.0]`
lets the solver *spread* a fixed throughput over more machines purely to save power — measured at
+1140 MW for +441 machines, i.e. 2.58 MW per extra machine. That is a real option, so each machine costs
`machine_cost_mw` (default **5 MW**, set just above that measured figure) whenever the objective is
`max_mw` or `min_power`. For `max_item` / `min_raw` no penalty is applied because underclocking gives no
throughput benefit at all and is never selected.

The warning fires **only** for genuine spreading, never for a derived ratio clock. An earlier version
warned on any clock below 100%, which meant a routine 99.4% ratio looked like a tradeoff the caller
should second-guess.

Two proven facts kept from the research:

- **Linear power is a safe over-estimate below 100% clock** (`c^k ≤ c` for `c ≤ 1, k > 1`), which is what
  the LP optimises; the readout then recomputes each row's draw exactly at the derived clock, so the
  reported figure is never worse than the solve promised.
- **Overclocking above 100% is never selected for a throughput objective** — adding 150/200/250% modes
  changed the optimum by exactly 0 MW. It is also not offered by default because it consumes Power
  Shards, which nothing here counts.

**Logistics are reported, never constrained.** Every plan lists each item's flow with the belt or pipe
lines it implies at the current tier. A throughput *cap* would be wrong — parallel lines are legal and
the game has no global limit — but a plan that silently needs 7 Mk2 pipes of water is not a plan. Water
is modelled as unlimited, which on this map it effectively is, so the extractor count and pipe count are
surfaced explicitly rather than hiding inside a ratio.

> **The table must be askable, because volume order buries the question.** The rows rank by flow, and
> the table used to truncate at a hardcoded 6 while ignoring the tool's own `limit`. On the reference
> Spire Coast plan that cut it at Polymer Resin: **Plastic ranks 7th** — one of the two items that plan
> exists to size — so the answer was invisible and had to be reconstructed by multiplying machine counts
> by recipe rates. It now honours `limit`, and `logistics_items=["Plastic", "Rubber"]` pins named items
> whatever they rank. Pins are **added to** the limit rather than carved out of it: naming two small
> items must not push two big ones out, or one blind spot is simply traded for another. Anything hidden
> is counted in the note, and the multi-line warning says how many flows it did not list.

**Somersloops were dormant** by explicit decision, and are no longer. The machinery existed
(`Process.sloops`, `Scenario.sloop_budget`, per-building boost multipliers) and nothing populated the
budget. `sloops=` now does (§8.2f), gated on the research (§6.9), with installed ones read exactly —
OQ4 is closed. The Alien Power Augmenter is still a different model and still unbuilt.

### 8.5 Layout: blocks, buses and floors

`plan_layout` turns a solved plan into a buildable **schematic** — modules, connections, floor
assignment and a space budget. Explicitly *not* a blueprint: no world coordinates and no belt routing,
because there is no terrain heightmap in any data available here and inventing one would be worse than
declining.

**Footprints are derived, not hardcoded.** Every buildable carries `mClearanceData`, so machine
dimensions come from the game: Constructor 8×10×6, Refinery 10×22×15, Blender 18×16×10.5,
Manufacturer 18×20×11. Foundations follow at the 8 m grid.

> **The rotation trap.** Clearance is a *list* of boxes, and they can carry a `RelativeTransform`
> rotation. The Fuel Generator's is several thin boxes at 45° increments approximating a round machine,
> so taking the largest box naively yields **22×4 m** instead of the real ~20×20 — understating a
> 176-generator plan by roughly 1,000 foundations. Every box is transformed by its quaternion and the
> union taken.

**Blocks come from throughput, not taste.** 46 Refineries drawing 1,380 m³/min of crude cannot share one
manifold when a Mk2 pipe carries 600 — that is 3 lines, so it is 3 blocks. **Line count is block count**,
which makes the split derived. A secondary cap (24 machines) keeps a manifold physically sensible.

**Connections are buses, not pairings.** The LP gives net balances, not who feeds whom; recovering
specific producer→consumer pairs is a min-cost flow with no unique answer absent geometry. Each item gets
one bus that producers feed and consumers draw from — which is what a manifold physically is.

**Floors come from chain depth**, computed on the graph's **condensation**. A plain longest path is
unavailable because the recipe graph contains real cycles (Recycled Plastic ↔ Recycled Rubber), and
naive relaxation does not settle on one — it lifts every member a stage per pass, so depth ends up
reporting how long the loop ran. Collapsing each strongly connected component makes the graph acyclic and
puts cycle members on one floor, which is also correct physically. Floor height clears the tallest
machine on it; logistics decks are 4 m.

Site size is the **peak** floor, not the sum, since floors stack.

### 8.5a plan_layout must take the same arguments as plan_factory

`plan_layout` accepted neither `extractor_clocks` nor `water_extractors` (nor `clocks` or
`machine_cost_mw`), so it silently **re-solved at defaults** and schematised a different
plan than the one it was asked to draw — measured at **15,043 MW against the 83,737 MW
plan**, because base extraction is roughly a sixth of overclocked. Both tools now take the
same solve-shaping set and agree; a test asserts the signatures stay in step, because the
failure is silent and looks like a legitimate operating point.

**Floors follow chain depth, which is a correctness property and not a physics one.**
Chain depth puts a consumer above its producer so the schematic reads in build order.
Fluids do not care: a pipe running downhill is free, one running uphill needs head, and
water can only be drawn at sea level whatever the chain says.

The consequence is that chain-depth ordering tends to make *everything* climb. On a
measured oil plan, nothing fell:

| fluid | direction | floors | rate |
|---|---|---|---|
| Water | climbs | 4 | 11,500 m³/min |
| Fuel | climbs | 2 | 9,200 m³/min |
| Heavy Oil Residue | climbs | 2 | 4,600 m³/min |
| Crude Oil | climbs | 2 | 3,450 m³/min |

Reordering by hand — water extractors at sea level under the blenders, generators one
above, refineries on top with crude arriving high — leaves only water and fuel climbing one
storey each, and lets residue and crude fall for free.

`fluid_head()` reports this and `plan_layout` surfaces it. It is deliberately **not**
optimised: the right stack depends on terrain, on where crude arrives, and on how much
pumping the player will accept, none of which this model has. Naming the cost is what lets
a planner disagree with the default.

### 8.5b Capping a deck, and naming the head

**`max_floor_foundations=` inverts the layout question.** Uncapped, `plan_layout` answers
"how big a site does this need" by giving each chain stage a deck of whatever size it
wants — 496×496 m on a measured oil plan. A player with a finished platform is asking the
reverse. Same computation, run backwards:

| cap | production decks | peak | site |
|---|---|---|---|
| none | 5 | 2,920 | 440×440 m |
| 1225 (35×35) | 7 | 1,200 | 280×280 m |
| 900 (30×30) | 8 | 900 | 240×240 m |

**Total foundations are conserved at 4,719 across every cap** — the same machines stacked
differently — and a test asserts it, because a total that moved would mean the cap was
dropping or duplicating blocks. A block larger than the cap gets a deck of its own rather
than being split: a block is one manifold.

**Elevation was never missing.** `z` is in the node table and in every machine position and
was read by nothing but `geo.cluster`'s centroid, so a planner reasonably concluded the tool
had no z-data and guessed pump counts by hand. Node rows now carry it, and a fluid field
reports its head span — the 13 Spire Coast crude nodes cover **−17 to 23 m, a 40 m span**.

Reported as a span, **never as a pump count**: head per pump is a game rule this project has
no data for, and a test asserts no pump count is invented. Solid fields say nothing, because
a coal field climbing 200 m costs a belt nothing.

### 8.5b2 Elevation, sampled -- because there is no heightmap

`z` reached the node table and the trunk head spans, but a bare coordinate still had no
answer. Nothing in Docs.json or the save carries terrain. What both carry is a lot of
things whose Z is exact:

| source | count | is it ground? |
|---|---|---|
| resource nodes | 608 | **yes** -- a node rests on terrain, and needs no save |
| foundation/wall pieces | 8,347 | no -- built elevation |
| production buildings | 566 | no -- built elevation |

So `describe_location` answers with a **sample**, its count and its spread, never an
interpolated surface. A single number invented from three points 40 m apart reads as
measured and is not.

**Ground and built are separate populations, and that is the load-bearing decision.** A
foundation is wherever the player put it -- often deliberately level across a slope. On this
world's main platform the probe finds **805 structures against 1 node**, so an average would
silently *become* the platform height while still being labelled ground. Reported apart,
their difference is the interesting number instead: the fill already stacked there.

Even that difference is refused below `MIN_GROUND_SAMPLES = 3`. Not a statistical
threshold -- a refusal: one node is a point, and a point is not a ground level. Quoting a
25 m fill from a single sample would be an invented number wearing a measurement's clothes.

Unsurveyed ground says so and names the radius, the same way the tool already returns
'off-map or ocean' rather than guessing the nearest land region. And an old projection
(the committed fixture is schema 5, predating `structures`) loses one source rather than
failing.

### 8.5c Which nodes share a pipe

`logistics` already counts LINES -- `ceil(rate / capacity)` -- which is the right total and
says nothing about which nodes share one, and the layout schematic starts at the factory
edge with the crude already arrived. `plan_layout detail="trunks"` fills the gap:

| trunk | nodes | rate | run | head |
|---|---|---|---|---|
| T1 Crude Oil | 3 | 600/600 | 289 m | |
| T3 Crude Oil | 2 | 450/600 | 118 m | |
| T4 Crude Oil | 1 | 600/600 | 0 m | |
| T6 Crude Oil | 3 | 600/600 | 290 m | **UP 40 m** |

**A trunk is a line, not a blob.** Pipes are laid end to end and each node joins the one
running past it, so nodes are ordered along a nearest-neighbour chain from the node
furthest from the destination inward, and the chain is cut wherever the next node would
overflow. Capacitated clustering would give tighter blobs and a worse answer: two nodes
40 m apart on opposite sides of a run are not on the same pipe.

**This is where the head span becomes actionable.** `search_resource_nodes` reports the
Spire crude field spanning 40 m (§ 8.5b). Attached to a trunk, the answer is sharper --
five of the six runs are flat and *one* climbs the whole 40 m. `lift_m` is signed and
measured inward, so the sign is the answer: downhill needs no pumping. Head is reported for
**pipes only**; a belt does not care that its sulfur climbs 218 m.

**And the pump count is now real.** This section previously said head-per-pump was a game
constant with no source and refused to give a number. It is `mDesignPressure` in Docs.json
— **20 m on a Mk1 pump, 50 m on a Mk2** — and it was there all along under a name nobody
grepped for, because the search terms were "head" and "lift" and the game calls it
*pressure*. The third instance in one session of treating a failed grep as proof of
absence (§ 6.9 has the other two). So T6 reads `UP 40m (1x Pipeline Pump Mk.2)`.

Two things keep it honest. It quotes **the best pump the player has actually unlocked**,
not the best that exists — quoting Mk2 to someone without it understates the build by more
than half. And it stays a **lower bound**, because pipe friction and the head a full pipe
holds on its own are not modelled: it answers "at least this many", which is what sizing a
build needs.

Two cases are surfaced rather than smoothed over. A single node above one line's capacity
(a pure Crude Oil node at 250% makes exactly 600 m3/min) gets a run of its own instead of
being split silently -- it is a real problem the player solves with a second pipe off one
extractor. And Water Extractors sit on no node, so they get no trunk and are named in a
note, because 9,200 m3/min vanishing from a table that otherwise conserves every unit
would read as a complete answer.

`run` is the straight-line chain and is labelled a LOWER BOUND: there is no terrain here,
so a drawn route would be invented -- the same line § 8.5 draws around the schematic.

### 8.5d Commissioning: a startup order, not a build order

The re-frame that removed most of this problem came from Lukas: *"Can't we just build an
unpowered factory, and just power it after the build is done?"*

Building costs materials, not power -- a machine draws only when it runs. So the whole
821-building plant is constructed at leisure, drawing nothing, and then energised block by
block. **There is no power-constrained build order to search for.** The commit-granularity
question that looked like the hard part (partition by self-powered slice? by grid-positive
slice? by vertical column?) simply dissolves, and with it the objection that vertical
slices make you revisit every block N times. Each block is built once.

What is left is one hard constraint:

> at every step, sum(energised consumer draw) <= headroom + generation from generators
> already receiving fuel

**Generators are free to energise** -- `power_mw == 0`, `power_production_mw == 250`, read
from the dump. Only consumers spend headroom, so a wave costs its consumers and refunds its
generators, and the refund pays for the next wave. On the measured Spire Coast plan
(14,524 MW draw, 121,875 MW generation) that converges in four waves from 711 MW free:

| wave | machines | draw | generation | free after |
|---|---|---|---|---|
| W1 | 16 | 661 MW | +1,249 MW | 1,299 MW |
| W2 | 49 | 1,171 MW | +6,493 MW | 6,622 MW |
| W3 | 284 | 5,093 MW | +41,957 MW | 43,485 MW |
| W4 | 472 | 7,598 MW | +72,176 MW | 108,062 MW |

**The bound is hard, not advisory.** Exceeding available power in Satisfactory does not
degrade gracefully -- the fuse blows and the whole grid stops until reset by hand,
including the plant that was feeding it. So the tool also recommends **one Power Switch per
block**, which has to be built in from the start; energising is then a switch flip and a
misbehaving block can be isolated.

**A wave never pays for itself.** Between energising a wave's refineries and its generators
burning fuel, the pipes are filling and nothing is coming back, so `available` only grows
once the wave completes. This is the difference between a sequence that works and one that
looks fine on paper.

**And the wait now has a number.** "Wait, then next wave" was the least actionable line in
the sequence, and the wait is exactly the interval the deficit is carried through.
`Wave.fill_s()` sums the slowest cycle at each chain depth — every stage must finish one
full cycle before the next sees anything — divided by clock, since a machine at 250%
finishes in 40% of the base time. The measured Spire waves come to **≥ 35 s**
(1 s extraction + 12 s refining + 6 s blending + 16 s packaging).

It is presented as a **floor**, not an estimate, and the reason is the recurring one: a
pipe's fluid volume is not in Docs.json. The only dimension there is `mRadius`, which is
collision geometry, and turning it into litres would be a guess dressed as a measurement —
so pipe transit is excluded and dominates on a long run. Machine input buffers are out for
a related reason: their capacity is per-BUILT-machine and these machines do not exist yet.
A generator contributes zero, because it burns continuously and has no cycle to wait
through.

**The floor is reported.** One machine of every process -- the cheapest slice that still
feeds the whole chain -- costs **631 MW** here against 711 free. Below that no startup order
exists at all, and the tool says so and names the number rather than emitting a sequence
that trips on step one.

Two honest limits. Waves are power-ordered, **not ratio-balanced**: whole machines cannot
hit the plan's ratios at the bottom of the ramp, so early waves run starved. That errs safe
-- a starved machine idles and draws less than modelled -- and is stated rather than dressed
up as a balanced mini-plant. And headroom is printed as a **labelled input**
(`source: power_report, nameplate`), so a sequence computed against a save that has since
moved is visibly stale instead of quietly wrong.

Node choice follows the same least-work-first idea (§ 8.5c). The ranking is by what a node
COSTS to take, which is not "prefer untapped": a node already carrying the extractor this
plan wants is cheapest of all, untapped is next, and a node held by the WRONG extractor is
last because taking it means demolishing something running. On the reference save all
thirteen Spire crude nodes are tapped -- every one by the Oil Pump the plan wants -- so a
plain free-first rule would have ranked them all equal-worst.

### 8.5e Which stage am I in — detected from the save, never stored

Lukas: *"I feel like the mcp should have fundamental capacity to identify stages."* So a stage is a
domain concept, not a printout — and usefully, one that needs **no new persistence layer at all**.
A stage is a partition of a stored plan (§ 8.5d), and `diff_vs_save` already matches built machines
against a plan by identity, so *grouping that output by stage* is the whole feature. Nothing is
written; a stored plan is still only a stored *request* (§ 10.1a) and is re-solved every call.

Three modules and no new tool, because each already owns exactly one half of the answer:

| owns | module | contributes |
|---|---|---|
| the partition | `commission()` | which machines are in wave *k* |
| the matching | `build_diff()` | which of them exist in the save |
| the evidence | `graph.health.assess()` | what each existing one is doing |

`track()` only joins them, on `diff.group_key` — the same key the diff matches on, promoted from
private to public for exactly this reason. Joining on anything else (the display label, the building
class) would let the tracker credit a Refinery on Alt HOR with one making alumina, which is the
failure §8.6 exists to prevent, re-introduced one layer up.

**Built machines fill the earliest stage first.** Identical machines are indistinguishable in the
save — nothing records which Refinery was *meant* for wave 2 — so progress is assumed to have been
made in the order the sequence prescribes, and within a stage the machines proven to be running are
taken first. This is a modelling decision, not a measurement, and it is stated in the code that makes
it. Any other rule needs evidence the file does not carry.

#### Built and energised are different states, and the save proves only one of them

This is the crux, and conflating the two would make the tracker lie on the most important day of a
build. Under the Q1 re-frame you construct the entire plant unpowered and then energise it block by
block, so **fully built and wholly dark is the expected state**, not an anomaly. What the file
actually supports, measured on the reference save rather than assumed:

| signal | present | what it settles |
|---|---|---|
| `uptime` (300 s productivity window) | **517 of 566** machines/extractors/generators | `produce_s > 0` **proves** the machine ran, so it **had power**. The only positive evidence of energisation in the file. |
| `uptime` at zero | the other side of the same field | Proves nothing. Unpowered, starved, blocked and idle are indistinguishable. |
| `buffers` | every record | Names a *supply* cause (blocked / starved), which **excludes** power as the explanation but can never confirm it. |
| `paused` (`mIsProductionPaused`) | 16 actors | A different thing entirely: the player switched this machine off, recorded per machine whatever the grid is doing. |
| `clock` (`mCurrentPotential`) | 46 actors | A slider position, not a state. Does not move when power does. |
| power wires (`graph.power`) | 1,287 edges | "Wired to nothing" is knowable. Wired is **not** energised. |
| `mHasPower` | **0 of 44,307 objects** | Carries no `SaveGame` specifier on `UFGPowerInfoComponent` (checked in Headers.zip). Not in the file. |
| `mCircuitID` | **0** | Same — grid membership is rebuilt at load, so *which grid a machine is on* is not readable. |
| `BP_CircuitSubsystem` | 1 object, **empty property set** | Confirms the above from the other direction. |
| `mIsSwitchOn` | *is* `SaveGame` on `AFGBuildableCircuitSwitch` | …but this world has built **no power switch at all**, and the projection does not read one. Even the per-block switch § 8.5d recommends would be unreadable today. |

So a stage reports `running` — what it can prove — and **refuses to convert silence into
"unpowered"**. The residue after the save's own explanations (paused, starved, blocked, no recipe,
dead node) are taken out is reported as `dark`, health.py's `stalled` bucket plus the unmonitored,
and named as *consistent with* not being energised rather than as evidence of it. Same precedent as
`sloop_budget()` reporting committed sloops as unknown rather than zero (§ 6.5) and
`phase_requirements` labelling stale rows instead of filtering them (§ 6.4). A save carrying **no**
monitor at all — the committed test projection is one — reports *no evidence either way*, which is a
different answer from "nothing is running" and must never be printed as one.

#### The surface

No new tool. `diff_vs_save` gains one argument:

* `plan=<name>` alone appends the stage table — a stored plan is what makes a stage number worth
  writing down, so recalling one turns grouping on without being asked.
* `stage=<n>` narrows to one stage's delta, and **drops the cost table**: a stage is a switch-on, not
  a build step, so splitting the materials bill across stages would describe a build nobody does.
* `stage=0` asks for the overview without a stored plan, at the stated cost that the numbering
  renumbers whenever an argument or the world moves.
* Plan-id drift is reported *here*, not only in `list_plans`, because a stage number is a milestone a
  player remembers and a re-solve against a moved world can renumber the whole partition under them.

Grouping is off by default: the numbering is only stable for a stored plan, and a diff nobody asked a
stage question of should not pay the context for one (the 2,600-char diff budget is pinned). Where
the stage table appears, the older *"place it in ≥18 proportional slices"* line is suppressed — that
is the answer from **before** the startup-order re-frame, and printing it beside a startup order tells
the player to partition a build that is not partitioned.

Measured against the reference save with the stored `spire-coast-full` plan (787 buildings, 99,730 MW,
711 MW free):

| stage | on | built | running | MW | free after | state |
|---|---|---|---|---|---|---|
| S1 | 17 | 13 | 13 | −701 / +1,750 | 1,761 | 76% built |
| S2 | 72 | 22..25 | 23 | −1,627 / +10,000 | 10,134 | 31–35% built |
| S3 | 524 | 7..23 | 10 | −10,024 / +76,500 | 76,610 | 1–4% built |
| S4 | 174 | 0 | 0 | −2,901 / +26,750 | 100,458 | not built |

→ *you are in **stage 1** of 4: 76% built (13/17), 13 machines proven running.*

Built counts stay a **range** wherever identity is unavailable, for the same reason § 8.6 gives — and
that is why `running` can sit *above* the low bound without contradicting it: the low bound throws
away every Water Extractor that cannot be attributed to this plan, and some of those are running. The
two columns have different denominators, and the report says so rather than letting the numbers
argue with each other.

### 8.5f What a plan costs to build

The startup re-frame separated running cost from construction cost (§ 8.5d), and only the
first had ever been measured. `plan_layout detail="materials"` measures the second:

```
machines=821  foundations=4719  distinct_parts=15
costliest: 488x Fuel-Powered Generator = 78,080 parts, 4719x Foundation = 23,595 parts

item                      need    have   short   for
Concrete                 23595   60622          Foundation
Rubber                   24400    5996   18404  Fuel-Powered Generator
Motor                    10705    5254    5451  Fuel-Powered Generator, Blender, Refinery
Heavy Modular Frame        920     244     676  Blender
```

**This does not replace `diff._cost`, and the difference is the point.** That charges the
**delta** -- what is left to place -- filtered to what you are short of and ranked by how
hard the shortfall is to fix: a shopping list for the next session. This charges the
**whole plan**, every item whether or not you hold it, attributed to the buildings that
want it. Neither is derivable from the other, and a reader who conflates them will think
the plant is cheaper than it is, so the output says so out loud.

**Foundations are the number nobody had.** They are not machines, so no build table counted
them, and 4,719 of them at 5 Concrete each is 23,595 Concrete — larger than every machine
line except Rubber. It is charged at `total_foundations`, not `Layout.foundations`: the
latter is the PEAK floor, which is what sizes the *ground* because floors stack, but
concrete is poured for every storey. Charging the peak would understate the deck by the
height of the stack.

Two refusals, both familiar. **Belts and pipes are not costed** -- their cost is per metre
and there is no route, so a length here would be the largest invented number in the
project; line counts and the trunk lower bound are offered instead. And the bill stops at
**build-gun components, not ore**: flattening would have to guess a depth through the
Recycled Plastic / Recycled Rubber 2-cycle, which is exactly why `bom` uses an LP. The two
compose -- this says "10,705 Motors", `bom` says what a Motor costs.

### 8.5g One sizing primitive

"How much floor do N of these need" was being answered in two places with the same wrong
arithmetic. `plan_layout` sized every block as `footprint.foundations × n`, and the
water-siting note had independently grown its own copy — while `Footprint.foundations`
says in its own docstring that it is per-machine and **ignores shared edges**.

It is one computation and it is now in one place: `Footprint.pack(count, columns=)`.

| pumps | `n × found` | packed block | pier |
|---|---|---|---|
| 30 | 270 | 10×3 = 200×54 m, **175** | 540 m, 204 |
| 64 | 576 | 8×8 = 160×144 m, **360** | 1,152 m, 432 |
| 77 | 693 | 11×7 = 220×126 m, **448** | 1,386 m, 522 |
| 105 | 945 | 15×7 = 300×126 m, **608** | 1,890 m, 711 |

`columns=1` gives a single row, whose *length* is what platform modules get measured
against. Left at 0 it searches every column count — and **two false starts are worth
recording, because both looked obviously right**:

*Squarest* was the first rule, on the reasoning that perimeter waste costs tiles. It is
wrong: four Oil Extractors (8×13 m) laid 3×2 span 24×26 m and need **12** tiles, where
four in a row span 32×13 m and need **8**. Unfilled grid slots and depths that land just
past a tile boundary lose more than the perimeter saves.

*Cheapest* then produced ribbons: the true tile optimum for 77 pumps is 2×39, a
**40×702 m** strip that wastes nothing at either edge, saves 4%, and is not a thing anyone
builds. So the default is cheapest among shapes within `MAX_BLOCK_ASPECT` (4:1) — the one
judgement call in the module, labelled as such — **plus the single row, always**. Keeping
the row as a candidate is what preserves the guarantee that this never exceeds
`n × foundations`; without it a 5-machine Lookout Tower block came out at 6 tiles against
the old 5, making the change an improvement on average and a regression in places.

**It moved real numbers.** The Spire Coast plan's deck fell from 6,529 foundations to
**4,719** — 28% — its peak floor from 3,774 to 2,920, and its site from 496 m square to
**440 m**. Everything downstream inherited the correction at once: the concrete line in the
build bill (§ 8.5f), the deck-cap table (§ 8.5b), and `assess_fit`'s verdict on whether a
plan fits an existing platform. That is the argument for one primitive rather than two
agreeing implementations — the second copy is not merely duplication, it is a second thing
that can be wrong on its own.

The layout tests did not need changing, which is the other half of the point: they assert
conservation across caps and `peak == max(floor)` rather than hardcoded totals, so they
survived a 19% shift in the underlying number and would still have caught a cap that
dropped or duplicated a block.

### 8.5h Duplicated logic found by asking

A sweep for "same computation implemented twice", prompted by the sizing primitive above
turning out to be exactly that. Ranked by whether the copies can actually disagree.

**Chain depth — fixed, and it was a live bug.** `commission._depths` relaxed depths
iteratively with a cap while `layout.chain_depth` condenses strongly connected components,
and `diff` already used the latter. On a Recycled-shaped 2-cycle they disagree outright:

| process | `chain_depth` | the relaxation |
|---|---|---|
| ore | 0 | 0 |
| plastic | **1** | 7 |
| rubber | **1** | 8 |
| sink | **2** | 8 |

The relaxation splits cycle members across stages, which is wrong (they have to be
energised together) and unstable (the depth depends on the iteration cap). It mattered
across modules, not just locally: `track` joins a commission wave against a diff row, so
the two halves of "which stage am I in" could order the same plant differently. Now one
function, with a test asserting the two agree on the reference plan.

**Carrier and line count — fixed. Three copies, not two.** `layout._carrier` /
`_lines_for` and `optimize._logistics` both derived carrier, unit, capacity and
`ceil(rate / capacity - 1e-9)` from `item.is_fluid` — and `Item.unit` had already
centralised the unit string both re-derived.

The shared epsilon is the tell. `- 1e-9` is load-bearing (1,560/min over a 780/min belt is
exactly two lines; binary rounding makes it three) and nobody arrives at it independently,
so one copy came from the other. They had already drifted on `capacity <= 0`: layout
returned 1 line, optimize returned `None`. Unreachable — capacity comes from a tier lookup
with a non-zero fallback — but a divergence inside duplicated code is a bug waiting for the
day it becomes reachable. Now `planning/carrier.py`, resolved toward 1, because the count
feeds block splitting and `None` would need a guard at every use.

**Centroid and spread — fixed, six sites.** `graph/identity.py` and `graph/query.py`
each carried `sum(p[0])/len(p)` plus `max(dist(a, b))`; `diff` had a named `_centroid`;
`app`, `select` and `trunks` inlined theirs again. `geo.Cluster` had both all along, just
shaped for node dicts rather than `(x, y)` tuples — which is how the copies started.

Now `geo.centroid` and `geo.diameter_m`, with two behaviours pinned that the copies
disagreed about. **Empty returns `None`, not the origin**, because (0, 0) is a real and
important place here — the world centre every compass direction is measured from — so
answering it for "no points" is a plausible wrong location rather than an obvious one.
And spread compares **`i < j` only**: the inline copies iterated
`for a in points for b in points`, computing every pair twice plus the zero diagonal.
Same answer, double the work — 317k distance calls instead of 158k at 563 machines — and
a test pins that the cheaper form changed no number.

**`math.dist(...) / 100` vs `geo.distance_m` — fixed, with one deliberate exception.**
`cohere`, `identity`, `select`, `trunks`, `elevation` and `tools/spatial` each retyped the
centimetre conversion by hand, in three different shapes: `/ 100.0` after the fact,
`<= link_m * 100` scaling the threshold instead, and `limit = radius_m * 100.0` hoisted
into a variable. All now compare in metres.

`graph/structure.py` keeps raw `math.dist`, and that is correct: it works in centimetres
throughout, against cm thresholds (`LINK_XY`, `LINK_Z`, `STAND_ON`) measured in the save's
own units, and reports nothing to a caller in metres. There is no conversion there to get
wrong, and introducing one would mean dividing by 100 only to compare against constants
that would have to be rewritten. Said in the module, so it does not get "fixed" later.

`trunks.run_m` needed the other thing entirely — a **3D** length, because the vertical leg
of a pipe is real pipe. It was passing 3-tuples to `math.dist` and letting it silently do
3D, which put the distinction in the shape of a tuple rather than the name of a function.
Now `geo.distance_3d_m`, separate from `distance_m` rather than a flag on it, because
dropping Z is a modelling decision: for "is this near that", a 40 m climb is noise against
a 400 m walk.

A test walks the source tree and fails if `math.dist` appears anywhere outside those two
modules, so a hand-typed conversion cannot creep back in.

### 8.5i Carrier tiers, assumed for a year

`belt_ipm=780` and `pipe_m3min=600` — Mk5 and Mk2 — were hardcoded defaults, and an entire
design session ran on them with nothing checking the tiers were unlocked. They were, on
this save. Had Pipeline Mk.2 been locked, **every pipe count doubles**: six crude trunks
become eleven and the deck stops fitting. Silent-wrong-by-default is the worst failure mode
a planner has.

Three separate bugs came out of checking:

**The tier is now read from the save.** `best_belt()` / `best_pipe()` pick the fastest
UNLOCKED tier, and `list_buildings` marks every row HAVE or LOCKED with its built count —
the same treatment `alternates_for_item` already gave recipes. Mk6 belt is locked here and
the pick correctly refuses to reach for it.

**`items_per_min` is not the test for "is a belt".** A Personnel Elevator reports 400/min
and carries *people*; a Conveyor Lift duplicates a belt tier's rate. Selection is by native
class (`FGBuildableConveyorBelt`, `FGBuildablePipeline`), or a naive "fastest thing with a
rate" can name something that is not a belt at all.

**`belt_tier` and `pipe_tier` had never worked.** The lookup keyed on
`name.replace("Conveyor Belt ", "")`, which yields `"Mk.5"` — while the parameter defaults
were `"Mk5"` and `"Mk2"`. Nothing ever matched; every call fell through to the hardcoded
780/600, and it looked right only because those were the same numbers. `belt_tier="Mk3"`
would have quietly planned at Mk5 speed. Tokens are normalised now and an unknown tier is
refused by name rather than silently defaulted.

**And the tier reached the schematic but not the solve** — §8.5a's drift, again, in a new
place. `plan_layout` passed its resolved rates to `build_layout` and left the scenario on
the default, so `pipe_tier="Mk1"` changed the block split while the trunk view (which reads
`sc.pipe_m3min`) stayed on Mk2: one response describing two different plants. Carrier
throughput is a stored plan argument now, because it shapes the solve — `belt_ipm` prices
sinks — and not merely the drawing.

### 8.5j What an unlock is worth to THIS plan

`advise_hard_drive` answered this for the two options of one pending drive. The question
underneath — across every alternate *not* unlocked, which would change the factory being
built — was being answered by tracing the recipe tree by hand.

`rank_unlocks` is one counterfactual per candidate, reusing `advisor._solve_with` rather
than growing a second copy of that machinery. On the measured Spire Coast plan:

```
baseline=107257.64  candidates=79  movers=1
gain      vs base  alternate                    machines  on offer  needs
14539.71  +13.6%   Alternate: Turbo Blend Fuel  38        drive 25  Blender
```

**One of 79.** And it is sitting in a pending hard drive with no rerolls left, which turns
`advise_hard_drive_pick` from "here is what is in the pool" into "here is what it is worth
to the plant you are building".

**Sweeping everything, because it is cheap.** A solve here takes **0.01 s**, so all 79 cost
about a second. An earlier guess of 1.5 s per solve had me designing a relevance filter —
only test recipes touching an item the plan already moves, which would have cut 79 to 21 —
and that filter would have been actively wrong: a recipe that opens a chain the plan cannot
currently reach touches none of its items *by definition*, and is exactly the interesting
case. Measuring first removed the need for the cleverness.

**A zero is an answer.** 78 of 79 change nothing, and saying so is the point: "you are not
missing anything here" is the decision the hand-walk was producing. So the sweep reports
how many were tried, not only the winners.

Three things keep the number honest. `gain` is oriented so larger is always better, because
`objective_value` is already sign-normalised for max/min and a `min_raw` plan that halved
its ore would otherwise report a large *negative* gain and sort last. Every delta is an
**upper bound**: a candidate is solved as if any machine it needs already existed — Turbo
Blend Fuel wants a Blender this world has never built — with that machine named beside the
number.

And each row names **what the gain switches on**. That began as a plan to flag "consumes an
item that crosses no site boundary", which the planner correctly called fuzzy — Turbo Blend
Fuel also drags in Sulfur and Petroleum Coke, a bigger architectural change than the fuel
return. Listing the processes the counterfactual *activates* and the baseline did not needs
no judgement at all: `Coal-Powered Generator on Coal, Petroleum Coke`. A headline that turns
on reintroducing a chain you deleted on purpose is a decision, not a free win.

**The trap this tool set for its own author.** Run with ad-hoc arguments it measures a
different plant from the one saved, and the answers genuinely differ:

| plan | movers / 79 | Turbo Blend Fuel |
|---|---|---|
| ad-hoc Spire Coast, no exclusions | 1 | +14,540 MW, +13.6% |
| `spire-coast-full` as saved | **0** | — |

The saved plan bans Turbofuel and coal generators, so the recipe produces a fuel it cannot
burn and is worth exactly nothing. Both answers are correct; only one is about the factory
being built — and this write-up originally reported the wrong one. A call without `plan=`
now says so and names the saved plans it might be ignoring.

### 8.5k Sites: accounting, not optimisation

A planner asked for a joint multi-site solver, then talked himself out of it while
answering what the sites were:

> The thing that actually bit me wasn't optimisation across sites — it was accounting
> across sites.

His three-module oil plant (rig / generator hall / resin plant) turned out to be
**preference-driven**. Only the rig's siting is forced, by water being drawable at sea
level; the hall has no siting constraint at all and the resin plant only wants a shoreline
of its own. A joint LP with no per-site cap would collapse all three into one — and that
collapse would be **correct**, because nothing in the model prices distance. Honouring a
preference the user never expressed as a constraint would be the optimiser being wrong.

So `plan_layout detail="sites"` builds the half with a defensible answer: declare a
partition, report what crosses it.

```
site     machines  net_MW        from      to        item           rate   carrier
A-rig    284       -13,982.82    A-rig ->  B-hall    Fuel           9200   16x pipe
B-hall   460       115,000       A-rig ->  C-resin   Polymer Resin  2300   3x belt
C-resin  43        -1,270.29     A-rig ->  C-resin   Water          1100   2x pipe
```

That reproduces his hand-built interface table exactly, and the coupled variant reproduces
the other one: A→B drops to 14 pipes and a new A→C Fuel link appears at 1,150 m³/min on 2.
**One interface going from zero to nonzero is the whole difference between the two
architectures** — 99,729.62 MW against 83,470.97, both pinned as regression tests.

**The error it exists to catch.** Reconciling by hand, he computed generator count from the
rig's total fuel output, assuming all 9,200 m³/min reached the hall. The resin plant's
plastic cycle was drinking some. A partition cannot make that mistake — the plan it cuts is
already mass-balanced by the LP's equality rows — but an *incomplete* partition can hide
it, so unassigned processes are named and the table is declared incomplete. A process
claimed by two sites is reported rather than resolved: a machine is in one place, and
first-wins would hide the ambiguity behind a plausible table.

Two attributions are refused. A shared flow is split between consumers **by share**,
because the LP gives net balances and never who fed whom — the same reason § 8.5 models a
bus rather than producer-consumer pairs. And site power **excludes the AWESOME Sink
charge**, which belongs to the plan as a whole (§ 8.2e).

**What is deliberately not built:** the joint solve. It becomes worth having when two sites
compete for one scarce input — *"I need 2,000 plastic: Spire Coast oil or Western
Beaches?"* — which this plant never did. The one constraint that was observed to bind and is
genuinely site-scoped is **water access**, since water is only at sea level: capping the
reference plan to 27 extractors cost 18.7% and shifted the recipe mix. Making
`water_extractors` per-site is the one piece that needs the solver, and it should wait for
a case where it binds.

### 8.5l Four ways the surface refused to answer

From a second planner's session review. Each cost real hand-work, and none needed new
modelling — only a surface that reached what the data already held.

**A site keyed on the item it produces matched nothing, silently.** Generators produce
`__MW__`, so a site spec of `{"hall": ["MW"]}` matched no process LABEL, came back empty,
and dropped 460 generators into `unassigned` — taking the 9,200 m³/min fuel flow, the one
number a multi-building plan exists to report, out of the interface table. The
incompleteness note fired, but nothing said *which* site was empty. Empty sites and dead
patterns are now named, and the note says what a pattern matches: a label, a building or a
recipe, never a produced item.

**`sloop_budget` existed and nothing exposed it.** The only way to learn how many
Somersloops you held was to guess a `sloops=` budget and read the shortfall warning — you
had to guess the budget to discover the budget. `somersloops` mirrors `power_shards`:
free / committed / owned, where they are, and which machines hold them.

**`list_buildings(kind="all")` matched nothing**, and the AWESOME Sink and both Pipeline
Pumps fell through every branch of the kind filter. So sink draw could not be checked
against the 30 MW the optimizer actually charges, and pump head — added the same day —
was unlistable. A caller fell back on general knowledge, which is the exact failure the
rest of this surface is written to prevent. Kinds are a table now; every building is
reachable and an unknown kind lists the valid ones.

**`recipe_detail` refused a display name** it could resolve, sending a caller to
`search_recipes` and back. It uses `match_recipes` now — the same resolution
`exclude_recipes` has always had — and an ambiguous name lists its candidates rather than
pretending to be unknown.

### 8.5m Ordering floors by head, and paying for the risers

`fluid_head` has always said *"water can only be drawn at sea level, so putting its
extractors at the bottom with consumers above lets the rest of the stack fall"* — and then
ordered floors by chain depth anyway. Naming a cost and defaulting to the arrangement that
pays it is the gap; a planner reordered it by hand and halved the water lift.

`order_floors_by="head"` searches the orders. Floors may be permuted freely, because a pipe
runs in either direction and only the **upward** leg costs pumps — chain depth is a
correctness property (a consumer above its producer reads in build order), not a physics
one. The one fixed point is **Water Extractors pinned to the bottom deck**: water cannot be
drawn anywhere but sea level, so a stack that lifts water to reach it is not a build
however good its arithmetic.

| | chain | head |
|---|---|---|
| water lift | 4 floors | **2 floors** |
| Heavy Oil Residue | +2 floors | **−4 (falls)** |
| pipe-storeys | 66 | **52** |
| pumps | 48 | **46** |

**The search metric is a proxy, and it is checked.** It minimises pipe-storeys weighted by
LINE COUNT — pumps serve one pipe each, so 10,300 m³/min of water is eighteen risers, not
"10,300 units of badness". But pumps round *up* per line, so a 21% better proxy bought only
4% of pumps here. A proxy that can be wrong in the small can be wrong in the large, so both
stacks are built and their real pump counts compared, and the head order is discarded if it
does not win. Two floor builds, against 40,320 if the search itself counted pumps.

**And the risers are now in the bill.** Pumps were absent from `detail="materials"`
entirely, so a fluid-heavy plan understated its own build by 46 buildings. Metres come from
the floors actually crossed rather than storeys times an assumed storey, head per pump from
`mDesignPressure`, and the tier is the best the save can place. Still a lower bound: pipe
friction and the head a full pipe holds are not modelled.

### 8.5n Tracing what feeds what

`factory_query` answers this between labelled sets. The question underneath was
unanswerable, and it is the one a cutover asks: thirteen Oil Extractors sit on the Spire
nodes, twenty Fuel Generators are burning, and repiping the wrong extractor first drops
several GW.

**The measured answer is one.** Of sixteen built Oil Extractors, exactly one reaches the
running generators; the other fifteen reach nothing. So "repipe the extractors" is fifteen
safe moves and one that browns out the base — and `commission_plan` now says so beside the
wave rather than leaving it to a tool you have to remember to call.

**Direction is read, not inferred.** Every material edge already carried the connector
role at each end. Of 2,300 connectors landing on a production machine:

| connector | count | oriented |
|---|---|---|
| `Input` / `Output` | 2,002 | ✅ |
| `PipeInputFactory` / `PipeOutputFactory` | 126 | ✅ |
| `FGPipeConnectionFactory` | 172 | ❌ by name |

**92.5% state it outright, and every one of the remaining 172 sits on an extractor or a
generator** — an extractor only produces and a generator only consumes, so the machine's
own nature settles the edge exactly. Segments with neither end known (belt-to-belt,
pipe-to-pipe) are walked **both ways**: over-reporting a feeder is recoverable, missing one
is what costs 5 GW.

A measurement trap worth recording: asking whether *both* ends name a direction reports
**0% of 11,664 edges orientable**, which is true and useless. The far end is nearly always
a belt, and a belt has no direction as an object — only the machine end does.

**Logistics is traversed, not reported.** A trace from the generators touches 331 nodes at
depth 72, almost all conveyor. The walk passes through and lists only machines, the same
thing `graph.query` does to find a factory boundary.

**Only proven-running generators are charged.** `power_at_risk` counts a generator that
produced inside the last complete 300 s window; one that did not may be idle for a dozen
reasons, and charging it would inflate the risk of touching a line that is already dead.

### 8.6 Diff vs save — what to actually change

`plan_factory` says what the factory should be. `diff_vs_save` says what to do about it. The hard part
is not arithmetic, it is deciding which existing machine **counts** toward the plan.

**Identity, never position.** Three keys, each forced by what the save stores:

| plan row | key | why |
|---|---|---|
| recipe | `(building, recipe)` | `mCurrentRecipe` is per machine and is exactly what the player changes. |
| generator | `building` only | A generator has no recipe; `mCurrentFuelClass` is whatever is piped in. 176 on Fuel + 20 on Turbofuel is **196 identical buildings and one plumbing decision**. |
| extractor | the node it occupies | Resolved from `mExtractableResource`, and the plan's extractor columns were built from those same node rows. Exact, no inference. |

Class-only matching is the failure this exists to avoid: the save has **36 Refineries, of which 5 run
Alt HOR** and 31 make copper, plastic and alumina. "You have 36, build 10" is arithmetically true and
tells the player to break their copper line. Off-recipe machines are named as a reuse pool, never counted.

**Spatial proximity is not part of matching**, and the evidence is in this save: the region raster is
±256 m advisory and splits the single 458-building site across *three* region names, 300 m single-linkage
merges the oil plant and the main base (only ~900 m apart) into one cluster, and the plan's own build
targets sit 0.4–2.5 km out. No radius separates them. Position is used only to *report* (`where(km)`
distance bands from the anchor — the centroid of the in-scope tapped extractors, the only thing a plan
pins to the ground) and for one 200 m proximity test that gates what is **mentioned**.

**Where identity is unavailable, emit a range.** All 23 Water Extractors point at `FGWaterVolume`
objects that are not node keys (OQ5), so none can be attributed to a plant. 31 needed vs 23 built is
reported as **build 8..27** — low bound counts every pump in the world, high bound only those standing
among the plan's own machines (4 at the plant, 13 at the main base, 6 at 2.5 km). A single number there
is a confident lie in whichever direction it falls.

**Action taxonomy, free actions first**, because materials are the scarce resource:
`UNPAUSE` → `SETRECIPE` → `BUILD`. On this save that turns "build 4 Assemblers" into four dropdowns —
4 Assemblers with **no recipe set** stand 20 m from the plan's Compacted Coal Assembler, so reusing them
has zero opportunity cost. Paused and recipe-less machines appear **only where they intersect the plan**;
the 10 paused biomass generators belong to `world_summary`, not here.

**There is no `DISMANTLE` verb.** Machines standing among the plan but not in it are listed for the
player to judge. Two guards keep that block honest: the 200 m radius (which excludes the 32 Coal
Generators at 887–1060 m) and a shared-item test — without it, one matched Assembler standing in the main
base swept in 22 Iron Ingot Smelters and 19 Iron Rod Constructors. What survives is the right answer: the
Diluted Packaged Fuel route the 37 Blenders replace.

**Clocks.** A reclock is proposed only when a machine's **own** clock is off 100%, never by comparing
against the plan's clock — 99.43% is a derived ratio (§8.4), and comparing against it renders an ordinary
plan as hundreds of slider adjustments. An overclocked machine is *noted* and never actioned: the oil pump
at 250% means the plan understates what the player already extracts.

**Ordering** reuses the layout's SCC-condensed `chain_depth`, so extractors fall out at stage 1 and
generators last with nothing special-cased. Power is **incremental** — charging the plan's total would
double-count the 20 Fuel Generators that already draw. "Power first?" then becomes a number: 4,655 MW of
new draw against 831 MW of headroom, and since an LP solution is a **ray**, any fraction of it is itself
feasible and self-powered, so the answer is "build it in ≥6 proportional slices".

**Cost** totals `build_cost` for the build counts against `WorldState.stock()` — carried + storage +
Depot, never machine buffers. Only shortfalls are shown, ranked by shortfall ÷ existing production lines,
so the finding is *"8,800 Rubber and you have no machine making Rubber"* rather than a shopping list.
Items with no automatable recipe (Portable Miner) have zero lines by nature and are demoted.

**Signature: re-solve, no plan handle.** `diff_vs_save` takes `plan_factory`'s arguments verbatim and
re-solves; the server keeps no state and a handle would be invalidated by every autosave rotation. Both
tools print `[plan <id>/save <id>]`. `plan_id` hashes the arguments **plus the save-derived solve inputs**
(unlocked recipes, extractor node census, buildable set), so two responses sharing it are provably the
same plan; `save_id` hashes the machine census, so *same plan, different save* is the mid-build signal.
Solving is deterministic on identical inputs (verified), which is what makes the id meaningful. Cost:
~1.5 s per call and the model must repeat the arguments.

Infeasible and empty plans short-circuit. An empty table would read as "you already have it", which is
the opposite of both.

### 8.8 Constraining the recipe set

Every planning tool takes `exclude_recipes` and `only_recipes`, applied in `build_scenario` so one
translation serves all of them. Patterns resolve in widening order — class id, exact display name, then
case-insensitive substring taking **every** match.

Substring-takes-all is the load-bearing choice: `["Recycled"]` must drop both Recycled Plastic and
Recycled Rubber, because banning half a two-recipe loop leaves the loop intact and the ban useless.
Exact-name match short-circuits it, because `"Plastic"` is the literal name of `Recipe_Plastic_C` and
there would otherwise be no way to target a single recipe whose name is a substring of others.

**A pattern matching nothing is reported, never ignored.** A silently dropped ban returns a plan happily
using the recipe the user forbade, which is worse than refusing because it looks like compliance. The
`plan_id` also covers the recipe set, so a banned-recipe plan cannot be confused with an unbanned one by
`diff_vs_save`.

Worked example on the reference save — max MW from Spire Coast with 300 plastic and 300 rubber required:

| | net MW | routes chosen |
|---|---|---|
| unrestricted | 40,337 | Residual Rubber, **Recycled Plastic**, Residual Plastic |
| `exclude_recipes=["Recycled"]` | 33,313 | Residual Rubber, Residual Plastic |

Banning the loop costs **7,024 MW**, which is the useful output: the tool does not argue, it prices the
preference. Note the direction — the loop is 9× more expensive than resin routes *per unit* in
isolation, but once cheap resin is exhausted it beats scaling resin production, so the solver was right
to use it at the margin.

### 8.9 Player position, node overclocking, and spelling

**Player position** comes from the `Char_Player_C` pawn's transform, never from
`BP_PlayerState_C` — that actor sits at the world origin, so reading it would report every player at
(0, 0). With several pawns the one holding a build gun wins, since that is the one being played.
Exposed as `whereami` and as the `near:me,<radius>` selector.

> **The trap, which I walked into.** The player position must be passed to `select_nodes` as `player`,
> *not* as `origin`. `origin` also turns direction selectors into cones, so supplying it silently
> changed `"north"` from *the northern half of the map* into *a 60° cone from wherever the player is
> standing* — quietly altering every plan scoped by direction. Seven unrelated tests caught it.
> `test_supplying_a_player_never_reinterprets_a_direction` pins it.

**`extractor_clocks`** overclocks source nodes only. This is the usual play: a node set is fixed, so
running it faster is the only way to get more from it, whereas overclocking production machines mostly
burns power. On Spire Coast, `[1.0, 1.5, 2.0, 2.5]` takes 43,092 MW to **107,258 MW**.

> **Its trap: clock modes of one node set share physical machines.** Each mode is its own column, so
> capping them individually would let the solver mine every node once *per mode* — four nodes offered at
> two clocks would silently become eight. `Process.group` ties them together under one shared
> constraint. Modes above a building's `max_clock` are dropped rather than invented.

**`power` and `mw` are interchangeable** in objectives (`max_power` = `max_mw`, `min_mw` = `min_power`)
and in exports (`MW`, `mw`, `power`, `Power`). Both words turn up in the same conversation and neither is
more correct. Normalisation happens in `Scenario.__post_init__`, so exactly one spelling reaches the
dispatch — otherwise `max_power` would fall through to the unknown-objective branch. The same resolution
now applies to `export_minimums` keys, where a minimum written `"MW"` never matched the power
pseudo-item and was a floor the LP silently ignored.

**An export token that resolves to no item is refused by name.** It used to pass through as the raw
string, which entered the LP as an item id nothing produces and no balance row can satisfy — a bare
INFEASIBLE with nothing pointing at the typo. This is the same call `recipe_errors` makes for a ban that
matched nothing: a silently mangled export whitelist describes a different factory from the one asked
for, and §8.2 makes `exports` the most load-bearing argument in the model.

> **`exports` REPLACES the default `[MW]`; it does not extend it. Kept, and now documented.** The reason
> is mechanical rather than stylistic: `grid_import_mw` is derived from the export set, because a power
> plant that imports power to export it is unbounded — so exporting MW also forbids drawing from the
> existing grid. Auto-appending MW would therefore silently force *every* item plan to be self-powered,
> which is a different question from the one asked. It cost a real session four INFEASIBLE calls anyway,
> so the tool docstring now spells out all three shapes (`["MW"]`, `["Plastic", "Rubber"]`,
> `["MW", "Plastic", "Rubber"]`) and every refusal quotes `EXPORT_HELP`.

### 8.7 Degeneracy

`min_raw` LPs are **degenerate** — equally optimal vertices give materially different raw vectors (water
−57.78 vs −46.67 on the same objective). Either apply a documented lexicographic tie-break or label
reported raw vectors as one of several optima. Never present a degenerate component as *the* number.

`Scenario.raw_weights` is the mechanism: a per-resource weight in the `min_raw` objective, defaulting to
1.0 and applied to both the raw columns and the extractor columns. A weight of 0 only makes sense as the
first half of a lexicographic pair — minimise the priced resources, then pin them and minimise the free
one, or the free one comes back at its stand-in cap. `bom` uses it for water ([§10.1c](#101c-bom--the-flattened-bill));
`compare_recipe_options` predates it and pins its primary resource by cap instead.

---

## 9. Hard-drive advisor

The save persists the real offers, so the tool advises an actual decision.

`BP_ResearchManager_C.mUnclaimedHardDriveData` → `[{HardDriveID, PendingRewards[], PendingRewardsRerollsExecuted}]`,
quoted from `FGResearchManager.h`. Per `UFGResearchSettings`: 2 schematics per drive, 1 reroll each
(`config`-driven defaults, so a packaged `DefaultGame.ini` could override them — treat as semi-verified).

### 9.1 Ranking method

Centrepiece is **marginal value by counterfactual LP**: solve the objective with the candidate's
recipes enabled vs disabled, report the delta in real units (+MW, −crude m³/min, −machines, −power).
The counterfactual must add **all new recipes of the schematic** (two carry three).

Because a recipe can be worthless for power and excellent elsewhere, evaluate every candidate against a
small standard battery — max net MW from a resource basket; min raw for a fixed target part; min
machines; min power — and **never collapse them into one score without naming the tradeoff**.

Report alongside, explicitly labelled as heuristics not maths: byproducts created/removed, new building
types required (and whether they're unlocked *and built*), water/pipe burden, belt pressure, complexity.

### 9.2 Methodology requirements learned the hard way

- **Deltas ramp; they don't step.** A sweep reporting Turbo Blend Fuel at a flat +641 MW up to a
  "crossover" at 3,280 m³/min water was an artifact of a coarse grid. Real values: +641 at 3,000,
  **+513 at 3,200**, +64 at 3,270, +0.064 at 3,279.99. Sample finely near any trigger, or report the
  crossover as a curve.
- **The baseline must include everything the player has.** A demo concluded "neither turbofuel alternate
  adds a single MW" using a baseline with only the Fuel generator — while admitting raw coal as input and
  ignoring the **32 coal generators the user has already built in the north**. State the baseline
  generator and recipe set explicitly in every response.
- **Price cross-objective costs.** Coated Cable saves 86.67 copper ore/min but costs +50 crude/min, and at
  the model's own crude shadow price of 29.89 MW per m³/min that's 1,494 MW of forgone power. A tool that
  reports only the ore saving is misleading.
- **Charge only genuinely new infrastructure.** One run charged all 3,280 m³/min of water as newly
  extracted, including 400 m³/min from 4 already-built extractors.

### 9.3 Unverified mechanics — must be labelled in output

- **CONFIRMED (player, 2026-07-28).** Picking one option does **not** forfeit the other: the unchosen
  schematic **returns to the pool** and a later drive can offer it again. Only the drive is spent. This was
  the inference the headers supported -- the doc comment on `GetAvailableAlternateSchematics` excludes
  "unclaimed hard drive rewards" from candidates, so return-to-pool follows *if* `ClaimHardDrive` removes
  the entry -- and it is now demonstrated in game rather than merely plausible.
  **It inverts the advice.** The module had described this as "a one-off irreversible choice", which argues
  for agonising over each pick and hoarding drives against a better roll. The opposite is correct: take
  whatever helps the factory you have now, because the other alternate comes back around. Both hard-drive
  tools now say so on every response, since the rule appears nowhere in the game's own UI.
- **Reroll has a documented fallback**: *"If the required number isn't met, we fall back to
  excludedSchematics to fill up the list"* — so rerolling with a thin pool can re-serve excluded
  schematics rather than failing. Advice like "rerolling is pointless" is wrong.
- The rollable pool is **not static**, since unchosen options stop being reserved once a drive is claimed.

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
`mode` instead of splitting ([§7.2a](#72a-node-lookup--one-tool-three-modes)): the body is ~90 % shared —
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
7. Scoped aggregates before rows ([§7.3](#73-query-semantics)).
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
  byproduct table in [§8.2](#82-the-byproduct-rule--the-crux) as a regression fixture.
- Mark real-file tests `@pytest.mark.integration`, skipped when `SATISFACTORY_DOCS` is unset, so CI is
  green with no game install.

Deps: `mcp[cli]>=1.28`, `pydantic>=2.13`, `platformdirs`, `scipy>=1.11`, `numpy`; dev `pytest`,
`pytest-cov`, `ruff`. `requires-python = ">=3.11"`.

---

## 13. Licence

**None.** Not published, so no `LICENSE` file and all rights reserved by default. GPL copyleft attaches
only on distribution (GPLv3 permits private use and modification without conveying), so vendoring
`sav_parse` carries no obligation here.

If that ever changes: `sav_parse.py` is **GPL-3.0-only** (no "or later") and has no packaging metadata, so
it can't be pip-installed — it must be vendored, which on distribution would make the combined work
GPL-3.0-only and incompatible with Apache-2.0 code. The subprocess boundary in
[§4.1](#41-the-save-seam) already provides the arm's-length separation that would keep the server's own
code freely licensable. Separately, `resourcePurity.py` data originates from SCIM, a third party — its
terms, not GreyHak's, would govern.

---

### 6.9 Capability gates, and absence as evidence

`plan_factory(sloops=N)` spends Somersloops, and a Somersloop cannot enter a machine until
**Production Amplifier** is researched in the MAM. Planning against it while locked prints
a plan that cannot be built, so the gate has to be readable.

**The first answer here was wrong, and the mistake is the useful part.** Probing a save
taken before the research found no key containing "Boost", "Amplif" or "Sloop" anywhere in
its 44,307 objects, and this section originally concluded that the game records no flag.
It does: `BP_UnlockSubsystem_C.mIsBuildingProductionBoostUnlocked` appears the moment the
research completes. UE omits a SaveGame property still at its default, so **absent means
false** — the same rule §6 already states for empty TArrays, applied to a bool. *"Not in
this file"* and *"no such field"* are different claims and only the first was evidence.
The fix was to research it and look again; the reference save now carries the flag, and
schema 10 extracts it.

So the flag is authoritative when present, with a `CAPABILITY_SCHEMATICS` register
(capability → gating schematic) as fallback. That fallback is not redundant: a projection
written before schema 10 looks exactly like a world that never did the research. The
register also answers the other half — *which research to do and what it costs* — from
Docs.json and `stock()`.

**The same absence fooled the sloop budget, in the same direction.** `sloop_budget` claimed
committed Somersloops were unreadable. They are not: they sit in `InventoryPotential`, the
*same component* as Power Shards, which the sidecar had been reading into `potential_slots`
all along. `mArbitrarySlotSizes` shows the shape — `[1, 1, 1, 2]` on an Assembler is three
shard slots plus one somersloop slot holding two. The count is now exact, and
`mPendingProductionBoost` gives an independent cross-check: **1.5 on an Assembler with one
of two slots filled, exactly `boost_for(1)`**. Reading the count from the slot is still
right and the multiplier is the worse source, since inverting it needs the building's base
and step and rounds.

On the reference save this closes a real gap: production boost was **not researched**, so
every sloop plan produced so far was unbuildable and nothing said so. `plan_factory` now
says it, with the bill:

```
! sloops=16 but PRODUCTION AMPLIFIER IS NOT RESEARCHED, so no somersloop can go in a
  machine yet and this plan is not buildable as printed. Research Production Amplifier in
  the MAM (1 Somersloop, 100 SAM Fluctuator, 50 Circuit Board) -- you can afford that now.
```

It **warns rather than refuses**, because planning ahead of cheap research is legitimate —
the same reasoning that makes `must build first:` a note and not an error. It fires only
when `sloops > 0`, since a plan spending none is buildable today and a standing warning
would be noise.

`mam_research` exposes the whole tree: status (DONE / READY / short / BLOCKED), cost,
what you are short of, prerequisites, and a `LOCKS <capability>` marker on the rows that
gate a feature rather than merely adding a recipe. Costs are checked against spendable
stock only — carried, crates and the Depot — never machine buffers, per § 6.

### 6.10 Pushing back on "not modelled"

Four claims of unknowability were audited after three of them turned out false in one
session (§6.9, §8.5c). The pattern each time: a failed search reported as a missing field.

**"Fuel supply and uptime are not modelled here" — false, and expensive.** `power_report`
returned pure nameplate while the 300 s productivity monitor sat in the projection on
**520 of 566 records**. Weighting each machine by it:

| | nameplate | measured |
|---|---|---|
| draw | 6,839 MW | **1,516 MW** |
| headroom | 711 MW | **6,034 MW** |

An 8.5× error in the number `commission_plan` sizes a startup against. Both are now
reported, because both are true and they answer different questions: nameplate is what
everything built would draw *running at once* — the safe bound, since energising a block
can un-starve idle machines and the fuse blows on demand rather than on averages —
while measured is what is free *now*. Commissioning still defaults to nameplate and names
the other, because being wrong in that direction trips a grid.

Two rules keep it honest. A machine with **no monitor is charged in full** on both figures:
unknown utilisation must not read as idle. And **generation is capacity on both**, because
generators burn to meet demand rather than at a rate of their own — weighting them would
double-count the idleness already seen on the draw side.

**OQ5's "water pumps carry no geometry" — a third right, and the conclusion wrong.** The
volume's *shape* is level geometry and genuinely absent from the save, so how many pumps a
body of water holds stays unknowable. But its *identity* is in every pump's
`mExtractableResource`, which the sidecar had been storing in `node` all along, and it
groups this save's 23 pumps into **three distinct bodies (13 / 6 / 4)**. Sea level falls
out of the same rows: every pump at **−17.4 m, spread 0.24 m**, which turns "water must be
drawn at sea level" from a rule of thumb into a number deck ordering can be checked
against.

**Still genuinely unknown**, and left alone: terrain (there is no heightmap in any input,
which is why layout draws no coordinates and trunk runs are lower bounds), water-volume
capacity, and belt/pipe length without a route.

### 6.11 The removed-actor list — the only record of what was collected

**The world is not saved.** Every power slug, mushroom, Mercer sphere, somersloop, shrine,
crashed drop pod and rock is placed by the map, and a `.sav` never mentions the ones still
standing. What it records is the **negative**: which map-placed actors are *gone*. So there is
no field anywhere that says how many slugs exist, and the only way to answer *"how many have I
picked up"* or *"which crash sites have I looted"* is to count what has been removed. A count
here **is** a collected count — not a proxy for one.

That list was the last region of the body still stepped over rather than read. Schema **11**
reads it and adds the projection's twentieth key, `removed`:

```json
"removed": {
  "cells":     ["0O2UIH8ZOBYWRN8PY7727SVBT", ...],   // interned partition cells
  "instances": [[cell_ix, "BP_Crystal_mk3_C_2146"], ...],
  "counts":    {"BP_Crystal_mk3": n, ...}            // approximate class -> count
}
```

Both engines produce it — `pioneersav` merges the three lists into `destroyed_actors`, the vendored
parser exposes them separately — and the projection **sorts before emitting**, so the two are
byte-identical rather than merely equivalent. The order in the file is an artefact of which list
a parser walks first and means nothing.

The save keeps **three** such lists — one trailing each level's header block, one in each
sub-level's trailer, one closing the body — and they must be **merged and deduplicated**, which
is measured rather than assumed: the header-block list alone falls 870 → 836 across a game
update while the merged union never falls, and the trailer list carries internal duplicates on 6
of the 31 saves that the vendored parser reproduces exactly. Format, overlap predicates and the
oracle parity are in `docs/savparse-notes.md`.

`WorldState.removed_actors(group=None)` groups by class-name prefix and `collected_from_world`
prints it. On the reference save, **889 actors over 284 cells**: flora 185, dropped_pickup 170,
slug_blue 163, mercer_shrine 80, artifact_unsplit 65, crash_site 55, debris 51, slug_yellow 50,
slug_purple 37, mercer_sphere 27, somersloop 6. Both the census and the per-group listing are
built from the instance names, so they agree group for group -- they did not always, and
`test_the_census_and_the_listing_agree_for_every_group` is why they cannot drift apart again.
Over all 31 saves the series and the union are **non-decreasing**, and the actor set is strictly
nested — each save is a superset of every earlier one — which is what a
"collected" reading predicts and a "currently despawned" reading would not.

**Two limits ride on every answer.** The first is printed on every response: these are *absolute
counts, not fractions*, because the denominator would need the map's own table of where every slug
is, which this project does not ship — and the obvious source for it is GPL-3.0 build-time data,
which makes it a licence decision rather than a feature (see `docs/savparse-notes.md`,
*Opportunities*). The tool also prints that `dropped_pickup` is loot the player dropped and
re-collected rather than a map collectible, so its 170 says nothing about the world.

The second limit is **not** printed and belongs here: the refs carry **no class path**, only an
instance name, of which only 32% spell the class out with `_C`; the rest have an instance number
glued straight onto the class with no separator. Harmless for slugs
(`BP_Crystal_mk21_23` still shows its `mk2`) and lossy for artifacts, where `BP_WAT1`
(somersloop) and `BP_WAT2` (Mercer sphere) differ in **exactly the glued digit** and `BP_WAT60`
fits neither — which is why 65 of the reference save's 98 artifacts are reported as
`artifact_unsplit` rather than guessed at. That bucket demonstrably holds somersloops: only 6
names spell `BP_WAT1_C` unambiguously, while the player holds **11 somersloops in the Dimensional
Depot plus 4 slotted in machines** — so at least nine of the unsplit names are sloops, on the one
game-behaviour premise that a somersloop is only ever picked up off the map.

One inconsistency in `removed_actors()` is known and stated rather than hidden: `groups` is built
from `counts`, whose keys have already lost their `_C`, so the two `strict` groups can never
match there and `groups["somersloop"]` / `groups["mercer_sphere"]` are absent on **31 of 31
saves** even though `removed_actors("mercer_sphere")["actors"]` returns 27 entries. 65 + 6 + 27 =
98, so nothing is lost, but the census and the listing disagree about whether the split exists.
Details in `docs/savparse-notes.md`.

## 13a. Replacing the vendored parser

The save parser WAS vendored GPL-3.0, which reached the whole project. It is now deleted and
`src/pioneersav` is the only parser; this section records how that was done and what the
agreement between the two measured, because the diff cannot be re-run. Replacing it started
with knowing what was actually used, and the answer was small: **three entry points**.

| what | used by | replaces |
|---|---|---|
| `readSaveFileInfo(path)` | `header_info` — 9 fields | ✅ `pioneersav.read_info` |
| `readFullSaveFile(path)` | `iter_objects` — levels, headers, objects, properties, destroyed actors | ✅ `pioneersav.read_full_save` — all 20 projection fields exact on all 31 readable saves |
| `ParseError` | one `except` | ✅ `pioneersav.ParseError` |

All three are **wired in and selectable**: `SATISFACTORY_SAVPARSE=own|vendor`, resolved at
import, **defaulting to `vendor`**. Nothing changes for users yet. What the switch buys is
that "the two parsers agree" is a diff someone can run rather than a claim — see *Both ways,
diffed* below.

Its 6,800 lines of `sav_data/` tables are **build-time only** — `tools/gen_*.py` uses them to
produce committed artifacts. Different question, different answer — and one that got harder,
not easier, when the destroyed-actor lists were read: four of those tables (`slug.py`,
`somersloop.py`, `mercerSphere.py`, `crashSites.py`) are location lists for exactly the
collectibles §6.11 can now count, so there is a real feature arguing for GPL data at the moment
the rest of it is being removed. That is the user's call and is written up as one in
`docs/savparse-notes.md` rather than assumed either way.

### What "cleanroom" can and cannot mean here

A strict cleanroom needs an implementer who has never seen the original, and the library is
vendored in this repo. What is done instead is a reimplementation **of the file format** —
a fact about what the game writes, not a creative work — verified black-box: same file in,
same values out. That is the ordinary interoperability route and it is written down here
rather than implied, because the distinction matters and is not mine to certify.

### The header, done

Field order was derived by walking the bytes and checking values against what the game
shows. It is a linear sequence with no offsets to seek by, so `save_header_type` is the
only thing that would announce a change.

The load-bearing detail is the string encoding: an int32 length then bytes, where **the
sign of the length is the encoding** — positive is one byte per character, negative is
UTF-16LE at two — and the count includes the terminator either way.

**Verification, across 67 saves on disk:** exact agreement on all **31** the old parser can
read, and failure on exactly the same **36** — pre-1.0 saves (`saveVersion` 52 and older)
that it also refuses. Same answers, same limits.

The header ends by asserting Unreal's `PACKAGE_FILE_TAG` (`0x9E2A83C1`) is the next four
bytes. Every field is positional, so one wrong width silently rereads the rest and returns
plausible nonsense; landing on the tag is the proof it did not. A patch that inserts a
field is caught there and the error names the versions it was reading.

Tests run against a committed 2 KiB fixture, so they survive the vendored library's removal
and pass on a machine with no game install.

### The compressed body, done

Everything after the header is a run of independently zlib-compressed blocks, each with a
**49-byte preamble**: tag, max chunk size, a one-byte compressor id, then the compressed
and uncompressed sizes **written twice, identically**. The duplication is real on every
chunk of every save checked, and both copies are read and compared — it is a free
integrity check on a format that has no other one.

Verified on all 31 readable saves: **1,194 MB of body inflated in 1.39 s**, none failed. The
reference save is 2.94 MB on disk and 44.4 MB inflated, in 0.047 s. All **9,125** chunks of all
31 saves declare a max chunk size of 131072 with both of their sizes inside it — which is why
that field is checked as a self-contradiction rather than against the constant, since the block
size is the writer's choice and not the format's.

The tag is checked **per chunk**, not once. This project reads autosaves, which are
rewritten every few minutes, so a file torn mid-write is routine — it now fails on the
chunk where the tear is, with the offset, instead of inflating garbage into the object walk
and failing somewhere unrelated.

### The two layers that carry the complexity

The body walk and the tagged property serialiser are 1,907 of the package's 2,969 lines
and all of the format that could not be read off a hex dump. They have a section of their
own, [§13b](#13b-the-object-walk-and-the-property-serialiser), because the argument that
they are *right* is a different kind of argument from the one above: the header and the
chunk stream are proved by a tag landing where it must, and these two are proved by a
length check balancing 2,269,824 times.

### Both ways, diffed

The composition is one module, `pioneersav/save.py`, and it holds two decisions rather than
glue. **The inflated body is retained** — every object slice and every `extra_offset` is an
absolute index into it, nothing is copied, and that is what keeps a 44 MB body at a fifth of
a second. And **there is deliberately no `actorSpecificInfo` attribute**, because
`_lightweight` and `_structures` read it through `getattr(..., None)`: its absence is what
makes them return empty, which is a *visible* gap. A partial decode there would turn an
undercount into a silent one, and a foundation census reporting 40 slabs where 8,347 pieces
are built reads exactly like a real answer.

Getting there also collapsed two exception types into one. `ParseError` moved below `Reader`,
because the commonest failure of all — a walk running off the end of a file the game is
rewriting — was raised by the primitive layer as a bare `ValueError`, fell through the
sidecar's single `except`, and was reported as `{"error": "ValueError"}` with no offset for a
file that was merely mid-write.

**The result, over all 67 files in the save folder, JSON compared leaf by leaf:**

* **31 readable and 36 refused, by both, the same 36**, each reported as `parse_error` on both
  sides. (One of the 36 is not a save at all: `ServerManager_V2.sav` opens `MSGF`.)
* **All 20 projection keys are identical on all 31 saves**, and identical means leaf by leaf,
  not key by key: **zero leaves anywhere where the two disagree on a value, and none present
  under one parser and absent under the other.** The last two to fall were `lightweight_counts`
  and `structures` — **224,530 structure instances in 488 classes**, which came out empty until
  the lightweight blob was decoded. The twentieth key, `removed` (§6.11), was identical on both
  engines from its first run: they reach the same three destroyed-actor lists under different
  attribute names, and the projection sorts before emitting so the byte order cannot differ.
* `n_objects` agrees on every save, 29,734 to 44,643. `Han Solo_270726-215626`: **44,307**
  objects, **566** machine/extractor/generator records, **11,554** material edges, 8,347
  structure instances, `schema_version` **11** — both parsers.
* `--list` over the folder: same 31/36 split, and **zero differences in any header field**.
* Whole sidecar including interpreter start, summed over the 31 readable saves: **vendor
  75.7 s, own 59.2 s** — 1.28×. The per-layer split is in
  [§13b](#13b-the-object-walk-and-the-property-serialiser); the short version is that
  inflation is 2% of a parse and the property bodies are 87%.

Every figure above was re-measured on the finished tree rather than carried forward. That
matters here for a specific reason: the package was edited by several agents in parallel, so
numbers taken mid-way describe code that no longer exists, and a parity claim about code that
no longer exists is not a parity claim.

### Torn files, fuzzed

Autosaves rewrite the save in place every few minutes, so a file read mid-write is routine.
About **60,000 mutations** of two real saves, each classified by outcome rather than eyeballed:
1,750 truncation points, 600 single-bit flips in the chunk stream, every byte of three chunk
preambles, every length field the body walk reads in both committed fixtures, ~30,000 mutations
inside real property blocks, and every int32 of two real headers. **Nothing hung** once the
container-count bound in [§13b](#13b-the-object-walk-and-the-property-serialiser) was in place —
no case took longer than one clean parse — and, after the four fixes below, nothing raises
anything but `ParseError`.

Two results worth keeping. **The realistic tear is caught by one int64**: a file half-rewritten
is a prefix of the new chunk stream followed by a suffix of the old, every chunk of it
individually valid — right tag, matching size copies, clean adler32 — and the only thing
between this project and a confidently-reported chimera of two factories is the body's own
`len(body) - 8`. Spliced at 14 chunk boundaries between two real autosaves, refused every time.
And **everything the property serialiser reads sits behind a per-chunk adler32**, so silently
corrupting the inflated body takes a deliberate re-compression rather than a tear: of 600 bit
flips, 599 were refused and the survivor landed in a deflate block's unused padding bits and
inflated to byte-identical output.

Four defects, now fixed with tests in `tests/test_savparse_robustness.py`:

* **Nested `StructProperty` tags raised `RecursionError`, not `ParseError`.** 29 KB of crafted
  payload was enough; through the sidecar it came out as `{"error": "RecursionError"}` with a
  traceback and no offset — the report `errors.py` exists to abolish, arriving through the one
  door it did not cover. Property lists now count their own depth against 32; the deepest real
  one is 4.
* **A property list terminating early was absorbed in silence.** Every property is size-checked
  but nothing looked at the payload as a whole, so the bytes after the `"None"` terminator
  absorbed anything: overwriting one property's name with the terminator made a
  `Build_ConstructorMk1` read as 2 properties instead of 13 and the sidecar emit **exit 0 and a
  complete projection** — 438 machines, one of them with no recipe and no inventories. Now
  bounded by what 1,243,288 objects actually do: no trailer is shorter than 4 bytes, and a
  *component's* is exactly 8 on 562,556 of them and 4 on the other 5,300, never anything else.
  Injecting the terminator into every component of two real saves is refused 104 times of 104
  and was silent 104 of 104 before. An **actor's** stays unchecked, because an actor may
  legitimately carry any amount and bounding it needs the eight-class whitelist that belongs to
  whoever decodes those bytes — refusing a whole save because a patch taught a ninth class to
  carry data is a worse failure than the one prevented. That is the one stated gap here, and it
  is defence in depth rather than urgent: every byte involved sits behind a per-chunk adler32,
  so only a parser misread or a deliberate forgery can produce it, never a tear.
* **The chunk preamble's `max chunk size` was read and discarded** — 99 of 588 preamble
  mutations undetected, all of them its eight bytes. Checked now as a contradiction (a maximum
  below the sizes beside it), not as the constant 131072, which is the writer's choice.
* **The `PACKAGE_FILE_TAG` proof was skipped when the file ended at the header**, so a save cut
  to exactly its 453 header bytes returned a full `SaveInfo` and failed two layers down at "an
  offset into a body that does not exist".

### Opportunities this turned up

* **Header reads need no subprocess and no decompression.** `read_info` takes a 64 KiB
  prefix. Save discovery and world grouping — which today spawn the sidecar — could run
  in-process.
* **`saveDataHash` is in the header**: two int64s, read without inflating anything. Cache
  validity currently keys on `mtime_ns`, so a rewritten-but-identical autosave invalidates.
  A content hash would not.
* **The grid table is a cell→size map of the whole world** that nothing reads. Seven grids on
  the reference save, and only three hold cells: `MainGrid` at 12800 uu with **1,288** cells,
  `ExplorationGrid` at 20480 with 758, `ExplorationGridFar` at 20480 with 43;
  `LandscapeGrid` (51200), `FoliageGrid` (26500) and `HLOD0_256m_1023m` (25600) are declared
  and empty. `cell_size` is in CENTIMETRES, so `MainGrid` cells are **128 m**, not 12.8 km
  worth something to spatial work.

### The lightweight buildables — where every foundation lives

Foundations, walls, ramps and catwalks are not actors. One `FGLightweightBuildableSubsystem`
carries all of them in the class-specific bytes trailing its empty property list, and
`pioneersav/lightweight.py` (185 lines) decodes it: **224,530 instances in 488 classes** across
the 31 readable saves, 8,347 on the reference save alone. This is the whole of `structures` and
`lightweight_counts`, and it was the last projection field needing the vendored parser.

The record is 162 bytes plus two length-prefixed reference paths — a rotation quaternion, a
position, a scale, seven references of which only the paint swatch and the recipe are ever
populated, two override colours, and three small trailing fields. Its length was derived from
the **stride between repetitions of the recipe path** (a constant 375 bytes over 4,616
consecutive foundations) and its field boundaries from a **per-byte variability map** over those
records: the two colour alphas are the only non-zero floats in the region, which places both
`FLinearColor`s exactly. Full layout and the honest account of what the method cannot settle —
how the always-zero runs are grouped — is in `docs/savparse-notes.md`.

**Two blob versions, and the trap.** saveVersion 52 writes version 2, saveVersion 60 writes
version 4, differing by exactly a trailing `(uint8, int32)`: 370 bytes per foundation against
375. Version 2 is **25 of the 31 readable saves** — the common case, not the legacy one. The
first pass knew only version 4 and *refused* those 25 rather than guessing, and the whole-folder
diff surfaced it immediately as 25 files where the parsers disagreed about readability. Reading
a 370-byte record as 375 would instead have desynchronised thousands of records later, which is
the argument for refusing an unknown version everywhere in this parser.

### What is left, and the verdict

**Nothing is left, and that is now a claim about bytes and not only about classes.** All eight
classes that write trailing bytes are decoded — `pioneersav/lightweight.py` for the foundations,
`pioneersav/trailers.py` (192 lines) for the conveyor chains and their three `RepSize` variants,
power lines, and the circuit and player-state subsystems. Across the 31 saves that is **88,097
records, every one consuming its declared bytes exactly**: 224,530 foundations, 688,282 items
riding on belts, 225,686 belt spline points, 36,773 power lines, and one circuit list and account
id per save.

And the last region that was *stepped over* rather than read — the destroyed-actor list at the
tail of every level's table of contents — is read too, which takes the byte budget from 99.8% to
**100%**: 97,250 of the reference save's 44,376,211 body bytes, 0.22%, and 2,414,398 bytes over
the folder. What that list turns out to be is not a formality; it is the only record of what the
player has collected, and it has a section of its own at §6.11.

Decoding is **lazy**, which is measured rather than stylistic: reading every chain costs 0.46 s on
top of a 2.10 s parse — 22% — which no projection field touched at the time and which schema 12's
`belts` key now pays deliberately, so `actorSpecificInfo` decodes on first access and caches. `_attach_trailer` runs for actors only; calling it on all 1.24 M
objects cost another 5%.

Knowing all eight closed the hole this section used to end on: **an actor's trailer is now
length-checked** the way a component's always was. An actor that is neither one of the eight nor
leaving a plain 4 or 8 bytes warns with its class and byte count — which is what a property list
that stopped early looks like, and it was previously silent. Measured before it was written: over
the 31 saves, 500,350 actors leave 4 bytes, 87,016 leave 8, 88,066 are one of the eight classes,
and nothing is left over.

What the new data is worth: not throughput, which the planner derives from recipes and clocks, but
**where each item physically sits** — a backed-up line becomes directly visible instead of inferred
from machine state as `factory_health` does now — and **the actual routed path of every belt**,
225,686 spline points that nothing in this project has had before.

**Is the own parser ready to be the default? Yes on the evidence, and the decision is still the
user's.** The acceptance test set out here is met: **20 of 20 projection keys leaf-identical on
all 31 readable saves**, no key present under one parser and missing under the other, the same 36
files refused for the same reasons, `n_objects` equal everywhere, 1.16× faster end to end
(vendor 78.2 s, own 67.5 s of sidecar wall over the folder), and 918 tests passing under both
engines. No input has been found — real, torn, fuzzed or synthesized — on which it returns a
plausible-but-different factory.

`vendor` stays the default regardless, and `test_the_sidecar_still_defaults_to_the_vendored_parser`
fails if that moves. **The flip buys nothing by itself:** the licence exposure comes from the
library being in the repository, not from which branch of an `if` executes at runtime. The
decision that matters is the deletion, and the flip belongs to it.

**To delete `sidecar/vendor/`:** flip the default and the tests that pin it; re-diff *after* the
flip, over the whole folder rather than a sample, because the same measurement is the acceptance
test — and bank the vendored parser's projection for every save first, since after the deletion
that comparison can never be run again; strip the last references outside `sidecar/vendor/`
(`extract_save.py`'s switch, `tests/test_savparse_save.py`'s default pin, and prose in
`README.md`, this file and `docs/savparse-notes.md`); and settle `sav_data/`'s licence
separately, since it is build-time input to `tools/gen_*.py` and not on this path — noting that
its four collectible location tables would now buy a real feature, which makes that the harder
half rather than the leftover half. The deletion itself is the user's call and nothing here
should make it for them.

What stays genuinely unknown is in `docs/savparse-notes.md`, and the conveyor chain is no longer
most of it: an adversarial pass over all 31 saves explained the per-segment float, derived the item
ring's capacity from the geometry (`floor(length/120) + 2*segmentCount + 1`, on 51,200 of 51,200),
identified the two remaining ints as ring indices with `-1` as their sentinel, and established that
offsets increase *along* the direction of travel — the reverse of what this file previously said —
leaving only the item's `state` int32 unexplained. What remains is the player state's two leading
bytes, two fields in the lightweight blob, and two questions about the destroyed-actor lists that
no save on this disk can settle: which of the three lists a given actor lands in, and which class
an instance name like `BP_WAT112` belongs to.

## 13b. The object walk and the property serialiser

The two layers `pioneersav` spends its lines on: `objects.py` (747 lines) walks the inflated
body into levels, object headers, one property-block slice per object and the three
destroyed-actor lists, and `properties.py` (1,160) turns a slice into the `[name, value]` pairs
the projection reads. Together they are 64% of the package and all of the format that could not
be read off a hex dump.

They belong in one section because they share one safety argument, and it is not the one the
header and the chunk stream use. Those two are proved by a constant landing where it must —
`PACKAGE_FILE_TAG`, the chunk tag, two copies of a size. These two are proved by
**self-describing lengths agreeing with each other**: every level, every block, every
property and every container declares its own byte count, and the reader is required to land
exactly on each declared end. Nothing is recognised by pattern-matching and nothing is
searched for. That is why an unknown property type costs one property rather than the file,
and it is why the reader can be confident about bytes it does not understand.

### The body, walked by its own lengths

The body opens with an int64 that equals `len(body) - 8`, then a world-partition grid table,
then one record per level. Each record holds a **header block and a property-blob block
separately** — parallel lists, not interleaved — so an object's header and its properties are
matched by index, which is a fact about the format rather than a convenience. Each block is
length-prefixed; each object entry inside the second block declares its own size; each
property inside that declares its own size again. Four levels of nesting, four independent
lengths, and the walk cross-checks them against each other.

Two facts cost the most to establish. **There are two body layouts:** saveVersion 60 opens
with a 59-byte archive version header and a count-terminated array of `(GUID, int32)` custom
versions (13 of them), and saveVersion 52 — **25 of the 31 readable saves** — goes straight to
the grid table and has no per-level archive headers either. And **object save versions are per
object**: 36, 52 and 60 all occur in one file, because an untouched world-partition cell keeps
the bytes it was written with, and a version-60 entry carries an extra int32 that its
predecessors do not.

Where that int32 sits could not be settled by arithmetic. As a fourth header field or as a
trailer it makes an entry `16 + size` bytes either way, and both readings balance every length
in the file. It took parsing 18,369 version-60 actors' reference lists and asking where the
first property name begins. That is the character of this layer: the lengths have several
self-consistent readings and only the payload's own content picks one.

**The object header identified its own flags word.** Both kinds open with an int32 (1 for an
actor, 0 for a component), three strings, and an unexplained uint32 — which takes **eight**
distinct values over 1,243,288 objects, not the two this file used to claim; `0x280008` and
`0x2C0008` cover only about half of each kind, and differ by `0x40000`,
`RF_DefaultSubObject`. That is UE's `EObjectFlags`, and knowing it is what confirmed the two
header shapes were being told apart correctly rather than coincidentally. Actors then carry
a transform (quaternion, position, scale as `x y z w` / three / three floats) and components a
parent actor name.

One deliberate naming choice: the bytes **do** name a component's class, and it is exposed as
`class_path` and not `typePath`, because `iter_objects` reads `getattr(header, "typePath", "")`
and the projection's class-based branching depends on a component resolving to `""`. Exposing
it under the obvious name would have silently changed which objects every downstream census
counts.

Two claims from the earlier working notes were **wrong and are corrected**: there is no 774 KB
partition table — that figure measured to the first literal `Persistent_Level` in the bytes;
the grid table is ~71 KB and ends at body offset 71578 — and the reference save has **44,634**
objects, not 44,307, which is a different autosave of the same world.

**Measured, over every readable save:** 31 bodies, **86,403 levels and 1,243,288 objects walked
in 7.77 s**, zero warnings, and **zero unparsed bytes** — and nothing stepped over either. The
reference save's 44 MB body walks in **0.231 s**.

The last thing that *was* stepped over is the destroyed-actor list at the tail of each level's
table of contents, and it is now read: **79,363 lists across the 31 saves, 21,038 references,
2,414,398 bytes** (97,250 on the reference save, 0.22% of its body). Two shapes — a bare
`[i32 count][refs]` on a sub-level and one grouped by partition cell on the persistent level —
and nothing in the file announces which, so the reading is proved by **landing**: after the list
the cursor must sit exactly on the header block's declared end, and it does **79,363 times out of
79,363, with 0 mislandings**. Two independent lengths agreeing, the block's and the list's own
counts, is the same argument the rest of this layer runs on. It costs **1–3 ms per save, under
1.1% of the walk and ~0.08% of a parse**, measured by interleaved A/B against a version that
jumps to the block's end. What the list means, and why it is the only record of what the player
has collected, is §6.11; the format, the overlap predicates and the longitudinal check are in
`docs/savparse-notes.md`.

`SaveBody.skipped_toc_bytes` still reports those byte counts. Its name is now historical: it is a
number a caller can see is nonzero, not a confession.

**Verified black-box on all 31 readable saves:** identical level counts, identical per-level
header *and* object counts, identical `typePath` multisets. Object by object on the reference
save — 23,821 actors and 20,813 components over 3,124 levels, the actors covering 184 distinct
class paths — `instanceName`, the actor/component split and `position` agree, **0 differences in
44,634**. The one divergence anywhere is the persistent level's name, `None` in the vendored
parser and `"Persistent_Level"` here; nothing reads level names.

### The property serialiser

Every property announces its own length before its bytes, and that one field is what makes
this layer tractable: an unrecognised type costs that property and nothing else. Two tag
layouts occur, keyed by the *object's* version — UE5 writes a **type-name tree**
(`ArrayProperty(StructProperty(InventoryStack(/Script/FactoryGame)))`, each node a name plus a
parameter count), UE4 writes the same information as fixed tag-data fields. Both are
normalised into one tree, so there is one value reader per type instead of two.

**The flags byte on a version-60 tag is the most useful field in the format**, and it
identified itself from data. `0x10` is set on a `BoolProperty` whose declared size is 0 and
which therefore has nowhere else to keep its value — so that bit *is* the bool, and it is
exactly the "16 means True" that `extract_save.truthy` has always documented without knowing
why. `0x08` is set on precisely the structs whose payload is raw numbers (`Box`, `Vector`,
`Guid`, `InventoryItem`) and clear on the ones written as nested property lists
(`InventoryStack`, `FeetOffset`, `FactoryCustomizationData`).

**The per-property size check is the whole safety argument.** After reading a value the cursor
must be exactly `size` past the payload start. That found every format detail worth recording
here, including two that were expensive: the version-60 terminator is a **bare name with no
type after it** (reading a type tree first turned the four trailing bytes into a string length
and broke 16,445 objects), and `InventoryItem` writes one extra int32 on object version **36
only, not 52** — the single place those two versions disagree, worth 87 pickups.

**Struct bodies are decided by name, not by the flag.** The flag is not reliably per-element:
the foliage subsystem's `mSaveData` is one MapProperty with `0x08` set whose keys are native
`IntVector` and whose values are property lists. Nine native structs cover every save —
`Vector` (three **doubles**, on version-52 objects too, because the width follows the writer and
every readable save is UE5-written), `Quat`, `Box`, `LinearColor`, `Guid`, `IntVector`,
`FluidBox`, `ClientIdentityInfo`, `InventoryItem`. `PlayerInfoHandle` and `UniqueNetIdRepl` are
kept as raw bytes on purpose, so that a genuinely new struct shows up as a warning instead of
hiding among them, and the other 36 struct names are nested property lists.

**What the layer actually faces**, on the reference save: 82,660 top-level properties declaring
**208,548 tags at every depth** (a struct's fields and a container's elements are tags too) in
**19 distinct types**, and **47 distinct struct names** — 9 native, 2 opaque, 36 nested lists.
The top-level distribution is led by `ObjectProperty` 34,139, `StructProperty` 22,301,
`ArrayProperty` 18,260 and `IntProperty` 17,091; `DoubleProperty` occurs only nested, never at
the top. Eight types occur fewer than 30 times in the whole save, which is the argument for
deriving from a census rather than from the first object that parses.

**One honest limit.** UE4's tag data for a map or a set names the element's *property* type and
not the struct behind it, so a struct element on a version-36/52 object is genuinely ambiguous.
Reading it as a property list and keeping the result **only when it lands exactly on the
declared end byte** recovers `mItemsPickedUp`, `mActorsBuiltCount` and
`mItemsManuallyCraftedCount`, and correctly refuses `mSaveData` and two `Guid` sets, which are
skipped by declared size. That is three warnings per saveVersion 52 save and zero on a
saveVersion 60 one — **75 over the whole folder**, in data nothing above reads.

**The escape hatch had a hole, and it was where two things could not be told apart: an
element's length and its container's.** An untagged map element with no reader was skipped to
the *map's* declared end. With pairs still to read, the next key then came out of the bytes
after the map and the next skip pulled the cursor back onto that end — so the size check
balanced and the object parsed, with fabricated keys and `None` values and no error at all.
`FText` inside an array had the same shape. Skipping *forwards* to a declared end is honest and
skipping backwards never is, so that is now the rule, and the last element of a container —
where the container's remaining length really is the element's — still recovers. In the same
family: a container's element count is bounded by the bytes left in its block rather than by a
flat ten million, because the count lives *inside* the payload where the tag's size check
cannot reach it, and a planted count of 9,000,000 read 36 MB of the following objects and took
13.4 seconds to fail. None of the three changed a value: the same 2,269,824 properties, the
same digest, before and after.

### Parity, property by property

**Verified black-box on all 31 readable saves, both parsers in one process:** **1,243,288
objects, 2,269,824 properties, zero objects whose property names or order differ.** Values are
identical on 2,168,837. The 100,987 that differ all fall in one of five classes with a stated
cause — 81,358 in the unread second element of an inventory `Item`, 19,492 in a plain byte's
enum name (`None` against the literal string UE4 writes), 75 skipped and warned about (the same
75 as the warnings, one for one), 31 in a `Guid` spelling and 31 in an empty soft-object
sub-path. Nothing is unexplained, nothing is in a field the projection reads, and the table is
in `docs/savparse-notes.md`.

**Two shape differences have to be normalised before any of that can be compared**, and both
are the ones `extract_save.struct_fields` already documents: a struct value is
`[values, propertyTypes]` here and sometimes bare `values` in the vendored parser, and a
`propertyTypes` entry's tail past `(fieldName, typeName)` is a rendering choice. So the
comparison drops types from the value tree and then compares the `(fieldName, typeName)`
sequence **separately** — 123 properties of 2,269,824 differ there, and **0** where a field both
parsers name is given a different type. Stripping something and not checking it is how a
cosmetic difference hides a real one, and both directions of that mistake happened here: a
first comparison called all 21,247 struct-bearing properties mismatched, and a second, which
compared only the top-level value, buried 2,904 real `Item` differences as "unexplained"
because the disagreement lives three lists deep at `.0.0.1.1`.

### What it costs

One fresh process, reference save (2.94 MB on disk, 44.4 MB inflated, 44,634 objects):

| stage | time | share |
|---|---|---|
| read + header | 0.001 s | — |
| inflate 44.4 MB | 0.047 s | 2% |
| level and object-header walk | 0.231 s | 11% |
| every object's properties | 1.791 s | 87% |
| **total** | **2.07 s** | against the vendored parser's **2.4–2.5 s** |

So the old note that "decompression is not the cost" holds, and its second half needed
splitting: the *walk* is 11% and the *property bodies* are 87%. A third measurement is the
interesting one — **0.53 s of that 1.791 s is retention, not parsing.** Reading each property
block and discarding it costs 1.257 s; keeping all 44,634 costs 1.791 s and about 136 MB. The
projection makes exactly one pass over the objects, so a streaming `read_full_save` would pay
for itself twice over. Not taken: it changes the shape the trailing-bytes work builds on, and
this is a measured opportunity rather than a guess.

End to end, including interpreter start, over the 31 readable saves: **vendor 75.7 s, own
59.2 s.**

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
`§13c`'s runtime gate makes the skew visible; deriving on update would remove it.

**Why it is now cheap.** The collectibles work built a reader for the game's own IoStore container:
4,521 `GameLevel01` `.umap` packages decompress in ~3 s with zero failures, positions agree with
live save actors to a **median 0.0000 cm**, and the extraction reproduces the deleted GPL tables
class-for-class. So `resource_nodes.json` and `world_collectibles.json` could become **caches keyed
on the game's `build_version`** rather than committed snapshots, regenerated when a save reports a
build the cache does not know.

**What has to be settled before building it**, and none of it is hard, only unexamined:

* **Oodle.** The decompressor is `pyooz`, GPL-3.0, and today it is deliberately an offline
  generation-time tool that never enters `pyproject.toml`, `src/` or `sidecar/`. Regenerating *at
  runtime* would put it in the dependency graph and undo the licence position this project spent
  months reaching. Either the cache is refreshed by an explicit `tools/` run the user invokes after
  a patch — which keeps the current posture and is the conservative answer — or a permissively
  licensed Kraken decoder is found first.
* **Cost.** ~3 s of extraction is fine for a build-time step and not for a tool call, so the
  refresh must be explicit or lazily cached, never per-request.
* **What survives a refresh.** `data/world_collectibles.json` carries per-instance `state`
  (`collected` / `present` / `unknown`) folded from the save history. A regenerated placement set
  must re-fold that rather than reset it, and instance renames mean the fold cannot key on the name
  alone — position within a tolerance is the fallback, and `§13c` shows the tolerance has to be
  chosen against the rounding floor rather than guessed.
* **No first-party escape for the region layer.** The game ships no biome geometry, so
  `data/satisfactory_regions.json` stays a CC BY-SA trace whatever happens here.

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
and by 0.8 output pixels of blur, which handles the great deal of this world's water that
sits in box-shaped bodies against a cliff and has no depth band to blend in.

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

### z5 and no further

The artwork pyramid can invent z6 and z7 with an upscaler because a drawn map has strokes a
model understands. These layers stop at 8192 px, which is 0.92 m to the pixel against a 1 m
field. A z6 here would be interpolation claiming to be terrain, and there is no measurement
that would make it not one.

### Why a new tool rather than a stage on the heightmap generator

`gen_world_heightmap.py`'s job is to get a field *out of the game* — sweep cooked packages,
rasterise collision meshes, validate against 626 nodes. `gen_map_renders.py` reads that
finished field back through the same public codec any consumer would, adds an input the
heightmap generator has never heard of, and writes pictures. They share an input and nothing
else: no stage, no constant, no intermediate array. Bolting them together would have coupled
a six-minute extraction to a ninety-second render and given one `--force` two meanings. What
*is* shared is shared by import — the codec from `domain.spatial.heightfield`, and the
pyramid cutter, its staging rename and its refusals from `gen_map_image.py`, which grew one
optional `source=` so a level record can say what actually drew it.

Measured on the reference machine: 12 s to draw terrain and 15 s satellite at 8192², 32 s
each to cut, 91 s for both layers end to end, 60.1 and 60.2 MB of PNG per pyramid over 1,365
tiles. Banded at 256 rows with a 4-row halo, so no pixel is computed from a one-sided
gradient or a truncated kernel and no whole-sheet float array is ever allocated.

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
z5 and Leaflet upscales past it while the artwork runs to its own z7 if it was `--enhance`d.

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

## Appendix A — current save state

`Han Solo`, 315 h, vanilla, `saveVersion 60` / `buildVersion 495413`.
**Snapshot only** — this is a live rotating autosave; every count drifts.

**Progression:** Current game phase `GP_Project_Assembly_Phase_3`, target `Phase_4`, and
`mTargetGamePhasePaidOffCosts` is empty — **nothing delivered toward Phase 4 yet**, so the whole of
Assembly Director System 4000 / Magnetic Field Generator 4000 / Thermal Propulsion Rocket 1000 /
Nuclear Pasta 1000 is outstanding. The deprecated `mGamePhaseCosts` array *also* claims 500 Modular
Engine and 100 Adaptive Control Unit are owed on Phase 3; that is **frozen and wrong** ([§6.4](#64-space-elevator-phases--two-records-and-only-one-is-alive)).
Tier 6 fully complete; **tier 7 is 3/5** (Hazmat Suit and Hoverpack outstanding). Phase 5 costs are
absent from the save entirely. 226 purchased schematics, **405 available recipes**, 60 inventory slots.

**Power Shards: 22 free, 97 committed, 119 owned.** The 97 are read from the `InventoryPotential` of the
41 overclocked buildings; 2 of those hold a shard their current clock does not use.

**Alternates: 30 recipes unlocked** of 109. Oil-relevant **held**: Recycled Plastic, Recycled Rubber,
**Diluted Fuel** (Blender), **Diluted Packaged Fuel**, **Heavy Oil Residue**, **Polymer Resin**, plus
**Turbofuel** and Compacted Coal (both via the Sulfur MAM tree, not hard drives).
Oil-relevant **missing**: Turbo Heavy Fuel, Turbo Blend Fuel, Coated Cable.

**Unlocked but never built:** Blender (0), Geothermal Generator (0), Alien Power Augmenter.
**Locked:** resource wells (Pressurizer), Miner Mk3, Conveyor Mk6.

**Built:** 36 Refineries, 32 Coal Generators, 23 Water Extractors, 20 Fuel Generators, 15 Packagers,
13 Oil Extractors, 10 biomass generators — **all 10 paused**. 16 buildings paused in total; 46 machines
over/underclocked; 8 Smelters in the north with **no recipe set**.

**Installed extraction:** crude **2,070 m³/min** across 13 pumps (1,230 north + 840 west, some at 250%
clock); coal 2,400/min from 8 miners; **sulfur only 30/min** from one impure node — the binding
constraint on every turbofuel route.

**Northern crude:** 4 clusters. Of the 13 northern nodes, **10 are already tapped**; the remaining
headroom is 3 nodes / 240 m³/min at x ≈ 147,000, well east of the build cluster at x ∈ [−44k, +57k].
The map has 30 pumpable oil nodes (8 pure / 12 normal / 10 impure) plus 18 well satellites that are
unreachable while wells are locked.

**Hard drives: 25 unclaimed, each a live 2-way choice** (50 distinct schematics, no duplicates, all
`EST_Alternate`, all dependencies met). Drive **25 has exhausted its reroll**; the rest have 1 each.
Plus 1 loose drive in the Dimensional Depot. Nothing currently being researched.

Notable pending offers: **HD9** Turbo Heavy Fuel *vs* Steel Rod · **HD25** Turbo Blend Fuel *vs* Leached
Caterium Ingot · **HD34** Coated Cable *vs* Charcoal · **HD17** Plastic Smart Plating *vs* Steel Canister ·
**HD15** Biocoal *vs* Coated Iron Canister.

Of the 109 alternates, **24 have unmet `BP_SchematicPurchasedDependency_C` gates** — everything behind
Schematic_8-x/9-x (Diamonds, Dark Matter, nuclear, Electric Motor …), since the highest purchased
progression schematics are 7-1/7-2/7-4-1/7-5.

---

## Appendix B — the oil finding

The reason the northern oil plant is hard is not layout. It's that **every crude→fuel route the user has
unlocked emits Polymer Resin**, resin only terminates in plastic or rubber, and Recycled Plastic +
Recycled Rubber *create* both from fuel rather than absorbing them. With MW as the only permitted export
the LP abandons crude entirely and returns **8,610 MW from coal** — the oil is worthless.

Given a solid outlet the picture inverts. Best route on current unlocks is **Alt Heavy Oil Residue →
Diluted Fuel** (Blender): 60 crude → 160 Fuel, **30.00 net MW per m³/min crude** versus 7.58 for the base
Fuel recipe — **3.96×**. Diluted Packaged Fuel is a Blender-free fallback, worse by ~11%, needing no new
building type.

**Design consequence, and the user's decision:** exports are `{__MW__, Plastic, Rubber}` with minimum
target rates, not MW alone. The resin then terminates in product the user wants, and the plant is both
feasible and worth building. Water is the next binding constraint (~11 Mk2 pipes at scale, plus more
Water Extractors), and the first Blender has to be built.

Two unpriced upsides the first analysis missed: **4 northern geysers** are worth ~675 MW of fuel-free,
byproduct-free power via the already-unlocked Geothermal Generator, and the already-unlocked **Alien Power
Augmenter** is worth ~686 MW per somersloop versus ~426 MW for production boost — so sloops probably
shouldn't go into refineries at all.
