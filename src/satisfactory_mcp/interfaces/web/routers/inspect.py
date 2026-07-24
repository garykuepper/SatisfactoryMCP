"""The point inspector: what is at a coordinate, and how well each part of it is known.

Named for its route rather than for the stdlib module it collides with -- the collision is
only a name in this package, since every ``import inspect`` in Python 3 is absolute and
still finds the standard library. Renaming the handler to dodge it would churn the
operation id, which costs more than the shadow does.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....domain.spatial import elevation as spatial_elevation
from ....domain.spatial import geo
from ....domain.spatial import nodes as spatial_nodes
from ....domain.spatial import regions as spatial_regions
from ....domain.world.state import WorldState
from .. import terrain
from ..serial import Region, _fail, _label_json, _state, _xyz

__all__ = ["INSPECT_NEAREST", "INSPECT_RADIUS_M", "router"]

router = APIRouter(prefix="/api")


# ----------------------------------------------------------- point inspector


#: How far a click looks for known elevations, metres. The same default
#: ``describe_location`` uses, so the map and the MCP tool answer one question one way.
INSPECT_RADIUS_M = 200.0

#: How many nodes a click reports. Enough to see what a site is next to; more would make
#: the popup a second copy of the node table.
INSPECT_NEAREST = 5


class InspectAt(TypedDict):
    """The coordinate that was asked about, rounded to the decimetre it was answered at.

    Neither field is nullable: both are required query parameters, so a request that carries
    no coordinate is a 422 before the handler runs and never reaches this shape.
    """

    x_m: float
    y_m: float


class Elevation(TypedDict):
    """One probe as JSON. The nullables here are the point of the endpoint, not slack in it.

    Named for the payload rather than for ``spatial_elevation.Elevation``, which is the
    domain object this is built FROM: that one holds populations, this one holds the four
    labelled answers plus the reason for every number it declines to give. See
    ``_elevation_json`` below for what each field means and which of the two causes each
    note names.

    **Declaration order is wire order**, so these are in the order ``_elevation_json``
    emits; see routers/floors.py for the rule at length. **A response_model FILTERS**, which
    is why ``counts`` is here even though nothing on the map page reads it -- leaving it out
    would delete it from the wire rather than merely from the types.

    ``radius_m``, ``ground_count`` and ``built_count`` are the three that cannot be null:
    the radius is a module constant and the two counts are lengths of lists. Everything
    else goes through ``_round``, which is ``None`` in, ``None`` out.
    """

    radius_m: float
    terrain_m: float | None
    terrain_source: str | None
    terrain_accuracy_m: float | None
    terrain_water_m: float | None
    terrain_water_depth_m: float | None
    terrain_water_note: str | None
    terrain_note: str | None
    ground_m: float | None
    ground_spread_m: float | None
    ground_count: int
    built_m: float | None
    built_count: int
    fill_m: float | None
    fill_note: str | None
    counts: dict[str, int]


class NearestNode(TypedDict):
    """One of the five nodes nearest a right-clicked point.

    The same coordinates ``/api/nodes`` sends and non-nullable for the same reason -- the
    static table's own three floats; routers/nodes.py's ``NodeRow`` states the evidence.
    ``occupant_cls`` is null wherever the occupancy join found nothing, and is null for ALL
    five whenever the save could not be read, which ``save_error`` says out loud.
    """

    id: str
    name: str
    resource: str
    kind: str
    purity: str
    x_m: float
    y_m: float
    z_m: float
    occupied: bool
    occupant_cls: str | None
    distance_m: float


class InspectResponse(TypedDict):
    """What ``/api/inspect`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``region`` is ``null`` for ocean and off-map -- ``_label_json``'s refusal, which this
    layer must not undo. ``save_error`` is non-null exactly when the save would not load,
    and the answer is still a real answer: the node table is static and needs no ``.sav``.
    """

    at: InspectAt
    region: Region | None
    elevation: Elevation
    nearest: list[NearestNode]
    save_error: str | None


def _elevation_json(near: spatial_elevation.Elevation) -> Elevation:
    """A probe as JSON, with the reason for every number it declines to give.

    Four sources, and each is labelled as what it is. ``terrain_m`` is one texel of the
    extracted heightfield read at exactly the coordinate asked about; ground and built are
    populations of things standing nearby. They stay apart all the way out to the page,
    because that is the whole point of the module they come from: a node rests on terrain,
    a foundation is wherever the player put it, a texel is the game's own ground, and one
    median over the three would be a number describing none of them.

    ``terrain_source`` says which layer of the field answered and ``terrain_accuracy_m``
    carries what the generator measured for that layer, so a reading is never quoted
    without the uncertainty that belongs to it -- a 0.2 m landscape texel and a 3.9 m fill
    texel are both "the terrain" and are not the same claim.

    ``fill_m`` is ``null`` more often than not, and a null with no reason next to it reads
    as a bug. It has exactly two causes -- fewer than ``MIN_GROUND_SAMPLES`` nodes nearby,
    or nothing built nearby -- and ``fill_note`` names whichever one applied. Neither is
    ever rendered as 0: zero fill is a real, different measurement. ``terrain_note`` does
    the same job for the field, and it too has exactly two causes: no field on this
    machine, or a coordinate the field has no data for.

    Water is two numbers for the same reason, and the second one is null far more often
    than the first. ``terrain_water_m`` is the surface's own height, which the channel
    takes from a cooked water volume's bounding box and knows to centimetres wherever
    there is water at all. ``terrain_water_depth_m`` is that minus the ground, which only
    exists where the ground under the water was itself measured at 1 m -- over the fill
    layer, which is most of the ocean, subtracting a 3.9 m-quantised raster from a sea
    surface produces a number nobody measured. So it is ``null`` there, with
    ``terrain_water_note`` saying why, and never 0.0.
    """
    ground, built = near.ground, near.built
    # Derived from the samples actually present rather than from a hardcoded list, so a
    # new non-ground source in the domain module arrives here without an edit.
    built_sources = tuple(s for s in near.counts if s not in spatial_elevation.GROUND_SOURCES)

    fill = near.fill_m
    note = None
    if fill is None:
        if len(ground) < spatial_elevation.MIN_GROUND_SAMPLES:
            note = (
                f"not enough ground samples ({len(ground)} of "
                f"{spatial_elevation.MIN_GROUND_SAMPLES} within {near.radius_m:g} m)"
            )
        elif not built:
            note = f"nothing built within {near.radius_m:g} m"

    def _round(value: float | None) -> float | None:
        return None if value is None else round(value, 1)

    terrain_probe = near.terrain
    terrain_note = None
    if terrain_probe is None:
        terrain_note = (
            "the field has no data at this point -- open ocean, or a cave mouth"
            if terrain.field() is not None
            else "no terrain field on this machine (run tools/gen_world_heightmap.py)"
        )
    water_note = None
    if (
        terrain_probe is not None
        and terrain_probe.submerged
        and terrain_probe.water_depth_m is None
    ):
        water_note = (
            f"the ground under this water is the {terrain_probe.source} layer, which is too "
            "coarse to subtract a surface from, so the depth here is not known"
        )

    return {
        "radius_m": near.radius_m,
        "terrain_m": _round(near.terrain_m),
        "terrain_source": terrain_probe.source if terrain_probe else None,
        "terrain_accuracy_m": terrain_probe.accuracy_m if terrain_probe else None,
        "terrain_water_m": (
            _round(terrain_probe.water_m) if terrain_probe and terrain_probe.submerged else None
        ),
        "terrain_water_depth_m": _round(terrain_probe.water_depth_m) if terrain_probe else None,
        "terrain_water_note": water_note,
        "terrain_note": terrain_note,
        "ground_m": _round(near.median(*spatial_elevation.GROUND_SOURCES)),
        "ground_spread_m": _round(near.spread(*spatial_elevation.GROUND_SOURCES)),
        "ground_count": len(ground),
        "built_m": _round(near.median(*built_sources)) if built_sources else None,
        "built_count": len(built),
        "fill_m": _round(fill),
        "fill_note": note,
        "counts": dict(near.counts),
    }


def _nearest_nodes(table, taken: dict, x: float, y: float, limit: int) -> list[NearestNode]:
    """The closest ``limit`` nodes to a point, centimetres in, metres out."""
    ranked = sorted(
        ((geo.distance_m((x, y), (n["x"], n["y"])), n) for n in table.nodes),
        key=lambda pair: pair[0],
    )
    out: list[NearestNode] = []
    for distance_m, n in ranked[:limit]:
        held = taken.get(n["instance"])
        out.append(
            {
                "id": n["instance"],
                "name": str(n["instance"]).rsplit(".", 1)[-1],
                "resource": n["resource"],
                "kind": n["kind"],
                "purity": n["purity"],
                **_xyz((n["x"], n["y"], n["z"])),
                "occupied": held is not None,
                "occupant_cls": held["extractor"] if held else None,
                "distance_m": round(distance_m, 1),
            }
        )
    return out


@router.get("/inspect", response_model=InspectResponse)
def inspect(
    request: Request,
    x_m: float,
    y_m: float,
    save: str | None = None,
    world: str | None = None,
) -> Any:
    """What is at a coordinate: the region, the measured ground, and the nearest nodes.

    The three answers a site starts with, and none of them was on the map before. Every
    one comes straight out of ``domain.spatial`` -- this endpoint converts metres to the
    save's centimetres, calls three functions, and rounds.

    **A failed save is not a failed answer.** The node table is static, covers the whole
    map and needs no ``.sav`` at all, so a world whose save will not load still gets its
    region, its ground elevation and its nearest nodes; what it loses is the built
    population and the occupancy join, and ``save_error`` says so out loud rather than
    letting "no extractor here" quietly mean "no save here".

    **And it prefers the extracted terrain when there is any.** On a machine where
    ``tools/gen_world_heightmap.py`` has been run, the 1 m field answers "how high is it
    here" for unexplored ground with one number at the coordinate asked about, instead of a
    population of things standing near it -- and it says which layer of itself answered, so
    a 0.2 m landscape reading and a 3.9 m fill reading are told apart. Where there is no
    field, or the field has no data there, this is exactly the endpoint it was before.

    Not cached, deliberately and by measurement: ``sample_points`` over the 320-hour
    reference world builds 9,525 samples in 2.0 ms and ``probe`` scans them in 0.8 ms, so
    a per-(world, save) cache would add an invalidation bug to save ~3 ms on a click. The
    field is cached, because it is 0.45 s of zlib and 170 MB either way -- but by the
    loader, keyed on its own sidecar's mtime, so this endpoint stays a caller.
    """
    try:
        table = spatial_nodes.load_nodes()
        rmap = spatial_regions.load_regions()
    except FileNotFoundError as exc:
        return _fail(str(exc), 404)

    st: WorldState | None = None
    save_error: str | None = None
    try:
        st = _state(request, save, world)
    except Exception as exc:
        save_error = f"could not read save: {exc}"

    x, y = x_m * 100.0, y_m * 100.0
    near = spatial_elevation.probe(
        x,
        y,
        spatial_elevation.sample_points(table, st),
        INSPECT_RADIUS_M,
        terrain_field=terrain.field(),
    )
    taken = spatial_nodes.occupancy(st.projection) if st is not None else {}
    return {
        "at": {"x_m": round(x_m, 1), "y_m": round(y_m, 1)},
        "region": _label_json(rmap.label_for(x, y)),
        "elevation": _elevation_json(near),
        "nearest": _nearest_nodes(table, taken, x, y, INSPECT_NEAREST),
        "save_error": save_error,
    }
