"""``/api/machines`` and ``/api/structures``: everything the player physically placed.

Two endpoints and one row builder, in one file because they answer one question in two
resolutions. A machine is an ACTOR -- it has an instance id, a recipe, a clock -- and a
foundation is a lightweight buildable with none of those, interned into a positional table
because a record per piece would be megabytes. What they share is that both are things
standing somewhere with a size and a facing, which is the whole of what a map draws.

WARNING: the function name is the operation_id -- rename it and the committed schema
churns. FastAPI's default id is ``{function_name}_{path}_{method}`` and ``api-schema.d.ts``
is generated off it.

**Declaration order is wire order** for the TypedDicts below, and a ``response_model``
FILTERS -- both rules are written out at length on ``FloorsResponse`` in routers/floors.py
and are not restated per file.

THE ONE THING THIS FILE DECIDES that its neighbours do not: an ACTOR record always carries
a class, and an INTERNED table row may not. ``machines``/``extractors``/``generators`` are
actor records, written by ``saveio.extract`` behind ``if not cls.startswith("Build_")``, so
``cls`` is a non-empty string on every one of them and ``building_name`` therefore never
falls through to a ``None``. ``structures`` is the interned table, where a class is an INDEX
into a legend and ``saveio.rows`` types ``Structure.cls`` as ``str | None`` for the row whose
index points past the end -- a real piece at a real place with no name. So the two row shapes
below differ on ``cls``/``name`` for a reason, and it is not an oversight.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import APIRouter, Request

from ....core.gamedata.footprint import FOUNDATION_M
from ....core.gamedata.model import pretty_class
from ....core.saveio import rows as saverows
from ....domain.world.state import WorldState
from ..serial import _fail, _m, _state, _xyz, _yaw

__all__ = ["router"]

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- helpers


class PlacementRow(TypedDict):
    """A machine, an extractor or a generator: one row shape, three layers.

    ``cls`` and ``name`` are NOT nullable, which is a claim about these three keys of the
    projection rather than about ``_record_row``. They are actor records and every one of
    them was written past ``cls.startswith("Build_")``, so the id is there and
    ``building_name`` resolves it or renders it -- see the module docstring for the line
    this draws against ``/api/structures`` next door.

    Everything a coordinate helper touches IS nullable, and each for its own reason.
    ``x_m``/``y_m``/``z_m``: an actor whose transform did not decode has no ``pos`` and
    ``_xyz`` answers with a triple of nulls -- the page's ``drawPlacements`` skips on
    exactly that. ``yaw``: ``null`` means the projection predates schema 12 and the facing
    was never recorded, which is a different claim from a facing of zero.

    ``clock`` is ``float | None`` and the float is load-bearing: ``mCurrentPotential``
    reaches the projection through ``round(float(...), 6)``, so 250% is ``2.5`` and 200% is
    ``2.0`` -- an ``int`` here would reject the first and a machine with no overclock
    property at all sends no ``clock`` key, which is the null. ``recipe_name`` is null on
    the same terms ``pretty_class`` is: no recipe in, no words out.

    ``w_m``/``l_m``/``h_m`` go null TOGETHER -- one clearance box, read whole or not at all
    -- for the 470 of 539 buildings the docs dump carries no ``mClearanceData`` for. All
    three are floats where they are anything: ``Footprint`` is metres already and this
    layer only rounds.
    """

    instance_leaf: str
    cls: str
    name: str
    x_m: float | None
    y_m: float | None
    z_m: float | None
    recipe: str | None
    recipe_name: str | None
    clock: float | None
    paused: bool
    yaw: float | None
    w_m: float | None
    l_m: float | None
    h_m: float | None


class MachinesResponse(TypedDict):
    """What ``/api/machines`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    Three keys and no counts, which is the payload's own shape: the handler is a dict
    comprehension over the three projection keys and the page reads ``data[kind]``.
    """

    machines: list[PlacementRow]
    extractors: list[PlacementRow]
    generators: list[PlacementRow]


class StructureRow(TypedDict):
    """One lightweight buildable: a foundation, a ramp, a wall, a catwalk.

    ``cls`` is nullable HERE and not on ``PlacementRow``, and the module docstring says why:
    this is the interned table, a class is an index into a legend, and a row whose index
    points past the end is a real piece at a real place with no name. ``saveio.rows`` types
    it that way and this endpoint passes ``piece.cls`` straight through.

    The three coordinates are NOT nullable, and that is ``iter_structures``' own refusal
    rather than this layer's: a row whose x, y or z will not read as a number is DROPPED
    there, so a piece that reaches ``_m`` here has all three and ``_m`` of a float is a
    float. This is the same shape of claim ``/api/nodes`` makes about its own triple --
    ``_xyz`` is broad because it also serves placements, whose transforms can fail, and
    neither of those two endpoints has a transform to fail.

    ``yaw`` is the one that survives being unreadable: ``null`` for a schema-11 row with no
    fifth column at all, and for the schema-16 rotation that will not decode.
    """

    cls: str | None
    x_m: float
    y_m: float
    z_m: float
    yaw: float | None


class StructuresResponse(TypedDict):
    """What ``/api/structures`` sends on a 200. An error is a 4xx with ``{"error": ...}``.

    ``tile_m`` is a float because ``FOUNDATION_M`` is ``8.0``: declaring it ``int`` would
    validate 8.0 into 8 and rewrite the bytes on the wire.
    """

    structures: list[StructureRow]
    count: int
    tile_m: float


def _record_row(st: WorldState, row: dict) -> PlacementRow:
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


@router.get("/machines", response_model=MachinesResponse)
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


@router.get("/structures", response_model=StructuresResponse)
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
