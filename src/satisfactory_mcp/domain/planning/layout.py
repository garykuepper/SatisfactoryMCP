"""Turn a solved plan into a buildable schematic: blocks, buses and floors.

This is a SCHEMATIC, not a blueprint. It answers "what modules do I build, what feeds
what, how much space, and what goes on which floor". It deliberately does not produce
world coordinates: there is no terrain heightmap in any data available here, so belt
pathfinding and foundation alignment would be invention rather than derivation.

Three ideas do the work:

**Blocks come from throughput, not taste.** 46 Refineries consuming 1380 m3/min of
crude cannot sit on one manifold when a Mk2 pipe carries 600 -- that is 3 lines, so it
is 3 blocks of ~16. Line count *is* block count, which makes the split derived rather
than arbitrary.

**Connections are buses, not pairings.** The LP gives net balances, not who feeds whom.
Recovering specific producer-consumer pairs is a min-cost flow problem with no unique
answer absent geometry, so each item gets one bus that producers feed and consumers
draw from. That is also what a manifold physically is.

**Floors come from chain depth.** Stage = longest path through the item graph, so
extractors land on the bottom floor and generators on top, with a logistics deck
between each pair. Depth is computed on the graph's CONDENSATION, because the recipe
graph genuinely contains cycles -- Recycled Plastic and Recycled Rubber consume each
other's output. Collapsing each strongly connected component makes the graph acyclic
and puts cycle members on one floor, which is also right physically: they have to be
built together.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ...core.gamedata.footprint import FOUNDATION_M, Packed
from ...core.gamedata.model import GameData
from .carrier import carrier_for
from .optimize import MW, Solution

__all__ = [
    "LOGISTICS_FLOOR_M",
    "Block",
    "Bus",
    "Floor",
    "Layout",
    "build_layout",
    "chain_depth",
    "fluid_head",
]

#: Height reserved for a logistics deck: belts, pipes and a walkway between them.
LOGISTICS_FLOOR_M = 4.0

#: Vertical headroom above the tallest machine on a production floor.
FLOOR_HEADROOM_M = 1.0

#: Foundations are 1, 2 or 4 m thick; floor heights round up to this.
FLOOR_STEP_M = 2.0

#: Slab-mode floor height (4 walls), and the double floor (8 walls) a floor gets when any
#: building on it stands taller than one -- a Refinery (30 m) or Coal Generator (32 m).
FLOOR_M = 16.0
DOUBLE_FLOOR_M = 32.0

#: A manifold longer than this stops being sensible to build or feed evenly.
MAX_MACHINES_PER_BLOCK = 24


@dataclass
class Block:
    """One buildable module: a row of identical machines on one manifold."""

    key: str
    label: str
    building_id: str
    building: str
    recipe: str | None
    machines: int
    clock: float
    part: int  # 1-based index within a split group
    parts: int  # how many blocks the process was split into
    inputs: dict[str, float] = field(default_factory=dict)
    outputs: dict[str, float] = field(default_factory=dict)
    stage: int = 0
    #: Per-MACHINE dimensions, which is what the build table prints as "each(m)".
    width_m: float = 0.0
    depth_m: float = 0.0
    height_m: float = 0.0
    #: The whole block laid out on foundations. The single source of "how much floor do
    #: N of these need" -- see Footprint.pack. None only when the building has no
    #: clearance data at all, which `build_layout` reports rather than treating as free.
    packed: Packed | None = None
    #: Foundation-grid position within this block's floor, and whether it was rotated
    #: 90deg to fit. Only meaningful in slab mode (Layout built with slab_foundations>0)
    #: -- 0, 0, False otherwise, including for anything in Layout.off_slab.
    x_fnd: int = 0
    y_fnd: int = 0
    rotated: bool = False
    #: Building mode only (0 otherwise): foundation rows reserved beside the body for
    #: the splitters (input side, one 4 m band per input, two per row) and the mergers
    #: (each output side -- both outer edges of a folded block, whose inputs share its
    #: lane). x_fnd/y_fnd stay the body's position; the rows sit outside it.
    manifold_in_fnd: int = 0
    manifold_out_fnd: int = 0

    @property
    def foundations(self) -> int:
        return self.packed.foundations if self.packed else 0

    @property
    def block_width_m(self) -> float:
        return self.packed.width_m if self.packed else 0.0

    @property
    def block_depth_m(self) -> float:
        return self.packed.depth_m if self.packed else 0.0

    @property
    def input_sides(self) -> tuple[str, ...]:
        """Edges inputs arrive on, in the block's own frame (row along X, before any
        rotation): the south edge of a straight row, the middle lane when folded."""
        return ("lane",) if self.packed and self.packed.folded else ("S",)

    @property
    def output_sides(self) -> tuple[str, ...]:
        """Edges outputs leave from: north for a straight row, both outer edges when
        folded (the two output belts merge at the row end)."""
        return ("N", "S") if self.packed and self.packed.folded else ("N",)

    @property
    def name(self) -> str:
        return f"{self.label} ({self.part}/{self.parts})" if self.parts > 1 else self.label


@dataclass
class Bus:
    """All movement of one item, pooled. Producers feed it, consumers draw from it."""

    item: str
    name: str
    rate: float
    carrier: str  # belt | pipe
    unit: str
    lines: int
    producers: list[str] = field(default_factory=list)
    consumers: list[str] = field(default_factory=list)
    from_stage: int = 0
    to_stage: int = 0
    external: bool = False  # enters or leaves the site


@dataclass
class Floor:
    index: int
    kind: str  # production | logistics
    stage: int | None
    height_m: float
    blocks: list[Block] = field(default_factory=list)
    buses: list[Bus] = field(default_factory=list)
    #: Which declared site this floor belongs to. Empty outside a site partition; set by
    #: ``layout_service`` when floors are stacked per site, so a reader can tell three
    #: separate buildings from one tower.
    site: str = ""
    #: Slab side in metres, set only for a slab-mode production floor. None in
    #: default (slab_foundations=0) mode, where a floor's footprint is still the sum of its
    #: blocks -- see Floor.foundations.
    slab_side_m: float | None = None
    #: Slab depth in metres when the slab is rectangular; None means square (depth =
    #: slab_side_m).
    slab_depth_m: float | None = None
    #: Every chain stage physically standing on this floor. One entry (equal to
    #: ``stage``) outside slab mode; several on a slab-mode floor holding more than one
    #: stage's blocks. Read by _slab_floors (Task 4) for bus routing.
    stages: list[int] = field(default_factory=list)
    #: Building group this floor holds in group_by="building" mode, e.g. "Constructor"
    #: or "Smelter + Foundry"; empty in stage mode.
    group: str = ""

    @property
    def used_foundations(self) -> int:
        """What the blocks on this floor actually cover, ignoring the rest of the slab."""
        return sum(b.foundations for b in self.blocks)

    @property
    def foundations(self) -> int:
        """What you pour: the whole slab in slab mode (you build the full floor, not
        just the covered part), otherwise the block sum (floor partitioning as upstream; block shapes are
        one row / folded since 2026-10-01)."""
        if self.slab_side_m is not None:
            depth_m = self.slab_depth_m or self.slab_side_m
            return round((self.slab_side_m / FOUNDATION_M) * (depth_m / FOUNDATION_M))
        return self.used_foundations

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)


@dataclass
class Layout:
    blocks: list[Block]
    buses: list[Bus]
    floors: list[Floor]
    warnings: list[str] = field(default_factory=list)
    #: Extractors -- they stand on their resource node, not on a factory floor. Always
    #: empty when slab_foundations=0 (upstream keeps extractors on their stage's floor).
    off_slab: list[Block] = field(default_factory=list)

    @property
    def foundations(self) -> int:
        """Peak footprint: floors stack, so the site is sized by its largest floor."""
        return max((f.foundations for f in self.floors), default=0)

    @property
    def total_foundations(self) -> int:
        return sum(f.foundations for f in self.floors)

    @property
    def machines(self) -> int:
        return sum(b.machines for b in self.blocks)

    @property
    def height_m(self) -> float:
        return sum(f.height_m for f in self.floors)

    def site_side_m(self) -> float:
        """Side of a square site that fits the largest floor."""
        return math.ceil(math.sqrt(max(self.foundations, 1))) * FOUNDATION_M


def _split_process(game: GameData, proc: dict, belt_ipm: float, pipe_m3min: float) -> int:
    """How many parallel manifolds this process needs.

    The binding item wins: if crude needs 3 pipes and the output needs 1 belt, the
    block is still 3 blocks, because one manifold cannot be fed by three pipes.
    """
    needed = 1
    for item, rate in proc.get("rates", {}).items():
        if item == MW:
            continue
        line = carrier_for(game, item, belt_ipm, pipe_m3min)
        needed = max(needed, line.lines_for(abs(rate)))
    # A manifold also stops being practical past a certain length.
    needed = max(needed, math.ceil(proc["machines"] / MAX_MACHINES_PER_BLOCK))
    return max(1, min(needed, proc["machines"]))


def _blocks_from(
    game: GameData, sol: Solution, belt_ipm: float, pipe_m3min: float, max_row_fnd: int = 0
) -> list[Block]:
    blocks: list[Block] = []
    for proc in sol.processes:
        parts = _split_process(game, proc, belt_ipm, pipe_m3min)
        machines = proc["machines"]
        building = game.buildings.get(proc["building_id"] or "")
        fp = building.footprint if building else None

        # Spread machines as evenly as possible; remainder goes to the first blocks.
        base, extra = divmod(machines, parts)
        for part in range(parts):
            n = base + (1 if part < extra else 0)
            if n <= 0:
                continue
            share = (n / machines) if machines else 0.0
            inputs: dict[str, float] = {}
            outputs: dict[str, float] = {}
            # Straight from the solve: these already include clock and boost, and
            # they cover extractors and generators, which have no recipe at all.
            for item, rate in proc.get("rates", {}).items():
                if item == MW:
                    continue
                (outputs if rate > 0 else inputs)[item] = abs(rate) * share
            blocks.append(
                Block(
                    key=f"{proc['pid']}#{part + 1}",
                    label=proc["label"],
                    building_id=proc["building_id"] or "",
                    building=proc["building"],
                    recipe=proc.get("recipe"),
                    machines=n,
                    clock=proc["clock"],
                    part=part + 1,
                    parts=parts,
                    inputs=inputs,
                    outputs=outputs,
                    width_m=fp.width_m if fp else 0.0,
                    depth_m=fp.depth_m if fp else 0.0,
                    height_m=fp.height_m if fp else 0.0,
                    # ONE sizing primitive, shared with everything else that asks how
                    # much floor N machines need. This used to be `fp.foundations * n`,
                    # which the footprint's own docstring warns is an upper bound: it
                    # ignores shared edges, so two 20 m machines side by side were
                    # charged 6 tiles where they span 40 m and need 5. Across a plan that
                    # was about a third too much concrete, and the water-siting note had
                    # independently grown its own copy of the same wrong arithmetic.
                    # A block is one manifold: one row, folded in two when long
                    # (spec 2026-10-01). Extractors stand on nodes, not manifolds, so
                    # they keep pack()'s free shape.
                    packed=(
                        (fp.pack(n) if building.is_extractor else fp.pack_manifold(n, max_row_fnd))
                        if fp else None
                    ),
                )
            )
    return blocks


def _strongly_connected(n: int, edges: dict[int, set[int]]) -> list[list[int]]:
    """Tarjan's SCC, iterative so a deep chain cannot blow the recursion limit."""
    index = [None] * n
    low = [0] * n
    on_stack = [False] * n
    stack: list[int] = []
    result: list[list[int]] = []
    counter = 0

    for root in range(n):
        if index[root] is not None:
            continue
        work = [(root, iter(sorted(edges.get(root, ()))))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack[root] = True

        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if index[nxt] is None:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(sorted(edges.get(nxt, ())))))
                    advanced = True
                    break
                if on_stack[nxt]:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack[member] = False
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
    return result


def chain_depth(nodes: Sequence[tuple[Iterable[str], Iterable[str]]]) -> list[int]:
    """Chain depth per node, computed on the condensation of the item graph.

    Each node is ``(inputs, outputs)`` as item ids; the result is its longest-path
    depth, so pure consumers of raw material sit at 0 and terminal consumers sit
    highest. Shared by the layout (floors) and the diff (build stages), because
    "what has to exist before this can run" is one question, not two.

    A plain longest-path walk is not available: the recipe graph genuinely contains
    cycles, because Recycled Plastic and Recycled Rubber each consume the other's
    output. Naive relaxation does not settle on a cycle either -- it lifts every
    member by one stage per pass until the iteration cap, so depth ends up reporting
    how long the loop ran rather than how deep the chain is.

    Collapsing each strongly connected component to a single node fixes both: the
    condensation is acyclic by construction, and every member of a cycle shares a
    depth, which is also right physically since they must be built together.
    """
    producers: dict[str, list[int]] = {}
    for i, (_ins, outs) in enumerate(nodes):
        for item in outs:
            producers.setdefault(item, []).append(i)

    edges: dict[int, set[int]] = {}
    for i, (ins, _outs) in enumerate(nodes):
        for item in ins:
            for src in producers.get(item, ()):
                if src != i:
                    edges.setdefault(src, set()).add(i)

    components = _strongly_connected(len(nodes), edges)
    component_of = {}
    for cid, members in enumerate(components):
        for member in members:
            component_of[member] = cid

    # Longest path over the condensation, which is a DAG.
    condensed: dict[int, set[int]] = {}
    for src, dsts in edges.items():
        for dst in dsts:
            a, b_ = component_of[src], component_of[dst]
            if a != b_:
                condensed.setdefault(a, set()).add(b_)

    depth = [0] * len(components)
    for _ in range(len(components)):
        changed = False
        for a, dsts in condensed.items():
            for b_ in dsts:
                if depth[b_] < depth[a] + 1:
                    depth[b_] = depth[a] + 1
                    changed = True
        if not changed:
            break

    return [depth[component_of[i]] for i in range(len(nodes))]


def _assign_stages(blocks: list[Block]) -> None:
    for stage, b in zip(chain_depth([(b.inputs, b.outputs) for b in blocks]), blocks):
        b.stage = stage


def _buses(
    game: GameData,
    blocks: list[Block],
    sol: Solution,
    belt_ipm: float,
    pipe_m3min: float,
) -> list[Bus]:
    items: set[str] = set()
    for b in blocks:
        items |= set(b.inputs) | set(b.outputs)

    buses: list[Bus] = []
    for item in sorted(items):
        if item == MW:
            continue
        produced = sum(b.outputs.get(item, 0.0) for b in blocks)
        consumed = sum(b.inputs.get(item, 0.0) for b in blocks)
        rate = max(produced, consumed)
        if rate <= 1e-6:
            continue
        line = carrier_for(game, item, belt_ipm, pipe_m3min)
        src = [b for b in blocks if b.outputs.get(item, 0.0) > 1e-6]
        dst = [b for b in blocks if b.inputs.get(item, 0.0) > 1e-6]
        it = game.items.get(item)
        buses.append(
            Bus(
                item=item,
                name=it.name if it else item,
                rate=rate,
                carrier=line.kind,
                unit=line.unit,
                lines=line.lines_for(rate),
                producers=[b.key for b in src],
                consumers=[b.key for b in dst],
                from_stage=min((b.stage for b in src), default=0),
                # With no consumer on site the item leaves at the level it is made,
                # so the destination is its own stage -- not 0, which would render
                # as flowing backwards down the stack.
                to_stage=max(
                    (b.stage for b in dst),
                    default=min((b.stage for b in src), default=0),
                ),
                # Leaves or enters the site: exported, sunk, or drawn from raw supply.
                external=item in sol.exports or item in sol.sunk or not src or not dst,
            )
        )
    buses.sort(key=lambda b: -b.rate)
    return buses


def fluid_head(layout: Layout, pump_head_m: float = 0.0) -> list[dict]:
    """Which fluids the floor assignment makes climb, and by how many storeys.

    Floors follow CHAIN DEPTH, which is a correctness property -- a consumer sits above
    its producer, so the schematic reads in build order. It is not a physics property.
    Fluids do not care about chain depth: a pipe running downhill is free while one
    running uphill needs head, and water in particular can only be drawn at sea level, so
    it always starts at the bottom whatever the chain says.

    Chain-depth ordering therefore tends to make everything climb. On a measured oil plan
    it put extractors at F0, refineries F2, blenders F4, generators F6 -- water up two
    storeys, crude, heavy oil residue and fuel up one each. Reordering by hand so the
    water extractors sit at sea level under the blenders, generators one above and
    refineries on top leaves only water and fuel climbing one storey each, and lets
    residue and crude fall for free.

    Reporting this was once the whole answer, on the grounds that the right stack depends
    on terrain and on how much the player will pump. `order_floors_by="head"` now searches
    the orders too -- naming a cost and then defaulting to the arrangement that pays it
    was the gap: on the measured oil plan chain order lifts 66 pipe-storeys where 52 is
    available, and water climbs four floors when two will do.

    ``pumps`` is per riser and real: metres come from the actual floors crossed, and head
    per pump from ``mDesignPressure``. It is a LOWER bound for the same reason the trunk
    figure is -- pipe friction and the head a full pipe holds are not modelled.
    """
    floor_of: dict[int, int] = {}
    site_of: dict[int, str] = {}
    for floor in layout.floors:
        # A slab-mode floor can hold several stages (floor.stages); the SPEC's own
        # rule (design doc, "for fluid_head, a stage maps to the lowest floor it
        # appears on") means the first floor (by index, i.e. lowest) that holds a
        # stage owns it -- setdefault, not plain assignment. Falls back to
        # floor.stage for a non-slab floor, where .stages is always empty and this is
        # unchanged from upstream (one stage per floor either way).
        for stage in floor.stages or ([floor.stage] if floor.stage is not None else []):
            floor_of.setdefault(stage, floor.index)
            site_of.setdefault(stage, floor.site)
    height_of = {floor.index: floor.height_m for floor in layout.floors}

    out: list[dict] = []
    for bus in layout.buses:
        if bus.carrier != "pipe" or bus.external:
            continue
        start, end = floor_of.get(bus.from_stage), floor_of.get(bus.to_stage)
        if start is None or end is None or start == end:
            continue
        # Every floor strictly between the two, so the climb is the real stack height
        # crossed rather than storeys times an assumed storey.
        lo, hi = sorted((start, end))
        metres = sum(h for i, h in height_of.items() if lo <= i < hi)
        per_line = math.ceil(metres / pump_head_m - 1e-9) if pump_head_m > 0 and end > start else 0
        out.append(
            {
                "item": bus.name,
                "rate": bus.rate,
                "unit": bus.unit,
                "floors": end - start,
                "lines": bus.lines,
                "metres": metres,
                "pumps_per_line": per_line,
                "pumps": per_line * bus.lines,
                "direction": "climbs" if end > start else "falls",
                # Under a site partition every bus is within ONE site (a cross-site flow
                # is external to both), so the riser can be named to its building.
                "site": site_of.get(bus.from_stage, ""),
            }
        )
    out.sort(key=lambda d: (-d["floors"], -d["rate"]))
    return out


def _to_fnd(m: float) -> int:
    return max(1, math.ceil(m / FOUNDATION_M))


@dataclass
class _Placed:
    """One block's grid-packed position, in foundations, within its floor."""

    block: Block
    x_fnd: int
    y_fnd: int
    w_fnd: int
    d_fnd: int
    rotated: bool


#: Clear walkway kept between every block and the slab's edge, in foundations.
EDGE_FND = 1

#: Building mode's extra west margin (belt routing: room for the lift strip, its exits
#: and a belt corridor on the west edge), in foundations.
WEST_BAY_FND = 2

#: belt routing: the east edge carries the other lift strip and corridor.
EAST_BAY_FND = 2

#: A merger row beside each output side: one 4 x 4 m merger per machine, snapped to the
#: foundation grid (owner, 2026-10-02).
MERGER_ROW_FND = 1


def _manifold_rows(b: Block) -> tuple[int, int]:
    """(splitter rows, merger rows) in foundations: one 4 m splitter band per input,
    two to a row -- none for a folded block, whose input lane is inside its depth -- and
    MERGER_ROW_FND per output side; 0 when there are no inputs / outputs."""
    folded = bool(b.packed and b.packed.folded)
    ins = 0 if folded or not b.inputs else max(1, math.ceil(len(b.inputs) / 2))
    return ins, MERGER_ROW_FND if b.outputs else 0


def _rows_before(b: Block, rotated: bool = False) -> int:
    """Reserved rows on the body's input (v < 0) side: a folded block's south merger
    row, else its splitter rows. A rotated block reserves the larger side on both
    sides: routing mirrors it across its depth on E-input floors, and floor parity
    isn't known while packing."""
    pre = b.manifold_out_fnd if b.packed and b.packed.folded else b.manifold_in_fnd
    return max(pre, b.manifold_out_fnd) if rotated else pre


def _rows_after(b: Block, rotated: bool = False) -> int:
    """Reserved rows on the output (v >= depth) side; see _rows_before."""
    return _rows_before(b, True) if rotated else b.manifold_out_fnd


def _grid(dims: list[int], inner_w: int, inner_d: int, aisle_fnd: int) -> tuple[int, int, int]:
    """(cell size, columns, rows) for a grid of square cells holding blocks whose
    largest side is one of `dims`: cell = the biggest of them, so it fits any block
    in the set in either orientation; columns/rows = how many such cells (plus the
    aisle between them) fit the inner width/depth."""
    cell = max(dims)
    cols = max(1, (inner_w + aisle_fnd) // (cell + aisle_fnd))
    rows = max(1, (inner_d + aisle_fnd) // (cell + aisle_fnd))
    return cell, cols, rows


def _fit_slab(placed: list[_Placed], cap_fnd: int, east_fnd: int = 0) -> tuple[int, int]:
    """Smallest even x even slab (width, depth) in foundations holding these placed
    blocks plus the EDGE_FND walkway on the far sides -- the near sides already have
    it, since placement starts at EDGE_FND -- never above cap_fnd. Even sides are the
    owner's rule (spec 2026-10-01)."""
    w = max(p.x_fnd + p.w_fnd for p in placed) + EDGE_FND + east_fnd
    d = max(p.y_fnd + p.d_fnd for p in placed) + EDGE_FND
    return min(cap_fnd, w + w % 2), min(cap_fnd, d + d % 2)


#: A building group under this many foundations shares a floor rather than getting
#: its own (spec 2026-10-01, Section 1).
MERGE_BELOW_FND = 4

#: Building types that always share one floor group (owner, 2026-10-01): Smelters and
#: Foundries both turn ore into ingots off the same ore belts -- a smelting floor.
FLOOR_FAMILY = {"Smelter": "smelting", "Foundry": "smelting"}


def _mean_stage(blocks: list[Block]) -> float:
    """Machine-weighted mean chain stage: where a floor of these blocks stacks."""
    n = sum(b.machines for b in blocks) or 1
    return sum(b.stage * b.machines for b in blocks) / n


def _group_blocks(blocks: list[Block], buses: list[Bus]) -> list[tuple[str, list[Block]]]:
    """(label, blocks) per floor group: one per building type, then each group under
    MERGE_BELOW_FND foundations merged into the group it trades the most items/min
    with (either direction; ties to the lower mean stage), smallest first, until none
    is small or one is left. Label = member building names, most machines first."""
    by_type: dict[str, list[Block]] = {}
    for b in blocks:
        by_type.setdefault(FLOOR_FAMILY.get(b.building, b.building), []).append(b)
    members = list(by_type.values())

    def size(g: list[Block]) -> int:
        return sum(b.foundations for b in g)

    def trade(a: list[Block], c: list[Block]) -> float:
        ka, kc = {b.key for b in a}, {b.key for b in c}
        return sum(
            bus.rate for bus in buses
            if (ka & set(bus.producers) and kc & set(bus.consumers))
            or (kc & set(bus.producers) and ka & set(bus.consumers))
        )

    while len(members) > 1:
        small = [g for g in members if size(g) < MERGE_BELOW_FND]
        if not small:
            break
        g = min(small, key=size)
        others = [o for o in members if o is not g]
        partner = max(others, key=lambda o: (trade(g, o), -_mean_stage(o)))
        members = [o for o in others if o is not partner] + [partner + g]

    out = []
    for g in members:
        count: dict[str, int] = {}
        for b in g:
            count[b.building] = count.get(b.building, 0) + b.machines
        out.append((" + ".join(sorted(count, key=lambda k: (-count[k], k))), g))
    return out


def _pack_slab(
    blocks: list[Block], slab_fnd: int, aisle_fnd: int, slab_depth_fnd: int = 0
) -> tuple[list[list[_Placed]], list[Block], list[str]]:
    """Grid-pack blocks onto slab_fnd x slab_depth_fnd floors (square when
    slab_depth_fnd is 0): every block on a floor takes
    a cell sized to that floor's biggest block, placed in shared rows AND columns --
    manifolds line up across the whole floor, not just within their own row -- with
    an EDGE_FND walkway kept at the slab's edge and an aisle_fnd gap between cells in
    both directions, doubling as belt runs.

    Deterministic, in the given order: a block joins the current floor if the grid it
    would force still holds every block already there; otherwise it starts a new
    floor. A block that doesn't fit the slab's interior in either dimension is
    skipped and reported separately -- the caller gives it a floor of its own at its
    real size, not a warped fit.

    Not an optimal packer -- a floor's cell size is set by its biggest block, so one
    oversized-but-fitting block wastes real space around smaller neighbours on the
    same floor. This is for a quick visual gut-check, not maximum density -- see the
    spec's Known Limitations.
    """
    depth_fnd = slab_depth_fnd or slab_fnd
    inner_w = slab_fnd - 2 * EDGE_FND
    inner_d = depth_fnd - 2 * EDGE_FND
    oversized: list[Block] = []
    warnings: list[str] = []
    fitting: list[Block] = []
    for b in blocks:
        w0, d0 = _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)
        if max(w0, d0) > min(inner_w, inner_d):
            oversized.append(b)
            warnings.append(
                f"{b.name}: {w0}x{d0} foundations does not fit a {slab_fnd}x{depth_fnd} "
                f"slab (with its {EDGE_FND}-foundation edge walkway) in either "
                "orientation -- given its own floor at its real size"
            )
            continue
        fitting.append(b)

    floors: list[list[Block]] = [[]]
    dims: list[list[int]] = [[]]
    for b in fitting:
        d = max(_to_fnd(b.block_width_m), _to_fnd(b.block_depth_m))
        _cell, cols, rows = _grid(dims[-1] + [d], inner_w, inner_d, aisle_fnd)
        if dims[-1] and len(dims[-1]) + 1 > cols * rows:
            floors.append([b])
            dims.append([d])
        else:
            floors[-1].append(b)
            dims[-1].append(d)

    placed_floors: list[list[_Placed]] = []
    for floor_blocks in floors:
        if not floor_blocks:
            placed_floors.append([])
            continue
        cell, cols, _rows = _grid(
            [max(_to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)) for b in floor_blocks],
            inner_w, inner_d, aisle_fnd,
        )
        placed = []
        for i, b in enumerate(floor_blocks):
            col, row = i % cols, i // cols
            x = EDGE_FND + col * (cell + aisle_fnd)
            y = EDGE_FND + row * (cell + aisle_fnd)
            placed.append(_Placed(b, x, y, _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m), False))
        placed_floors.append(placed)

    return placed_floors, oversized, warnings


def _pack_rows(
    blocks: list[Block], slab_fnd: int, aisle_fnd: int, slab_depth_fnd: int = 0,
    west_fnd: int = 0, east_fnd: int = 0,
) -> tuple[list[list[_Placed]], list[Block], list[str]]:
    """One manifold per row: every block is turned so its long side runs along the
    slab's long side, starts flush at the EDGE_FND walkway, and gets a row to itself;
    rows stack across the slab's short side, aisle_fnd apart (the lane between two
    rows carries one row's output and the next row's input), in the given order. A
    block that can't fit a row -- longer than the long side, or wider than the short
    side -- is reported as oversized, like _pack_slab. west_fnd shifts every block
    that many foundations further east (a west bay); west_fnd + east_fnd shrink the span along x.
    A block's reserved manifold rows (manifold_in_fnd/_out_fnd) widen it across its
    row: the _Placed box is the reserved box, not the body.
    """
    depth_fnd = slab_depth_fnd or slab_fnd
    long_is_y = depth_fnd > slab_fnd
    inner_long = max(slab_fnd, depth_fnd) - 2 * EDGE_FND - (0 if long_is_y else west_fnd + east_fnd)
    inner_short = min(slab_fnd, depth_fnd) - 2 * EDGE_FND - (west_fnd + east_fnd if long_is_y else 0)
    floors: list[list[_Placed]] = [[]]
    oversized: list[Block] = []
    warnings: list[str] = []
    offset = 0  # along the short side, within the inner span
    for b in blocks:
        w0, d0 = _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)
        # Rendered width is along x: make the long side lie along the slab's long axis.
        rotated = w0 > d0 if long_is_y else d0 > w0
        w, d = (d0, w0) if rotated else (w0, d0)
        extra = _rows_before(b, rotated) + _rows_after(b, rotated)  # across: y, or x when turned
        w, d = (w + extra, d) if rotated else (w, d + extra)
        along, span = (d, w) if long_is_y else (w, d)
        if along > inner_long or span > inner_short:
            oversized.append(b)
            warnings.append(
                f"{b.name}: {w0}x{d0} foundations does not fit a row of a "
                f"{slab_fnd}x{depth_fnd} slab (with its {EDGE_FND}-foundation edge "
                "walkway) -- given its own floor at its real size"
            )
            continue
        start = offset + (aisle_fnd if floors[-1] else 0)
        if floors[-1] and start + span > inner_short:
            floors.append([])
            start = 0
        if long_is_y:
            x, y = EDGE_FND + west_fnd + start, EDGE_FND
        else:
            x, y = EDGE_FND + west_fnd, EDGE_FND + start
        floors[-1].append(_Placed(b, x, y, w, d, rotated))
        offset = start + span
    return floors, oversized, warnings


def _attach_crossing_buses(floors: list[Floor], buses: list[Bus]) -> None:
    """Attach each internal bus to every floor holding one of its consumers and
    sitting above the lowest floor holding one of its producers."""
    floor_of_block = {b.key: f.index for f in floors for b in f.blocks}
    # Crossing buses attach to EVERY floor that holds a consumer and sits above the
    # lowest floor holding a producer -- not to a single floor picked by stage number.
    # The final review found the old stage-level version wrong on the actual flagship
    # scenario: Steel Rotor (stage 3) sits on F1, and F1 was shown "receiving" Rotor
    # (which it makes itself) while NOT receiving Wire/Steel Pipe (which Steel Rotor
    # genuinely draws from F0) -- both of those stages' lowest floor happened to be F0,
    # so `to_floor <= from_floor` incorrectly skipped them. Routing by which floor
    # actually holds which block fixes this regardless of how a stage's blocks are
    # spread across floors.
    for floor in floors:
        crossing = []
        for bus in buses:
            if bus.external:
                continue
            producer_floors = {floor_of_block[p] for p in bus.producers if p in floor_of_block}
            if not producer_floors:
                continue
            lowest_producer_floor = min(producer_floors)
            consumer_here = any(floor_of_block.get(c) == floor.index for c in bus.consumers)
            if consumer_here and floor.index > lowest_producer_floor:
                crossing.append(bus)
        floor.buses = crossing


def _slab_floors(
    blocks: list[Block],
    buses: list[Bus],
    slab_fnd: int,
    aisle_fnd: int,
    slab_depth_fnd: int = 0,
    slab_layout: str = "grid",
) -> tuple[list[Floor], list[str]]:
    """Real floors from a shelf-packed slab: positions written back onto the blocks,
    one Floor per pack result (plus one per oversized block, slotted into chain order
    rather than appended after every packed floor), crossing buses attached to every
    floor that actually consumes them above their producer (slab mode never has a
    logistics floor)."""
    ordered = sorted(blocks, key=lambda b: (b.stage, -b.foundations))
    pack = _pack_rows if slab_layout == "rows" else _pack_slab
    packed_floors, oversized, warnings = pack(ordered, slab_fnd, aisle_fnd, slab_depth_fnd)

    # Build (min_stage, floor_blocks, slab_side_m_or_None) specs for both packed and
    # oversized floors, then order ALL of them by min_stage so an oversized block from
    # an early stage doesn't get stacked above a later stage's packed floor -- that
    # inverted build order (the final review found stage-0 Refinery floors landing
    # above stage-2 Generator floors on a slab-15 oil-fixture run) and, downstream,
    # broke fluid_head's floor-crossing math, which depends on floors being in chain
    # order to mean anything.
    specs: list[tuple[int, list[Block], float | None]] = []
    for placed in packed_floors:
        if not placed:
            continue
        for p in placed:
            p.block.x_fnd, p.block.y_fnd, p.block.rotated = p.x_fnd, p.y_fnd, p.rotated
        floor_blocks = [p.block for p in placed]
        specs.append((min(b.stage for b in floor_blocks), floor_blocks, slab_fnd * FOUNDATION_M))
    for b in oversized:
        specs.append((b.stage, [b], None))
    specs.sort(key=lambda s: s[0])  # stable: preserves each list's own internal order

    floors: list[Floor] = []
    for _min_stage, floor_blocks, slab_side in specs:
        stages = sorted({b.stage for b in floor_blocks})
        floors.append(
            Floor(
                index=len(floors),
                kind="production",
                stage=stages[0],
                stages=stages,
                height_m=_slab_floor_height(floor_blocks, warnings),
                blocks=floor_blocks,
                slab_side_m=slab_side,
                slab_depth_m=(slab_depth_fnd * FOUNDATION_M) if (slab_side and slab_depth_fnd) else None,
            )
        )

    _attach_crossing_buses(floors, buses)

    return floors, warnings


def _slab_floor_height(blocks: list[Block], warnings: list[str]) -> float:
    """FLOOR_M, or DOUBLE_FLOOR_M when a building plus FLOOR_HEADROOM_M outgrows it. A
    building with no headroom even under the double floor is warned about, once per
    building however many manifolds or floors it fills."""
    for b in blocks:
        need = b.height_m + FLOOR_HEADROOM_M
        if need > DOUBLE_FLOOR_M:
            msg = (f"{b.building} is {b.height_m:g} m tall: no headroom under a "
                   f"{DOUBLE_FLOOR_M:g} m double floor (needs {need:g} m)")
            if msg not in warnings:
                warnings.append(msg)
    fits = all(b.height_m + FLOOR_HEADROOM_M <= FLOOR_M for b in blocks)
    return FLOOR_M if fits else DOUBLE_FLOOR_M


def _building_floors(
    blocks: list[Block], buses: list[Bus], cap_fnd: int,
    wide_groups: frozenset[str] = frozenset(),
) -> tuple[list[Floor], list[str]]:
    """Floors grouped by building type (spec 2026-10-01): each group row-packed onto
    a cap_fnd x cap_fnd slab, one manifold per row (_pack_rows, WEST_BAY_FND west bay)
    with its splitter and merger rows reserved beside it and no aisle between rows --
    those rows are walkable and replace it (owner, 2026-10-02); a wide group keeps a
    2-foundation aisle -- spilling onto more floors of the same group, whole
    manifolds only, when it doesn't fit -- then every floor takes one shared slab: the
    smallest even x even size holding the largest floor's contents. Floors stack by machine-weighted mean stage, so smelting
    sits at the bottom and final assembly on top."""
    specs: list[tuple[float, str, list[Block], tuple[int, int] | None]] = []
    warnings: list[str] = []
    for label, group in _group_blocks(blocks, buses):
        ordered = sorted(group, key=lambda b: (b.stage, -b.foundations))
        for b in ordered:
            b.manifold_in_fnd, b.manifold_out_fnd = _manifold_rows(b)
        packed_floors, oversized, warns = _pack_rows(
            ordered, cap_fnd, 2 if label in wide_groups else 0, west_fnd=WEST_BAY_FND,
            east_fnd=EAST_BAY_FND,
        )
        warnings.extend(warns)
        for placed in packed_floors:
            if not placed:
                continue
            for p in placed:  # the body sits past its input-side reserved rows
                pre = _rows_before(p.block, p.rotated)
                p.block.x_fnd = p.x_fnd + (pre if p.rotated else 0)
                p.block.y_fnd = p.y_fnd + (0 if p.rotated else pre)
                p.block.rotated = p.rotated
            floor_blocks = [p.block for p in placed]
            specs.append((_mean_stage(floor_blocks), label, floor_blocks, _fit_slab(placed, cap_fnd, EAST_BAY_FND)))
        for b in oversized:
            specs.append((float(b.stage), label, [b], None))
    specs.sort(key=lambda s: s[0])  # stable: equal means keep group order
    # One slab for the whole stack (owner, 2026-10-01): every packed floor takes the
    # largest width and largest depth among them -- still even, still <= cap_fnd.
    # Oversized blocks' own floors (size None) keep their real size.
    sized = [s[3] for s in specs if s[3]]
    if sized:
        common = (max(w for w, _d in sized), max(d for _w, d in sized))
        specs = [(m, lbl, fb, common if size else None) for m, lbl, fb, size in specs]

    floors: list[Floor] = []
    for _mean, label, floor_blocks, size in specs:
        stages = sorted({b.stage for b in floor_blocks})
        floors.append(
            Floor(
                index=len(floors),
                kind="production",
                stage=stages[0],
                stages=stages,
                height_m=_slab_floor_height(floor_blocks, warnings),
                blocks=floor_blocks,
                group=label,
                slab_side_m=size[0] * FOUNDATION_M if size else None,
                slab_depth_m=size[1] * FOUNDATION_M if size else None,
            )
        )
    _attach_crossing_buses(floors, buses)
    return floors, warnings


def _decks_for(blocks: list[Block], cap: int) -> list[list[Block]]:
    """Split one chain stage across as many decks as a foundation cap allows.

    The uncapped layout answers "how big a site does this need" by giving each stage a
    deck of whatever size it wants -- 504x504 m on a measured oil plan. The question a
    player with a finished platform actually has is the reverse: *I have 30x30
    foundations, how many decks?* Same computation, run backwards.

    Blocks keep their order, so a deck still reads in build order, and a block larger
    than the cap gets a deck to itself rather than being silently dropped -- the caller
    is told instead.
    """
    if cap <= 0:
        return [blocks]
    decks: list[list[Block]] = []
    current: list[Block] = []
    used = 0
    for block in blocks:
        need = block.foundations
        if current and used + need > cap:
            decks.append(current)
            current, used = [], 0
        current.append(block)
        used += need
    if current:
        decks.append(current)
    return decks


#: Above this many production stages, the exact head ordering is not searched. 8! is
#: 40,320 permutations and instant; 12! is half a billion and is not. Measured plans run
#: to four or five stages, so the cap has never bitten -- it exists so that a pathological
#: plan degrades to the chain order with a note rather than hanging.
MAX_ORDERED_STAGES = 8


def _lift_cost(order: list[int], blocks: list[Block], buses: list[Bus]) -> float:
    """Pipe-storeys climbed under a given bottom-to-top stage order.

    Weighted by LINE COUNT rather than by raw rate, because the thing being paid for is
    pumps and a pump serves one pipe: 10,300 m3/min of water is 18 pipes, and lifting it
    one storey costs eighteen risers to pump, not "10,300 units of badness". Rate and
    lines are near-proportional, so this rarely changes the winner -- it changes what the
    number MEANS, and the number is quoted.

    Only the upward leg counts. A pipe running downhill is free, which is the whole reason
    reordering helps, and only pipes count at all: a belt does not care which way it runs.
    """
    at = {stage: position for position, stage in enumerate(order)}
    cost = 0.0
    for bus in buses:
        if bus.carrier != "pipe" or bus.external:
            continue
        start, end = at.get(bus.from_stage), at.get(bus.to_stage)
        if start is None or end is None:
            continue
        cost += bus.lines * max(0, end - start)
    return cost


def _water_stages(blocks: list[Block]) -> set[int]:
    """Stages holding a Water Extractor.

    Pinned to the bottom whatever the search prefers: water is the one fluid that cannot
    be drawn anywhere but sea level, so a stack that lifts water to reach it is not a
    build. Everything else is free to move.
    """
    return {b.stage for b in blocks if b.building_id == "Build_WaterPump_C"}


def order_stages_by_head(blocks: list[Block], buses: list[Bus]) -> tuple[list[int], list[str]]:
    """Bottom-to-top stage order that minimises fluid lift.

    Chain depth is a CORRECTNESS property -- a consumer above its producer reads in build
    order -- and it is not a physics property. Fluids do not care about chain depth: a pipe
    running downhill is free and one running uphill needs pumps. `fluid_head` has always
    said so and then ordered by chain depth anyway, which on the measured oil plan lifted
    water two storeys when one was available.

    Floors may be reordered freely because a pipe or belt runs in either direction. The
    only fixed point is water at the bottom.
    """
    from itertools import permutations

    stages = sorted({b.stage for b in blocks})
    notes: list[str] = []
    if len(stages) > MAX_ORDERED_STAGES:
        notes.append(
            f"{len(stages)} stages is past the {MAX_ORDERED_STAGES}-stage search limit, so "
            "floors keep chain order; the head figures below are still measured"
        )
        return stages, notes

    pinned = _water_stages(blocks)
    best, best_cost = stages, _lift_cost(stages, blocks, buses)
    for candidate in permutations(stages):
        order = list(candidate)
        # Water first, or not at all. Sorted so a tie is deterministic rather than
        # whichever permutation the iterator happened to reach first.
        if pinned and set(order[: len(pinned)]) != pinned:
            continue
        cost = _lift_cost(order, blocks, buses)
        if cost < best_cost - 1e-9:
            best, best_cost = order, cost
    if best != stages:
        notes.append(
            "floors are ordered to MINIMISE FLUID LIFT, not by chain depth, so a block may "
            "sit below something it feeds -- pipes run both ways and only the upward leg "
            "costs pumps"
        )
    if pinned:
        notes.append(
            "Water Extractors are pinned to the bottom deck: water is the one fluid that "
            "cannot be drawn anywhere but sea level"
        )
    return best, notes


def _pump_total(floors: list[Floor], buses: list[Bus], pump_head_m: float = 50.0) -> int:
    """Pumps a floor arrangement needs, for comparing two candidate stacks.

    The default head is the Mk2 pump, which is what the search assumes when the caller has
    not said. Which tier is actually available changes the count but almost never the
    ranking, since it scales every riser together.
    """
    stub = Layout(blocks=[], buses=buses, floors=floors)
    return sum(row["pumps"] for row in fluid_head(stub, pump_head_m))


def _floors(
    blocks: list[Block],
    buses: list[Bus],
    max_floor_foundations: int = 0,
    stage_order: list[int] | None = None,
) -> list[Floor]:
    stages = list(stage_order) if stage_order else sorted({b.stage for b in blocks})
    at = {stage: position for position, stage in enumerate(stages)}
    floors: list[Floor] = []
    index = 0
    for position, stage in enumerate(stages):
        on_stage = [b for b in blocks if b.stage == stage]
        for deck in _decks_for(on_stage, max_floor_foundations):
            index = _emit_deck(floors, index, stage, deck)
        if position < len(stages) - 1:
            # By POSITION in the stack, not by stage number. The two are the same under
            # chain order and diverge the moment floors are reordered for head -- a bus
            # between stages 1 and 3 crosses this deck only if the deck sits between
            # where those stages actually ended up.
            crossing = [
                bus
                for bus in buses
                if (
                    bus.from_stage in at
                    and bus.to_stage in at
                    and min(at[bus.from_stage], at[bus.to_stage])
                    <= position
                    < max(at[bus.from_stage], at[bus.to_stage])
                )
                or (bus.external and bus.from_stage == stage)
            ]
            floors.append(
                Floor(
                    index=index,
                    kind="logistics",
                    stage=None,
                    height_m=LOGISTICS_FLOOR_M,
                    buses=crossing,
                )
            )
            index += 1
    return floors


def _emit_deck(floors: list[Floor], index: int, stage: int, on_stage: list[Block]) -> int:
    """Append one production deck, sized by its tallest machine. Returns the next index."""
    tallest = max((b.height_m for b in on_stage), default=0.0)
    height = math.ceil((tallest + FLOOR_HEADROOM_M) / FLOOR_STEP_M) * FLOOR_STEP_M
    floors.append(
        Floor(index=index, kind="production", stage=stage, height_m=height, blocks=on_stage)
    )
    return index + 1


def build_layout(
    game: GameData,
    sol: Solution,
    belt_ipm: float = 780.0,
    pipe_m3min: float = 600.0,
    max_floor_foundations: int = 0,
    order_floors_by: str = "chain",
    slab_foundations: int = 0,
    aisle_foundations: int = 1,
    slab_depth_foundations: int = 0,
    slab_layout: str = "grid",
    group_by: str = "stage",
    max_slab_foundations: int = 16,
    wide_groups: frozenset[str] = frozenset(),
) -> Layout:
    """Decompose a solved plan into blocks, buses and floors.

    ``order_floors_by`` is "chain" (depth order, so the schematic reads in build order) or
    "head" (minimise fluid lift).

    ``slab_foundations`` (0 = off: floor partitioning matches upstream -- one stage per floor, a
    logistics deck between each pair -- but manifold shapes are one row / folded
    (2026-10-01), so foundation counts differ from upstream) packs blocks shelf-style onto square slabs of
    that many foundations per side, ``aisle_foundations`` apart, extractors excluded
    (they go to ``Layout.off_slab`` -- see ``_pack_slab``). ``slab_depth_foundations``
    makes the slab rectangular (``slab_foundations`` wide by this deep); 0 = square.
    ``slab_layout`` is "grid" (manifolds in shared rows and columns) or "rows" (one
    manifold per row, each along the slab's long side).

    ``group_by="building"`` (spec 2026-10-01) puts each building type on its own
    floor(s), merging groups under 4 foundations into their biggest trading partner,
    and gives every floor one shared slab -- the smallest even x even size that holds
    the largest floor, capped at ``max_slab_foundations``; the slab_* arguments are then ignored. "stage" (default)
    is the default stage-per-floor partitioning.

    ``wide_groups`` (building mode, internal: belt routing's retry) names floor groups
    re-packed with 2-foundation aisles.
    """
    group_by = (group_by or "stage").strip().casefold()
    if group_by not in ("stage", "building"):
        raise ValueError(f"group_by must be 'stage' or 'building', not {group_by!r}")
    if group_by == "building" and (max_slab_foundations < 4 or max_slab_foundations % 2):
        raise ValueError(
            f"max_slab_foundations must be an even number >= 4, not {max_slab_foundations}"
        )
    # The row length a manifold may have before it folds: the slab's inner short side.
    max_row_fnd = 0
    if group_by == "building":
        max_row_fnd = max_slab_foundations - 2 * EDGE_FND - WEST_BAY_FND - EAST_BAY_FND
    elif slab_foundations > 0:
        max_row_fnd = min(slab_foundations, slab_depth_foundations or slab_foundations) - 2 * EDGE_FND
    blocks = _blocks_from(game, sol, belt_ipm, pipe_m3min, max_row_fnd)
    _assign_stages(blocks)
    buses = _buses(game, blocks, sol, belt_ipm, pipe_m3min)

    warnings: list[str] = []
    off_slab: list[Block] = []
    if group_by == "building" or slab_foundations > 0:
        aisle_foundations = max(0, aisle_foundations)
        slab_depth_foundations = max(0, slab_depth_foundations)
        is_extractor = lambda b: bool(
            (bd := game.buildings.get(b.building_id)) and bd.is_extractor
        )
        off_slab = [b for b in blocks if is_extractor(b)]
        on_slab = [b for b in blocks if not is_extractor(b)]
        if group_by == "building":
            floors, slab_warnings = _building_floors(
                on_slab, buses, max_slab_foundations, wide_groups=wide_groups
            )
        else:
            floors, slab_warnings = _slab_floors(
                on_slab, buses, slab_foundations, aisle_foundations, slab_depth_foundations,
                (slab_layout or "grid").strip().casefold(),
            )
        warnings.extend(slab_warnings)
        return Layout(blocks=blocks, buses=buses, floors=floors, warnings=warnings, off_slab=off_slab)

    order = None
    if (order_floors_by or "chain").strip().casefold() == "head":
        order, head_notes = order_stages_by_head(blocks, buses)
        warnings.extend(head_notes)
        # The search minimises PIPE-STOREYS, which is a proxy: the real cost is pumps, and
        # pumps round up per line, so a 21% better proxy was worth only 4% of pumps on the
        # measured plan. A proxy that can be wrong in the small can be wrong in the large,
        # so both candidate stacks are built and counted, and the loser is discarded. Two
        # floor builds, against 40,320 if the search itself counted pumps.
        chain_floors = _floors(blocks, buses, max_floor_foundations, None)
        head_floors = _floors(blocks, buses, max_floor_foundations, order)
        best_head = min(
            (chain_floors, None), (head_floors, order), key=lambda pair: _pump_total(pair[0], buses)
        )
        if best_head[1] is None:
            warnings.append(
                "chain order needs no more pumps than the head-ordered stack here, so the "
                "floors are left in build order -- reordering has to earn it"
            )
        order = best_head[1]
    floors = _floors(blocks, buses, max_floor_foundations, order)

    missing = sorted({b.building for b in blocks if b.foundations == 0})
    if missing:
        warnings.append("no clearance data, excluded from the space budget: " + ", ".join(missing))
    split = [b for b in blocks if b.parts > 1]
    if split:
        worst = max(split, key=lambda b: b.parts)
        warnings.append(
            f"{len({b.label for b in split})} process(es) split across parallel "
            f"manifolds by throughput, up to {worst.parts}x ({worst.label})"
        )
    return Layout(blocks=blocks, buses=buses, floors=floors, warnings=warnings, off_slab=off_slab)
