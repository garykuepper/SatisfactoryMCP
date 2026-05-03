"""Optimizer correctness: the guards, and the byproduct rule that motivates it all."""

from __future__ import annotations

import pytest

from satisfactory_mcp.planning.optimize import (
    MW,
    Process,
    Scenario,
    build_processes,
    free_lunch_audit,
    solve,
)

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
WATER = "Desc_Water_C"
PLASTIC = "Desc_Plastic_C"


def _plastic(game, recipes, exports, sinks=True, crude=300.0):
    return solve(
        Scenario(
            game=game,
            recipes=recipes,
            objective="max_item",
            target_item=PLASTIC,
            raw_caps={CRUDE: crude, WATER: 1e6},
            exports=exports,
            allow_sinks=sinks,
            grid_import_mw=1e5,
        )
    )


def test_free_lunch_audit_returns_exactly_zero(game, state):
    """Strip every matter source; MW must be exactly 0. A non-zero result means some
    cycle creates matter from nothing."""
    ok, value = free_lunch_audit(
        Scenario(
            game=game,
            recipes=[r.cls for r in state.unlocked_recipes("part")],
            buildings_available=state.unlocked_building_ids,
        )
    )
    assert ok, f"free lunch of {value} MW"


def test_duplicate_process_id_is_fatal(game, monkeypatch):
    """A duplicate pid merges two columns and yields a plausible, mass-balanced,
    WRONG answer -- exactly the bug that made one miner produce both coal and
    sulfur. It must raise, not warn."""
    import satisfactory_mcp.planning.optimize as opt

    dupe = Process(pid="same", kind="recipe", label="a", rates={}, mw=0.0)
    monkeypatch.setattr(opt, "recipe_processes", lambda sc: [dupe, dupe])
    monkeypatch.setattr(opt, "extractor_processes", lambda sc: [])
    monkeypatch.setattr(opt, "generator_processes", lambda sc: [])
    with pytest.raises(AssertionError, match="duplicate process id"):
        build_processes(Scenario(game=game, recipes=[]))


def test_byproduct_with_no_consumer_makes_the_plan_infeasible(game):
    """Recipe_Plastic_C emits Heavy Oil Residue. HOR is a fluid, so it cannot be
    sunk; with no consumer the only feasible answer is zero plastic."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC,))
    assert sol.objective_value == pytest.approx(0.0, abs=1e-6)


def test_pretending_a_byproduct_is_exportable_is_the_naive_bug(game):
    """This is what a net>=0 formulation computes: 200 plastic in 10 machines, with
    nothing consuming 100 m3/min of residue. In game the pipe fills and it stalls."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC, "Desc_HeavyOilResidue_C"))
    assert sol.objective_value == pytest.approx(200.0)
    assert sol.machines_total == pytest.approx(10.0)


def test_correct_formulation_needs_more_machines(game):
    """Consuming the residue for real costs 17% more buildings than the naive answer,
    and routing it to a solid sink costs 25% more."""
    consumed = _plastic(
        game, ["Recipe_Plastic_C", "Recipe_ResidualFuel_C"], (PLASTIC, "Desc_LiquidFuel_C")
    )
    assert consumed.objective_value == pytest.approx(200.0)
    assert consumed.machines_total == pytest.approx(11.667, abs=1e-2)

    sunk = _plastic(game, ["Recipe_Plastic_C", "Recipe_PetroleumCoke_C"], (PLASTIC,))
    assert sunk.objective_value == pytest.approx(200.0)
    assert sunk.machines_total == pytest.approx(12.5, abs=1e-2)
    assert sunk.sunk.get("Desc_PetroleumCoke_C", 0) > 0
    assert any("AWESOME Sink" in w for w in sunk.warnings)


def test_machine_counts_are_minimal(game):
    """Without the phase-2 lexicographic pass, any larger machine count is equally
    optimal and the solver returned counts of 1e12."""
    sol = _plastic(game, ["Recipe_Plastic_C"], (PLASTIC, "Desc_HeavyOilResidue_C"))
    assert sol.machines_total < 100


def test_power_plant_cannot_import_grid_power(game):
    """A plant exporting MW must be self-contained, or max_mw is unbounded."""
    sol = solve(
        Scenario(
            game=game,
            recipes=["Recipe_Plastic_C"],
            objective="max_mw",
            exports=(MW,),
            raw_caps={CRUDE: 300},
            grid_import_mw=1e9,  # requested, but must be ignored
        )
    )
    assert sol.grid_import_mw == pytest.approx(0.0)


def test_recycled_plastic_rubber_cycle_is_handled(game, state):
    """These two recipes form a genuine 2-cycle and the player has both, which is why
    recursive chain expansion is unsound and an LP is required."""
    recycled_p = game.recipes["Recipe_Alternate_Plastic_1_C"]
    recycled_r = game.recipes["Recipe_Alternate_RecycledRubber_C"]
    assert state.has_recipe(recycled_p.cls) and state.has_recipe(recycled_r.cls)
    assert recycled_p.rate_of("Desc_Rubber_C") < 0 < recycled_p.rate_of(PLASTIC)
    assert recycled_r.rate_of(PLASTIC) < 0 < recycled_r.rate_of("Desc_Rubber_C")
    # The LP terminates and finds a finite optimum despite the cycle.
    sol = _plastic(
        game,
        [
            recycled_p.cls,
            recycled_r.cls,
            "Recipe_Alternate_HeavyOilResidue_C",
            "Recipe_Alternate_DilutedFuel_C",
        ],
        (PLASTIC,),
    )
    assert sol.ok
    assert 0 < sol.objective_value < 1e6
