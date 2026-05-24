"""Solving a plant in pieces: three loops instead of one.

A module is a plan with no nodes of its own and declared inputs. `supplied={item: rate}`
hands it what another plan makes, as a free raw input up to that rate -- which is exactly
how a hand reconciliation works, and exactly where one goes wrong.

Measured on the reference plant, the three modules chained come to 765 machines and
99,386 MW against 787 and 99,730 for a single solve: decomposition costs **0.35%**, because
each module optimises locally. That is the number that makes it a choice.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.planning.prepare import prepare
from satisfactory_mcp.planning.scenario import build_scenario

pytestmark = pytest.mark.integration

#: A bbox in open water: a valid selector that selects no nodes, which is what makes a
#: module a module. Water survives it, because water is placeless and has no node anyway.
OCEAN = ["bbox:-3900,3400,-3800,3500"]
NORECYC = ["Alternate: Recycled Plastic", "Alternate: Recycled Rubber"]


@pytest.fixture
def live(game):
    from satisfactory_mcp.app import _state

    return _state(None, None)


@pytest.fixture
def rig(game, live):
    return prepare(
        game,
        live,
        dict(
            objective="max_item",
            target_item="Fuel",
            exports=["Fuel", "Polymer Resin"],
            sources=["region:Spire Coast"],
            extractor_clocks=[1, 1.5, 2, 2.5],
            allow_sinks=False,
            exclude_recipes=[
                "Turbofuel",
                "Alternate: Compacted Coal",
                "Coal-Powered Generator",
                *NORECYC,
            ],
            water_extractors=64,
        ),
    )


def _resin_plant(game, live, resin):
    return prepare(
        game,
        live,
        dict(
            objective="min_power",
            exports=["Plastic", "Rubber"],
            export_minimums={"Plastic": 600, "Rubber": 250},
            sources=OCEAN,
            allow_sinks=False,
            exclude_recipes=NORECYC,
            supplied={"Polymer Resin": resin},
            water_extractors=12,
        ),
    )


# ------------------------------------------------------------ a module solves alone


def test_a_module_has_no_nodes_and_still_solves(game, live, rig):
    """No extraction of its own -- everything it needs is declared."""
    plant = _resin_plant(game, live, rig.solution.exports["Desc_PolymerResin_C"])
    assert plant.ok
    assert plant.request.node_rows == []
    assert not any(
        p["kind"] == "extractor" and "Oil" in p["label"] for p in plant.solution.processes
    )


def test_it_reproduces_the_hand_built_resin_plant(game, live, rig):
    """30 Residual Plastic + 13 Residual Rubber, derived rather than reasoned out."""
    plant = _resin_plant(game, live, rig.solution.exports["Desc_PolymerResin_C"])
    built = {r["label"]: r["machines"] for r in plant.solution.processes if r["machines"]}
    assert built.get("Residual Plastic") == 30
    assert built.get("Residual Rubber") == 13


def test_the_supplied_input_binds(game, live, rig):
    """If it did not, the module would be inventing feedstock."""
    resin = rig.solution.exports["Desc_PolymerResin_C"]
    plant = _resin_plant(game, live, resin)
    assert plant.solution.raw_used["Desc_PolymerResin_C"] == pytest.approx(resin, rel=1e-4)
    assert any("capped at" in b for b in plant.solution.binding)


# ------------------------------------------------------------ the boundary trap


def test_an_exported_rate_can_be_handed_straight_on(game, live, rig):
    """`Solution.exports` is rounded to 4dp, so the rig exports 2299.9998 Polymer Resin
    while the resin plant's demand needs exactly 2300. Fed as an exact cap that is
    INFEASIBLE -- a whole module lost to two ten-thousandths. raw_caps carries the same
    kind of tolerance phase 2 already uses when it pins an objective."""
    resin = rig.solution.exports["Desc_PolymerResin_C"]
    assert resin < 2300.0, "the rounding that used to break the chain"
    assert _resin_plant(game, live, resin).ok


def test_the_slack_is_far_below_anything_physical(game, live):
    """It must fix rounding without quietly granting material."""
    req = build_scenario(
        game,
        live,
        objective="min_power",
        exports=["Plastic"],
        sources=OCEAN,
        supplied={"Polymer Resin": 2300.0},
    )
    granted = req.scenario.raw_caps["Desc_PolymerResin_C"]
    assert 2300.0 < granted < 2300.01


# ------------------------------------------------------------ what it costs


def test_three_loops_cost_a_little_optimality(game, live, rig):
    """Each module optimises locally, so the chain cannot beat the joint solve. Measured
    at 0.35% on this plant -- small enough that building in modules is nearly free, and
    the number is what makes that a decision rather than a hunch."""
    stored = live.plans.find("spire-coast-full")
    if stored is None:
        pytest.skip("the reference plan is not saved on this machine")
    fuel = rig.solution.exports["Desc_LiquidFuel_C"]
    resin = rig.solution.exports["Desc_PolymerResin_C"]
    hall = prepare(
        game,
        live,
        dict(
            objective="max_mw",
            exports=["MW"],
            sources=OCEAN,
            exclude_recipes=["Turbofuel", "Coal-Powered Generator"],
            supplied={"Fuel": fuel},
            water_extractors=0,
        ),
    )
    plant = _resin_plant(game, live, resin)
    assert hall.ok and plant.ok
    chained = rig.solution.net_mw + hall.solution.net_mw + plant.solution.net_mw
    single = prepare(game, live, dict(stored.kwargs())).solution.net_mw
    assert chained < single, "decomposition cannot beat a joint solve"
    assert chained > single * 0.99, "and should not lose much"


# ------------------------------------------------------------ honesty


def test_a_module_plan_says_its_inputs_are_unpriced(game):
    """advisor.py records what forgetting this costs: a basket fed in as free raw inflated
    a baseline from 92,269 MW to 171,882. Correct for a module, badly wrong as a
    whole-plant comparison."""
    out = srv.plan_factory(
        objective="min_power",
        exports=["Plastic", "Rubber"],
        export_minimums={"Plastic": 600, "Rubber": 250},
        sources=OCEAN,
        exclude_recipes=NORECYC,
        supplied={"Polymer Resin": 2300},
        water_extractors=12,
        limit=3,
    )
    assert "MODULE PLAN" in out
    assert "arrive free" in out
    assert "NOT comparable with a whole-plant plan" in out
    assert "must export at least these rates" in out


def test_power_cannot_be_supplied_as_an_item(game, live):
    """MW is the grid pseudo-item and has its own import allowance; letting it in here
    would be a second, unpriced way to draw power."""
    req = build_scenario(
        game,
        live,
        objective="min_power",
        exports=["Plastic"],
        sources=OCEAN,
        supplied={"MW": 1000},
    )
    assert any("supplied" in e for e in req.export_errors)


def test_supplied_changes_the_plan_id(game, live):
    kw = dict(objective="min_power", exports=["Plastic"], sources=OCEAN)
    a = build_scenario(game, live, **kw)
    b = build_scenario(game, live, supplied={"Polymer Resin": 2300}, **kw)
    assert a.plan_id != b.plan_id
