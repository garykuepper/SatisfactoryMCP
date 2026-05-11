# SatisfactoryMcp — Design Spec

An MCP server that helps plan Satisfactory factories: recipe/resource lookup, save-file analysis of
progress and unlocks, spatial resource queries, and LP/MILP factory optimization.

**Status:** implemented. 19 tools, 3 resources, 3 prompts, 185 tests passing. See README.md for usage.
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
    server.py          # FastMCP instance + tool registration ONLY
    config.py          # env: SATISFACTORY_DOCS, SATISFACTORY_SAVES, cache dir
    docs/
      loader.py        # UTF-16 read, NativeClass grouping
      uestruct.py      # UE struct-string parser
      normalize.py     # -> items / recipes / buildings / schematics
      model.py         # dataclasses
    save/
      projection.py    # invokes sidecar, validates, caches
      cache.py
      model.py
    spatial/  geo.py  nodes.py  regions.py  select.py
    planning/ optimize.py  advisor.py
    render.py          # ALL formatting: TSV, envelopes, truncation
  sidecar/
    extract_save.py    # imports sav_parse, emits JSON projection on stdout
    vendor/sat_sav_parse/
  tests/
    fixtures/          # tiny Docs slice + ~9 kB save projection (committed)
```

**Layout rules.** All formatting lives in `render.py` — context efficiency is cross-cutting and silently
regresses if each tool formats its own output. `server.py` contains no logic.

### 4.1 The save seam

`save/projection.py` is the **only** module that knows a save parser exists. It shells out to
`sidecar/extract_save.py`, which imports `sav_parse` and prints a JSON projection to stdout.

Reasons (licensing is *not* one of them, since this isn't distributed):

- **Version fragility.** `sav_parse` hard-fails on unrecognised `saveVersion`. Every game patch can break
  it until upstream updates. One seam = one module to touch.
- **Crash isolation.** A parser crash or a torn autosave can't take down the server.
- **Test seam.** The projection is ~9 kB of JSON. Commit that instead of a 2.9 MB `.sav`; the entire test
  suite then runs with no game install and no parser.

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
| `POTENTIAL_SHARD_SLOTS` | `3` → max clock 2.5 | `Desc_CrystalShard_C.mExtraPotential = 0.5` is in Docs; the slot count is not (`mPotentialShardSlots = 0` everywhere). `[WIKI]` for the 2.5 figure. |
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

---

## 6. Save projection

The sidecar emits a flat JSON projection — never the parse tree. Verified property names:

| fact | object (`typePath` substring) | property |
|---|---|---|
| unlocked recipes ★ | `FGRecipeManager` | `mAvailableRecipes` (405) |
| purchased schematics | `BP_SchematicManager_C` | `mPurchasedSchematics` (226) |
| game phase | `BP_GamePhaseManager_C` | `mCurrentGamePhase`, `mTargetGamePhase`, `mGamePhaseCosts` (**remaining** amounts) |
| hard-drive offers ★ | `BP_ResearchManager_C` | `mUnclaimedHardDriveData`, `mLastUsedHardDriveID` |
| research in progress | `BP_ResearchManager_C` | `mSavedOngoingResearch` — **seconds remaining**, absent when empty |
| misc unlocks | `BP_UnlockSubsystem_C` | `mIsMapUnlocked`, `mIsBuildingOverclockUnlocked`, `mNumTotalInventorySlots`, … |
| depot contents | `FGCentralStorageSubsystem` | `mStoredItems` |
| lifetime stats | `FGStatisticsSubsystem` | `mItemsPickedUp` — **nested**, see below |
| machine recipe | manufacturers | `mCurrentRecipe` |
| clock speed | clockable buildings | `mCurrentPotential`, `mPendingPotential` |
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

---

## 7. Spatial model

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

**Somersloops remain dormant** by explicit decision. The machinery exists (`Process.sloops`,
`Scenario.sloop_budget`, per-building boost multipliers) but nothing populates the budget, so no sloop
modes are generated. Unconstrained sloops give meaningless answers, the runtime property names for
reading installed ones are still unverified (OQ4), and the Alien Power Augmenter may be the better use
anyway — a different model entirely.

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
dispatch — otherwise `max_power` would fall through to the unknown-objective branch.

### 8.7 Degeneracy

`min_raw` LPs are **degenerate** — equally optimal vertices give materially different raw vectors (water
−57.78 vs −46.67 on the same objective). Either apply a documented lexicographic tie-break or label
reported raw vectors as one of several optima. Never present a degenerate component as *the* number.

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

- **`[UNVERIFIED]`** Whether picking one option permanently forfeits the other, and whether the forfeited
  schematic returns to the pool. Only headers ship; the supporting doc comment on
  `GetAvailableAlternateSchematics` excludes "unclaimed hard drive rewards" from candidates, from which
  return-to-pool follows *only if* `ClaimHardDrive` removes the entry. Plausible, not demonstrated.
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
**Save state:** `list_worlds`, `world_summary`, `unlocked_recipes`, `power_report`, `node_occupancy`, `factory_sites`
**Factories:** `factory_map`, `propose_factories`, `select_machines`, `name_factory`, `list_factories`, `forget_factory`
**Spatial:** `list_regions`, `describe_location`, `search_resource_nodes`, `rank_build_sites`
**Layout:** `plan_layout`
**Planning:** `plan_factory`, `plan_layout`, `diff_vs_save`, `explain_byproducts`, `compare_recipe_options`
**Hard drives:** `list_pending_hard_drive_choices`, `advise_hard_drive_pick`

```
plan_factory(objective="max_mw", target_item=None, sources=[...],
             exports=["__MW__"], export_minimums={}, only_free_nodes=False,
             allow_sinks=True, save=None, world=None, limit=15)
  -> summary (net_MW, machines, grid_import, exports, raw, sunk)
     + warnings/binding first, then a process table with a BUILD marker

search_resource_nodes(sources=[...], resource=None, purity=None, kind=None,
                      only_free=False, group="field"|"node", limit=25)
  -> per-field clusters (region, grid, centre, purity mix, total/free, spread)
     or per-node rows whose ids feed straight back in as node: selectors
```

`warnings` and `binding_constraints` come **first** — that's where the insight is ("Blender unlocked but
0 built"; "920/min resin needs an outlet or the line stalls").

**No `plan_chain` by recursive expansion.** It is unsound on this user's own recipe set: Recycled Plastic
(30 Rubber + 30 Fuel → 60 Plastic) and Recycled Rubber (30 Plastic + 30 Fuel → 60 Rubber) form a genuine
2-cycle and **both are unlocked**. There is no correct depth limit. The LP is the only engine.

Also required and absent from the first draft: a **power target** must be expressible (the driving use
case is MW, not an item), and generator fuel throughput needs `mEnergyValue` with its per-litre /
per-item split.

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

## 14. Open questions

| id | question | impact | how to resolve |
|---|---|---|---|
| ~~OQ1~~ | ~~Can fluids actually be sunk?~~ | **CLOSED** — user confirms fluids cannot be sunk. Hardcoded per §5.6. | — |
| OQ2 | Does an unchosen hard-drive option return to the pool, and is the forfeit permanent? | Whether picking is low-stakes or high-stakes advice. | In-game test; or `.pak` inspection. |
| OQ3 | Are `mNumSchematicsPerHardDrive = 2` / `mNumRerollsPerHardDrive = 1` overridden by a packaged ini? | Reroll advice. | Only inside the shipping DLL; treat as semi-verified. |
| OQ4 | Runtime property names for installed somersloops. | Reading sloop placement from a save. | Place a sloop, re-save, diff the properties. |
| OQ5 | Water pump -> water volume mapping (`FGWaterVolume*` aren't purity keys). | Water capacity accounting. | Coordinate fallback, or accept "unknown". |
| ~~OQ6~~ | ~~Regenerate the node/purity table independently of SCIM.~~ | **CLOSED** — merged with an MIT, game-asset-derived set; 0 purity/resource mismatches, and a missing node recovered. See §3.4. | — |
| OQ7 | How many somersloops does the user actually hold? 37 collected per one table, 15 demonstrably on hand. | Sloop budget in the optimizer. | Reconcile depot + inventories + world actors. |

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

---

## Appendix A — current save state

`Han Solo`, 315 h, vanilla, `saveVersion 60` / `buildVersion 495413`.
**Snapshot only** — this is a live rotating autosave; every count drifts.

**Progression:** Game phase 3 of 5 (target 4). Tier 6 fully complete; **tier 7 is 3/5** (Hazmat Suit and
Hoverpack outstanding). Phase 5 costs are absent from the save entirely. 226 purchased schematics,
**405 available recipes**, 60 inventory slots.

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
