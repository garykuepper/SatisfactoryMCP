"""Persisted plans: what is stored, and how a recall merges with overrides.

The design decision worth pinning is that the REQUEST is stored, never the solution. A
solve depends on unlocked recipes, free nodes and built buildings, all of which move as
the game is played, so a stored solution would keep answering about a world that no
longer exists -- silently.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.planning.store import PLAN_ARGS, Plan, PlanStore

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path, monkeypatch):
    from satisfactory_mcp.planning import store as store_mod

    monkeypatch.setattr(store_mod.config, "plans_dir", lambda: tmp_path)
    return PlanStore(world_id="TESTWORLD")


class _FakeState:
    def __init__(self, plans):
        self.plans = plans


# ---------------------------------------------------------------- storage


def test_a_plan_round_trips_through_disk(store):
    store.put(
        "north oil",
        {"objective": "max_mw", "sources": ["north"], "limit": 25, "save": "x.sav"},
        plan_id="abc123",
        notes="the coast",
        factory="oil setup",
    )
    store.save()

    again = PlanStore.load("TESTWORLD")
    assert [p.name for p in again.plans] == ["north oil"]
    plan = again.plans[0]
    assert plan.plan_id == "abc123"
    assert plan.notes == "the coast"
    assert plan.factory == "oil setup"


def test_only_solve_shaping_arguments_are_stored(store):
    """`limit` is presentation and `save`/`world` say which file was read, not what was
    asked for. Storing them would make two identical requests compare unequal."""
    plan = store.put(
        "p",
        {"objective": "max_item", "limit": 25, "save": "x.sav", "world": "W", "sources": ["north"]},
        plan_id="x",
    )
    assert set(plan.args) <= set(PLAN_ARGS)
    assert "limit" not in plan.args
    assert "save" not in plan.args


def test_defaults_are_not_stored(store):
    """A stored plan should read as the request that was made, not a dump of every knob."""
    plan = store.put("p", {"objective": "max_mw", "sources": None, "exports": []}, plan_id="x")
    assert plan.args == {"objective": "max_mw"}


def test_saving_the_same_name_twice_updates_rather_than_duplicates(store):
    store.put("p", {"objective": "max_mw"}, plan_id="one")
    store.put("p", {"objective": "min_power"}, plan_id="two")
    assert len(store.plans) == 1
    assert store.plans[0].plan_id == "two"
    assert store.plans[0].args["objective"] == "min_power"


def test_plans_do_not_live_in_the_cache():
    """cache_prune wipes the cache tree, and a plan the player named is not
    regenerable."""
    from satisfactory_mcp import config

    assert config.plans_dir() != config.cache_dir()
    assert config.cache_dir() not in config.plans_dir().parents
    assert config.plans_dir() != config.labels_dir()


# ----------------------------------------------------------------- recall


def test_recall_returns_the_stored_arguments(store):
    store.put("p", {"objective": "min_power", "sources": ["north"]}, plan_id="x")
    kwargs, name, _ = srv._plan_kwargs(_FakeState(store), "p", dict(srv.PLAN_DEFAULTS))
    assert name == "p"
    assert kwargs["objective"] == "min_power"
    assert kwargs["sources"] == ["north"]


def test_a_default_valued_argument_does_not_clobber_the_plan(store):
    """THE trap. MCP fills defaults in before the tool sees them, so `objective` always
    arrives as "max_mw"; a naive merge would overwrite every recalled plan with it."""
    store.put("p", {"objective": "min_power"}, plan_id="x")
    supplied = dict(srv.PLAN_DEFAULTS)  # exactly what an untouched call looks like
    kwargs, _, notes = srv._plan_kwargs(_FakeState(store), "p", supplied)
    assert kwargs["objective"] == "min_power"
    assert not any("overridden" in n for n in notes)


def test_an_explicit_override_wins_and_says_it_was_not_saved(store):
    store.put("p", {"objective": "min_power", "sources": ["north"]}, plan_id="x")
    supplied = {**srv.PLAN_DEFAULTS, "sources": ["south"]}
    kwargs, _, notes = srv._plan_kwargs(_FakeState(store), "p", supplied)
    assert kwargs["sources"] == ["south"]
    assert kwargs["objective"] == "min_power", "untouched arguments still come from the plan"
    assert any("overridden" in n and "sources" in n for n in notes)
    assert any("not saved" in n for n in notes)


def test_recalling_an_unknown_plan_lists_what_exists(store):
    store.put("north oil", {}, plan_id="x")
    with pytest.raises(KeyError, match="north oil"):
        srv._plan_kwargs(_FakeState(store), "nope", dict(srv.PLAN_DEFAULTS))


def test_no_plan_name_passes_arguments_straight_through(store):
    supplied = {**srv.PLAN_DEFAULTS, "objective": "max_item"}
    kwargs, name, notes = srv._plan_kwargs(_FakeState(store), None, supplied)
    assert name == "" and notes == []
    assert kwargs["objective"] == "max_item"


def test_plan_defaults_cover_every_stored_argument():
    """A stored argument with no declared default could never be overridden, because
    the override test compares against PLAN_DEFAULTS."""
    assert set(PLAN_ARGS) == set(srv.PLAN_DEFAULTS)


def test_kwargs_filters_out_anything_no_longer_accepted():
    """A plan saved by an older build must not blow up a newer build_scenario call."""
    plan = Plan(name="p", args={"objective": "max_mw", "retired_knob": 7})
    assert plan.kwargs() == {"objective": "max_mw"}


# ----------------------------------------------------------------- scoping


def _diff_index(state, request, scope=None):
    from satisfactory_mcp.planning.diff import _index

    return _index(state, request, scope)


def test_scoping_limits_what_counts_as_already_built(game, state):
    """Unscoped, "you already have 12 of these" counts machines on the far side of the
    map that are busy doing something else -- the wrong answer to "how far along is the
    aluminium setup"."""
    from satisfactory_mcp.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    everything = _diff_index(state, request)
    total = sum(len(v) for v in everything.by_recipe.values())
    assert total > 0

    first = next(iter(everything.by_recipe.values()))[0]
    only_one = _diff_index(state, request, {first["instance"].rsplit(".", 1)[-1]})
    assert sum(len(v) for v in only_one.by_recipe.values()) == 1


def test_an_empty_scope_means_nothing_is_already_built(game, state):
    from satisfactory_mcp.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    index = _diff_index(state, request, set())
    assert not index.by_recipe
    assert not index.idle
    assert not index.by_generator
    assert not index.by_extractor_class


def test_a_node_tapped_by_another_factory_is_neither_reusable_nor_free(game, state):
    """It must drop out of BOTH. Left in `tapped` it would read as already built for
    this plan; moved to `free` it would plan a second miner onto an occupied node."""
    from satisfactory_mcp.planning.scenario import build_scenario

    request = build_scenario(game, state, objective="max_mw", exports=["MW"])
    unscoped = _diff_index(state, request)
    scoped = _diff_index(state, request, set())

    assert sum(len(v) for v in unscoped.tapped.values()) > 0
    assert not scoped.tapped, "no in-scope extractor taps anything"
    before = sum(len(v) for v in unscoped.free.values())
    after = sum(len(v) for v in scoped.free.values())
    assert after == before, "tapped nodes must not become free just because we narrowed"
