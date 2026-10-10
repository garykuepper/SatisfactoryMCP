"""The schematic's parts: blocks of machines, the buses between them, and the floors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ....core.gamedata.footprint import FOUNDATION_M, Packed

__all__ = ["DOUBLE_FLOOR_M", "FLOOR_M", "LOGISTICS_FLOOR_M", "Block", "Bus", "Floor", "Layout"]

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
    inputs: dict[str, float] = field(default_factory=dict[str, float])
    outputs: dict[str, float] = field(default_factory=dict[str, float])
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
    #: Building mode only (0 otherwise): foundation rows reserved outside the body for
    #: the splitters (input side, one 4 m band per input, two per row) and the merger
    #: (output/north side; 0 when the body's own rounding pad holds the merger band and
    #: walk lane -- see packing._manifold_rows; a folded block's south merger row is always
    #: reserved, see packing._rows_before). x_fnd/y_fnd stay the body's position.
    manifold_in_fnd: int = 0
    manifold_out_fnd: int = 0
    #: Building mode: the block has splitter/merger bands (even when its rows are 0 --
    #: a merger band can sit in the body's own rounding pad, see packing._manifold_rows).
    manifold_rows: bool = False

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
    producers: list[str] = field(default_factory=list[str])
    consumers: list[str] = field(default_factory=list[str])
    from_stage: int = 0
    to_stage: int = 0
    external: bool = False  # enters or leaves the site


@dataclass
class Floor:
    index: int
    kind: str  # production | logistics
    stage: int | None
    height_m: float
    blocks: list[Block] = field(default_factory=list[Block])
    buses: list[Bus] = field(default_factory=list[Bus])
    #: Which declared site this floor belongs to, set when floors are stacked per site;
    #: empty outside a site partition.
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
    #: stage's blocks.
    stages: list[int] = field(default_factory=list[int])
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
        just the covered part), otherwise the block sum (floor partitioning as upstream;
        block shapes are one row / folded since 2026-10-01)."""
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
    warnings: list[str] = field(default_factory=list[str])
    #: Extractors -- they stand on their resource node, not on a factory floor. Always
    #: empty when slab_foundations=0 (upstream keeps extractors on their stage's floor).
    off_slab: list[Block] = field(default_factory=list[Block])

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
