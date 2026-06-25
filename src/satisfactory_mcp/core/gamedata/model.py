"""Normalized game-data model.

Every field here is derived from Docs.json except the four in ``constants.py``.
Rates are per minute at 100% clock with no somersloops; fluids are in m3 (already
divided by 1000).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .footprint import Footprint

__all__ = [
    "MANUFACTURER_NATIVES",
    "Building",
    "Flow",
    "Fuel",
    "GameData",
    "Item",
    "Recipe",
    "Schematic",
]

#: The 11 classes that make a recipe automatable. Both natives are required --
#: omitting FGBuildableManufacturerVariablePower silently loses the 43
#: Particle-Accelerator / Converter / Quantum-Encoder recipes.
MANUFACTURER_NATIVES = ("FGBuildableManufacturer", "FGBuildableManufacturerVariablePower")


@dataclass(frozen=True)
class Item:
    cls: str
    name: str
    native: str
    form: str  # RF_SOLID | RF_LIQUID | RF_GAS | RF_INVALID
    energy_mj: float  # per m3 for fluids, per item for solids; 0 if not a fuel
    stack_size: str
    sink_points: int
    can_be_discarded: bool
    is_resource: bool
    #: ``mExtraPotential``: how much max clock ONE of these adds when slotted into a
    #: building's InventoryPotential. 0.5 for the Power Shard, 0 for everything else
    #: including the Somersloop, which shares the same native class but boosts
    #: production rather than clock. Reading it is what lets the shard maths avoid
    #: naming ``Desc_CrystalShard_C`` anywhere.
    extra_potential: float = 0.0

    @property
    def is_fluid(self) -> bool:
        return self.form in ("RF_LIQUID", "RF_GAS")

    @property
    def unit(self) -> str:
        return "m3/min" if self.is_fluid else "/min"

    @property
    def sinkable(self) -> bool:
        """Whether an AWESOME Sink can consume this.

        Fluids are excluded by a hardcoded physical rule that deliberately
        contradicts Docs.json -- see constants.FLUIDS_CANNOT_BE_SUNK.
        """
        from .constants import FLUIDS_CANNOT_BE_SUNK

        if self.is_fluid and FLUIDS_CANNOT_BE_SUNK:
            return False
        return self.sink_points > 0 and self.can_be_discarded


@dataclass(frozen=True)
class Flow:
    """One side of a recipe: an item and its rate."""

    item: str
    amount: float  # per cycle, already m3 for fluids
    per_min: float


@dataclass(frozen=True)
class Recipe:
    cls: str
    name: str
    kind: str  # part | building | manual
    machine: str | None  # Build_* class; None for manual/building recipes
    duration_s: float
    ingredients: tuple[Flow, ...]
    products: tuple[Flow, ...]
    manual_mult: float
    power_min_mw: float  # variable-power machines only; 0 otherwise
    power_max_mw: float
    unlocked_by: tuple[str, ...] = ()
    is_alternate: bool = False
    events: str = ""

    @property
    def power_avg_mw(self) -> float:
        return (self.power_min_mw + self.power_max_mw) / 2

    @property
    def is_variable_power(self) -> bool:
        return self.power_max_mw > 0

    @property
    def is_event(self) -> bool:
        return bool(self.events)

    def rate_of(self, item: str) -> float:
        """Net per-minute rate of ``item`` for one machine at 100% clock."""
        out = sum(f.per_min for f in self.products if f.item == item)
        inn = sum(f.per_min for f in self.ingredients if f.item == item)
        return out - inn


@dataclass(frozen=True)
class Fuel:
    fuel_class: str
    supplemental_class: str | None
    byproduct_class: str | None
    byproduct_amount: float


@dataclass(frozen=True)
class Building:
    cls: str
    name: str
    native: str
    power_mw: float
    power_exponent: float  # ALWAYS read per building: 1.321929 vs 1.600000
    boost_power_exponent: float
    can_overclock: bool
    min_clock: float
    base_max_clock: float
    can_boost: bool
    sloop_slots: int
    sloop_mult: float
    base_boost: float
    mfg_speed: float
    descriptor: str | None = None
    build_cost: tuple[Flow, ...] = ()
    unlocked_by: tuple[str, ...] = ()
    #: Axis-aligned footprint from mClearanceData, in metres. None for a few
    #: buildables that declare no clearance at all.
    footprint: Footprint | None = None

    # extractor
    items_per_cycle: float = 0.0
    extract_cycle_s: float = 0.0
    base_extract_rate: float = 0.0  # per min at normal purity, 100% clock
    allowed_resources: tuple[str, ...] = ()
    allowed_forms: tuple[str, ...] = ()

    # generator
    power_production_mw: float = 0.0
    variable_power_factor: float = 0.0
    requires_supplemental: bool = False
    supplemental_ratio: float = 0.0
    fuels: tuple[Fuel, ...] = ()

    # logistics
    items_per_min: float = 0.0
    flow_m3_min: float = 0.0
    #: How much fluid a buffer holds, in cubic metres, from ``mStorageCapacity`` -- 400 on the
    #: Fluid Buffer and 2,400 on the Industrial one. 0.0 for everything that is not a
    #: reservoir, which is every other building in the dump: the field is on
    #: ``FGBuildablePipeReservoir`` alone.
    #:
    #: Here because a level is not a reading without one. The save records a buffer's contents
    #: as a bare ``mFluidBox`` float, so "1,730.6 m3" is a number a player cannot act on and
    #: "1,730.6 of 2,400, 72% full" is the same number as an answer. The solid containers need
    #: no equivalent: their slot count comes off the save itself, per container.
    storage_capacity_m3: float = 0.0
    #: Metres of head a pipeline pump lifts, from ``mDesignPressure``. 20 on Mk1, 50 on
    #: Mk2.
    #:
    #: This project spent a while asserting that head-per-pump was "a game rule with no
    #: data behind it" and refusing to give pump counts on that basis. It was in
    #: Docs.json the whole time, under a name nobody grepped for -- *pressure*, not
    #: *head* or *lift*. The refusal was right in spirit and wrong in fact.
    #:
    #: ``mMaxPressure`` is higher (22 and 55) and is the point of failure rather than the
    #: rating, so the design figure is the one to plan against.
    head_lift_m: float = 0.0
    max_head_lift_m: float = 0.0

    @property
    def max_clock(self) -> float:
        from .constants import POTENTIAL_SHARD_SLOTS

        if not self.can_overclock:
            return 1.0
        return self.base_max_clock + POTENTIAL_SHARD_SLOTS * 0.5

    @property
    def is_manufacturer(self) -> bool:
        return self.native in MANUFACTURER_NATIVES

    @property
    def is_extractor(self) -> bool:
        return self.base_extract_rate > 0 or self.native in (
            "FGBuildableResourceExtractor",
            "FGBuildableWaterPump",
            "FGBuildableFrackingExtractor",
        )

    @property
    def is_generator(self) -> bool:
        return self.native.startswith("FGBuildableGenerator")

    def power_at(self, clock: float = 1.0, sloops: int = 0) -> float:
        """Consumption in MW at a given clock and somersloop count."""
        boost = self.boost_for(sloops)
        return self.power_mw * (clock**self.power_exponent) * (boost**self.boost_power_exponent)

    def boost_for(self, sloops: int) -> float:
        """Output multiplier from ``sloops`` somersloops. Capped at 2x everywhere."""
        from .constants import MAX_PRODUCTION_BOOST

        if not self.can_boost or sloops <= 0:
            return self.base_boost
        n = min(sloops, self.sloop_slots)
        return min(self.base_boost + n * self.sloop_mult, MAX_PRODUCTION_BOOST)

    def extract_rate(self, purity: str = "normal", clock: float = 1.0) -> float:
        from .constants import PURITY_MULT

        return self.base_extract_rate * PURITY_MULT[purity] * clock

    def fuel_rate_per_min(self, item: Item) -> float:
        """Fuel consumption per minute at 100% clock, in m3/min or items/min."""
        if not self.power_production_mw or not item.energy_mj:
            return 0.0
        return self.power_production_mw / item.energy_mj * 60

    def supplemental_m3_min(self) -> float:
        """Water (or other supplemental) demand in m3/min at 100% clock."""
        if not self.requires_supplemental:
            return 0.0
        return self.power_production_mw * self.supplemental_ratio * 60 / 1000


@dataclass(frozen=True)
class Schematic:
    cls: str
    name: str
    type: str  # EST_Milestone | EST_Alternate | EST_MAM | EST_ResourceSink | ...
    tier: int
    time_s: float
    cost: tuple[Flow, ...]
    unlocks_recipes: tuple[str, ...]
    unlocks_schematics: tuple[str, ...]
    dependencies: tuple[str, ...]
    events: str
    grants_inventory_slots: int = 0

    @property
    def is_alternate(self) -> bool:
        return self.type == "EST_Alternate"


@dataclass
class GameData:
    items: dict[str, Item]
    recipes: dict[str, Recipe]
    buildings: dict[str, Building]
    schematics: dict[str, Schematic]
    docs_sha256: str
    warnings: list[str] = field(default_factory=list)

    # ---- lookups -------------------------------------------------------

    def item_name(self, cls: str) -> str:
        it = self.items.get(cls)
        return it.name if it else cls

    def clock_shards(self) -> dict[str, float]:
        """Item class -> max-clock added per unit slotted, for every shard that
        overclocks. Derived, not listed: ``FGPowerShardDescriptor`` holds two classes
        and only the Power Shard has ``mExtraPotential > 0``, so filtering on the field
        excludes the Somersloop without either class being named in code."""
        return {c: it.extra_potential for c, it in self.items.items() if it.extra_potential > 0}

    def slug_yields(self) -> dict[str, float]:
        """Item class -> Power Shards it crafts into, for every single-ingredient
        shard recipe.

        Derived from the recipes, never listed: the dump gives Power Shard (1), (2) and
        (5) taking one Blue, Yellow or Purple slug, so the 1/2/5 ratios are data rather
        than game knowledge. Restricted to ONE ingredient on purpose -- Synthetic Power
        Shard also makes shards, but from Time Crystal, Dark Matter Crystal, Quartz and
        Photonic Matter, which is a production chain and not something lying in a crate.
        """
        shard_items = set(self.clock_shards())
        out: dict[str, float] = {}
        for recipe in self.recipes.values():
            if recipe.kind != "part" or len(recipe.ingredients) != 1:
                continue
            made = sum(f.amount for f in recipe.products if f.item in shard_items)
            if not made:
                continue
            source = recipe.ingredients[0]
            if source.item in shard_items or not source.amount:
                continue
            out[source.item] = max(out.get(source.item, 0.0), made / source.amount)
        return out

    def part_recipes(self) -> list[Recipe]:
        return [r for r in self.recipes.values() if r.kind == "part"]

    def automatable(self, include_events: bool = False) -> list[Recipe]:
        return [r for r in self.part_recipes() if include_events or not r.is_event]

    def producers_of(self, item_cls: str, kind: str = "part") -> list[Recipe]:
        return [
            r
            for r in self.recipes.values()
            if r.kind == kind and any(f.item == item_cls for f in r.products)
        ]

    def consumers_of(self, item_cls: str, kind: str = "part") -> list[Recipe]:
        return [
            r
            for r in self.recipes.values()
            if r.kind == kind and any(f.item == item_cls for f in r.ingredients)
        ]

    def alternates(self) -> list[Recipe]:
        return [r for r in self.recipes.values() if r.is_alternate]

    def machine(self, recipe: Recipe) -> Building | None:
        return self.buildings.get(recipe.machine) if recipe.machine else None

    def recipe_power_mw(self, recipe: Recipe, clock: float = 1.0, sloops: int = 0) -> float:
        """MW drawn by one machine running ``recipe``.

        Variable-power machines (Particle Accelerator, Converter, Quantum Encoder)
        have mPowerConsumption == 0 and take their draw from the recipe instead.
        """
        b = self.machine(recipe)
        if b is None:
            return 0.0
        if recipe.is_variable_power:
            base = recipe.power_avg_mw
            boost = b.boost_for(sloops)
            return base * (clock**b.power_exponent) * (boost**b.boost_power_exponent)
        return b.power_at(clock, sloops)

    def builds(self, building_cls: str) -> Recipe | None:
        """The build-gun recipe that constructs a building, for build costs."""
        b = self.buildings.get(building_cls)
        if b is None or not b.descriptor:
            return None
        for r in self.recipes.values():
            if r.kind == "building" and any(f.item == b.descriptor for f in r.products):
                return r
        return None
