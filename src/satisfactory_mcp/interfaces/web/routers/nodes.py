"""The resource node table, joined to what this save has built on it.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ..serial import Region, _fail, _label_json, _state, _xyz

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- nodes


class NodeRow(TypedDict):
    """One resource node, joined to whatever this save has built on it.

    **Declaration order is wire order.** A ``response_model`` serialises in declaration
    order, so these are in the order the loop below emits and reordering one reorders the
    bytes -- and, through ``npm run typegen``, the committed ``api-schema.d.ts``. The same
    rule ``FloorsResponse`` is written under; routers/floors.py says it at length.

    **A response_model FILTERS**, so a field left out here is a field deleted from the wire.
    Every key the loop writes is below.

    ``x_m``/``y_m``/``z_m`` are NOT nullable, and that is a claim about this endpoint rather
    than about ``_xyz``. The triple comes from the static node table, whose generator writes
    three floats per node -- 608 of 608 on the committed table, no nulls in any of the three
    -- and the page's own ``xy()`` takes two plain numbers. ``_xyz`` returns ``float | None``
    because it also serves placements, whose transforms can fail to decode; a node has no
    transform to fail. A ``| null`` nobody can produce is as wrong as a missing one: it makes
    the drawing code carry an untestable branch.

    ``occupant_cls`` and ``occupant_name`` ARE nullable, and both for one reason: the
    occupancy join resolves only the extractors whose target is a node key, so an unoccupied
    node has neither. ``region`` is null for the handful of nodes the raster calls void.
    """

    id: str
    resource: str
    name: str
    kind: str
    purity: str
    x_m: float
    y_m: float
    z_m: float
    occupied: bool
    occupant_cls: str | None
    occupant_name: str | None
    region: Region | None


class NodesResponse(TypedDict):
    """What ``/api/nodes`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``resource`` echoes the query parameter and is ``null`` when none was given. ``occupied``
    is ``null`` rather than 0 whenever ``save_error`` is set, because "0 of them occupied"
    would be a claim nobody measured -- the two fields are one statement and are typed as
    one.
    """

    nodes: list[NodeRow]
    resource: str | None
    occupied: int | None
    save_error: str | None


@router.get("/nodes", response_model=NodesResponse)
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
    out: list[NodeRow] = []
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
