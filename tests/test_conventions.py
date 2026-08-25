"""One spelling per question, across the whole tool surface.

Every entry here cost a client a retry: it asked for a view with the word the last tool
used, or wrote a radius the way the last tool's help wrote it, and got an error. Where an
alias was added, what is pinned is that BOTH forms reach the same answer. Where a spelling
was retired, what is pinned is that it ERRORS and names its replacement.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("game")]

BASE = "near:-1069,-1273"


def test_a_radius_is_written_the_same_way_on_both_sides_of_the_surface():
    """Node selectors spelled a circle `x,y,r` and machine selectors spelled it `x,y@r`,
    so a radius copied out of one tool's help was a parse error in the next. `@` is the
    one spelling now, and it is the one both sides accept."""
    nodes = srv.search_resource_nodes(sources=["near:0,-2000@900"], show="nodes", limit=3)
    assert "no nodes" not in nodes and "!" not in nodes.splitlines()[0]
    assert "0 machines" not in srv.factory_query(factory=f"{BASE}@200", show="summary")


@pytest.mark.parametrize(
    ("call", "wanted"),
    [
        (
            lambda: srv.search_resource_nodes(sources=["near:0,-2000,900"], show="nodes", limit=3),
            "near:0,-2000@900",
        ),
        (lambda: srv.search_resource_nodes(sources=["near:me,500"], show="nodes"), "near:me@500"),
        (lambda: srv.factory_query(factory=f"{BASE},200", show="summary"), f"{BASE}@200"),
    ],
)
def test_the_retired_comma_radius_errors_and_names_its_replacement(call, wanted):
    """A radius as a third comma value is gone from both sides. It must not resolve to a
    circle it no longer describes, and the error has to carry the rewrite: a stored plan
    written in the old grammar is the thing that meets this message."""
    out = call()
    assert "@" in out and wanted in out, out
    assert "Write near:" in out or "radius" in out


#: Every tool with more than one view of its own answer, and one value each. The value sets
#: differ per tool on purpose -- `show=` asks "which of YOUR views", not "which kind of
#: thing" -- so what is pinned here is the parameter, never a shared vocabulary.
@pytest.mark.parametrize(
    ("call", "value"),
    [
        (lambda **kw: srv.factory_query(factory=BASE + "@200", **kw), "power"),
        (lambda **kw: srv.search_resource_nodes(resource="Coal", limit=3, **kw), "nodes"),
        (lambda **kw: srv.mam_research(limit=3, **kw), "all"),
        (lambda **kw: srv.milestones(limit=3, **kw), "all"),
        (lambda **kw: srv.collected_from_world(limit=3, **kw), "census"),
        (lambda **kw: srv.factory_map(limit=3, **kw), "named"),
        (
            lambda **kw: srv.search_conduits(near="-1069,-1273", radius_m=300.0, limit=3, **kw),
            "runs",
        ),
    ],
)
def test_show_is_the_only_way_to_ask_for_a_view(call, value):
    """`of=`, `detail=`, `mode=` and `status=` were four more spellings of this one question,
    and giving each a `show=` alias left a client reading the schema meeting five rather than
    fewer. `show=` is the parameter now, on every tool that has a view at all."""
    assert not call(show=value).startswith("! ")


@pytest.mark.parametrize(
    ("call", "old", "value"),
    [
        (lambda **kw: srv.factory_query(factory=BASE + "@200", **kw), "of", "power"),
        (lambda **kw: srv.plan_layout(**kw), "detail", "materials"),
        (lambda **kw: srv.mam_research(limit=3, **kw), "status", "all"),
        (lambda **kw: srv.milestones(limit=3, **kw), "status", "all"),
        (lambda **kw: srv.collected_from_world(limit=3, **kw), "mode", "census"),
        (lambda **kw: srv.search_resource_nodes(resource="Coal", limit=3, **kw), "mode", "nodes"),
        (lambda **kw: srv.search_resource_nodes(resource="Coal", limit=3, **kw), "group", "nodes"),
    ],
)
def test_a_retired_view_spelling_names_show_and_echoes_what_was_written(call, old, value):
    """A retirement that only listed the valid set would leave the caller to work out which
    of their arguments was refused. The message carries the line to write instead."""
    out = call(**{old: value})
    assert out.startswith(f"! {old}={value!r} is retired"), out
    assert f"show={value!r}" in out, out


def test_all_means_no_filter_wherever_kind_is_a_filter():
    """`kind="all"` was accepted by list_buildings and search_recipes and rejected by the
    other two, so the same word was a vocabulary on one tool and an error on the next."""
    here = {"near": "-1069,-1273", "radius_m": 300.0, "limit": 3}
    assert srv.search_conduits(kind="all", **here) == srv.search_conduits(**here)
    assert srv.storage(kind="all", limit=3) == srv.storage(limit=3)


@pytest.mark.parametrize(
    "call",
    [
        lambda **kw: srv.search_items(limit=3, **kw),
        lambda **kw: srv.search_recipes(limit=3, **kw),
        lambda **kw: srv.mam_research(limit=3, **kw),
        lambda **kw: srv.milestones(limit=3, **kw),
    ],
)
def test_query_is_the_only_name_filter(call):
    """Matching a row by the text in its name is one question, and `search=` was a second
    spelling of it on three tools -- on two of which the tool is itself called `search_`."""
    assert not call(query="Depot").startswith("! ")


@pytest.mark.parametrize(
    ("call", "old", "new", "value"),
    [
        (lambda **kw: srv.mam_research(limit=3, **kw), "search", "query", "Depot"),
        (lambda **kw: srv.milestones(limit=3, **kw), "search", "query", "Depot"),
        (lambda **kw: srv.rank_unlocks(**kw), "search", "query", "Recycled"),
        (lambda **kw: srv.list_regions(**kw), "with_resource", "resource", "Coal"),
    ],
)
def test_a_retired_name_filter_names_its_replacement(call, old, new, value):
    """`with_resource=` filtered by resource type, which the rest of the surface spells
    `resource=`; `search=` filtered by name, which it spells `query=`."""
    out = call(**{old: value})
    assert out == f"! {old}={value!r} is retired -- write {new}={value!r} instead", out


def test_group_means_one_thing_now():
    """It was a real category filter on collected_from_world and a second spelling of the
    view on search_resource_nodes -- one parameter name, two unrelated questions."""
    assert "power_slug_blue" in srv.collected_from_world(group="power_slug_blue", limit=3)


def test_nowhere_the_map_names_is_spelled_two_ways():
    """ "off-map or ocean", "ocean/off-map" and "off the map" were three wordings of one
    fact, which reads as three facts."""
    from satisfactory_mcp.domain.spatial import regions as regions_mod

    for out in (
        srv.search_resource_nodes(resource="Iron Ore", limit=25),
        srv.rank_build_sites(resource="Iron Ore", limit=5),
        srv.storage(limit=25),
    ):
        assert "ocean/off-map" not in out
    assert regions_mod.OFF_MAP == "off-map or ocean"
