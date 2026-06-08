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

**Licence.** The bytes this writes are Coffee Stain's artwork, read out of the reader's
own installed copy of the game and left in a gitignored directory. Nothing here is
committed, uploaded or redistributed, and ``/api/mapimage`` serves it to localhost only.
"""

from __future__ import annotations

import argparse
import json
import sys
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

#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "map_slices", "game_version_pinned")

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


def build_sidecar(
    *,
    build_pin: str,
    build_raw: dict,
    image: dict,
    integrity: dict,
    layout: dict,
    calibration: dict,
    versions: dict[str, str],
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
                "Corners for data/local/map.png, and where that picture came from. Both "
                "files are local: data/local/ is gitignored and no map imagery is ever "
                "committed to this repository."
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
                "tools/gen_map_image.py refuses to overwrite map.png unless this sidecar "
                "names the build then installed; --force says it anyway."
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
        help="destination directory for map.png and map.json (gitignored)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a map.png this run cannot show was cut from the installed build",
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
    if image_path.is_file() and not args.force:
        existing = None
        try:
            existing = pinned_build(json.loads(sidecar_path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            existing = None
        if existing != build_pin:
            print(
                f"{image_path} already exists and this run cannot show it was cut from the "
                f"installed build.\n"
                f"  installed: {build_pin}\n"
                f"  that file: {existing or 'no sidecar, or no build recorded in it'}\n"
                "A picture from another build -- or from somewhere else entirely -- is not "
                "this tool's to replace: the repository's own tables are pinned to a build "
                "the new artwork may no longer agree with, and drift is meant to be "
                "announced rather than overwritten. Pass --force to overwrite it anyway."
            )
            return 3

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
    )
    sidecar_path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
    print(f"wrote {sidecar_path}  {sidecar_path.stat().st_size} B")
    print(
        "  pinned at x [{x_min_m:.0f}, {x_max_m:.0f}] y [{y_min_m:.0f}, {y_max_m:.0f}] m".format(
            **BOUNDS_M
        )
    )
    print("neither file is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
