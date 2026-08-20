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
    nodes = srv.search_resource_nodes(sources=["near:0,-2000@900"], mode="nodes", limit=3)
    assert "no nodes" not in nodes and "!" not in nodes.splitlines()[0]
    assert "0 machines" not in srv.factory_query(factory=f"{BASE}@200", of="summary")


@pytest.mark.parametrize(
    ("call", "wanted"),
    [
        (
            lambda: srv.search_resource_nodes(sources=["near:0,-2000,900"], mode="nodes", limit=3),
            "near:0,-2000@900",
        ),
        (lambda: srv.search_resource_nodes(sources=["near:me,500"], mode="nodes"), "near:me@500"),
        (lambda: srv.factory_query(factory=f"{BASE},200", of="summary"), f"{BASE}@200"),
    ],
)
def test_the_retired_comma_radius_errors_and_names_its_replacement(call, wanted):
    """A radius as a third comma value is gone from both sides. It must not resolve to a
    circle it no longer describes, and the error has to carry the rewrite: a stored plan
    written in the old grammar is the thing that meets this message."""
    out = call()
    assert "@" in out and wanted in out, out
    assert "Write near:" in out or "radius" in out


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
