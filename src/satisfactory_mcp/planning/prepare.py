"""One place where a planning request becomes a solved plan.

``plan_factory``, ``plan_layout`` and ``diff_vs_save`` all run the same sequence: recall a
saved plan, merge the caller's overrides, build a scenario, reject an empty source
selection or an unusable export, solve, and on failure work out why. It was written out
three times.

That duplication was not harmless. ``plan_layout`` drifted from the other two and stopped
accepting ``extractor_clocks`` and ``water_extractors``, so it silently re-solved at
defaults and schematised a *different* plan than the one it was asked to draw -- 15,043 MW
against 83,737. Nothing in its output said arguments had been dropped, because from its own
point of view nothing had. Three copies of a pipeline drift; one does not.

**This module renders nothing.** A failure comes back as a headline plus notes and the tool
decides how to show it, so the sequence stays reusable by anything that is not an MCP tool
-- a test, a script, a future batch planner.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.gamedata.model import GameData
from ..save.state import WorldState
from . import supply
from .optimize import Solution, free_lunch_audit, solve
from .scenario import PlanRequest, build_scenario

__all__ = ["PlanFailure", "PreparedPlan", "prepare"]


@dataclass
class PlanFailure:
    """Why a plan could not be produced, in the caller's own words."""

    headline: str
    notes: list[str] = field(default_factory=list)


@dataclass
class PreparedPlan:
    """A solved plan, or the reason there is not one."""

    request: PlanRequest | None = None
    solution: Solution | None = None
    #: Name of the saved plan this came from, empty if the arguments were given directly.
    plan_name: str = ""
    #: Recall and override notices, plus anything the solve wants to say.
    notes: list[str] = field(default_factory=list)
    failure: PlanFailure | None = None
    #: free_lunch_audit result, only when the caller asked for it.
    audit_ok: bool = True
    audit_value: float = 0.0

    @property
    def ok(self) -> bool:
        return self.failure is None and self.solution is not None


def prepare(
    game: GameData,
    state: WorldState,
    plan_kwargs: dict,
    *,
    objective_label: str = "",
    audit: bool = False,
    diagnose: bool = True,
) -> PreparedPlan:
    """Build and solve, or explain why not.

    ``plan_kwargs`` is already merged -- recall and override live in the tool layer,
    because only it knows which arguments a given tool exposes.

    ``diagnose`` runs the supply probe on failure. It costs one extra solve and only on
    the infeasible path, which is why it is on by default: a bare INFEASIBLE was the
    single most-reported problem with this surface.
    """
    from ..spatial.select import SELECTOR_HELP
    from .scenario import EXPORT_HELP

    request = build_scenario(game, state, **plan_kwargs)
    prepared = PreparedPlan(request=request)

    selection = request.selection
    if selection.errors and not selection.nodes:
        prepared.failure = PlanFailure("no sources selected", [*selection.errors, SELECTOR_HELP])
        return prepared

    if request.export_errors:
        prepared.failure = PlanFailure("unusable exports", [*request.export_errors, EXPORT_HELP])
        return prepared

    if audit:
        prepared.audit_ok, prepared.audit_value = free_lunch_audit(request.scenario)

    solution = solve(request.scenario)
    if not solution.ok:
        notes = list(solution.warnings)
        if diagnose:
            notes += supply.describe(
                supply.diagnose(request, game, state.unlocked_building_ids), game
            )
        # The trailing hint is the CALLER's: plan_factory explains byproduct balance,
        # while plan_layout and diff_vs_save defer to it rather than repeating a
        # diagnosis they did not run. Wording is presentation and stays with the tool.
        label = objective_label or plan_kwargs.get("objective", "")
        prepared.failure = PlanFailure(f"INFEASIBLE ({label})", notes)
        return prepared

    prepared.solution = solution
    prepared.notes = list(solution.warnings)
    # An all-zero solve is OPTIMAL and useless, and it reads as success: "buildings=0,
    # exports:" with no complaint. It happens whenever a needed input is capped out of
    # existence -- water_extractors=0 on an aluminium plan, say -- where every recipe is
    # present and unlocked so `unmakeable` finds nothing to report. The supply probe is
    # run for the same reason it is run on INFEASIBLE: a bare zero is not a diagnosis.
    if solution.machines_total <= 0:
        prepared.notes.append(
            "this plan is EMPTY -- it solved to zero machines. That is optimal, not "
            "broken: nothing can be made under these arguments, so making nothing is the "
            "best available answer"
        )
        if diagnose:
            prepared.notes += supply.describe(
                supply.diagnose(request, game, state.unlocked_building_ids), game
            )
    # An export nothing produces is pinned to 0 rather than conjured, but zero output is
    # a quiet answer, so the reason is said out loud on the success path too.
    for line in supply.unmakeable(request, game):
        prepared.notes.append(line + " -- it is pinned to 0 in this plan")
    return prepared
