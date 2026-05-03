# SatisfactoryMcp — Design Spec

An MCP server that helps plan Satisfactory factories: recipe/resource lookup, save-file analysis of
progress and unlocks, spatial resource queries, and LP/MILP factory optimization.

**Status:** implemented. 17 tools, 3 resources, 3 prompts, 119 tests passing. See README.md for usage.
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
| D2 | Game data from the **local `Docs/en-US.json`**, normalized in-process | Ships with the game, updates on patch, authoritative. No web API, no hardcoding. |
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

### 3.4 Resource node purity — the one gap

Node actors serialize **only** `mResourcesLeft` and their transform. Resource type and purity are
**not in the save**, and no biome/region geometry is in `Docs.json`.

`sav_data/resourcePurity.py` in the GreyHak repo fills this: 607 entries keyed by `instanceName` →
`(resourceClass, Purity, (x,y,z), parentFrackingCore)`. Joins **607/607 = 100%** when
`BP_FrackingCore_C` actors are included in the match set.

> **Provenance caveat.** That file carries no licence header; its only provenance line is
> `# Extracted from SCIM for Satisfactory v1.2.0.0` — the data came from a third party
> (Satisfactory-Calculator Interactive Map), and it is **pinned to v1.2.0.0 while the save is v1.2.2.1**.
> Treat it as a vendored data snapshot with a `source` and `game_version` field, validated at load
> (assert 100% key join; warn on node-count drift). Regenerating it independently is a known future task.

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

### 8.4 Clocks and sloops

Continuous clock is genuinely non-convex (`power = P·x^k·n^(1−k)`), and for fixed throughput power
strictly decreases in `n`, so a min-power objective drives `n → ∞, c → 0`. **Use discrete
`(clock, sloops)` modes**, each its own process with exact rate and power. Default `clocks = [1.0]`.

Two proven facts worth exposing:

- **Linear power is a safe over-estimate below 100% clock** (`c^k ≤ c` for `c ≤ 1, k > 1`).
- **Overclocking above 100% is never selected** for a throughput objective — adding 150/200/250% modes
  changed the optimum by exactly 0 MW.
- **The underclocking trap:** allowing 50% modes gained +1,140 MW while going from 516 to 957 machines.
  A max-power model with a free clock is ill-posed. Bound the clock set or price machines, and **warn when
  a sub-100% clock set is what produced the gain.**

### 8.5 Degeneracy

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
**Spatial:** `list_regions`, `describe_location`, `search_resource_nodes`, `rank_build_sites`
**Planning:** `plan_factory`, `explain_byproducts`, `compare_recipe_options`, `diff_vs_save`
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
`LOCALAPPDATA`, not `APPDATA`; regenerable data must not roam). Prune to N newest on startup.

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
| OQ6 | Regenerate the node/purity table independently of SCIM, for v1.2.2.1. | Provenance and version drift. | Parse map assets, or validate the existing table against a fresh save. |
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
