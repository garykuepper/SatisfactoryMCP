"""``/api/regions``: the biome raster the base map is drawn from.

One endpoint in a file of its own, deliberately. The raster is re-derived from the game's
own ``FGMapAreaTexture`` whenever the map changes, and every re-derivation moves numbers
that this endpoint's label-anchor rule is written against -- so the churn is concentrated
here rather than spread through a module that also serves tiles.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ....domain.spatial import regions as spatial_regions
from ..serial import _fail, _m

__all__ = ["router"]

router = APIRouter(prefix="/api")


# -------------------------------------------------------------------- regions


@router.get("/regions")
def regions() -> Any:
    """The biome raster: a 30x30 character grid, its legend, and each region's extent.

    No ``?save``/``?world``: this is the world's own geography, identical for every save,
    which is why it is cacheable and fetched once per page load.

    The raster comes through ``domain.spatial.regions``, which reads
    ``data/region_names.json`` -- a majority downsample of the game's own ``FGMapAreaTexture``
    at 1.83 m. Every per-region bounding box in it is derived from this same 30x30 grid, so
    every cell provably lies inside the box of the region it names, which is what the
    drawing client is checked against.

    The 30x30 grid is what is SERVED and it is not the finest thing in that file: a 64 m
    grid rides along beside it and is what ``label_for`` answers from. This payload keeps
    the coarse one -- 768 rectangles rather than twelve thousand -- so a label anchor below
    is placed against ``rmap.grid`` rather than by asking ``label_for``, or the page would
    print a name on a cell it paints as somebody else's.

    The one thing a drawing client gets wrong is orientation, so it is stated here rather
    than left to be inferred. Game +X is east and game **+Y is south**; ``y0_m`` is the
    smallest y, so **grid row 0 is the northern edge** and column 0 the western one. Cell
    ``(i, j)`` spans x ``[x0_m + i*cell_m, x0_m + (i+1)*cell_m]`` and y ``[y0_m + j*cell_m,
    ...]``, and a page that plots ``[-y, x]`` has to flip those y bounds to draw it. The
    ``.`` cells are ocean or off-map and carry no name: left unpainted they are the
    coastline.
    """
    try:
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    letters = {name: ch for ch, name in rmap.legend.items()}

    def _label_anchor(name: str, centroid: tuple[float, float]) -> list[float | None]:
        """Where to print a region's name: a cell that provably belongs to it.

        A centroid is a mean, and the mean of a concave region can land on a
        neighbour's ground -- Titan Forest's sits in the Swamp, so a label printed
        there flatly contradicts the same page's own right-click inspector. If the
        centroid's cell already carries the region's letter it is used as-is;
        otherwise the anchor moves to the centre of the nearest cell that does.

        Against the PUBLISHED grid, not against ``label_for``. The two answer at different
        resolutions -- ``label_for`` reads the region table's finer 64 m grid, and this
        endpoint serves the 256 m one -- so asking the finer question here would place a
        label on a cell this payload paints as somebody else's, which is the exact
        contradiction the anchor exists to prevent, moved one level down.
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
