"""Floor packing: blocks onto foundation slabs, as manifold rows with walkways.

Two modes, both off by default (``build_layout`` then partitions floors as upstream):
``slab_floors`` packs stages onto fixed-size slabs (grid or rows), and
``building_floors`` gives each building type its own floor(s) on one shared slab
(spec docs/superpowers/specs/2026-10-01-*). Extractors never reach here: they stand on
their nodes (``Layout.off_slab``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ....core.gamedata.footprint import FOUNDATION_M
from .model import DOUBLE_FLOOR_M, FLOOR_HEADROOM_M, FLOOR_M, Block, Bus, Floor

__all__ = ["EAST_BAY_FND", "EDGE_FND", "WEST_BAY_FND", "building_floors", "slab_floors"]


def _to_fnd(m: float) -> int:
    return max(1, math.ceil(m / FOUNDATION_M))


#: One block in a row being packed: (block, rotated, w, d, along, span), in foundations.
_RowItem = tuple[Block, bool, int, int, int, int]


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

#: Building mode: the gap between manifolds sharing a row -- a belt corridor (owner,
#: 2026-10-02).
ROW_GAP_FND = 1

#: A merger row beside each output side: one 4 x 4 m merger per machine, snapped to the
#: foundation grid (owner, 2026-10-02).
MERGER_ROW_FND = 1

#: A merger band (4 m) plus the walk lane past it (2 m): when the body's output-side
#: rounding pad is at least this, they fit inside its own foundations (owner, 2026-10-02).
MERGER_PAD_M = 6.0


def _manifold_rows(b: Block) -> tuple[int, int]:
    """(splitter rows, merger rows) in foundations: one 4 m splitter band per input,
    two to a row -- none for a folded block, whose input lane is inside its depth -- and
    MERGER_ROW_FND on the output (north) side; 0 when there are no inputs / outputs, or
    when the body's rounding pad (whole foundations minus its depth) already holds the
    merger band and walk lane. A folded block's south merger row is _rows_before's."""
    folded = bool(b.packed and b.packed.folded)
    ins = 0 if folded or not b.inputs else max(1, math.ceil(len(b.inputs) / 2))
    pad = _to_fnd(b.block_depth_m) * FOUNDATION_M - b.block_depth_m
    return ins, MERGER_ROW_FND if b.outputs and pad < MERGER_PAD_M - 1e-9 else 0


def _rows_before(b: Block, rotated: bool = False) -> int:
    """Reserved rows on the body's input (v < 0) side: a folded block's south merger
    row (always: its band is outside the body), else its splitter rows. A rotated block
    reserves the larger side on both sides: routing mirrors it across its depth on
    E-input floors, and floor parity isn't known while packing -- and at least
    MERGER_ROW_FND when it has outputs, since a mirrored merger band leaves the body's
    pad for the far side."""
    merger = MERGER_ROW_FND if b.manifold_rows and b.outputs else 0
    pre = merger if b.packed and b.packed.folded else b.manifold_in_fnd
    return max(pre, b.manifold_out_fnd, merger) if rotated else pre


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
            bus.rate
            for bus in buses
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

    out: list[tuple[str, list[Block]]] = []
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
            inner_w,
            inner_d,
            aisle_fnd,
        )
        placed: list[_Placed] = []
        for i, b in enumerate(floor_blocks):
            col, row = i % cols, i // cols
            x = EDGE_FND + col * (cell + aisle_fnd)
            y = EDGE_FND + row * (cell + aisle_fnd)
            placed.append(
                _Placed(b, x, y, _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m), False)
            )
        placed_floors.append(placed)

    return placed_floors, oversized, warnings


def _pack_rows(
    blocks: list[Block],
    slab_fnd: int,
    aisle_fnd: int,
    slab_depth_fnd: int = 0,
    west_fnd: int = 0,
    east_fnd: int = 0,
    row_gap: int | None = None,
    row_len: int = 0,
) -> tuple[list[list[_Placed]], list[Block], list[str]]:
    """Manifold rows: every block is turned so its long side runs along the slab's
    long side, starting flush at the EDGE_FND walkway; rows stack across the slab's
    short side, aisle_fnd apart (the lane between two rows carries one row's output
    and the next row's input), in the given order. One manifold per row unless
    row_gap is set (building mode, owner 2026-10-02): then short manifolds share a
    row left to right, row_gap foundations apart, while they fit its length
    (next-fit, in order); a row is as deep as its deepest block, and every body in it
    starts on one line -- a block with fewer splitter rows than its neighbours is
    padded, so its bands still sit against its body. A rotated block keeps a row to
    itself. A block that can't fit a row -- longer than the long side, or wider than
    the short side -- is reported as oversized, like _pack_slab. west_fnd shifts
    every block that many foundations further east (a west bay); west_fnd + east_fnd
    shrink the span along x. A block's reserved manifold rows
    (manifold_in_fnd/_out_fnd) widen it across its row: the _Placed box is the
    reserved box, not the body.
    """
    depth_fnd = slab_depth_fnd or slab_fnd
    long_is_y = depth_fnd > slab_fnd
    inner_long = max(slab_fnd, depth_fnd) - 2 * EDGE_FND - (0 if long_is_y else west_fnd + east_fnd)
    inner_short = (
        min(slab_fnd, depth_fnd) - 2 * EDGE_FND - (west_fnd + east_fnd if long_is_y else 0)
    )
    floors: list[list[_Placed]] = [[]]
    oversized: list[Block] = []
    warnings: list[str] = []
    offset = 0  # along the short side, within the inner span

    def pre(t: _RowItem) -> int:  # splitter rows before the body, across the row
        return 0 if t[1] else _rows_before(t[0])

    def depth(row: list[_RowItem]) -> int:
        top = max(pre(t) for t in row)
        return max(top - pre(t) + t[5] for t in row)

    def place(row: list[_RowItem]) -> None:
        nonlocal offset
        start = offset + (aisle_fnd if floors[-1] else 0)
        if floors[-1] and start + depth(row) > inner_short:
            floors.append([])
            start = 0
        top, pos = max(pre(t) for t in row), 0
        for t in row:
            b, rotated, w, d, along, _span = t
            across = start + top - pre(t)
            if long_is_y:
                x, y = EDGE_FND + west_fnd + across, EDGE_FND + pos
            else:
                x, y = EDGE_FND + west_fnd + pos, EDGE_FND + across
            floors[-1].append(_Placed(b, x, y, w, d, rotated))
            pos += along + (row_gap or 0)
        offset = start + depth(row)

    row: list[_RowItem] = []
    for b in blocks:
        w0, d0 = _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)
        # Rendered width is along x: make the long side lie along the slab's long axis.
        # A folded block stays unturned: it folded to get SHORTER along its row, and
        # turning its two-row depth back along the row undoes that (2026-10-04).
        folded = bool(b.packed and b.packed.folded)
        rotated = not folded and (w0 > d0 if long_is_y else d0 > w0)

        def fit(
            rotated: bool, b: Block = b, w0: int = w0, d0: int = d0
        ) -> tuple[int, int, int, int]:
            w, d = (d0, w0) if rotated else (w0, d0)
            extra = _rows_before(b, rotated) + _rows_after(
                b, rotated
            )  # across: y, or x when turned
            w, d = (w + extra, d) if rotated else (w, d + extra)
            return (w, d, *((d, w) if long_is_y else (w, d)))

        w, d, along, span = fit(rotated)
        # Turning adds the manifold rows to the width; if that no longer fits but the
        # unturned block does, keep it unturned (a lone Refinery on 10x10, 2026-10-04).
        if rotated and (along > inner_long or span > inner_short):
            alt = fit(False)
            if alt[2] <= inner_long and alt[3] <= inner_short:
                rotated, (w, d, along, span) = False, alt
        if along > inner_long or span > inner_short:
            oversized.append(b)
            warnings.append(
                f"{b.name}: {w0}x{d0} foundations does not fit a row of a "
                f"{slab_fnd}x{depth_fnd} slab (with its {EDGE_FND}-foundation edge "
                "walkway) -- given its own floor at its real size"
            )
            continue
        t = (b, rotated, w, d, along, span)
        used = sum(r[4] for r in row) + len(row) * (row_gap or 0)
        if (
            row_gap is not None
            and row
            and not rotated
            and not row[0][1]
            and used + along <= min(inner_long, row_len or inner_long)
            and depth(row + [t]) <= inner_short
        ):
            row.append(t)
            continue
        if row:
            place(row)
        row = [t]
    if row:
        place(row)
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
        crossing: list[Bus] = []
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


def slab_floors(
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
                slab_depth_m=(slab_depth_fnd * FOUNDATION_M)
                if (slab_side and slab_depth_fnd)
                else None,
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
            msg = (
                f"{b.building} is {b.height_m:g} m tall: no headroom under a "
                f"{DOUBLE_FLOOR_M:g} m double floor (needs {need:g} m)"
            )
            if msg not in warnings:
                warnings.append(msg)
    fits = all(b.height_m + FLOOR_HEADROOM_M <= FLOOR_M for b in blocks)
    return FLOOR_M if fits else DOUBLE_FLOOR_M


def building_floors(
    blocks: list[Block],
    buses: list[Bus],
    cap_fnd: int,
    wide_groups: frozenset[str] = frozenset(),
) -> tuple[list[Floor], list[str]]:
    """Floors grouped by building type (spec 2026-10-01): each group row-packed onto
    a cap_fnd x cap_fnd slab in manifold rows (_pack_rows, WEST_BAY_FND west bay; short
    manifolds share a row, ROW_GAP_FND apart) with their splitter and merger rows
    reserved beside them and no aisle between rows --
    those rows are walkable and replace it (owner, 2026-10-02); a wide group keeps a
    2-foundation aisle -- spilling onto more floors of the same group, whole
    manifolds only, when it doesn't fit -- then every floor takes one shared slab: the
    smallest even x even size holding the largest floor's contents. Floors stack by machine-weighted mean stage, so smelting
    sits at the bottom and final assembly on top."""
    specs: list[tuple[float, str, list[Block], tuple[int, int] | None]] = []
    warnings: list[str] = []
    # Shared rows fill the longest manifold's length, not the cap: sharing shouldn't
    # widen the slab (owner, 2026-10-02).
    row_len = max(
        (max(_to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)) for b in blocks if b.packed),
        default=0,
    )
    for label, group in _group_blocks(blocks, buses):
        ordered = sorted(group, key=lambda b: (b.stage, -b.foundations))
        for b in ordered:
            b.manifold_in_fnd, b.manifold_out_fnd = _manifold_rows(b)
            b.manifold_rows = True
        packed_floors, oversized, warns = _pack_rows(
            ordered,
            cap_fnd,
            2 if label in wide_groups else 0,
            west_fnd=WEST_BAY_FND,
            east_fnd=EAST_BAY_FND,
            row_gap=ROW_GAP_FND,
            row_len=row_len,
        )
        warnings.extend(warns)
        for placed in packed_floors:
            if not placed:
                continue
            for p in placed:  # the body sits past its input-side reserved rows (and padding)
                pre = _rows_before(p.block, p.rotated)
                p.block.x_fnd = p.x_fnd + (pre if p.rotated else 0)
                p.block.y_fnd = p.y_fnd + (0 if p.rotated else pre)
                p.block.rotated = p.rotated
            floor_blocks = [p.block for p in placed]
            specs.append(
                (
                    _mean_stage(floor_blocks),
                    label,
                    floor_blocks,
                    _fit_slab(placed, cap_fnd, EAST_BAY_FND),
                )
            )
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
