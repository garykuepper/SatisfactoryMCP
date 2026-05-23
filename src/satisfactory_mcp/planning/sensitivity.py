"""What would unlocking a recipe be worth to THIS plan?

`advise_hard_drive` already answers this for the two options of one pending drive. The
question underneath is bigger and was being answered by hand: across every alternate the
player has NOT unlocked, which ones would actually change the factory they are building,
and by how much?

It is one counterfactual per candidate -- solve the plan, solve it again with the recipe
bolted on, report the difference -- reusing `advisor._solve_with` rather than growing a
second copy of that machinery.

Sweeping everything, because it is cheap
----------------------------------------
A solve on the measured Spire Coast plan takes **0.01 s**, so all 79 locked alternates
cost about a second. An earlier estimate of 1.5 s per solve suggested filtering candidates
by "does this recipe touch an item the plan already moves", which would have cut 79 to 21 --
and would have been a real risk: a recipe that opens a chain the plan cannot currently
reach touches none of its items by definition, and is exactly the interesting case. Cheap
enough to skip the cleverness.

A zero is an answer
-------------------
Most candidates change nothing, and saying so is the point. "You are not missing anything
on Circuit Board" is a decision, and it is the one a player otherwise gets by walking the
tree by hand. So the sweep reports how many were tried and how many moved the needle,
rather than only listing winners.

The optimistic assumption, stated
---------------------------------
A candidate's recipe may need a machine the player has unlocked but never built, or has
not unlocked at all. `advisor._needed_buildings` adds it to the buildable set so the
candidate is not judged infeasible for that reason -- which makes every delta here an
UPPER bound on what unlocking the recipe alone buys. The building it needs is named.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..docs.model import Recipe
from ..save.state import WorldState
from .advisor import _needed_buildings, _solve_with
from .optimize import Solution

__all__ = ["UnlockDelta", "sweep_unlocks"]


@dataclass
class UnlockDelta:
    """One locked recipe, and what adding it does to a plan."""

    recipe: str
    name: str
    #: The plan's own objective, before and after. Already sign-normalised by `solve`,
    #: so higher is better for max_* and lower is better for min_*.
    before: float
    after: float
    machines_before: float
    machines_after: float
    #: Buildings the recipe needs that the plan's world has not built. The delta assumes
    #: they exist, so this is what the number is conditional on.
    needs: list[str] = field(default_factory=list)
    #: Schematics that grant it -- how the player would actually get it.
    unlocked_by: list[str] = field(default_factory=list)
    #: Processes the counterfactual switches ON that the baseline did not use. This is
    #: what the gain actually DEPENDS on, and it is the difference between "+13.6%" and
    #: "+13.6% if you reintroduce the Turbofuel chain you deleted on purpose".
    activates: list[str] = field(default_factory=list)
    ok: bool = True

    @property
    def gain(self) -> float:
        """Improvement in the plan's objective. Positive is always better."""
        return self.after - self.before

    @property
    def machines(self) -> float:
        return self.machines_after - self.machines_before


@dataclass
class UnlockSweep:
    objective: str
    baseline: float
    rows: list[UnlockDelta] = field(default_factory=list)
    tried: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def movers(self) -> list[UnlockDelta]:
        return [r for r in self.rows if abs(r.gain) > self.tolerance]

    @property
    def tolerance(self) -> float:
        """Below this a delta is solver noise rather than a finding.

        Relative to the baseline, because an LP on a 107,000 MW plan does not return the
        same digits twice at the bottom end, and a 0.4 MW "improvement" listed above a
        genuine zero would be worse than saying nothing.
        """
        return max(1e-6, abs(self.baseline) * 1e-6)


def _better(objective: str, value: float) -> float:
    """Objective value oriented so that larger is always an improvement.

    ``Solution.objective_value`` is already sign-normalised for max/min, so a min_*
    objective needs flipping to make "gain" mean the same thing in both directions --
    otherwise a recipe that halves raw usage reports a large NEGATIVE gain and sorts last.
    """
    return -value if objective.startswith("min") else value


def sweep_unlocks(
    request,
    state: WorldState,
    candidates: list[Recipe] | None = None,
) -> UnlockSweep:
    """Re-solve ``request`` once per locked recipe and report what each is worth."""
    sc = request.scenario
    objective = sc.objective
    base: Solution = _solve_with(sc, state, [])
    running = {p["label"] for p in base.processes}
    out = UnlockSweep(objective=objective, baseline=_better(objective, base.objective_value))
    if not base.ok:
        out.notes.append("the plan itself is infeasible, so there is nothing to compare against")
        return out

    pool = candidates if candidates is not None else state.locked_alternates
    for recipe in pool:
        out.tried += 1
        needed = _needed_buildings(state, [recipe.cls])
        after = _solve_with(sc, state, [recipe.cls])
        row = UnlockDelta(
            recipe=recipe.cls,
            name=recipe.name,
            before=out.baseline,
            after=_better(objective, after.objective_value) if after.ok else out.baseline,
            machines_before=base.machines_total,
            machines_after=after.machines_total if after.ok else base.machines_total,
            needs=sorted(
                state.game.buildings[b].name if b in state.game.buildings else b
                for b in needed
                if state.built(b) == 0
            ),
            unlocked_by=sorted(
                state.game.schematics[s].name
                for s in (recipe.unlocked_by or ())
                if s in state.game.schematics
            ),
            # Mechanically exact, and better than the heuristic it replaced. The first
            # instinct was to flag "consumes an item that crosses no boundary", which is
            # fuzzy and picks the wrong culprit: Turbo Blend Fuel also drags in Sulfur and
            # Petroleum Coke, a bigger architectural change than the fuel return. Naming
            # what the solve SWITCHES ON needs no judgement and catches every case where a
            # headline gain is unavailable under the reader's own constraints.
            activates=sorted({p["label"] for p in after.processes} - running - {recipe.name})
            if after.ok
            else [],
            ok=after.ok,
        )
        out.rows.append(row)

    # Best first, and a tie on gain broken by fewer machines: two routes worth the same
    # power are not equally good to build.
    out.rows.sort(key=lambda r: (-r.gain, r.machines))
    return out
