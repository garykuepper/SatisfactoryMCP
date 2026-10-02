"""Belt routing (spec ~/satisfactory/docs/superpowers/specs/2026-10-01-belt-routing-and-lifts-design.md).
Pure: hand-built blocks and floors, no game data."""
from __future__ import annotations

import math

import pytest

from satisfactory_mcp.core.gamedata.footprint import Packed
from satisfactory_mcp.domain.planning import routing as R
from satisfactory_mcp.domain.planning.layout import Block, Bus, Floor, Layout


def blk(key, x, y, n=2, w=8.0, d=10.0, inputs=None, outputs=None, folded=False, rotated=False):
    if folded:
        cols = math.ceil(n / 2)
        packed = Packed(count=n, columns=cols, rows=2, width_m=cols * w, depth_m=2 * d + 8.0,
                        foundations=0, folded=True, lane_m=8.0)
    else:
        packed = Packed(count=n, columns=n, rows=1, width_m=n * w, depth_m=d, foundations=0)
    b = Block(key=key, label=key, building_id="x", building="Constructor", recipe=None,
              machines=n, clock=1.0, part=1, parts=1,
              inputs=dict(inputs or {}), outputs=dict(outputs or {}), packed=packed)
    b.x_fnd, b.y_fnd, b.rotated = x, y, rotated
    return b


def floor(i, blocks, w=10, d=10, group=None):
    return Floor(index=i, kind="production", stage=i, height_m=16.0, blocks=blocks,
                 slab_side_m=w * 8.0, slab_depth_m=d * 8.0, group=group or f"G{i}")


def bus(item, carrier="belt"):
    return Bus(item=item, name=item, rate=1.0, carrier=carrier, unit="/min", lines=1)


def test_block_rect_is_the_true_footprint_in_2m_cells():
    assert R.block_rect(blk("a", 1, 1)) == (4, 4, 8, 5)            # 16 m x 10 m
    assert R.block_rect(blk("a", 1, 1, rotated=True)) == (4, 4, 5, 8)


def test_straight_row_input_below_output_above_west_end_first():
    p = R.manifold_ports(blk("a", 1, 1, inputs={"B": 1, "A": 1}, outputs={"P": 1}))
    assert p.inputs["A"] == [(x, 3) for x in range(4, 12)]          # first input, sorted
    assert p.inputs["B"] == [(x, 2) for x in range(4, 12)]          # second, one row out
    assert p.outputs == [[(x, 9) for x in range(4, 12)]]            # y0 + 5


def test_folded_row_input_in_the_lane_outputs_on_both_edges():
    p = R.manifold_ports(blk("f", 1, 1, n=4, inputs={"A": 1}, outputs={"P": 1}, folded=True))
    # 2 columns x 8 m = 16 m -> 8 cells long; depth 2*10+8 = 28 m -> 14 cells; lane middle int(28/2/2) = 7
    assert p.inputs["A"] == [(x, 11) for x in range(4, 12)]
    assert p.outputs == [[(x, 18) for x in range(4, 12)], [(x, 3) for x in range(4, 12)]]


def test_rotated_block_turns_its_belts_into_columns_south_end_first():
    p = R.manifold_ports(blk("r", 1, 1, inputs={"A": 1}, outputs={"P": 1}, rotated=True))
    assert p.inputs["A"] == [(3, y) for y in range(4, 12)]
    assert p.outputs == [[(9, y) for y in range(4, 12)]]


def test_walk_lanes_ring_plus_one_per_aisle_band():
    lower, upper = blk("a", 1, 1), blk("b", 1, 4)   # lower reserves 2 fnd -> top at fnd 3
    rows, cols = R.walk_lanes(floor(0, [lower, upper]))
    assert rows == {1, 38, 13} and cols == {1, 38}
    assert R.walk_lanes(floor(0, [lower]))[0] == {1, 38}   # no row above: no aisle lane


def test_strip_rows_skip_walk_lane_rows():
    rows = R.strip_rows(floor(0, [blk("a", 1, 1), blk("b", 1, 4)]))
    assert 1 not in rows and 13 not in rows and 38 not in rows
    assert rows[:3] == [0, 2, 3]


def _turns(path):
    dirs = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
    return sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)


def test_astar_straight_line():
    g = R.Grid(10, 3, set(), set(), set())
    assert R.astar(g, (0, 1), (9, 1)) == [(x, 1) for x in range(10)]


def test_astar_prefers_one_turn_over_a_staircase():
    path = R.astar(R.Grid(4, 4, set(), set(), set()), (0, 0), (3, 3))
    assert len(path) == 7 and _turns(path) == 1


def test_astar_goes_around_blocked_cells_and_may_end_on_blocked_ends():
    wall = {(2, y) for y in range(5) if y != 4}
    g = R.Grid(5, 5, wall | {(0, 0), (4, 0)}, set(), set())
    path = R.astar(g, (0, 0), (4, 0))
    assert path[0] == (0, 0) and path[-1] == (4, 0) and (2, 4) in path
    assert not (set(path[1:-1]) & g.blocked)


def test_astar_forced_lane_crossing_is_straight_and_costs_cross_cost():
    # Wall on row 3 with one gap at (2, 3), a lane cell; the only route crosses it vertically.
    # Fails if lane cells were impassable (None) or the crossing were free (cost check).
    g = R.Grid(7, 7, {(x, 3) for x in range(7) if x != 2}, {3}, set())
    path = R.astar(g, (2, 0), (2, 6))
    assert path == [(2, y) for y in range(7)]
    assert len(path) - 1 + R.CROSS_COST == 6 + 4  # 6 steps + one lane entry


def test_astar_lane_crossing_cell_is_impassable():
    # Wall on column 3, only gap (3, 3) = row-lane and col-lane cell. Without a lane rule the
    # path squeezes through the gap; with it the gap is impassable (note the per-axis
    # perpendicular rules already imply this, so it is not separately mutable).
    g = R.Grid(7, 7, {(3, y) for y in range(7) if y != 3}, {3}, {3})
    assert R.astar(g, (0, 0), (6, 0)) is None


def test_astar_refuses_to_run_along_a_lane():
    # One-row corridor that is exactly the lane row; without the "perpendicular only"
    # rule start (0,1) -> goal (6,1) runs straight along it.
    g = R.Grid(7, 3, {(x, y) for x in range(7) for y in (0, 2)}, {1}, set())
    assert R.astar(g, (0, 1), (6, 1)) is None


def test_astar_refuses_to_turn_on_a_lane_cell():
    # (1, 3) is a lane cell and the goal (0, 3) is only reachable from it (its other
    # neighbours are walled). Entering vertically then leaving west is a turn on the lane;
    # without the no-turn rule the path (1,0)..(1,3),(0,3) exists.
    g = R.Grid(7, 7, {(0, 2), (0, 4)}, {3}, set())
    assert R.astar(g, (1, 0), (0, 3)) is None


def test_astar_returns_none_when_walled_off():
    g = R.Grid(5, 5, {(3, y) for y in range(5)}, set(), set())
    assert R.astar(g, (0, 2), (4, 2)) is None
