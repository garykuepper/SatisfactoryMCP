"""The base map: a user-supplied render, and the tile pyramids cut from it.

Everything here is a LOADER. Nothing in this repository ships a picture of this world --
a rendered map is somebody else's artwork and the licence posture is that we carry none of
it -- so every route in this file answers either "here is the file you generated" or "here
is the exact tool that would write it". Which is why the 404s are long: they are the whole
of the documentation a reader gets at the moment they need it.

Three layers, one grid. ``map`` is the game's own artwork under ``local/tiles/``; ``terrain``
and ``satellite`` are renders drawn from the 1 m heightfield and live under
``local/renders/<layer>/``. Every one of them is cut on the same frame at the same tile size
into the same ``{z}/{x}_{y}.png``, so switching layers is switching a directory and a client
changes one path segment and nothing else.

The names and the layout of a pyramid are ``core.gameassets.pyramid``'s, imported rather
than retyped: the tool that writes the tree and the endpoint that serves it hold ONE opinion
about where a tile lives. What this side owns is the bounds check, because only a server has
requests to refuse.

WARNING: the function names are the operation_ids -- rename one and the committed schema
churns. The three routes here serve GET and HEAD from a single handler and therefore carry
EXPLICIT ids (see ``OPERATION_MAPIMAGE`` below); those ids move with their decorators.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from .... import config
from ....core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    tile_relpath,
)
from ..serial import _fail

__all__ = ["DEFAULT_MAP_BOUNDS_M", "router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


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
#:
#: Imported from the cutter rather than typed again, here and for the three below. These
#: four names and the ``{z}/{x}_{y}.png`` layout are ONE fact about a directory on disk,
#: shared by the tool that writes it and the endpoint that serves it, and a second copy is
#: a second opinion waiting to happen -- which is why there used to be a pair of tests
#: asserting the two agreed. The ``MAP_`` aliases stay, because the rest of this module
#: reads better with the prefix; what is gone is the second DEFINITION.
MAP_TILES_DIR_NAME = TILES_DIR_NAME

#: ...and the same tile GRID at twice the pixels, for a display whose device pixel ratio is
#: above one. Level z of ``tiles@2x/`` covers the identical squares of the world that level
#: z of ``tiles/`` does -- 2**z of them a side -- and each square is 512 px instead of 256,
#: so a client asks for the same ``{z}/{x}/{y}`` it always did and draws twice the pixels
#: into the same CSS box. That is the whole of the @2x design on this side: one query
#: parameter chooses a directory, and every other answer this endpoint gives is unchanged.
#:
#: One level shallower than the 1x tree by arithmetic rather than by choice, since
#: ``512 * 2**z`` runs out of sheet before ``256 * 2**z`` does -- so the probe advertises
#: the two depths separately and a client past the @2x top asks for 1x tiles again.
MAP_TILES_2X_DIR_NAME = TILES_2X_DIR_NAME
MAP_TILE_2X_PX = PYRAMID_TILE_2X_PX

#: The query parameter that picks between them, and what it takes: the tile size the client
#: wants in pixels. A number rather than a flag because it says what it means and because a
#: third density would be one more value rather than one more spelling; anything this
#: server has no tree for falls back to the 1x tile, which every client can draw.
MAP_TILE_PX_PARAM = "px"

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
MAP_TILE_PX = PYRAMID_TILE_PX
MAP_TILE_MAX_Z = 5

#: The corners of the in-game map square, metres, game axes. The playable content is
#: strictly inside it: ``geo.CONTENT_BBOX`` -- the min/max over 2,687 static world objects
#: -- is x [-2988.4, 4065.6], y [-3141.0, 3042.0], so an image pinned here cannot clip
#: anything the map draws. Also the frame the map-area raster is pinned on, re-measured on
#: every ``tools/gen_region_names.py`` run.
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
    block: Any = meta.get("_meta") if isinstance(meta, dict) else None
    block = block if isinstance(block, dict) else {}
    tiles = block.get("tiles") if isinstance(block.get("tiles"), dict) else {}
    dense = block.get("tiles_2x") if isinstance(block.get("tiles_2x"), dict) else None

    def _whole(source: dict, key: str, default: int, floor: int) -> int:
        value = source.get(key)
        return (
            value
            if isinstance(value, int) and not isinstance(value, bool) and value >= floor
            else default
        )

    # The @2x tree's own numbers ride in the SAME digest, so recutting one and not the other
    # still changes every URL of that layer. Two trees of one picture that could disagree
    # about which build they came from is exactly the state an ``immutable`` tile must not
    # be served in.
    stamp = "|".join(
        [
            layer,
            *(str(tiles.get(key)) for key in ("game_version_pinned", "count", "bytes", "max_z")),
            *(str((dense or {}).get(key)) for key in ("count", "bytes", "max_z")),
        ]
    )
    return {
        "tile_px": _whole(tiles, "tile_px", MAP_TILE_PX, 1),
        "max_z": _whole(tiles, "max_z", MAP_TILE_MAX_Z, 0),
        "tile_2x_px": _whole(dense, "tile_px", MAP_TILE_2X_PX, 1) if dense else None,
        "max_2x_z": _whole(dense, "max_z", 0, 0) if dense else None,
        "build": hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12],
    }


def map_tile_path(
    z: int,
    x: int,
    y: int,
    max_z: int = MAP_TILE_MAX_Z,
    layer: str = MAP_LAYER_DEFAULT,
    tree: str = MAP_TILES_DIR_NAME,
) -> Path | None:
    """Where one pyramid tile lives, or ``None`` if ``(z, x, y)`` is off the pyramid.

    The layout itself is ``pyramid.tile_relpath``, the cutter's own function, so the
    writer and the reader cannot hold two opinions about where a tile lives. What is this
    side's job is the BOUNDS CHECK below, because only a server has requests to refuse.
    Nothing here joins a string a caller supplied: the three coordinates arrive as
    ints -- FastAPI answers anything else with a 422 before this runs -- and are checked
    against the ``2**z`` grid of their own level before they become a filename, so no
    request can name a path outside the tree, whatever it is shaped like.

    ``layer`` is the one segment that IS a string, and it never reaches a path: it is
    looked up in ``_layer_dir``, which answers ``None`` for anything that is not one of the
    names this module wrote down. So a layer segment shaped like an escape is an unknown
    layer and nothing else -- there is no join for it to escape through. ``tree`` is the
    same kind of thing one step further in: it is chosen by ``_tile_tree`` from the two
    names written down above and is never a string a request supplied.
    """
    directory = _layer_dir(layer)
    if directory is None or tree not in (MAP_TILES_DIR_NAME, MAP_TILES_2X_DIR_NAME):
        return None
    if not 0 <= z <= max_z:
        return None
    span = 1 << z
    if not (0 <= x < span and 0 <= y < span):
        return None
    return directory / tree / tile_relpath(z, x, y)


def _tile_tree(request: Request, pyramid: dict[str, Any]) -> tuple[str, int]:
    """Which of a layer's two trees this request asked for, and how deep that one goes.

    The rule is deliberately forgiving in one direction only. A client that asks for a
    density this layer has gets it; a client that asks for one it does not -- an artwork
    pyramid cut by a tool that writes no @2x tree, a layer generated before this existed,
    a hand-typed number -- gets the 1x tile, which every client can draw at any density. The
    other direction has no fallback to make: a client that asks for nothing wants 256.
    """
    if pyramid["max_2x_z"] is None:
        return MAP_TILES_DIR_NAME, pyramid["max_z"]
    asked = request.query_params.get(MAP_TILE_PX_PARAM)
    if asked is not None and asked.isdigit() and int(asked) == pyramid["tile_2x_px"]:
        return MAP_TILES_2X_DIR_NAME, pyramid["max_2x_z"]
    return MAP_TILES_DIR_NAME, pyramid["max_z"]


# ------------------------------------------------------------------- mapimage


#: Explicit, on the three routes that serve GET and HEAD from one handler, and not for
#: tidiness. FastAPI derives an operation id per operation and walks ``route.methods``, which
#: is a SET -- so which of the two methods names the shared id is decided by string hash
#: order and differs from one interpreter run to the next. That is invisible in the server
#: and loud in the repository: ``npm run typegen`` writes ``operations["..._get"]`` or
#: ``operations["..._head"]`` at random, and the committed schema, whose whole claim is that
#: "a diff here is the server's surface changing", grows a diff that is nothing of the kind.
#: Naming them fixes the id to one string that is true of both methods.
OPERATION_MAPIMAGE = "mapimage"
OPERATION_MAPTILES = "maptiles"
OPERATION_MAPTILES_LAYER = "maptiles_layer"


@router.api_route("/mapimage", methods=["GET", "HEAD"], operation_id=OPERATION_MAPIMAGE)
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


# ------------------------------------------------------------------- maptiles


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

    **And ``?px=`` picks the density.** A layer may hold two trees of the same grid -- 256 px
    tiles and 512 px tiles over the identical squares of the world -- so a hi-DPI client asks
    for the same ``{z}/{x}/{y}`` and names the tile size it wants. The @2x tree is one level
    shallower, which is why the depth in the headers is the depth of the tree actually being
    served and both depths are advertised.
    """
    pyramid = _map_pyramid(layer)
    tree, depth = _tile_tree(request, pyramid)
    path = map_tile_path(z, x, y, depth, layer, tree)
    if path is None:
        return _fail(
            f"no tile {layer}/{z}/{x}/{y}: this pyramid runs z0..z{depth}, and level z is a "
            "2**z by 2**z grid, so x and y stop there",
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
        # The denser tree, when this layer has one, on the same probe: a client that is
        # going to ask for @2x tiles has to know both that they exist and how deep they go
        # BEFORE it builds its layer, and this is the request it was already making.
        # Absent, not zero, when there is no such tree -- the same way an absent pyramid is
        # a 204 rather than a depth of -1.
        **(
            {
                "X-Map-Tile-2x-Px": str(pyramid["tile_2x_px"]),
                "X-Map-Tile-2x-Max-Z": str(pyramid["max_2x_z"]),
            }
            if pyramid["max_2x_z"] is not None
            else {}
        ),
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, headers=headers)


@router.api_route("/maptiles/{z}/{x}/{y}", methods=["GET", "HEAD"], operation_id=OPERATION_MAPTILES)
def maptiles(request: Request, z: int, x: int, y: int) -> Any:
    """The artwork pyramid, at the URL it has always had. An alias for ``map``.

    Kept because it is live: every cached tile, every bookmark and the page as it stands
    all address the base map here, and a route that moved would break all three to say the
    same thing one segment longer. So this is not a redirect and not a deprecation -- it is
    the default layer's name being optional, and it answers byte for byte and header for
    header what ``/api/maptiles/map/{z}/{x}/{y}`` answers.
    """
    return _serve_tile(request, MAP_LAYER_DEFAULT, z, x, y)


@router.api_route(
    "/maptiles/{layer}/{z}/{x}/{y}", methods=["GET", "HEAD"], operation_id=OPERATION_MAPTILES_LAYER
)
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
