"""What the world holds, split into what can be spent and what merely exists."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ...core.gamedata.model import GameData

__all__ = ["Inventory"]


@dataclass
class Inventory:
    """Item stacks, joined to the item dump so fluids can be scaled to m3."""

    projection: dict
    game: GameData

    @cached_property
    def sources(self) -> dict[str, dict[str, float]]:
        """The raw per-place stacks the sidecar wrote: player, storage, machine -- and,
        from schema 19, crate: the death and dismantle crates lying on the ground, which
        counted into ``machine`` until then. A pre-19 projection simply has no ``crate``
        key, and every ``.get`` below reads that as an empty bucket."""
        return self.projection.get("inventories", {}) or {}

    def stock(self) -> dict[str, float]:
        """What the player can actually spend: carried + storage + Dimensional Depot.

        Deliberately EXCLUDES machine buffers. Summing every stack in the world gives
        Water 5,556,375 and Fuel 1,048,762 -- pipe and machine contents in litres --
        so a build-cost check against that would say anything is affordable. Fluids
        are scaled to m3 here; the sidecar reports raw litres.

        Also excludes the ``crate`` bucket, and that is a decision stated rather than an
        oversight: a death crate's contents are recoverable -- schema 19 moved them out of
        ``machine`` for exactly that reason -- but a crate deletes itself the moment it is
        emptied and exists because something went wrong, so counting its contents as
        affordable would have a build plan quietly depending on the player walking back to
        where they died. ``/api/crates`` itemises what is out there; nothing here spends it.
        """
        out: dict[str, float] = {}
        sources = [
            self.sources.get("player", {}),
            self.sources.get("storage", {}),
            self.projection.get("depot", {}),
        ]
        for source in sources:
            for item, amount in source.items():
                out[item] = out.get(item, 0.0) + amount
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        return out

    def machine_buffers(self) -> dict[str, float]:
        """Material sitting in machine inputs, outputs and pipes. Not spendable.

        No longer includes the crates on the ground: schema 19 gave those their own
        bucket, so this is at last only what its name says. On a pre-19 projection the
        crates are still in here, indistinguishably, which is the shape that projection
        actually wrote.
        """
        out = dict(self.sources.get("machine", {}))
        for item in list(out):
            it = self.game.items.get(item)
            if it is not None and it.is_fluid:
                out[item] /= 1000.0
        return out
