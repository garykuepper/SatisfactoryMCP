"""Belt and pipe runs as queryable things -- the projection's geometry, finally readable.

The failure this guards against was live and expensive: the projection has carried every
conveyor and pipeline polyline since schemas 12/13, the web map drew them, and no text
tool could see them -- so the assistant twice told the player a build did not exist when
the tools simply could not look. These tests pin the two halves of the fix: the domain
grouping in ``domain.world.conduits``, and the honesty rules of what the surface says --
mid-span crossings count, zeroes are printed, and geometry guesses are labelled as such.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio import rows as saverows
from satisfactory_mcp.domain.world import conduits

# ------------------------------------------------------------ synthetic worlds
#
# Hand-built projections, because every rule here is about SHAPE -- what joins, what
# counts as near, which end is which -- and a shape is easiest to reason about when the
# test states it in full. Real-world coverage rides on the fixture tests below.

#: Two belt pieces in one chain, end to start, then a second chain 500 m away whose one
#: piece is a LONG straight line -- the mid-span case that point-distance misses.
BELT_PROJECTION = {
    "belts": {
        "classes": ["Build_ConveyorBeltMk1_C", "Build_ConveyorBeltMk3_C"],
        "segments": [
            [7, 0, [[0, 0, 100], [1000, 0, 100]], None],
            [7, 1, [[1000, 0, 100], [2000, 0, 150]], None],
            [8, 0, [[0, 50000, 0], [50000, 50000, 0]], None],
        ],
    },
    "machines": [],
    "extractors": [],
    "generators": [],
}

#: Two pipe pieces meeting at a joint, claimed by one network. The second network is
#: empty of fluid, which is what a drained or half-built system gives.
PIPE_PROJECTION = {
    "pipes": {
        "classes": ["Build_Pipeline_C"],
        "networks": [{"id": 33, "fluid": "Desc_Water_C"}],
        "segments": [
            [0, 0, [[0, 0, 0], [3000, 0, 0]], -1, None],
            [0, 0, [[3000, 0, 0], [3000, 4000, 0]], -1, None],
        ],
    },
    "machines": [],
    "extractors": [],
    "generators": [],
}


def test_belt_pieces_group_by_chain_and_the_open_ends_win(game):
    runs = conduits.build_runs(BELT_PROJECTION, game)
    assert len(runs) == 2
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.ident == "chain:7"
    # 10 m flat, then 10 m across with a 0.5 m rise -- 3D length, not the plan view.
    assert two_piece.length_m == pytest.approx(10.0 + (10.0**2 + 0.5**2) ** 0.5)
    # The joint at (1000, 0) is internal; the extremities are the run's ends, oriented
    # input -> output because every piece's point order says so.
    assert (two_piece.a.x, two_piece.a.y) == (0, 0)
    assert (two_piece.b.x, two_piece.b.y) == (2000, 0)
    assert two_piece.directed


def test_a_mixed_tier_chain_shows_its_span_and_quotes_the_slowest_cap(game):
    runs = conduits.build_runs(BELT_PROJECTION, game)
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.label == "belt mk1-mk3"
    assert two_piece.rate == game.buildings["Build_ConveyorBeltMk1_C"].items_per_min


def test_distance_is_to_the_drawn_line_not_the_corner_points(game):
    """A 500 m straight belt has exactly two stored points. Measuring to the points
    alone calls its middle 250 m away -- the precise blindness that had describe_location
    deny a build that was there."""
    runs = conduits.build_runs(BELT_PROJECTION, game)
    long_run = next(r for r in runs if r.ident == "chain:8")
    assert long_run.dist_m(25000, 50000) == pytest.approx(0.0)
    counted = conduits.near_counts(runs, 25000, 50500, radius_m=10.0)
    assert counted == {"belt": 1, "pipe": 0}


def test_near_counts_always_answers_both_kinds(game):
    """The zero is the deliverable: 'no pipes here' must be a statement, not a missing
    key, because absence in the output is now read as absence in the world."""
    counted = conduits.near_counts(conduits.build_runs(BELT_PROJECTION, game), 0, 0, 5.0)
    assert counted == {"belt": 1, "pipe": 0}
    assert conduits.near_counts([], 0, 0, 5.0) == {"belt": 0, "pipe": 0}


def test_pipes_stay_one_run_per_piece_and_carry_their_network(game):
    runs = conduits.build_runs(PIPE_PROJECTION, game)
    assert [r.kind for r in runs] == ["pipe", "pipe"]
    assert all(r.fluid == "Desc_Water_C" and r.network == 33 for r in runs)
    assert runs[0].length_m == pytest.approx(30.0)


def test_an_unplugged_end_names_the_run_it_continues_into(game):
    """A mid-network joint is the ordinary case for pipes, not an unknown: the end
    names its neighbour's ident so a route can be followed piece to piece."""
    runs = conduits.build_runs(PIPE_PROJECTION, game)
    first, second = runs
    assert first.b.plugs == second.ident
    assert second.a.plugs == first.ident
    # The far ends touch nothing and say so.
    assert first.a.plugs is None
    assert second.b.plugs is None


def test_an_end_inside_a_machines_footprint_names_the_machine(game):
    projection = {
        **BELT_PROJECTION,
        "machines": [
            {
                "instance": "L:P.Build_ConstructorMk1_C_1",
                "cls": "Build_ConstructorMk1_C",
                "pos": [2100.0, 0.0, 100.0],
            }
        ],
    }
    runs = conduits.build_runs(projection, game)
    two_piece = next(r for r in runs if r.pieces == 2)
    assert two_piece.b.plugs == game.buildings["Build_ConstructorMk1_C"].name
    assert two_piece.a.plugs is None


def test_a_projection_without_conduit_tables_yields_no_runs(game):
    """Schema 11 and earlier. One empty answer, never a raise -- the same degradation
    every other reader of these tables promises."""
    assert conduits.build_runs({"machines": []}, game) == []


# ------------------------------------------------------------ the fixture world


def test_every_belt_chain_and_every_pipe_piece_becomes_a_run(game, projection):
    """The committed world, whole: nothing the map draws may be missing here, because
    'the tools could not see it' is the exact failure this module retires."""
    runs = conduits.build_runs(projection, game)
    chains = {seg.chain for seg in saverows.iter_belt_segments(projection)}
    pipes = sum(1 for _ in saverows.iter_pipe_segments(projection))
    belts = [r for r in runs if r.kind != "pipe"]
    assert len(belts) == len(chains)
    assert sum(1 for r in runs if r.kind == "pipe") == pipes
    assert all(r.length_m > 0 for r in runs)


def test_most_ends_on_the_reference_world_resolve_to_something(game, projection):
    """A guess rate is worth pinning loosely: on the reference save the nearest-port
    join plus the continuation join resolve the large majority of ends. If this drops
    sharply, the geometry join broke -- if it hits 100%, somebody started inventing."""
    runs = conduits.build_runs(projection, game)
    ends = [e for r in runs for e in (r.a, r.b)]
    plugged = sum(1 for e in ends if e.plugs is not None)
    assert 0.7 < plugged / len(ends) < 1.0


def test_the_tool_pages_the_offset_its_truncation_line_promises(game, monkeypatch):
    """The truncation envelope ends 'call again with offset=N', so the tool has to
    HAVE an offset -- a next step the caller cannot take is worse than no next step,
    and this surface already pages this way in list_buildings and commission_plan."""
    from satisfactory_mcp.domain.world.state import WorldState
    from satisfactory_mcp.interfaces.mcp.tools import spatial as stools

    # Five chains through one point, each a different length, so the longest-first
    # order is unambiguous and a page boundary is checkable by name.
    projection = {
        "header": {"save_identifier": "TEST-conduit-paging", "session_name": "t"},
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                [i, 0, [[0, i * 100, 0], [(i + 1) * 1000, i * 100, 0]], None] for i in range(5)
            ],
        },
        "machines": [],
        "extractors": [],
        "generators": [],
    }
    st = WorldState(projection=projection, game=game)
    monkeypatch.setattr(stools, "_state", lambda save=None, world=None: st)

    first = stools.search_conduits(near="0,0", radius_m=500, limit=2)
    assert "5 match(es), showing 2 from offset 0. 3 more: call again with offset=2" in first
    assert "chain:4" in first and "chain:2" not in first

    second = stools.search_conduits(near="0,0", radius_m=500, limit=2, offset=2)
    assert "5 match(es), showing 2 from offset 2. 1 more: call again with offset=4" in second
    assert "chain:2" in second and "chain:4" not in second


# ------------------------------------------------------------ the tool, live


@pytest.mark.integration
def test_the_tool_lists_runs_where_the_map_draws_them(game, live):
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="239,-1928", radius_m=200)
    assert "conduit run(s) within 200m" in out
    # The Spire Coast oil site: developed for 300+ hours, so SOMETHING runs there --
    # and if the player one day tears it all out, the zero form is still the contract.
    assert ("id\tkind\tlen" in out) or ("nothing runs there" in out)


@pytest.mark.integration
def test_open_ocean_answers_zero_not_silence(game, live):
    """The deliverable: 'no pipe here' as a statement the reader can trust, in both
    tools, because absence in the output was twice read as the tools being unable to
    see rather than the world being empty -- and it was the tools."""
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="-3500,3500", radius_m=200)
    assert "0 conduit run(s)" in out
    assert "nothing runs there" in out

    described = srv.describe_location(-3500, 3500)
    assert "conduits=0 belt run(s), 0 pipe run(s) within 200m" in described


@pytest.mark.integration
def test_describe_location_counts_what_runs_through_a_built_site(game, live):
    from satisfactory_mcp import server as srv

    out = srv.describe_location(239, -1928)
    assert "conduits=" in out
    assert "belt run(s)" in out and "pipe run(s)" in out


@pytest.mark.integration
def test_between_mode_requires_both_areas_and_says_so_in_the_scope(game, live):
    from satisfactory_mcp import server as srv

    out = srv.search_conduits(near="-3500,3500", to="-3400,3400", radius_m=50)
    assert "AND 50m of -3400,3400" in out
