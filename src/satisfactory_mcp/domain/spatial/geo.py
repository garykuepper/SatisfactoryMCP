"""Exact map geometry. No hand-authored region shapes here.

Coordinate frame, established four independent ways (strongest: the wiki Crash_Site
table's 118 pods carry both a Region column and raw save coordinates, and joining
them to the vendored crash-site table matches 117/118 to sub-centimetre)::

    -Y = north      +X = east      +Z = up      1 m = 100 cm exactly

Everything in this module is derived and exact. Fuzzy biome *names* live in
spatial.regions and are advisory only -- they never feed a calculation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "CM_PER_M",
    "DIRECTIONS",
    "GRID_CELL",
    "Cluster",
    "bbox",
    "bearing_deg",
    "centroid",
    "cluster",
    "diameter_m",
    "direction_of",
    "distance_3d_m",
    "distance_m",
    "grid_cell",
    "in_direction",
]

CM_PER_M = 100.0

#: Biome grid: 1.024 km cells, numbered from the SOUTH-WEST corner. [WIKI]
GRID_CELL = 102_400.0
GRID_X0 = -319_600.0  # west edge of column X0
GRID_Y0_SOUTH = 302_800.0  # south edge of row Y0

#: Content extents measured over 2,371 static world objects.
CONTENT_BBOX = (-298_838.0, -314_104.0, 406_564.0, 304_196.0)  # minx, miny, maxx, maxy

#: Compass bearings in degrees, clockwise from north.
DIRECTIONS: dict[str, float] = {
    "north": 0.0,
    "northeast": 45.0,
    "east": 90.0,
    "southeast": 135.0,
    "south": 180.0,
    "southwest": 225.0,
    "west": 270.0,
    "northwest": 315.0,
}
_ALIASES = {
    "n": "north",
    "ne": "northeast",
    "e": "east",
    "se": "southeast",
    "s": "south",
    "sw": "southwest",
    "w": "west",
    "nw": "northwest",
}


def grid_cell(x: float, y: float) -> str:
    """Biome grid cell label, e.g. ``"X3Y4"``. Exact, no interpolation."""
    i = math.floor((x - GRID_X0) / GRID_CELL)
    j = math.floor((GRID_Y0_SOUTH - y) / GRID_CELL)
    return f"X{i}Y{j}"


def bearing_deg(x: float, y: float, ox: float = 0.0, oy: float = 0.0) -> float:
    """Compass bearing from (ox, oy) to (x, y), degrees clockwise from north.

    The negated Y is the thing naive implementations get wrong: north is -Y, so
    ``atan2(dx, -dy)`` -- not ``atan2(dy, dx)``.
    """
    return math.degrees(math.atan2(x - ox, -(y - oy))) % 360.0


def direction_of(x: float, y: float, ox: float = 0.0, oy: float = 0.0) -> str:
    """Nearest of the eight compass names."""
    b = bearing_deg(x, y, ox, oy)
    return min(DIRECTIONS, key=lambda name: _angle_gap(b, DIRECTIONS[name]))


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def normalise_direction(name: str) -> str:
    key = name.strip().casefold().replace("-", "").replace(" ", "")
    key = _ALIASES.get(key, key)
    if key not in DIRECTIONS:
        raise ValueError(f"unknown direction {name!r}; use one of {sorted(DIRECTIONS)}")
    return key


def in_direction(
    x: float,
    y: float,
    direction: str,
    origin: tuple[float, float] | None = None,
    half_angle: float = 60.0,
) -> bool:
    """Cone test, falling back to a hemisphere when no origin is given.

    With no origin the test is a pure hemisphere about the map centre, which is what
    a question like "what oil is in the north" actually means. With an origin it is a
    cone from the player, meaning "north of me".
    """
    d = normalise_direction(direction)
    if origin is None:
        return _hemisphere(x, y, d)
    b = bearing_deg(x, y, origin[0], origin[1])
    return _angle_gap(b, DIRECTIONS[d]) <= half_angle


def _hemisphere(x: float, y: float, direction: str) -> bool:
    target = DIRECTIONS[direction]
    b = bearing_deg(x, y, 0.0, 0.0)
    return _angle_gap(b, target) <= 90.0


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Planar XY distance in metres.

    Z is deliberately excluded: it spans only 0.64 km and matters for pipe head,
    not for proximity.
    """
    return math.dist(a, b) / CM_PER_M


def distance_3d_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Straight-line distance including Z, in metres.

    Separate from ``distance_m`` rather than a flag on it, because the choice is a
    modelling decision and not a detail. ``distance_m`` drops Z on purpose: for
    "is this near that" a 40 m climb is noise against a 400 m walk.

    A pipe RUN is the opposite case -- the vertical leg is real pipe you have to build and
    pump through -- so a trunk's length includes it. Passing 3-tuples to ``math.dist`` and
    letting it silently do 3D, which is what this replaced, meant the distinction lived in
    the shape of a tuple rather than in the name of the function.
    """
    return math.dist(a, b) / CM_PER_M


def centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """Mean XY of a set of points, in the units they came in. ``None`` when empty.

    ``None`` rather than the origin, because (0, 0) is a real and rather important place
    on this map -- it is the world centre every compass direction is measured from -- so
    an empty set silently answering "the middle of the map" would be a plausible wrong
    location rather than an obvious one. Every caller already had to handle the empty
    case; this makes them handle it in the type.
    """
    if not points:
        return None
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def bbox(points: Sequence[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    """Axis-aligned extent as ``(x_min, y_min, x_max, y_max)``, in the units given.

    ``None`` when empty, for the reason ``centroid`` gives: a zero-size box at (0, 0) is
    a real and rather important place on this map, so an empty set answering with one
    would frame the world centre rather than say there was nothing to frame.

    A single point yields a degenerate box, which is the truth about a one-machine
    factory. Padding it into something a viewport can use is the caller's decision --
    how much padding is "enough" depends on what the caller is drawing.
    """
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def diameter_m(points: Sequence[tuple[float, float]]) -> float:
    """Largest pairwise distance in metres -- the honest measure of spread.

    Only ``i < j`` pairs are compared. The two inline copies this replaced iterated
    ``for a in points for b in points``, which computes every pair twice plus the zero
    diagonal: identical answer, double the work, and at 563 machines that is 317k
    distance calls instead of 158k.
    """
    if len(points) < 2:
        return 0.0
    return max(
        distance_m(points[i], points[j])
        for i in range(len(points))
        for j in range(i + 1, len(points))
    )


@dataclass
class Cluster:
    """A group of nearby nodes. Named by CONTENT, never by biome."""

    members: list[dict]

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def centroid(self) -> tuple[float, float, float]:
        n = len(self.members)
        return (
            sum(m["x"] for m in self.members) / n,
            sum(m["y"] for m in self.members) / n,
            sum(m["z"] for m in self.members) / n,
        )

    @property
    def diameter_m(self) -> float:
        """Largest pairwise distance -- the honest measure of how spread out it is."""
        return diameter_m([(m["x"], m["y"]) for m in self.members])

    @property
    def grid_cell(self) -> str:
        cx, cy, _ = self.centroid
        return grid_cell(cx, cy)

    def kinds(self) -> dict[str, int]:
        """Node kinds present.

        Kind must never be inferred from a single member: at a 200 m link distance
        one real cluster merges 6 well satellites with a plain node 85 m away, and
        assuming 'well' for all 7 understates the field by 120 m3/min.
        """
        out: dict[str, int] = {}
        for m in self.members:
            out[m["kind"]] = out.get(m["kind"], 0) + 1
        return out

    def purities(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.members:
            out[m["purity"]] = out.get(m["purity"], 0) + 1
        return out


def cluster(nodes: list[dict], link_m: float = 200.0) -> list[Cluster]:
    """Single-linkage clustering on XY.

    200 m is the empirically right link distance: it recovers the real oil fields
    (max within-field spread 288 m, far smaller than any between-field gap).
    """
    remaining = list(nodes)
    out: list[Cluster] = []
    while remaining:
        seed = remaining.pop()
        group = [seed]
        changed = True
        while changed:
            changed = False
            for cand in list(remaining):
                cp = (cand["x"], cand["y"])
                if any(distance_m(cp, (m["x"], m["y"])) <= link_m for m in group):
                    group.append(cand)
                    remaining.remove(cand)
                    changed = True
        out.append(Cluster(members=group))
    out.sort(key=lambda c: (-c.size, c.centroid[1]))
    return out
