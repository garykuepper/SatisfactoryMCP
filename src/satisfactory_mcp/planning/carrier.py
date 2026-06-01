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

``resolve_tiers`` is the other half of the same question: WHICH belt and pipe those
capacities come from. It lived in the layout tool, where a tier name arriving from an MCP
argument was normalised, looked up and defaulted in the middle of building a response.
None of that is presentation, and the dead-lookup bug it documents is the reason it wants
one home.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..docs.model import GameData
from ..save.state import WorldState

__all__ = ["Carrier", "TierChoice", "carrier_for", "resolve_tiers"]


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


@dataclass
class TierChoice:
    """Which belt and pipe a schematic is drawn against, and whether the caller said so.

    ``asked_belt``/``asked_pipe`` are not cosmetic: a tier the CALLER named has to reach
    the scenario as well as the schematic, while a resolved default must not, or every
    recalled plan reports an override the user never made.
    """

    belt_ipm: float = 0.0
    pipe_m3min: float = 0.0
    #: Display names, resolved to the fastest unlocked tier when the caller named none.
    belt_tier: str = ""
    pipe_tier: str = ""
    asked_belt: bool = False
    asked_pipe: bool = False
    #: Populated only when a named tier does not exist; everything above is then unset.
    errors: list[str] = field(default_factory=list)


def resolve_tiers(game: GameData, st: WorldState, belt_tier: str, pipe_tier: str) -> TierChoice:
    """The belt and pipe a layout should be planned against.

    A blank tier means "the fastest this save can actually build", which is a world
    question, not a default.
    """

    # Keyed on a NORMALISED tier token, because these lookups were dead. The keys used to
    # be `name.replace("Conveyor Belt ", "")`, which yields "Mk.5" -- while the parameter
    # defaults were "Mk5" and "Mk2". Nothing ever matched, every call silently fell back to
    # the hardcoded 780/600, and it looked correct only because those were the same
    # numbers. Passing belt_tier="Mk3" would have quietly planned at Mk5 speed.
    def _tier(name: str, prefix: str) -> str:
        return name.replace(prefix, "").replace(".", "").strip().casefold()

    belts = {
        _tier(b.name, "Conveyor Belt "): b.items_per_min
        for b in game.buildings.values()
        if b.native == st.BELT_NATIVE
    }
    pipes = {
        _tier(b.name, "Pipeline "): b.flow_m3_min
        for b in game.buildings.values()
        if b.native == st.PIPE_NATIVE and "Clean" not in b.name
    }
    tier_errors = [
        f"unknown {what}_tier {given!r}; known: {', '.join(sorted(table))}"
        for what, given, table in (("belt", belt_tier, belts), ("pipe", pipe_tier, pipes))
        if given and _tier(given, "") not in table
    ]
    if tier_errors:
        return TierChoice(errors=tier_errors)
    asked_belt, asked_pipe = bool(belt_tier), bool(pipe_tier)
    belt_tier, pipe_tier = _tier(belt_tier, ""), _tier(pipe_tier, "")
    # Blank means "what this save can actually build". A hardcoded Mk5/Mk2 default ran
    # unverified through an entire design session; it happened to be right, which is not
    # the same as being checked.
    best_belt, best_pipe = st.best_belt(), st.best_pipe()
    return TierChoice(
        belt_ipm=belts.get(belt_tier) or (best_belt[1] if best_belt else 780.0),
        pipe_m3min=pipes.get(pipe_tier) or (best_pipe[1] if best_pipe else 600.0),
        belt_tier=belt_tier or (game.buildings[best_belt[0]].name if best_belt else "Mk5"),
        pipe_tier=pipe_tier or (game.buildings[best_pipe[0]].name if best_pipe else "Mk2"),
        asked_belt=asked_belt,
        asked_pipe=asked_pipe,
    )
