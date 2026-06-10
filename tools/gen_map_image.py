"""Cut data/local/map.png -- the web map's base image -- out of the installed game.

    uv run python tools/gen_map_image.py --pyooz-path <dir containing ooz>

The web page has always been able to show a base map at ``data/local/map.png``, and the
repository has always shipped none: a rendered map of this world is Coffee Stain's
artwork, so ``/api/mapimage`` is a loader and only a loader. That left the one honest
source of a base map -- **the player's own installed game** -- unreachable without a
third-party download, which is what this file fixes. It writes to ``data/local/``, which
is gitignored; the tool is committed and its output never is.

**Where the picture is.** The in-game world map is four ``Texture2D`` under
``/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_{col}-{row}``, each
4096x4096 ``PF_DXT1`` with 13 mips. The four stitch into one 8192x8192 sheet.

**The name is column-row, not row-column, and that is re-proven every run.** ``Map_1-0``
is the NORTH-EAST quadrant, not the south-west one. Both readings produce a plausible
map -- the world is roughly symmetric at a glance -- so the layout is not asserted here:
``seam_residuals`` measures the mean per-channel difference across each 4096 px seam at
full resolution, scores the *other* reading of the name with the same statistic, and
compares both against two controls taken inside a single tile. Tiles that genuinely abut
differ about as little as two adjacent scanlines of the same tile do; tiles that do not,
differ like two scanlines 100 rows apart. The run refuses to write unless the chosen
reading beats both the other reading and the distant control, so a slice being renamed or
re-cut cannot silently produce a mirrored world.

**And it is cut into a pyramid, because one 8192 px sheet is the wrong thing to hand a
browser.** ``data/local/tiles/{z}/{x}_{y}.png`` holds the same sheet at one resolution per
zoom: z0 is the whole world in a single 256 px tile, z5 is the full 8192 in 32x32 of them,
1,365 tiles in all. The page then fetches the pixels it can actually show -- a few hundred
KB at the whole-world framing instead of 16.2 MB that decodes to 268 MB of RGBA -- and
``/api/maptiles`` serves them. ``map.png`` is still written: it is what a page falls back
to when it finds no pyramid, and the one file a reader can open and eyeball. The pyramid
is written to ``tiles.incoming`` and **renamed** into place, so ``tiles/`` is either whole
or absent; a run interrupted halfway leaves a staging directory nothing serves rather than
a tree missing the levels it had not reached.

**The ``.ubulk`` length is the integrity check.** Each is exactly 11,182,080 bytes, which
is the mip chain 4096 down to 128 stored largest-first -- ``MIP_SIZES`` derives that total
rather than quoting it, so the constant cannot drift from the arithmetic. Mip 0 is
therefore simply ``ubulk[:8388608]``, with no offset to guess. A length that is not this
one means the texture was re-cooked at a different size or mip count, i.e. **the game
changed**, and the run stops rather than decoding whatever is there.

**BC1 decodes to BGRA.** ``texture2ddecoder.decode_bc1`` returns raw bytes in B, G, R, A
order; handing them to Pillow as ``"RGBA"`` swaps the red and blue channels, which turns
the ocean orange and looks enough like a stylised map that it survives a glance. The
``"raw", "BGRA"`` argument to ``Image.frombytes`` is what makes the water blue.

**The corners are measured, not trusted.** The sidecar pins the image at the in-game map
square the server already defaults to -- x [-3247, 4253] m, y [-3750, 3750] m -- and
states it explicitly rather than relying on that default. That the sheet really spans it
is re-measured every run in ``_meta.calibration``: the 626 static resource nodes of
``data/world_resource_nodes.mit.json`` are projected onto the sheet and counted against
the flat open-ocean colour, and the whole box is then swept +-300 m in 50 m steps. Nodes
stand on land, so a pin that is right cannot be improved on by sliding the box. A few
nodes read as sea at every pin -- this map's shoreline is drawn rather than sampled, and a
node on a headland sits inside a stroke of it -- so the verdict is not "zero" but "nothing
beyond one sweep step does better", which is what makes the corners good to about +-100 m
and no finer. A larger best shift is drift, and the run says so instead of quietly drawing
a map a few hundred metres off its own dots.

**Staleness.** The project's standing rule is that a pinned map artifact announces drift
rather than answering silently wrong. Two halves of it live here. The sidecar records the
installed build under ``sources.map_slices.game_version_pinned``, in the same shape
``data/resource_nodes.json`` uses, so an image and a node table cut from different builds
are comparable on sight. And the run **refuses to overwrite** an existing ``map.png``
unless the sidecar beside it names the build now installed: a picture cut from another
build -- or from somewhere else entirely, with no sidecar at all -- is not ours to
replace on a whim, and the repository's own tables are pinned to a build the new artwork
might no longer agree with. ``--force`` says it anyway.

**The side venv, and why there is one.** Oodle-compressed container blocks are opened by
``pyooz``, which is GPL-3.0, and the BC1 blocks by ``texture2ddecoder``. Neither is a
dependency of this project: both are offline generation-time tools, never imported from
``src/`` or ``sidecar/``, and no part of either is in the output. Pillow is only wanted
here as well. All three come off ``--pyooz-path``, which is a throwaway venv's
site-packages -- the same argument, and the same posture, as
``tools/gen_world_collectibles.py``. The recipe, run and proven:

    uv venv <tmp>/mapvenv
    uv pip install --python <tmp>/mapvenv pyooz texture2ddecoder pillow
    uv run python tools/gen_map_image.py --pyooz-path <tmp>/mapvenv/Lib/site-packages

The container reader itself is not reimplemented: ``tools/gen_world_collectibles.py``
already has one, and it is imported from there by path so this file stays runnable and
importable on its own.

**``--enhance``: two more zoom levels than the artwork has pixels.** The sheet runs out at
8192 px -- about 0.9 m to the pixel -- and a factory is machines eight metres across, so
the closest useful framing is already looking at a handful of pixels blown up eight times.
``--enhance`` adds z6 and z7 by running the sheet through Real-ESRGAN 4x on the GPU, which
a read-only bake-off picked over Lanczos, vtracer+resvg and two other models by measuring
stroke depth and edge gradient on four sampled regions. It is **off by default**: it needs
a 45 MB binary this repository will not vendor and a Vulkan device, and a tool whose
default path silently depends on either is a tool that fails on somebody else's machine.
The same recipe, with the flag on the end:

    uv run python tools/gen_map_image.py --pyooz-path <tmp>/mapvenv/Lib/site-packages --enhance

Four things about that stage are findings rather than choices, and each is pinned here.

*Tiling is mandatory, not an optimisation.* Handed the whole 32768 px output the binary
segfaults at about 70% of the rows: 32768*32768*3 is 3,221,225,472, and the index into its
output buffer is signed 32-bit, which tops out at 2,147,483,648. So the sheet goes through
in ``ENHANCE_TILE_PX`` squares with ``ENHANCE_OVERLAP_PX`` of context on every side, and
the overlap is cropped off after upscaling -- no tile edge the model saw is ever in the
output. The overlap is 96 px rather than the 64 the bake-off measured because 64 left one
seam of eight reading 2.4x its own in-tile control; every run re-measures all seven internal
boundaries at three rows and records the ratios in ``_meta.tiles.enhancement.seams``.

*The AI erases the faintest marks, so they are put back.* Its one measured defect is
expanded contrast: strong strokes deepen, and the weakest contour lines and map glyphs fade
out. Lanczos is the only candidate that keeps weak strokes at full depth, so the output is
the AI everywhere except a feathered mask over faint detail, where it is Lanczos --
computed from the same source pixels, at about 6% coverage. ``faint_mask`` is the whole
rule and is measured on the SOURCE alone, so it is reproducible from the input.

*The low levels still come from the original sheet.* z0..z5 are Lanczos downscales of the
8192 px artwork exactly as they were before ``--enhance`` existed; only z6 and z7, which
have no artwork behind them at all, come from the enhanced pixels. Whether that seam in
provenance is visible is measured rather than assumed: every run downscales eight enhanced
cores back to their source resolution and compares them against the source, against the
adjacent-column control, in ``_meta.tiles.enhancement.low_zoom``.

*The binary is pinned and verified.* ``ENHANCE_URL`` names one immutable GitHub release
asset and ``ENHANCE_SHA256`` is its digest; a download that hashes to anything else is not
unzipped. It is cached **outside this repository**, under platformdirs'
``user_cache_dir`` -- the same tree ``src/satisfactory_mcp/config.py`` builds ``cache_dir``
from, with ``bin/`` beneath it -- because it belongs to the machine's GPU rather than to
one checkout, and ``prune_cache`` only ever deletes ``save-*.pkl``. If it will not download
or will not run, the stage says which and exits nonzero. It never quietly substitutes
Lanczos: a pyramid that is soft where the sidecar says it is sharp is worse than no run.

And an enhanced pyramid is not silently downgraded. A later plain re-run over a
``tiles/`` tree whose sidecar says ``enhanced`` refuses in the same breath as the
cross-build guard, for the same reason: a refresh that quietly halves the map's resolution
is drift, and drift is announced. ``--enhance`` again, or ``--force``.

The stage needs ``numpy`` and ``scipy``, which unlike Pillow ARE dependencies of this
project, so they come from the project environment that ``uv run`` provides -- which is
also why they must not be installed into the side venv, where a second numpy would be
first on ``sys.path`` and leave scipy compiled against the other one.

**Licence.** The bytes this writes are Coffee Stain's artwork, read out of the reader's
own installed copy of the game and left in a gitignored directory. Nothing here is
committed, uploaded or redistributed, and ``/api/mapimage`` serves it to localhost only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Where Steam puts the game. Overridable; the container is the only thing read from it.
DEFAULT_GAME = Path("G:/SteamLibrary/steamapps/common/Satisfactory")

#: The mount-relative directory holding the four slices, inside FactoryGame-Windows.utoc.
SLICE_DIR = "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/"

#: The four slices. The suffix is ``<col>-<row>``: 0-0 is NW, 1-0 NE, 0-1 SW, 1-1 SE.
#: Proven per run by ``seam_residuals`` -- see the module docstring.
SLICES = ("Map_0-0", "Map_1-0", "Map_0-1", "Map_1-1")

#: One slice, decoded, is this square. Four of them make the sheet.
TILE_PX = 4096
SHEET_PX = TILE_PX * 2

#: The ``.ubulk`` mip chain, largest-first: 4096 down to 128, BC1's 8 bytes per 4x4 block.
#: Derived so that the file-length check below is arithmetic rather than a typed-in number.
MIP_SIZES = tuple(((TILE_PX >> i), (max(TILE_PX >> i, 4) // 4) ** 2 * 8) for i in range(6))
MIP0_BYTES = MIP_SIZES[0][1]
UBULK_BYTES = sum(size for _px, size in MIP_SIZES)

#: What the sheet is written at by default: the game's own resolution, because measured
#: rather than feared. An optimised RGB PNG of the whole 8192 sheet is 16 MB, which a
#: browser ``imageOverlay`` fetches off localhost instantly, so there is no reason to hand
#: the reader a downscale of their own map. ``--size`` takes it down for a machine where
#: 8192x8192 is too much picture to decode.
DEFAULT_SIZE_PX = 8192

#: The corners the sidecar pins, metres, game axes -- the in-game map square, which is
#: also ``DEFAULT_MAP_BOUNDS_M`` in the web API. Stated here so the sidecar carries them
#: explicitly instead of leaning on the server's default, and re-measured by ``calibrate``.
BOUNDS_M = {"x_min_m": -3247.0, "x_max_m": 4253.0, "y_min_m": -3750.0, "y_max_m": 3750.0}

#: Where the picture and its sidecar go. Gitignored, and that is the point.
LOCAL_DIR = ROOT / "data" / "local"
IMAGE_NAME = "map.png"
SIDECAR_NAME = "map.json"

#: And where the pyramid goes: ``tiles/{z}/{x}_{y}.png``, cut from the same sheet in the
#: same run. ``map.png`` stays -- it is the fallback for a page that finds no pyramid, and
#: the one file a reader can open and eyeball -- but it is 16 MB of 8192x8192 that a
#: browser decodes to 268 MB of RGBA whatever the view is, which is what the pyramid is
#: for: at the whole-world framing the page fetches a few hundred KB of z2 instead.
#: ``PYRAMID_TILE_PX`` is deliberately not called TILE_PX: a "tile" in this file is one of
#: the four 4096 px slices the game ships, and this is the 256 px square a browser fetches.
TILES_DIR_NAME = "tiles"
PYRAMID_TILE_PX = 256

#: The staging and retirement names beside it. A pyramid is only ever *renamed* into
#: place, so a reader can never meet half of one: an interrupted run leaves
#: ``tiles.incoming`` -- which nothing serves and the next run deletes -- rather than a
#: ``tiles/`` tree that is missing the levels the run had not got to yet.
TILES_STAGING = TILES_DIR_NAME + ".incoming"
TILES_RETIRED = TILES_DIR_NAME + ".retired"

#: --------------------------------------------------------------------------------------
#: The optional enhancement stage. See the module docstring; these are the pinned numbers.
#: --------------------------------------------------------------------------------------

#: One immutable GitHub release asset, and the digest of the bytes this file was written
#: against. A download that hashes to anything else is not unzipped and not run: it is a
#: 6 MB executable off the internet, and "the asset changed" is not a thing that happens
#: to a published release for a good reason.
ENHANCE_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
    "realesrgan-ncnn-vulkan-20220424-windows.zip"
)
ENHANCE_SHA256 = "abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d"
ENHANCE_EXE_NAME = "realesrgan-ncnn-vulkan.exe"

#: The model the bake-off chose, of the five the archive ships. It is the anime-tuned one
#: because this artwork is flat colour and drawn linework rather than photography, which is
#: what that model is trained on -- x4plus keeps photographic texture the map does not have.
ENHANCE_MODEL = "realesrgan-x4plus-anime"
ENHANCE_SCALE = 4

#: Source-side tiling. 1024 px squares with 96 px of context every side, so the model never
#: sees a tile edge that survives into the output -- and, more bluntly, so it does not
#: segfault on a 32-bit index. See the docstring.
ENHANCE_TILE_PX = 1024
ENHANCE_OVERLAP_PX = 96

#: The scratch tree the stage works in, beside the staging pyramid and gitignored with it.
#: Deleted before and after, so a dead run leaves nothing a later one would merge into.
ENHANCE_WORK = "enhance.work"

#: The faint-detail mask, and the whole of it. ``depth`` is how far a pixel sits below the
#: local mean of a FAINT_WINDOW box -- the map's marks are darker than what they are drawn
#: on -- grown by FAINT_GROW so a mark's halo is covered too. Between FAINT_LO and FAINT_HI
#: is the band the AI drops; below it there is nothing to protect and above it the AI is
#: better than Lanczos. FAINT_FEATHER then blurs the mask so the blend has no edge.
FAINT_WINDOW = 9
FAINT_GROW = 3
FAINT_LO = 3.0
FAINT_HI = 14.0
FAINT_FEATHER = 5

#: Where the seam check reads, in tile rows of the enhanced top level, and the columns its
#: control averages over. One arbitrary pair of adjacent columns is far too noisy a
#: denominator -- on quiet ground it is near zero and any seam divides into it enormously --
#: so the control is what adjacent columns cost across the whole tile.
SEAM_ROWS = (32, 64, 96)
CONTROL_COLS = (32, 64, 96, 128, 160, 192, 224)

#: How much worse than a boundary that is NOT a seam a real seam may read. The comparison
#: is against the same statistic measured where two tiles were cut from one upscaled core,
#: because that is the only honest zero: an edge in the artwork that happens to land on a
#: boundary costs the same whether the boundary is a seam or not.
SEAM_RATIO_MAX = 1.5

#: Where the low-zoom check samples, in tiles of the original top level. A stride that is
#: coprime with nothing in particular -- just a spread across the sheet -- and tiles that
#: are flat ocean are dropped rather than counted as agreement.
LOW_ZOOM_STRIDE = 5
LOW_ZOOM_SAMPLES = 12

#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "map_slices", "game_version_pinned")

#: And where it records whether the pyramid beside it was enhanced, which is what the
#: no-silent-downgrade guard reads back. It lives inside the ``tiles`` block rather than
#: beside it because it is a fact about that pyramid, and the two are replaced together.
ENHANCED_PATH = ("tiles", "enhanced")

#: The calibration's own knobs. The sweep resolution is what bounds the claim: a pin that
#: survives +-300 m in 50 m steps is right to about 100 m, and no better than that.
CALIBRATION_PX = 1024
SWEEP_M = 300
SWEEP_STEP_M = 50

#: How close a sampled pixel must be to the corner colour to count as open ocean. The
#: sheet's extreme corner is flat sea, and land on this map is beige-to-green: 12 sits in
#: the wide gap between "the same flat colour" and "anything the map actually draws".
OCEAN_TOLERANCE = 12.0

#: A seam is only believed if it reads better than this control -- two scanlines 100 rows
#: apart inside one tile, i.e. what two pieces of map that do NOT abut look like.
CONTROL_NEAR = (2000, 2001)
CONTROL_FAR = (2000, 2100)


class MissingImaging(RuntimeError):
    """No BC1 decoder or no Pillow: a setup problem, not a bug."""


class MissingUpscaler(RuntimeError):
    """The GPU stage cannot run, and says what to do about it.

    Raised rather than degraded from. ``--enhance`` asked for a pyramid whose top two
    levels are upscaled on the GPU; quietly giving back Lanczos ones instead would write a
    sidecar that says ``enhanced`` over pixels that are not, which is the one outcome worse
    than not running at all.
    """


def load_container_reader():
    """``tools/gen_world_collectibles.py``, imported by path.

    That file already carries this repository's IoStore reader, and a second copy of a
    format parser is a second thing to be wrong. Imported here rather than at module
    scope so this file can be imported -- by a test, say -- without pulling in the save
    parser it does not need.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gen_world_collectibles

    return gen_world_collectibles


def load_imaging(extra_path: Path | None) -> tuple[object, object, dict[str, str]]:
    """Import the BC1 decoder and Pillow out of the same throwaway venv as ``ooz``.

    Same posture as the Oodle import next door: neither is a dependency of this project,
    so neither is imported from the project environment.
    """
    if extra_path is not None:
        sys.path.insert(0, str(extra_path))
    try:
        import texture2ddecoder
        from PIL import Image
    except ImportError as exc:
        raise MissingImaging(
            "no `texture2ddecoder` or no Pillow importable, so the map's BC1 blocks cannot "
            "be decoded. This is deliberate: both are offline tools here, never "
            "dependencies of this project. Do:\n"
            "    uv venv <tmp>/mapvenv\n"
            "    uv pip install --python <tmp>/mapvenv pyooz texture2ddecoder pillow\n"
            "    uv run python tools/gen_map_image.py "
            "--pyooz-path <tmp>/mapvenv/Lib/site-packages"
        ) from exc
    versions = {}
    for dist in ("texture2ddecoder", "pillow"):
        try:
            from importlib.metadata import version

            versions[dist] = version(dist)
        except Exception:
            versions[dist] = "unknown"
    return texture2ddecoder, Image, versions


def read_game_build(game: Path) -> tuple[str, dict]:
    """The installed build, from the engine's own ``.version`` file beside the executable.

    A stated number rather than a scanned one: the file is JSON the build system wrote,
    so ``Changelist`` and ``BranchName`` are exactly what the pin string needs and there
    is nothing to parse out of a binary.
    """
    found = sorted(game.glob("Engine/Binaries/Win64/*-Win64-Shipping.version"))
    if not found:
        raise SystemExit(
            f"no Engine/Binaries/Win64/*-Win64-Shipping.version under {game} -- "
            "point --game at the install holding FactoryGame/ and Engine/"
        )
    raw = json.loads(found[0].read_text(encoding="utf-8"))
    pin = (
        f"buildVersion {raw.get('Changelist')} "
        f"(engine branch {raw.get('BranchName')}), the installed build"
    )
    return pin, raw


# --------------------------------------------------------------------------------------
# Decoding and stitching.
# --------------------------------------------------------------------------------------


def read_slice(store, name: str) -> bytes:
    """Mip 0's BC1 blocks for one slice, with the length check that guards the layout."""
    path = f"{SLICE_DIR}{name}.ubulk"
    if path not in store.by_path:
        raise SystemExit(
            f"{name}.ubulk is not in the container. The map slices moved or were renamed, "
            "which means the game changed; nothing here can be trusted until that is "
            "looked at."
        )
    raw = store.read_path(path)
    if len(raw) != UBULK_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in MIP_SIZES)
        raise SystemExit(
            f"{name}.ubulk is {len(raw)} bytes, expected exactly {UBULK_BYTES} -- the mip "
            f"chain {chain} at 8 bytes per 4x4 BC1 block. A different length means the "
            "texture was re-cooked at another size or mip count, i.e. the game changed. "
            "Refusing to decode mip 0 out of a file whose layout is no longer known."
        )
    return raw[:MIP0_BYTES]


def decode_tile(decoder, image_mod, raw: bytes):
    """One 4096x4096 slice. ``decode_bc1`` returns **BGRA**, which is the whole trick."""
    return image_mod.frombytes(
        "RGBA", (TILE_PX, TILE_PX), decoder.decode_bc1(raw, TILE_PX, TILE_PX), "raw", "BGRA"
    )


def _line(tile, box: tuple[int, int, int, int]) -> bytes:
    """One row or column of a tile as raw RGB bytes -- three per pixel, in order."""
    return tile.crop(box).convert("RGB").tobytes()


def _mean_abs(left: bytes, right: bytes) -> float:
    return round(sum(abs(a - b) for a, b in zip(left, right, strict=True)) / len(left), 4)


def _seams(nw, ne, sw, se) -> dict[str, float]:
    """The four abutting-edge residuals of one 2x2 arrangement of the slices."""
    right_edge = (TILE_PX - 1, 0, TILE_PX, TILE_PX)
    left_edge = (0, 0, 1, TILE_PX)
    bottom_edge = (0, TILE_PX - 1, TILE_PX, TILE_PX)
    top_edge = (0, 0, TILE_PX, 1)
    return {
        "vertical_x_4096_north": _mean_abs(_line(nw, right_edge), _line(ne, left_edge)),
        "vertical_x_4096_south": _mean_abs(_line(sw, right_edge), _line(se, left_edge)),
        "horizontal_y_4096_west": _mean_abs(_line(nw, bottom_edge), _line(sw, top_edge)),
        "horizontal_y_4096_east": _mean_abs(_line(ne, bottom_edge), _line(se, top_edge)),
    }


def seam_residuals(tiles: dict) -> dict:
    """Mean per-channel difference across each seam, against the alternative and controls.

    This is what proves ``Map_<col>-<row>``. Three numbers decide it, and none of them
    assumes the answer. The **other** reading of the name -- ``<row>-<col>``, which swaps
    the two off-diagonal slices -- is scored with the identical statistic, so the layout is
    chosen by comparison rather than by decree. The **controls** come from inside one tile,
    so they need no layout at all: adjacent scanlines say what a continuous map costs, and
    scanlines 100 rows apart say what two unrelated pieces of map cost.
    """
    nw, ne, sw, se = (tiles[n] for n in ("Map_0-0", "Map_1-0", "Map_0-1", "Map_1-1"))
    seams = _seams(nw, ne, sw, se)
    # <row>-<col> would put Map_0-1 in the north-east and Map_1-0 in the south-west.
    other = _seams(nw, sw, ne, se)
    controls = {
        f"adjacent_rows_{CONTROL_NEAR[0]}_vs_{CONTROL_NEAR[1]}": _mean_abs(
            _line(nw, (0, CONTROL_NEAR[0], TILE_PX, CONTROL_NEAR[0] + 1)),
            _line(nw, (0, CONTROL_NEAR[1], TILE_PX, CONTROL_NEAR[1] + 1)),
        ),
        f"distant_rows_{CONTROL_FAR[0]}_vs_{CONTROL_FAR[1]}": _mean_abs(
            _line(nw, (0, CONTROL_FAR[0], TILE_PX, CONTROL_FAR[0] + 1)),
            _line(nw, (0, CONTROL_FAR[1], TILE_PX, CONTROL_FAR[1] + 1)),
        ),
    }
    far = controls[f"distant_rows_{CONTROL_FAR[0]}_vs_{CONTROL_FAR[1]}"]
    worst = max(seams.values())
    return {
        "reading": (
            "the slice name is <col>-<row>: Map_0-0 north-west, Map_1-0 north-east, "
            "Map_0-1 south-west, Map_1-1 south-east"
        ),
        "seams": seams,
        "seams_under_the_other_reading": other,
        "controls_inside_one_tile": controls,
        "worst_seam": worst,
        "worst_seam_under_the_other_reading": max(other.values()),
        "layout_holds": worst < far and worst < max(other.values()),
        "verdict": (
            "every seam of the chosen reading sits near the adjacent-scanline control and "
            "far below two scanlines 100 rows apart, and the other reading of the name "
            "does not. The slices abut the way this file places them."
        ),
    }


# --------------------------------------------------------------------------------------
# Calibration: do the corners the sidecar pins actually put the world where the map is?
# --------------------------------------------------------------------------------------


def calibrate(sheet, image_mod, bounds: dict[str, float]) -> dict:
    """Project the static node table onto the sheet and sweep the pin for a better one.

    Nodes stand on land. A pin that is right therefore puts as few of them as possible on
    the flat open-ocean colour, and a pin that is wrong can be beaten by shifting the box.
    The sweep is the measurement; the wiki square is only the starting point.
    """
    table = ROOT / "data" / "world_resource_nodes.mit.json"
    if not table.is_file():
        return {"skipped": f"{table.relative_to(ROOT)} is not present, so the pin is unchecked"}
    nodes = json.loads(table.read_text(encoding="utf-8"))["nodes"]
    small = sheet.resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS).convert("RGB")
    px = small.load()
    ocean = px[8, 8]  # the extreme corner of the sheet is open sea on every reading

    def on_ocean(dx_m: float, dy_m: float) -> int:
        x0 = (bounds["x_min_m"] + dx_m) * 100.0
        x1 = (bounds["x_max_m"] + dx_m) * 100.0
        y0 = (bounds["y_min_m"] + dy_m) * 100.0
        y1 = (bounds["y_max_m"] + dy_m) * 100.0
        count = 0
        for node in nodes:
            u = int((node["x"] - x0) / (x1 - x0) * CALIBRATION_PX)
            v = int((node["y"] - y0) / (y1 - y0) * CALIBRATION_PX)
            u = min(max(u, 0), CALIBRATION_PX - 1)
            v = min(max(v, 0), CALIBRATION_PX - 1)
            here = px[u, v]
            if sum(abs(a - b) for a, b in zip(here, ocean, strict=True)) / 3.0 < OCEAN_TOLERANCE:
                count += 1
        return count

    steps = range(-SWEEP_M, SWEEP_M + 1, SWEEP_STEP_M)
    at_pin = on_ocean(0, 0)
    best = (at_pin, 0, 0)
    for dx in steps:
        for dy in steps:
            score = on_ocean(dx, dy)
            if score < best[0]:
                best = (score, dx, dy)
    off_by_m = max(abs(best[1]), abs(best[2]))
    return {
        "method": (
            f"{len(nodes)} static resource nodes from data/world_resource_nodes.mit.json "
            f"projected onto a {CALIBRATION_PX}px copy of the sheet and counted against the "
            "flat open-ocean colour. Nodes stand on land, so fewer is better."
        ),
        "nodes_projected": len(nodes),
        "nodes_on_open_ocean_at_the_pin": at_pin,
        "sweep": f"+-{SWEEP_M} m in {SWEEP_STEP_M} m steps, both axes",
        "best_shift_m": {"dx": best[1], "dy": best[2]},
        "nodes_on_open_ocean_at_the_best_shift": best[0],
        "pin_agrees_within_m": off_by_m,
        "pin_holds": off_by_m <= SWEEP_STEP_M,
        "accuracy_m": SWEEP_STEP_M * 2,
        "reading": (
            "a handful of nodes read as sea at any pin -- the shoreline on this map is "
            "drawn, not sampled, and a node on a headland or an islet sits inside a stroke "
            "of it -- so the number that matters is not zero but whether SHIFTING the whole "
            "box does better. Nothing beyond one sweep step does, which is the evidence for "
            "the corners: they are right to about accuracy_m and no finer. A best shift "
            "larger than one step would be real drift, and pin_holds would say so."
        ),
    }


# --------------------------------------------------------------------------------------
# The pyramid: the same sheet, cut small enough that a view fetches only what it shows.
# --------------------------------------------------------------------------------------


def pyramid_top_z(sheet_px: int, tile_px: int = PYRAMID_TILE_PX) -> int:
    """The deepest level of a pyramid over a ``sheet_px`` square: 8192 -> 5.

    Level z holds ``2**z`` tiles a side, so level ``top`` is the sheet at its own
    resolution. Derived rather than typed in, because ``--size`` can halve the sheet and a
    pyramid one level too deep is a level of tiles upscaled from nothing.
    """
    levels = sheet_px // tile_px
    if levels < 1 or levels & (levels - 1):
        raise SystemExit(
            f"a {sheet_px} px sheet is not a power-of-two multiple of {tile_px} px tiles, "
            "so no pyramid divides it evenly"
        )
    return levels.bit_length() - 1


def enhanced_top_z(
    sheet_px: int, scale: int = ENHANCE_SCALE, tile_px: int = PYRAMID_TILE_PX
) -> int:
    """The deepest level once the sheet has been upscaled ``scale`` times: 8192, 4x -> 7.

    Derived from ``pyramid_top_z`` rather than typed in, so the two cannot disagree about
    how many levels a 4x upscale is worth -- it is exactly log2(scale) of them, and a scale
    that is not a power of two would not divide the tile grid at all.
    """
    if scale < 1 or scale & (scale - 1):
        raise SystemExit(f"an upscale of {scale}x is not a power of two, so it adds no levels")
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

    Square, so ``width`` is asked for twice rather than ``height`` once: every image this
    file makes is one, and asking for only the attribute that is actually needed keeps the
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


def cut_pyramid(sheet, image_mod, dest: Path, tile_px: int = PYRAMID_TILE_PX) -> dict:
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
                "from": "the game's own 8192 px artwork, Lanczos",
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
    sheet, image_mod, out_dir: Path, tile_px: int = PYRAMID_TILE_PX, enhance=None
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
    stats = cut_pyramid(sheet, image_mod, staging, tile_px)
    if enhance is not None:
        stats = merge_enhanced(stats, enhance(staging))

    on_disk = sum(1 for _ in staging.rglob("*.png"))
    if on_disk != stats["count"]:
        raise SystemExit(
            f"the pyramid was cut with {stats['count']} tiles but {on_disk} PNGs are in "
            f"{staging} -- refusing to install a tree that does not match its own count"
        )
    if final.exists():
        final.rename(retired)
    staging.rename(final)
    if retired.exists():
        shutil.rmtree(retired)
    return stats


# --------------------------------------------------------------------------------------
# --enhance: two levels the artwork does not have, on the GPU, with the faint marks kept.
# --------------------------------------------------------------------------------------


def upscaler_cache_dir() -> Path:
    """Where the GPU binary lives: the machine's cache, never this repository.

    Built the way ``src/satisfactory_mcp/config.py`` builds ``cache_dir`` -- platformdirs'
    ``user_cache_dir`` under LOCALAPPDATA, which must not roam -- with ``bin/`` beneath it.
    A checkout is the wrong home for it twice over: it is 45 MB of somebody else's
    executable, and it is a property of this machine's GPU rather than of this clone, so N
    worktrees should share one copy. ``prune_cache`` next door only ever deletes
    ``save-*.pkl``, so nothing in the project sweeps this away behind the reader's back.
    """
    from platformdirs import user_cache_dir

    return Path(user_cache_dir("satisfactory-mcp", appauthor=False)) / "bin"


def sha256_of(path: Path) -> str:
    """The digest of a file, read in chunks -- the archive is 45 MB."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_upscaler(exe: Path, models: Path, src: Path, dst: Path, scale: int) -> tuple[int, str]:
    """One invocation of the ncnn binary. ``src``/``dst`` are both files or both directories.

    ``-m`` is passed explicitly: the binary will find ``models/`` beside itself, but only
    by resolving its own path, and a tool that depends on that is a tool that breaks when
    it is invoked through a symlink or a copied exe.
    """
    proc = subprocess.run(
        [
            str(exe),
            "-i",
            str(src),
            "-o",
            str(dst),
            "-m",
            str(models),
            "-n",
            ENHANCE_MODEL,
            "-s",
            str(scale),
            "-f",
            "png",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, (proc.stderr or proc.stdout or "")[-2000:]


def ensure_upscaler(cache: Path | None = None, *, smoke: bool = True) -> dict:
    """Download, verify and unpack the upscaler once, and prove the GPU will run it.

    Every failure here is a ``MissingUpscaler`` carrying what to do about it, because all
    of them are setup rather than bugs: no network, a proxy serving an error page, no
    Vulkan device, a laptop on its integrated GPU. The digest is checked before the archive
    is opened -- an executable is not unpacked on the strength of having arrived.

    The smoke test is the only cheap way to tell "no GPU" from "the map broke it": it is
    the 12 KB sample the archive ships, upscaled into a temporary directory, and it costs
    about a second against a run of several minutes.
    """
    cache = Path(cache) if cache is not None else upscaler_cache_dir()
    stem = ENHANCE_URL.rsplit("/", 1)[-1][: -len(".zip")]
    archive = cache / f"{stem}.zip"
    home = cache / stem
    exe = home / ENHANCE_EXE_NAME
    models = home / "models"

    cache.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        print(f"downloading the upscaler once to {archive}")
        partial = archive.with_suffix(".zip.part")
        try:
            with (
                urllib.request.urlopen(ENHANCE_URL, timeout=180) as response,
                partial.open("wb") as handle,
            ):
                shutil.copyfileobj(response, handle)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise MissingUpscaler(
                f"could not download the upscaler from {ENHANCE_URL}: {exc}\n"
                f"Fetch it by hand into {archive} and run this again -- the download is the "
                "only part of --enhance that needs the network, and it happens once."
            ) from exc
        partial.replace(archive)

    digest = sha256_of(archive)
    if digest != ENHANCE_SHA256:
        raise MissingUpscaler(
            f"{archive} hashes to {digest}, not the pinned {ENHANCE_SHA256}.\n"
            "That asset is an immutable GitHub release, so a different digest means the "
            "download was truncated or intercepted, not that upstream changed its mind. "
            f"Delete it and run this again; if it keeps happening, this file's pin is what "
            "to look at rather than the check."
        )

    if not exe.is_file():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(home)
    if not exe.is_file() or not models.is_dir():
        raise MissingUpscaler(
            f"{archive} unpacked into {home} without {ENHANCE_EXE_NAME} or models/ in it. "
            "Delete both and run this again."
        )
    if not (models / f"{ENHANCE_MODEL}.param").is_file():
        raise MissingUpscaler(
            f"{models} has no {ENHANCE_MODEL}.param -- this archive does not ship the model "
            "this file was measured against, so the run would be a different pipeline."
        )

    if smoke:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "smoke.png"
            code, log = run_upscaler(exe, models, home / "input.jpg", probe, ENHANCE_SCALE)
            if code != 0 or not probe.is_file():
                raise MissingUpscaler(
                    f"{exe} could not upscale its own 12 KB sample image (exit {code}), so "
                    "the GPU stage will not run on this machine.\n"
                    f"{log}\n"
                    "This binary needs a Vulkan 1.1 device and its driver -- on a laptop, "
                    "check that it is not being handed the integrated GPU. Re-run without "
                    "--enhance for the plain z0..z5 pyramid; nothing else about this tool "
                    "needs a GPU."
                )

    return {
        "exe": exe,
        "models": models,
        "home": home,
        "archive": archive,
        "url": ENHANCE_URL,
        "sha256": digest,
    }


def check_array_stack() -> tuple[str, str]:
    """numpy and scipy, and the check that they came out of the same environment.

    These two are dependencies of this project, unlike Pillow, so they come from what
    ``uv run`` provides rather than from ``--pyooz-path``. But that path is inserted at the
    FRONT of ``sys.path``, so a side venv that happened to carry its own numpy would win
    the import while scipy still came from the project -- a scipy compiled against a
    different numpy, which fails as a segfault or a wrong answer rather than an ImportError.
    Cheaper to state and check than to debug.
    """
    try:
        import numpy
        import scipy
    except ImportError as exc:
        raise MissingUpscaler(
            "--enhance needs numpy and scipy, which ARE dependencies of this project: run "
            "this through `uv run` rather than a bare python, and do not install them into "
            "the --pyooz-path venv."
        ) from exc
    homes = {Path(module.__file__).resolve().parents[1] for module in (numpy, scipy)}
    if len(homes) != 1:
        raise MissingUpscaler(
            "numpy and scipy are imported from different environments -- "
            + " and ".join(sorted(str(home) for home in homes))
            + ".\nThat is what happens when the --pyooz-path venv carries its own numpy: it "
            "goes to the front of sys.path and scipy is left compiled against another one. "
            "Uninstall numpy and scipy from that venv; it only needs pyooz, "
            "texture2ddecoder and pillow."
        )
    return numpy.__version__, scipy.__version__


def faint_mask(luma):
    """Where the AI must not be trusted: 1 on faint marks, 0 on flat fill and strong ones.

    Measured on the SOURCE luma alone, so the mask is reproducible from the input without
    reference to any candidate's output -- which is what makes the blend below a rule
    rather than a taste. ``depth`` is how far a pixel sits below the local mean of its
    neighbourhood; the map's marks are all darker than what they are drawn on. Between
    FAINT_LO and FAINT_HI is the band the model erases, and the two clips are the two ends
    of it: nothing to protect on flat fill, and above the band the AI is the better answer.

    Returns weights in [0, 1] -- a product of two clipped ramps, box-blurred, which cannot
    leave the interval -- so the caller can blend with it without clamping again.
    """
    import numpy as np
    from scipy.ndimage import maximum_filter, uniform_filter

    depth = uniform_filter(luma.astype(np.float32), FAINT_WINDOW) - luma.astype(np.float32)
    grown = maximum_filter(depth, FAINT_GROW)
    weight = np.clip((FAINT_HI - grown) / (FAINT_HI - FAINT_LO), 0.0, 1.0)
    weight *= np.clip((grown - FAINT_LO) / FAINT_LO, 0.0, 1.0)
    return uniform_filter(weight, FAINT_FEATHER)


def hybrid_upscale(source, upscaled, image_mod, scale: int = ENHANCE_SCALE):
    """The AI everywhere, Lanczos where the AI drops detail. Returns (image, coverage).

    Both sides are computed from the same source square, so the only thing the mask picks
    between is two renderings of identical pixels. The mask is upsampled bilinearly rather
    than by nearest: it is a blend weight, and a blocky weight would print the mask's own
    4 px grid into the output.
    """
    import numpy as np

    side = source.width * scale
    weight = faint_mask(np.asarray(source, np.float32).mean(2))
    grid = image_mod.fromarray((weight * 255.0).astype(np.uint8))
    big = np.asarray(grid.resize((side, side), image_mod.BILINEAR), np.float32)[..., None] / 255.0
    anime = np.asarray(upscaled, np.float32)
    lanczos = np.asarray(source.resize((side, side), image_mod.LANCZOS), np.float32)
    blend = anime * (1.0 - big) + lanczos * big
    return image_mod.fromarray(blend.astype(np.uint8)), float(weight.mean())


def _padded_crop(sheet, image_mod, tx: int, ty: int, tile: int, overlap: int):
    """One source square plus ``overlap`` px of context on every side.

    Off the sheet -- which only happens at its outer border -- the pad is black. Every
    padded pixel is cropped away again after upscaling, so the pad's only effect is on what
    the model sees as context at the world's edge, which is open ocean.
    """
    x0, y0 = tx * tile, ty * tile
    box = (x0 - overlap, y0 - overlap, x0 + tile + overlap, y0 + tile + overlap)
    clamped = (
        max(0, box[0]),
        max(0, box[1]),
        min(sheet.width, box[2]),
        min(sheet.height, box[3]),
    )
    crop = sheet.crop(clamped).convert("RGB")
    if crop.size != (tile + 2 * overlap, tile + 2 * overlap):
        pad = image_mod.new("RGB", (tile + 2 * overlap, tile + 2 * overlap))
        pad.paste(crop, (clamped[0] - box[0], clamped[1] - box[1]))
        crop = pad
    return crop


def _column(image, x: int) -> bytes:
    """One pixel column of an image as raw RGB bytes."""
    return _line(image, (x, 0, x + 1, image.height))


def _column_control(image) -> float:
    """What two adjacent columns of this tile cost, averaged over the whole tile.

    The denominator every ratio below is taken against. One arbitrary column pair is not
    it: quiet ground gives a control near zero, and dividing by that turns an invisible
    difference into an enormous number. Averaged across the tile it is a stable statement
    of how much this piece of map changes from one column to the next.
    """
    return sum(_mean_abs(_column(image, c), _column(image, c + 1)) for c in CONTROL_COLS) / len(
        CONTROL_COLS
    )


def _boundary(dest: Path, image_mod, z: int, x: int, y: int) -> tuple[float, float]:
    """The across-boundary difference at tile ``(x, y)``, and that tile's own control."""
    left = image_mod.open(dest / tile_relpath(z, x - 1, y))
    right = image_mod.open(dest / tile_relpath(z, x, y))
    edge = _mean_abs(_column(left, left.width - 1), _column(right, 0))
    return round(edge, 4), round(_column_control(right), 4)


def _split_boundaries(samples: list[tuple[float, float]]) -> tuple[list[float], list[float]]:
    """Ratios where the ground has variation, absolute differences where it has none.

    A tile of open ocean has a control of exactly zero, and no ratio taken against it means
    anything -- so those samples are not divided into and quietly turned into infinities.
    They are reported as what they are: the raw difference across a boundary drawn on
    ground that is one flat colour, where anything but zero would be a visible line.
    """
    live = sorted(round(edge / control, 3) for edge, control in samples if control)
    flat = sorted(edge for edge, control in samples if not control)
    return live, flat


def seam_check(dest: Path, image_mod, z: int, step: int, span: int) -> dict:
    """Is any source-tile boundary visible? Measured against boundaries that are not seams.

    A real seam falls every ``step`` tiles of level ``z``, where two separately upscaled
    source squares meet. The control is the identical statistic at boundaries halfway
    between them, where the two tiles were cut from ONE upscaled core and there is nothing
    to stitch -- so whatever this measurement costs when there is no seam is what it costs
    here, and the only question is whether the seams cost more.

    That control is the whole point. An edge in the artwork that happens to land on a tile
    boundary reads large whether or not the boundary is a seam, so a bare threshold on the
    ratio would condemn the map's own coastlines. Comparing like with like does not.
    """
    seams, seam_flat = _split_boundaries(
        [_boundary(dest, image_mod, z, x, y) for x in range(step, span, step) for y in SEAM_ROWS]
    )
    interior, interior_flat = _split_boundaries(
        [
            _boundary(dest, image_mod, z, x, y)
            for x in range(step // 2, span, step)
            for y in SEAM_ROWS
        ]
    )
    seam_median = seams[len(seams) // 2] if seams else 0.0
    interior_median = interior[len(interior) // 2] if interior else 0.0
    return {
        "method": (
            f"the across-boundary mean per-channel difference over the tile's own "
            f"adjacent-column difference, at rows {list(SEAM_ROWS)} of z{z}. seam_ratios "
            f"are the real boundaries -- one every {step} tiles, where two separately "
            "upscaled squares meet -- and interior_ratios the same statistic halfway "
            "between them, where one core was simply cut in two."
        ),
        "overlap_px": ENHANCE_OVERLAP_PX,
        "seam_ratios": seams,
        "interior_ratios": interior,
        "seam_median": seam_median,
        "interior_median": interior_median,
        "seam_worst": max(seams) if seams else 0.0,
        "interior_worst": max(interior) if interior else 0.0,
        "on_flat_ground": {
            "note": (
                "boundaries whose tile has no column-to-column variation at all -- open "
                "ocean. No ratio is meaningful there, so these are the raw across-boundary "
                "differences, and they are the strictest test the sheet has: on one flat "
                "colour, anything but zero is a line a reader would see."
            ),
            "seam_edges": seam_flat,
            "interior_edges": interior_flat,
        },
        "threshold": SEAM_RATIO_MAX,
        "seams_invisible": (
            seam_median <= SEAM_RATIO_MAX * interior_median and max(seam_flat, default=0.0) == 0.0
        ),
        "reading": (
            "the seams read at or below what a boundary with no seam in it reads, so the "
            "stitching contributes nothing a reader could pick out from the map's own "
            "edges. The overlap is what buys that: the model never sees a tile edge that "
            "survives into the output."
        ),
    }


def low_zoom_residual(dest: Path, image_mod, top: int, tile_px: int) -> dict:
    """Would the low levels look different if they came from the enhanced sheet instead?

    z0..z5 are downscales of the game's own artwork, and the honest question about that
    choice is whether anybody could tell. So each sampled top-level tile of the artwork is
    compared against the four enhanced tiles above it, mosaicked and Lanczos'd back down to
    the same resolution -- literally the two candidate provenances for that one tile --
    against the same adjacent-column control the seam check uses.

    Tiles whose control is zero are dropped rather than counted: open ocean agrees with
    everything, and counting it would be padding the answer with tiles that cannot disagree.
    """
    span = 1 << top
    ratios = []
    for x in range(2, span, LOW_ZOOM_STRIDE):
        for y in range(2, span, LOW_ZOOM_STRIDE):
            if len(ratios) >= LOW_ZOOM_SAMPLES:
                break
            artwork = image_mod.open(dest / tile_relpath(top, x, y)).convert("RGB")
            control = _column_control(artwork)
            if not control:
                continue
            mosaic = image_mod.new("RGB", (tile_px * 2, tile_px * 2))
            for dx in (0, 1):
                for dy in (0, 1):
                    child = image_mod.open(dest / tile_relpath(top + 1, 2 * x + dx, 2 * y + dy))
                    mosaic.paste(child.convert("RGB"), (dx * tile_px, dy * tile_px))
            back = mosaic.resize((tile_px, tile_px), image_mod.LANCZOS)
            middle = tile_px // 2
            difference = _mean_abs(_column(artwork, middle), _column(back, middle))
            ratios.append(round(difference / control, 3))
    ratios.sort()
    median = ratios[len(ratios) // 2] if ratios else 0.0
    return {
        "method": (
            f"{len(ratios)} tiles of z{top} as cut from the artwork, differenced against "
            f"their own four z{top + 1} children mosaicked and Lanczos'd back down to "
            f"{tile_px} px, over the artwork tile's adjacent-column difference"
        ),
        "samples": len(ratios),
        "ratios": ratios,
        "median_ratio": median,
        "levels_from": (
            f"z0..z{top} are downscales of the game's own artwork; only the levels above "
            "it, which have no artwork behind them, come from the enhanced pixels"
        ),
        "indistinguishable": median <= 1.0,
        "reading": (
            "a ratio at or under 1 means the two provenances differ by less than the "
            "artwork differs from its own next column -- so cutting the low levels from "
            "the original changes nothing a reader could see, and taking the simple path "
            "is a measurement rather than a shrug."
        ),
    }


def enhance_levels(
    sheet,
    image_mod,
    dest: Path,
    upscaler: dict,
    work: Path,
    *,
    tile_px: int = PYRAMID_TILE_PX,
    source_tile: int = ENHANCE_TILE_PX,
    overlap: int = ENHANCE_OVERLAP_PX,
    scale: int = ENHANCE_SCALE,
) -> dict:
    """Add the upscaled levels to ``dest``, and measure everything the sidecar claims.

    The enhanced sheet is never held whole: at 32768 px it would be 3.2 GB of RGB, which is
    both more than a machine wants to spend and the exact overflow that makes the binary
    segfault. It exists only as sixty-four cores, each cut into its z7 tiles and its z6
    ones as soon as it is blended and then dropped.
    """
    top = pyramid_top_z(sheet.width, tile_px)
    enhanced_top = enhanced_top_z(sheet.width, scale, tile_px)
    grid = sheet.width // source_tile
    if grid * source_tile != sheet.width:
        raise MissingUpscaler(
            f"a {sheet.width} px sheet does not divide into {source_tile} px squares, so the "
            "upscaler cannot be fed without a partial tile"
        )
    src_dir, up_dir = work / "in", work / "out"
    for directory in (src_dir, up_dir):
        directory.mkdir(parents=True)

    started = time.perf_counter()
    for ty in range(grid):
        for tx in range(grid):
            crop = _padded_crop(sheet, image_mod, tx, ty, source_tile, overlap)
            crop.save(src_dir / f"t_{tx:02d}_{ty:02d}.png")
    t_cut = time.perf_counter() - started
    print(
        f"  enhance: {grid * grid} source squares of {source_tile + 2 * overlap}px "
        f"({source_tile} + 2x{overlap} overlap) cut in {t_cut:.1f}s"
    )

    started = time.perf_counter()
    code, log = run_upscaler(upscaler["exe"], upscaler["models"], src_dir, up_dir, scale)
    t_upscale = time.perf_counter() - started
    produced = sorted(up_dir.glob("*.png"))
    if code != 0 or len(produced) != grid * grid:
        raise MissingUpscaler(
            f"{upscaler['exe']} exited {code} after {t_upscale:.0f}s with "
            f"{len(produced)} of {grid * grid} squares written.\n"
            f"{log}\n"
            "The pyramid already installed has not been touched -- this ran inside the "
            f"staging tree. Nothing here recovers by falling back to Lanczos: re-run "
            "without --enhance if that is what is wanted, and the sidecar will say so."
        )
    print(
        f"  enhance: {grid * grid} squares upscaled {scale}x with {ENHANCE_MODEL} in "
        f"{t_upscale:.1f}s ({t_upscale / (grid * grid):.2f}s each)"
    )

    started = time.perf_counter()
    core_px = source_tile * scale
    margin = overlap * scale
    per_level = {z: 0 for z in range(top + 1, enhanced_top + 1)}
    coverage: list[float] = []
    for ty in range(grid):
        for tx in range(grid):
            name = f"t_{tx:02d}_{ty:02d}.png"
            source = image_mod.open(src_dir / name).convert("RGB")
            upscaled = image_mod.open(up_dir / name).convert("RGB")
            blended, covered = hybrid_upscale(source, upscaled, image_mod, scale)
            core = blended.crop((margin, margin, margin + core_px, margin + core_px))
            coverage.append(covered)
            for z in per_level:
                side = core_px >> (enhanced_top - z)
                piece = core if side == core_px else core.resize((side, side), image_mod.LANCZOS)
                span = side // tile_px
                per_level[z] += cut_square(piece, dest, z, tx * span, ty * span, tile_px)
    t_pyramid = time.perf_counter() - started

    levels = []
    for z, written in sorted(per_level.items()):
        side = tile_px << z
        levels.append(
            {
                "z": z,
                "sheet_px": side,
                "tiles": (1 << z) ** 2,
                "bytes": written,
                "from": f"the sheet upscaled {scale}x by {ENHANCE_MODEL}, faint detail restored",
            }
        )
        print(f"  pyramid z{z}: {side}x{side}, {(1 << z) ** 2} tiles, {written / 1e6:.2f} MB")

    seams = seam_check(dest, image_mod, enhanced_top, core_px // tile_px, 1 << enhanced_top)
    low = low_zoom_residual(dest, image_mod, top, tile_px)
    print(
        f"  enhance: seams read {seams['seam_median']:.2f}x their own control where "
        f"boundaries that are NOT seams read {seams['interior_median']:.2f}x theirs"
    )
    print(
        f"  enhance: the mask covers {sum(coverage) / len(coverage) * 100:.1f}% of the sheet; "
        f"z{top} from the artwork differs {low['median_ratio']:.2f}x its control from the "
        f"same tile taken out of z{top + 1}"
    )
    if not seams["seams_invisible"]:
        print(
            f"  WARNING: the seams read {seams['seam_median']:.2f}x their control against "
            f"{seams['interior_median']:.2f}x at boundaries with no seam in them, over the "
            f"{SEAM_RATIO_MAX}x this file calls invisible. The overlap is not buying enough "
            "context. The tiles are still written -- _meta.tiles.enhancement says so."
        )

    return {
        "levels": levels,
        "enhancement": {
            "model": ENHANCE_MODEL,
            "scale": scale,
            "source_tile_px": source_tile,
            "overlap_px": overlap,
            "source_squares": grid * grid,
            "enhanced_sheet_px": sheet.width * scale,
            "binary": {
                "url": upscaler["url"],
                "sha256": upscaler["sha256"],
                "exe": ENHANCE_EXE_NAME,
                "cached_at": str(upscaler["home"]),
                "licence": (
                    "Real-ESRGAN ncnn-Vulkan, BSD-3-Clause, by Xintao Wang et al. A "
                    "prebuilt binary run offline at generation time; not vendored, not a "
                    "dependency of this project, and no part of it is in the output."
                ),
            },
            "hybrid": {
                "rule": (
                    "output = anime * (1 - w) + lanczos * w, w from faint_mask on the "
                    "source luma alone, upsampled bilinearly"
                ),
                "window_px": FAINT_WINDOW,
                "grow_px": FAINT_GROW,
                "band": [FAINT_LO, FAINT_HI],
                "feather_px": FAINT_FEATHER,
                "mask_coverage": round(sum(coverage) / len(coverage), 4),
                "why": (
                    "the model's one measured defect is expanded contrast: it deepens strong "
                    "strokes and erases the faintest ones. Lanczos is the only candidate "
                    "that keeps weak strokes at full depth, so it is used exactly where they "
                    "are and nowhere else."
                ),
            },
            "seams": seams,
            "low_zoom": low,
            "timings_s": {
                "cut_source_squares": round(t_cut, 2),
                "upscale": round(t_upscale, 2),
                "blend_and_cut": round(t_pyramid, 2),
                "total": round(t_cut + t_upscale + t_pyramid, 2),
            },
        },
    }


# --------------------------------------------------------------------------------------
# The sidecar, and the staleness guard that reads it back.
# --------------------------------------------------------------------------------------


def pinned_build(sidecar: dict) -> str | None:
    """The build an existing sidecar names, or None if it names none."""
    node: object = sidecar.get("_meta")
    for key in PIN_PATH:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


def pinned_enhanced(sidecar: dict) -> bool:
    """Whether the pyramid an existing sidecar describes was cut with ``--enhance``.

    Anything that is not a literal ``true`` reads as false, including a sidecar that has
    never heard of the key -- which is every sidecar written before this stage existed, and
    they describe plain pyramids, so that is the right answer rather than a lenient one.
    """
    node: object = sidecar.get("_meta")
    for key in ENHANCED_PATH:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return node is True


def enhancement_downgrades(sidecar: dict, enhance_now: bool) -> bool:
    """Would this run replace an enhanced pyramid with a plainer one? Then it must not.

    The whole rule, in one place so the test can hold it rather than re-derive it from the
    branch in ``main``. Enhancing an unenhanced pyramid is an upgrade and never blocked;
    re-enhancing an enhanced one is a refresh; only the fourth case is the silent quality
    loss this guards -- a plain re-run over a tree the reader had sharpened, which would
    otherwise halve the map's usable resolution and say nothing.
    """
    return pinned_enhanced(sidecar) and not enhance_now


def build_sidecar(
    *,
    build_pin: str,
    build_raw: dict,
    image: dict,
    integrity: dict,
    layout: dict,
    calibration: dict,
    versions: dict[str, str],
    tiles: dict | None = None,
) -> dict:
    """The file the web API reads, plus the provenance a reader needs to date it.

    The four corner keys are the whole of what ``/api/mapimage`` looks at -- it copies
    only the keys it already knows and ignores everything else -- so ``_meta`` rides along
    beside them without the server needing to be taught anything.
    """
    return {
        **BOUNDS_M,
        "_meta": {
            "description": (
                "Corners for data/local/map.png and the tiles/ pyramid cut from it, and "
                "where that picture came from. All of it is local: data/local/ is "
                "gitignored and no map imagery is ever committed to this repository."
            ),
            "bounds": (
                "metres, game axes -- +X east, +Y south. These are the corners of the "
                "in-game map square, stated here rather than left to the server's own "
                "default so this file says where its picture goes without reference to "
                "anything else. calibration below is the measurement behind them."
            ),
            "generator": "tools/gen_map_image.py",
            "transcribed": datetime.now(UTC).date().isoformat(),
            "sources": {
                "map_slices": {
                    "name": (
                        "/Game/FactoryGame/Interface/UI/Assets/MapTest/SlicedMap/Map_{col}-{row}"
                    ),
                    "licence": (
                        "Coffee Stain Studios' own artwork, read out of the reader's "
                        "installed copy of the game. Not committed, not redistributed, "
                        "and served to localhost only."
                    ),
                    "derivation": (
                        f"four {TILE_PX}x{TILE_PX} PF_DXT1 Texture2D; mip 0 of each .ubulk, "
                        f"BC1-decoded and stitched 2x2 into a {SHEET_PX}x{SHEET_PX} sheet"
                    ),
                    "role": "the whole picture",
                    "game_version_pinned": build_pin,
                    "game_version_raw": {
                        key: build_raw.get(key)
                        for key in ("Changelist", "BranchName", "BuildId", "GameVersion")
                    },
                    "transcribed": datetime.now(UTC).date().isoformat(),
                },
            },
            "image": image,
            "tiles": tiles or {"absent": "this run wrote no pyramid; map.png is the whole map"},
            "integrity": integrity,
            "layout": layout,
            "calibration": calibration,
            "decoders": {
                "oodle": {
                    "name": "pyooz",
                    "version": versions.get("pyooz", "unknown"),
                    "import_name": "ooz",
                    "licence": "GPL-3.0",
                    "role": (
                        "container block decompression, offline, at generation time only. "
                        "Not a dependency of this project, never imported from src/ or "
                        "sidecar/, and no part of it is in the output."
                    ),
                },
                "block_compression": {
                    "name": "texture2ddecoder",
                    "version": versions.get("texture2ddecoder", "unknown"),
                    "role": "BC1 (DXT1) block decoding",
                    "note": (
                        "decode_bc1 returns BGRA, not RGBA. Read as RGBA the red and blue "
                        "channels swap, which turns the ocean orange and still looks like "
                        "a stylised map -- hence the explicit raw/BGRA decode."
                    ),
                },
                "imaging": {"name": "pillow", "version": versions.get("pillow", "unknown")},
            },
            "staleness": (
                "sources.map_slices.game_version_pinned is the build this picture was cut "
                "from, in the same shape data/resource_nodes.json uses, so an image and a "
                "node table from different builds are comparable on sight. "
                "tools/gen_map_image.py refuses to overwrite map.png OR tiles/ unless this "
                "sidecar names the build then installed; --force says it anyway. tiles."
                "enhanced is the second half of the same posture: a run WITHOUT --enhance "
                "refuses to replace a pyramid this says was enhanced, because a refresh "
                "that quietly drops two zoom levels is drift too."
            ),
        },
    }


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--game",
        type=Path,
        default=DEFAULT_GAME,
        help="Satisfactory install directory (the one holding FactoryGame/ and Engine/)",
    )
    parser.add_argument(
        "--pyooz-path",
        type=Path,
        default=None,
        help=(
            "directory holding an importable `ooz` -- a throwaway venv's site-packages. "
            "The same venv must carry texture2ddecoder and Pillow; see the module docstring"
        ),
    )
    parser.add_argument(
        "--size",
        type=int,
        default=DEFAULT_SIZE_PX,
        choices=[SHEET_PX, SHEET_PX // 2, SHEET_PX // 4],
        help=(
            f"square edge of the written PNG (default {DEFAULT_SIZE_PX}). {SHEET_PX} is the "
            "game's own resolution; anything smaller is a Lanczos downscale of it"
        ),
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR,
        help="destination directory for map.png, map.json and tiles/ (gitignored)",
    )
    parser.add_argument(
        "--enhance",
        action="store_true",
        help=(
            f"add z6 and z7 by upscaling the sheet {ENHANCE_SCALE}x with {ENHANCE_MODEL} on "
            "the GPU. Off by default: it downloads a 45 MB binary once and needs a Vulkan "
            "device. See the module docstring"
        ),
    )
    parser.add_argument(
        "--esrgan-cache",
        type=Path,
        default=None,
        help=(
            "where the upscaler is kept (default: platformdirs' user cache, bin/). Outside "
            "the repository either way"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "overwrite a map.png or tiles/ this run cannot show was cut from the installed "
            "build, or replace an enhanced pyramid with a plain one"
        ),
    )
    args = parser.parse_args()

    gwc = load_container_reader()
    try:
        ooz, pyooz_version = gwc.load_oodle(args.pyooz_path)
    except gwc.MissingOodle as exc:
        print(exc)
        return 2
    try:
        decoder, image_mod, versions = load_imaging(args.pyooz_path)
    except MissingImaging as exc:
        print(exc)
        return 2
    versions["pyooz"] = pyooz_version

    build_pin, build_raw = read_game_build(args.game)
    print(f"installed build: {build_pin}")

    # ---- staleness: whose picture is already there, and from which build? ------------
    out_dir: Path = args.out_dir
    image_path = out_dir / IMAGE_NAME
    sidecar_path = out_dir / SIDECAR_NAME
    tiles_dir = out_dir / TILES_DIR_NAME
    # The pyramid is covered by the same refusal as the picture, and for the same reason:
    # a tiles/ tree cut from another build is somebody else's artwork of another world.
    if (image_path.is_file() or tiles_dir.is_dir()) and not args.force:
        sidecar_now: dict = {}
        try:
            sidecar_now = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            sidecar_now = {}
        if not isinstance(sidecar_now, dict):
            sidecar_now = {}
        # The other half of the staleness posture: a refresh may not quietly cost the
        # reader the two zoom levels they generated last time.
        if enhancement_downgrades(sidecar_now, args.enhance):
            print(
                f"{tiles_dir} was cut with --enhance and this run was not, so it would "
                "replace a z0..z7 pyramid with a z0..z5 one.\n"
                "A refresh that quietly drops the two sharpest zoom levels is exactly the "
                "kind of drift this tool announces rather than performs. Pass --enhance to "
                "keep them, or --force to accept the plainer map."
            )
            return 5
        existing = pinned_build(sidecar_now)
        if existing != build_pin:
            there = " and ".join(str(p) for p in (image_path, tiles_dir) if p.exists())
            print(
                f"{there} already exists and this run cannot show it was cut from the "
                f"installed build.\n"
                f"  installed: {build_pin}\n"
                f"  that file: {existing or 'no sidecar, or no build recorded in it'}\n"
                "A picture from another build -- or from somewhere else entirely -- is not "
                "this tool's to replace: the repository's own tables are pinned to a build "
                "the new artwork may no longer agree with, and drift is meant to be "
                "announced rather than overwritten. Pass --force to overwrite it anyway."
            )
            return 3

    # ---- the GPU stage is proven before anything is decoded -------------------------
    # Downloading 45 MB and finding out there is no Vulkan device is a two-second answer;
    # finding it out after six minutes of decoding and cutting is not. So --enhance settles
    # its binary, its model and its numpy up front, and a failure here costs nothing.
    upscaler = None
    if args.enhance:
        if args.size != SHEET_PX:
            print(
                f"--enhance and --size {args.size} together would upscale a downscale, "
                "which is inventing detail twice. Refusing: run at "
                f"{SHEET_PX} or without --enhance."
            )
            return 6
        try:
            upscaler = ensure_upscaler(args.esrgan_cache)
            upscaler["numpy"], upscaler["scipy"] = check_array_stack()
        except MissingUpscaler as exc:
            print(exc)
            return 6
        print(
            f"  upscaler: {upscaler['exe']} ({ENHANCE_MODEL}, sha256 "
            f"{upscaler['sha256'][:16]}...), numpy {upscaler['numpy']} / "
            f"scipy {upscaler['scipy']}"
        )

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the map slices from {paks} with pyooz {pyooz_version}")
    store = gwc.IoStore(paks, "FactoryGame-Windows", ooz.decompress)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )

    # ---- decode -----------------------------------------------------------------------
    tiles = {}
    for name in SLICES:
        raw = read_slice(store, name)
        tiles[name] = decode_tile(decoder, image_mod, raw)
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        print(
            f"  {name}: {UBULK_BYTES} B .ubulk, mip 0 decoded -> ({col * TILE_PX}, {row * TILE_PX})"
        )

    layout = seam_residuals(tiles)
    for label, value in layout["seams"].items():
        print(f"  seam {label:26s} {value:8.4f}")
    for label, value in layout["controls_inside_one_tile"].items():
        print(f"  control {label:23s} {value:8.4f}")
    if not layout["layout_holds"]:
        print(
            "the seams read no better than two scanlines 100 rows apart inside one tile, "
            "so these four slices do not abut the way their names say. The layout is "
            "wrong -- a mirrored world is worse than no world. Refusing to write."
        )
        return 4

    sheet = image_mod.new("RGBA", (SHEET_PX, SHEET_PX))
    for name in SLICES:
        col, row = (int(v) for v in name.split("_")[1].split("-"))
        sheet.paste(tiles[name], (col * TILE_PX, row * TILE_PX))
    tiles.clear()

    # The slices carry an alpha channel; whether it says anything is a measurement, not an
    # assumption. Uniformly opaque alpha is a third of the file for nothing.
    alpha_min, alpha_max = sheet.getextrema()[3]
    if alpha_min == 255:
        sheet = sheet.convert("RGB")
        alpha_note = "alpha was 255 everywhere and was dropped; the PNG is RGB"
    else:
        alpha_note = f"alpha varies ({alpha_min}..{alpha_max}) and is kept; the PNG is RGBA"
    print(f"  {alpha_note}")

    calibration = calibrate(sheet, image_mod, BOUNDS_M)
    if "skipped" in calibration:
        print(f"  calibration skipped: {calibration['skipped']}")
    else:
        print(
            f"  calibration: {calibration['nodes_on_open_ocean_at_the_pin']} of "
            f"{calibration['nodes_projected']} nodes stand on open ocean at the pin; best "
            f"shift over {calibration['sweep']} is "
            f"{calibration['best_shift_m']['dx']:+d}, {calibration['best_shift_m']['dy']:+d} m "
            f"at {calibration['nodes_on_open_ocean_at_the_best_shift']} -- the pin holds to "
            f"{calibration['accuracy_m']} m"
        )
        if not calibration["pin_holds"]:
            print(
                f"  WARNING: shifting the whole box by "
                f"{calibration['pin_agrees_within_m']} m draws the world better than the "
                "pinned corners, which is more than this sweep's own resolution. The map "
                "moved, or the node table did. The picture is still written -- it is the "
                "corners that are in question -- and _meta.calibration says so."
            )

    if args.size != SHEET_PX:
        sheet = sheet.resize((args.size, args.size), image_mod.LANCZOS)

    out_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(image_path, format="PNG", optimize=True)
    written = image_path.stat().st_size
    print(f"wrote {image_path}  {args.size}x{args.size}  {written} B  ({written / 1e6:.1f} MB)")

    # The enhanced levels are cut from the sheet at its own resolution -- which the
    # preflight above already guaranteed by refusing --enhance together with --size.
    work = out_dir / ENHANCE_WORK
    enhance = None
    if upscaler is not None:

        def run_enhance(staging: Path) -> dict:
            if work.exists():
                shutil.rmtree(work)
            try:
                return enhance_levels(sheet, image_mod, staging, upscaler, work)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        enhance = run_enhance

    try:
        tiles = install_pyramid(sheet, image_mod, out_dir, enhance=enhance)
    except MissingUpscaler as exc:
        print(exc)
        return 6
    tiles["game_version_pinned"] = build_pin
    print(
        f"wrote {out_dir / TILES_DIR_NAME}  {tiles['count']} tiles over z0..z{tiles['max_z']}  "
        f"{tiles['bytes']} B  ({tiles['bytes'] / 1e6:.1f} MB)"
    )

    image = {
        "file": IMAGE_NAME,
        "width_px": args.size,
        "height_px": args.size,
        "bytes": written,
        "mode": sheet.mode,
        "source_resolution_px": SHEET_PX,
        "downscale": (
            "none; this is the game's own resolution"
            if args.size == SHEET_PX
            else f"Lanczos, {SHEET_PX} -> {args.size}"
        ),
        "alpha": alpha_note,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size, 4),
    }
    integrity = {
        "ubulk_bytes_expected": UBULK_BYTES,
        "mip_chain": [f"{px}x{px}: {size} B" for px, size in MIP_SIZES],
        "mip0_bytes": MIP0_BYTES,
        "role": (
            "every slice's .ubulk is exactly this long, so the length is a free check that "
            "the texture still has the size and mip count this file knows how to read. Mip "
            "0 is then the first mip0_bytes with no offset to guess. A different length "
            "means the game changed and the run stops."
        ),
    }
    sidecar = build_sidecar(
        build_pin=build_pin,
        build_raw=build_raw,
        image=image,
        integrity=integrity,
        layout=layout,
        calibration=calibration,
        versions=versions,
        tiles=tiles,
    )
    sidecar_path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    print(f"wrote {sidecar_path}  {sidecar_path.stat().st_size} B")
    print(
        "  pinned at x [{x_min_m:.0f}, {x_max_m:.0f}] y [{y_min_m:.0f}, {y_max_m:.0f}] m".format(
            **BOUNDS_M
        )
    )
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
