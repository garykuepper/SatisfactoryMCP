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
from .layout import Block, Floor, Layout

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


EPS = 1e-6


@dataclass
class Belt:
    item: str
    rate: float
    floor: int
    path: list[Cell]
    src: str  # block key or "lift:<item>"
    dst: str


@dataclass
class Lift:
    item: str
    rate: float
    cell: Cell
    from_floor: int
    to_floor: int
    kind: str  # up | down | in | out


@dataclass
class Routing:
    layout: Layout
    belts: list[Belt] = field(default_factory=list)
    lifts: list[Lift] = field(default_factory=list)
    widened: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    #: floor indexes with a failure -- what build_routed_layout widens
    failed_floors: list[int] = field(default_factory=list)


class LiftStripFull(ValueError):
    """More lifts than free strip rows: a hard error, never a lift placed elsewhere."""


@dataclass
class _Conn:
    item: str
    rate: float
    a: Cell
    b: Cell
    src: str
    dst: str


def _dist(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _span(lift: Lift) -> range:
    return range(min(lift.from_floor, lift.to_floor), max(lift.from_floor, lift.to_floor) + 1)


def _floor_plan(f: Floor, ports: dict[str, Ports], pipes: set[str]):
    """(same-floor connections, need {item: [(key, feed, rate)]} from lifts,
    surplus {item: [(key, exit, rate)]} to lifts) -- spec "Connections"."""
    conns: list[_Conn] = []
    need: dict[str, list] = {}
    surplus: dict[str, list] = {}
    blocks = sorted(f.blocks, key=lambda b: b.key)
    items = sorted({i for b in blocks for i in (*b.inputs, *b.outputs)} - pipes)
    for item in items:
        prod = [[b.key, ports[b.key].outputs[0][0], b.outputs[item]]
                for b in blocks if b.outputs.get(item, 0.0) > EPS]
        for b in blocks:  # greedy partial matching: nearest producer with spare first
            left = b.inputs.get(item, 0.0)
            if left <= EPS:
                continue
            feed = ports[b.key].inputs[item][0]
            while left > EPS:
                live = [p for p in prod if p[2] > EPS]
                if not live:
                    break
                p = min(live, key=lambda p: (_dist(p[1], feed), p[0]))
                take = min(left, p[2])
                conns.append(_Conn(item, take, p[1], feed, p[0], b.key))
                p[2] -= take
                left -= take
            if left > EPS:
                need.setdefault(item, []).append((b.key, feed, left))
        for key, exit_, spare in prod:
            if spare > EPS:
                surplus.setdefault(item, []).append((key, exit_, spare))
    for b in blocks:  # a folded block's south output belt merges into its north one
        outs = ports[b.key].outputs
        main = max(b.outputs, key=b.outputs.get) if b.outputs else ""
        if len(outs) == 2 and main and main not in pipes:
            conns.append(_Conn(main, b.outputs[main] / 2, outs[1][0], outs[0][0], b.key, b.key))
    return conns, need, surplus


def _plan_lifts(floor_ids, needs, surpluses, belt_ipm: float) -> list[Lift]:
    """spec "Lifts", mass-balanced per item: each sink floor (in floor order) draws from
    the nearest source floors; one group per (source floor, direction); unmet need comes
    'in' on the ground floor, unsent surplus goes 'out'. A group is ceil(rate/belt_ipm)
    Lift lines."""
    ground = min(floor_ids)
    items = sorted({i for fi in floor_ids for i in (*needs[fi], *surpluses[fi])})
    plan: list[tuple[str, float, int, int, str]] = []
    for item in items:
        avail = {fi: sum(r for _k, _c, r in surpluses[fi][item]) for fi in floor_ids if item in surpluses[fi]}
        sinks = {fi: sum(r for _k, _c, r in needs[fi][item]) for fi in floor_ids if item in needs[fi]}
        flows: dict[tuple[int, str], list] = {}
        unmet: dict[int, float] = {}
        for t in sorted(sinks):
            left = sinks[t]
            for s_ in sorted(avail, key=lambda s_: (abs(s_ - t), s_)):
                take = min(left, avail[s_])
                if take <= EPS:
                    continue
                kind = "up" if t > s_ else "down"
                g = flows.setdefault((s_, kind), [0.0, t])
                g[0] += take
                g[1] = max(g[1], t) if kind == "up" else min(g[1], t)
                avail[s_] -= take
                left -= take
            if left > EPS:
                unmet[t] = left
        for (s_, kind), (rate, far) in sorted(flows.items()):
            plan.append((item, rate, s_, far, kind))
        if unmet:
            plan.append((item, sum(unmet.values()), ground, max(unmet), "in"))
        plan += [(item, left, s_, s_, "out") for s_, left in sorted(avail.items()) if left > EPS]
    lifts = []
    for item, rate, a, b, kind in plan:
        n = max(1, math.ceil(rate / belt_ipm - EPS))
        lifts += [Lift(item, rate / n, (0, -1), a, b, kind) for _ in range(n)]
    return lifts


def _assign_cells(lifts: list[Lift], floors: dict[int, Floor]) -> None:
    def stub_free(f: Floor) -> set[int]:  # exit stub (1..3, cy) must avoid manifold belts
        belts = {c for b in f.blocks for ln in (*manifold_ports(b).inputs.values(), *manifold_ports(b).outputs)
                 for c in ln}
        return {cy for cy in strip_rows(f) if not any((x, cy) in belts for x in (1, 2, 3))}

    usable = {fi: stub_free(f) for fi, f in floors.items()}
    taken: dict[int, set[int]] = {fi: set() for fi in floors}
    for lift in sorted(lifts, key=lambda l: (l.from_floor, l.item, l.kind)):  # stable: line order
        span = [fi for fi in _span(lift) if fi in floors]
        free = set.intersection(*(usable[fi] - taken[fi] for fi in span))
        if not free:
            raise LiftStripFull(
                f"lift strip full: no free strip row for {lift.item} on floors "
                f"F{span[0]}-F{span[-1]}"
            )
        cy = min(free)
        lift.cell = (0, cy)
        for fi in span:
            taken[fi].add(cy)


def _grid(f: Floor, ports: dict[str, Ports]) -> tuple[Grid, str]:
    """The floor's routing grid, and a description of the first manifold-belt clash
    (a manifold belt on a walk lane, on another block, off the slab, or on another
    manifold belt) -- '' when clean."""
    w, h = floor_cells(f)
    rows, cols = walk_lanes(f)
    g = Grid(w, h, {(0, y) for y in range(h)}, rows, cols)
    footprint: dict[Cell, str] = {}
    for b in f.blocks:
        x0, y0, bw, bh = block_rect(b)
        for x in range(x0, x0 + bw):
            for y in range(y0, y0 + bh):
                footprint[(x, y)] = b.key
    g.blocked |= set(footprint)
    reserved: set[Cell] = set()
    for b in f.blocks:
        ps = ports[b.key]
        for belt in (*ps.inputs.values(), *ps.outputs):
            for c in belt:
                if (not (0 < c[0] < w and 0 <= c[1] < h) or c in reserved or any(g.lanes(c))
                        or footprint.get(c, b.key) != b.key):
                    return g, f"{b.key} manifold belt at {c}"
                reserved.add(c)
    g.blocked |= reserved
    return g, ""


def route_belts(layout: Layout, belt_ipm: float) -> Routing:
    """Route one attempt (no widening): connections per floor, lifts on the strip,
    then A*. Each used lift cell gets a reserved 3-cell exit stub (1..3, cy) and its
    connections route first (highest lift row first, then shortest), then the rest longest-first. One belt per
    connection (over-capacity is flagged, never split); a lift's lines are used round-robin.
    Unroutable belts and clashing floors land in failures/failed_floors; nothing is dropped."""
    out = Routing(layout=layout)
    names = {bus.item: bus.name for bus in layout.buses}
    pipes = {bus.item for bus in layout.buses if bus.carrier == "pipe"}
    out.failures += [f"{names.get(i, i)}: pipe, not routed" for i in sorted(pipes)]
    floors = {f.index: f for f in layout.floors if f.slab_side_m is not None}
    out.failures += [f"F{f.index}: oversized floor, not routed"
                     for f in layout.floors if f.slab_side_m is None]
    if not floors:
        return out
    ports = {b.key: manifold_ports(b) for f in floors.values() for b in f.blocks}
    plans = {fi: _floor_plan(f, ports, pipes) for fi, f in floors.items()}
    out.lifts = _plan_lifts(sorted(floors), {fi: p[1] for fi, p in plans.items()},
                            {fi: p[2] for fi, p in plans.items()}, belt_ipm)
    _assign_cells(out.lifts, floors)

    for fi, f in floors.items():
        same, need, surplus = plans[fi]
        conns: list[_Conn] = list(same)

        def fail(msg: str) -> None:
            out.failures.append(f"F{fi}: {msg}")
            if fi not in out.failed_floors:
                out.failed_floors.append(fi)

        for item, users in need.items():
            feeders = [l for l in out.lifts if l.item == item and fi in _span(l)
                       and (l.kind == "in" or (l.kind in ("up", "down") and l.from_floor != fi))]
            for k, (key, feed, rate) in enumerate(users):
                if feeders:
                    conns.append(_Conn(item, rate, feeders[k % len(feeders)].cell, feed, f"lift:{item}", key))
                else:
                    fail(f"{names.get(item, item)} has no lift to feed {key}")
        for item, senders in surplus.items():
            groups = [[l for l in out.lifts if l.item == item and l.from_floor == fi and l.kind == kind]
                      for kind in ("up", "down", "out")]
            groups = [g for g in groups if g]
            if not groups:
                fail(f"{names.get(item, item)} has no lift to take {senders[0][0]}")
                continue
            caps = [sum(l.rate for l in g) for g in groups]
            used = [0] * len(groups)
            gi = 0
            for key, exit_, rate in senders:
                while rate > EPS and gi < len(groups):
                    take = min(rate, caps[gi])
                    if take > EPS:
                        line = groups[gi][used[gi] % len(groups[gi])]
                        used[gi] += 1
                        conns.append(_Conn(item, take, exit_, line.cell, key, f"lift:{item}"))
                        caps[gi] -= take
                        rate -= take
                    if caps[gi] <= EPS:
                        gi += 1
                if rate > EPS:
                    fail(f"{names.get(item, item)} has no lift to take {key}")
        for c in conns:
            if c.rate > belt_ipm + EPS:
                out.failures.append(f"F{fi}: {names.get(c.item, c.item)} {c.src} -> {c.dst}: "
                                    f"{c.rate:.4g}/min exceeds one belt ({belt_ipm:.4g}/min)")

        grid, clash = _grid(f, ports)
        if clash:
            out.failures.append(f"F{fi}: manifold belts collide ({clash})")
            out.failed_floors.append(fi)
            continue
        lift_cells = {l.cell for l in out.lifts}
        stubs = {c.a if c.a in lift_cells else c.b for c in conns if c.a in lift_cells or c.b in lift_cells}
        for cx, cy in stubs:
            grid.blocked |= {(1, cy), (2, cy), (3, cy)}

        def ends(c: _Conn):
            return (3, c.a[1]) if c.a in lift_cells else c.a, (3, c.b[1]) if c.b in lift_cells else c.b

        def order(c: _Conn):
            a, b = ends(c)
            lifty = c.a[1] if c.a in lift_cells else c.b[1] if c.b in lift_cells else -1
            return (0, -lifty, _dist(a, b), c.item, c.src, c.dst) if lifty >= 0 else (
                1, 0, -_dist(a, b), c.item, c.src, c.dst)

        for c in sorted(conns, key=order):
            a, b = ends(c)
            path = astar(grid, a, b)
            if path is None:
                out.failures.append(f"F{fi}: {names.get(c.item, c.item)} {c.src} -> {c.dst} unroutable")
                if fi not in out.failed_floors:
                    out.failed_floors.append(fi)
                continue
            grid.blocked.update(path[1:-1])
            if c.a in lift_cells:
                path = [c.a, (1, c.a[1]), (2, c.a[1])] + path
            if c.b in lift_cells:
                path = path + [(2, c.b[1]), (1, c.b[1]), c.b]
            out.belts.append(Belt(c.item, c.rate, fi, path, c.src, c.dst))
    return out
