"""One spelling per question, across the whole tool surface.

Every entry here cost a client a retry: it asked for a view with the word the last tool
used, or wrote a radius the way the last tool's help wrote it, and got an error. The old
spellings all still work -- renaming a parameter breaks stored calls -- so what is pinned
is that BOTH forms reach the same answer.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp import server as srv

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("game")]

BASE = "near:-1069,-1273"


def test_a_radius_is_written_the_same_way_on_both_sides_of_the_surface():
    """Node selectors spelled a circle `x,y,r` and machine selectors spelled it `x,y@r`,
    so a radius copied out of one tool's help was a parse error in the next."""
    comma = srv.search_resource_nodes(sources=["near:0,-2000,900"], mode="nodes", limit=3)
    at = srv.search_resource_nodes(sources=["near:0,-2000@900"], mode="nodes", limit=3)
    assert comma == at
    assert "no nodes" not in comma

    machines_at = srv.factory_query(factory=f"{BASE}@200", of="summary")
    machines_comma = srv.factory_query(factory=f"{BASE},200", of="summary")
    # The echoed selector differs because it echoes what was asked; the answer must not.
    assert machines_at.split("\n", 2)[2] == machines_comma.split("\n", 2)[2]


@pytest.mark.parametrize(
    ("call", "native", "value"),
    [
        (lambda **kw: srv.factory_query(factory=BASE + "@200", **kw), "of", "power"),
        (lambda **kw: srv.search_resource_nodes(resource="Coal", limit=3, **kw), "mode", "nodes"),
        (lambda **kw: srv.mam_research(limit=3, **kw), "status", "all"),
        (lambda **kw: srv.collected_from_world(limit=3, **kw), "mode", "census"),
    ],
)
def test_show_asks_for_a_view_wherever_a_tool_has_one(call, native, value):
    """Five spellings of "which view" -- show=, of=, detail=, mode=, status=. The native
    word stays, because it reads better in place and stored calls use it, but `show=`
    now works on all of them so one guess is enough."""
    assert call(show=value) == call(**{native: value})


def test_all_means_no_filter_wherever_kind_is_a_filter():
    """`kind="all"` was accepted by list_buildings and search_recipes and rejected by the
    other two, so the same word was a vocabulary on one tool and an error on the next."""
    here = {"near": "-1069,-1273", "radius_m": 300.0, "limit": 3}
    assert srv.search_conduits(kind="all", **here) == srv.search_conduits(**here)
    assert srv.storage(kind="all", limit=3) == srv.storage(limit=3)


def test_query_is_accepted_wherever_a_name_filter_lives():
    """search_items and search_recipes take `query=`; mam_research and rank_unlocks spelled
    the same thing `search=`."""
    assert srv.mam_research(query="Depot", limit=5) == srv.mam_research(search="Depot", limit=5)


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
