"""Advisory region names -- layer 2 of the spatial design.

Layer 1 (``geo``: grid cells, cones, radii, clustering) is exact, derived from the
coordinate frame, and is what every calculation uses. This module only attaches
human-readable names to coordinates. Names are approximate by construction and every
lookup carries a confidence, so a caller can tell "definitely Northern Forest" from
"somewhere near the Northern Forest boundary".

**A region name must never feed a computation.** It is for labelling output and for
letting a person say "the oil in the Northern Forest" instead of a bounding box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache

from .. import config
from . import geo

__all__ = ["Label", "RegionMap", "load_regions"]

VOID = "."

#: Confidence codes, ordered worst to best.
CONFIDENCE = {
    ".": "void",
    "u": "sparse",
    "b": "boundary",
    "l": "interior",
}


@dataclass(frozen=True)
class Label:
    """The result of a coordinate -> name lookup."""

    name: str | None
    confidence: str  # void | sparse | boundary | interior | verified
    accuracy_m: int

    @property
    def certain(self) -> bool:
        return self.confidence in ("interior", "verified")

    def describe(self) -> str:
        if self.name is None:
            return "off-map or ocean"
        if self.confidence == "verified":
            return self.name
        if self.confidence == "interior":
            return self.name
        return f"{self.name} (~{self.accuracy_m}m accuracy, {self.confidence})"


@dataclass
class RegionMap:
    grid: list[str]
    confidence: list[str]
    legend: dict[str, str]
    regions: dict[str, dict]
    overrides: dict[str, str]
    meta: dict
    x0: float
    y0: float
    cell: float
    nx: int
    ny: int

    # ---- coordinate -> name -------------------------------------------

    def _cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        i = int((x - self.x0) // self.cell)
        j = int((y - self.y0) // self.cell)
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return i, j
        return None

    def label_for(self, x: float, y: float) -> Label:
        """Name the region containing a point.

        Returns ``name=None`` for ocean and off-map coordinates rather than
        fabricating the nearest land label.
        """
        acc = int(self.meta.get("accuracy_m", 256))
        at = self._cell_of(x, y)
        if at is None:
            return Label(None, "void", acc)
        i, j = at
        ch = self.grid[j][i]
        if ch == VOID:
            return Label(None, "void", acc)
        conf = CONFIDENCE.get(self.confidence[j][i], "boundary")
        return Label(self.legend.get(ch), conf, acc)

    def label_for_node(self, node: dict) -> Label:
        """Name a resource node, preferring the hand-verified override table."""
        int(self.meta.get("accuracy_m", 256))
        inst = str(node.get("instance", ""))
        short = inst.rsplit(".", 1)[-1]
        for key in (inst, short):
            if key in self.overrides:
                return Label(self.overrides[key], "verified", 0)
        return self.label_for(node["x"], node["y"])

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
        """Nodes whose label matches ``name`` (overrides respected)."""
        resolved = self.resolve(name)
        if resolved is None:
            return []
        out = []
        for n in nodes:
            label = self.label_for_node(n)
            if label.name == resolved:
                out.append(n)
        return out

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


@lru_cache(maxsize=1)
def load_regions() -> RegionMap:
    path = config.data_dir() / "region_names.json"
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing -- run: uv run python tools/gen_region_names.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    gm = payload["grid_meta"]
    return RegionMap(
        grid=payload["region_grid"],
        confidence=payload["confidence_grid"],
        legend=payload["legend"],
        regions=payload["regions"],
        overrides=payload.get("node_region_overrides", {}),
        meta=payload.get("_meta", {}),
        x0=gm["x0"],
        y0=gm["y0"],
        cell=gm["cell"],
        nx=gm["nx"],
        ny=gm["ny"],
    )
