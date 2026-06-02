"""Power Shards and Somersloops: what is held, what is slotted, what is free."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from ..world.inventory import Inventory

__all__ = ["OverclockBudget"]


@dataclass
class OverclockBudget:
    """The two spendable pools that change a machine's rate.

    Takes the build records rather than reaching for them: both budgets read
    ``InventoryPotential`` off every machine, extractor and generator alike, and
    which records those are is the census's business.
    """

    projection: dict
    game: GameData
    inventory: Inventory
    records: list[dict] = field(default_factory=list)

    #: The Somersloop and Mercer Sphere item classes. Named here rather than resolved by
    #: display name because both are stable class ids and a name lookup would silently
    #: match nothing in a localised dump.
    SLOOP_ITEM: ClassVar[str] = "Desc_WAT1_C"
    MERCER_ITEM: ClassVar[str] = "Desc_WAT2_C"

    def shard_budget(self) -> dict:
        """Power Shards held, committed and free.

        Committed shards are READ, never derived. Every buildable carries an
        ``InventoryPotential`` component holding the shards actually slotted into it,
        and that is the only faithful count: a shard raises the maximum clock, it does
        not set it, so a building may hold more shards than its current clock needs. On
        the reference save 39 of 41 overclocked buildings hold exactly
        ``shards_for_clock``, and two hold 3 while running at 2.0 -- deriving from clock
        would report 95 spent where 97 are.

        "Free" excludes machine inventories on purpose (see ``stock``): the 97 in
        InventoryPotential components are all inside machines, so counting the raw
        ``inventories["machine"]`` total as shards on hand overstates the free pool by
        more than 4x on this save.
        """
        from ...core.gamedata.constants import POTENTIAL_SHARD_SLOTS, shards_for_clock

        shard_items = self.game.clock_shards()
        per_shard = max(shard_items.values()) if shard_items else 0.0
        stock = self.inventory.stock()
        free = sum(stock.get(item, 0.0) for item in shard_items)

        # Uncrafted slugs are latent shards. They sit wherever stock() looks -- carried,
        # in crates, or in the Dimensional Depot -- and on the reference save the depot
        # alone holds 93 Blue, 58 Yellow and 39 Purple, worth 404 shards against 22
        # already crafted. Reporting only the crafted pool understates what the player
        # can overclock with by ~19x, which is the same class of error as counting
        # machine buffers as stock.
        # Where they physically are. "free" pools carried + crates + Depot, which is
        # correct for spending but hides the answer to "is that the Depot?" -- a question
        # worth not making the player ask.
        wanted = set(shard_items) | set(self.game.slug_yields())
        by_place: dict[str, dict[str, float]] = {}
        for place, source in (
            ("carried", self.inventory.sources.get("player", {})),
            ("crates", self.inventory.sources.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = {self.game.item_name(k): v for k, v in source.items() if k in wanted and v}
            if held:
                by_place[place] = held

        slugs = []
        craftable = 0.0
        for item, yield_each in sorted(self.game.slug_yields().items(), key=lambda kv: -kv[1]):
            held = stock.get(item, 0.0)
            if not held:
                continue
            slugs.append(
                {
                    "item": item,
                    "name": self.game.item_name(item),
                    "held": held,
                    "each": yield_each,
                    "shards": held * yield_each,
                }
            )
            craftable += held * yield_each

        committed = 0
        holders: list[dict] = []
        for record in self.records:
            slotted = sum(
                n
                for item, n in (record.get("potential_slots") or {}).items()
                if item in shard_items
            )
            clock = float(record.get("clock") or 1.0)
            needed = shards_for_clock(clock, per_shard)
            if not slotted and not needed:
                continue
            committed += slotted
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "clock": clock,
                    "slotted": slotted,
                    "needed": needed,
                    #: A slot filled but not being used by the current clock.
                    "idle": max(0, slotted - needed),
                }
            )
        holders.sort(key=lambda h: (-h["slotted"], h["cls"]))
        return {
            "shard_items": shard_items,
            "slugs": slugs,
            "by_place": by_place,
            #: Shards these slugs would yield once crafted. NOT free: crafting is a
            #: manual step, so this is potential, never availability.
            "craftable": craftable,
            "potential": free + craftable,
            "free": free,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "slots_per_building": POTENTIAL_SHARD_SLOTS,
            #: Buildings whose InventoryPotential is unreadable get no entry at all, so
            #: a projection predating schema 9 reports 0 committed rather than a wrong
            #: number. Flagged so a caller can tell the two apart.
            "measured": any("potential_slots" in r for r in self.records),
        }

    def sloop_budget(self) -> dict:
        """Somersloops on hand and in machines.

        Free ones are read the same way as shards: ``stock`` pools carried, crates and
        the Dimensional Depot, which is exactly the set that can be spent.

        **Committed ones are read too, and this module used to claim they could not be.**
        That was wrong, and wrong in an instructive way: a save taken before Production
        Amplifier was researched contains no somersloop anywhere, so probing it found
        nothing and the absence was read as "the game does not record this". It does --
        in ``InventoryPotential``, the *same component* that holds Power Shards, which the
        sidecar had been reading into ``potential_slots`` the whole time. The component
        carries both, distinguished only by item class, and its ``mArbitrarySlotSizes``
        shows the shape: ``[1, 1, 1, 2]`` on an Assembler is three shard slots plus one
        somersloop slot holding up to two.

        So the number is exact, like the shard one, and for the same reason: it is the
        slot contents, not something derived from the boost. ``mPendingProductionBoost``
        does record the resulting MULTIPLIER (1.5 on an Assembler with one of two slots
        filled), which is a useful cross-check but a worse source -- inverting a
        multiplier to a count needs the building's base and step, and rounds.

        Mercer Spheres are counted separately and never added in. They share the WAT
        prefix and the same alien-artifact feel, and they do nothing for production.
        """
        stock = self.inventory.stock()
        free = float(stock.get(self.SLOOP_ITEM, 0.0))
        by_place: dict[str, float] = {}
        for place, source in (
            ("carried", self.inventory.sources.get("player", {})),
            ("crates", self.inventory.sources.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = float(source.get(self.SLOOP_ITEM, 0.0))
            if held:
                by_place[place] = held

        committed = 0.0
        holders: list[dict] = []
        for record in self.records:
            slotted = float((record.get("potential_slots") or {}).get(self.SLOOP_ITEM, 0.0))
            if not slotted:
                continue
            committed += slotted
            building = self.game.buildings.get(record.get("cls", ""))
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "name": building.name if building else record.get("cls", "?"),
                    "sloops": slotted,
                    #: What the plan model says that many slots are worth, so a caller can
                    #: check it against the boost the save reports.
                    "boost": building.boost_for(int(slotted)) if building else None,
                    "boost_in_save": record.get("production_boost"),
                }
            )
        holders.sort(key=lambda h: (-h["sloops"], h["cls"]))
        return {
            "item": self.SLOOP_ITEM,
            "free": free,
            "by_place": by_place,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "mercer_spheres": float(stock.get(self.MERCER_ITEM, 0.0)),
            #: False only on a projection too old to carry InventoryPotential at all, in
            #: which case `committed` is unknown rather than zero. Same flag, and the same
            #: reason, as the shard budget's.
            "committed_measured": any("potential_slots" in r for r in self.records),
        }
