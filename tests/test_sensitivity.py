"""What a locked recipe would be worth to a specific plan.

`advise_hard_drive` answered this for the two options of one pending drive. The question
underneath -- across every alternate NOT unlocked, which would change the factory being
built -- was answered by hand, by tracing the recipe tree.

The behaviour worth pinning is that a ZERO is a result. Most candidates change nothing,
and "you are not missing anything here" is the decision the hand-walk was producing.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.planning.scenario import build_scenario
from satisfactory_mcp.planning.sensitivity import sweep_unlocks

pytestmark = pytest.mark.integration

SPIRE = dict(
    objective="max_mw",
    sources=["region:Spire Coast"],
    exports=["MW"],
    extractor_clocks=[1.0, 1.5, 2.0, 2.5],
)


@pytest.fixture
def live(game):
    from satisfactory_mcp.app import _state

    return _state(None, None)


@pytest.fixture
def sweep(game, live):
    return sweep_unlocks(build_scenario(game, live, **SPIRE), live)


# ------------------------------------------------------------ the sweep


def test_every_locked_alternate_is_tried(sweep, live):
    """No relevance filter. A recipe that opens a chain the plan cannot currently reach
    touches none of its items by definition, and is exactly the interesting case -- and at
    0.01 s a solve, skipping the cleverness costs about a second."""
    assert sweep.tried == len(live.locked_alternates)
    assert sweep.tried > 50


def test_an_unlocked_recipe_is_never_a_candidate(sweep, live):
    """It would report a delta of zero and pad the table with things you already own."""
    tried = {r.recipe for r in sweep.rows}
    assert not (tried & live.available_recipe_ids)


def test_most_candidates_change_nothing_and_that_is_reported(sweep):
    """The zero is the answer. Reporting only winners would hide that 78 of 79 were
    checked and found irrelevant, which is the finding."""
    assert len(sweep.movers) < sweep.tried
    assert sweep.rows, "every candidate gets a row even when its gain is zero"


def test_the_baseline_matches_the_plan_it_is_measured_against(sweep, game, live):
    """A delta against a different baseline is not a delta. This is the mistake advisor.py
    documents: feeding raw_caps instead of real extractors inflated a baseline by 86%."""
    from satisfactory_mcp.planning.prepare import prepare

    plan = prepare(game, live, dict(SPIRE))
    assert sweep.baseline == pytest.approx(plan.solution.objective_value)


def test_gain_is_positive_for_better_whichever_way_the_objective_points(game, live):
    """`objective_value` is sign-normalised for max/min, so a min_* objective needs
    flipping -- otherwise a recipe that halves raw usage reports a large NEGATIVE gain and
    sorts last, which is exactly backwards."""
    from satisfactory_mcp.planning.sensitivity import _better

    assert _better("max_mw", 100.0) == 100.0
    assert _better("min_raw", 100.0) == -100.0
    # A min_raw plan that drops from 100 to 60 must read as a gain.
    assert _better("min_raw", 60.0) - _better("min_raw", 100.0) == pytest.approx(40.0)


def test_rows_are_ranked_best_first(sweep):
    gains = [r.gain for r in sweep.rows]
    assert gains == sorted(gains, reverse=True)


def test_noise_is_not_a_finding(sweep):
    """An LP on a 107,000 MW plan does not return the same digits twice at the bottom
    end, so a 0.4 MW 'improvement' listed above a genuine zero would be worse than
    silence."""
    assert sweep.tolerance > 0
    assert all(abs(r.gain) > sweep.tolerance for r in sweep.movers)


def test_a_search_narrows_the_candidate_pool(game, live):
    narrow = sweep_unlocks(
        build_scenario(game, live, **SPIRE),
        live,
        [r for r in live.locked_alternates if "Turbo" in r.name],
    )
    assert 0 < narrow.tried < len(live.locked_alternates)


# ------------------------------------------------------------ the finding


def test_turbo_blend_fuel_is_the_one_that_matters_here(sweep):
    """The measured answer on the reference plan: 1 of 79 moves it, by +13.6%. This is
    what an hour of hand-tracing produced, and it is the number that decides the build."""
    movers = sweep.movers
    assert len(movers) == 1
    top = movers[0]
    assert top.name == "Alternate: Turbo Blend Fuel"
    assert top.gain / sweep.baseline == pytest.approx(0.136, abs=0.01)


def test_a_candidate_names_the_machine_its_delta_assumes(sweep):
    """The delta is an UPPER bound wherever a machine is missing, so the condition has to
    travel with the number."""
    top = sweep.movers[0]
    assert "Blender" in top.needs


# ------------------------------------------------------------ the tool


def test_the_tool_reports_the_sweep(game):
    out = srv.rank_unlocks(**SPIRE)
    assert not out.startswith("! ")
    assert "gain\tvs base\talternate" in out
    assert "Turbo Blend Fuel" in out
    assert "candidates=79" in out


def test_it_flags_what_is_claimable_from_a_pending_drive(game, live):
    """The difference between "worth having" and "you can have it right now". Turbo Blend
    Fuel is worth +14,540 MW here AND sitting in drive 25."""
    out = srv.rank_unlocks(**SPIRE)
    on_offer = {r.cls for o in live.hard_drive_offers for opt in o.options for r in opt["recipes"]}
    if "Recipe_Alternate_TurboBlendFuel_C" not in on_offer:
        pytest.skip("that drive has been claimed")
    assert "claimable NOW" in out
    assert "drive 25" in out


def test_it_says_how_many_were_checked_and_found_irrelevant(game):
    out = srv.rank_unlocks(**SPIRE)
    assert "changed this plan" in out
    assert "worth nothing HERE" in out


def test_an_unmatched_search_refuses_rather_than_sweeping_everything(game):
    assert srv.rank_unlocks(search="no such recipe", **SPIRE).startswith("! no LOCKED")


def test_an_infeasible_plan_has_nothing_to_rank(game):
    out = srv.rank_unlocks(objective="max_mw", sources=["region:Nowhere"], exports=["MW"])
    assert "nothing to rank against" in out
