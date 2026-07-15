"""The resource node table, joined to what this save has built on it.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ..serial import _fail, _label_json, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- nodes


@router.get("/nodes")
def nodes(
    request: Request,
    resource: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """The resource node table, joined to what this save has built on it.

    The join is deliberately partial and says so: ``occupancy`` resolves only the
    extractors whose target is a node key, so ``occupied`` false means "no extractor
    known here", never "free". The map draws it as unknown-or-free and the popup
    carries the node id, which doubles as a ``node:`` selector for the MCP tools.

    The region name is joined here rather than in the browser because the raster lives on
    this side: sending 608 rows and then a 30x30 grid for the page to index into would put
    the orientation trap (row 0 is the NORTH edge) in two places. ``label_for_node``, which is a
    position lookup and nothing more: there used to be an override table of nodes someone
    had checked against a wiki image by eye, reported as ``verified``, and the region
    geometry is the game's own now so there is nothing for it to correct. ``null`` for a
    node the raster calls void, which is the honest answer for the handful that sit on
    islands off the grid.

    **A failed save is not a failed answer** -- the same rule ``/api/inspect`` already
    follows, because the two used to disagree: the node table is static and needs no
    ``.sav``, so a world whose save will not load still gets its geography. What it loses
    is the occupancy join, and ``save_error`` says so out loud (with ``occupied`` null at
    the top, since "0 of them occupied" would be a claim no one measured).
    """
    try:
        table = spatial_nodes.load_nodes()
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    save_error: str | None = None
    taken: dict = {}
    try:
        st = _state(request, save, world)
        taken = spatial_nodes.occupancy(st.projection)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    game = request.app.state.game()
    rows = table.by_resource(resource) if resource else table.nodes
    out = []
    for n in rows:
        held = taken.get(n["instance"])
        occupant = held["extractor"] if held else None
        out.append(
            {
                "id": n["instance"],
                "resource": n["resource"],
                "name": str(n["instance"]).rsplit(".", 1)[-1],
                "kind": n["kind"],
                "purity": n["purity"],
                **_xyz((n["x"], n["y"], n["z"])),
                "occupied": held is not None,
                "occupant_cls": occupant,
                "occupant_name": game.building_name(occupant),
                "region": _label_json(rmap.label_for_node(n)),
            }
        )
    return {
        "nodes": out,
        "resource": resource,
        "occupied": None if save_error else sum(1 for r in out if r["occupied"]),
        "save_error": save_error,
    }
