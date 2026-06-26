"""The tile pyramid: one sheet at one resolution per zoom, and renamed into place whole.

One 8192 px sheet is the wrong thing to hand a browser -- 16 MB that decodes to 268 MB of
RGBA whatever the view is -- so every base layer this project draws is cut into
``{z}/{x}_{y}.png``, one level per zoom, and the page fetches the pixels it can actually
show. Three pyramids are cut that way now (the game's own artwork and both of
``tools/gen_map_renders.py``'s layers), which is why the cutting is here rather than in the
generator that happened to need it first: the layout, the level arithmetic and the staged
rename are one statement about how a tile tree is shaped, and the endpoint that serves it
reads that shape back out of the sidecar.

**Every level is cut from the sheet, not from the level above it.** Level ``z`` is one
Lanczos downscale of the whole sheet, sliced up -- so no level accumulates the softening of
five successive halvings. The levels are cheap: level ``z`` is a quarter of level ``z+1``,
so everything under the top adds a third again to the top's own bytes.

**And a pyramid is only ever *renamed* into place**, never written where it is served. A
reader meets a whole tree or no tree: an interrupted run leaves a staging directory that
nothing serves and the next run deletes, rather than a ``tiles/`` missing the levels it had
not reached. It is the same rule :func:`~.provenance.install_directory` applies to a
sidecar and its rasters, and it takes the same two suffixes from there so the project has
one spelling of "being written" and one of "about to be deleted".

Pillow is a parameter here, as it is everywhere in this package: ``image_mod`` and the
sheet arrive from the caller, so nothing is imported and the suite drives the cutting with
a stand-in that has ``width``, ``resize`` and ``crop`` and nothing else.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .provenance import RETIRED_SUFFIX, STAGING_SUFFIX

#: The directory a pyramid lives in, and the square a browser fetches. Deliberately not
#: called ``TILE_PX``: to ``tools/gen_map_image.py`` a "tile" is one of the four 4096 px
#: slices the game ships, and the two meanings must not collide in a file that holds both.
TILES_DIR_NAME = "tiles"
PYRAMID_TILE_PX = 256

#: The staging and retirement names beside it, off the suffixes ``provenance`` already
#: names. A pyramid is renamed into place, so an interrupted run leaves ``tiles.incoming``
#: -- which nothing serves and the next run deletes -- rather than a ``tiles/`` tree that is
#: missing the levels the run had not got to yet.
TILES_STAGING = TILES_DIR_NAME + STAGING_SUFFIX
TILES_RETIRED = TILES_DIR_NAME + RETIRED_SUFFIX

#: What a level says it was cut from when the caller does not say: the artwork tool's own
#: answer, because it was the first pyramid. ``tools/gen_map_renders.py`` passes its own,
#: since the same cutter now serves three pyramids and a level record that named the
#: artwork under a hillshade would be the one part of the sidecar a reader could not trust.
DEFAULT_LEVEL_SOURCE = "the game's own 8192 px artwork, Lanczos"

#: How much an upscaled top level multiplies the sheet by when the caller does not say.
#: Four, which is what every upscaler this project has run does; the caller that actually
#: runs one passes its own scale rather than inheriting this.
DEFAULT_UPSCALE = 4


class PyramidError(Exception):
    """A pyramid cannot be cut, or must not be installed.

    Raised rather than exited on, for the same reason ``provenance.InstallNotFound`` is:
    nothing in ``core`` decides that a process should stop. Each of these states a fact --
    this sheet does not divide, that tree does not match its own count -- and the generator
    that has a command line is the one that turns a fact into a message and a return code.
    """


def pyramid_top_z(sheet_px: int, tile_px: int = PYRAMID_TILE_PX) -> int:
    """The deepest level of a pyramid over a ``sheet_px`` square: 8192 -> 5.

    Level z holds ``2**z`` tiles a side, so level ``top`` is the sheet at its own
    resolution. Derived rather than typed in, because ``--size`` can halve the sheet and a
    pyramid one level too deep is a level of tiles upscaled from nothing.
    """
    levels = sheet_px // tile_px
    if levels < 1 or levels & (levels - 1):
        raise PyramidError(
            f"a {sheet_px} px sheet is not a power-of-two multiple of {tile_px} px tiles, "
            "so no pyramid divides it evenly"
        )
    return levels.bit_length() - 1


def enhanced_top_z(
    sheet_px: int, scale: int = DEFAULT_UPSCALE, tile_px: int = PYRAMID_TILE_PX
) -> int:
    """The deepest level once the sheet has been upscaled ``scale`` times: 8192, 4x -> 7.

    Derived from ``pyramid_top_z`` rather than typed in, so the two cannot disagree about
    how many levels a 4x upscale is worth -- it is exactly log2(scale) of them, and a scale
    that is not a power of two would not divide the tile grid at all.
    """
    if scale < 1 or scale & (scale - 1):
        raise PyramidError(f"an upscale of {scale}x is not a power of two, so it adds no levels")
    return pyramid_top_z(sheet_px, tile_px) + (scale.bit_length() - 1)


def tile_relpath(z: int, x: int, y: int) -> str:
    """``{z}/{x}_{y}.png`` -- the one place the layout is written down.

    The web API has the same function, and a test asserts the two agree: the tool that
    writes the tree and the endpoint that serves it must not hold two opinions about
    where a tile lives.
    """
    return f"{z}/{x}_{y}.png"


def cut_square(piece, dest: Path, z: int, ox: int, oy: int, tile_px: int) -> int:
    """Slice one square image into ``dest/{z}/{x}_{y}.png``, starting at tile ``(ox, oy)``.

    The one place a level's pixels become files, whether the square is a whole downscale of
    the sheet or one enhanced core out of sixty-four. Returns the bytes written, which is
    what the caller sums into the level record a reader checks the tree against.

    Square, so ``width`` is asked for twice rather than ``height`` once: every image cut
    here is one, and asking for only the attribute that is actually needed keeps the
    stand-in a test can pass in down to the two methods that are really used.
    """
    (dest / str(z)).mkdir(parents=True, exist_ok=True)
    written = 0
    for y in range(piece.width // tile_px):
        for x in range(piece.width // tile_px):
            box = (x * tile_px, y * tile_px, (x + 1) * tile_px, (y + 1) * tile_px)
            path = dest / tile_relpath(z, ox + x, oy + y)
            piece.crop(box).save(path, format="PNG", optimize=True)
            written += path.stat().st_size
    return written


def cut_pyramid(
    sheet,
    image_mod,
    dest: Path,
    tile_px: int = PYRAMID_TILE_PX,
    source: str = DEFAULT_LEVEL_SOURCE,
) -> dict:
    """Cut ``sheet`` into ``dest/{z}/{x}_{y}.png`` for every level, and say what it wrote.

    Each level below the top is one Lanczos downscale of the whole sheet, sliced up --
    downscaling the sheet once per level rather than each tile from its four children
    keeps every level a resampling of the original pixels, so no level accumulates the
    softening of five successive halvings.

    The levels are cheap: level z is a quarter of level z+1, so everything under the top
    adds a third again to the top's own bytes.

    ``--enhance`` adds levels ABOVE this top out of upscaled pixels; it does not change
    these. z0..z5 are downscales of the game's own artwork here whether that stage runs or
    not, because a level that has real pixels behind it has no business being drawn from
    invented ones.
    """
    top = pyramid_top_z(sheet.width, tile_px)
    levels = []
    for z in range(top + 1):
        side = tile_px << z
        level = sheet if side == sheet.width else sheet.resize((side, side), image_mod.LANCZOS)
        written = cut_square(level, dest, z, 0, 0, tile_px)
        levels.append(
            {
                "z": z,
                "sheet_px": side,
                "tiles": (1 << z) ** 2,
                "bytes": written,
                "from": source,
            }
        )
        print(f"  pyramid z{z}: {side}x{side}, {(1 << z) ** 2} tiles, {written / 1e6:.2f} MB")
    return {
        "layout": f"{TILES_DIR_NAME}/{{z}}/{{x}}_{{y}}.png",
        "tile_px": tile_px,
        "max_z": top,
        "enhanced": False,
        "count": sum(level["tiles"] for level in levels),
        "bytes": sum(level["bytes"] for level in levels),
        "levels": levels,
        "role": (
            "the same sheet at one resolution per zoom, so the page fetches the pixels it "
            "can actually show. map.png is still written beside it: it is what a page "
            "falls back to when there is no pyramid, and the one file a reader can open."
        ),
        "completeness": (
            "written to " + TILES_STAGING + " and renamed into place, so this directory is "
            "either a whole pyramid or absent -- an interrupted run cannot leave a partial "
            "one for a reader to trust. count is what a doubter can check it against."
        ),
    }


def merge_enhanced(stats: dict, extra: dict) -> dict:
    """Fold the enhanced levels into the pyramid record the sidecar carries.

    ``count`` and ``bytes`` are re-summed from the levels rather than added to, so the one
    number ``install_pyramid`` checks the tree against stays derived from the same list a
    reader would count themselves.
    """
    levels = stats["levels"] + extra["levels"]
    return {
        **stats,
        "max_z": max(level["z"] for level in levels),
        "enhanced": True,
        "count": sum(level["tiles"] for level in levels),
        "bytes": sum(level["bytes"] for level in levels),
        "levels": levels,
        "enhancement": extra["enhancement"],
    }


def install_pyramid(
    sheet,
    image_mod,
    out_dir: Path,
    tile_px: int = PYRAMID_TILE_PX,
    enhance=None,
    source: str = DEFAULT_LEVEL_SOURCE,
) -> dict:
    """Cut the pyramid into staging, then rename it over any older one.

    The rename is the whole point: ``tiles/`` appears complete or not at all. A previous
    tree is moved aside first (Windows will not rename onto a non-empty directory) and
    deleted afterwards, and any leftovers from a run that died mid-swap are cleared first
    rather than merged into.

    ``enhance`` -- when ``--enhance`` was asked for -- is called with the staging directory
    and adds the upscaled levels to it before the swap. It runs INSIDE the staging window
    on purpose: the GPU stage is the part most likely to fail, and a failure there must
    leave the pyramid that is already installed untouched rather than half-replaced.
    """
    staging = out_dir / TILES_STAGING
    retired = out_dir / TILES_RETIRED
    final = out_dir / TILES_DIR_NAME
    for stale in (staging, retired):
        if stale.exists():
            shutil.rmtree(stale)
    staging.mkdir(parents=True)
    stats = cut_pyramid(sheet, image_mod, staging, tile_px, source)
    if enhance is not None:
        stats = merge_enhanced(stats, enhance(staging))

    on_disk = sum(1 for _ in staging.rglob("*.png"))
    if on_disk != stats["count"]:
        raise PyramidError(
            f"the pyramid was cut with {stats['count']} tiles but {on_disk} PNGs are in "
            f"{staging} -- refusing to install a tree that does not match its own count"
        )
    if final.exists():
        final.rename(retired)
    staging.rename(final)
    if retired.exists():
        shutil.rmtree(retired)
    return stats
