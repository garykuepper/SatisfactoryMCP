"""The only values in this project that are NOT read from game data.

Every other rate, power figure and capacity is a cited Docs.json field. These four
are not present in Docs.json at all, so they are pinned here, justified, and
unit-tested. Do not add to this list without the same treatment.
"""

from __future__ import annotations

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
POTENTIAL_SHARD_SLOTS: int = 3

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
