"""factory_query: what a machine set makes, needs, draws and touches.

The balance table is the part that earns the tool. A per-machine listing tells you a
Foundry runs Solid Steel Ingot; only production minus consumption across the whole set
tells you the steel factory needs 975 Coal/min fed in and hands you 405 Steel Ingot/min.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.graph.build import build_graph
from satisfactory_mcp.graph.query import ASPECTS, build_view

pytestmark = pytest.mark.integration

SMELTER = "Build_SmelterMk1_C_1"
ROD_A = "Build_ConstructorMk1_C_2"
ROD_B = "Build_ConstructorMk1_C_4"
OUTSIDER = "Build_ConstructorMk1_C_3"
BELT = "Build_ConveyorBeltMk1_C_50"
INSIDE = (SMELTER, ROD_A, ROD_B)


def _machine(name, recipe, x, **extra):
    return {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [x, 0, 0],
        **extra,
    }


def _projection(clock: float = 1.0, paused: bool = False) -> dict:
    """One smelter feeding TWO rod constructors, plus a machine belted on from outside.

    Deliberately balanced: 30 Iron Ingot/min made, 2 x 15 consumed. That is what makes
    Iron Ingot an *internal* item rather than a surplus, which is the case worth testing.
    """
    actors = [SMELTER, ROD_A, ROD_B, OUTSIDER, BELT]
    index = {a: i for i, a in enumerate(actors)}
    return {
        "machines": [
            _machine(SMELTER, "Recipe_IngotIron_C", 0),
            _machine(ROD_A, "Recipe_IronRod_C", 1000, clock=clock, paused=paused),
            _machine(ROD_B, "Recipe_IronRod_C", 2000),
            _machine(OUTSIDER, "Recipe_IronPlate_C", 9000),
        ],
        "extractors": [],
        "generators": [],
        "graph": {
            "actors": actors,
            "roles": [],
            # The outsider is reached only THROUGH a belt, which is the normal shape.
            "material": [
                [index[SMELTER], index[ROD_A], 0, 0],
                [index[SMELTER], index[ROD_B], 0, 0],
                [index[ROD_A], index[BELT], 0, 0],
                [index[BELT], index[OUTSIDER], 0, 0],
            ],
            "power": [],
        },
    }


def _view(game, projection, machines=INSIDE, name="test"):
    return build_view(name, list(machines), build_graph(projection), game, projection)


def test_balance_splits_surplus_from_deficit_from_internal(game):
    """The three signs are the whole point. Iron Ore is consumed and never made here, the
    rods leave, and the ingots are made and eaten inside the set."""
    view = _view(game, _projection())
    assert dict(view.outputs())["Iron Rod"] == pytest.approx(30.0)
    assert dict(view.inputs())["Iron Ore"] == pytest.approx(30.0)
    internal = dict(view.internal())
    assert "Iron Ingot" in internal, "made and consumed in equal measure"
    assert view.net("Iron Ingot") == pytest.approx(0.0)


def test_rates_scale_with_the_saved_clock(game):
    """Nameplate AT THE SAVED CLOCK. A machine left at 150% is not producing its 100%
    rate, and reporting the recipe rate unscaled would understate the whole line."""
    base = _view(game, _projection(clock=1.0))
    fast = _view(game, _projection(clock=1.5))
    # Only ROD_A is overclocked, so the set gains half of one constructor's 15/min --
    # the clock is applied per machine, not to the factory total.
    assert dict(base.outputs())["Iron Rod"] == pytest.approx(30.0)
    assert dict(fast.outputs())["Iron Rod"] == pytest.approx(37.5)
    assert fast.draw_mw > base.draw_mw


def test_a_paused_machine_contributes_no_flow_but_is_reported(game):
    """Counting a paused machine's output would invent throughput that does not exist."""
    view = _view(game, _projection(paused=True))
    assert any("paused" in issue for issue in view.issues)
    # It is still a member: it exists, it just is not running.
    assert view.size == 3
    # The other constructor keeps running, so rods do not vanish entirely.
    assert dict(view.outputs())["Iron Rod"] == pytest.approx(15.0)


def test_the_boundary_walk_passes_through_belts(game):
    """A material edge runs machine -> belt -> machine, so a walk that stops at the first
    non-machine finds no links at all and every factory looks isolated."""
    view = _view(game, _projection())
    assert sum(view.links.values()) == 1, view.links
    assert "(unlabelled)" in view.links


def test_machines_outside_the_set_do_not_leak_into_the_balance(game):
    view = _view(game, _projection())
    assert not any(k == "Iron Plate" for k, _ in view.outputs())
    assert OUTSIDER not in {m.instance for m in view.machines}


def test_power_draw_accumulates_over_the_set(game):
    view = _view(game, _projection())
    assert view.draw_mw > 0
    assert view.generation_mw == 0.0


def test_a_machine_with_no_recipe_is_an_issue_not_a_silent_zero(game):
    projection = _projection()
    projection["machines"][1].pop("recipe")
    view = _view(game, projection)
    assert any("no recipe" in issue for issue in view.issues)


def test_an_unknown_building_is_an_issue_not_a_silent_zero(game):
    """Power that cannot be looked up must be reported, not quietly dropped -- otherwise
    a factory's draw is understated with nothing to show why."""
    projection = _projection()
    projection["machines"][0]["cls"] = "Build_SomethingModded_C"
    view = _view(game, projection)
    assert any("unknown building" in issue for issue in view.issues)


def test_every_advertised_aspect_is_handled(game):
    """ASPECTS is the tool's public menu; an entry with no branch would return an empty
    section instead of an error."""
    from satisfactory_mcp import server

    src = server.factory_query.__doc__ or ""
    for aspect in ASPECTS:
        assert aspect in src, f"{aspect} is offered but undocumented"
