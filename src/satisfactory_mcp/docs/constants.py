"""The only values in this project that are NOT read from game data.

Every other rate, power figure and capacity is a cited Docs.json field. The values
here are not present in Docs.json at all, so they are pinned here, justified, and
unit-tested. Do not add to this list without the same treatment.

The two functions at the bottom are not extra constants: they are the *only* places
POTENTIAL_SHARD_SLOTS is combined with data, kept here so the one game-knowledge
number stays next to the arithmetic that depends on it.
"""

from __future__ import annotations

import math

#: Extraction rate multiplier by node purity.
#:
#: Not in Docs.json, but proven by it: every extractor's ``mDescription`` states its
#: base rate as the *normal*-node rate ("Default extraction rate is 60 resources per
#: minute"), and ``mItemsPerCycle * 60 / mExtractCycleTime`` reproduces that exactly.
#: Asserted against the descriptions at build time.
PURITY_MULT: dict[str, float] = {"impure": 0.5, "normal": 1.0, "pure": 2.0}

#: Power Shard slots per building, giving a 250% max clock.
#:
#: ``Desc_CrystalShard_C.mExtraPotential = 0.5`` IS in Docs.json, but the slot count
#: is not: ``mPotentialShardSlots`` is 0 and ``mOverridePotentialShardSlots`` is False
#: on every building, i.e. the field is simply unpopulated in the dump. [WIKI] for 3.
#:
#: Corroborated on the reference save, which is the strongest evidence available
#: without extracting the paks. Every building carries an ``InventoryPotential``
#: component holding the shards actually slotted into it; 447 exist, 41 are non-empty,
#: and across those 41 the counts are exactly {1: 6, 2: 16, 3: 19}. **No building holds
#: 4**, and the highest clock observed is 2.5 = 1.0 + 3 x 0.5. A save cannot prove an
#: upper bound the player never tried to exceed, so this stays [WIKI]-tagged -- but the
#: cap and the observed maximum agree.
POTENTIAL_SHARD_SLOTS: int = 3


#: Max clock a building can be set to, from the slot count and the shard's own
#: ``mExtraPotential``. Only the slot count above is game knowledge; the 0.5 is data.
#: Kept as a formula rather than a literal 2.5 so a patch to mExtraPotential flows
#: through, and so the one hardcoded input stays visible at the point of use.
def max_clock(extra_potential_per_shard: float) -> float:
    return 1.0 + POTENTIAL_SHARD_SLOTS * extra_potential_per_shard


#: Shards a building needs slotted to be *allowed* to run at ``clock``.
#:
#: A shard raises the building's MAXIMUM potential, it does not set the clock: the
#: player slots shards and then drags the slider anywhere up to the new maximum. So
#: this is a lower bound on what is installed, never an equality -- measured on the
#: reference save, 39 of 41 overclocked buildings hold exactly this many and **two
#: hold 3 shards while running at clock 2.0**, a spare slot filled with the slider
#: pulled back. Deriving committed shards from clocks would therefore have leaked
#: those 2; ``InventoryPotential`` is read directly instead.
def shards_for_clock(clock: float, extra_potential_per_shard: float) -> int:
    if extra_potential_per_shard <= 0 or clock <= 1.0:
        return 0
    # round() before ceil(): saved clocks are floats, and 2.0 arrives as 1.9999999
    # often enough that a bare ceil() would demand a fourth shard for a 200% machine.
    need = math.ceil(round((clock - 1.0) / extra_potential_per_shard, 6))
    return min(need, POTENTIAL_SHARD_SLOTS)


#: Conveyor ``mSpeed`` -> items/min. Cross-checked against each belt's own
#: ``mDescription`` prose at build time, so the assertion is self-contained.
BELT_SPEED_TO_IPM: float = 0.5

#: Fluids cannot be sunk or discarded, so a fluid byproduct must be consumed exactly.
#:
#: This CONTRADICTS Docs.json, deliberately. The data says Heavy Oil Residue has
#: ``mResourceSinkPoints = 30`` and ``mCanBeDiscarded = True`` (as do Fuel 75,
#: Water 5, Turbofuel 225), but the AWESOME Sink has a conveyor-only input, so a
#: fluid must be packaged into a solid first. Confirmed by the user.
#:
#: This is the conservative direction: a plan that never relies on dumping a fluid
#: cannot stall on one in game.
FLUIDS_CANNOT_BE_SUNK: bool = True

#: Power draw of one AWESOME Sink, charged whenever a plan sinks anything.
#: (This one *is* from Docs.json; kept here so the sink model reads in one place.)
AWESOME_SINK_MW: float = 30.0

#: Somersloop amplification is capped at 2x output for 4x power on every building.
MAX_PRODUCTION_BOOST: float = 2.0


#: Stack sizes by the enum Docs.json reports. Not derivable from the dump -- the JSON
#: gives only the symbol, so the numbers are game knowledge and belong in this register.
#: Needed to answer "is this machine's output backed up", which is what separates a
#: STARVED machine from a BLOCKED one; those need opposite fixes.
STACK_SIZE: dict[str, int] = {
    "SS_ONE": 1,
    "SS_SMALL": 50,
    "SS_MEDIUM": 100,
    "SS_BIG": 200,
    "SS_HUGE": 500,
    # Fluid buffers are quoted in litres in the save, and a machine's fluid buffer holds
    # 50 m3. Verified against observed values: Wire 500 = SS_HUGE, Iron Rod 200 = SS_BIG.
    "SS_FLUID": 50_000,
}


#: Save building class -> the class Docs.json uses for the same building.
#:
#: The dump and the save disagree on a handful of names. Measured on the reference save,
#: 13 classes are built that appear in NO Docs.json entry -- but almost all are world
#: objects (BP_ResourceNode_C, BP_FrackingSatellite_C), HUB-integrated fixtures
#: (Build_HubTerminal_C, Build_WorkBenchIntegrated_C) or fittings with no build recipe
#: (Build_PipelineFlowIndicator_C). Those are correctly absent.
#:
#: Exactly ONE is a placeable building the dump names differently, and it produced a
#: false warning: `unlocked_building_ids` is derived from build recipes, which yield
#: Build_GeneratorBiomass_Automated_C ("Biomass Burner"), while the save stores the
#: eight standing burners as Build_GeneratorBiomass_C. world_summary therefore reported
#: "unlocked but never built: Biomass Burner" against 8 of them running.
#:
#: Build_GeneratorIntegratedBiomass_C is deliberately NOT aliased. It is the burner built
#: into the HUB, has no build recipe of its own, and folding it in would credit the
#: player with generators they never placed.
BUILDING_CLASS_ALIASES: dict[str, str] = {
    "Build_GeneratorBiomass_C": "Build_GeneratorBiomass_Automated_C",
}
