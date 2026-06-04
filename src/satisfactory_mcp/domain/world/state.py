"""Derived views over a save projection, joined against normalized game data.

``WorldState`` used to hold every one of those views itself, which made it the one
class in the package that owned a dozen subjects: unlocks, the build census, power,
progression, research gates, hard drives, overclocking, inventories, the map's
collectible table and the save's own identity. Each of those is now a small
dataclass of its own -- one subject each, built from the same two fields -- and this
class holds them and delegates.

The delegation is not a transitional layer. ``WorldState`` is the context every
other domain package takes as an argument, and the surface it had before is the
surface those callers spell, so it keeps all of it: the properties, the methods, the
class-level constants and the module-level names alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from ...core.gamedata.model import GameData, Recipe, Schematic
from ...core.saveio import projection as proj
from ..collectibles.removed import RemovedActors
from ..collectibles.table import CollectibleTable, _name_stem, load_collectibles
from ..power.report import PowerLedger
from ..progression.harddrives import HardDriveDesk, HardDriveOffer
from ..progression.phases import PhaseLedger
from ..progression.research import ResearchGates
from ..progression.shards import OverclockBudget
from ..progression.unlocks import UnlockSet
from . import sites as world_sites
from . import water as world_water
from .carriers import CarrierSet
from .census import BuildCensus
from .identity import SaveIdentity
from .inventory import Inventory

__all__ = ["CollectibleTable", "HardDriveOffer", "WorldState", "load_collectibles"]

#: Re-exported rather than used: ``_name_stem`` was born in this module and the
#: collectibles tests still import it from here.
_ = (_name_stem,)


@dataclass
class WorldState:
    """A save projection plus the game data needed to interpret it."""

    projection: dict
    game: GameData

    # ---- facets ---------------------------------------------------------
    #
    # One cached_property each, so a facet is built at most once per state and the
    # ``cached_property`` caches inside it live as long as this object does. That is
    # exactly the lifetime those caches had while they were attributes here.

    @cached_property
    def identity(self) -> SaveIdentity:
        return SaveIdentity(projection=self.projection)

    @cached_property
    def unlocks(self) -> UnlockSet:
        return UnlockSet(projection=self.projection, game=self.game)

    @cached_property
    def census(self) -> BuildCensus:
        return BuildCensus(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def inventory(self) -> Inventory:
        return Inventory(projection=self.projection, game=self.game)

    @cached_property
    def carriers(self) -> CarrierSet:
        return CarrierSet(game=self.game, unlocked_building_ids=self.unlocks.unlocked_building_ids)

    @cached_property
    def power(self) -> PowerLedger:
        return PowerLedger(
            projection=self.projection, game=self.game, paused_count=len(self.census.paused)
        )

    @cached_property
    def phases(self) -> PhaseLedger:
        return PhaseLedger(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def research(self) -> ResearchGates:
        return ResearchGates(
            projection=self.projection,
            game=self.game,
            unlocks=self.unlocks,
            inventory=self.inventory,
        )

    @cached_property
    def harddrive_desk(self) -> HardDriveDesk:
        return HardDriveDesk(projection=self.projection, game=self.game, unlocks=self.unlocks)

    @cached_property
    def overclock(self) -> OverclockBudget:
        return OverclockBudget(
            projection=self.projection,
            game=self.game,
            inventory=self.inventory,
            records=self.census.all_records(),
        )

    @cached_property
    def removed(self) -> RemovedActors:
        return RemovedActors(projection=self.projection, table=self.collectibles)

    # ---- identity ------------------------------------------------------

    @property
    def header(self) -> dict:
        return self.identity.header

    @property
    def world_id(self) -> str:
        return self.identity.world_id

    @property
    def age_note(self) -> str:
        return self.identity.age_note

    @cached_property
    def graph(self):
        """The factory graph. Built once per state, since identity, health and layout
        all want it."""
        from ..factories.build import build_graph

        return build_graph(self.projection)

    @cached_property
    def structures(self):
        """Foundation slabs -- what was physically built as one platform."""
        from ..factories.structure import build_structures

        return build_structures(self.projection)

    @cached_property
    def proposals(self):
        """Coherence-scored factory proposals. ~0.3 s, so built once per state."""
        from ..factories.cohere import propose

        return propose(self.graph, self.game, self.projection, self.structures)

    @cached_property
    def plans(self):
        """Named plans saved for this world."""
        from ...planning.store import PlanStore

        return PlanStore.load(self.world_id, self.header.get("session_name") or "")

    @cached_property
    def labels(self):
        """Persisted factory names for this world."""
        from ..factories.labels import LabelStore

        return LabelStore.load(self.world_id, self.header.get("session_name") or "")

    # ---- unlocks -------------------------------------------------------

    @property
    def available_recipe_ids(self) -> set[str]:
        return self.unlocks.available_recipe_ids

    @property
    def purchased_schematic_ids(self) -> set[str]:
        return self.unlocks.purchased_schematic_ids

    @property
    def unresolved_recipe_ids(self) -> set[str]:
        return self.unlocks.unresolved_recipe_ids

    def unlocked_recipes(self, kind: str = "part") -> list[Recipe]:
        return self.unlocks.unlocked_recipes(kind)

    def has_recipe(self, recipe_id: str) -> bool:
        return self.unlocks.has_recipe(recipe_id)

    @property
    def unlocked_alternates(self) -> list[Recipe]:
        return self.unlocks.unlocked_alternates

    @property
    def locked_alternates(self) -> list[Recipe]:
        return self.unlocks.locked_alternates

    @property
    def unlocked_building_ids(self) -> set[str]:
        return self.unlocks.unlocked_building_ids

    def can_build(self, building_id: str) -> bool:
        return self.unlocks.can_build(building_id)

    def dependencies_met(self, schematic_id: str) -> tuple[bool, list[str]]:
        return self.unlocks.dependencies_met(schematic_id)

    def _schematic_recipes(self, s: Schematic) -> list[Recipe]:
        return self.unlocks.schematic_recipes(s)

    # ---- what is actually built ----------------------------------------

    @property
    def built_counts(self) -> dict[str, int]:
        return self.census.built_counts

    def built(self, building_id: str) -> int:
        return self.census.built(building_id)

    def unlocked_but_unbuilt(self) -> list[str]:
        return self.census.unlocked_but_unbuilt()

    @property
    def paused(self) -> list[dict]:
        return self.census.paused

    @property
    def misconfigured(self) -> list[dict]:
        return self.census.misconfigured

    @property
    def overclocked(self) -> list[dict]:
        return self.census.overclocked

    def _all_records(self) -> list[dict]:
        return self.census.all_records()

    # ---- power ---------------------------------------------------------

    def power_report(self) -> dict:
        return self.power.power_report()

    # ---- carriers, and the water they are drawn from --------------------

    BELT_NATIVE: ClassVar[str] = CarrierSet.BELT_NATIVE
    PIPE_NATIVE: ClassVar[str] = CarrierSet.PIPE_NATIVE

    def best_carrier(self, native: str, rate: str) -> tuple[str, float] | None:
        return self.carriers.best_carrier(native, rate)

    def best_belt(self) -> tuple[str, float] | None:
        return self.carriers.best_belt()

    def best_pipe(self) -> tuple[str, float] | None:
        return self.carriers.best_pipe()

    def water_volumes(self) -> dict:
        return world_water.water_volumes(self.projection)

    # ---- progression ---------------------------------------------------

    EGP_TO_PHASE: ClassVar[dict[str, str]] = PhaseLedger.EGP_TO_PHASE

    def progression(self) -> dict:
        return self.phases.progression()

    def phase_requirements(self) -> dict:
        return self.phases.phase_requirements()

    # ---- overclocking ----------------------------------------------------

    SLOOP_ITEM: ClassVar[str] = OverclockBudget.SLOOP_ITEM
    MERCER_ITEM: ClassVar[str] = OverclockBudget.MERCER_ITEM

    def shard_budget(self) -> dict:
        return self.overclock.shard_budget()

    def sloop_budget(self) -> dict:
        return self.overclock.sloop_budget()

    # ---- research gates --------------------------------------------------

    CAPABILITY_FLAGS: ClassVar[dict[str, str]] = ResearchGates.CAPABILITY_FLAGS

    @property
    def _unlock_flags(self) -> dict:
        return self.research._unlock_flags

    def has_capability(self, name: str) -> bool:
        return self.research.has_capability(name)

    def research_gate(self, name: str) -> dict | None:
        return self.research.research_gate(name)

    # ---- what the map placed, and what is left of it ----------------------

    OBSERVED: ClassVar[dict[str, str]] = RemovedActors.OBSERVED
    REMOVED_GROUPS: ClassVar[tuple[tuple[str, tuple[str, ...], bool], ...]] = (
        RemovedActors.REMOVED_GROUPS
    )

    @cached_property
    def collectibles(self) -> CollectibleTable | None:
        """The map's placement table, or ``None`` when it has not been generated.

        Loaded here rather than inside ``RemovedActors`` on purpose: whether a caller
        can see a table at all is the caller's business, and reading it through this
        module's own global is what lets a test stand in a clone that has none.
        """
        return load_collectibles()

    @property
    def destroyed_keys(self) -> frozenset[tuple[str, str]]:
        return self.removed.destroyed_keys

    def placements(self, category: str | None = None, remaining_only: bool = False) -> list[dict]:
        return self.removed.placements(category, remaining_only)

    def nearest_placements(
        self, origin: tuple[float, float], category: str | None = None
    ) -> list[dict]:
        return self.removed.nearest_placements(origin, category)

    def collectible_census(self) -> list[dict]:
        return self.removed.collectible_census()

    def removed_actors(self, group: str | None = None) -> dict:
        return self.removed.removed_actors(group)

    def _removed_by_name(
        self, out: dict, instances: list, cells: list[str], group: str | None
    ) -> dict:
        return self.removed._removed_by_name(out, instances, cells, group)

    def removed_group(self, name: str) -> str | None:
        return self.removed.removed_group(name)

    # ---- MAM / hard drives ---------------------------------------------

    @property
    def hard_drive_offers(self) -> list[HardDriveOffer]:
        return self.harddrive_desk.hard_drive_offers

    def spare_hard_drives(self) -> int:
        return self.harddrive_desk.spare_hard_drives(self.inventory)

    # ---- where the player is -------------------------------------------

    @property
    def players(self) -> list[dict]:
        return self.identity.players

    def player_position(self) -> tuple[float, float, float] | None:
        return self.identity.player_position()

    # ---- what is held ----------------------------------------------------

    @property
    def _inventories(self) -> dict[str, dict[str, float]]:
        return self.inventory.sources

    def stock(self) -> dict[str, float]:
        return self.inventory.stock()

    def machine_buffers(self) -> dict[str, float]:
        return self.inventory.machine_buffers()

    # ---- sites ---------------------------------------------------------

    def infra_points(self) -> list[tuple[float, float]]:
        return world_sites.infra_points(self._all_records())

    def consumer_z(self, building_ids: tuple[str, ...] = ("Build_OilRefinery_C",)) -> float | None:
        return world_sites.consumer_z(self._all_records(), building_ids)

    def sites(self, link_m: float = 300.0) -> list[dict]:
        return world_sites.sites(self._all_records(), link_m)


def load_state(
    game: GameData,
    path: str | None = None,
    world: str | None = None,
    prefer_manual: bool = False,
    refresh: bool = False,
) -> WorldState:
    return WorldState(
        projection=proj.load_projection(path, world, prefer_manual, refresh), game=game
    )
