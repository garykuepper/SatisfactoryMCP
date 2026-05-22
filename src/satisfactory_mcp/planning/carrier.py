"""Which carrier moves an item, and how many parallel lines it takes.

Four lines of arithmetic that were written three times. ``layout`` had ``_carrier`` and
``_lines_for``; ``optimize._logistics`` inlined both; and ``Item.unit`` had already
centralised the unit string that all of them re-derived from ``is_fluid`` anyway.

The tell was the epsilon. Both copies computed ``ceil(rate / capacity - 1e-9)`` -- the
same non-obvious fudge, needed because a rate that divides exactly (1,560 items/min over
a 780/min belt) otherwise rounds up to 3 lines through floating-point drift. Nobody
arrives at that independently; it was copied, and copies drift.

They had in fact already drifted, on ``capacity <= 0``: layout returned 1 line, optimize
returned ``None``. Unreachable, because both filter to a positive rate against a capacity
that comes from a belt or pipe tier lookup with a non-zero fallback -- but a divergence
sitting in duplicated code is a bug waiting for the day it becomes reachable. Resolved
here toward layout's answer, since a line count feeds block splitting and ``None`` there
would need a guard at every use.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..docs.model import GameData

__all__ = ["Carrier", "carrier_for"]


@dataclass(frozen=True)
class Carrier:
    """A belt or a pipe, at a chosen tier."""

    kind: str  # belt | pipe
    unit: str
    capacity: float

    @property
    def fluid(self) -> bool:
        return self.kind == "pipe"

    def lines_for(self, rate: float) -> int:
        """Parallel lines needed to move ``rate``. Never fewer than one.

        The ``- 1e-9`` is load-bearing: 1,560 items/min over a 780/min belt is exactly
        two lines, and without the epsilon binary rounding turns it into three.
        """
        if self.capacity <= 0:
            return 1
        return max(1, math.ceil(rate / self.capacity - 1e-9))


def carrier_for(game: GameData, item: str, belt_ipm: float, pipe_m3min: float) -> Carrier:
    """The carrier for one item. Fluids go by pipe, everything else by belt."""
    it = game.items.get(item)
    if it is not None and it.is_fluid:
        return Carrier("pipe", it.unit, pipe_m3min)
    # `Item.unit` is the authority on the string, but an item the dump does not know
    # still has to be carried somehow, and a solid belt is the safe assumption: it is
    # what every non-fluid uses and it never silently promotes something to a pipe.
    return Carrier("belt", it.unit if it is not None else "/min", belt_ipm)
