"""Foundation slabs: what the player physically built as one thing.

The fourth signal, and on the reference save the sharpest one. Measured against the
player's own list of factories:

============================  =======  ==========================================
signal                        pieces   failure
============================  =======  ==========================================
power islands (no towers)           9  one island holds 476 machines
material components                35  19 are fragments; splits one factory in two
**foundation slabs**              101  **every named factory has its own slab**
============================  =======  ==========================================

Slabs succeed where the others fail because a player builds a platform, then fills it.
Belts and wires cross between platforms freely -- that is the whole point of them --
but a foundation is only ever placed against another foundation deliberately.

Adjacency is geometric, because snapping is a build-time UI concept that is not
serialized: two 8 m foundations that touch have centres 800 cm apart. Three link rules,
each measured rather than assumed:

* **face adjacency** -- centres within ``LINK_XY`` at the same height. The cutoff sits
  between a shared face (800 cm) and a shared corner (1131 cm), so diagonal-only
  contact does not weld two platforms together.
* **stacked floors** -- overlapping in XY within ``LINK_Z``. A multi-storey factory is
  one structure. Without this the tor factory reads as three separate platforms that
  happen to share a footprint.
* **ramps, stairs and walls** -- these join the union as nodes of their own, so a RUN of
  them bridges a gap no single 8 m piece could span. Chaining is the whole point: asking
  only whether one piece touches two slabs finds nothing, because the interesting case is
  slab -> wall -> wall -> slab. Tested as single pieces, zero of 1,937 walls touch two
  slabs; tested as chains, they join four pairs.

**Catwalks are excluded, and that is the whole trick.** Scored against the player's own
twelve factories, where purity is the share of a label's machines landing on its single
dominant slab, and a collision is one slab claimed by two different factories:

=================  =====  ======  ==========================================
bridging rule      slabs  purity  collisions
=================  =====  ======  ==========================================
nothing                    90    0.68  none
walls only                 71    0.99  none
ramps + stairs             42    0.99  none
**ramps + stairs + walls** 41  **0.99**  **none**
catwalks only              85    0.78  none
plus catwalks              46    0.90  tier 1&2 welded to the tor factory
=================  =====  ======  ==========================================

Ramps connect the floors of one structure; catwalks are the long walkways a player runs
BETWEEN distant platforms. Chaining catwalks scores the highest purity of any rule and is
still wrong, because the one thing it merges is two genuinely separate factories. An
over-segmented slab can be merged by naming; an over-merged one cannot be split.

Walls earn their place on the same evidence. Alone they take 90 slabs down to 71; added
to ramps and stairs they take 42 to 41, with purity unchanged at 0.987 and still no
collision. They buy little on this save because ramps already cover most of the same
joins, but they are structurally the right kind of edge and they cost nothing.

**Not every factory sits on foundations.** Two of the player's twelve -- the concrete
setup and the copper setup -- are built straight on the ground and have no slab at all.
Slabs are therefore another candidate signal, never the arbiter.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

__all__ = ["LINK_XY", "LINK_Z", "STAND_ON", "Slab", "Structures", "build_structures"]

#: Face-adjacency cutoff in cm. Between a shared face (800) and a shared corner (1131).
LINK_XY = 830.0

#: Vertical reach in cm for stacked floors, measured rather than guessed. Scored against
#: the player's twelve named factories, purity runs 0.84 at 450 cm (one wall), 0.85 at
#: 900, 0.94 at 1200 and 0.99 at 1600 -- with no collision at any of them. Past 1600 the
#: purity stops improving and only the merge risk grows, so the knee is the value. Four
#: storeys sounds generous until you notice a refinery deck is not built at wall height.
LINK_Z = 1600.0

#: How far under a machine to look for the tile it stands on, in cm.
STAND_ON = 600.0

#: Connective tissue. Catwalks are POINTEDLY absent -- see the module docstring.
_BRIDGE = ("Ramp", "Stair", "Wall")
_FOUNDATION = ("Foundation", "Platform")

_CELL = 800.0


@dataclass
class Slab:
    """One connected platform."""

    index: int
    tiles: int
    centre: tuple[float, float, float]
    extent: tuple[float, float]
    z_span: tuple[float, float]

    @property
    def storeys(self) -> int:
        return max(1, round((self.z_span[1] - self.z_span[0]) / 400.0) + 1)


@dataclass
class Structures:
    """Slabs, and which slab each machine stands on."""

    slabs: list[Slab] = field(default_factory=list)
    slab_of: dict[str, int] = field(default_factory=dict)

    def machines_on(self, index: int) -> list[str]:
        return sorted(m for m, s in self.slab_of.items() if s == index)

    def groups(self) -> list[list[str]]:
        """Machine sets by slab, largest first. Machines on no slab are omitted --
        they are ground-built and this signal has nothing to say about them."""
        by_slab: dict[int, list[str]] = defaultdict(list)
        for machine, index in self.slab_of.items():
            by_slab[index].append(machine)
        return sorted((sorted(v) for v in by_slab.values()), key=len, reverse=True)

    def summary(self) -> dict:
        return {
            "slabs": len(self.slabs),
            "tiles": sum(s.tiles for s in self.slabs),
            "machines_on_slabs": len(self.slab_of),
        }


class _Union:
    def __init__(self, n: int) -> None:
        self.par = list(range(n))

    def find(self, a: int) -> int:
        par = self.par
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.par[rb] = ra


def _grid(points: list[tuple[float, float, float]]) -> dict[tuple[int, int], list[int]]:
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        cells[(int(p[0] // _CELL), int(p[1] // _CELL))].append(i)
    return cells


def _neighbourhood(cells: dict, cx: int, cy: int) -> list[int]:
    out: list[int] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out += cells.get((cx + dx, cy + dy), ())
    return out


def build_structures(
    projection: dict,
    link_xy: float = LINK_XY,
    link_z: float = LINK_Z,
) -> Structures:
    """Group foundations into slabs and assign each machine to the one beneath it."""
    payload = projection.get("structures") or {}
    classes: list[str] = payload.get("classes", [])
    instances: list[list[int]] = payload.get("instances", [])
    if not instances:
        return Structures()

    tiles: list[tuple[float, float, float]] = []
    walkways: list[tuple[float, float, float]] = []
    for row in instances:
        if len(row) < 4:
            continue
        cls = classes[row[0]] if 0 <= row[0] < len(classes) else ""
        point = (float(row[1]), float(row[2]), float(row[3]))
        if any(k in cls for k in _FOUNDATION):
            tiles.append(point)
        elif any(k in cls for k in _BRIDGE):
            walkways.append(point)

    if not tiles:
        return Structures()

    # Walkways join the union as nodes of their own rather than as a post-pass, so a
    # CHAIN of catwalks bridges a gap no single 4 m piece could span.
    nodes = tiles + walkways
    cells = _grid(nodes)
    union = _Union(len(nodes))
    for (cx, cy), members in cells.items():
        near = _neighbourhood(cells, cx, cy)
        for i in members:
            a = nodes[i]
            for j in near:
                if j <= i:
                    continue
                b = nodes[j]
                if math.dist(a[:2], b[:2]) > link_xy:
                    continue
                # Same level (touching faces) or one directly above the other.
                if abs(a[2] - b[2]) <= link_z:
                    union.join(i, j)

    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tiles)):
        grouped[union.find(i)].append(i)

    slabs: list[Slab] = []
    tile_slab: dict[int, int] = {}
    for members in sorted(grouped.values(), key=len, reverse=True):
        xs = [tiles[i][0] for i in members]
        ys = [tiles[i][1] for i in members]
        zs = [tiles[i][2] for i in members]
        index = len(slabs)
        slabs.append(
            Slab(
                index=index,
                tiles=len(members),
                centre=(sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)),
                extent=(max(xs) - min(xs), max(ys) - min(ys)),
                z_span=(min(zs), max(zs)),
            )
        )
        for i in members:
            tile_slab[i] = index

    tile_cells = _grid(tiles)
    slab_of: dict[str, int] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            pos = record.get("pos")
            if not pos:
                continue
            name = record["instance"].rsplit(".", 1)[-1]
            cx, cy = int(pos[0] // _CELL), int(pos[1] // _CELL)
            best, best_d = None, STAND_ON
            for i in _neighbourhood(tile_cells, cx, cy):
                t = tiles[i]
                # The tile must be UNDER the machine: a machine on an upper floor
                # would otherwise claim the ground-level slab it happens to sit above.
                if not (-STAND_ON <= pos[2] - t[2] <= 1200.0):
                    continue
                d = math.dist(t[:2], pos[:2])
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                slab_of[name] = tile_slab[best]

    return Structures(slabs=slabs, slab_of=slab_of)
