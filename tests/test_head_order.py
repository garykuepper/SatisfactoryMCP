"""Ordering floors by fluid head, and paying for the risers.

`fluid_head` has always said "water can only be drawn at sea level, so putting its
extractors at the bottom with consumers above lets the rest of the stack fall" -- and then
ordered floors by chain depth anyway, which lifts water four storeys where two will do. A
tool that names a cost and then defaults to the arrangement that pays it is the gap.

Related: pumps were absent from the materials bill entirely, so a fluid-heavy plan
understated its own build.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.domain.planning.layout import build_layout, fluid_head, order_stages_by_head
from satisfactory_mcp.domain.planning.prepare import prepare

pytestmark = pytest.mark.integration

MK2_HEAD = 50.0

#: The saved plan the two tool tests read. Named once, because it is also what the skip
#: predicate has to look up -- and a plan name spelled twice is a skip that can stop matching
#: the call it is guarding.
REFERENCE_PLAN = "spire-coast-full"


@pytest.fixture
def plan(game, state):
    return prepare(
        game,
        state,
        dict(
            sources=["region:Spire Coast"],
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )


def _pumps(lay) -> int:
    return sum(d["pumps"] for d in fluid_head(lay, MK2_HEAD))


# ------------------------------------------------------------ the ordering


def test_head_order_never_costs_more_pumps_than_chain(plan, game):
    """The search minimises PIPE-STOREYS, which is a proxy -- pumps round up per line, so
    a 21% better proxy was worth 4% of pumps on the measured plan. A proxy that can be
    wrong in the small can be wrong in the large, so both stacks are built and counted and
    the loser is discarded."""
    chain = build_layout(game, plan.solution, order_floors_by="chain")
    head = build_layout(game, plan.solution, order_floors_by="head")
    assert _pumps(head) <= _pumps(chain)


def test_it_actually_lowers_the_water_lift(plan, game):
    """The specific complaint: blenders above refineries doubles the water lift."""
    chain = build_layout(game, plan.solution, order_floors_by="chain")
    head = build_layout(game, plan.solution, order_floors_by="head")

    def water(lay):
        return next(d["floors"] for d in fluid_head(lay, MK2_HEAD) if d["item"] == "Water")

    assert water(chain) == 4
    assert water(head) == 2


def test_water_extractors_stay_on_the_bottom_deck(plan, game):
    """Water is the one fluid that cannot be drawn anywhere but sea level, so a stack that
    lifts water to reach it is not a build, however good its arithmetic."""
    lay = build_layout(game, plan.solution, order_floors_by="head")
    production = [f for f in lay.floors if f.kind == "production"]
    pumps_on = [
        f.index for f in production if any(b.building_id == "Build_WaterPump_C" for b in f.blocks)
    ]
    assert pumps_on
    assert min(pumps_on) == production[0].index


def test_chain_order_is_still_the_default(plan, game):
    """It is a correctness property -- a consumer above its producer reads in build order
    -- so the physics mode has to be asked for."""
    default = build_layout(game, plan.solution)
    chain = build_layout(game, plan.solution, order_floors_by="chain")
    assert [f.stage for f in default.floors] == [f.stage for f in chain.floors]


def test_a_reordered_stack_keeps_every_block(plan, game):
    """Reordering must move floors, never lose one."""
    chain = build_layout(game, plan.solution, order_floors_by="chain")
    head = build_layout(game, plan.solution, order_floors_by="head")
    assert head.machines == chain.machines
    assert {b.key for b in head.blocks} == {b.key for b in chain.blocks}
    assert head.total_foundations == chain.total_foundations


def test_a_single_stage_plan_has_nothing_to_reorder(game, state):
    prepared = prepare(game, state, dict(objective="max_mw", exports=["MW"], sources=["north"]))
    if not prepared.ok:
        pytest.skip("no plan")
    blocks = build_layout(game, prepared.solution).blocks
    order, _ = order_stages_by_head(blocks, build_layout(game, prepared.solution).buses)
    assert sorted(order) == sorted({b.stage for b in blocks})


# ------------------------------------------------------------ paying for it


def test_a_riser_reports_metres_and_pumps(plan, game):
    """Metres come from the floors actually crossed, not storeys times an assumed storey,
    and head per pump from mDesignPressure."""
    lay = build_layout(game, plan.solution, order_floors_by="chain")
    water = next(d for d in fluid_head(lay, MK2_HEAD) if d["item"] == "Water")
    assert water["metres"] > 0
    assert water["pumps_per_line"] >= 1
    assert water["pumps"] == water["pumps_per_line"] * water["lines"]


def test_a_falling_pipe_needs_no_pumps(plan, game):
    lay = build_layout(game, plan.solution, order_floors_by="head")
    for row in fluid_head(lay, MK2_HEAD):
        if row["floors"] < 0:
            assert row["pumps"] == 0


def test_an_unknown_pump_head_reports_no_count(plan, game):
    """Rather than dividing by zero or inventing a tier."""
    lay = build_layout(game, plan.solution)
    assert all(d["pumps"] == 0 for d in fluid_head(lay, 0.0))


# The two tool tests below need the reference plan, and they used to skip on the TOOL'S
# ANSWER: any string starting with "! " was read as "not saved on this machine". That
# predicate is far wider than the condition it names. ``plan_layout`` refuses with "! " for a
# plan whose solution will not load, for an unknown ``detail``, for a layout it cannot build --
# so a real regression in any of those turned this file green with a reassuring skip reason
# instead of red, which is the one outcome a test must never have. A skip is a claim about the
# MACHINE, so it has to be decided by asking the machine.
#
# Skip on the plan store, exactly as ``test_modules.py`` does, and then assert the call did not
# refuse. The ``live`` fixture is what holds that store -- a plan is saved beside the world it
# was planned for -- which is also why ``game`` alone could never have answered the question.


def _needs_reference_plan(live) -> None:
    stored = live.plans.find(REFERENCE_PLAN)
    if stored is None:
        pytest.skip(f"the {REFERENCE_PLAN} plan is not saved on this machine")


def test_the_bill_now_charges_the_risers(game, live):
    """They were missing entirely, so a fluid-heavy plan understated its own build."""
    _needs_reference_plan(live)
    out = srv.plan_layout(plan=REFERENCE_PLAN, detail="materials", order_floors_by="head")
    assert not out.startswith("! "), out
    assert "for the fluid risers" in out
    assert "Pipeline Pump" in out


def test_the_tool_quotes_a_pump_tier_it_can_build(game, live):
    _needs_reference_plan(live)
    out = srv.plan_layout(plan=REFERENCE_PLAN, limit=3)
    assert not out.startswith("! "), out
    assert "risers need at least" in out
    assert "Pipeline Pump Mk.2" in out
    assert "LOWER bound" in out
