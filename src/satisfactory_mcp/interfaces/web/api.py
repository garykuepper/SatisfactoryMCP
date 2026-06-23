"""The JSON endpoints. Parse the query, call a domain service, serialise.

The same shape as an MCP tool module and for the same reason: this layer decides
nothing. Every question answered here is answered by ``domain/`` -- the placement rows,
the power ledger, the node occupancy, the factory proposals -- and the only work done
in this file is turning a query string into arguments and a dataclass into JSON.

Two conventions run through the whole surface:

* **Metres, one decimal.** The save stores centimetres. Every coordinate that leaves
  this module has been divided by 100 and rounded, exactly as the text presenters do,
  because a reader who sees two units in one product will eventually mix them.
* **``?save=`` and ``?world=`` everywhere a state is read**, so a page can pin itself
  to one save while the game keeps autosaving over another.

Errors are ``{"error": "..."}`` with a 4xx, never a 200 with an empty list: a browser
that cannot tell "no nodes" from "no save" will draw an empty map and say nothing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from ... import config
from ...core.gamedata.footprint import FOUNDATION_M
from ...core.saveio import projection as proj
from ...domain.collectibles.service import collect_view
from ...domain.factories import identity as fidentity
from ...domain.spatial import elevation as spatial_elevation
from ...domain.spatial import geo
from ...domain.spatial import heightfield as spatial_heightfield
from ...domain.spatial import nodes as spatial_nodes
from ...domain.spatial import regions as spatial_regions
from ...domain.world.state import WorldState

__all__ = ["DEFAULT_MAP_BOUNDS_M", "INSPECT_NEAREST", "INSPECT_RADIUS_M", "PING_SECONDS", "router"]

#: How long a quiet SSE stream waits before sending a comment. Proxies and browsers
#: both drop a connection that has said nothing for a while, and a comment line is the
#: cheapest thing that counts as having said something.
PING_SECONDS = 15.0

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- helpers


def _m(value: float | None) -> float | None:
    """Centimetres to metres, one decimal. The unit rule, in one place."""
    return None if value is None else round(float(value) / 100.0, 1)


def _xyz(pos: Any) -> dict[str, float | None]:
    """A projection ``pos`` triple as named metre fields."""
    if not pos:
        return {"x_m": None, "y_m": None, "z_m": None}
    p = list(pos) + [None, None, None]
    return {"x_m": _m(p[0]), "y_m": _m(p[1]), "z_m": _m(p[2])}


def _yaw(value: Any) -> float | None:
    """A placement's rotation about world Z, degrees, one decimal.

    Positive turns +X towards +Y, so it is directly comparable with ``atan2(dy, dx)`` over
    two ``pos`` values -- which is how the projection's own convention was verified, and
    the one sentence a client needs to draw a rotated footprint.

    ``None``, never 0.0, when the projection carries no yaw at all: schema 12 added the
    field, and an absent one means "this projection predates it", which is a different
    claim from "this thing is axis-aligned". Both end up drawn the same way, and only one
    of them is a measurement.

    Rounded like every other number that leaves this module. 0.1 degrees swings the corner
    of an 8 m foundation by 7 mm.
    """
    if value is None:
        return None
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _fail(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _pretty_cls(cls: str | None) -> str | None:
    """An engine class id as words: ``Build_GeneratorIntegratedBiomass_C`` ->
    ``Generator Integrated Biomass``.

    The popups resolve every id they can against the docs dump; this is the fallback for
    the ids the dump has no entry for (the biomass burners, the synthetic recipe strings).
    A popup that prints a display name in one row and a raw ``Build_…_C`` in the next is
    teaching the reader two vocabularies for one object, so the fallback at least speaks
    the same language, even if it cannot know the official name.
    """
    if not cls:
        return None
    leaf = re.sub(r"^(Build|Desc|Recipe|BP)_", "", str(cls))
    leaf = re.sub(r"_C$", "", leaf)
    words = _CAMEL.sub(" ", leaf.replace("_", " ")).strip()
    return words or str(cls)


def _state(request: Request, save: str | None, world: str | None) -> WorldState:
    """The world a request is asking about. Raises whatever the loader raises."""
    return request.app.state.load_state(save, world)


def _label_json(label: spatial_regions.Label) -> dict | None:
    """A region lookup as JSON, or ``None`` for ocean and off-map.

    ``None`` rather than a nearest-land guess, which is the refusal ``label_for`` already
    makes and which this layer must not undo -- a page that printed the closest biome for
    a click in the sea would read exactly like a measurement.

    The confidence word travels with the name because the name alone cannot be trusted:
    the raster is 256 m per cell, so "Northern Forest, boundary" and "Northern Forest,
    interior" are different claims. ``certain`` is the domain's own reading of that word,
    computed here once so the page does not have to know the four codes.
    """
    if label.name is None:
        return None
    return {
        "name": label.name,
        "confidence": label.confidence,
        "accuracy_m": label.accuracy_m,
        "certain": label.certain,
        "text": label.describe(),
    }


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
        "name": building.name if building else _pretty_cls(cls),
        **_xyz(row.get("pos")),
        "recipe": recipe_id,
        # Same rule for the recipe: the row a player most wants to read must not be the
        # one row still speaking engine ids.
        "recipe_name": recipe.name if recipe else _pretty_cls(recipe_id),
        "clock": row.get("clock"),
        "paused": bool(row.get("paused", False)),
        "yaw": _yaw(row.get("yaw")),
        # Footprint is already metres; the projection's coordinates are not.
        "w_m": round(footprint.width_m, 1) if footprint else None,
        "l_m": round(footprint.depth_m, 1) if footprint else None,
    }


# --------------------------------------------------------------------- worlds


@router.get("/worlds")
def worlds() -> Any:
    """Every world the save directory holds, newest first."""
    try:
        found, unsupported = proj.list_worlds()
    except Exception as exc:
        return _fail(f"could not scan saves: {exc}", 404)
    rows = []
    for w in found:
        newest = w.newest
        rows.append(
            {
                **asdict(w),
                "mtime": newest.get("mtime_ns", 0) / 1e9,
                "newest_filename": newest.get("filename"),
                "play_duration_s": w.max_play_duration_s,
            }
        )
    return {"worlds": rows, "unsupported": list(unsupported)}


# -------------------------------------------------------------------- summary


@router.get("/summary")
def summary(request: Request, save: str | None = None, world: str | None = None) -> Any:
    try:
        st = _state(request, save, world)
    except Exception as exc:
        return _fail(f"could not read save: {exc}", 404)
    return {
        "header": st.header,
        "age_note": st.age_note,
        "power": st.power_report(),
        "progression": st.progression(),
        # Where the player last stood, so the map can draw a you-are-here. Nulls when
        # the save has no pawn, which _xyz already says honestly.
        "player": _xyz(st.player_position()),
    }


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
    the orientation trap (row 0 is the NORTH edge) in two places. ``label_for_node`` rather
    than ``label_for`` -- it prefers the hand-verified override table, so the nodes someone
    actually checked come back as ``verified`` instead of as a 256 m cell's best guess.
    ``null`` for a node the raster calls void, which is the honest answer for the handful
    that sit on islands off the grid.

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
        building = game.buildings.get(occupant) if occupant else None
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
                "occupant_name": building.name if building else _pretty_cls(occupant),
                "region": _label_json(rmap.label_for_node(n)),
            }
        )
    return {
        "nodes": out,
        "resource": resource,
        "occupied": None if save_error else sum(1 for r in out if r["occupied"]),
        "save_error": save_error,
    }


# ----------------------------------------------------------- point inspector


#: How far a click looks for known elevations, metres. The same default
#: ``describe_location`` uses, so the map and the MCP tool answer one question one way.
INSPECT_RADIUS_M = 200.0

#: How many nodes a click reports. Enough to see what a site is next to; more would make
#: the popup a second copy of the node table.
INSPECT_NEAREST = 5


def _terrain_field():
    """The extracted 1 m heightfield, or ``None`` on a machine that has none.

    A loader and only a loader, exactly like ``/api/mapimage``: the raster is derived from
    the game's cooked assets, so this repository ships none and most installs have none.
    Wrapped in a function of its own rather than called inline so a test can replace it
    with a synthetic field and get a deterministic answer without a game install.
    """
    return spatial_heightfield.load_field()


def _elevation_json(near: spatial_elevation.Elevation) -> dict:
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

    terrain = near.terrain
    terrain_note = None
    if terrain is None:
        terrain_note = (
            "the field has no data at this point -- open ocean, or a cave mouth"
            if _terrain_field() is not None
            else "no terrain field on this machine (run tools/gen_world_heightmap.py)"
        )

    return {
        "radius_m": near.radius_m,
        "terrain_m": _round(near.terrain_m),
        "terrain_source": terrain.source if terrain else None,
        "terrain_accuracy_m": terrain.accuracy_m if terrain else None,
        "terrain_water_m": _round(terrain.water_m) if terrain and terrain.submerged else None,
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


def _nearest_nodes(table, taken: dict, x: float, y: float, limit: int) -> list[dict]:
    """The closest ``limit`` nodes to a point, centimetres in, metres out."""
    ranked = sorted(
        ((geo.distance_m((x, y), (n["x"], n["y"])), n) for n in table.nodes),
        key=lambda pair: pair[0],
    )
    out = []
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


@router.get("/inspect")
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
        terrain_field=_terrain_field(),
    )
    taken = spatial_nodes.occupancy(st.projection) if st is not None else {}
    return {
        "at": {"x_m": round(x_m, 1), "y_m": round(y_m, 1)},
        "region": _label_json(rmap.label_for(x, y)),
        "elevation": _elevation_json(near),
        "nearest": _nearest_nodes(table, taken, x, y, INSPECT_NEAREST),
        "save_error": save_error,
    }


# -------------------------------------------------------------------- regions


#: Where a user-supplied map render goes. Nothing here is ever committed: the endpoint
#: below is a loader and only a loader, because a rendered map of this world is someone
#: else's artwork and the licence posture of this repository is that we ship none of it.
#: Drop your own render at ``data/local/map.png`` and, if its corners are not the standard
#: in-game map square, ``data/local/map.json`` next to it.
LOCAL_DIR_NAME = "local"
MAP_IMAGE_NAME = "map.png"
MAP_BOUNDS_NAME = "map.json"

#: And where the same render's tile pyramid goes, if the generator cut one. One 8192 px
#: sheet is 16 MB on the wire and 268 MB of RGBA in the browser however far out the view
#: is zoomed; the pyramid is that sheet at one resolution per zoom, so a whole-world
#: framing costs the 16 tiles of z2 and nothing else. ``tools/gen_map_image.py`` writes it,
#: renaming the finished tree into place so this endpoint can never serve half of one.
MAP_TILES_DIR_NAME = "tiles"

#: There is now more than one picture of this world, so a tile has to say WHICH.
#:
#: ``map`` is the game's own artwork under ``local/tiles/``, which is where it has always
#: been and where ``/api/maptiles/{z}/{x}/{y}`` still finds it. The rest are renders drawn
#: by ``tools/gen_map_renders.py`` from the 1 m heightfield -- ``terrain`` is a hypsometric
#: relief map, ``satellite`` is the same relief coloured from the game's own biome raster --
#: and they live one directory down, ``local/renders/<layer>/tiles/``, each with its own
#: sidecar naming its own depth and its own build.
#:
#: Every layer is cut on the same frame, at the same tile size, into the same
#: ``{z}/{x}_{y}.png`` grid. That is the whole design: switching layers is switching a
#: directory, so a client changes one path segment and nothing else -- not its CRS, not its
#: bounds, not its zoom range -- and a layer that has never been generated answers exactly
#: the way an absent ``map`` always has.
MAP_LAYER_DEFAULT = "map"
MAP_RENDERS_DIR_NAME = "renders"
MAP_RENDER_SIDECAR_NAME = "meta.json"
MAP_RENDER_LAYERS = ("terrain", "satellite")
MAP_LAYERS = (MAP_LAYER_DEFAULT, *MAP_RENDER_LAYERS)

#: What a pyramid looks like when the sidecar does not say: 256 px tiles, z0 (the world in
#: one tile) through z5 (the full 8192 in 32x32). Both are read back from ``_meta.tiles``
#: when it is there, so a pyramid cut at another size is served at that size rather than
#: half-refused.
MAP_TILE_PX = 256
MAP_TILE_MAX_Z = 5

#: The corners of the in-game map square, metres, game axes. The playable content is
#: strictly inside it: ``frame.content_bbox`` in ``data/satisfactory_regions.json`` --
#: the min/max over 2,688 static world objects -- is x [-2988.4, 4065.6], y [-3141.0,
#: 3042.0], so an image pinned here cannot clip anything the map draws.
DEFAULT_MAP_BOUNDS_M = {
    "x_min_m": -3247.0,
    "x_max_m": 4253.0,
    "y_min_m": -3750.0,
    "y_max_m": 3750.0,
}


def _local_dir() -> Path:
    """The user's own files, read at call time so a test can point it somewhere else."""
    return config.data_dir() / LOCAL_DIR_NAME


def _layer_dir(layer: str) -> Path | None:
    """Where one layer's ``tiles/`` tree and sidecar live, or ``None`` for an unknown layer.

    The artwork keeps the place it has always had -- ``local/tiles/`` beside ``map.png`` --
    because moving it would break every cached URL and every reader's existing tree for the
    sake of tidiness. Renders go one level down under their own name.
    """
    if layer == MAP_LAYER_DEFAULT:
        return _local_dir()
    if layer in MAP_RENDER_LAYERS:
        return _local_dir() / MAP_RENDERS_DIR_NAME / layer
    return None


def _layer_sidecar(layer: str) -> Path | None:
    """The JSON beside one layer's pyramid. ``map.json`` for the artwork, ``meta.json`` else.

    Two names rather than one because ``map.json`` is also the corners file a reader may
    have written by hand for their own ``map.png``, and it predates all of this; a render's
    sidecar is generated and never hand-edited, so it is named for what it is.
    """
    directory = _layer_dir(layer)
    if directory is None:
        return None
    return directory / (MAP_BOUNDS_NAME if layer == MAP_LAYER_DEFAULT else MAP_RENDER_SIDECAR_NAME)


def _map_bounds(layer: str = MAP_LAYER_DEFAULT) -> dict[str, float]:
    """Where to pin a layer, defaults overridden by its own sidecar if present.

    A malformed override is ignored rather than fatal: the picture is decoration, and a
    typo in an optional sidecar must not take the endpoint that serves it down with it.

    Per layer rather than once, even though every layer this repository generates is cut on
    the same square: the corners are a property of a picture, and a reader who drops in
    their own ``map.png`` pinned somewhere else must not thereby move the renders.
    """
    bounds = dict(DEFAULT_MAP_BOUNDS_M)
    path = _layer_sidecar(layer)
    if path is None:
        return bounds
    try:
        override = json.loads(path.read_text(encoding="utf-8"))
        bounds.update({k: float(override[k]) for k in DEFAULT_MAP_BOUNDS_M if k in override})
    except (OSError, ValueError, TypeError):
        return bounds
    return bounds


def _map_pyramid(layer: str = MAP_LAYER_DEFAULT) -> dict[str, Any]:
    """What a layer's sidecar says about its pyramid: tile size, depth, build.

    Same posture as ``_map_bounds``: an absent or malformed sidecar is not an error, it is
    a pyramid described by the defaults above. ``build`` is a short digest of what the
    generator recorded -- the game build it cut, and how many tiles that came to -- and is
    only ever used as a cache tag, so a sidecar that says nothing simply produces a stable
    tag for "nothing".

    The layer's name is folded into that digest, so two layers that happen to have been cut
    from one build at one tile count still get different tags. Sharing a tag between two
    pictures is how an ``immutable`` tile of one ends up cached as a tile of the other.
    """
    path = _layer_sidecar(layer)
    meta: Any = {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8")) if path is not None else {}
    except (OSError, ValueError):
        meta = {}
    tiles: Any = meta.get("_meta") if isinstance(meta, dict) else None
    tiles = tiles.get("tiles") if isinstance(tiles, dict) else None
    if not isinstance(tiles, dict):
        tiles = {}

    def _whole(key: str, default: int, floor: int) -> int:
        value = tiles.get(key)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= floor
            else default
        )

    stamp = "|".join(
        [
            layer,
            *(str(tiles.get(key)) for key in ("game_version_pinned", "count", "bytes", "max_z")),
        ]
    )
    return {
        "tile_px": _whole("tile_px", MAP_TILE_PX, 1),
        "max_z": _whole("max_z", MAP_TILE_MAX_Z, 0),
        "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
    }


def map_tile_path(
    z: int, x: int, y: int, max_z: int = MAP_TILE_MAX_Z, layer: str = MAP_LAYER_DEFAULT
) -> Path | None:
    """Where one pyramid tile lives, or ``None`` if ``(z, x, y)`` is off the pyramid.

    The only place this side writes the layout ``tiles/{z}/{x}_{y}.png`` down, and a test
    holds it against the generator's own copy so the writer and the reader cannot drift
    apart. Nothing here joins a string a caller supplied: the three coordinates arrive as
    ints -- FastAPI answers anything else with a 422 before this runs -- and are checked
    against the ``2**z`` grid of their own level before they become a filename, so no
    request can name a path outside the tree, whatever it is shaped like.

    ``layer`` is the one segment that IS a string, and it never reaches a path: it is
    looked up in ``_layer_dir``, which answers ``None`` for anything that is not one of the
    names this module wrote down. So a layer segment shaped like an escape is an unknown
    layer and nothing else -- there is no join for it to escape through.
    """
    directory = _layer_dir(layer)
    if directory is None or not 0 <= z <= max_z:
        return None
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        return None
    return directory / MAP_TILES_DIR_NAME / str(z) / f"{x}_{y}.png"


@router.get("/regions")
def regions() -> Any:
    """The biome raster: a 30x30 character grid, its legend, and each region's extent.

    No ``?save``/``?world``: this is the world's own geography, identical for every save,
    which is why it is cacheable and fetched once per page load.

    The raster comes through ``domain.spatial.regions``, which reads
    ``data/region_names.json``. Two committed files carry a 30x30 grid and they differ:
    this one is the file whose per-region bounding boxes are derived from its own grid, so
    every cell provably lies inside the box of the region it names -- which is exactly
    what the drawing client is checked against. ``satisfactory_regions.json``'s boxes come
    from a coarser 1.024 km grid and do not agree with its raster cell for cell, so
    painting from it would leave nothing to verify orientation with.

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
        """
        cx, cy = centroid
        if rmap.label_for(cx, cy).name == name:
            return [_m(cx), _m(cy)]
        ch = letters.get(name)
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


@router.api_route("/mapimage", methods=["GET", "HEAD"])
def mapimage(request: Request) -> Any:
    """A map render the *user* dropped in, if they dropped one in. Never shipped.

    HEAD is routed alongside GET on purpose: the page probes with HEAD before it builds
    an ``imageOverlay``, and FastAPI -- unlike bare Starlette -- does not add HEAD to a
    GET route by itself, so a probe would come back 405 and read as "no image".

    An absent file is the *expected* state, so the HEAD probe answers **204**, not 404:
    a 404 is logged red in every devtools console on every clean page load, which trains
    the reader to ignore console errors on this page. The GET keeps its 404 with the
    where-to-put-it message -- anything actually fetching the bytes deserves the reason.

    The corners travel with the file in ``X-Map-Bounds-M`` (``x_min,y_min,x_max,y_max``,
    metres, game axes) so the one probe the page already makes answers both questions.
    """
    path = _local_dir() / MAP_IMAGE_NAME
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        return _fail(
            f"no map image: put a map render at {path}; it is only ever read locally, "
            "never uploaded and never committed. Optionally pin its corners with "
            f"{path.with_name(MAP_BOUNDS_NAME)} "
            '{"x_min_m":…,"x_max_m":…,"y_min_m":…,"y_max_m":…}',
            404,
        )
    b = _map_bounds()
    return FileResponse(
        path,
        headers={
            "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**b),
            "Cache-Control": "no-cache",
        },
    )


#: Which tool writes which layer, so an absent pyramid can say what would fill it. One
#: sentence per layer rather than one for all of them: "run the generator" is not help when
#: there are three trees and two generators.
_LAYER_TOOLS = {
    MAP_LAYER_DEFAULT: (
        "tools/gen_map_image.py, which cuts it out of your own installed game beside map.png"
    ),
    "terrain": (
        "tools/gen_map_renders.py, which draws a hypsometric relief map of this world from "
        "the 1 m heightfield in data/local/heightmap/"
    ),
    "satellite": (
        "tools/gen_map_renders.py, which draws the same relief coloured from the game's own "
        "biome raster, from the 1 m heightfield in data/local/heightmap/"
    ),
}


def _serve_tile(request: Request, layer: str, z: int, x: int, y: int) -> Any:
    """One tile of one layer's pyramid. The whole of what both tile routes do.

    **HEAD 204 for an absent pyramid, like the image probe next door.** The page probes
    ``0/0/0`` to decide between the pyramid and the single overlay -- and now also to decide
    which layers exist at all -- and an absent optional file is the ordinary answer: a 404
    on every clean page load teaches the reader to ignore red lines in the console. GET
    keeps its 404 and names the tool that would write that particular tree.

    **Off the pyramid is 404, and cannot be anything else.** ``z``, ``x`` and ``y`` are
    typed ``int``, so a path segment that is not one never reaches this function -- FastAPI
    answers 422 -- and ``map_tile_path`` range-checks the three against the level's own
    grid before building a name. The client is bounds-clamped as well, so in practice this
    404 fires for a hand-typed URL rather than for the map.

    **Every header is that layer's own.** Depth, tile size, corners and build tag are read
    from the sidecar beside the tiles being served, because the layers are generated
    separately and by different tools: the artwork can be two levels deeper than the renders
    if it was enhanced, and a regenerated satellite must not invalidate the terrain a
    browser is holding.

    **Cached hard, and stamped with the build.** A tile is immutable for a given cut, so the
    page asks for it with ``?v=`` the build tag this endpoint hands out on the probe:
    regenerating a pyramid changes that layer's tag, which changes every URL of that layer,
    which is what makes ``immutable`` safe to send. The ETag carries the same tag for
    anything that revalidates instead.
    """
    pyramid = _map_pyramid(layer)
    path = map_tile_path(z, x, y, pyramid["max_z"], layer)
    if path is None:
        return _fail(
            f"no tile {layer}/{z}/{x}/{y}: this pyramid runs z0..z{pyramid['max_z']}, and "
            "level z is a 2**z by 2**z grid, so x and y stop there",
            404,
        )
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        return _fail(
            f"no {layer} tiles: {path.parent.parent} is written by {_LAYER_TOOLS[layer]}. "
            "Like the map image, it is only ever read locally, never uploaded and never "
            "committed.",
            404,
        )
    b = _map_bounds(layer)
    etag = f'"{pyramid["build"]}"'
    # ``immutable`` is earned by the ``?v=`` build tag and only by it: a tagged URL changes
    # whenever the pyramid is recut, so the response behind it never can. The page's probe
    # and any untagged fetch carry NO tag -- caching those hard is how a regenerated map
    # stayed invisible behind a year-old probe until the browser cache was disabled by
    # hand. Untagged answers revalidate instead, and the ETag makes that a 304, not bytes.
    versioned = "v" in request.query_params
    headers = {
        # The corners, the shape of the grid and the build, on the probe the page already
        # makes -- so the client configures its tile layer from the server rather than from
        # a second opinion about how the pyramid was cut.
        "X-Map-Bounds-M": "{x_min_m},{y_min_m},{x_max_m},{y_max_m}".format(**b),
        "X-Map-Layer": layer,
        "X-Map-Tile-Px": str(pyramid["tile_px"]),
        "X-Map-Tile-Max-Z": str(pyramid["max_z"]),
        "X-Map-Build": pyramid["build"],
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, headers=headers)


@router.api_route("/maptiles/{z}/{x}/{y}", methods=["GET", "HEAD"])
def maptiles(request: Request, z: int, x: int, y: int) -> Any:
    """The artwork pyramid, at the URL it has always had. An alias for ``map``.

    Kept because it is live: every cached tile, every bookmark and the page as it stands
    all address the base map here, and a route that moved would break all three to say the
    same thing one segment longer. So this is not a redirect and not a deprecation -- it is
    the default layer's name being optional, and it answers byte for byte and header for
    header what ``/api/maptiles/map/{z}/{x}/{y}`` answers.
    """
    return _serve_tile(request, MAP_LAYER_DEFAULT, z, x, y)


@router.api_route("/maptiles/{layer}/{z}/{x}/{y}", methods=["GET", "HEAD"])
def maptiles_layer(request: Request, layer: str, z: int, x: int, y: int) -> Any:
    """One tile of a named base layer: ``map``, ``terrain`` or ``satellite``.

    Four segments where the alias above has three, so the two routes cannot collide: a
    three-segment path has no layer to name and is the artwork by definition.

    An unknown layer is a 404 that lists the ones there are, rather than a 422 about a path
    parameter. The distinction is the reader's: asking for a layer this build does not have
    is asking for a picture that is not there, which is the same answer as asking for a tile
    of one that has not been generated -- and a page that probes for layers it might find
    deserves to be told which names exist rather than which types were expected.
    """
    if layer not in MAP_LAYERS:
        return _fail(
            f"no base layer {layer!r}: this server serves {', '.join(MAP_LAYERS)}. "
            f"{MAP_LAYER_DEFAULT} is the game's own artwork; the rest are renders drawn "
            "from your heightfield by tools/gen_map_renders.py.",
            404,
        )
    return _serve_tile(request, layer, z, x, y)


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
    Read guarded field by field, exactly as ``domain.spatial.elevation`` reads them: this
    is raw projection data and a malformed row should cost one piece, not the endpoint.

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

    raw = st.projection.get("structures") or {}
    classes = list(raw.get("classes") or ())
    rows = []
    for inst in raw.get("instances") or ():
        if not isinstance(inst, (list, tuple)) or len(inst) < 4:
            continue
        try:
            index = int(inst[0])
            x, y, z = float(inst[1]), float(inst[2]), float(inst[3])
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "cls": classes[index] if 0 <= index < len(classes) else None,
                "x_m": _m(x),
                "y_m": _m(y),
                "z_m": _m(z),
                # Optional on purpose: a row from a schema-11 projection is four columns
                # long and is still a real piece at a real place, it just has no facing.
                "yaw": _yaw(inst[4]) if len(inst) > 4 else None,
            }
        )
    return {"structures": rows, "count": len(rows), "tile_m": FOUNDATION_M}


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
        "name": building.name if building else _pretty_cls(cls),
        # From the dump's native class, not from the class id -- see LIFT_NATIVE. ``None``
        # for a class the dump has no entry for: "not a lift" would be a guess, and the
        # map draws a lift and a belt as different things.
        "lift": None if building is None else building.native == LIFT_NATIVE,
        # The tier's own throughput, 60 to 780, instead of a "Mk3" the page would have to
        # parse back out of a display name. ``null`` where the dump is silent, exactly like
        # the machine footprints next door.
        "items_per_min": (building.items_per_min or None) if building else None,
    }


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
        "name": building.name if building else _pretty_cls(cls),
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
    the legend is resolved here so the page does not have to carry it, and every field is
    read guarded so that a malformed segment costs that segment rather than the network.

    **Points are in travel order, input to output.** The save stores them output-first and
    the projection reverses them, so a client can draw direction along a run without
    knowing that. ``chain`` is the belt chain a piece belongs to -- 1,909 chains over 3,085
    pieces on the reference world -- so "the whole run" is a group-by rather than a
    geometry problem.

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

    raw = st.projection.get("belts") or {}
    classes = list(raw.get("classes") or ())
    resolved: dict[int, dict[str, Any]] = {}
    rows = []
    for seg in raw.get("segments") or ():
        if not isinstance(seg, (list, tuple)) or len(seg) < 3:
            continue
        try:
            chain = int(seg[0])
            index = int(seg[1])
        except (TypeError, ValueError):
            continue
        points = []
        for p in seg[2] or ():
            if not isinstance(p, (list, tuple)) or len(p) < 3:
                continue
            try:
                points.append([_m(float(p[0])), _m(float(p[1])), _m(float(p[2]))])
            except (TypeError, ValueError):
                continue
        if not points:
            continue  # nothing to place; a piece with no geometry is not a piece
        if index not in resolved:
            resolved[index] = _belt_class(st, classes[index] if 0 <= index < len(classes) else None)
        rows.append({"chain": chain, **resolved[index], "points_m": points})
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
        "name": building.name if building else _pretty_cls(cls),
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
    here so the page carries no legend, every field read guarded so a malformed segment costs
    that segment.

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

    raw = st.projection.get("pipes") or {}
    classes = list(raw.get("classes") or ())
    networks = list(raw.get("networks") or ())
    # Positional against ``segments``, which is what the domain service promises, so a
    # projection too old to carry the join reads as one long row of "unknown" rather than
    # as an error. Guarded by index below for the same reason.
    flows = st.pipe_flow
    resolved: dict[int, dict[str, Any]] = {}
    rows = []
    for order, seg in enumerate(raw.get("segments") or ()):
        if not isinstance(seg, (list, tuple)) or len(seg) < 3:
            continue
        try:
            net = int(seg[0])
            index = int(seg[1])
        except (TypeError, ValueError):
            continue
        points = []
        for p in seg[2] or ():
            if not isinstance(p, (list, tuple)) or len(p) < 3:
                continue
            try:
                points.append([_m(float(p[0])), _m(float(p[1])), _m(float(p[2]))])
            except (TypeError, ValueError):
                continue
        if not points:
            continue  # nothing to place; a piece with no geometry is not a piece
        if index not in resolved:
            resolved[index] = _pipe_class(st, classes[index] if 0 <= index < len(classes) else None)
        entry = networks[net] if 0 <= net < len(networks) else {}
        fluid = entry.get("fluid") if isinstance(entry, dict) else None
        flow = flows[order] if 0 <= order < len(flows) else {}
        rows.append(
            {
                "direction": flow.get("direction", "unknown"),
                "basis": flow.get("basis", "unresolved"),
                # The game's own network id, not the index into the list above: the index is
                # an encoding detail of this payload and the id is a thing in the world.
                "network": entry.get("id") if isinstance(entry, dict) else None,
                "fluid": fluid,
                # Resolved against the dump like every other class here, so a popup never has
                # to show a reader a `Desc_…_C`.
                "fluid_name": st.game.item_name(fluid) if fluid else None,
                **resolved[index],
                "points_m": points,
            }
        )
    return {
        "pipes": rows,
        "count": len(rows),
        "networks": len({r["network"] for r in rows if r["network"] is not None}),
        "directed": sum(1 for r in rows if r["direction"] != "unknown"),
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
