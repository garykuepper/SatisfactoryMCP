"""Machine footprints, derived from mClearanceData.

Every buildable carries `mClearanceData`: a list of boxes describing the space it
occupies. Turning that into a usable footprint has one trap worth naming --

**The boxes can be rotated.** The Fuel-Powered Generator's clearance is several thin
boxes at 45-degree increments approximating a round machine. Taking the largest box
naively yields 22 x 4 m, when the real footprint is roughly 22 x 22 -- an understatement
of about 1,000 foundations across a 176-generator plan. So every box is transformed by
its `RelativeTransform` and the union of all of them is taken.

Boxes flagged `ExcludeForSnapping` are always skipped: those are approach clearances,
not the building's own volume. `CT_Soft` boxes are skipped only while a hard box
exists: on a machine, soft boxes are the overlap allowances AROUND the hard volume,
and counting them would overstate the machine. But every architecture piece --
foundation, wall, pillar, ramp, beam -- carries ONLY soft boxes, because soft
clearance is how the game lets them clip into each other, and for them the soft box
IS the piece's own size. So a buildable with no hard box falls back to the union of
its soft ones rather than reporting no size at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from .uestruct import as_list, parse_struct

__all__ = ["FOUNDATION_M", "Footprint", "Packed", "extract_footprint"]

#: Standard foundation edge length. Everything in Satisfactory grids to this.
FOUNDATION_M = 8.0

#: Longest side of a default block, as a multiple of its shortest. A judgement call, not
#: a game rule, and the only number in this module that is not derived: the unconstrained
#: tile optimum is routinely a ribbon (77 Water Extractors pack cheapest as 40x702 m,
#: saving 4% over a squarish block), which is correct arithmetic and not a build. Pass
#: `columns=` to override it in either direction.
MAX_BLOCK_ASPECT = 4.0


@dataclass(frozen=True)
class Footprint:
    """Axis-aligned bounding box of a building, in metres."""

    width_m: float  # X
    depth_m: float  # Y
    height_m: float  # Z

    @property
    def area_m2(self) -> float:
        return self.width_m * self.depth_m

    @property
    def foundations(self) -> int:
        """8 m foundations covered by one machine, ignoring shared edges."""
        import math

        return max(1, math.ceil(self.width_m / FOUNDATION_M)) * max(
            1, math.ceil(self.depth_m / FOUNDATION_M)
        )

    def __str__(self) -> str:
        return f"{self.width_m:g}x{self.depth_m:g}x{self.height_m:g}m"

    def pack(self, count: int, columns: int = 0) -> Packed:
        """Lay ``count`` of this machine out on foundations, and measure the result.

        ``foundations`` above is per-machine and says it ignores shared edges, which makes
        ``count x foundations`` an UPPER bound rather than a build. Two Water Extractors
        side by side span 40 m and need 5 tiles, not 6, so a real block is meaningfully
        cheaper than the naive product -- 77 of them measure **448 foundations** packed
        against 693 counted one at a time, a third less concrete.

        ``columns`` forces an arrangement; 1 gives a single row, whose LENGTH is what
        platform modules get measured against. Left at 0, every column count is tried and
        the cheapest BUILDABLE one wins, ties broken toward the squarer block.

        Two false starts, both recorded because both looked obviously right:

        *Squarest*, on the reasoning that perimeter waste costs tiles. Wrong: four Oil
        Extractors (8x13 m) laid 3x2 span 24x26 m and need **12** tiles, where four in a
        row span 32x13 m and need **8**. Unfilled grid slots, and depths landing just past
        a tile boundary, lose more than the perimeter saves.

        *Cheapest outright*, which produces ribbons -- the true optimum for 77 Water
        Extractors is 2x39, a 40x702 m strip that wastes nothing at either edge, saves 4%,
        and is not a thing anyone builds. Hence ``MAX_BLOCK_ASPECT``.

        The result is never worse than ``count x foundations``, because the single row is
        always among the candidates and ``ceil`` is subadditive -- n machines in a row can
        never need more tiles than n machines each given their own patch. That guarantee
        is what makes this strictly an improvement on the arithmetic it replaced, rather
        than one that is better on average and worse in places.
        """
        import math

        n = max(1, int(count))

        def measure(cols: int) -> Packed:
            cols = max(1, min(cols, n))
            rows = math.ceil(n / cols)
            width, depth = cols * self.width_m, rows * self.depth_m
            tiles = max(1, math.ceil(width / FOUNDATION_M)) * max(
                1, math.ceil(depth / FOUNDATION_M)
            )
            return Packed(
                count=n, columns=cols, rows=rows, width_m=width, depth_m=depth, foundations=tiles
            )

        if columns:
            return measure(int(columns))
        # Cheapest among BUILDABLE shapes. Unconstrained, the true optimum for 77 Water
        # Extractors is 2x39 -- a 40x702 m ribbon that happens to waste no tiles at either
        # edge. It saves 4% over a squarish block and nobody builds it. Capping the aspect
        # keeps the default something a player would actually lay, and `columns=` still
        # gives the long row on demand, where the length is the point.
        options = [measure(c) for c in range(1, n + 1)]
        buildable = [
            p
            for p in options
            if max(p.width_m, p.depth_m) <= MAX_BLOCK_ASPECT * min(p.width_m, p.depth_m)
        ]
        # The single row is ALWAYS a candidate, whatever its aspect. It is the shape the
        # old `n x foundations` bound implicitly assumed, so keeping it is what makes this
        # never worse than what it replaced -- without it a 5-machine Lookout Tower block
        # came out at 6 tiles against the old 5, and the change would have been an
        # improvement on average and a regression in places. It rarely wins on anything
        # large: 77 Water Extractors in one row is 579 tiles against the block's 448.
        return min(
            [*buildable, measure(n)], key=lambda p: (p.foundations, abs(p.width_m - p.depth_m))
        )


@dataclass(frozen=True)
class Packed:
    """A rectangular block of identical machines, snapped to the 8 m grid."""

    count: int
    columns: int
    rows: int
    width_m: float
    depth_m: float
    foundations: int

    def __str__(self) -> str:
        return f"{self.columns}x{self.rows} = {self.width_m:,.0f}x{self.depth_m:,.0f}m"


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _rotate(q: dict, v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Rotate a vector by a quaternion (x, y, z, w).

    v' = v + 2 * cross(q_xyz, cross(q_xyz, v) + w*v)
    """
    qx, qy, qz, qw = (_f(q.get(k)) for k in ("X", "Y", "Z", "W"))
    if (qx, qy, qz, qw) == (0.0, 0.0, 0.0, 0.0):
        return v
    vx, vy, vz = v
    # t = cross(q_xyz, v) + w*v
    tx = qy * vz - qz * vy + qw * vx
    ty = qz * vx - qx * vz + qw * vy
    tz = qx * vy - qy * vx + qw * vz
    # v + 2 * cross(q_xyz, t)
    return (
        vx + 2.0 * (qy * tz - qz * ty),
        vy + 2.0 * (qz * tx - qx * tz),
        vz + 2.0 * (qx * ty - qy * tx),
    )


def _corners(mn: dict, mx: dict) -> list[tuple[float, float, float]]:
    x0, y0, z0 = (_f(mn.get(k)) for k in ("X", "Y", "Z"))
    x1, y1, z1 = (_f(mx.get(k)) for k in ("X", "Y", "Z"))
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def extract_footprint(raw: object) -> Footprint | None:
    """Union AABB of a building's own clearance boxes, in metres.

    Hard boxes when the buildable has any; otherwise its soft ones. Architecture
    pieces carry ONLY `CT_Soft` boxes (soft clearance is what lets a wall meet a
    foundation without a collision refusal), so skipping soft unconditionally
    reported every foundation, wall, pillar, ramp and beam as having no size --
    which sent a player out to measure a Big Pillar Support in-game.
    """
    entries = [
        e
        for e in as_list(parse_struct(raw))
        if isinstance(e, dict)
        # Approach clearance is never the building's volume, hard or soft.
        and str(e.get("ExcludeForSnapping", "")).strip().lower() != "true"
    ]
    hard = _union(e for e in entries if e.get("Type") != "CT_Soft")
    # No hard box anywhere: the soft union IS the piece (and cannot overstate a hard
    # volume that does not exist). Never mixed with hard boxes -- on machines the
    # soft boxes are overlap allowances around the hard one, and the Fuel Generator
    # would grow past the 20x20 its own hard boxes measure.
    return hard if hard is not None else _union(entries)


def _union(entries) -> Footprint | None:
    """Axis-aligned union of transformed clearance boxes, or None for no boxes."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False

    for entry in entries:
        box = entry.get("ClearanceBox")
        if not isinstance(box, dict):
            continue
        mn, mx = box.get("Min"), box.get("Max")
        if not isinstance(mn, dict) or not isinstance(mx, dict):
            continue

        transform = entry.get("RelativeTransform") or {}
        rotation = transform.get("Rotation") if isinstance(transform, dict) else None
        translation = transform.get("Translation") if isinstance(transform, dict) else None
        tx, ty, tz = (
            (_f(translation.get(k)) for k in ("X", "Y", "Z"))
            if isinstance(translation, dict)
            else (0.0, 0.0, 0.0)
        )

        for corner in _corners(mn, mx):
            point = _rotate(rotation, corner) if isinstance(rotation, dict) else corner
            for axis, (value, offset) in enumerate(zip(point, (tx, ty, tz))):
                world = value + offset
                lo[axis] = min(lo[axis], world)
                hi[axis] = max(hi[axis], world)
            seen = True

    if not seen:
        return None
    return Footprint(
        width_m=round((hi[0] - lo[0]) / 100.0, 2),
        depth_m=round((hi[1] - lo[1]) / 100.0, 2),
        height_m=round((hi[2] - lo[2]) / 100.0, 2),
    )
