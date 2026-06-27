"""The tile pyramid: one sheet at one resolution per zoom, and renamed into place whole.

One 16384 px sheet is the wrong thing to hand a browser -- 60 MB that decodes to a
gigabyte of RGBA whatever the view is -- so every base layer this project draws is cut into
``{z}/{x}_{y}.png``, one level per zoom, and the page fetches the pixels it can actually
show. Three pyramids are cut that way now (the game's own artwork and both of
``tools/gen_map_renders.py``'s layers), which is why the cutting is here rather than in the
generator that happened to need it first: the layout, the level arithmetic and the staged
rename are one statement about how a tile tree is shaped, and the endpoint that serves it
reads that shape back out of the sidecar.

**Every level is cut from the sheet, not from the level above it.** Level ``z`` is one
Lanczos downscale of the whole sheet, sliced up -- so no level accumulates the softening of
six successive halvings. The levels are cheap: level ``z`` is a quarter of level ``z+1``,
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

Two tile sizes, and the second one is the same grid
---------------------------------------------------
``tiles@2x/`` is the identical tile GRID at twice the pixels: level ``z`` is still
``2**z`` tiles a side covering the identical squares of the world, but each tile is 512 px
rather than 256. That is what a hi-DPI display wants -- the client asks for the same
``{z}/{x}/{y}`` it always did and draws the answer into the same CSS box, and the box now
holds twice the pixels in each direction. Because a 512 px tile eats a level of depth
(``512 * 2**z`` runs out of sheet one level before ``256 * 2**z`` does), an @2x tree is
always exactly one level shallower than the 1x tree cut from the same sheet, and past that
level the two carry identical information. Nothing about the two is a different pyramid:
same cutter, same rename, same record.

Cutting in parallel, and why it is allowed to be
------------------------------------------------
The expensive half of cutting is not the resampling, it is PNG compression: 5,461 tiles at
optimize=True is minutes of one core doing nothing but deflate. **The resampling stays
serial and single-threaded** -- level ``z`` is one Lanczos downscale of the whole sheet, in
the parent, exactly as before -- and only the per-tile encode is spread over processes.
The level's pixels go into one ``shared_memory`` block that every worker maps read-only, so
a 16384 px level is published once rather than copied thirty-two times, and each worker is
handed a row of tiles to crop out of it and write.

That split is what makes the parallel path **byte-identical** to the serial one rather than
merely equivalent: no worker resamples anything, so no worker can disagree about a filter
tap at a strip boundary. Both paths hand Pillow the same RGB pixels with the same empty
``info`` and the same encoder options, and ``tools/gen_map_renders.py --check-parallel``
cuts one level both ways and compares the SHA-256 of every tile.
"""

from __future__ import annotations

import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .provenance import RETIRED_SUFFIX, STAGING_SUFFIX

#: The directory a pyramid lives in, and the square a browser fetches. Deliberately not
#: called ``TILE_PX``: to ``tools/gen_map_image.py`` a "tile" is one of the four 4096 px
#: slices the game ships, and the two meanings must not collide in a file that holds both.
TILES_DIR_NAME = "tiles"
PYRAMID_TILE_PX = 256

#: And the same grid at twice the density, for a display whose device pixel ratio is
#: greater than one. A separate directory rather than a suffix on the filename because it
#: is a whole tree with its own depth, and the endpoint picks between the two by asking for
#: a directory exactly the way it picks between layers.
TILES_2X_DIR_NAME = "tiles@2x"
PYRAMID_TILE_2X_PX = PYRAMID_TILE_PX * 2

#: The staging and retirement names beside it, off the suffixes ``provenance`` already
#: names. A pyramid is renamed into place, so an interrupted run leaves ``tiles.incoming``
#: -- which nothing serves and the next run deletes -- rather than a ``tiles/`` tree that is
#: missing the levels the run had not got to yet.
TILES_STAGING = TILES_DIR_NAME + STAGING_SUFFIX
TILES_RETIRED = TILES_DIR_NAME + RETIRED_SUFFIX

#: One parallel task is one ROW of tiles, so the task count is the level's own side in
#: tiles and the shared block is read in long contiguous runs. And a level with fewer tiles
#: than this is cut serially however many workers were asked for: publishing a shared block
#: and waking a pool to write four PNGs costs more than writing them.
PARALLEL_MIN_TILES = 16

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


def _encode_tile_row(job: tuple[str, str, int, int, int, str, int]) -> int:
    """One row of tiles, cropped out of a shared block and deflated. Runs in a child.

    Top level and argument-shaped rather than a closure because Windows spawns its workers:
    everything a task needs travels as picklable data, and the block of pixels travels as a
    NAME rather than as bytes -- ``shared_memory`` is what stops a 16384 px level being
    copied once per worker.

    A row of tiles is one contiguous run of that block, so the child rebuilds exactly that
    strip and then calls the same ``crop(...).save(...)`` the serial path calls. That is
    where the byte-identity comes from: same pixels, same Pillow image, same encoder
    options, and no resampling anywhere in a worker.

    Pillow is imported HERE, inside the child, which is the rule the rest of this package
    follows: the ``gen`` extra stays optional at import time, so a machine without Pillow
    still imports this module and runs the suite. It just cannot cut.
    """
    from multiprocessing.shared_memory import SharedMemory

    from PIL import Image

    name, mode, width, stride, z, row, dest, tile_px = job
    block = SharedMemory(name=name)
    try:
        start, length = row * tile_px * stride, tile_px * stride
        strip = Image.frombytes(mode, (width, tile_px), bytes(block.buf[start : start + length]))
        written = 0
        for x in range(width // tile_px):
            path = Path(dest) / tile_relpath(z, x, row)
            box = (x * tile_px, 0, (x + 1) * tile_px, tile_px)
            strip.crop(box).save(path, format="PNG", optimize=True)
            written += path.stat().st_size
        return written
    finally:
        block.close()


def cut_square_parallel(piece, dest: Path, z: int, tile_px: int, pool) -> int:
    """``cut_square`` at ``(0, 0)`` with the deflating spread over a process pool.

    The pixels are published once into a ``shared_memory`` block and every worker maps it;
    each task is one row of tiles. Nothing here resamples, filters or moves a coordinate --
    the level arrives already resized, and a worker only decides where one rectangle of it
    starts -- which is why this is byte-identical to the serial path rather than merely
    close to it, and why proving that is one hash comparison and not a tolerance.

    The offset arguments ``cut_square`` takes are deliberately absent: the only caller that
    cuts at an offset is the enhancement stage, which hands over one upscaled core at a time
    and is GPU-bound rather than deflate-bound.
    """
    from multiprocessing.shared_memory import SharedMemory

    (dest / str(z)).mkdir(parents=True, exist_ok=True)
    raw = piece.tobytes()
    stride = len(raw) // piece.width
    block = SharedMemory(create=True, size=len(raw))
    try:
        block.buf[: len(raw)] = raw
        del raw
        jobs = [
            (block.name, piece.mode, piece.width, stride, z, row, str(dest), tile_px)
            for row in range(piece.width // tile_px)
        ]
        return sum(pool.map(_encode_tile_row, jobs, chunksize=1))
    finally:
        block.close()
        block.unlink()


def cut_pyramid(
    sheet,
    image_mod,
    dest: Path,
    tile_px: int = PYRAMID_TILE_PX,
    source: str = DEFAULT_LEVEL_SOURCE,
    workers: int = 1,
    dir_name: str = TILES_DIR_NAME,
) -> dict:
    """Cut ``sheet`` into ``dest/{z}/{x}_{y}.png`` for every level, and say what it wrote.

    Each level below the top is one Lanczos downscale of the whole sheet, sliced up --
    downscaling the sheet once per level rather than each tile from its four children
    keeps every level a resampling of the original pixels, so no level accumulates the
    softening of six successive halvings.

    The levels are cheap: level z is a quarter of level z+1, so everything under the top
    adds a third again to the top's own bytes.

    ``--enhance`` adds levels ABOVE this top out of upscaled pixels; it does not change
    these. z0..z6 are downscales of the sheet here whether that stage runs or not, because
    a level that has real pixels behind it has no business being drawn from invented ones.

    ``workers`` above one spreads the per-tile PNG encode over that many processes. The
    resampling above stays exactly where it was -- one downscale of the whole sheet, in this
    process -- so the tiles are the same bytes either way; see the module docstring. One is
    the default because the suite drives this with a stand-in sheet that is not an image.
    """
    top = pyramid_top_z(sheet.width, tile_px)
    levels = []
    pool = ProcessPoolExecutor(max_workers=workers) if workers > 1 else None
    try:
        for z in range(top + 1):
            side = tile_px << z
            level = sheet if side == sheet.width else sheet.resize((side, side), image_mod.LANCZOS)
            tiles = (1 << z) ** 2
            if pool is not None and tiles >= PARALLEL_MIN_TILES:
                written = cut_square_parallel(level, dest, z, tile_px, pool)
            else:
                written = cut_square(level, dest, z, 0, 0, tile_px)
            levels.append(
                {
                    "z": z,
                    "sheet_px": side,
                    "tiles": tiles,
                    "bytes": written,
                    "from": source,
                }
            )
            print(f"  pyramid z{z}: {side}x{side}, {tiles} tiles, {written / 1e6:.2f} MB")
    finally:
        if pool is not None:
            pool.shutdown()
    return {
        "layout": f"{dir_name}/{{z}}/{{x}}_{{y}}.png",
        "tile_px": tile_px,
        "max_z": top,
        "enhanced": False,
        "count": sum(level["tiles"] for level in levels),
        "bytes": sum(level["bytes"] for level in levels),
        "levels": levels,
        "workers": workers,
        "role": (
            "the same sheet at one resolution per zoom, so the page fetches the pixels it "
            "can actually show. map.png is still written beside it: it is what a page "
            "falls back to when there is no pyramid, and the one file a reader can open."
        ),
        "completeness": (
            "written to " + dir_name + STAGING_SUFFIX + " and renamed into place, so this "
            "directory is either a whole pyramid or absent -- an interrupted run cannot "
            "leave a partial one for a reader to trust. count is what a doubter can check "
            "it against."
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
    workers: int = 1,
    dir_name: str = TILES_DIR_NAME,
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

    ``dir_name`` is which tree of this layer is being installed -- ``tiles/`` or the @2x
    grid beside it -- and it carries its own staging and retirement names, so cutting the
    second one can never disturb the first one a reader is being served from.
    """
    staging = out_dir / (dir_name + STAGING_SUFFIX)
    retired = out_dir / (dir_name + RETIRED_SUFFIX)
    final = out_dir / dir_name
    for stale in (staging, retired):
        if stale.exists():
            shutil.rmtree(stale)
    staging.mkdir(parents=True)
    stats = cut_pyramid(sheet, image_mod, staging, tile_px, source, workers, dir_name)
    if enhance is not None:
        stats = merge_enhanced(stats, enhance(staging))

    on_disk = sum(1 for _ in staging.rglob("*.png"))
    if on_disk != stats["count"]:
        raise PyramidError(
            f"the pyramid was cut with {stats['count']} tiles but {on_disk} PNGs are in "
            f"{staging} -- refusing to install a tree that does not match its own count"
        )
    stats["installed_by"] = swap_into_place(staging, final, retired)
    return stats


def swap_into_place(staging: Path, final: Path, retired: Path) -> str:
    """Put the finished tree where it is served from, and say how it managed it.

    The whole tree at once is the intent and the first thing tried: the old tree is moved
    aside, the new one takes its name, the old one is deleted. A reader either meets the old
    pyramid or the new one and never a mixture.

    **Windows will not rename a directory anything has open**, though, and "anything" is not
    only this program: an Explorer window sitting in ``tiles/``, the search indexer walking
    it, a backup agent. That is not a rare state on a directory a person has been looking at
    -- it is the ordinary state -- and it used to end the run with an ``Access is denied``
    after ten minutes of drawing. So there is a second-best, and it is second-best rather
    than equal: the swap is done **one level at a time**, each level renamed atomically over
    its predecessor. A reader who catches the middle of that sees every level present and
    some of them still the old cut, rather than a level missing. Levels that only the old
    tree had are removed after, so a shallower new pyramid cannot leave a deep level of the
    old one behind pretending to belong to it.

    Which of the two happened is returned and recorded in the sidecar, because the weaker
    guarantee is worth being able to see from the outside.
    """
    if not final.exists():
        staging.rename(final)
        return "the whole tree renamed into place"
    try:
        final.rename(retired)
    except OSError:
        levels = sorted(child.name for child in staging.iterdir())
        for name in levels:
            target = final / name
            if target.exists():
                spent = final / (name + RETIRED_SUFFIX)
                if spent.exists():
                    shutil.rmtree(spent)
                target.rename(spent)
                (staging / name).rename(target)
                shutil.rmtree(spent)
            else:
                (staging / name).rename(target)
        for leftover in final.iterdir():
            if leftover.name in levels:
                continue
            if leftover.is_dir():
                shutil.rmtree(leftover)
            else:
                leftover.unlink()
        shutil.rmtree(staging)
        return (
            "level by level: something has the served directory open, which on Windows "
            "refuses a whole-tree rename"
        )
    staging.rename(final)
    shutil.rmtree(retired)
    return "the whole tree renamed into place"
