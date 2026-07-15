"""``/api/storage``: every container and fluid buffer, and what is inside each one.

The endpoint that answers "where did I put the steel" rather than "how much steel have I
got" -- the projection has been able to answer the second since schema 11 and could never
answer the first. Its own file because the two record shapes behind the one row shape, and
the truncation rule that keeps a popup readable, are a concern nothing else on this surface
shares.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....domain.world.state import WorldState
from ..serial import _fail, _state, _xyz, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")


# -------------------------------------------------------------------- storage


#: How many kinds of thing a storage row spells out before it starts counting instead.
#:
#: A container has 24 or 48 slots and can in principle hold one kind of thing per slot. In
#: practice the reference world's containers hold one or two kinds each and only a handful hold
#: more -- the busiest, a catch-all box, holds 12 -- so this truncation fires rarely and exists
#: for exactly that box, which would otherwise render a popup taller than the window. The row
#: says how many it left out rather than trailing off: "and 6 more" is information, and a list
#: that simply stops is a bug the reader has to notice.
STORAGE_ITEMS_SHOWN = 6


def _storage_row(st: WorldState, row: dict) -> dict:
    """One container or fluid buffer: where it stands, how big it is, and what is in it.

    Two record shapes behind one row shape, told apart by ``kind``. A solid container carries
    ``items`` and ``slots``; a fluid buffer carries ``fluid``, ``stored_m3`` and ``fill``. The
    other side's fields are absent rather than null, because a container does not have an empty
    fluid level -- it has no fluid level -- and a row that said ``"stored_m3": null`` on 146 of
    151 rows would be inviting a client to print it.

    ``items`` is resolved against the docs dump like every other class here, so a popup never
    shows a reader a ``Desc_…_C``, and it is TRUNCATED with a count of the remainder: the whole
    list is in ``item_kinds`` and ``total`` either way, so a client that wants everything has
    the numbers to say so honestly.

    ``fill`` is the one figure here that needs the dump rather than the save. A buffer's
    contents are a bare float of cubic metres, and 1,730.6 is not a reading until it is put
    against the 2,400 the class holds -- so the fraction is computed where the capacity lives,
    and is ``null`` for a class the dump has no capacity for, on the same terms as every
    footprint on this page.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    out: dict[str, Any] = {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "name": st.game.building_name(cls),
        **_xyz(row.get("pos")),
        "yaw": _yaw(row.get("yaw")),
        # Null for the four classes the dump carries no clearance for -- the HUB's built-in
        # container, the Blueprint Designer's, the Dimensional Depot uploader -- exactly as the
        # machines endpoint sends null for a biomass burner, and for the same reason: a size
        # invented here would arrive indistinguishable from a measured one.
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
    }
    if "stored_m3" in row:
        fluid = row.get("fluid")
        capacity = getattr(building, "storage_capacity_m3", 0.0) if building else 0.0
        stored = row.get("stored_m3")
        out.update(
            {
                "kind": "fluid",
                "fluid": fluid,
                "fluid_name": st.game.item_name(fluid) if fluid else None,
                "stored_m3": stored,
                "capacity_m3": round(capacity, 1) if capacity else None,
                "fill": (
                    round(float(stored) / capacity, 4)
                    if capacity and isinstance(stored, (int, float))
                    else None
                ),
            }
        )
        return out

    raw = [e for e in row.get("items") or () if isinstance(e, (list, tuple)) and len(e) >= 2]
    items = [
        {
            "cls": str(e[0]),
            "name": st.game.item_name(str(e[0])),
            "count": e[1],
        }
        for e in raw[:STORAGE_ITEMS_SHOWN]
    ]
    out.update(
        {
            "kind": "solid",
            "items": items,
            # What was left off the list above, so a client can say "and 3 more" rather than
            # showing six of nine and implying nine is six.
            "more": max(0, len(raw) - len(items)),
            "item_kinds": len(raw),
            "total": sum(e[1] for e in raw if isinstance(e[1], (int, float))),
            "slots": row.get("slots"),
        }
    )
    return out


@router.get("/storage")
def storage(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every storage container and fluid buffer, and what is inside each one.

    New in schema 15, and the gap it fills is a specific one. The projection has been able to
    say what the player OWNS since schema 11 -- ``inventories["storage"]``, every stack in
    every container summed -- and has never been able to say where any of it is. That answers
    "have I got enough steel to build that" and is exactly the wrong shape for "where did I
    put the steel", which is the question a base with 105 containers spread over 7 km actually
    raises. Nothing else carried a container at all: they run no recipe so they were never
    machines, draw no power so they are not in the power graph, and are ordinary actors so they
    are not lightweight buildables either.

    **Which classes, and the one that had to be excluded.** 151 rows on the reference world:
    61 Storage Containers and 44 Industrial ones, 6 Personal Storage Boxes, 33 Dimensional
    Depot uploaders, the HUB's built-in container and the Blueprint Designer's, and 5 fluid
    buffers. NOT the splitters and mergers -- every one of the world's 848 owns a component
    literally named ``StorageInventory``, holding the one to three items physically inside the
    junction, and a payload built by matching that name would report 848 phantom containers,
    draw them a second time over the belt layer that already has them, and count items in
    transit as stock. Machine input and output buffers are excluded on the same principle and
    are not lost: they are on their own machine's row under ``buffers``, where they mean "this
    smelter is starved" rather than "the player owns this".

    **Two record shapes, told apart by ``kind``.** A solid container reports ``items`` (biggest
    first, resolved to display names, truncated with a ``more`` count), ``slots`` and
    ``total``; a fluid buffer reports ``fluid``, ``stored_m3``, ``capacity_m3`` and ``fill``.

    **The fluid's identity comes off the plumbing, not off the buffer.** A buffer stores a bare
    ``mFluidBox`` float and never names its contents, so the name is taken from the
    ``FGPipeNetwork`` that claims it -- the same join, and the same source, ``/api/pipes`` uses
    for a pipe's ``fluid``. ``null`` for a buffer no network claims, which happens on none of
    the five here.

    Small: 151 rows against the 3,085 of ``/api/belts``, and the whole key is 26 KB of
    projection. Sent in one payload, ungrouped, the posture every placement endpoint here
    takes.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    rows = [
        _storage_row(st, row) for row in st.projection.get("storage") or () if isinstance(row, dict)
    ]
    solids = [r for r in rows if r["kind"] == "solid"]
    return {
        "storage": rows,
        "count": len(rows),
        # What the player can actually see at a glance, and the two numbers a header wants:
        # how many boxes hold anything, and how many things are in them altogether.
        "filled": sum(1 for r in solids if r["total"]),
        "items_total": sum(r["total"] for r in solids),
    }
