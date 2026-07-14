"""Advisory region names -- layer 2 of the spatial design.

Layer 1 (``geo``: grid cells, cones, radii, clustering) is exact, derived from the
coordinate frame, and is what every calculation uses. This module only attaches
human-readable names to coordinates. Every lookup carries a confidence, so a caller can tell
"definitely Northern Forest" from "somewhere near the Northern Forest boundary".

**A region name must never feed a computation.** It is for labelling output and for
letting a person say "the oil in the Northern Forest" instead of a bounding box.

What the confidence words mean now
----------------------------------
They changed with the table under them, and the change is worth stating because the words
did not. ``data/region_names.json`` used to be a raster of a hand trace of a wiki image, so
``boundary`` meant *the trace might be off here* -- a statement about somebody's eyesight.
The table is derived from the game's own ``FGMapAreaTexture`` now, whose boundaries are exact
polygon edges at 1.83 m, so a confidence is a statement about THIS TABLE's resolution and
nothing else:

``interior``
    the whole 64 m cell is one region. The name is right unless the map changed.
``boundary``
    an exact region boundary runs through this 64 m cell, so a point inside it can be on
    either side. Off by at most one cell, and the source knows which -- this table does not.
``unnamed``
    the game names no region here. The label is ``No Man's Land``, which is the game's own
    name for the outer coast and the ocean, and it is a real answer rather than a shrug.
``void``
    no name at all: off the grid, or the game names nothing here and no known static object
    stands within a kilometre. Ocean and off-map.

``verified`` is gone. It meant "a human checked this node against the wiki's picture", which
was the best available answer while the geometry was a trace and is worth nothing beside the
geometry itself.

Two grids, and which one answers
--------------------------------
The file publishes a 256 m grid -- what ``/api/regions`` serves and what the map paints -- and
carries a 64 m one for lookups. This module reads the 64 m grid, because a majority downsample
at 256 m mislabels 14.1% of known world objects against 5.3% at 64 m. A table with no fine
grid still loads and answers at its own resolution, which is what makes the two shapes one
contract rather than two.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ... import config
from . import geo

__all__ = ["Label", "RegionMap", "load_regions"]

VOID = "."

#: Confidence codes, ordered worst to best. See the module docstring for what each one is a
#: statement ABOUT -- they are facts about this table's resolution, not about anyone's care.
CONFIDENCE = {
    ".": "void",
    "u": "unnamed",
    "b": "boundary",
    "l": "interior",
}


@dataclass(frozen=True)
class Label:
    """The result of a coordinate -> name lookup."""

    name: str | None
    confidence: str  # void | unnamed | boundary | interior
    accuracy_m: int

    @property
    def certain(self) -> bool:
        return self.confidence == "interior"

    def describe(self) -> str:
        if self.name is None:
            return "off-map or ocean"
        if self.confidence == "interior":
            return self.name
        return f"{self.name} (~{self.accuracy_m}m accuracy, {self.confidence})"


@dataclass
class RegionMap:
    grid: list[str]
    confidence: list[str]
    legend: dict[str, str]
    regions: dict[str, dict]
    meta: dict
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int
    #: The finer pair, when the table carries one. Same origin, same legend, smaller cell.
    fine: list[str] | None = None
    fine_confidence: list[str] | None = None
    fine_cell: float = 0.0
    fine_nx: int = 0
    fine_ny: int = 0

    # ---- coordinate -> name -------------------------------------------

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        """The PUBLISHED grid's cell at a point, or ``None`` off it.

        Public because ``/api/regions`` needs it: that endpoint serves this grid and has to
        place its label anchors on it, and asking ``label_for`` instead would answer at the
        finer grid's resolution and put a label on a cell the payload paints as another
        region's.
        """
        i = int((x - self.x0) // self.cell)
        j = int((y - self.y0) // self.cell)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return i, j
        return None

    def _lookup(self, x: float, y: float) -> tuple[str, str, int] | None:
        """The raw ``(letter, confidence letter, accuracy)`` at a point, or ``None``.

        Prefers the fine pair and falls back to the published one, which is the whole of the
        two-grid arrangement: one origin, one legend, and a caller that never has to know
        which answered. Off either grid is off the map, so it is ``None`` either way.
        """
        if self.fine and self.fine_confidence:
            i = int((x - self.x0) // self.fine_cell)
            j = int((y - self.y0) // self.fine_cell)
            if 0 <= i < self.fine_nx and 0 <= j < self.fine_ny:
                return self.fine[j][i], self.fine_confidence[j][i], int(self.fine_cell / 100)
            return None
        at = self.cell_of(x, y)
        if at is None:
            return None
        i, j = at
        return self.grid[j][i], self.confidence[j][i], int(self.cell / 100)

    def label_for(self, x: float, y: float) -> Label:
        """Name the region containing a point.

        Returns ``name=None`` for ocean and off-map coordinates rather than
        fabricating the nearest land label.
        """
        found = self._lookup(x, y)
        if found is None:
            return Label(None, "void", self.accuracy_m)
        letter, code, accuracy = found
        if letter == VOID:
            return Label(None, "void", accuracy)
        return Label(self.legend.get(letter), CONFIDENCE.get(code, "boundary"), accuracy)

    def label_for_node(self, node: dict) -> Label:
        """Name a resource node, which is to say: name where it stands.

        Kept as its own method because that is what every caller asks for and because it used
        to mean something else. There was an override table -- 48 oil nodes whose region had
        been read off a wiki image by eye and was trusted over the raster, reported as
        ``verified``. The raster is the game's own geometry now, so a hand correction has
        nothing to correct; and keying one by instance name would be the wrong repair anyway,
        since a map update renames instances. This is a position lookup and says so.
        """
        return self.label_for(node["x"], node["y"])

    @property
    def accuracy_m(self) -> int:
        """How far a name is trustworthy in metres: the finer grid's cell, if there is one."""
        default = int((self.fine_cell or self.cell) / 100)
        return int(self.meta.get("accuracy_m", default))

    # ---- name -> nodes -------------------------------------------------

    def names(self) -> list[str]:
        return sorted(self.regions)

    def resolve(self, name: str) -> str | None:
        """Resolve a region name case-insensitively, allowing unique prefixes."""
        q = name.strip().casefold()
        for known in self.regions:
            if known.casefold() == q:
                return known
        hits = [k for k in self.regions if k.casefold().startswith(q)]
        if len(hits) == 1:
            return hits[0]
        hits = [k for k in self.regions if q in k.casefold()]
        return hits[0] if len(hits) == 1 else None

    def filter_nodes(self, nodes: list[dict], name: str) -> list[dict]:
        """Nodes whose label matches ``name``."""
        resolved = self.resolve(name)
        if resolved is None:
            return []
        return [n for n in nodes if self.label_for_node(n).name == resolved]

    def summary(self, name: str) -> dict | None:
        resolved = self.resolve(name)
        if resolved is None:
            return None
        entry = dict(self.regions[resolved])
        cx, cy = entry["centroid"]
        entry["name"] = resolved
        entry["grid"] = geo.grid_cell(cx, cy)
        entry["direction"] = geo.direction_of(cx, cy)
        return entry


#: Keyed by the file and its mtime, so a regenerated raster is picked up without a restart.
#: See ``spatial.nodes._TABLE`` for the argument; this artifact moves for the same reason,
#: a map update, and moves in the same generation run.
_MAP: dict[tuple[str, int], RegionMap] = {}


def load_regions() -> RegionMap:
    path = config.data_dir() / "region_names.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_region_names.py")
    key = (str(path), path.stat().st_mtime_ns)
    hit = _MAP.get(key)
    if hit is not None:
        return hit
    payload = json.loads(path.read_text(encoding="utf-8"))
    gm = payload["grid_meta"]
    region_map = RegionMap(
        grid=payload["region_grid"],
        confidence=payload["confidence_grid"],
        legend=payload["legend"],
        regions=payload["regions"],
        meta=payload.get("_meta", {}),
        x0=gm["x0"],
        y0=gm["y0"],
        cell=gm["cell"],
        nx=gm["nx"],
        ny=gm["ny"],
        fine=payload.get("fine_grid"),
        fine_confidence=payload.get("fine_confidence"),
        fine_cell=gm.get("fine_cell", 0.0),
        fine_nx=gm.get("fine_nx", 0),
        fine_ny=gm.get("fine_ny", 0),
    )
    _MAP.clear()
    _MAP[key] = region_map
    return region_map
