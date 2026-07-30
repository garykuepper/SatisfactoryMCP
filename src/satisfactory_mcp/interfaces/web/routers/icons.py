"""``/api/icons/{desc}``: one item's picture, out of the reader's own game.

A LOADER and only a loader, on ``routers/tiles.py``'s terms exactly and for its reason: an
item icon is the game's artwork, this repository ships none of it, and every answer here is
either "here is the file you generated" or "here is the exact tool that would write it".
Which is why the 404 is long -- it is the whole of the documentation a reader gets at the
moment they need it.

Infrastructure rather than a feature. There is nothing item-shaped about this route: it
serves a PNG named by a descriptor class, and the classes come from whichever payload the
client is already holding -- a crate's contents, a container's, a plan's bill of materials,
a recipe's inputs. Every popup on this surface upgrades the day the page starts asking.

WARNING: the function names are the operation_ids -- rename one and the committed schema
churns. This route serves GET and HEAD from a single handler and therefore carries an
EXPLICIT id, for the reason ``routers/tiles.py`` gives at ``OPERATION_MAPIMAGE``: FastAPI
walks ``route.methods``, which is a SET, so an implicit id is decided by string hash order
and differs from one interpreter run to the next.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from .... import config
from ..serial import _fail

__all__ = ["ICONS_DIR_NAME", "router"]

router = APIRouter(prefix="/api")


# ------------------------------------------------------------- where it lives


#: Where ``tools/gen_item_icons.py`` writes, under the same ``data/local/`` the map render
#: and the tile pyramids live in. Nothing here is ever committed.
LOCAL_DIR_NAME = "local"
ICONS_DIR_NAME = "icons"
ICONS_MANIFEST_NAME = "manifest.json"

#: The tool that fills the directory, named in the 404 so an empty one explains itself.
ICONS_TOOL = (
    "tools/gen_item_icons.py, which decodes them out of your own installed game's container"
)

#: What a ``{desc}`` segment may be, and the reason it is a pattern rather than a sanitiser.
#:
#: A descriptor class is ``Desc_IronPlate_C``, ``BP_EquipmentDescriptorNobeliskDetonator_C``,
#: ``Build_StorageContainerMk1_C`` -- letters, digits and underscores, and every one of the
#: 747 classes the generator writes matches this. So the segment is VALIDATED and then used,
#: rather than joined and then cleaned: a name that is not this shape never becomes a path at
#: all, which makes traversal impossible by construction instead of by a list of things to
#: strip. There is no ``..`` to reject, no separator to normalise and no encoding to unwrap,
#: because none of them can match in the first place.
#:
#: The same posture ``routers/tiles.py`` takes with ``layer``: the one string segment there
#: is looked up in a written-down set and never reaches a join either.
DESC_RE = re.compile(r"\A[A-Za-z0-9_]{1,128}\Z")


def _icons_dir() -> Path:
    """The generated directory, read at call time so a test can point it somewhere else."""
    return config.data_dir() / LOCAL_DIR_NAME / ICONS_DIR_NAME


def _icons_build() -> str:
    """A short digest of what the generator recorded, used only ever as a cache tag.

    The manifest's own provenance -- the build it was cut from, how many icons that came to,
    how many bytes and at what size -- folded into twelve hex characters. Same shape and
    same job as ``tiles._map_pyramid``'s ``build``: it changes when the directory is
    regenerated and at no other time, which is what makes ``immutable`` safe to send behind
    a ``?v=`` carrying it.

    An absent or malformed manifest is not an error, it is a stable tag for "nothing" -- the
    tile sidecar's posture, because a picture is decoration and a typo in an optional file
    must not take down the endpoint that serves it.
    """
    try:
        meta = json.loads((_icons_dir() / ICONS_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    block = meta.get("_meta") if isinstance(meta, dict) else None
    block = block if isinstance(block, dict) else {}
    source = block.get("source") if isinstance(block.get("source"), dict) else {}
    counts = block.get("counts") if isinstance(block.get("counts"), dict) else {}
    stamp = "|".join(
        [
            str(source.get("game_version_pinned")),
            *(str(counts.get(key)) for key in ("icons_written", "bytes", "written_px")),
        ]
    )
    return hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:12]


def icon_path(desc: str) -> Path | None:
    """Where one item's PNG lives, or ``None`` if ``desc`` is not a descriptor class name.

    The whole of this module's path handling, and it is a refusal rather than a repair. See
    :data:`DESC_RE`: the segment either is ``[A-Za-z0-9_]{1,128}`` -- in which case it cannot
    contain a separator, a dot, a drive letter or an escape, and joining it is safe by
    construction -- or it is not a class name at all and no path is built from it.
    """
    return _icons_dir() / f"{desc}.png" if DESC_RE.match(desc) else None


# -------------------------------------------------------------------- serving


#: Explicit, because this route serves GET and HEAD from one handler. See the module
#: docstring and ``tiles.OPERATION_MAPIMAGE``: an implicit id would be ``..._get`` or
#: ``..._head`` at random per interpreter run, and ``api-schema.d.ts`` would churn for it.
OPERATION_ICON = "icon"


@router.api_route("/icons/{desc}", methods=["GET", "HEAD"], operation_id=OPERATION_ICON)
def icon(request: Request, desc: str) -> Any:
    """One item descriptor's icon as a PNG, from the reader's own install.

    **Absent is the ordinary state, so HEAD answers 204 rather than 404.** A page decides
    whether to draw icons at all by probing one, and a clean load that logs a red 404 in
    every devtools console trains the reader to ignore console errors on this page --
    ``/api/mapimage`` settled this and this follows it. The GET keeps its 404 and names the
    generator, because anything actually fetching bytes deserves the reason.

    **Two different absences, told apart, because they need different sentences.** A
    directory that was never generated is answered with the command that would fill it. A
    directory that exists without this particular class is a different fact: 3 of the game's
    750 item classes genuinely have no picture anywhere -- their docs entries name no icon
    at all, and the manifest marks each one ``no-icon-in-docs`` -- so "no icon for
    ``Desc_PillarTop_C``" is a complete answer rather than a missing file, and it says so.

    **Cached hard, and stamped with the build**, on the tile route's terms exactly: the icon
    for a class is immutable for a given cut, so a client asks with ``?v=`` the tag this
    endpoint hands out in ``X-Icons-Build``, and a tagged URL changes whenever the directory
    is regenerated -- which is what earns ``immutable``. An UNTAGGED request revalidates
    instead, which is the half that matters: caching those hard is how a regenerated
    directory stayed invisible behind a year-old probe until somebody disabled the browser
    cache by hand. The ETag makes a revalidation a 304 rather than 50 KB.

    **The name is validated, never repaired.** ``desc`` is a descriptor class, so it is
    ``[A-Za-z0-9_]`` and nothing else; anything else is refused before a path exists. There
    is no traversal to defend against because there is no join for one to escape through.
    """
    path = icon_path(desc)
    if path is None:
        return _fail(
            f"{desc!r} is not a descriptor class name: these are named exactly as the save "
            "and the docs dump name them, e.g. Desc_IronPlate_C or "
            "Build_StorageContainerMk1_C, and nothing else is looked up",
            404,
        )
    if not path.is_file():
        if request.method == "HEAD":
            return Response(status_code=204)
        directory = _icons_dir()
        if not (directory / ICONS_MANIFEST_NAME).is_file():
            return _fail(
                f"no icons: {directory} is written by {ICONS_TOOL}. Like the map image, it "
                "is only ever read locally, never uploaded and never committed.",
                404,
            )
        return _fail(
            f"no icon for {desc}: the directory was generated and holds no such file. Either "
            "the class is one of the few the game ships no picture for -- manifest.json "
            "lists every one under 'unresolved', with the reason -- or it is not an item "
            "class at all.",
            404,
        )
    build = _icons_build()
    etag = f'"{build}"'
    versioned = "v" in request.query_params
    headers = {
        # On the probe the page already makes, so a client configures its own cache-busting
        # from the server rather than from a second opinion about how the icons were cut.
        "X-Icons-Build": build,
        "Cache-Control": "public, max-age=31536000, immutable" if versioned else "no-cache",
        "ETag": etag,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/png", headers=headers)
