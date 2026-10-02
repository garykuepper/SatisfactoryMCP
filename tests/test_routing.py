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


def lay(*floors_, buses=()):
    blocks = [b for f in floors_ for b in f.blocks]
    return Layout(blocks=blocks, buses=list(buses), floors=list(floors_))


def test_one_producer_feeds_two_consumers_on_its_floor():
    p = blk("p", 1, 1, outputs={"I": 60.0})
    c1 = blk("c1", 1, 4, inputs={"I": 30.0})
    c2 = blk("c2", 1, 7, inputs={"I": 30.0})
    r = R.route_belts(lay(floor(0, [p, c1, c2], d=12)), belt_ipm=270)
    assert not r.failures and not r.lifts
    assert sorted((b.src, b.dst, b.rate) for b in r.belts) == [("p", "c1", 30.0), ("p", "c2", 30.0)]
    for b in r.belts:
        assert b.path[0] == R.manifold_ports(p).outputs[0][0]


def test_an_item_made_below_goes_up_one_lift_at_one_cell():
    p = blk("p", 1, 1, outputs={"I": 60.0})
    c = blk("c", 1, 1, inputs={"I": 60.0})
    r = R.route_belts(lay(floor(0, [p]), floor(1, [c])), belt_ipm=270)
    assert [(l.item, l.kind, l.from_floor, l.to_floor) for l in r.lifts] == [("I", "up", 0, 1)]
    cell = r.lifts[0].cell
    assert cell[0] == 0
    assert {(b.floor, b.src, b.dst) for b in r.belts} == {(0, "p", "lift:I"), (1, "lift:I", "c")}
    assert [b.path[-1] for b in r.belts if b.floor == 0] == [cell]
    assert [b.path[0] for b in r.belts if b.floor == 1] == [cell]


def test_an_item_made_above_its_user_goes_down():
    c = blk("c", 1, 1, inputs={"I": 10.0})
    p = blk("p", 1, 1, outputs={"I": 10.0})
    r = R.route_belts(lay(floor(0, [c]), floor(1, [p])), belt_ipm=270)
    assert [(l.kind, l.from_floor, l.to_floor) for l in r.lifts] == [("down", 1, 0)]
    assert {(b.floor, b.src, b.dst) for b in r.belts} == {(1, "p", "lift:I"), (0, "lift:I", "c")}


def test_raw_items_come_in_on_the_ground_floor_and_exports_go_out():
    c = blk("c", 1, 1, inputs={"Ore": 30.0}, outputs={"Ingot": 30.0})
    r = R.route_belts(lay(floor(0, [c])), belt_ipm=270)
    assert sorted((l.item, l.kind) for l in r.lifts) == [("Ingot", "out"), ("Ore", "in")]
    assert not r.failures


def test_lift_cells_are_deterministic_and_shared_across_spanned_floors():
    p = blk("p", 1, 1, outputs={"A": 1.0, "B": 1.0})
    c = blk("c", 1, 1, inputs={"A": 1.0, "B": 1.0})
    r = R.route_belts(lay(floor(0, [p]), floor(1, [c])), belt_ipm=270)
    by_item = {l.item: l.cell for l in r.lifts}
    assert by_item["A"][1] < by_item["B"][1]          # handed out south-up, by item
    rows0, rows1 = set(R.strip_rows(floor(0, [p]))), set(R.strip_rows(floor(1, [c])))
    assert all(cell[1] in rows0 & rows1 for cell in by_item.values())


def test_a_full_lift_strip_is_a_hard_error():
    # 2-foundation-deep floor: 8 strip rows minus lane rows {1, 6} = 6 usable
    c = blk("c", 1, 1, inputs={f"Raw{k}": 1.0 for k in range(7)})
    with pytest.raises(R.LiftStripFull, match="lift strip full"):
        R.route_belts(lay(floor(0, [c], d=2)), belt_ipm=270)


def _two_by_two():
    p1, p2 = blk("p1", 1, 1, outputs={"I": 250.0}), blk("p2", 1, 4, outputs={"I": 250.0})
    c1, c2 = blk("c1", 1, 1, inputs={"I": 250.0}), blk("c2", 1, 4, inputs={"I": 250.0})
    return R.route_belts(lay(floor(0, [p1, p2]), floor(1, [c1, c2])), belt_ipm=270)


def test_a_lift_gets_one_line_per_belt_and_each_manifold_takes_one():
    r = _two_by_two()
    assert not r.failures
    assert len(r.lifts) == 2 and len({l.cell for l in r.lifts}) == 2
    assert {l.kind for l in r.lifts} == {"up"}
    f0 = {b.src: b for b in r.belts if b.floor == 0}
    f1 = {b.dst: b for b in r.belts if b.floor == 1}
    assert sorted(f0) == ["p1", "p2"] and sorted(f1) == ["c1", "c2"]
    assert f0["p1"].path[-1] != f0["p2"].path[-1] and f1["c1"].path[0] != f1["c2"].path[0]
    assert all(b.rate == 250.0 for b in r.belts)


def test_an_over_capacity_connection_is_flagged_not_split():
    p = blk("p", 1, 1, outputs={"I": 600.0})
    c = blk("c", 1, 1, inputs={"I": 600.0})
    r = R.route_belts(lay(floor(0, [p]), floor(1, [c])), belt_ipm=270)
    assert sorted(b.floor for b in r.belts) == [0, 1]
    assert any("exceeds one belt" in f for f in r.failures)
    assert r.failed_floors == []


def test_a_folded_producer_merges_its_two_output_belts():
    f = blk("f", 1, 1, n=12, outputs={"P": 60.0}, folded=True)
    r = R.route_belts(lay(floor(0, [f], w=16, d=16)), belt_ipm=270)
    ports = R.manifold_ports(f)
    merges = [b for b in r.belts if b.src == b.dst == "f"]
    assert len(merges) == 1
    assert (merges[0].path[0], merges[0].path[-1]) == (ports.outputs[1][0], ports.outputs[0][0])


def test_pipes_are_listed_not_routed():
    c = blk("c", 1, 1, inputs={"Water": 30.0, "Ore": 10.0})
    r = R.route_belts(lay(floor(0, [c]), buses=[bus("Water", "pipe"), bus("Ore")]), belt_ipm=270)
    assert "Water: pipe, not routed" in r.failures
    assert all(b.item != "Water" for b in r.belts) and all(l.item != "Water" for l in r.lifts)


def test_an_oversized_floor_is_reported_not_routed():
    big = Floor(index=1, kind="production", stage=1, height_m=16.0, blocks=[blk("x", 0, 0)],
                slab_side_m=None, slab_depth_m=None, group="Big")
    r = R.route_belts(lay(floor(0, [blk("c", 1, 1, inputs={"Ore": 1.0})]), big), belt_ipm=270)
    assert "F1: oversized floor, not routed" in r.failures
    assert any(b.floor == 0 for b in r.belts)


def test_colliding_manifold_belts_fail_the_floor_without_dropping_others():
    lower = blk("a", 1, 1, outputs={"P": 1.0})
    upper = blk("b", 1, 4, inputs={"X": 1.0, "Y": 1.0, "Z": 1.0})   # 3rd input row lands on lane 13
    r = R.route_belts(lay(floor(0, [lower, upper])), belt_ipm=270)
    assert r.failed_floors == [0]
    assert any("collide" in f for f in r.failures)


def test_lift_exit_stubs_are_not_crossed_by_other_lifts_belts():
    # Without the reserved (1..3, cy) stub, a long belt to one lift hugs column 2 and
    # seals the other lift off, so it comes out unroutable.
    r = _two_by_two()
    assert not r.failures
    for b in r.belts:
        for l in r.lifts:
            cy = l.cell[1]
            if {(1, cy), (2, cy), (3, cy)} & set(b.path) and b.floor in _span_floors(l):
                assert l.cell in (b.path[0], b.path[-1])


def _span_floors(l):
    return range(min(l.from_floor, l.to_floor), max(l.from_floor, l.to_floor) + 1)


def test_a_source_between_two_users_lifts_both_ways():
    c0 = blk("c0", 1, 1, inputs={"I": 10.0})
    p = blk("p", 1, 1, outputs={"I": 20.0})
    c2 = blk("c2", 1, 1, inputs={"I": 10.0})
    r = R.route_belts(lay(floor(0, [c0]), floor(1, [p]), floor(2, [c2])), belt_ipm=270)
    assert not r.failures
    assert sorted((l.kind, l.from_floor, l.to_floor) for l in r.lifts) == [("down", 1, 0), ("up", 1, 2)]
    assert {b.dst for b in r.belts if b.floor in (0, 2)} == {"c0", "c2"}


def test_a_partial_producer_tops_up_from_below_not_a_self_loop():
    p = blk("p", 1, 4, outputs={"I": 60.0})
    c1 = blk("c1", 1, 7, inputs={"I": 30.0})
    c2 = blk("c2", 1, 1, inputs={"I": 40.0})
    r = R.route_belts(lay(floor(0, [p, c1, c2], d=12)), belt_ipm=270)
    assert not r.failures
    assert not [l for l in r.lifts if l.from_floor == l.to_floor and l.kind in ("up", "down")]
    assert [(l.kind, l.rate) for l in r.lifts] == [("in", pytest.approx(10.0))]
    got = sorted((b.src, b.dst, round(b.rate, 6)) for b in r.belts)
    assert ("p", "c2", 30.0) in got and ("lift:I", "c2", 10.0) in got and ("p", "c1", 30.0) in got


def test_partial_surplus_goes_up_and_the_rest_out():
    p = blk("p", 1, 1, outputs={"I": 100.0})
    c = blk("c", 1, 1, inputs={"I": 10.0})
    r = R.route_belts(lay(floor(0, [p]), floor(1, [c])), belt_ipm=270)
    assert not r.failures
    assert sorted((l.kind, l.rate) for l in r.lifts) == [("out", pytest.approx(90.0)), ("up", pytest.approx(10.0))]


def _clash_layout(wide):
    lower = blk("a", 1, 1, outputs={"P": 1.0})
    upper = blk("b", 1, 5 if wide else 4, inputs={"X": 1.0, "Y": 1.0, "Z": 1.0})
    return lay(floor(0, [lower, upper], d=12, group="Constructor"))


def test_build_routed_layout_widens_a_failing_floor_and_retries(monkeypatch):
    calls = []

    def fake_build_layout(game, sol, **kw):
        calls.append(kw["wide_groups"])
        assert kw["group_by"] == "building"
        return _clash_layout("Constructor" in kw["wide_groups"])

    monkeypatch.setattr(R, "build_layout", fake_build_layout)
    r = R.build_routed_layout(None, None, belt_ipm=270)
    assert calls == [frozenset(), frozenset({"Constructor"})]
    assert r.widened == ["Constructor"] and not r.failed_floors


def test_build_routed_layout_reports_a_floor_that_fails_even_when_wide(monkeypatch):
    monkeypatch.setattr(R, "build_layout", lambda game, sol, **kw: _clash_layout(False))
    r = R.build_routed_layout(None, None, belt_ipm=270)
    assert r.widened == ["Constructor"] and r.failed_floors == [0]
    assert any("collide" in f for f in r.failures)
