"""MAM-gated capabilities: whether one is researched, and what still blocks it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from ..world.inventory import Inventory
    from .unlocks import UnlockSet

__all__ = ["ResearchGates"]


@dataclass
class ResearchGates:
    """The unlock flags a save carries, and the schematics behind the ones it lacks."""

    projection: dict
    game: GameData
    unlocks: UnlockSet
    inventory: Inventory

    #: Capability -> the unlock flag that records it, where one exists. Present only
    #: once true: UE omits a SaveGame property at its default, so absent means false.
    CAPABILITY_FLAGS: ClassVar[dict[str, str]] = {
        "production_boost": "mIsBuildingProductionBoostUnlocked",
    }

    @property
    def _unlock_flags(self) -> dict:
        return self.projection.get("unlock_flags", {}) or {}

    def has_capability(self, name: str) -> bool:
        """Whether a MAM-gated capability is researched.

        The unlock flag wins when the projection carries one, because it is what the game
        itself checks. The purchased-schematic set is the fallback, and it is not merely
        belt-and-braces: the flag is absent from a save taken before the research AND from
        any projection written before schema 10 extracted it, and those two look identical
        from here. Falling back keeps an older projection answering correctly instead of
        reporting every capability locked.
        """
        from ...core.gamedata.constants import CAPABILITY_SCHEMATICS

        flag = self.CAPABILITY_FLAGS.get(name)
        if flag and flag in self._unlock_flags:
            return bool(self._unlock_flags[flag])
        gate = CAPABILITY_SCHEMATICS.get(name)
        return bool(gate) and gate in self.unlocks.purchased_schematic_ids

    def research_gate(self, name: str) -> dict | None:
        """The schematic that unlocks ``name``, its cost, and what the player holds.

        ``None`` when the capability is already researched, so a caller can treat a
        truthy result as "here is what is still in the way".
        """
        from ...core.gamedata.constants import CAPABILITY_SCHEMATICS

        gate = CAPABILITY_SCHEMATICS.get(name)
        schematic = self.game.schematics.get(gate or "")
        if schematic is None or self.has_capability(name):
            return None
        stock = self.inventory.stock()
        rows = [
            {
                "item": f.item,
                "name": self.game.item_name(f.item),
                "need": f.amount,
                "have": stock.get(f.item, 0.0),
            }
            for f in schematic.cost
        ]
        return {
            "capability": name,
            "schematic": gate,
            "schematic_name": schematic.name,
            "kind": schematic.type,
            "cost": rows,
            "short": [r for r in rows if r["have"] < r["need"]],
            "affordable": all(r["have"] >= r["need"] for r in rows),
            #: Prerequisite schematics not yet purchased. Empty on an unblocked node.
            "blocked_by": [
                self.game.schematics[d].name
                for d in schematic.dependencies
                if d in self.game.schematics and d not in self.unlocks.purchased_schematic_ids
            ],
        }
