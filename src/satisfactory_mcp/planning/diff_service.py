"""Everything ``diff_vs_save`` has to DECIDE before a delta can be written down.

``build_diff`` answers "what is missing", and it answers it against a scope and a
solution somebody else had to choose. That choosing was the half of the tool that was
not presentation: solve the plan, work out which machines even count as already built
(a named factory, or the one a stored plan was saved for), read the grid, and -- only
when a stage question was asked -- partition the plan into startup stages and match
them against the save.

The stage partition is deliberately conditional. Nothing is stored and nothing is
re-solved for it, but a diff nobody asked a stage question of should not pay the
context for one, and the numbering is only stable for a STORED plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.gamedata.model import GameData
from ..graph.resolve import resolve_factory
from ..graph.select import SelectorError
from ..save.state import WorldState
from .commission import Tracking, commission, track
from .diff import DiffReport, build_diff
from .prepare import PreparedPlan, prepare

__all__ = ["DiffVsSaveReport", "build_diff_report"]


@dataclass
class DiffVsSaveReport:
    """A solved plan, the save it was matched against, and the stages if asked."""

    prepared: PreparedPlan
    #: ``None`` when the plan failed or came back empty -- there is nothing to diff.
    rep: DiffReport | None = None
    power: dict = field(default_factory=dict)
    #: The startup partition matched against the save, only when a stage was asked for.
    tracking: Tracking | None = None
    #: What the scope costs the reader, when a factory narrowed what counts as built.
    scope_note: str = ""
    #: Said when a stored plan re-solves to a different plan_id than it was saved with.
    drift_note: str = ""
    #: Feasible, but the solve chose to build nothing. Distinct from a failure.
    empty: bool = False


def build_diff_report(
    g: GameData,
    st: WorldState,
    plan_kwargs: dict,
    *,
    objective: str = "",
    plan: str | None = None,
    plan_name: str = "",
    stage: int | None = None,
    factory: str | None = None,
) -> DiffVsSaveReport:
    """Solve ``plan_kwargs`` and match it against the save under an optional scope.

    A ``SelectorError`` from a named factory propagates, including the case where the
    selector resolves but every machine it named has since been dismantled: an empty
    scope is the caller's mistake, not a diff saying the plan is unbuilt.
    """
    prepared = prepare(g, st, plan_kwargs, objective_label=objective, diagnose=False)
    report = DiffVsSaveReport(prepared=prepared)
    if prepared.failure:
        return report
    req, sol = prepared.request, prepared.solution

    if not sol.processes:
        report.empty = True
        return report

    # A plan saved with for_factory carries its own scope, so `diff_vs_save(plan=...)`
    # already answers "how far along is THAT factory" without naming it again.
    scope_name = factory
    if scope_name is None and plan:
        stored = st.plans.find(plan)
        scope_name = (stored.factory or None) if stored else None

    scope = None
    if scope_name:
        resolved_name, machines = resolve_factory(st, scope_name)
        if not machines:
            raise SelectorError(
                f"{scope_name!r} resolved to no machines that still exist in this save"
            )
        scope = set(machines)
        report.scope_note = (
            f"scoped to {resolved_name!r} ({len(scope)} machines): everything outside it "
            "counts as not built, and nodes tapped by other factories are unavailable"
        )

    report.rep = rep = build_diff(g, st, sol, req, scope=scope)
    report.power = pw = st.power_report()

    # Stage detection is the same partition commission_plan emits, matched against the
    # save -- nothing is stored and nothing is re-solved. It is off unless asked for,
    # because the numbering is only stable for a STORED plan and because a diff that
    # nobody asked a stage question of should not pay the context for one.
    if plan or stage is not None:
        report.tracking = track(
            prepared,
            commission(prepared, g, pw["headroom_mw"], "power_report, nameplate"),
            rep,
            g,
            st,
            plan_name=plan_name,
        )
        if plan_name and (stored := st.plans.find(plan_name)) and stored.plan_id != req.plan_id:
            # The same drift list_plans reports, said where it bites hardest: a stage
            # number is a milestone the player remembers, and a re-solve against a moved
            # world can renumber the whole partition under them.
            report.drift_note = (
                f"plan {plan_name!r} was saved against plan_id {stored.plan_id} and "
                f"re-solves to {req.plan_id} -- the WORLD moved, so these stage numbers "
                "may not be the ones you were given before"
            )
    return report
