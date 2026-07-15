"""``/api/power``: every pole and tower, and the span of every wire between them.

The geometry half of a network whose connectivity has been in the projection since schema
11. Its own file because the join it makes is its own -- ``wires[i]`` is the span of
``graph["power"][i]``, positional, and this is the one place the two lists are put back
together -- and because naming a wire's ends needs a lookup built from five record lists
that nothing else on this surface wants.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....core.saveio import rows as saverows
from ....domain.spatial import geo
from ....domain.world.state import WorldState
from ..serial import _fail, _m, _state, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- power


def _power_names(st: WorldState) -> dict[str, str]:
    """Actor short name -> display name, for every actor this projection can name.

    What lets a wire say what is at each end of it. The projection carries a short name per
    actor in ``graph["actors"]`` -- ``Build_SmelterMk1_C_2147380350`` -- and that is an
    IDENTITY, not a name: it is the class with a serial glued on, so printing it is printing
    an engine id at a reader. The class is on the record lists, and this joins the two.

    Built once per request rather than per wire: 2,594 endpoints over 1,297 wires, and a
    per-endpoint scan of six lists would be six thousand list walks for one payload.

    A pole is in here through its ``actor_index`` column, which is the whole reason schema 17
    gave it one. Everything else comes off the five record lists, which is not all of the
    world -- a wire ending on a hypertube entrance, a drop pod or a resource sink lands on an
    actor no list carries, 40 of the 2,594 on the reference save -- and those ends come out
    ``null``. That is the same refusal the rest of this module makes: an actor this projection
    does not name is not an actor with a name to be guessed at.
    """
    actors = st.projection.get("graph", {}).get("actors") or []
    out: dict[str, str] = {}
    for key in ("machines", "extractors", "generators", "attachments", "storage"):
        for row in st.projection.get(key) or ():
            if not isinstance(row, dict):
                continue
            name = st.game.building_name(row.get("cls"))
            if name:
                out[str(row.get("instance", "")).rsplit(".", 1)[-1]] = name
    for pole in saverows.iter_power_poles(st.projection):
        if 0 <= pole.actor_index < len(actors):
            name = st.game.building_name(pole.cls)
            if name:
                out[str(actors[pole.actor_index])] = name
    return out


@router.get("/power")
def power(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every power pole and tower, and the span of every wire between them.

    New in schema 17, and the half of the power network that was never drawable. The
    CONNECTIVITY has been in the projection since schema 11 -- ``graph["power"]``, 1,297
    interned actor pairs on the reference world, which is what ``/api/summary``'s draw and
    generation figures are computed over -- and it says who is joined to whom and nothing
    about where. This endpoint is the geometry beside it, joined by position: the projection
    writes both lists in one pass so that ``wires[i]`` is the span of ``graph["power"][i]``,
    and this is the one place the two are put back together.

    **The endpoints are the game's own, not a line between two buildings.** A wire ends at a
    CONNECTOR, and a connector sits at a fixed offset on its owner -- 7 m above a Mk1 pole,
    2.1 m forward and 4.7 m to one side of a constructor's centre -- so origin-to-origin would
    draw every wire through the middle of the machine it feeds. ``Build_PowerLine_C`` stores
    both endpoints in world coordinates and the projection reads them; verified against the
    game's own ``mCachedLength`` to a median of 0.000031 cm over 1,162 lines, and against two
    pole origins with no free parameter at all. See ``extract._wire_span``.

    **``span_m`` is the CHORD and says so.** A wire hangs as a catenary and this is the
    straight line between its ends, which is shorter -- ``mCachedLength`` is the same chord, so
    the sag is not a number the save carries either. Three-dimensional, because a tower span
    climbs 24 m and that is real cable. It is what a top-down map draws and what a "how far is
    that run" question wants; it is not the length of hanging wire.

    **``from`` and ``to`` are named where the projection can name them.** 40 of the reference
    world's 2,594 endpoints land on an actor no record list carries -- a hypertube entrance, a
    drop pod, the AWESOME Sink -- and those come out ``null`` rather than as the engine id the
    graph holds. The pair is in the edge's own order: the save's own endpoint order agrees with
    it only about half the time, so the projection measures which end is which.

    **A pole carries its connection count**, off the edge list rather than out of a second copy
    of it. 701 poles on this world -- 426 Mk1, 105 Mk2, 7 Mk3, 26 wall outlets and 137 Power
    Tower platforms -- and 2 of them are strung to nothing at all, which is a real answer and
    not a torn row.

    Small beside its neighbours: 1,297 spans and 701 poles against the 3,085 pieces of
    ``/api/belts``, and 78 KB of projection against that layer's 562 KB.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    projection = st.projection
    actors = projection.get("graph", {}).get("actors") or []
    edges = projection.get("graph", {}).get("power") or []
    named = _power_names(st)

    # Degree per actor, counted once over the edge list. A pole's own row cannot carry it --
    # ``power["poles"]`` is geometry and holds no connectivity on purpose -- and counting it
    # per pole would be 701 scans of a 1,297-row list.
    degree: dict[int, int] = {}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) >= 2:
            for end in edge[:2]:
                if isinstance(end, int):
                    degree[end] = degree.get(end, 0) + 1

    poles = [
        {
            "cls": pole.cls,
            "name": st.game.building_name(pole.cls),
            "x_m": _m(pole.x),
            "y_m": _m(pole.y),
            "z_m": _m(pole.z),
            "yaw": _yaw(pole.yaw),
            # 0 for a pole no wire names, which is a count and not a missing value: the two
            # on this world are tower platforms somebody built and never strung.
            "connections": degree.get(pole.actor_index, 0) if pole.actor_index >= 0 else 0,
        }
        for pole in saverows.iter_power_poles(projection)
    ]

    wires = []
    for wire in saverows.iter_wires(projection):
        edge = edges[wire.index] if wire.index < len(edges) else None
        ends = [
            named.get(str(actors[end])) if isinstance(end, int) and 0 <= end < len(actors) else None
            for end in (
                edge[:2] if isinstance(edge, (list, tuple)) and len(edge) >= 2 else (None, None)
            )
        ]
        a = [_m(v) for v in wire.a]
        b = [_m(v) for v in wire.b]
        wires.append(
            {
                "a_m": a,
                "b_m": b,
                "from": ends[0],
                "to": ends[1],
                # Three-dimensional, through ``geo`` like every other distance that leaves
                # this module: a wire's climb is real cable, exactly as a pipe's is real pipe,
                # and it is 24 m of it on a tower span. ``distance_m`` would drop it.
                "span_m": round(geo.distance_3d_m(wire.a, wire.b), 1),
            }
        )

    return {
        "poles": poles,
        "pole_count": len(poles),
        "wires": wires,
        "wire_count": len(wires),
        # What the layer control's header wants, and the one number that says whether the
        # geometry is there at all: a save older than schema 17's property answers every edge
        # with a null span, so this comes out 0 against a non-zero edge count.
        "edge_count": len(edges),
    }
