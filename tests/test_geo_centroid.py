"""Centroid and spread — one implementation, previously four.

`graph/identity.py` and `graph/query.py` each carried the same four lines; `diff` had a
named `_centroid`; `app`, `select` and `trunks` inlined theirs again. `geo.Cluster` had
had both all along, just shaped for node dicts rather than for (x, y) tuples, which is
how the copies got started.

The two behaviours worth pinning are the ones the copies disagreed about: what an empty
set answers, and whether spread double-counts.
"""

from __future__ import annotations

import math

import pytest

from satisfactory_mcp.spatial import geo

# ------------------------------------------------------------- centroid


def test_the_mean_is_the_mean():
    assert geo.centroid([(0.0, 0.0), (10.0, 20.0)]) == (5.0, 10.0)
    assert geo.centroid([(3.0, 4.0)]) == (3.0, 4.0)


def test_an_empty_set_is_None_and_not_the_origin():
    """(0, 0) is a real and important place here -- the world centre every compass
    direction is measured from -- so answering it for "no points" would be a plausible
    wrong location rather than an obvious one."""
    assert geo.centroid([]) is None
    assert geo.centroid(()) is None


def test_units_pass_through_untouched():
    """Centroid does no cm/m conversion. Callers hold centimetres, and a helper that
    silently converted would be the 100x bug this project already documents."""
    assert geo.centroid([(100_000.0, -200_000.0)]) == (100_000.0, -200_000.0)


# ------------------------------------------------------------- spread


def test_diameter_is_the_largest_pairwise_distance_in_metres():
    # 300 cm and 400 cm legs -> 500 cm -> 5 m.
    assert geo.diameter_m([(0.0, 0.0), (300.0, 400.0)]) == pytest.approx(5.0)


def test_fewer_than_two_points_have_no_spread():
    assert geo.diameter_m([]) == 0.0
    assert geo.diameter_m([(5.0, 5.0)]) == 0.0


def test_the_half_matrix_gives_the_same_answer_as_the_full_one():
    """The inline copies iterated `for a in points for b in points`, computing every pair
    twice plus the zero diagonal. Same answer, double the work -- at 563 machines, 317k
    distance calls instead of 158k. This pins that the cheaper form did not change any
    number while removing the duplication."""
    points = [(i * 137.0 % 9000, i * 911.0 % 7000) for i in range(60)]
    naive = max((math.dist(a, b) for a in points for b in points), default=0.0) / 100.0
    assert geo.diameter_m(points) == pytest.approx(naive)


def test_a_cluster_reuses_the_same_function(game):
    """geo.Cluster had this logic first; it now delegates rather than keeping a fifth
    copy shaped for node dicts."""
    members = [
        {"x": 0.0, "y": 0.0, "z": 0.0},
        {"x": 300.0, "y": 400.0, "z": 0.0},
    ]
    assert geo.Cluster(members).diameter_m == pytest.approx(
        geo.diameter_m([(0.0, 0.0), (300.0, 400.0)])
    )


# ------------------------------------------------------------- the callers


def test_a_factory_view_still_reports_a_centroid_and_spread(game, state):
    """The two graph modules were the biggest copies. Their output must be unchanged."""
    from satisfactory_mcp.graph.query import build_view

    machines = sorted(state.graph.machines())[:40]
    if len(machines) < 2:
        pytest.skip("not enough machines in this projection")
    view = build_view("probe", machines, state.graph, game, state.projection)
    assert view.centroid != (0.0, 0.0)
    assert view.spread_m > 0
    # And the spread is a real diameter over those machines, not a stale field.
    assert view.spread_m == pytest.approx(
        geo.diameter_m(
            [
                (r["pos"][0], r["pos"][1])
                for r in state._all_records()
                if r.get("pos") and r["instance"].rsplit(".", 1)[-1] in set(machines)
            ]
        )
    )
