"""The only values in this project that are NOT read from game data.

Every other rate, power figure and capacity is a cited Docs.json field; the values here are
absent from the dump altogether, so they are pinned here and unit-tested. Do not add to this
register without the same treatment.
"""

from __future__ import annotations

import math

#: Extraction rate multiplier by node purity. Not in Docs.json, but proven by it: every
#: extractor's ``mDescription`` states its base rate as the *normal*-node rate, which
#: ``mItemsPerCycle * 60 / mExtractCycleTime`` reproduces exactly. Asserted at build time.
PURITY_MULT: dict[str, float] = {"impure": 0.5, "normal": 1.0, "pure": 2.0}

#: Power Shard slots per building, giving a 250% max clock. [WIKI]: ``mPotentialShardSlots``
#: is 0 on every building in the dump. The two functions below are the only places this is
#: combined with data and stay beside it rather than at the bottom of the file.
POTENTIAL_SHARD_SLOTS: int = 3


#: Max clock a building can be set to. A formula rather than a literal 2.5 so a patch to
#: ``mExtraPotential`` flows through.
def max_clock(extra_potential_per_shard: float) -> float:
    return 1.0 + POTENTIAL_SHARD_SLOTS * extra_potential_per_shard


#: Shards a building needs slotted to be *allowed* to run at ``clock`` -- a lower bound on
#: what is installed, never an equality. A shard raises the MAXIMUM potential and the player
#: then drags the slider anywhere below it, so committed shards are read from
#: ``InventoryPotential`` rather than derived from a clock.
def shards_for_clock(clock: float, extra_potential_per_shard: float) -> int:
    if extra_potential_per_shard <= 0 or clock <= 1.0:
        return 0
    # round() before ceil(): saved clocks are floats, and 2.0 arrives as 1.9999999 often
    # enough that a bare ceil() would demand a fourth shard for a 200% machine.
    need = math.ceil(round((clock - 1.0) / extra_potential_per_shard, 6))
    return min(need, POTENTIAL_SHARD_SLOTS)


#: Conveyor ``mSpeed`` -> items/min. Cross-checked against each belt's own ``mDescription``
#: prose at build time, so the assertion is self-contained.
BELT_SPEED_TO_IPM: float = 0.5

#: Fluids cannot be sunk or discarded, so a fluid byproduct must be consumed exactly. This
#: CONTRADICTS Docs.json, which gives Heavy Oil Residue 30 sink points and
#: ``mCanBeDiscarded = True``: the AWESOME Sink's input is conveyor-only, so a fluid must be
#: packaged into a solid first. Confirmed by the player.
FLUIDS_CANNOT_BE_SUNK: bool = True

#: Power draw of one AWESOME Sink, charged whenever a plan sinks anything. From Docs.json;
#: kept here so the sink model reads in one place.
AWESOME_SINK_MW: float = 30.0

#: Somersloop amplification is capped at 2x output for 4x power on every building.
MAX_PRODUCTION_BOOST: float = 2.0


#: Stack sizes by the enum Docs.json reports. The dump gives only the symbol, so the numbers
#: are game knowledge. Needed to tell a STARVED machine from a BLOCKED one, which need
#: opposite fixes.
STACK_SIZE: dict[str, int] = {
    "SS_ONE": 1,
    "SS_SMALL": 50,
    "SS_MEDIUM": 100,
    "SS_BIG": 200,
    "SS_HUGE": 500,
    # Fluid buffers are quoted in litres in the save, and a machine's fluid buffer holds 50 m3.
    "SS_FLUID": 50_000,
}


#: Save building class -> the class Docs.json uses for the same building. The dump names the
#: Biomass Burner by its build recipe's Build_GeneratorBiomass_Automated_C while the save
#: stores standing burners as Build_GeneratorBiomass_C. Build_GeneratorIntegratedBiomass_C is
#: NOT aliased: it is the HUB's built-in burner, has no build recipe, and folding it in would
#: credit the player with generators they never placed.
BUILDING_CLASS_ALIASES: dict[str, str] = {
    "Build_GeneratorBiomass_C": "Build_GeneratorBiomass_Automated_C",
}


#: Capabilities the game gates behind MAM research, and the schematic that grants each. A
#: cross-check, not the primary source: ``BP_UnlockSubsystem_C``'s own flags are authoritative
#: where present (§6, `docs/save-projection.md`). This register answers the other half --
#: *which research to go and do* -- and covers a projection written before the flag existed.
CAPABILITY_SCHEMATICS: dict[str, str] = {
    #: Somersloops in production machines: 2x output for 4x power.
    "production_boost": "Research_Alien_ProductionBooster_C",
    #: The Alien Power Augmenter building, which is a different use of the same item.
    "power_augmenter": "Research_Alien_PowerBooster_C",
}

#: Water Extractors the planner assumes can be sited, when the caller does not say.
#:
#: The only number here with no data behind it, and DANGEROUS to read as capacity. Water is
#: drawn from FGWaterVolume objects -- ocean, lakes -- which carry no node entry, no purity and
#: no geometry, so this exists only to keep the column bounded and is set high enough not to
#: bind. Extractors go on platforms built out over open water, so frontage is irrelevant and
#: only water AREA matters; what binds is vertical, since water alone must be drawn at sea
#: level. Pass ``water_extractors`` to replace this with a number the player has measured.
WATER_EXTRACTOR_CAP_ASSUMED: int = 200

#: Above this many extractors in one plan, say plainly that the count is an assumption and
#: quote what the platform costs. Not a danger threshold -- platforming for hundreds is
#: ordinary play -- just where the concrete stops being a rounding error.
WATER_EXTRACTOR_WARN_AT: int = 30
