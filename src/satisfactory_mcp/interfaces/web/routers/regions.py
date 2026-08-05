"""``/api/regions``: the biome raster the base map is drawn from.

The raster is re-derived from the game's own ``FGMapAreaTexture`` whenever the map changes,
and every re-derivation moves the numbers the label anchor below is written against.

WARNING: the function name is the operation_id -- renaming it churns the committed schema.

Wire rules: docs/web-wire.md.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ....domain.spatial import regions as spatial_regions
from ..serial import _fail, _m

__all__ = ["router"]

router = APIRouter(prefix="/api")


# -------------------------------------------------------------------- regions


class RegionExtent(TypedDict):
    """Where one region is: its mean, its box, and where to print its name.

    Three fixed-length lists, spelled as tuples because that is how a schema says "exactly
    two" -- JSON has no pair, and ``prefixItems`` survives typegen as a ``[number, number]``
    the page can index without a length guard.

    ``label_m`` is never null and is often different from ``centroid_m``, which is the whole
    reason it exists; see ``_label_anchor``.
    """

    centroid_m: tuple[float, float]
    bbox_m: tuple[float, float, float, float]
    label_m: tuple[float, float]


class RegionsResponse(TypedDict):
    """What ``/api/regions`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    **Declared but not enforced.** The handler returns a ``JSONResponse`` of its own, to set
    a ``Cache-Control``, and FastAPI skips response_model validation for a handler that
    returns a ``Response`` -- so nothing here filters the wire or rejects a bad row. Read it
    as the description ``/openapi.json`` publishes, not as a guard.
    """

    grid: list[str]
    legend: dict[str, str]
    cell_m: float
    x0_m: float
    y0_m: float
    regions: dict[str, RegionExtent]


@router.get("/regions", response_model=RegionsResponse)
def regions() -> Any:
    """The biome raster: a 30x30 character grid, its legend, and each region's extent.

    No ``?save``/``?world``: this is the world's own geography, identical for every save,
    which is why it is cacheable and fetched once per page load. The grid served is the
    coarse one, 768 rectangles rather than the twelve thousand of the 64 m grid ``label_for``
    answers from.

    **Orientation is what a drawing client gets wrong.** Game +X is east and game **+Y is
    south**; ``y0_m`` is the smallest y, so **grid row 0 is the northern edge** and column 0
    the western one. Cell ``(i, j)`` spans x ``[x0_m + i*cell_m, x0_m + (i+1)*cell_m]`` and y
    ``[y0_m + j*cell_m, ...]``, so a page that plots ``[-y, x]`` has to flip those y bounds.
    The ``.`` cells are ocean or off-map and carry no name.
    """
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    letters = {name: ch for ch, name in rmap.legend.items()}

    def _label_anchor(name: str, centroid: tuple[float, float]) -> list[float | None]:
        """Where to print a region's name: a cell that provably belongs to it.

        A centroid is a mean, and the mean of a concave region can land on a neighbour's
        ground -- Titan Forest's sits in the Swamp. So the centroid is used only when its
        own cell carries the region's letter, and otherwise the anchor moves to the centre
        of the nearest cell that does.

        Measured against the PUBLISHED grid, never against ``label_for``: that reads the
        finer 64 m grid, and answering at that resolution would put a label on a cell this
        payload paints as somebody else's.
        """
        cx, cy = centroid
        ch = letters.get(name)
        at = rmap.cell_of(cx, cy)
        if at is not None and rmap.grid[at[1]][at[0]] == ch:
            return [_m(cx), _m(cy)]
        best: tuple[float, float, float] | None = None
        for j, row in enumerate(rmap.grid):
            for i, cell_ch in enumerate(row):
                if cell_ch != ch:
                    continue
                px = rmap.x0 + (i + 0.5) * rmap.cell
                py = rmap.y0 + (j + 0.5) * rmap.cell
                d = (px - cx) ** 2 + (py - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, px, py)
        if best is None:
            return [_m(cx), _m(cy)]
        return [_m(best[1]), _m(best[2])]

    payload = {
        "grid": list(rmap.grid),
        "legend": dict(rmap.legend),
        "cell_m": _m(rmap.cell),
        "x0_m": _m(rmap.x0),
        "y0_m": _m(rmap.y0),
        "regions": {
            name: {
                "centroid_m": [_m(entry["centroid"][0]), _m(entry["centroid"][1])],
                "bbox_m": [_m(v) for v in entry["bbox"]],
                "label_m": _label_anchor(name, entry["centroid"]),
            }
            for name, entry in rmap.regions.items()
        },
    }
    return JSONResponse(payload, headers={"Cache-Control": "max-age=3600"})
