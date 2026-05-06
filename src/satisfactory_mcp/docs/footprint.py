"""Machine footprints, derived from mClearanceData.

Every buildable carries `mClearanceData`: a list of boxes describing the space it
occupies. Turning that into a usable footprint has one trap worth naming --

**The boxes can be rotated.** The Fuel-Powered Generator's clearance is several thin
boxes at 45-degree increments approximating a round machine. Taking the largest box
naively yields 22 x 4 m, when the real footprint is roughly 22 x 22 -- an understatement
of about 1,000 foundations across a 176-generator plan. So every box is transformed by
its `RelativeTransform` and the union of all of them is taken.

Boxes flagged `ExcludeForSnapping` or typed `CT_Soft` are skipped: those are approach
clearances and soft overlaps, not the building's own volume.
"""

from __future__ import annotations

from dataclasses import dataclass

from .uestruct import as_list, parse_struct

__all__ = ["FOUNDATION_M", "Footprint", "extract_footprint"]

#: Standard foundation edge length. Everything in Satisfactory grids to this.
FOUNDATION_M = 8.0


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
    """Union AABB of a building's own clearance boxes, in metres."""
    entries = as_list(parse_struct(raw))
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Approach clearance and soft overlaps are not the building's volume.
        if str(entry.get("ExcludeForSnapping", "")).strip().lower() == "true":
            continue
        if entry.get("Type") == "CT_Soft":
            continue
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
