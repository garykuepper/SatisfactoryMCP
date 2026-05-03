"""Build-site ranking.

The scoring itself is a judgement call, so these tests pin the things that would make
it *wrong* rather than merely differently weighted.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.spatial import geo
from satisfactory_mcp.spatial.ranking import WEIGHTS, rank_sites


def _node(x, y, z=0, rate=120.0, purity="normal", tapped=False, reachable=True):
    return {
        "instance": f"n{x}_{y}",
        "x": x,
        "y": y,
        "z": z,
        "rate": rate,
        "purity": purity,
        "kind": "node",
        "tapped": tapped,
        "reachable": reachable,
    }


def _cluster(nodes):
    (c,) = geo.cluster(nodes, link_m=10_000.0)
    return c


def test_fully_tapped_field_is_not_a_candidate():
    """A field whose nodes all have extractors offers nothing, whatever its size."""
    c = _cluster([_node(0, 0, tapped=True), _node(1000, 0, tapped=True)])
    assert rank_sites([c]) == []


def test_unreachable_capacity_is_excluded():
    """Well satellites are worthless until the Pressurizer is unlocked, so they must
    not inflate a site's throughput."""
    c = _cluster([_node(0, 0, reachable=False), _node(1000, 0, rate=60.0)])
    (scored,) = rank_sites([c])
    assert scored.raw["untapped_rate"] == pytest.approx(60.0)
    assert scored.raw["total_rate"] == pytest.approx(180.0)


def test_higher_throughput_wins_all_else_equal():
    big = _cluster([_node(0, 0, rate=480.0)])
    small = _cluster([_node(100_000, 0, rate=60.0)])
    ranked = rank_sites([small, big], infra=[(0.0, 0.0)])
    assert ranked[0].cluster is big


def test_closer_wins_when_throughput_is_equal():
    near = _cluster([_node(10_000, 0)])
    far = _cluster([_node(500_000, 0)])
    ranked = rank_sites([near, far], infra=[(0.0, 0.0)])
    assert ranked[0].cluster is near
    assert ranked[0].raw["distance_to_infra_m"] < ranked[1].raw["distance_to_infra_m"]


def test_purity_breaks_a_tie():
    pure = _cluster([_node(0, 0, rate=240.0, purity="pure")])
    impure = _cluster([_node(100_000, 0, rate=240.0, purity="impure")])
    ranked = rank_sites([pure, impure], infra=[(0.0, 0.0), (100_000.0, 0.0)])
    assert ranked[0].cluster is pure
    assert ranked[0].raw["purity_quality"] > ranked[1].raw["purity_quality"]


def test_raw_components_are_always_returned():
    """The single score is a starting point; a caller must be able to re-weight."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[(50_000.0, 0.0)])
    for key in (
        "untapped_rate",
        "total_rate",
        "nodes",
        "spread_m",
        "distance_to_infra_m",
        "purity_quality",
    ):
        assert key in scored.raw, key
    assert set(scored.normalised) == set(WEIGHTS)


def test_missing_infrastructure_does_not_score_as_zero_distance():
    """With no known buildings, distance must not silently become the best possible
    value -- that would rank every field as adjacent to the base."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[])
    assert scored.raw["distance_to_infra_m"] is None


def test_single_candidate_gets_neutral_normalisation():
    """A degenerate min-max spread must not arbitrarily favour or punish the only
    candidate."""
    c = _cluster([_node(0, 0)])
    (scored,) = rank_sites([c], infra=[(0.0, 0.0)])
    assert all(v == pytest.approx(0.5) for v in scored.normalised.values())


def test_altitude_sign_favours_a_field_above_the_consumer():
    """Positive means the fluid flows DOWNHILL to the refineries, needing no pumps.
    Getting this backwards would rank uphill fields as cheap."""
    high = _cluster([_node(0, 0, z=25_000)])
    (scored,) = rank_sites([high], consumer_z=-1_700.0)
    assert scored.raw["altitude_vs_consumer_m"] == pytest.approx(267.0)


def test_altitude_absent_without_a_consumer():
    c = _cluster([_node(0, 0, z=25_000)])
    (scored,) = rank_sites([c])
    assert scored.raw["altitude_vs_consumer_m"] is None


def test_custom_weights_change_the_order():
    near_small = _cluster([_node(10_000, 0, rate=120.0)])
    far_big = _cluster([_node(500_000, 0, rate=480.0)])
    default = rank_sites([near_small, far_big], infra=[(0.0, 0.0)])
    assert default[0].cluster is far_big
    distance_first = rank_sites(
        [near_small, far_big],
        infra=[(0.0, 0.0)],
        weights={"throughput": 0.1, "distance": -1.0},
    )
    assert distance_first[0].cluster is near_small
