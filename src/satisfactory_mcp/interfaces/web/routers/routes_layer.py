"""``/api/belts`` and ``/api/pipes``: the two routed networks, as built.

One file for two endpoints, and the reason is ``_curve_m``: schema 15's spline tangents are
the belts' fourth column and the pipes' fifth, translated by exactly one helper that both
call and nothing else does. Splitting them would either duplicate that translation or put it
in a third module that neither owns -- and a bend read one way here and another way there is
the failure mode a shared helper exists to make impossible.

Named ``routes_layer`` rather than ``routes``: a module called ``routes`` under a package of
FastAPI routers reads as the framework's own word for the endpoint table, which it is not.

WARNING: the function names are the operation_ids -- rename one and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ....core.saveio import rows as saverows
from ....domain.world.state import WorldState
from ..serial import _fail, _m, _state, _xyz, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------- belts


#: The docs dump's own native class for a conveyor LIFT. This is how a lift is told apart
#: from a belt here, and it is deliberately not the ``Lift`` in ``Build_ConveyorLiftMk2_C``:
#: the distinction decides how the map draws a piece, and a substring match on an engine id
#: is not a classification. ``domain.world.carriers`` picks the fastest belt off the same
#: field for the same reason.
LIFT_NATIVE = "FGBuildableConveyorLift"


def _belt_class(st: WorldState, cls: str | None) -> dict[str, Any]:
    """What one belt class is, resolved once per class rather than once per piece."""
    building = st.game.buildings.get(cls) if cls else None
    return {
        "cls": cls,
        "name": st.game.building_name(cls),
        # From the dump's native class, not from the class id -- see LIFT_NATIVE. ``None``
        # for a class the dump has no entry for: "not a lift" would be a guess, and the
        # map draws a lift and a belt as different things.
        "lift": None if building is None else building.native == LIFT_NATIVE,
        # The tier's own throughput, 60 to 780, instead of a "Mk3" the page would have to
        # parse back out of a display name. ``null`` where the dump is silent, exactly like
        # the machine footprints next door.
        "items_per_min": (building.items_per_min or None) if building else None,
    }


def _curve_m(spans: Any, points: list) -> list | None:
    """A route's spline tangents as metres, or ``None`` where the route is straight.

    Schema 15's fourth belt column and fifth pipe column, translated into this module's units
    and no further. One entry per SPAN, in step with ``points_m``: ``null`` where the span is a
    straight line, and otherwise ``[leave, arrive]`` -- the tangent leaving the point behind
    the span and the tangent arriving at the point ahead of it, which is the pair a cubic
    Hermite between those two points needs and the pair the projection stores.

    **Metres like everything else here, and a tangent scales exactly as a position does**: it
    is a displacement in the same space, so the same divide-by-100 is the whole conversion.
    What it must NOT get is the y-flip a client applies to draw the map -- but that is the
    client's transform and is applied to these the same way it is applied to the points, which
    is what makes the pair usable without any further instruction.

    ``None`` for the whole field when the projection carries no column at all -- a straight
    run, or a projection older than schema 15. Both mean "draw the points as a polyline", which
    is what every client did before this existed, so an old sidecar degrades to the old picture
    rather than to an error.

    Read guarded, entry by entry, on the same terms as the points beside it: a span that will
    not decode becomes a straight one and costs a curve rather than the route.
    """
    if not isinstance(spans, (list, tuple)) or len(spans) != len(points) - 1:
        return None
    out: list = []
    for entry in spans:
        if not isinstance(entry, (list, tuple)) or len(entry) != 6:
            out.append(None)  # 0, the projection's flat-span marker, lands here too
            continue
        try:
            vals = [_m(float(c)) for c in entry]
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append([vals[:3], vals[3:]])
    return out if any(out) else None


def _attachment_row(st: WorldState, row: dict) -> dict:
    """One splitter or merger: where it stands, which way it faces, and what it is.

    Shorter than ``_record_row`` on purpose. A splitter has no recipe, no clock and nothing
    to pause, so the machine row's shape would be six null columns saying that six times;
    what a belt attachment IS, is a placement. ``w_m``/``l_m`` are asked for anyway, and are
    ``null`` for all four of these classes today, because the dump carries no clearance for
    them -- the same null the machines endpoint sends for a biomass burner, and for the same
    reason: a size invented here would be indistinguishable from a measured one.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        "name": st.game.building_name(cls),
        **_xyz(row.get("pos")),
        "yaw": _yaw(row.get("yaw")),
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
    }


@router.get("/belts")
def belts(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every conveyor belt and lift, as the polyline it was actually built along.

    New in schema 12, and the reason the map drew no belts at all until now: the splines
    were decoded by the parser and thrown away at the projection. They arrive interned the
    way the structures next door are -- ``{"classes": [...], "segments": [[chain_index,
    class_index, [[x, y, z], ...]], ...]}``, world centimetres -- and, like that endpoint,
    the legend is resolved here so the page does not have to carry it, and the row is
    decoded by ``core.saveio.rows`` so that a malformed segment costs that segment rather
    than the network.

    **Points are in travel order, input to output.** The save stores them output-first and
    the projection reverses them, so a client can draw direction along a run without
    knowing that. ``chain`` is the belt chain a piece belongs to -- 1,909 chains over 3,085
    pieces on the reference world -- so "the whole run" is a group-by rather than a
    geometry problem.

    **``curve_m`` is new in schema 15, and it is what makes a curved belt curved.** The points
    are the spline's control points and were never the whole spline: the chain trailer stores
    two tangents beside each one, the projection dropped them, and a bend therefore arrived as
    the chords between its corners -- out by up to 16.4 m of arc on a single piece, measured
    against the length the save states for it. One entry per span here, ``null`` where that
    span is straight and ``[leave, arrive]`` metres where it is not, so a client draws a cubic
    Hermite where there is one and the same two-point line as before where there is not.
    ``null`` for the whole field on the 2,119 pieces with no bend anywhere in them.

    **A lift is a belt whose top-down polyline is a single point.** All 302 lifts on the
    reference save have exactly zero horizontal extent (measured: median *and* maximum
    horizontal span 0.0 cm, median rise 4 m), so a map that draws them as lines draws
    nothing at all where they are. ``lift`` says which ones, and the client owes them a
    glyph instead.

    Sent one row per piece, ungrouped, the same posture ``/api/structures`` takes and for
    the same reason: the per-piece class is what a popup reads. Measured on the reference
    world -- 3,085 pieces, 8,292 points, 562 KB -- which is the same order as the floor
    plan beside it (8,347 pieces, 708 KB). The geometry is already only the bends: 2,237 of
    the 3,085 pieces are two-point straight lines, 2.7 points per piece overall.

    **``attachments`` rides along, and belongs here rather than with the machines.** A
    splitter or a merger is a piece of the belt network -- it runs no recipe, draws no power
    and is meaningless without the runs either side of it -- so it travels with the runs and
    is drawn by the layer that draws them. That is also what keeps it from being drawn twice:
    it is in no other payload, so a map with the machines layer on and the belts layer off
    shows no splitters at all, which is the honest picture of "these are belt parts".

    They carry no spline -- a splitter is a point with a facing, not a route -- so they are a
    row shape of their own: where it stands, which way it faces, and what it is. 848 of them
    on the reference world -- 481 splitters, 364 mergers, 3 smart splitters -- 170 KB against
    the 562 KB of runs they join.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    resolved: dict[int, dict[str, Any]] = {}
    rows = []
    for seg in saverows.iter_belt_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _belt_class(st, seg.cls)
        points = [[_m(x), _m(y), _m(z)] for x, y, z in seg.points]
        rows.append(
            {
                "chain": seg.chain,
                **resolved[seg.class_index],
                "points_m": points,
                "curve_m": _curve_m(seg.spans, points),
            }
        )
    attachments = [
        _attachment_row(st, row)
        for row in st.projection.get("attachments") or ()
        if isinstance(row, dict)
    ]
    return {
        "belts": rows,
        "count": len(rows),
        "chains": len({r["chain"] for r in rows}),
        "attachments": attachments,
        "attachment_count": len(attachments),
    }


# ---------------------------------------------------------------------- pipes


def _pipe_class(st: WorldState, cls: str | None) -> dict[str, Any]:
    """What one pipe class is, resolved once per class rather than once per piece."""
    building = st.game.buildings.get(cls) if cls else None
    return {
        "cls": cls,
        "name": st.game.building_name(cls),
        # The dump's own throughput for the tier -- 300 on Mk1, 600 on Mk2 -- rather than a
        # "Mk2" the page would have to parse back out of a display name, exactly as the belts
        # next door take `items_per_min`. ``null`` where the dump is silent.
        "flow_m3_min": (building.flow_m3_min or None) if building else None,
    }


@router.get("/pipes")
def pipes(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every fluid pipe, as the polyline it was actually built along, and what it carries.

    New in schema 13, and the belts' other half. The geometry was never hidden the way the
    belts' was -- a pipe's spline is an ordinary ``mSplineData`` PROPERTY on the pipeline
    actor, stored in the actor's own frame and translated back at the projection -- so this
    is the same shape one layer down: ``{"classes": [...], "networks": [...], "segments":
    [[network_index, class_index, [[x, y, z], ...]], ...]}`` in world centimetres, resolved
    here so the page carries no legend, the row decoded by ``core.saveio.rows`` so a
    malformed segment costs that segment.

    **Each pipe says which fluid it carries**, which is the thing a belt cannot say: the game
    keeps an ``FGPipeNetwork`` per connected plumbing system with the fluid on it and its
    members listed, so ``fluid`` is the world's own answer rather than an inference from what
    the pipe is plugged into. All 503 pipes on the reference world are claimed by one of its
    19 networks -- 215 crude oil, 198 water, 55 fuel, 31 heavy oil residue, 4 alumina
    solution. ``null`` for a pipe no network claims, which happens on none of them here but
    is what an empty or half-built network would give.

    **``direction`` is INFERRED, and ``basis`` says from what.** Nothing on a pipe records
    which way the fluid goes -- that much of the old refusal stands, and the points are still
    in the order the file stores them. But the plumbing AROUND it records a great deal: the
    save serialises every fluid coupling, and names a machine's port ``PipeInputFactory`` or
    ``PipeOutputFactory``. ``domain/world/flow.py`` reads that graph and declines wherever
    more than one answer is consistent. So ``direction`` is ``forward`` along ``points_m``,
    ``reverse`` against it, or ``unknown`` -- 365, and 138 unknown, on the reference world --
    and ``basis`` is one of:

    * ``machine port`` -- this very pipe ends at a port the save TYPES. Barely an inference.
    * ``pump`` -- a pump or valve at one end, one-way by construction.
    * ``propagated`` -- only the shape of the wider network settles it.
    * ``unresolved`` -- and then ``direction`` is ``unknown``. A pipe in a loop, or a trunk
      with producers and consumers on both sides, genuinely has no fixed direction.

    A client may draw an arrow on the first three and must not on the fourth.

    **``curve_m`` rides here too, on exactly the belts' terms.** A pipe's ``mSplineData`` has
    carried an ``ArriveTangent`` and a ``LeaveTangent`` beside every ``Location`` the whole
    time, and schema 13 dropped them on the grounds that pipes are straight runs and elbows.
    They are -- and the six points of an elbow are its corners, not its curve: 166 of this
    world's 1,484 spans leave their chord by more than 10 cm and one by 6.6 m, so an elbow drew
    as the polygon cutting the corner it was built to round. One entry per span, ``null`` where
    the span is straight, ``null`` for all 207 pipes with no bend in them at all.

    Sent one row per piece, ungrouped, the posture ``/api/belts`` and ``/api/structures`` both
    take. Measured on the reference world -- 503 pipes, 1,987 points, 48 KB -- an order
    smaller than the 562 KB of belts beside it, because there are six times fewer of them.
    Nothing to thin: 3.9 points a pipe, and they are already only the corners.

    Not in here: pumps, junctions, valves and fluid buffers. They carry no spline at all, only
    a header position, so they are a different row shape and a different question -- the same
    question the belts key leaves open about splitters and mergers.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    networks = list((st.projection.get("pipes") or {}).get("networks") or ())
    # Positional against ``segments``, which is what the domain service promises, so a
    # projection too old to carry the join reads as one long row of "unknown" rather than
    # as an error. Guarded by index below for the same reason -- and ``seg.index`` is the
    # row's position in the raw table rather than a count of what decoded, which is what
    # keeps the two lists lined up when a row is torn.
    flows = st.pipe_flow
    resolved: dict[int, dict[str, Any]] = {}
    rows = []
    for seg in saverows.iter_pipe_segments(st.projection):
        if seg.class_index not in resolved:
            resolved[seg.class_index] = _pipe_class(st, seg.cls)
        points = [[_m(x), _m(y), _m(z)] for x, y, z in seg.points]
        net = seg.network_index
        entry = networks[net] if 0 <= net < len(networks) else {}
        fluid = entry.get("fluid") if isinstance(entry, dict) else None
        flow = flows[seg.index] if 0 <= seg.index < len(flows) else {}
        rows.append(
            {
                # The join, and the reason it is a field rather than the row's place in this
                # list: ``seg.index`` is the position in the RAW table, so a torn row leaves
                # a gap here that counting would silently close. ``/api/floors`` keys a pipe
                # run by exactly this number, and a client cannot line the two up without it.
                "row": seg.index,
                "direction": flow.get("direction", "unknown"),
                "basis": flow.get("basis", "unresolved"),
                # The game's own network id, not the index into the list above: the index is
                # an encoding detail of this payload and the id is a thing in the world.
                "network": entry.get("id") if isinstance(entry, dict) else None,
                "fluid": fluid,
                # Resolved against the dump like every other class here, so a popup never has
                # to show a reader a `Desc_…_C`.
                "fluid_name": st.game.item_name(fluid) if fluid else None,
                **resolved[seg.class_index],
                "points_m": points,
                "curve_m": _curve_m(seg.spans, points),
            }
        )
    return {
        "pipes": rows,
        "count": len(rows),
        "networks": len({r["network"] for r in rows if r["network"] is not None}),
        "directed": sum(1 for r in rows if r["direction"] != "unknown"),
    }
