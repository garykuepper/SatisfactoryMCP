"""Draw two base-map layers of this world out of the 1 m heightfield and the game's biomes.

    uv run python tools/gen_map_renders.py --pyooz-path <dir containing ooz and PIL>

``tools/gen_map_image.py`` cuts the game's own drawn map into ``data/local/tiles/``. That
picture is Coffee Stain's artwork and it is the only base layer the page has ever had. This
file adds two more, drawn here rather than found:

* **terrain** -- a hypsometric ramp under a north-west hillshade, water tinted by its own
  depth. Cartography, not photography: the colour is the height and nothing else.
* **satellite** -- the same relief lit the same way, but coloured from the game's own
  per-pixel biome raster through a palette designed to look like imagery, with bare rock on
  the steep ground, sun-bleached tops on the high plateaus and a little noise so the flats
  are not vector-flat.

Both are 8192x8192 on the **same frame as the artwork sheet** -- x [-3247, 4253] m,
y [-3750, 3750] m -- and cut into the same 256 px pyramid, so the page's existing tile grid,
its CRS and its bounds are untouched and a layer is a change of picture and nothing else.

Why a new file rather than a stage on gen_world_heightmap.py
------------------------------------------------------------
The heightmap generator's job is to get a field **out of the game**: it sweeps cooked
packages, rasterises collision meshes and validates the result against 626 nodes. This file
reads that finished field back through the same public codec any other consumer would, adds
a second input the heightmap generator has never heard of, and writes pictures. The two
share an input and nothing else -- no stage, no constant, no intermediate array -- so
bolting this on would have coupled a six-minute extraction to a four-minute render and given
one ``--force`` two meanings. What IS shared is shared by import: the codec comes from
``satisfactory_mcp.domain.spatial.heightfield`` and the pyramid cutter, its staging dance and
its refusals come from ``tools/gen_map_image.py``, because a tile layout with two
implementations is a tile layout with two opinions.

The biome raster, and how its corners were found
------------------------------------------------
``/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel``
is a ``FGMapAreaTexture``: ``mDataWidth`` 4096, ``mAreaData`` 4096*4096 palette indices, and
``mColorToArea`` naming each index's ``UFGMapArea`` object. 37 indices resolve to 17 distinct
areas plus ``Area_NoMansLand``, which is what the game calls everything it does not name.
This is first-party biome geometry, and its existence contradicts
``data/region_names.json``'s own note that "the game ships no biome geometry" -- that file's
30x30 grid is a hand trace of a wiki image, and this is the thing it was tracing.

Nothing in the asset says where those 4096 texels go, so the corners are **measured**.
``calibrate_biome`` scores a pin by the artwork sheet's own edge strength averaged over the
biome raster's boundary texels, divided by its edge strength everywhere: an area boundary
that is pinned right sits on a shore or a cliff the map draws, and one that is pinned wrong
sits on flat fill. It is a ratio, so it does not reward a pin for merely making more
boundary. Measured on the shipped sheet the in-game map square reads **2.28** and every
neighbour is far below it -- 1.42 at 5% larger, 1.40 at 5% smaller, and the best of a
+-600 m translation sweep at true scale is the pin itself. So the biome raster spans exactly
the square the artwork does, 4096 texels over 7500 m, 1.831 m to the texel, row 0 north.

That is the sharp measurement. The one the reader can check by name is
``agree_with_region_grid``: the 768 non-void cells of ``data/region_names.json``, which is an
INDEPENDENT source -- traced off the wiki's biome map, good to about one 256 m cell -- looked
up in the raster. 62.6% of them land on a named game area at all; the rest are the outer
coast, which the wiki names and the game leaves as no-man's-land. Of the cells that DO land
on a named area and whose wiki region has a one-to-one counterpart among the game's, 68%
agree. The residual is not noise and is not drift: it is one disagreement, Spire Coast, where
the wiki draws a coastal ring the game divides differently. Both numbers are recorded every
run, and neither of them is what pins the corners -- a 68% agreement could not tell 100 m
from 400 m, and the edge ratio can.

The satellite palette is designed, not borrowed
-----------------------------------------------
``mColorPalette`` ships 37 RGBA entries and they are the game's UI colours -- flat primaries
for a minimap legend, cyan and magenta and pure white. Reading them into a satellite render
would produce a highlighter drawing of a world, so they are ignored: ``BIOME_COLOURS`` is a
table written by eye against crops of this world, one colour per named area, sand through
deep canopy. The palette in the asset is still decoded and recorded in ``_meta`` so a reader
can see what was there and what was done instead.

What it writes
--------------
``data/local/renders/{terrain,satellite}/``, each holding ``tiles/{z}/{x}_{y}.png`` for
z0..z5 and a ``meta.json`` the web API reads. **z5 is 8192 px and there is no z6.** The
artwork pyramid can invent two more levels with an upscaler because a drawn map has strokes
an upscaler understands; these layers' truth ends at the 1 m field they are sampled from, and
a z6 here would be an interpolation claiming to be terrain.

Everything is under ``data/local/``, which is gitignored, and nothing here is ever committed.

Staleness
---------
Two pins, both refused on rather than overwritten. The field's own ``meta.json`` names the
build it was cut from, and this run records that same string; a re-run over renders whose
sidecar names a different field build stops. And a field that is not there at all is not an
error to work around -- it is the one input this file cannot invent, so the run says which
tool writes it and exits. ``--force`` says it anyway.

The side venv
-------------
``pyooz`` (GPL-3.0) opens the container's Oodle blocks and Pillow writes the PNGs. Neither is
a dependency of this project -- both are offline generation-time tools, never imported from
``src/`` or ``sidecar/``, and no part of either is in the output -- so both come off
``--pyooz-path``, the same argument and the same posture as ``tools/gen_map_image.py``::

    uv venv <tmp>/renderenv
    uv pip install --python <tmp>/renderenv pyooz pillow
    uv run python tools/gen_map_renders.py --pyooz-path <tmp>/renderenv/Lib/site-packages

numpy and scipy ARE dependencies of this project and are imported at the top of this file,
before ``--pyooz-path`` touches ``sys.path``, so a side venv that happened to carry its own
numpy cannot win the import and leave scipy compiled against the other one.

Licence
-------
The heightfield and the biome raster are both derived from Coffee Stain's cooked assets, read
out of the reader's own installed copy of the game. The colours are this file's. Nothing is
committed, uploaded or redistributed, and the server serves it to localhost only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Imported before --pyooz-path touches sys.path: see the module docstring.
import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.domain.spatial import heightfield as hf


def load_pyramid_cutter():
    """``tools/gen_map_image.py``, imported by path -- ``tools/`` is not a package.

    The pyramid layout, the staging rename and the level arithmetic all live there already.
    A second copy of any of them is a second thing that can disagree with the endpoint that
    serves the tree, so this file borrows rather than repeats. That module imports nothing
    heavier than the standard library at module scope, which is why this can be a plain
    module-level call rather than the deferred one ``gen_map_image`` itself uses for the
    container reader.
    """
    path = Path(__file__).resolve().parent / "gen_map_image.py"
    spec = importlib.util.spec_from_file_location("gen_map_image", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("gen_map_image", module)
    spec.loader.exec_module(module)
    return module


gmi = load_pyramid_cutter()

#: Where Steam puts the game. Overridable; the biome texture is the only thing read from it.
DEFAULT_GAME = gmi.DEFAULT_GAME

#: The corners every layer is drawn on: the in-game map square, taken from the tool that
#: measured it rather than typed again, so the three pyramids cannot drift apart.
BOUNDS_M = gmi.BOUNDS_M

#: One layer's sheet, and the pyramid cut from it. Both the artwork's, for the same reason:
#: a page that can switch base layers must not have to reconfigure its tile grid to do it.
SHEET_PX = gmi.SHEET_PX
PYRAMID_TILE_PX = gmi.PYRAMID_TILE_PX

#: Where the layers go, and what each one's sidecar is called. ``renders/<layer>/`` holds a
#: ``tiles/`` tree of exactly the shape ``data/local/tiles/`` has, so the endpoint that
#: serves one serves the others with a directory swapped and nothing else.
LOCAL_DIR = ROOT / "data" / "local"
RENDERS_DIR_NAME = "renders"
RENDER_SIDECAR_NAME = "meta.json"

#: The layers this file draws. Order is the order they are cut in, which is the order the
#: run prints; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite")

#: Which recipe drew the pixels. Recorded per layer, so a reader looking at a tile can find
#: out which set of rules made it, and a later recipe over an earlier one is an upgrade a
#: run performs rather than announces.
RECIPES = {
    1: (
        "terrain: hypsometric ramp over the 1st..99.5th height percentile, NW hillshade at "
        "45 deg, water tinted by depth. satellite: biome palette, slope-driven rock, "
        "elevation lightening, two octaves of noise, the same hillshade and water"
    ),
}
RECIPE = 1

# --------------------------------------------------------------------------------------
# The biome raster.
# --------------------------------------------------------------------------------------

#: The asset, mount-relative, inside FactoryGame-Windows.utoc.
BIOME_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Minimap/"
    "MapAreaPersistenLevel/MapareatexturePersistentLevel.uasset"
)

#: What the asset has to be for this file to know how to read it. ``mDataWidth`` is in the
#: asset and is checked against this rather than trusted from it: a re-cooked texture at
#: another size is the game changing, and the run stops instead of decoding whatever is
#: there -- the same posture as ``gen_map_image``'s ``.ubulk`` length check.
BIOME_TEXELS = 4096

#: The properties this file reads out of the export, and the class it expects to find them on.
BIOME_CLASS = "/Script/FactoryGame.FGMapAreaTexture"
BIOME_PROPS = ("mAreaData", "mColorPalette", "mColorToArea", "mDataWidth")

#: What ``mColorToArea``'s entries are called. Each is a tagged-property struct: the area
#: object, then the bounding box of that index in texel coordinates.
BIOME_ENTRY_FIELDS = ("MapArea", "MinX", "MinY", "MaxX", "MaxY")

#: The area every index that names nothing resolves to. Not "unknown": the game has an
#: object for it, and it means the outer coast and the ocean past it.
NO_MANS_LAND = "Area_NoMansLand"

# --------------------------------------------------------------------------------------
# The calibration. See the module docstring for what these numbers bought.
# --------------------------------------------------------------------------------------

#: The sheet the pin is scored against, and the resolution the scoring runs at. The artwork
#: is the only picture in this repository whose corners have already been measured, which is
#: what makes it the ruler here rather than another thing to calibrate.
CALIBRATION_PX = 1024
CALIBRATION_SHIFT_M = 600
CALIBRATION_STEP_M = 200
CALIBRATION_SCALES = (0.95, 1.05)

#: How much better than its neighbours the pin has to read before this file believes it.
#: 1.15 is well inside the measured gap -- 2.28 against 1.42 -- and well outside the noise.
CALIBRATION_MARGIN = 1.15

#: The hand-traced grid the agreement is reported against, and the pairs of names that mean
#: the same place in both. Deliberately not all of them: the file has two crater regions and
#: the game one area, and the game has a Savanna the wiki does not, so those cannot vote.
REGION_TABLE = ROOT / "data" / "region_names.json"
REGION_PAIRS = {
    "Abyss Cliffs": "Area_AbyssCliffs",
    "Desert Canyons": "Area_DesertCanyons",
    "Dune Desert": "Area_DuneDesert",
    "Grass Fields": "Area_GrassFields",
    "Lake Forest": "Area_LakeForest",
    "Maze Canyons": "Area_MazeCanyons",
    "Northern Forest": "Area_NorthernForest",
    "Red Bamboo Fields": "Area_RedBambooFields",
    "Red Jungle": "Area_RedJungle",
    "Rocky Desert": "Area_RockyDesert",
    "Southern Forest": "Area_SouthernForest",
    "Spire Coast": "Area_SpireCoast",
    "Swamp": "Area_Swamp",
    "Titan Forest": "Area_TitanForest",
    "Western Dune Forest": "Area_WesternDuneForest",
}

# --------------------------------------------------------------------------------------
# The shading both layers share.
# --------------------------------------------------------------------------------------

#: The sun: north-west at 45 degrees, which is the convention every relief map uses and the
#: one the approved preview was drawn with. Anything else and the reader's eye inverts the
#: valleys.
SUN_AZIMUTH_DEG = 315.0
SUN_ALTITUDE_DEG = 45.0

#: How much of the picture the hillshade is allowed to be. A shade of 0 would be black
#: ground; the ramp keeps a fully shadowed slope at 45% of its own colour, which is dark
#: enough to read as shadow and light enough that the colour underneath still says something.
SHADE_FLOOR = 0.45
SHADE_RANGE = 0.55

#: The height band the hypsometric ramp is stretched over, as percentiles of the land. Not
#: min and max: a single 400 m spire would flatten the ramp over the whole rest of the world.
RAMP_LO_PCT = 1.0
RAMP_HI_PCT = 99.5

#: The ramp itself: dark green lowland, olive, tan, rock, snow. Straight off the preview the
#: owner approved, and deliberately not re-tuned -- this is the picture that was liked.
RAMP_STOPS = np.array(
    [
        [46, 74, 44],
        [86, 116, 56],
        [140, 148, 78],
        [176, 152, 108],
        [190, 170, 150],
        [226, 226, 226],
        [255, 255, 255],
    ],
    np.float32,
)

#: Water, tinted by how deep it is: shallow reads pale and green, deep reads dark blue. The
#: depth is clipped at this many metres, past which more depth is not more colour.
WATER_DEPTH_FULL_M = 40.0
WATER_SHALLOW = np.array([100, 190, 230], np.float32)
WATER_DEEP = np.array([40, 110, 170], np.float32)

#: How deep the water has to be before it is drawn as water and nothing else. Under this
#: the two are mixed, which is what keeps a shoreline from being a staircase: the field is
#: a 1 m grid and "is this texel under water" is a step function on it, so a hard test draws
#: every coast as 1 m blocks. A metre of feather costs nothing anybody can see on a lake and
#: turns those blocks into a beach.
WATER_EDGE_M = 0.9

#: And the same edge softened in SPACE as well as in depth, in output pixels. The depth
#: feather above does nothing where the shore is a cliff -- the water goes from nothing to
#: metres deep across one texel and there is no band to blend over -- and much of this
#: world's water sits in box-shaped bodies against exactly that. A sub-pixel blur of the
#: coverage is what antialiases those, and it touches nothing else: a pixel two from the
#: edge is fully water or fully ground either way.
WATER_EDGE_PX = 0.8

#: How much the hillshade is allowed to touch water. Some, because a lake surface that
#: ignores the light sits on the picture rather than in it; not much, because the shading is
#: computed from the ground under the water and that is not what a lake looks like.
WATER_SHADE_FLOOR = 0.75
WATER_SHADE_RANGE = 0.25

#: No data: the page's own ``--sea``. The map's edge is the one place the render must say
#: nothing rather than guess, and saying it in the colour the page is already painted means
#: the tile disappears into the background instead of drawing a border nobody asked for.
SEA_RGB = np.array([16, 32, 44], np.float32)

# --------------------------------------------------------------------------------------
# The satellite layer's own rules.
# --------------------------------------------------------------------------------------

#: One colour per named area, chosen by eye against crops of this world. NOT the asset's own
#: ``mColorPalette``, which is a UI legend: flat primaries, cyan, magenta, pure white. See
#: the module docstring.
#:
#: The scheme is a satellite one rather than a map one -- everything is desaturated, nothing
#: is brighter than about 220, and the greens run from bleached olive on the dry forests to
#: near-black on the canopy that actually is near-black from above.
BIOME_COLOURS = {
    "Area_DuneDesert": (200, 178, 138),
    "Area_RockyDesert": (166, 138, 102),
    "Area_DesertCanyons": (150, 114, 84),
    "Area_MazeCanyons": (142, 126, 106),
    "Area_AbyssCliffs": (112, 108, 102),
    "Area_crater": (124, 128, 114),
    "Area_Savanna": (148, 142, 94),
    "Area_GrassFields": (110, 128, 76),
    "Area_SpireCoast": (124, 132, 102),
    "Area_LakeForest": (68, 94, 62),
    "Area_NorthernForest": (58, 82, 52),
    "Area_SouthernForest": (62, 86, 54),
    "Area_TitanForest": (46, 68, 46),
    "Area_WesternDuneForest": (120, 124, 84),
    "Area_RedBambooFields": (124, 92, 62),
    "Area_RedJungle": (102, 78, 54),
    "Area_Swamp": (74, 84, 56),
}

#: How far a biome's colour is allowed to bleed into its neighbour's, in texels of the
#: raster (1.83 m each). The game's areas are polygons with hard edges -- they exist to name
#: a place on a minimap, not to draw one -- and nothing in an aerial photograph has a hard
#: edge, so the colour field is blurred before it is drawn. 24 texels is about 44 m, which is
#: a tree line's worth of transition: enough that no boundary reads as a line, small enough
#: that a 300 m biome is still its own colour in the middle.
BIOME_BLEND_TEXELS = 24.0

#: What the outer coast is drawn as. The game names no biome there, so neither does this: a
#: neutral bleached ground that reads as beach and shelf and lets the relief carry it.
NO_MANS_LAND_RGB = (124, 122, 108)

#: And what an index resolves to that this file has never heard of -- a new area in a later
#: build. Deliberately the same neutral rather than a guessed green: an unrecognised biome
#: should look unremarkable, not wrong.
UNKNOWN_BIOME_RGB = NO_MANS_LAND_RGB

#: Bare rock, and the slope band over which the biome's own colour gives way to it. Below
#: ROCK_LO_DEG nothing is exposed; above ROCK_HI_DEG the ground is rock whatever grows near
#: it, which is what a cliff face looks like from above in every real image of one.
ROCK_RGB = np.array([132, 122, 108], np.float32)
ROCK_LO_DEG = 20.0
ROCK_HI_DEG = 42.0

#: The high ground: sun-bleached, thin soil, and pale in imagery. Blended in linearly over
#: this band of metres, to at most HIGH_LIFT of the way to HIGH_RGB.
HIGH_RGB = np.array([206, 200, 184], np.float32)
HIGH_LO_M = 340.0
HIGH_HI_M = 580.0
HIGH_LIFT = 0.20

#: Water, seen from above rather than drawn on a map: dark, desaturated, green in the
#: shallows and near-black in the deep. Its own pair rather than the terrain layer's, which
#: is a cartographer's blue and belongs to the layer that is a cartographer's map.
SATELLITE_WATER_SHALLOW = np.array([76, 108, 104], np.float32)
SATELLITE_WATER_DEEP = np.array([22, 44, 62], np.float32)

#: Two octaves of value noise, so a flat biome is not a flat fill. Sampled bilinearly out of
#: two small fixed-seed fields rather than generated per band, which is what keeps a band
#: boundary from being visible: the noise is a property of the world position, not of the
#: order the rows happened to be drawn in.
NOISE_SEED = 20260731
NOISE_OCTAVES = ((256, 0.055), (1024, 0.035))
NOISE_SMOOTH = 1.0

#: Rows of the output drawn at a time. 256 rows of 8192 costs about 30 MB of float32 per
#: intermediate, which is the point: the whole sheet at once would be 268 MB apiece.
BAND_ROWS = 256

#: The halo each band is computed with, so the hillshade's gradient at a band edge sees the
#: rows on the other side of it -- and so does the water edge's blur, which reaches further
#: than the gradient does. Cropped off before the band is stored, so no pixel of the output
#: was ever computed from a one-sided difference or a truncated kernel.
BAND_HALO = 4


class MissingImaging(RuntimeError):
    """No Pillow: a setup problem, not a bug."""


class MissingField(RuntimeError):
    """No heightfield. The one input this file cannot invent, and it says who writes it."""


def load_imaging(extra_path: Path | None):
    """Pillow, out of the same throwaway venv as ``ooz``. Same posture as next door."""
    if extra_path is not None:
        sys.path.insert(0, str(extra_path))
    try:
        from PIL import Image
    except ImportError as exc:
        raise MissingImaging(
            "no Pillow importable, so the renders cannot be written. This is deliberate: it "
            "is an offline tool here, never a dependency of this project. Do:\n"
            "    uv venv <tmp>/renderenv\n"
            "    uv pip install --python <tmp>/renderenv pyooz pillow\n"
            "    uv run python tools/gen_map_renders.py "
            "--pyooz-path <tmp>/renderenv/Lib/site-packages"
        ) from exc
    Image.MAX_IMAGE_PIXELS = None
    try:
        from importlib.metadata import version

        pillow = version("pillow")
    except Exception:
        pillow = "unknown"
    return Image, pillow


# --------------------------------------------------------------------------------------
# Reading the biome raster out of the container.
# --------------------------------------------------------------------------------------


def read_biome(gwc, store, scripts) -> dict:
    """The 4096x4096 biome raster, its palette, and what each index is called.

    Every check here is the same kind of check ``gen_map_image`` makes on the map slices'
    ``.ubulk`` length: a shape that is not the one this file knows how to read means the
    asset was re-cooked, i.e. the game changed, and a raster reshaped into whatever fits is
    worse than no raster at all.
    """
    if BIOME_PATH not in store.by_path:
        raise SystemExit(
            f"{BIOME_PATH} is not in the container. The biome texture moved or was renamed, "
            "which means the game changed; the satellite layer has no other source for what "
            "grows where, and nothing here can be trusted until that is looked at."
        )
    view = gwc.PackageView(store.read_path(BIOME_PATH), scripts)
    export = next(
        (e for e in view.exports if (view.class_of[e["slot"]] or "") == BIOME_CLASS), None
    )
    if export is None:
        found = ", ".join(sorted({str(view.class_of[e["slot"]]) for e in view.exports})) or "none"
        raise SystemExit(
            f"{BIOME_PATH} has no {BIOME_CLASS} export (found: {found}). The asset is no "
            "longer the class this file knows how to read."
        )
    props = view.props(export["slot"])
    missing = [name for name in BIOME_PROPS if name not in props]
    if missing:
        raise SystemExit(
            f"{BIOME_CLASS} is missing {', '.join(missing)} -- the properties this file "
            "reads. The class changed shape; refusing to guess at the rest."
        )

    width = struct.unpack("<i", props["mDataWidth"])[0]
    if width != BIOME_TEXELS:
        raise SystemExit(
            f"mDataWidth is {width}, not {BIOME_TEXELS}. The biome texture was re-cooked at "
            "another size, so every corner this file measured is measured against a "
            "different picture. Refusing to draw it."
        )
    raw = props["mAreaData"]
    count = struct.unpack_from("<i", raw, 0)[0]
    if count != width * width or len(raw) != 4 + width * width:
        raise SystemExit(
            f"mAreaData says {count} texels in {len(raw)} bytes, but a {width}x{width} array "
            f"of palette indices is {width * width} in {4 + width * width}. The array is not "
            "the shape its own width says."
        )
    area = np.frombuffer(raw, dtype=np.uint8, count=width * width, offset=4).reshape(width, width)

    palette_raw = props["mColorPalette"]
    entries = struct.unpack_from("<i", palette_raw, 0)[0]
    palette = [
        tuple(palette_raw[4 + i * 4 : 8 + i * 4]) for i in range(entries)
    ]  # RGBA, the game's UI legend -- decoded for the record, never drawn

    names = decode_colour_to_area(gwc, view, props["mColorToArea"])
    if len(names) != entries:
        raise SystemExit(
            f"mColorPalette has {entries} entries and mColorToArea {len(names)}. The two "
            "halves of one lookup disagree about how many indices there are."
        )
    used = int(area.max()) + 1
    if used > entries:
        raise SystemExit(
            f"the raster uses index {used - 1} but the palette stops at {entries - 1}. "
            "Refusing to draw a texel whose area has no name."
        )
    return {
        "width": width,
        "area": area,
        "palette": palette,
        "names": names,
        "distinct_areas": sorted({n for n in names if n and n != NO_MANS_LAND}),
    }


def decode_colour_to_area(gwc, view, blob: bytes) -> list[str | None]:
    """``mColorToArea`` -> the ``Area_*`` leaf name of each palette index.

    A ``TArray<FStruct>`` in Zen's tagged form is the count and then one property stream per
    element, which is why the walk carries its own cursor rather than slicing: the elements
    are not a fixed width and the only thing that knows where one ends is the parser that
    read it.
    """
    count = struct.unpack_from("<i", blob, 0)[0]
    out: list[str | None] = []
    pos = 4
    for _ in range(count):
        tags, end = gwc.property_tags(blob, view.pkg.names, pos)
        fields = {name: payload for name, _kind, payload, _value in tags}
        if "MapArea" not in fields:
            raise SystemExit(
                "an mColorToArea entry carries no MapArea reference -- the struct this file "
                f"reads is {', '.join(BIOME_ENTRY_FIELDS)} and it is no longer that"
            )
        path = view.import_path(fields["MapArea"])
        out.append(path.rsplit("/", 1)[-1] if path else None)
        pos = end
    return out


def biome_colour_field(biome: dict, table: np.ndarray) -> np.ndarray:
    """The raster's indices turned into colour, then blurred so no boundary is a line.

    Done once at the raster's own 4096, one channel at a time -- a Gaussian over three
    float32 channels of that square at once is 200 MB held for no reason -- and kept as
    uint8, which is 50 MB and all the precision a colour has anyway.

    The blur is what makes this a picture rather than a choropleth. The game's areas are
    minimap polygons: they meet along a mathematical line, and drawn straight that line is
    the most conspicuous thing on the render and the one thing no aerial photograph has.
    """
    field = np.empty(biome["area"].shape + (3,), np.uint8)
    for channel in range(3):
        blurred = ndimage.gaussian_filter(
            table[:, channel][biome["area"]], BIOME_BLEND_TEXELS, mode="nearest"
        )
        field[..., channel] = np.clip(blurred, 0, 255).astype(np.uint8)
    return field


def biome_lookup(biome: dict) -> tuple[np.ndarray, list[str]]:
    """Per palette index: its RGB in the designed palette, and the name it was drawn as.

    Returned together because a reader checking the render against the table needs both, and
    because building them apart is how they come to disagree.
    """
    rgb = np.zeros((len(biome["names"]), 3), np.float32)
    drawn = []
    for index, name in enumerate(biome["names"]):
        if name in BIOME_COLOURS:
            colour = BIOME_COLOURS[name]
            drawn.append(name)
        elif name == NO_MANS_LAND or name is None:
            colour = NO_MANS_LAND_RGB
            drawn.append(NO_MANS_LAND)
        else:
            colour = UNKNOWN_BIOME_RGB
            drawn.append(f"{name} (no colour in this file's table)")
        rgb[index] = colour
    return rgb, drawn


# --------------------------------------------------------------------------------------
# Calibration: where do the biome raster's 4096 texels go?
# --------------------------------------------------------------------------------------


def boundary_mask(labels: np.ndarray) -> np.ndarray:
    """Where one area meets another, grown by one so a one-pixel drift still overlaps."""
    edge = np.zeros(labels.shape, bool)
    edge[:-1, :] |= labels[:-1, :] != labels[1:, :]
    edge[:, :-1] |= labels[:, :-1] != labels[:, 1:]
    return ndimage.binary_dilation(edge)


def sample_area(area: np.ndarray, box: tuple[float, float, float, float], size: int) -> np.ndarray:
    """The raster resampled onto a ``size`` square over the artwork frame, given a pin.

    ``box`` is the pin under test -- where the raster's own corners are being supposed to be
    -- while the output grid is always the artwork square, because that is the frame the
    ruler is in.
    """
    x0, x1, y0, y1 = box
    ax = BOUNDS_M["x_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    ay = BOUNDS_M["y_min_m"] * 100 + (np.arange(size) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    width = area.shape[0]
    u = ((ax[None, :] - x0) / (x1 - x0) * width).astype(np.int64)
    v = ((ay[:, None] - y0) / (y1 - y0) * width).astype(np.int64)
    u = np.broadcast_to(u, (size, size))
    v = np.broadcast_to(v, (size, size))
    inside = (u >= 0) & (u < width) & (v >= 0) & (v < width)
    out = np.full((size, size), 255, np.uint8)
    out[inside] = area[np.clip(v, 0, width - 1)[inside], np.clip(u, 0, width - 1)[inside]]
    return out


def pinned_box(dx_cm: float, dy_cm: float, scale: float) -> tuple[float, float, float, float]:
    """The artwork square, shifted and scaled about its own centre."""
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = (x1 - x0) / 2 * scale
    return (cx + dx_cm - half, cx + dx_cm + half, cy + dy_cm - half, cy + dy_cm + half)


def calibrate_biome(biome: dict, sheet_path: Path, image_mod) -> dict:
    """Score the pin by the artwork's own edges, and sweep for one that beats it.

    The statistic is the ratio of the sheet's mean edge strength ON the biome raster's area
    boundaries to its mean edge strength everywhere. A boundary that is pinned right lies on
    a shore or a scarp the map draws; one that is pinned wrong lies on flat fill. Being a
    ratio, it cannot be won by a pin that simply produces more boundary.

    Skipped, not faked, when the artwork sheet is absent: it is the only picture here whose
    corners have already been measured, and without a ruler there is no measurement.
    """
    if not sheet_path.is_file():
        return {
            "skipped": (
                f"{sheet_path} is not present, so the pin could not be scored. Run "
                "tools/gen_map_image.py to cut the artwork sheet, then this again; the "
                "renders are still drawn at the pin below, which is what that sheet measured."
            ),
            "pin": dict(BOUNDS_M),
        }
    grey = np.asarray(
        image_mod.open(sheet_path)
        .convert("L")
        .resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS),
        np.float32,
    )
    gy, gx = np.gradient(ndimage.gaussian_filter(grey, 1.0))
    edge = np.hypot(gx, gy)
    everywhere = float(edge.mean())

    def ratio(dx_cm: float, dy_cm: float, scale: float) -> float:
        labels = sample_area(biome["area"], pinned_box(dx_cm, dy_cm, scale), CALIBRATION_PX)
        return float(edge[boundary_mask(labels)].mean() / everywhere)

    at_pin = ratio(0.0, 0.0, 1.0)
    shifts = range(
        -CALIBRATION_SHIFT_M * 100, CALIBRATION_SHIFT_M * 100 + 1, CALIBRATION_STEP_M * 100
    )
    # The pin is deliberately NOT in this maximum: what is being asked is whether anything
    # ELSE does better, so the rival set is every candidate that is not the pin itself.
    best = (0.0, 0, 0)
    for dx in shifts:
        for dy in shifts:
            if dx == 0 and dy == 0:
                continue
            score = ratio(dx, dy, 1.0)
            if score > best[0]:
                best = (score, dx, dy)
    scales = {f"x{scale:.2f}": ratio(0.0, 0.0, scale) for scale in CALIBRATION_SCALES}
    rivals = max([best[0], *scales.values()])
    return {
        "method": (
            "the artwork sheet's mean edge strength over the biome raster's area boundaries, "
            "divided by its mean edge strength everywhere. A boundary pinned right sits on a "
            "shore or a scarp the map draws; one pinned wrong sits on flat fill. A ratio, so "
            "a pin cannot win it by making more boundary."
        ),
        "ruler": str(sheet_path.relative_to(ROOT))
        if sheet_path.is_relative_to(ROOT)
        else str(sheet_path),
        "resolution_px": CALIBRATION_PX,
        "edge_ratio_at_the_pin": round(at_pin, 4),
        "sweep": (
            f"+-{CALIBRATION_SHIFT_M} m in {CALIBRATION_STEP_M} m steps at true scale, plus "
            + ", ".join(f"x{scale:.2f}" for scale in CALIBRATION_SCALES)
        ),
        "best_rival_shift_m": {"dx": best[1] / 100, "dy": best[2] / 100},
        "edge_ratio_at_the_best_rival_shift": round(best[0], 4),
        "edge_ratio_at_other_scales": {key: round(value, 4) for key, value in scales.items()},
        "margin_over_the_best_rival": round(at_pin / rivals, 4) if rivals else None,
        "margin_required": CALIBRATION_MARGIN,
        "pin_holds": at_pin >= rivals * CALIBRATION_MARGIN,
        "pin": dict(BOUNDS_M),
        "metres_per_texel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / biome["width"], 4),
        "reading": (
            "the raster spans exactly the square the artwork does. Every rival -- a shift in "
            "either direction, a box 5% larger, a box 5% smaller -- reads far below it, "
            "which is what makes this a measurement rather than a preference. pin_holds "
            "false would mean the texture moved, and the run says so instead of quietly "
            "colouring the world off its own biomes."
        ),
    }


def agree_with_region_grid(biome: dict) -> dict:
    """What the hand-traced region grid says about the raster, cell by cell.

    ``data/region_names.json`` is an independent source -- a trace of the wiki's biome map,
    good to about one 256 m cell -- so this is a check rather than a circle. It is NOT what
    pins the corners: a 68% agreement cannot tell 100 m from 400 m, and the edge ratio can.
    """
    if not REGION_TABLE.is_file():
        return {"skipped": f"{REGION_TABLE.name} is not present, so the raster is unchecked"}
    table = json.loads(REGION_TABLE.read_text(encoding="utf-8"))
    meta, grid, legend = table["grid_meta"], table["region_grid"], table["legend"]
    cell, gx0, gy0 = meta["cell"], meta["x0"], meta["y0"]
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    width = biome["width"]
    names = biome["names"]

    cells = named = comparable = agree = 0
    disagreements: dict[str, int] = {}
    for j, row in enumerate(grid):
        for i, letter in enumerate(row):
            if letter == meta["void"]:
                continue
            cells += 1
            u = [int((gx0 + (i + k) * cell - x0) / (x1 - x0) * width) for k in (0, 1)]
            v = [int((gy0 + (j + k) * cell - y0) / (y1 - y0) * width) for k in (0, 1)]
            u = [max(0, min(width, value)) for value in u]
            v = [max(0, min(width, value)) for value in v]
            if u[1] <= u[0] or v[1] <= v[0]:
                continue
            values, counts = np.unique(biome["area"][v[0] : v[1], u[0] : u[1]], return_counts=True)
            got = names[int(values[counts.argmax()])]
            if got in (None, NO_MANS_LAND):
                continue
            named += 1
            region = legend[letter]
            want = REGION_PAIRS.get(region)
            if want is None:
                continue
            comparable += 1
            if got == want:
                agree += 1
            else:
                disagreements[f"{region} -> {got}"] = disagreements.get(f"{region} -> {got}", 0) + 1
    worst = sorted(disagreements.items(), key=lambda kv: -kv[1])[:5]
    return {
        "source": "data/region_names.json, a hand trace of the wiki's biome map at 256 m",
        "cells_not_void": cells,
        "cells_on_a_named_area": named,
        "cells_on_a_named_area_pct": round(100 * named / cells, 1) if cells else None,
        "cells_comparable_by_name": comparable,
        "cells_agreeing": agree,
        "agreement_pct": round(100 * agree / comparable, 1) if comparable else None,
        "largest_disagreements": [f"{key} ({count} cells)" for key, count in worst],
        "reading": (
            "the cells that land on no named area are the outer coast, which the wiki names "
            "and the game leaves as no-man's-land -- a difference of scope, not a "
            "misalignment. Of the rest, the residual is dominated by one genuine "
            "disagreement about where a coastal ring stops. This number is reported, not "
            "optimised: the corners come from the edge ratio next door."
        ),
    }


# --------------------------------------------------------------------------------------
# Sampling the field onto the output frame.
# --------------------------------------------------------------------------------------


def frame_coordinates(size: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel-centre world coordinates, centimetres, for a ``size`` square on the frame.

    Row 0 is the northern edge and column 0 the western one, which is the artwork sheet's
    order and the heightfield's: game +Y is south, so the smallest y is the top of the
    picture. Nothing here flips anything, and that is deliberate -- a render that agreed
    with the field but disagreed with the tile grid would be upside down on the page only.
    """
    x = BOUNDS_M["x_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    )
    y = BOUNDS_M["y_min_m"] * 100 + (np.arange(size, dtype=np.float64) + 0.5) * (
        (BOUNDS_M["y_max_m"] - BOUNDS_M["y_min_m"]) * 100 / size
    )
    return x, y


def bilinear_weights(coordinate: np.ndarray, origin: float, spacing: float, limit: int):
    """The two flanking indices and the weight of the second, clamped to the grid.

    Clamped rather than masked: the frame is half a metre wider than the field's last vertex
    on two sides, and a strip of no-data half a pixel wide down the east and south edges
    would be a hole this file invented. Past the edge the nearest vertex is the honest
    answer, and it is the same answer the field's own reader gives.
    """
    position = np.clip((coordinate - origin) / spacing, 0.0, limit - 1.0)
    low = np.floor(position).astype(np.int64)
    low = np.minimum(low, limit - 2) if limit > 1 else low
    return low, np.minimum(low + 1, limit - 1), (position - low).astype(np.float32)


def sample_bilinear(raster: np.ndarray, rows, cols, nodata: int):
    """Bilinear over an int16 raster that has holes in it. Returns (values, all-four-missing).

    A no-data texel is given zero weight rather than a value, and the remaining weights are
    renormalised, so a pixel beside a hole is the average of the neighbours that DO know
    rather than an average dragged toward -32768. Only where all four are missing does the
    render have nothing to say, and that is the mask that comes back.
    """
    (r0, r1, fr), (c0, c1, fc) = rows, cols
    corners = np.stack(
        [
            raster[np.ix_(r0, c0)],
            raster[np.ix_(r0, c1)],
            raster[np.ix_(r1, c0)],
            raster[np.ix_(r1, c1)],
        ]
    ).astype(np.float32)
    fr = fr[:, None]
    weights = np.stack(
        [
            (1 - fr) * (1 - fc),
            (1 - fr) * fc,
            fr * (1 - fc),
            fr * fc,
        ]
    ).astype(np.float32)
    weights *= corners != nodata
    total = weights.sum(0)
    missing = total <= 0
    value = (weights * corners).sum(0) / np.where(missing, 1.0, total)
    return value, missing


def hillshade(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """North-west relief in [SHADE_FLOOR, SHADE_FLOOR + SHADE_RANGE].

    Written as a dot product against the surface normal rather than as slope-and-aspect
    trigonometry, because the array's axes are unambiguous and compass angles are not: rows
    run south, columns run east, and the sun sits north-west at 45 degrees. Getting that
    backwards inverts every valley on the map and still looks like terrain, which is exactly
    the kind of error worth writing out of reach.
    """
    azimuth = np.deg2rad(SUN_AZIMUTH_DEG)
    altitude = np.deg2rad(SUN_ALTITUDE_DEG)
    light = np.array(
        [
            np.cos(altitude) * np.sin(azimuth),  # east
            -np.cos(altitude) * np.cos(azimuth),  # south
            np.sin(altitude),  # up
        ],
        np.float32,
    )
    d_south, d_east = np.gradient(z_m, spacing_m)
    lit = (-d_east * light[0] - d_south * light[1] + light[2]) / np.sqrt(
        d_east * d_east + d_south * d_south + 1.0
    )
    return np.clip(lit, 0.0, 1.0) * SHADE_RANGE + SHADE_FLOOR


def slope_degrees(z_m: np.ndarray, spacing_m: float) -> np.ndarray:
    """How steep the ground is, in degrees. The satellite layer's rock rule reads this."""
    d_south, d_east = np.gradient(z_m, spacing_m)
    return np.degrees(np.arctan(np.hypot(d_east, d_south)))


def ramp(values: np.ndarray, stops: np.ndarray) -> np.ndarray:
    """Linear interpolation along a colour ramp, ``values`` in [0, 1]."""
    position = np.clip(values, 0.0, 1.0) * (len(stops) - 1)
    low = np.clip(np.floor(position), 0, len(stops) - 2).astype(np.int64)
    fraction = (position - low)[..., None].astype(np.float32)
    return stops[low] * (1 - fraction) + stops[low + 1] * fraction


def noise_fields(rng_seed: int) -> list[tuple[np.ndarray, float]]:
    """The value-noise octaves, made once and sampled by world position afterwards.

    Made once on purpose. Noise generated per band would put a different random field on
    either side of every band boundary and draw 31 horizontal seams across the world; this
    is one field per octave, and which rows happen to be drawn together stops mattering.
    """
    rng = np.random.default_rng(rng_seed)
    fields = []
    for size, amount in NOISE_OCTAVES:
        field = rng.standard_normal((size, size), dtype=np.float32)
        fields.append((ndimage.gaussian_filter(field, NOISE_SMOOTH, mode="wrap"), amount))
    return fields


def biome_index(coordinate: np.ndarray, lo_m: float, hi_m: float, width: int) -> np.ndarray:
    """Which biome texel a run of world coordinates falls in. Nearest, and never in between.

    Nearest rather than interpolated on purpose: an area index is a name, and the average of
    "desert" and "forest" is not a biome, it is whichever unrelated area happens to sit
    between their numbers in a palette nobody ordered.
    """
    position = (coordinate - lo_m * 100) / ((hi_m - lo_m) * 100) * width
    return np.clip(position.astype(np.int64), 0, width - 1)


def sample_noise(fields, rows: np.ndarray, cols: np.ndarray, size: int) -> np.ndarray:
    """The octaves added up at these output pixels, as a multiplier around 1."""
    out = np.ones((len(rows), len(cols)), np.float32)
    for field, amount in fields:
        side = field.shape[0]
        u = (cols.astype(np.float32) + 0.5) * side / size
        v = (rows.astype(np.float32) + 0.5) * side / size
        out += amount * field[np.ix_(v.astype(np.int64) % side, u.astype(np.int64) % side)]
    return out


# --------------------------------------------------------------------------------------
# The two layers.
# --------------------------------------------------------------------------------------


def water_alpha(z_m: np.ndarray, water_m: np.ndarray, submerged: np.ndarray) -> np.ndarray:
    """How much of each pixel is water, in [0, 1]. Feathered twice, for two reasons.

    ``submerged`` is a step function on a 1 m grid, so a hard composite draws every
    coastline as a staircase of metre blocks. The first feather is in DEPTH, which handles
    a beach: the water thins out over the last ``WATER_EDGE_M`` and the blend goes with it.
    The second is in SPACE, because a beach is not what most of this world's water has -- a
    great deal of it sits in box-shaped bodies against a cliff, where the depth goes from
    nothing to metres across one texel and there is no band for the first feather to work
    in. A sub-pixel blur is what antialiases those, and being sub-pixel it cannot move a
    shoreline, only stop it being a staircase.
    """
    alpha = np.where(submerged, np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0), 0.0)
    return ndimage.gaussian_filter(alpha, WATER_EDGE_PX, mode="nearest")


def water_over(rgb, z_m, water_m, alpha, shade, shallow, deep):
    """Lay water over ground, tinted by its own depth. Both layers want this arithmetic.

    Shallow reads pale, deep reads dark, and the light touches it a little -- but only a
    little, because the hillshade under a lake is computed from the lake BED and that is not
    what a water surface looks like from above.
    """
    depth = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)[..., None]
    colour = (shallow * (1 - depth) + deep * depth) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def terrain_colours(z_m, water_m, wet, missing, shade, ramp_lo, ramp_hi, **_unused):
    """The approved preview at full resolution: ramp, shade, water, and silence.

    Deliberately the same arithmetic as the preview the owner picked, scaled up rather than
    re-tuned. The one thing that is not the same is the resolution the hillshade is computed
    at -- 0.92 m to the pixel instead of 4 -- which is not a change of recipe, it is the
    recipe finally seeing the field it was always sampling.
    """
    height = np.clip((z_m - ramp_lo) / max(ramp_hi - ramp_lo, 1e-6), 0.0, 1.0)
    rgb = ramp(height, RAMP_STOPS) * shade[..., None]
    rgb = water_over(rgb, z_m, water_m, wet, shade, WATER_SHALLOW, WATER_DEEP)
    return np.where(missing[..., None], SEA_RGB, rgb)


def satellite_colours(z_m, water_m, wet, missing, shade, slope, biome_rgb, noise, **_unused):
    """Ground colour from the biome, then rock, then altitude, then light, then water.

    In that order, and the order is the argument. The biome says what grows there; the slope
    overrules it, because nothing grows on a cliff face and every real image of one is rock;
    the altitude bleaches what is left, because the high plateaus are thin soil and sun; the
    hillshade lights all of it at once, because a shadow falls on rock and canopy alike; and
    the water goes on top, because it is a different surface rather than a different ground.
    """
    rock = np.clip((slope - ROCK_LO_DEG) / (ROCK_HI_DEG - ROCK_LO_DEG), 0.0, 1.0)[..., None]
    rgb = biome_rgb * (1 - rock) + ROCK_RGB * rock
    lift = np.clip((z_m - HIGH_LO_M) / (HIGH_HI_M - HIGH_LO_M), 0.0, 1.0)[..., None] * HIGH_LIFT
    rgb = rgb * (1 - lift) + HIGH_RGB * lift
    rgb = rgb * noise[..., None] * shade[..., None]
    rgb = water_over(rgb, z_m, water_m, wet, shade, SATELLITE_WATER_SHALLOW, SATELLITE_WATER_DEEP)
    return np.where(missing[..., None], SEA_RGB, rgb)


LAYER_PAINTERS = {"terrain": terrain_colours, "satellite": satellite_colours}


def ramp_range(field) -> tuple[float, float]:
    """The height band the ramp is stretched over, from the field itself.

    Sampled every fourth texel in each direction: 3.5 million heights is far more than a
    percentile needs and a sixteenth of the arithmetic, and the answer moves by less than a
    decimetre either way.
    """
    sample = field._height_dm[::4, ::4]
    land = sample[sample != hf.NODATA].astype(np.float32) / hf.DM_PER_M
    return (
        float(np.percentile(land, RAMP_LO_PCT)),
        float(np.percentile(land, RAMP_HI_PCT)),
    )


def render_layer(
    layer: str, field, biome_rgb: np.ndarray | None, biome, size, progress
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Banded because the sheet is 67 million pixels and this recipe holds a dozen float32
    intermediates over it: whole-sheet arrays would be 268 MB apiece and the run would live
    or die on how much memory the reader's machine happened to have. Each band is computed
    with BAND_HALO extra rows on both sides and cropped afterwards, so the hillshade's
    gradient never sees a band edge -- a one-sided difference at every 256th row would draw
    31 horizontal lines across the world.
    """
    painter = LAYER_PAINTERS[layer]
    x_cm, y_cm = frame_coordinates(size)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    cols = bilinear_weights(x_cm, field.x0_cm, field.spacing_cm, field.width)
    biome_cols = biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome["width"])
    ramp_lo, ramp_hi = ramp_range(field)
    noise = noise_fields(NOISE_SEED) if layer == "satellite" else None
    water = field._water_raster()
    out = np.empty((size, size, 3), np.uint8)
    column_index = np.arange(size)

    started = time.time()
    for top in range(0, size, BAND_ROWS):
        bottom = min(top + BAND_ROWS, size)
        lo = max(top - BAND_HALO, 0)
        hi = min(bottom + BAND_HALO, size)
        rows = bilinear_weights(y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
        z_dm, missing = sample_bilinear(field._height_dm, rows, cols, hf.NODATA)
        z_m = z_dm / np.float32(hf.DM_PER_M)
        if water is not None:
            water_dm, dry = sample_bilinear(water, rows, cols, hf.NODATA)
            water_m = water_dm / np.float32(hf.DM_PER_M)
            submerged = ~dry & (water_m > z_m)
        else:
            water_m = z_m
            submerged = np.zeros(z_m.shape, bool)
        shade = hillshade(z_m, spacing_m)
        extra: dict = {}
        if layer == "satellite":
            extra["slope"] = slope_degrees(z_m, spacing_m)
            biome_rows = biome_index(
                y_cm[lo:hi], BOUNDS_M["y_min_m"], BOUNDS_M["y_max_m"], biome["width"]
            )
            extra["biome_rgb"] = biome_rgb[np.ix_(biome_rows, biome_cols)].astype(np.float32)
            extra["noise"] = sample_noise(noise, np.arange(lo, hi), column_index, size)
        rgb = painter(
            z_m=z_m,
            water_m=water_m,
            wet=water_alpha(z_m, water_m, submerged),
            missing=missing,
            shade=shade,
            ramp_lo=ramp_lo,
            ramp_hi=ramp_hi,
            **extra,
        )
        out[top:bottom] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
        if progress and (top // BAND_ROWS) % 8 == 0:
            done = bottom / size
            print(
                f"  {layer}: {done:5.1%} of {size}x{size} in {time.time() - started:5.1f}s",
                flush=True,
            )
    return out


# --------------------------------------------------------------------------------------
# Installing a layer.
# --------------------------------------------------------------------------------------


def layer_dir(out_dir: Path, layer: str) -> Path:
    return out_dir / RENDERS_DIR_NAME / layer


def pinned_field_build(sidecar: dict) -> str | None:
    """The heightfield build an existing layer sidecar names, or None if it names none."""
    node: object = sidecar.get("_meta")
    for key in ("sources", "heightfield", "game_version_pinned"):
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None


def build_sidecar(*, layer: str, field_meta: dict, tiles: dict, render: dict, extra: dict) -> dict:
    """The file the web API reads for this layer, plus the provenance to date it by.

    The four corner keys sit at the top exactly as ``map.json``'s do, and ``_meta.tiles``
    carries the same block, so the endpoint reads a render layer with the code it already
    had for the artwork one.
    """
    build = ((field_meta.get("sources") or {}).get("game") or {}).get("game_version_pinned")
    return {
        **BOUNDS_M,
        "_meta": {
            "description": (
                f"The {layer} base layer: a render of this world drawn from the 1 m "
                "heightfield, and the pyramid cut from it. All of it is local: data/local/ "
                "is gitignored and no map imagery is ever committed to this repository."
            ),
            "bounds": (
                "metres, game axes -- +X east, +Y south. The corners of the in-game map "
                "square, the same frame data/local/map.json pins, so a page can swap base "
                "layers without touching its tile grid."
            ),
            "generator": "tools/gen_map_renders.py",
            "layer": layer,
            "recipe": RECIPE,
            "recipe_description": RECIPES[RECIPE],
            "transcribed": datetime.now(UTC).date().isoformat(),
            "sources": {
                "heightfield": {
                    "name": f"data/local/{hf.DIR_NAME}/",
                    "generator": field_meta.get("generator"),
                    "generator_version": field_meta.get("generator_version"),
                    "grid": field_meta.get("grid"),
                    "game_version_pinned": build,
                    "role": "every pixel's height, and the relief and water on it",
                },
                **extra,
            },
            "render": render,
            "tiles": tiles,
            "staleness": (
                "sources.heightfield.game_version_pinned is the build the field under these "
                "pixels was cut from. tools/gen_map_renders.py refuses to replace this layer "
                "unless the field now on disk names the same build; --force says it anyway. "
                "Terrain moves every patch, and a render that quietly disagrees with the "
                "node tables beside it is exactly the drift this project announces."
            ),
        },
    }


def install_layer(sheet_rgb, image_mod, out_dir: Path, layer: str) -> tuple[dict, float]:
    """Cut one layer's pyramid into place, and say what it wrote and how long it took."""
    directory = layer_dir(out_dir, layer)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.time()
    stats = gmi.install_pyramid(
        image_mod.fromarray(sheet_rgb),
        image_mod,
        directory,
        source=f"tools/gen_map_renders.py, {layer} recipe {RECIPE}, Lanczos",
    )
    return stats, time.time() - started


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
            "directory holding an importable `ooz` and Pillow -- a throwaway venv's "
            "site-packages. See the module docstring for the recipe"
        ),
    )
    parser.add_argument(
        "--field",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="the heightfield directory tools/gen_world_heightmap.py wrote",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR,
        help="destination for renders/<layer>/ (gitignored)",
    )
    parser.add_argument(
        "--layer",
        action="append",
        choices=LAYERS,
        help=f"only this layer (repeatable; default all of {', '.join(LAYERS)})",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=SHEET_PX,
        choices=[SHEET_PX, SHEET_PX // 2, SHEET_PX // 4],
        help=f"square edge of each render (default {SHEET_PX}, the artwork's own)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace layers this run cannot show were drawn from the field now on disk",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-band progress lines")
    args = parser.parse_args()

    layers = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS

    try:
        image_mod, pillow_version = load_imaging(args.pyooz_path)
    except MissingImaging as exc:
        print(exc)
        return 2

    field = hf.load_field(args.field)
    if field is None:
        print(
            f"no heightfield at {args.field}. That field is the one input this file cannot "
            "invent -- every pixel of both layers is a height off it -- so there is nothing "
            "to draw. Write it first:\n"
            "    uv run python tools/gen_world_heightmap.py --pyooz-path <venv>/Lib/"
            "site-packages\n"
            "It reads your own installed game and writes to the same gitignored directory."
        )
        return 4
    field_meta = field.meta
    field_build = field.build
    print(
        f"field: {field.width}x{field.height} at {field.spacing_cm / 100:g} m, build {field_build}"
    )

    out_dir: Path = args.out_dir
    if not args.force:
        for layer in layers:
            sidecar_path = layer_dir(out_dir, layer) / RENDER_SIDECAR_NAME
            if not (layer_dir(out_dir, layer) / gmi.TILES_DIR_NAME).is_dir():
                continue
            try:
                existing = json.loads(sidecar_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                existing = {}
            pinned = pinned_field_build(existing if isinstance(existing, dict) else {})
            if pinned != field_build:
                print(
                    f"{layer_dir(out_dir, layer)} already holds a {layer} pyramid and this "
                    "run cannot show it was drawn from the field now on disk.\n"
                    f"  field on disk: {field_build}\n"
                    f"  those tiles:   {pinned or 'no meta.json, or no build recorded in it'}\n"
                    "A render from another build is a picture of another world's terrain, "
                    "and drift is announced rather than overwritten. Pass --force to "
                    "replace it anyway."
                )
                return 3

    # ---- the biome raster ------------------------------------------------------------
    biome = None
    if "satellite" in layers:
        gwc = gmi.load_container_reader()
        try:
            ooz, pyooz_version = gwc.load_oodle(args.pyooz_path)
        except gwc.MissingOodle as exc:
            print(exc)
            return 2
        paks = args.game / "FactoryGame" / "Content" / "Paks"
        if not (paks / "FactoryGame-Windows.utoc").exists():
            print(f"no FactoryGame-Windows.utoc under {paks}")
            return 1
        print(f"reading the biome raster from {paks} with pyooz {pyooz_version}")
        store = gwc.IoStore(paks, "FactoryGame-Windows", ooz.decompress)
        scripts = gwc.ScriptObjects(paks, ooz.decompress)
        biome = read_biome(gwc, store, scripts)
        print(
            f"  {biome['width']}x{biome['width']} palette indices, "
            f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
        )
        calibration = calibrate_biome(biome, out_dir / gmi.IMAGE_NAME, image_mod)
        if "skipped" in calibration:
            print(f"  calibration skipped: {calibration['skipped']}")
        else:
            print(
                f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin "
                f"against {calibration['edge_ratio_at_the_best_rival_shift']} for the best "
                f"shift and {calibration['edge_ratio_at_other_scales']} at other scales -- "
                f"margin {calibration['margin_over_the_best_rival']}x over "
                f"{calibration['sweep']}"
            )
            if not calibration["pin_holds"]:
                print(
                    "  WARNING: the pin no longer beats its rivals by the required margin. "
                    "The biome texture moved, or the artwork sheet did. The layer is still "
                    "drawn -- it is the corners that are in question -- and _meta says so."
                )
        agreement = agree_with_region_grid(biome)
        if "skipped" not in agreement:
            print(
                f"  region grid: {agreement['cells_on_a_named_area']} of "
                f"{agreement['cells_not_void']} cells land on a named area "
                f"({agreement['cells_on_a_named_area_pct']}%); of the "
                f"{agreement['cells_comparable_by_name']} comparable by name, "
                f"{agreement['cells_agreeing']} agree ({agreement['agreement_pct']}%)"
            )
        table, drawn = biome_lookup(biome)
        biome_rgb = biome_colour_field(biome, table)
        biome_source = {
            "biome_raster": {
                "name": "/Game/" + BIOME_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
                "class": BIOME_CLASS,
                "licence": (
                    "Coffee Stain Studios' own asset, read out of the reader's installed "
                    "copy of the game. Not committed, not redistributed, and served to "
                    "localhost only."
                ),
                "derivation": (
                    f"mAreaData, {biome['width']}x{biome['width']} palette indices; "
                    "mColorToArea resolves each index to a UFGMapArea object"
                ),
                "areas": biome["distinct_areas"],
                "shipped_palette_rgba": [list(entry) for entry in biome["palette"]],
                "shipped_palette_role": (
                    "the game's own UI legend -- flat primaries, cyan, magenta, white. "
                    "Decoded for the record and NOT drawn: see palette below, which is this "
                    "file's own and was written to look like imagery."
                ),
                "palette": {name: list(BIOME_COLOURS[name]) for name in sorted(BIOME_COLOURS)},
                "palette_blend_texels": BIOME_BLEND_TEXELS,
                "palette_fallback": {
                    NO_MANS_LAND: list(NO_MANS_LAND_RGB),
                    "an area this file has no colour for": list(UNKNOWN_BIOME_RGB),
                },
                "index_to_area": {str(i): name for i, name in enumerate(drawn)},
                "calibration": calibration,
                "region_grid_check": agreement,
                "pyooz_version": pyooz_version,
            }
        }
    else:
        biome_rgb, biome_source = None, {}

    # ---- draw and cut ----------------------------------------------------------------
    total_started = time.time()
    for layer in layers:
        print(f"drawing {layer} at {args.size}x{args.size}")
        started = time.time()
        sheet = render_layer(
            layer,
            field,
            biome_rgb,
            biome or {"width": 1, "area": np.zeros((1, 1), np.uint8)},
            args.size,
            not args.quiet,
        )
        drew = time.time() - started
        stats, cut = install_layer(sheet, image_mod, out_dir, layer)
        del sheet
        stats["game_version_pinned"] = field_build
        render = {
            "width_px": args.size,
            "height_px": args.size,
            "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size, 4),
            "sampling": (
                "bilinear over the 1 m field per output pixel, with a no-data texel given "
                "zero weight rather than a value"
            ),
            "hillshade": (
                f"sun at azimuth {SUN_AZIMUTH_DEG} deg, altitude {SUN_ALTITUDE_DEG} deg, "
                f"shade in [{SHADE_FLOOR}, {SHADE_FLOOR + SHADE_RANGE}]"
            ),
            "seconds_to_draw": round(drew, 1),
            "seconds_to_cut": round(cut, 1),
            "imaging": {"name": "pillow", "version": pillow_version},
        }
        sidecar = build_sidecar(
            layer=layer,
            field_meta=field_meta,
            tiles=stats,
            render=render,
            extra=biome_source if layer == "satellite" else {},
        )
        path = layer_dir(out_dir, layer) / RENDER_SIDECAR_NAME
        path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
        print(
            f"wrote {layer_dir(out_dir, layer)}  {stats['count']} tiles over "
            f"z0..z{stats['max_z']}  {stats['bytes'] / 1e6:.1f} MB  "
            f"(drew {drew:.0f}s, cut {cut:.0f}s)"
        )
    print(f"done in {time.time() - total_started:.0f}s")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
