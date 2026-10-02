"""Belt routing and conveyor lifts on a 2 m grid, for group_by="building" layouts.

Spec: ~/satisfactory/docs/superpowers/specs/2026-10-01-belt-routing-and-lifts-design.md.
Pure -- a Layout in, belt paths and lifts out. Cells are 2 m; (0, 0) is a floor's
south-west corner, x runs east, y north.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
