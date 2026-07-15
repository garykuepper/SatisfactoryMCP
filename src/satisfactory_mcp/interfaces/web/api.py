"""The JSON endpoints. Parse the query, call a domain service, serialise.

The same shape as an MCP tool module and for the same reason: this layer decides
nothing. Every question answered here is answered by ``domain/`` -- the placement rows,
the power ledger, the node occupancy, the factory proposals -- and the only work done
in this file is turning a query string into arguments and a dataclass into JSON.

The two conventions this surface runs on -- metres at one decimal, and ``?save=``/
``?world=`` everywhere a state is read -- live with the helpers that enforce them, in
``serial.py``, which is where the error shape is written down too.

**This module is being emptied.** One concern at a time moves to ``routers/``, in the
order the handlers are decorated here, and ``app.py`` includes what is left AFTER them --
which is what keeps ``/openapi.json``'s path order, and therefore the committed
``api-schema.d.ts``, byte-identical while the file shrinks. Nothing new goes in here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypedDict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ...core.gamedata.footprint import FOUNDATION_M
from ...core.gamedata.model import pretty_class
from ...core.saveio import rows as saverows
from ...domain.collectibles.service import collect_view
from ...domain.factories import floors as ffloors
from ...domain.factories import identity as fidentity
from ...domain.factories import select as fselect
from ...domain.spatial import geo
from ...domain.world.state import WorldState
from . import terrain
from .serial import _fail, _m, _state, _xyz, _yaw

__all__ = ["PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment. Proxies and browsers
#: both drop a connection that has said nothing for a while, and a comment line is the
#: cheapest thing that counts as having said something.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- helpers


def _record_row(st: WorldState, row: dict) -> dict:
    """One machine/extractor/generator, flattened for the map.

    ``clock`` and ``paused`` are read with ``get``: the projection only carries them
    for the records that have them, and an extractor at 250% and a constructor with no
    overclock property must both come out of here without a KeyError.

    ``w_m``/``l_m`` are the building's own footprint -- the X and Y extent of the union
    of its clearance boxes, which is what makes a Manufacturer draw bigger than a
    Constructor instead of both being the same nominal square. Only 69 of 539 buildings
    carry clearance data, so these are **null** for the rest (the two biomass burners on
    the reference save among them) rather than a guessed number: the client picks the
    fallback, because a fallback drawn here would be indistinguishable from a measurement.

    ``yaw`` is which way the building faces, and it is what turns ``w_m``/``l_m`` from an
    axis-aligned box into the rectangle the player actually placed -- the two are one
    answer and are read together or not at all.

    ``h_m`` is the third side of the same box, and it is the one dimension a top-down map
    cannot show: it is here because a FLOOR view needs it. A Refinery is 15 m tall standing
    on a 12 m storey, so it comes three metres through the deck above and is physically in
    the way of anything built there -- a fact about the floor above that can only be read
    off the floor below. Null on exactly the same terms as ``w_m``/``l_m``, from the same
    ``mClearanceData``, and a client that has no height draws no such claim.
    """
    cls = row.get("cls") or ""
    building = st.game.buildings.get(cls)
    footprint = getattr(building, "footprint", None) if building else None
    recipe_id = row.get("recipe")
    recipe = st.game.recipes.get(recipe_id) if recipe_id else None
    return {
        "instance_leaf": str(row.get("instance", "")).rsplit(".", 1)[-1],
        "cls": row.get("cls"),
        # The docs name where the dump has one; readable words either way. The raw class
        # stays in ``cls`` for anything that needs the exact id.
        "name": st.game.building_name(cls),
        **_xyz(row.get("pos")),
        "recipe": recipe_id,
        # Same rule for the recipe: the row a player most wants to read must not be the
        # one row still speaking engine ids.
        "recipe_name": recipe.name if recipe else pretty_class(recipe_id),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
        "yaw": _yaw(row.get("yaw")),
        # Footprint is already metres; the projection's coordinates are not.
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
        "h_m": round(footprint.height_m, 1) if footprint else None,
    }


# ------------------------------------------------------------------- machines


@router.get("/machines")
def machines(request: Request, save: str | None = None, world: str | None = None) -> Any:
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    p = st.projection
    return {
        kind: [_record_row(st, row) for row in p.get(kind, ())]
        for kind in ("machines", "extractors", "generators")
    }


# ----------------------------------------------------------------- structures


@router.get("/structures")
def structures(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Every lightweight buildable the player placed: foundations, ramps, walls, catwalks.

    These are the only record of what was physically BUILT -- they appear in no actor
    header, which is why the projection interns them separately as
    ``{"classes": [...], "instances": [[class_index, x, y, z, yaw], ...]}`` in centimetres.
    Decoded by ``core.saveio.rows``, which is where the guard lives for all ten readers
    of these interned tables: a malformed row costs one piece, not the endpoint.

    **Rotation is carried, as of schema 12**, and this docstring used to say the opposite
    -- the instance transform's quaternion was dropped at extraction and a client could
    only draw these axis-aligned, which is why an angled slab came out of the map as a
    staircase of squares. ``yaw`` is now the fifth column of a row and comes out as a
    ``yaw`` field: degrees about world Z, positive turning +X towards +Y. ``null`` for a
    projection cut before 12, which a client must keep drawing axis-aligned rather than
    reading as zero.

    One thing the projection still does not carry, and it is not invented here:

    * **Per-class size.** None of these classes has clearance data, so ``footprint`` is
      ``None`` for all eighteen of them. They are all built on the same grid instead,
      whose edge ``tile_m`` reports from ``FOUNDATION_M`` so the page does not hardcode 8.

    Positions are piece centres: on the reference save consecutive foundations of one
    slab sit exactly ``tile_m`` apart.

    A world with nothing built answers ``{"structures": [], "count": 0}`` -- an empty
    list is a real answer here, unlike a save that could not be read at all.

    Sent one row per piece, ungrouped. Measured on the reference world -- 8,347 pieces,
    708 KB (610 KB of it before the yaw column) -- which is the same order as
    ``/api/collectibles`` already ships (3,455 rows, 547 KB). Grouping into grid cells would halve a
    payload that is not the bottleneck and would cost the per-piece class the popup and
    the point inspector read.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    out = [
        {
            "cls": piece.cls,
            "x_m": _m(piece.x),
            "y_m": _m(piece.y),
            "z_m": _m(piece.z),
            # Optional on purpose: a row from a schema-11 projection is four columns long
            # and is still a real piece at a real place, it just has no facing. ``None``
            # covers the schema-16 unreadable rotation too -- see ``saveio.rows``.
            "yaw": _yaw(piece.yaw),
        }
        for piece in saverows.iter_structures(st.projection)
    ]
    return {"structures": out, "count": len(out), "tile_m": FOUNDATION_M}


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


# ------------------------------------------------------------------ factories


@router.get("/factories")
def factories(request: Request, save: str | None = None, world: str | None = None) -> Any:
    """Named factories and the coherence-scored proposals for the unnamed rest.

    Each row carries ``bbox_m`` -- ``[x_min, y_min, x_max, y_max]`` in metres, game axes
    -- alongside its centroid, because a centroid alone cannot frame a viewport. The map
    turns a label into a button that flies to its factory, and "fly to the mean of 50
    machines" is not the same request as "show me all 50": the first picks a zoom out of
    the air, the second is decided by the extent. Computed here rather than client-side
    because the client is never sent the anchor machines, only their count.

    ``null`` when nothing in the set is still standing -- ``geo.bbox`` refuses to invent
    a zero box at the world centre, and so does this. A label whose machines were all
    demolished keeps its name and its remembered centroid; what it loses is the ability
    to be flown to, which is the honest report.

    A proposal whose machines the player has already named is not a proposal: the
    clusterer runs over the whole world, so it re-discovers every named factory, and
    sending those rows lets a machine-generated recipe string draw itself exactly on
    top of the player's own label. Any proposal in which named anchors are the majority
    is dropped here; ``index`` stays the position in the full proposal list, so a
    ``proposal:N`` selector still resolves to the same cluster in the MCP tools.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    placed = fidentity.positions(st.projection)

    def _bbox_m(machines) -> list[float] | None:
        box = geo.bbox([placed[m][:2] for m in machines if m in placed])
        return None if box is None else [_m(v) for v in box]

    named = [
        {
            "name": label.name,
            "centroid_m": [_m(label.centroid[0]), _m(label.centroid[1])],
            "bbox_m": _bbox_m(label.anchors),
            "machines": len(label.anchors),
            "notes": label.notes,
        }
        for label in sorted(st.labels.labels, key=lambda x: -len(x.anchors))
    ]

    labelled = {anchor for label in st.labels.labels for anchor in label.anchors}

    proposals = []
    for index, pr in enumerate(st.proposals):
        if pr.machines and 2 * sum(1 for m in pr.machines if m in labelled) > len(pr.machines):
            continue  # already named by the player; the label speaks for it
        cand = fidentity.describe(pr.machines, st.graph, st.game, st.projection, "proposal")
        proposals.append(
            {
                "index": index,
                "label": cand.name_hint(),
                "centroid_m": [_m(cand.centroid[0]), _m(cand.centroid[1])],
                "bbox_m": _bbox_m(pr.machines),
                "machines": pr.size,
                "score": round(pr.cohesion, 3),
                "spread_m": round(cand.spread_m, 1),
            }
        )
    return {"labels": named, "proposals": proposals}


# --------------------------------------------------------------------- floors
#
# **The one endpoint in this module that declares its response body, and the reason is that
# nothing has been written against it yet.** Every other endpoint here is annotated ``->
# dict``, so FastAPI publishes no response schema for it and ``/openapi.json`` types the
# rest of them ``unknown``; the map page fills that gap by hand in ``api-types.ts``, from
# observed payloads, which that file says at the top and which is honest about what it is --
# an observation, and an observation can be wrong in one direction.
#
# Converting the lot is a change to the server's whole public surface and belongs in its own
# commit. Converting THIS one is a different act: the floor view has no client at all yet, so
# there is no hand-written block to reconcile with and no drawing code whose guards are the
# evidence for a nullable. The types below are read off the serialisers three screens down
# rather than off a payload, ``npm run typegen`` turns them into the page's own types, and
# the client that gets written next is written against a declared contract instead of a
# further hand-maintained interface.
#
# TypedDict rather than a pydantic model, for the same reason the serialisers are functions
# returning dicts: this layer decides nothing and holds no state, and a class hierarchy here
# would invite behaviour into a module whose whole claim is that it has none.
#
# **Nullability is not decoration.** ``_m``, ``_xyz`` and ``_yaw`` all return ``float |
# None``, so every field they produce is declared that way even where the reference world
# has never produced a null -- a response_model is a validator as well as a schema, and a
# field declared ``float`` that arrives null is a 500 rather than a null.


class FloorDeck(TypedDict):
    """One band, identified. What a run's ``ends`` are made of."""

    platform: int
    ordinal: int
    top_m: float | None


class FloorBand(TypedDict):
    """One floor of one platform. See ``_band_json`` for what each field means."""

    ordinal: int
    top_m: float | None
    low_m: float | None
    high_m: float | None
    span_m: float | None
    pieces: int
    cells: int
    area_m2: float
    share: float
    minor: bool
    machines: list[str]
    attachments: list[str]
    deck_rows: list[int]
    machine_count: int
    attachment_count: int
    deck_row_count: int


class FloorPlatform(TypedDict):
    """One 4-connected run of foundation cells, and the bands its tops fall into."""

    index: int
    cells: int
    pieces: int
    area_m2: float
    centre_m: list[float | None]
    extent_m: list[float | None]
    clean: float
    label: str | None
    slab: int | None
    bands: list[FloorBand]


class FloorRun(TypedDict):
    """One belt chain or one pipe, keyed by the join the belt and pipe payloads carry.

    ``ends`` is always two entries, head then tail, either of which may be ``null`` where
    that end is over no deck. A pair rather than a list is what it means, and JSON has no
    pair -- so the length is a promise the prose makes and the schema cannot.
    """

    kind: str
    key: int
    pieces: int
    lift: bool
    rise_m: float | None
    riser: bool
    ends: list[FloorDeck | None]


class FloorPlacement(TypedDict):
    """One thing that is NOT on a floor, and the reason it is not."""

    instance_leaf: str
    cls: str
    name: str
    kind: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    above_terrain_m: float | None


class FloorCounts(TypedDict):
    """The shape of the answer before the rows. Nested; see ``FloorReport.counts``."""

    platforms: int
    bands: int
    runs: int
    violations: int
    #: Keyed by ``ffloors.GROUPS`` and ``ffloors.MEMBERSHIPS``. Left as open maps rather
    #: than spelled out as four fields each: the two vocabularies are the domain's, they
    #: are exported from there, and restating them here would be a second place to update.
    placements: dict[str, int]
    membership: dict[str, int]


class FloorRules(TypedDict):
    """The thresholds the answer was produced with, in the units the answer is in."""

    tile_m: float | None
    cluster_tol_m: float | None
    band_eps_m: float | None
    min_band_pieces: int
    belt_height_m: float | None
    riser_m: float | None
    terrain_tol_m: float
    minor_share: float


class FloorsResponse(TypedDict):
    """What ``/api/floors`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    Field order matters here and is the emission order below, because a response_model
    serialises in declaration order: reordering these reorders the bytes on the wire.
    """

    note: str | None
    selection: str | None
    terrain_measured: bool
    counts: FloorCounts
    platforms: list[FloorPlatform]
    #: Keyed by ``ffloors.MEMBERSHIPS``, and ``placements`` by ``ffloors.GROUPS`` less
    #: ``band`` -- what landed on a floor is listed inside its own band, by id.
    runs: dict[str, list[FloorRun]]
    placements: dict[str, list[FloorPlacement]]
    violations: list[FloorRun]
    rules: FloorRules


def _band_json(band: ffloors.Band) -> FloorBand:
    """One floor: where its deck is, how big it is, and what stands on it -- by id.

    ``machines`` and ``attachments`` are **instance ids, not geometry**, and that is the
    whole shape of this payload. The page already holds every machine, splitter, belt and
    pipe in the world from ``/api/machines``, ``/api/belts`` and ``/api/pipes``; what it
    cannot work out for itself is which floor each one is on. Re-serialising the positions
    here would ship the same 700 KB a second time so that a filter could be applied to it.

    ``deck_rows`` is the same idea for the concrete, by the only name a lightweight
    buildable has. The subsystem stores no instance ids at all -- that is what makes it
    lightweight -- so a deck is listed by its pieces' POSITIONS in ``/api/structures``,
    which every reader of that payload already has and which both sides derive from one
    ``saveio.rows`` walk in one order. Without it a client can only re-derive a deck from
    heights, and this world's 1 m and 2 m half-steps are exactly where that goes wrong.

    ``deck_rows`` is the pieces at the band's own LEVEL and ``pieces`` is the size of the
    cluster it was found in. Those are different questions and could differ where a cluster
    is wider than ``BAND_EPS_CM``; on the reference world they agree on all 93 bands, which
    is the same "the bands are exact" the epsilon sweep measured. Both are reported rather
    than reconciled, and a client that draws a deck wants ``deck_rows``.

    ``span_m`` is how much the band's own level is spread, which is 0.0 for every band on
    the reference world -- a band is a level, not a cluster. It is not the storey height:
    the distance to the floor above is the next band's ``top_m``, and a client that wants a
    ceiling can subtract two numbers it already has.
    """
    return {
        "ordinal": band.ordinal,
        "top_m": _m(band.top_cm),
        "low_m": _m(band.low_cm),
        "high_m": _m(band.high_cm),
        "span_m": _m(band.span_cm),
        "pieces": band.pieces,
        "cells": band.cells,
        "area_m2": round(band.area_m2, 1),
        # What keeps a six-cell mezzanine from being read in the same voice as a 218-cell
        # deck. The share is against the platform's own largest band, so it is a statement
        # about this platform rather than about the world.
        "share": round(band.share, 3),
        "minor": band.minor,
        "machines": band.machines,
        "attachments": band.attachments,
        "deck_rows": band.rows,
        "machine_count": len(band.machines),
        "attachment_count": len(band.attachments),
        "deck_row_count": len(band.rows),
    }


def _platform_json(platform: ffloors.Platform) -> FloorPlatform:
    """One platform, and the provenance of the decomposition that produced it."""
    return {
        "index": platform.index,
        "cells": platform.cells,
        "pieces": platform.pieces,
        "area_m2": round(platform.area_m2, 1),
        "centre_m": [_m(platform.centre_cm[0]), _m(platform.centre_cm[1])],
        "extent_m": [_m(platform.extent_cm[0]), _m(platform.extent_cm[1])],
        # The premise, per platform rather than averaged: the share of this platform's
        # foundation pieces that landed within epsilon of one of its own bands.
        "clean": round(platform.clean, 4),
        # Naming only. Neither took any part in deciding where the floors are.
        "label": platform.label,
        "slab": platform.slab,
        "bands": [_band_json(b) for b in platform.bands],
    }


def _deck_json(deck: ffloors.Deck | None) -> FloorDeck | None:
    if deck is None:
        return None
    return {"platform": deck.platform, "ordinal": deck.ordinal, "top_m": _m(deck.top_cm)}


def _run_json(run: ffloors.Run) -> FloorRun:
    """One belt chain or one pipe, keyed by the join a client already has.

    For a belt that is ``chain``, the field ``/api/belts`` puts on every piece. For a pipe
    it is the row's position in ``/api/pipes``, which is the same positional key
    ``domain.world.flow`` uses to attach a direction. Neither carries the polyline again.
    """
    return {
        "kind": run.kind,
        "key": run.key,
        "pieces": run.pieces,
        "lift": run.lift,
        "rise_m": _m(run.rise_cm),
        # Tall enough that it can only be a floor connector, which is a different claim
        # from being a lift: a quarter of lift chains are belt-height jogs on one deck.
        "riser": run.riser,
        "ends": [_deck_json(d) for d in run.ends],
    }


def _placement_json(st: WorldState, placement: ffloors.Placement) -> FloorPlacement:
    """One thing that is NOT on a floor, and the reason it is not."""
    return {
        "instance_leaf": placement.instance,
        "cls": placement.cls,
        "name": st.game.building_name(placement.cls),
        "kind": placement.kind,
        **_xyz(placement.pos_cm),
        "above_terrain_m": (
            None if placement.above_terrain_m is None else round(placement.above_terrain_m, 1)
        ),
    }


@router.get("/floors", response_model=FloorsResponse)
def floors_view(
    request: Request,
    factory: str | None = None,
    platform: int | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is built, one storey at a time: the floor decomposition of a world.

    Nothing in the save says "floor". ``domain.factories.floors`` recovers them from the
    geometry -- 4-connected platforms of 8 m foundation cells, then a per-platform cluster
    of deck heights -- and every constant it uses was measured before it was written. This
    endpoint parses the query, calls it once, and rounds.

    **It ships ids, not geometry, and that is the design.** A client already has every
    machine, splitter, belt and pipe from the four endpoints above; the one thing it cannot
    derive is which floor each of them is on. So a band lists ``machines`` and
    ``attachments`` as instance leaves, and a run is keyed by its belt ``chain`` or its pipe
    row position -- the joins those payloads already carry. Sending the coordinates again
    would double 1.3 MB so that a filter could be applied to the copy.

    **The runs are grouped by what they do to a floor**, not listed flat:

    * ``same-deck`` -- both ends over one band. 84.8% of belt runs, and the set a floor
      filter draws.
    * ``connector`` -- the ends are on two different bands. This is how you leave a floor,
      and it is where the lifts and risers are.
    * ``terrain`` -- neither end is over a deck. 44.5% of pipes, because plumbing hugs the
      ground.
    * ``mixed`` -- one end on a deck, one on the ground.

    **``placements`` is only what did NOT land on a floor.** Things that did are listed by
    id inside their own band, so listing them here as well would be the same 1,252 rows
    twice. What is here is the three honest ways of not being on a floor: ``exempt`` (a
    miner stands on a resource node and a water extractor on water -- by native class, not
    by a substring), ``terrain`` (measured against the heightfield) and ``off-deck``.

    **``terrain_measured`` says whether the ground was consulted at all.** The 1 m
    heightfield is derived from the reader's own game install and most machines have none,
    in which case nothing can be in the ``terrain`` group and an empty one would otherwise
    read as "nothing is on the ground here".

    ``?factory=`` takes a label the player gave a factory, or any selector the MCP tools
    take, and narrows the answer to the platforms that factory stands on. ``?platform=`` is
    the index this endpoint hands out, which is stable across calls. Either narrows
    placements and runs to that footprint -- including the ones underneath it, since "what
    is under this deck" is part of the question.

    A save too old to carry ``FGLightweightBuildableSubsystem`` is a **200 with a
    ``note``**, not an error and not an empty list: the world has floors, this file cannot
    show them, and those are different sentences.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    try:
        report = ffloors.floor_decomposition(
            st, platform=platform, label=factory, terrain_field=terrain.field()
        )
    except fselect.SelectorError as exc:
        return _fail(str(exc))

    if report.note and (platform is not None or factory is not None) and not report.platforms:
        # A selection that matched nothing is a bad request; a save that cannot carry the
        # data at all is not, and falls through to the 200 with its note below.
        return _fail(report.note, 404)

    return {
        "note": report.note,
        "selection": report.selection,
        "terrain_measured": report.terrain_measured,
        "counts": report.counts(),
        "platforms": [_platform_json(p) for p in report.platforms],
        "runs": {
            membership: [_run_json(r) for r in report.runs_of(membership)]
            for membership in ffloors.MEMBERSHIPS
        },
        "placements": {
            group: [_placement_json(st, p) for p in report.group(group)]
            for group in ffloors.GROUPS
            if group != "band"
        },
        # A riser that lands both ends on one band cannot happen -- 0 of 89 on the reference
        # world, 0 of 75 on the oldest save that can carry the data -- so one here is a
        # symptom of the decomposition drifting, and it is reported rather than swallowed.
        "violations": [_run_json(r) for r in report.violations],
        # The thresholds the answer was produced with, in the units the answer is in, so a
        # reader never has to go and look up what "clean" was measured against.
        "rules": {
            "tile_m": _m(ffloors.CELL_CM),
            "cluster_tol_m": _m(ffloors.CLUSTER_TOL_CM),
            "band_eps_m": _m(ffloors.BAND_EPS_CM),
            "min_band_pieces": ffloors.MIN_BAND_PIECES,
            "belt_height_m": _m(ffloors.BELT_HEIGHT_CM),
            "riser_m": _m(ffloors.RISER_CM),
            "terrain_tol_m": ffloors.TERRAIN_TOL_M,
            "minor_share": ffloors.MINOR_SHARE,
        },
    }


# ---------------------------------------------------------------- collectibles


@router.get("/collectibles")
def collectibles(
    request: Request,
    group: str | None = None,
    mode: str = "remaining",
    near: str | None = None,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """Map placements, filtered exactly the way the MCP tool filters them.

    ``collect_view`` owns every refusal -- unknown mode, retired group, and the one
    that matters here: ``mode=remaining`` needs the generated placement table, and
    without it the honest answer is the refusal rather than a shorter list.
    """
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)

    view = collect_view(st, group, mode, near)
    if view.error:
        return _fail(view.error)

    rows = [
        {
            "category": r["category"],
            "name": r["name"],
            **_xyz(r["pos"]),
            "collected": r["collected"],
            "observed": r["observed"],
            "distance_m": round(r["distance_m"], 1) if r.get("distance_m") is not None else None,
        }
        for r in (view.rows or ())
    ]
    return {
        "mode": view.mode,
        "group": view.group,
        "rows": rows,
        "counts": view.counts,
        "hidden_pedestals": view.hidden,
        "save_only": view.save_only,
        "where": view.where,
    }


# --------------------------------------------------------------------- events


def _sse(event: str | None, data: str) -> bytes:
    if event is None:
        return f": {data}\n\n".encode()
    return f"event: {event}\ndata: {data}\n\n".encode()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """Server-sent events: one ``save`` event per observed write, plus keepalives.

    The stream carries the trigger, never the payload. A save event says which file
    moved and when; the page decides what to refetch. That keeps this endpoint O(1) in
    the size of the world and means a browser that missed an event is one refetch, not
    one resync, behind.
    """
    watcher = request.app.state.watcher
    queue = watcher.subscribe()

    async def stream():
        try:
            if watcher.latest is not None:
                yield _sse("save", json.dumps(watcher.latest.as_dict()))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield _sse(None, "ping")
                    continue
                yield _sse("save", json.dumps(event.as_dict()))
        finally:
            watcher.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
