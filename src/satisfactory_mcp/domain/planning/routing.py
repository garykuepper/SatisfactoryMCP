"""Belt routing and conveyor lifts on a 2 m grid, for group_by="building" layouts.

Spec: ~/satisfactory/docs/superpowers/specs/2026-10-01-belt-routing-and-lifts-design.md.
Pure -- a Layout in, belt paths and lifts out. Cells are 2 m; (0, 0) is a floor's
south-west corner, x runs east, y north.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from ...core.gamedata.footprint import FOUNDATION_M
from .layout import Block, Floor

CELL_M = 2.0
PER_FND = int(FOUNDATION_M / CELL_M)  # 4 cells per foundation

Cell = tuple[int, int]


def _cells(m: float) -> int:
    return max(1, math.ceil(m / CELL_M - 1e-9))


def block_rect(b: Block) -> tuple[int, int, int, int]:
    """(x0, y0, w, h) in cells: the block's true footprint on its slab, turned when the
    layout rotated it."""
    w, h = _cells(b.packed.width_m), _cells(b.packed.depth_m)
    if b.rotated:
        w, h = h, w
    return b.x_fnd * PER_FND, b.y_fnd * PER_FND, w, h


@dataclass
class Ports:
    #: item -> that item's input manifold belt cells, feed end first
    inputs: dict[str, list[Cell]]
    #: output manifold belts, exit end first; a folded block lists [north, south]
    outputs: list[list[Cell]]


def manifold_ports(b: Block) -> Ports:
    """Manifold belt cells around a block (spec "Manifold belts"). Built in the block's
    own frame -- u along the row, v across it, v < 0 the input (S) side -- then placed
    on the slab, transposed when rotated, so each belt's first cell is its west end
    (its south end once rotated)."""
    p = b.packed
    length, depth = _cells(p.width_m), _cells(p.depth_m)
    x0, y0 = b.x_fnd * PER_FND, b.y_fnd * PER_FND

    def row(v: int) -> list[Cell]:
        return [(x0 + v, y0 + u) if b.rotated else (x0 + u, y0 + v) for u in range(length)]

    items = sorted(b.inputs)
    if p.folded:
        mid = int(p.depth_m / 2 / CELL_M)
        return Ports({it: row(mid - k) for k, it in enumerate(items)}, [row(depth), row(-1)])
    return Ports({it: row(-1 - k) for k, it in enumerate(items)}, [row(depth)])


def floor_cells(f: Floor) -> tuple[int, int]:
    return round(f.slab_side_m / CELL_M), round(f.slab_depth_m / CELL_M)


def walk_lanes(f: Floor) -> tuple[set[int], set[int]]:
    """(rows, cols) of walk-lane cells: offset 1 inside every slab edge, and offset 1
    above each row's reserved box when another row starts above it (its aisle band)."""
    w, h = floor_cells(f)
    rows, cols = {1, h - 2}, {1, w - 2}
    starts = {b.y_fnd for b in f.blocks}
    for b in f.blocks:
        top = b.y_fnd + math.ceil(block_rect(b)[3] / PER_FND)
        if any(s >= top for s in starts):
            rows.add(top * PER_FND + 1)
    return rows, cols


def strip_rows(f: Floor) -> list[int]:
    """Lift-strip rows usable on this floor: the west column minus walk-lane rows (a
    lift there would feed straight into a lane crossing a lane)."""
    _w, h = floor_cells(f)
    rows, _cols = walk_lanes(f)
    return [cy for cy in range(h) if cy not in rows]


TURN_COST = 2
CROSS_COST = 4
_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass
class Grid:
    w: int
    h: int
    blocked: set[Cell] = field(default_factory=set)
    lane_rows: set[int] = field(default_factory=set)
    lane_cols: set[int] = field(default_factory=set)

    def lanes(self, c: Cell) -> tuple[bool, bool]:
        """(on a horizontal lane, on a vertical lane). Lanes stop short of the strip
        column and the slab's outermost ring."""
        x, y = c
        return (y in self.lane_rows and 1 <= x <= self.w - 2,
                x in self.lane_cols and 1 <= y <= self.h - 2)


def astar(g: Grid, start: Cell, goal: Cell) -> list[Cell] | None:
    """Cheapest 4-connected path start -> goal, both included; both may be blocked
    cells (they are manifold or strip ends). Step 1, turn +TURN_COST, entering a
    walk-lane cell +CROSS_COST. A lane cell is crossed straight through, perpendicular
    to its lane; a cell on two lanes is impassable. None when there is no path."""

    def est(c: Cell) -> int:
        return abs(c[0] - goal[0]) + abs(c[1] - goal[1])

    tie = 0
    heap: list = [(est(start), 0, tie, start, None)]
    best: dict = {(start, None): 0}
    prev: dict = {}
    while heap:
        _f, cost, _t, cell, d = heapq.heappop(heap)
        if cell == goal:
            path, key = [cell], (cell, d)
            while key in prev:
                key = prev[key]
                path.append(key[0])
            return path[::-1]
        if cost > best.get((cell, d), math.inf):
            continue
        on_lane = cell != start and any(g.lanes(cell))
        for nd in _DIRS:
            if on_lane and nd != d:
                continue  # no turning on a lane cell
            n = (cell[0] + nd[0], cell[1] + nd[1])
            if not (0 <= n[0] < g.w and 0 <= n[1] < g.h):
                continue
            step = 1 + (TURN_COST if d is not None and nd != d else 0)
            if n != goal:
                if n in g.blocked:
                    continue
                on_row, on_col = g.lanes(n)
                if (on_row and on_col) or (on_row and nd[1] == 0) or (on_col and nd[0] == 0):
                    continue
                if on_row or on_col:
                    step += CROSS_COST
            nc = cost + step
            if nc < best.get((n, nd), math.inf):
                best[(n, nd)] = nc
                prev[(n, nd)] = (cell, d)
                tie += 1
                heapq.heappush(heap, (nc + est(n), nc, tie, n, nd))
    return None
