"""Draw two base-map layers of this world out of the 1 m heightfield and the game's biomes.

    uv run --extra gen python tools/gen_map_renders.py

``tools/gen_map_image.py`` cuts the game's own drawn map into ``data/local/tiles/``. That
picture is Coffee Stain's artwork and it is the only base layer the page has ever had. This
file adds two more, drawn here rather than found:

* **terrain** -- a hypsometric ramp under a north-west hillshade, water tinted by its own
  depth. Cartography, not photography: the colour is the height and nothing else.
* **satellite** -- the same relief lit the same way, but coloured from the game's own
  per-pixel biome raster through a palette designed to look like imagery, with bare rock on
  the steep ground, sun-bleached tops on the high plateaus and a little noise so the flats
  are not vector-flat.

Both are 32768x32768 on the **same frame as the artwork sheet** -- x [-3247, 4253] m,
y [-3750, 3750] m -- and cut into the same 256 px pyramid, so the page's existing tile grid,
its CRS and its bounds are untouched and a layer is a change of picture and nothing else.

Two regimes, because the field is one resolution and not one kind of answer
---------------------------------------------------------------------------
This file used to sample the 1 m field and nothing else, and every pixel of every layer was
therefore an **interpolation** of it -- an honest one, C1 and continuous, but an
interpolation, and a reconstruction from a 1 m max-Z fold cannot put a cliff rim anywhere
except on a 1 m staircase. That staircase is what every rock on this map carried as a dark
ragged rim, and it was never the geometry's fault: the cliff layer's triangles have a
world-space median edge of **0.48 m**, three times finer than the lattice the field folds
them onto.

So the render reads the geometry too. ``tools/gen_world_heightmap.py``'s own sweep, its own
mesh decode and its own ``MaxZRaster`` are **imported and called here** -- not copied -- and
the same rocks, under the same placement culls, are rasterised a second time into *this*
picture's grid at *this* picture's spacing. Where that answer is a measurement it is drawn
directly; where it is not, the Catmull-Rom kernel over the 1 m lattice is, exactly as
before:

* **direct** -- the geometry, rasterised at 0.229 m. Used where ``density.u8.z`` says at
  least one source vertex landed in the ground under an output texel, which at 0.229 m means
  a 1 m texel holding 19 or more of them. Those are the near-vertical faces: a rim projects
  a whole cliff face into a handful of texels in plan, so the density plane is highest
  exactly where the staircase was worst.
* **kernel** -- Catmull-Rom (cubic convolution, a = -1/2) over the 1 m field, which is C1,
  falling back to bilinear where the 4x4 stencil straddles no-data, and to nothing at all
  where no texel under it has a value. This is the great majority of the sheet and it is
  unchanged; ``_meta.render.two_regime.regimes`` counts exactly how much, per province.
* **and a cross-fade between them**, never a switch. A hard switch between a rasterised
  surface and a C1 interpolant is a derivative discontinuity, and the hillshade is a
  function of the derivative -- so the density plane's own boundaries would draw themselves
  into the relief as ridges. The height is blended, ``z = w*z_direct + (1-w)*z_kernel``,
  with ``w`` a coverage: the qualifying mask feathered at the field's own resolution,
  sampled bilinearly and clipped to [0, 1], multiplied by the direct raster's
  own coverage fraction so a silhouette fades rather than steps. ``SeamTrace`` below
  measures what that bought, along a line rather than at a probe -- the probes are sparse
  relative to the seam and a ridge one texel wide is invisible to all of them. The statistic
  is the second derivative of the drawn height over every 3-texel stencil that straddles the
  blend, against the same statistic in the two pure regimes beside it, and beside THAT the
  same texels under the hard switch this design refused.

Why 32768, when 16384 was measured and defended
-----------------------------------------------
Because the two claims are different claims, and only the first one was ever refused.

The measurement that stopped this file at 16384 -- and it was re-run on the v3 field and
came out the same -- is that **doubling the sampling of a 1 m field finds no new world**:
high-frequency energy per pixel falls at every doubling, 3.05 to 1.50 levels on the worst
cliff window, which is the signature of a sampler resolving an interpolant. That is still
true and this file still says so. z7 is **not** sold as more information.

What z7 is, is **smoothness that a browser cannot produce**. A client shown z6 at twice its
scale upsamples it bilinearly, and bilinear is C0: its derivative jumps at every source
texel, so the relief comes out ruled into 0.458 m squares -- which is the identical failure
that moved this file off a bilinear sampler in the first place, moved from the render into
the viewer. A z7 tile is the same surface evaluated by the same C1 kernel at 0.229 m, and
the difference between the two is not detail, it is the derivative being continuous. That is
a thing only the server can do, and ``_meta`` says so in those words rather than claiming a
level of terrain the field does not have.

And over the direct regime it *is* new information, because there the pixels are triangles
rather than an interpolation. How much of the sheet that is depends on the density plane
and on the spacing, so it is measured every run and recorded per province in
``_meta.render.two_regime.regimes`` rather than asserted here.

What the fill province gets instead
------------------------------------
Neither regime helps the third province. ``fill`` is the interface raster: 3.66 m cells
quantised to 3.9 m in Z, so it draws the ocean shelf and the map's edge as **terraces** --
flat plateaus with blocky outlines that no kernel can un-terrace, because those steps are
real in the data and are not in the world. They are low-passed at their own cell size,
inside the province only and faded at its edge, which smooths every terrace contour at once:
smoothing each level's indicator by one kernel and summing is, by the linearity of a
convolution, the same array as smoothing the level field itself, so the spline along the
marching-squares contour that this was designed as is one convolution and there is no
polyline to extract.

Borrowing detail where the field has none
------------------------------------------
The 1 m grid is one resolution, but it is **not one accuracy**, and pretending otherwise is
what made the offshore islands look like melted wax. Measured on the shipped field:
45.3% of it is landscape, which is real continuous geometry and genuinely sharpens; 21.3%
is cliff, rasterised low-poly collision hulls whose facets resampling can only polish; and
14.0% is fill, a 3.9 m-quantised block raster where resampling does nothing at all. Those
last two provinces carry less information than the artwork does -- Coffee Stain drew those
same islands at 0.92 m -- so over them, and only over them, the render **borrows the
artwork's luminance detail**: the sheet's own high-pass, above the scale the field can
speak to, multiplied into the ground colour and faded out on the provenance byte. It is
shading, not colour: the artwork's blue never reaches the render, only its light and shade.
Where the provenance says landscape, the shading is the field's and nothing else.

The water channel is read, not inferred
----------------------------------------
``waterq.u8.z`` says whether a texel is dry, water whose depth was measured against 1 m
terrain, or water whose level is known and whose depth is not. **This file asks that byte.**
The arithmetic it replaced -- water surface standing above the ground -- reads the open
ocean as dry, because over the fill province the "ground" is a 3.9 m raster that rounds
above a sea surface 17 m down: measured on the shipped field, 3.572 km2 of ocean out of
18.248 vanished that way. The depth ramp is kept for the shallow end where a depth was
really measured, and a level-only texel is drawn at full alpha and at the deep end of the
ramp -- which is a measurement rather than a preference: 95.2% of level-only water stands
over the fill province and 98% of its surface levels lie in a 0.7 m band around the ocean's
own -16.99 m. It IS the ocean.

Why a new file rather than a stage on gen_world_heightmap.py
------------------------------------------------------------
The heightmap generator's job is to get a field **out of the game**: it sweeps cooked
packages, rasterises collision meshes and validates the result against 626 nodes. This file
reads that finished field back through the same public codec any other consumer would, adds
two inputs the heightmap generator has never heard of, and writes pictures. Bolting this on
would couple a six-minute extraction to a twenty-minute render and give one ``--force`` two
meanings, so they stay two programs with two command lines.

They no longer share only an input, and that is worth stating plainly, because the direct
regime above needs the *triangles* and the baked 1 m field can only ever be interpolated.
This file therefore **imports the shipping generator** -- ``sweep_levels``,
``read_mesh_geometry``, ``rotation_matrix``, ``winding_sign``, ``MaxZRaster`` and every one
of its cull rules -- and calls them, the same posture ``tools/check_terrain_geometry.py``
takes and for the same reason: a second rasteriser would make every difference between the
render and the field a difference between two rasterisers as much as between two spacings.
The one thing that differs is the grid they are pointed at. Nothing is copied, nothing is
re-decided, and no constant is retyped.

The rest is shared by import as it always was: the codec comes from
``satisfactory_mcp.domain.spatial.heightfield``, the pyramid cutter with its staging dance
and its refusals from ``satisfactory_mcp.core.gameassets.pyramid``, and the container reader
from ``satisfactory_mcp.core.gameassets`` beside it -- because a tile layout with two
implementations is a tile layout with two opinions, and so is a container reader. The frame
itself still comes from ``tools/gen_map_image.py``: those corners were measured by that tool
against the artwork, and re-typing them here is exactly how three pyramids come to disagree
about where the world is.

The biome raster, and how its corners were found
------------------------------------------------
``/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel``
is a ``FGMapAreaTexture``: ``mDataWidth`` 4096, ``mAreaData`` 4096*4096 palette indices, and
``mColorToArea`` naming each index's ``UFGMapArea`` object. 37 indices resolve to 17 distinct
areas plus ``Area_NoMansLand``, which is what the game calls everything it does not name.
Decoding it is ``core.gameassets.maparea``'s job, shared with ``tools/gen_region_names.py``,
which builds ``data/region_names.json`` out of this same raster -- so the region table and
this render now describe one geometry rather than two.

Nothing in the asset says where those 4096 texels go, so the corners are **measured**.
``calibrate_biome`` scores a pin by the artwork sheet's own edge strength averaged over the
biome raster's boundary texels, divided by its edge strength everywhere: an area boundary
that is pinned right sits on a shore or a cliff the map draws, and one that is pinned wrong
sits on flat fill. It is a ratio, so it does not reward a pin for merely making more
boundary. Measured on the shipped sheet the in-game map square reads **1.97** and every
neighbour is far below it -- 1.28 at 5% larger, 1.24 at 5% smaller, and 1.33 for the best of
a +-600 m translation sweep at true scale. So the biome raster spans exactly the square the
artwork does, 4096 texels over 7500 m, 1.831 m to the texel, row 0 north.

That is the sharp measurement, and it is the only one that pins anything.
``region_table_is_current`` is the other number recorded every run and it is a staleness
gate rather than evidence: ``data/region_names.json`` is now derived from this same asset, so
its 768 non-void cells must be exactly this raster's majority downsample. Anything under 100%
means the committed table was cut from a different build, and the run says which command
fixes it. (It used to be a genuinely independent comparison -- that file was a hand trace of
a wiki image and agreed with the game on 68.1% of the cells that were comparable at all --
and that number is kept as history in the region table's own ``_meta``, because it is what
the re-derivation moved.)

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
z0..z7, ``tiles@2x/{z}/{x}_{y}.png`` for z0..z5, and a ``meta.json`` the web API reads.
**z7 is 32768 px**, 0.229 m to the pixel, and ``_meta.render.z7`` says in words what it is
and is not.

``tiles@2x/`` is the identical tile GRID at 512 px a tile: level z is still 2**z tiles a
side over the identical squares of the world, so a client asks for the same ``{z}/{x}/{y}``
and gets twice the pixels in each direction, which is what a display with a device pixel
ratio above one wants. It is cut from a 16384 px downscale of the sheet rather than from the
sheet, which **caps it at z5, where it already was**, and that is a size decision taken
by measurement rather than by arithmetic: cutting it from the full 32768 would add a z6
level of 512 px tiles weighing as much as the entire 1x tree, for pixels a hi-DPI client
already has -- Leaflet's own retina path asks for ``z+1`` at 1x and draws it at half size,
and the 1x tree is now a level deeper than it was. ``RENDER_2X_PX`` is where that is
written down.

Everything is under ``data/local/``, which is gitignored, and nothing here is ever committed
-- including ``renders/direct.cache/``, the one scratch artifact this file keeps between
layers so the geometry is rasterised once and drawn twice.

Staleness
---------
Four pins now, all refused on rather than overwritten. The field's own ``meta.json`` names
the build it was cut from, and this run records that same string; a re-run over renders whose
sidecar names a different field build stops. A field that is not there at all is not an
error to work around -- it is the one input this file cannot invent, so the run says which
tool writes it and exits. A field with **no density plane** cannot say which of its texels
are measurements, so the two-regime sampler has nothing to switch on: that run stops too,
and names the generator version that writes one, rather than quietly drawing recipe 2 under
a sidecar that claims recipe 3. And the direct cache carries the size, the sub-sampling and
the build it was rasterised for, so a cache from another render is rebuilt rather than
reused. ``--force`` says the first two anyway; ``--kernel-only`` is the honest way to draw
without the geometry, and it records itself in the sidecar.

What opens the container
------------------------
``ooz``, from ``pyooz``, opens the container's Oodle blocks, ``texture2ddecoder`` unpacks
the artwork's BC1 slices and Pillow writes the PNGs. All three are the project's ``gen``
extra: optional dependencies, pinned exactly because they decide the bytes this file
writes, and asked for on the command line, the same posture as ``tools/gen_map_image.py``::

    uv run --extra gen python tools/gen_map_renders.py

Optional means optional **at import time**: neither is imported at module scope anywhere in
this repository -- the one ``import ooz`` sits inside
``core.gameassets.iostore.oodle_decompress`` and Pillow is imported by the one function
below that needs it -- so a machine without the extra still imports every module and runs
the whole test suite; it just cannot generate. numpy and scipy are dependencies of this
project outright and are imported at the top of this file.

Licence
-------
The heightfield and the biome raster are both derived from Coffee Stain's cooked assets, read
out of the reader's own installed copy of the game. The colours are this file's. Nothing is
committed, uploaded or redistributed, and the server serves it to localhost only.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.maparea import (
    MAP_AREA_CLASS,
    MAP_AREA_PATH,
    NO_MANS_LAND,
    MapAreaError,
    read_map_areas,
)
from satisfactory_mcp.core.gameassets.packages import AssetIndex, ClassFacts, ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import read_str_path
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
    TILES_DIR_NAME,
    PyramidError,
    cut_square,
    cut_square_parallel,
    install_pyramid,
)
from satisfactory_mcp.core.gameassets.textures import decode_bc1_rgba
from satisfactory_mcp.domain.spatial import heightfield as hf

# And the generator that WRITES the field, for the direct regime: its sweep, its mesh
# decode, its cull rules and its rasteriser, called rather than reimplemented. See the
# module docstring -- the only thing this file changes is the grid they are pointed at.
from tools import gen_world_heightmap as gen
from tools._common import base_parser, require_gen

# The corners every layer is drawn on, the sheet the ARTWORK is drawn at, and how to read
# its four slices out of the container: all from the tool that MEASURED them rather than
# typed again here, so the three pyramids cannot drift apart. A plain import of a sibling
# generator, which is possible now that the sibling holds nothing but its own constants and
# its own stages -- the cutter and the reader it used to be borrowed for are both in core.
from tools.gen_map_image import BOUNDS_M, SHEET_PX, SLICES, TILE_PX, read_slice

#: Where the layers go, and what each one's sidecar is called. ``renders/<layer>/`` holds a
#: ``tiles/`` tree of exactly the shape ``data/local/tiles/`` has, so the endpoint that
#: serves one serves the others with a directory swapped and nothing else.
LOCAL_DIR = ROOT / "data" / "local"
RENDERS_DIR_NAME = "renders"
RENDER_SIDECAR_NAME = "meta.json"

#: The layers this file draws. Order is the order they are cut in, which is the order the
#: run prints; ``--layer`` restricts it.
LAYERS = ("terrain", "satellite")

#: How many processes deflate tiles when ``--workers`` is not given. One per core, capped:
#: past a certain point the cores are waiting on the disk rather than on zlib, and thirty-two
#: Python interpreters each importing numpy to write PNGs is a startup cost with nothing on
#: the other side of it.
WORKER_CAP = 16
DEFAULT_WORKERS = min(os.cpu_count() or 1, WORKER_CAP)

#: Which level ``--check-parallel`` cuts twice. z5 is 1,024 tiles -- enough that the timing
#: means something and the comparison is not one file -- and it is a level every supported
#: sheet size actually has.
CHECK_PARALLEL_Z = 5

#: What a render is drawn at, and the levels that buys. 32768 px over 7500 m is 0.229 m to
#: the pixel; z7 is the top of the 1x tree. Derived from the artwork's own sheet size rather
#: than typed, because the two are the same frame and one of them moving without the other
#: is the drift this file exists not to have.
#:
#: The doubling from 16384 is **not** a claim that the field has more to say -- that was
#: measured twice and refused twice, and the module docstring keeps the numbers. It is a
#: claim about the CLIENT: a browser shown z6 at twice its scale upsamples it bilinearly,
#: and bilinear is C0, so the relief it draws is ruled into 0.458 m squares. z7 is the same
#: surface evaluated by the same C1 kernel at half the spacing, which is a thing only a
#: server can hand over.
RENDER_PX = SHEET_PX * 4

#: And what the @2x tree is cut from, which is one level shallower than the sheet on
#: purpose. See "What it writes": a 512 px z6 tree costs as much as the whole 1x pyramid for
#: pixels a hi-DPI client gets by asking for the 1x tile one level deeper, which is what
#: Leaflet's own retina path does.
RENDER_2X_PX = SHEET_PX * 2

#: Which recipe drew the pixels. Recorded per layer, so a reader looking at a tile can find
#: out which set of rules made it, and a later recipe over an earlier one is an upgrade a
#: run performs rather than announces.
RECIPES = {
    1: (
        "terrain: hypsometric ramp over the 1st..99.5th height percentile, NW hillshade at "
        "45 deg, water tinted by depth. satellite: biome palette, slope-driven rock, "
        "elevation lightening, two octaves of noise, the same hillshade and water"
    ),
    2: (
        "recipe 1 at 16384 px, sampled with a C1 Catmull-Rom kernel so the hillshade has no "
        "cell structure; submersion read from waterq.u8.z rather than inferred from a "
        "comparison, level-only water at full alpha and the deep end of the ramp; and the "
        "artwork sheet's luminance high-pass borrowed into the shading wherever the "
        "provenance byte says cliff or fill, faded out towards landscape"
    ),
    3: (
        "recipe 2 at 32768 px, with a two-regime sampler: the cliff geometry decoded from "
        "the container and rasterised into this grid at 0.229 m wherever density.u8.z says "
        "a source vertex landed under the output texel, the Catmull-Rom kernel over the 1 m "
        "field everywhere else, and a density-weighted cross-fade between them so no "
        "province boundary is ever a derivative discontinuity. Plus the fill province "
        "low-passed at its own 3.66 m cell so its 3.9 m terraces stop being contours"
    ),
}
RECIPE = 3

#: What ``--kernel-only`` draws, and it is not "recipe 3 with a stage switched off". It is
#: the recipe that shipped before this one, whole: no geometry opened, no direct regime, no
#: cross-fade and no de-terracing either -- so a run of it at 16384 IS the picture the page
#: had, and a before/after against it is a comparison of two recipes rather than of one
#: recipe against a half of itself. The sidecar records this number, so a layer drawn that
#: way never claims the recipe above.
RECIPE_KERNEL_ONLY = 2

# --------------------------------------------------------------------------------------
# The biome raster.
# --------------------------------------------------------------------------------------

#: Decoding the raster, resolving each palette index to one ``Area_*`` asset and reading the
#: game's own name for it all live in ``core.gameassets.maparea`` now, because
#: ``tools/gen_region_names.py`` reads the same texture for the region layer and two decoders
#: would be two opinions about what the game says. What stays here is what this file DRAWS
#: with: a palette of its own, a blur, and the pin the raster is placed on.

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

#: The committed region table, which is now DERIVED from this same raster by
#: ``tools/gen_region_names.py``. It used to be an independent hand trace of a wiki image and
#: the agreement between the two was worth reporting; it is a downsample of the raster this
#: file just decoded, so agreement is no longer evidence about the pin -- it is a staleness
#: check, and that is what ``region_table_is_current`` below makes it.
REGION_TABLE = ROOT / "data" / "region_names.json"

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

#: And the same edge softened in SPACE as well as in depth, in METRES of ground. The depth
#: feather above does nothing where the shore is a cliff -- the water goes from nothing to
#: metres deep across one texel and there is no band to blend over -- and much of this
#: world's water sits in box-shaped bodies against exactly that. A blur of the coverage is
#: what antialiases those, and it touches nothing else: a pixel two from the edge is fully
#: water or fully ground either way.
#:
#: In metres rather than in output pixels, which is the one thing about it that changed
#: when the sheet doubled. 0.8 px was 0.73 m at 8192 and would have been 0.37 m at 16384 --
#: the same constant quietly meaning half as much ground, which is how a recipe stops being
#: the recipe that was approved.
WATER_EDGE_BLUR_M = 0.73

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
# Borrowing the artwork's detail where the field's own province is coarse.
# --------------------------------------------------------------------------------------

#: Which provinces of the field carry less information than the artwork does. Landscape is
#: deliberately absent: it is continuous geometry the game itself evaluates, it is 45.3% of
#: the field, and its shading is the field's own. These two are not -- cliff is rasterised
#: low-poly collision hulls and fill is a 3.9 m block raster -- and over them a render drawn
#: from the field alone is smooth because it has nothing to say, not because the world is.
#:
#: **Both cliff values, and this is not a formality.** Heightfield v3 split the cliff
#: province into 4 (a texel the rasteriser reached by interpolating a triangle wider than
#: itself) and 5 (a texel a source vertex landed in), and on the shipped field 73% of it is
#: 5. Listing only 4 would silently withdraw the borrow from three quarters of the province
#: it was measured on -- the same shading change that was picked by looking, unpicked by a
#: renumbering. Spelled through ``PROV_CLIFF_VALUES`` so a third cliff value cannot be
#: added without this line seeing it.
BORROW_PROVENANCE = (*hf.PROV_CLIFF_VALUES, hf.PROV_FILL)

#: How far the borrow fades out across a province boundary, in field texels (metres). The
#: provenance byte is a hard label on a 1 m grid, and a hard switch between two shading
#: rules would draw the label itself: a coastline of shading style around every island. Six
#: metres is wide enough that no boundary reads as a line and narrow enough that a 40 m
#: island is fully borrowed in the middle.
BORROW_FEATHER_M = 6.0

#: The scale of artwork detail that is borrowed, in artwork pixels (0.92 m each). The high
#: pass is the sheet minus its own Gaussian blur at this sigma, so what comes across is
#: everything FINER than about 7 m and nothing coarser -- the coarse structure is the
#: field's job and the two must not both draw it. Deliberately smaller than the fill layer's
#: own 3.9 m quantisation is large, because that is exactly the band the field cannot speak
#: in.
BORROW_DETAIL_SIGMA_PX = 8.0

#: And then two things are done to that high pass, both of which are the difference between
#: borrowing the artwork's SHADING and tracing its ink.
#:
#: The map is a drawing, and a drawing has strokes: every rock formation on it is outlined
#: in a hard dark line one or two pixels wide. Multiplied straight into a render those come
#: out as exactly what they are -- ink -- and the result reads as a line drawing laid over
#: terrain rather than as light falling on it. So the high pass is first blurred by
#: ``BORROW_DETAIL_SOFTEN_PX``, which turns a stroke into the gradient a stroke was standing
#: in for; and then it is squashed through ``tanh`` at ``BORROW_DETAIL_SIGMAS`` standard
#: deviations of itself, which is a soft clip rather than a hard one -- the mid-tones, which
#: are the shading, pass through almost linearly, and the strokes, which are the outliers,
#: saturate instead of dominating.
BORROW_DETAIL_SOFTEN_PX = 1.6
BORROW_DETAIL_SIGMAS = 1.2

#: How much of the result reaches the picture, and how far it may push a pixel either way.
#: Picked by looking, on the four crops the upgrade was judged on. At 0.17 the offshore
#: cliff islands are still flat facets with a hint of something on them; at 0.50 the drawn
#: map's contour rings read as rings rather than as terracing. 0.30 is where a collision
#: hull stops being eight flat plates and starts being rock, and where the dune field --
#: which is landscape province and therefore untouched -- still looks exactly as it did.
BORROW_GAIN = 0.30
BORROW_CLAMP = (0.74, 1.26)

#: Luma weights. Rec. 601, because what is wanted is the artwork's LIGHT -- the shading a
#: human drew on those cliffs -- and 601 is the weighting that matches how a human sees it.
#: The colour never crosses: an ocean drawn blue contributes its brightness and nothing else.
BORROW_LUMA = np.array([0.299, 0.587, 0.114], np.float32)

# --------------------------------------------------------------------------------------
# The direct regime: which output texels are entitled to the triangles, and how the two
# regimes are joined.
# --------------------------------------------------------------------------------------

#: How many source vertices the ground under one OUTPUT texel has to have contributed
#: before that texel's height is a measurement rather than an interpolation across a
#: triangle wider than itself. One, which is ``gen_world_heightmap.DIRECT_SAMPLES_MIN``
#: imported rather than retyped -- the rule is the generator's and this file only evaluates
#: it at a different spacing. ``density.u8.z`` counts per 1 m texel, so the test against a
#: 0.229 m texel is ``density >= 1 / 0.229**2``, i.e. 19 of them.
DIRECT_SAMPLES_PER_TEXEL = gen.DIRECT_SAMPLES_MIN

#: How many sub-samples per output texel per axis the direct pass rasterises at. One, and
#: that is a measurement rather than a preference: the pass costs 4x per doubling and the
#: silhouette it is antialiasing is already at 0.229 m, an eighth of the 1 m staircase this
#: whole regime exists to remove. What replaces the supersample is ``COVERAGE_TENT`` below,
#: which reconstructs the same fractional edge from the binary mask for the price of two
#: separable 3-taps. Raise it with ``--direct-subsamples`` and the sidecar records what was
#: actually run.
DIRECT_SUBSAMPLES = 1

#: The tent the direct raster's own coverage is reconstructed with, and it is the
#: antialiasing on the direct silhouettes. A 3-tap 1-2-1 in each axis over the binary
#: coverage turns a hard per-texel in/out decision into a fraction over one texel, and the
#: heights are carried through the same kernel WEIGHTED BY THAT COVERAGE, so a texel just
#: outside the rock is a fraction of the rock's own edge height rather than a fraction of
#: zero. Off with ``--direct-subsamples`` above 1, where the supersample has already done it.
COVERAGE_TENT = np.array([0.25, 0.5, 0.25], np.float32)

#: The knee of the smoothed positive part that lets a rock raise the ground and never lower
#: it, in metres. The field's own composition is a hard ``max`` and this is that same max
#: with its corner rounded: a hard one puts a first-derivative discontinuity exactly where
#: the rock meets the ground, which is a line the hillshade would draw around the base of
#: every formation on the map. A quarter of a metre sits at most an eighth of one above the
#: hard answer, is never below it -- so the rule "a rock raises the ground and never lowers
#: it" holds exactly rather than nearly -- and is C-infinity everywhere.
DIRECT_LIFT_KNEE_M = 0.25


#: Rows of the output the direct pass rasterises at a time. A whole 32768 square of float32
#: is 4.3 GB and the render already holds 3.2 GB of output; 256 rows of it is 34 MB, and a
#: triangle at the 0.48 m median touches one band or two, so a per-placement y-bbox test is
#: all the selection this needs. The same 256 the colour bands use, which is also one row of
#: 256 px tiles -- three things that want the same number and have no reason to disagree.
DIRECT_BAND_ROWS = 256

#: Where the direct raster is kept between the two layers. Rasterising 216 M triangles is
#: twenty minutes and the answer does not depend on which layer is being coloured, so it is
#: done once, memory-mapped, and deleted at the end of the run unless ``--keep-direct``.
#: Under ``renders/``, which is under ``data/local/``, which is gitignored.
DIRECT_CACHE_DIR_NAME = "direct.cache"
DIRECT_Z_NAME = "direct.z.f32"
DIRECT_COVERAGE_NAME = "direct.cov.u8"
DIRECT_CACHE_SIDECAR = "meta.json"

#: What the seam trace calls "at the seam" and "in a pure regime", as distances from a
#: half-weight crossing. The statistic is the p99 of the second difference of the drawn
#: height along a row: at the seam against the pure regimes on either side of it, as a
#: ratio. Above ``SEAM_RATIO_MAX`` the cross-fade is drawing curvature the surface does not
#: have, which is the failure this design exists to avoid, and the run says so.
#: What counts as "at the seam" is the whole BLEND, not a window around the half-weight
#: line, and that is a repair rather than a preference. A feather's second derivative is
#: zero at its own midpoint by symmetry -- it lives at the shoulders -- so a window around
#: ``w = 0.5`` measures the one place a hard join has nothing to show, and a nearly-hard
#: join sails through it. Every 3-texel stencil is therefore sorted into exactly one of
#: three pools by the weights under it: wholly kernel, wholly direct, or straddling.
SEAM_MID = 0.5
SEAM_PURE = 0.02
SEAM_RATIO_MAX = 1.5

#: What a hard switch reads, which is the scale the fade's own number is on. A convex blend
#: of two surfaces can never be rougher than the switch between them -- rounding the weight
#: to 0 or 1 is the extreme point of the blend -- so this is an identity and not a bound, and
#: it is written down as the ceiling the reported ratio is a fraction of rather than as a
#: gate anything can fail. See ``SeamTrace`` for the three references that were tried and
#: what each of them turned out to be measuring.
SEAM_SWITCH_CEILING = 1.0

SEAM_SAME_SURFACE_M = 0.5

#: How far from a crossing the comparison pool is allowed to be gathered, in output texels.
#: 32 of them is 7.3 m at z7 -- the ground on either side of the seam and not the rest of
#: the world, which would be mostly open ocean and would make any seam at all look like a
#: spike against it.
SEAM_NEAR_TEXELS = 32

#: How many texels of each pool one band contributes. A systematic sample rather than the
#: whole pool, because the pools run to tens of millions of texels over a 32768 square and
#: the percentile taken from them moves in the fourth decimal.
SEAM_SAMPLE_MAX_PER_BAND = 200_000

#: How the fill province stops being terraces. Its cells are 3.66 m and its Z step is
#: 3.9 m, so it draws the ocean shelf as flat plateaus with blocky outlines; the low pass is
#: one cell wide, which is the scale below which that raster says nothing at all. Both
#: numbers come from the generator that decoded the raster rather than from here.
#:
#: Normalised over the province and faded by its own weight, so nothing outside the fill is
#: touched and the province boundary is not itself drawn -- and so a fill texel next to a
#: cliff keeps its own height rather than being pulled towards a neighbour that is not in
#: the same raster.
FILL_DETERRACE_SIGMA_M = gen.FILL_HORIZONTAL_M
FILL_QUANTISATION_M = gen.FILL_VERTICAL_M

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

#: Rows of the output drawn at a time. 256 rows of 16384 costs about 17 MB of float32 per
#: intermediate, which is the point: the whole sheet at once would be over a gigabyte
#: apiece.
BAND_ROWS = 256

#: The halo each band is computed with, so the hillshade's gradient at a band edge sees the
#: rows on the other side of it -- and so does the water edge's blur, which reaches further
#: than the gradient does. Cropped off before the band is stored, so no pixel of the output
#: was ever computed from a one-sided difference or a truncated kernel.
#:
#: Eight rather than four: the cubic sampler's stencil is two texels either side instead of
#: one, and the water blur's three sigma at 16384 is five pixels rather than two. Both
#: numbers went up when the sheet did, and a halo that had not would have drawn a seam
#: every 256 rows -- which is the failure this constant exists to prevent, so it is sized
#: against the widest kernel in the band rather than left at what used to be enough.
BAND_HALO = 8


def load_imaging():
    """Pillow, once ``require_gen`` has shown it is there. Same posture as next door.

    The size limit goes off because Pillow's default guard is a decompression-bomb rule
    for images off the internet, and every image here is one this repository's own tools
    cut from the reader's own game -- an 8192 px sheet is the point, not an attack.
    """
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    return Image


# --------------------------------------------------------------------------------------
# Reading the biome raster out of the container.
# --------------------------------------------------------------------------------------


def read_biome(store, scripts) -> dict:
    """The map-area raster as this file wants it: a numpy square and a name per index.

    The decode, the shape checks and the index -> ``Area_*`` resolution are
    ``core.gameassets.maparea``'s, shared with ``tools/gen_region_names.py``. What this
    adapter adds is the two things only a renderer wants: the raster as a numpy array to
    index a colour table with, and each index flattened to the STEM -- ``Area_RedJungle``
    rather than ``Area_RedJungle_2`` -- because ``BIOME_COLOURS`` is one colour per kind of
    ground, and the two assets behind one stem are one kind of ground however the game
    names them.
    """
    try:
        areas = read_map_areas(store, scripts)
    except MapAreaError as exc:
        raise SystemExit(
            f"{exc} The satellite layer has no other source for what grows where, so "
            "nothing here can be trusted until that is looked at."
        ) from exc
    raster = np.frombuffer(areas.texels, dtype=np.uint8).reshape(areas.width, areas.width)
    names = [None if area is None else area.stem for area in areas.areas]
    return {
        "width": areas.width,
        "area": raster,
        "palette": [tuple(entry) for entry in areas.palette],
        "names": names,
        # The exact asset per index, kept beside the stem because the staleness check below
        # reads a name map that is keyed by asset -- ``Area_crater_1`` and ``Area_crater_2``
        # are one stem and two different named regions.
        "assets_by_index": [None if area is None else area.asset for area in areas.areas],
        "assets": list(areas.assets),
        "distinct_areas": sorted({n for n in names if n and n != NO_MANS_LAND}),
    }


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
# The artwork sheet: the ruler the biome pin is scored against, and the detail the coarse
# provinces borrow.
# --------------------------------------------------------------------------------------


def read_artwork_sheet(store, decoder, image_mod):
    """The game's own 8192 px map sheet, stitched out of its four BC1 slices.

    Read from the container rather than from ``data/local/map.png``, and that is worth a
    sentence: the PNG is the same pixels, but it is written by a DIFFERENT tool that a
    reader may not have run, and this file now needs the sheet for two things that are not
    optional -- scoring the biome pin, which used to be skipped when the PNG was absent, and
    the detail the cliff and fill provinces borrow. Reading the source removes the skip and
    removes the second copy at once.

    The slice names, their layout and the ``.ubulk`` length check all come from
    ``tools/gen_map_image.py``, which is the tool that proved them; nothing about the
    artwork is re-decided here.
    """
    sheet = image_mod.new("RGB", (SHEET_PX, SHEET_PX))
    for name in SLICES:
        col, row = (int(value) for value in name.split("_")[1].split("-"))
        slice_image = decode_bc1_rgba(decoder, image_mod, read_slice(store, name), TILE_PX)
        sheet.paste(slice_image.convert("RGB"), (col * TILE_PX, row * TILE_PX))
    return sheet


def artwork_detail(sheet) -> tuple[np.ndarray, dict]:
    """The artwork's luminance high pass as int8, and what scaling it took.

    What comes back is everything in the drawn map FINER than ``BORROW_DETAIL_SIGMA_PX``
    -- the light and shade a human put on those cliffs -- with the coarse structure removed,
    because the coarse structure is the field's job and two sources drawing it at once would
    double every hillside. Luminance only: the artwork's colour never crosses into a render.

    Held as int8 rather than float32 because it is 67 MB against 268, it is sampled
    bilinearly afterwards anyway, and 1/127th of two and a half standard deviations is finer
    than any of it will survive being multiplied into a colour.
    """
    rgb = np.asarray(sheet, np.float32)
    luma = rgb @ BORROW_LUMA
    high = luma - ndimage.gaussian_filter(luma, BORROW_DETAIL_SIGMA_PX, mode="nearest")
    high = ndimage.gaussian_filter(high, BORROW_DETAIL_SOFTEN_PX, mode="nearest")
    spread = float(high.std())
    detail = (np.tanh(high / max(spread * BORROW_DETAIL_SIGMAS, 1e-6)) * 127.0).astype(np.int8)
    return detail, {
        "role": (
            "the artwork sheet's own luminance minus its Gaussian blur, i.e. everything the "
            "drawn map says below about "
            f"{BORROW_DETAIL_SIGMA_PX * (BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / SHEET_PX:.1f}"
            " m and nothing above it"
        ),
        "sheet_px": SHEET_PX,
        "metres_per_pixel": round((BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / SHEET_PX, 4),
        "high_pass_sigma_px": BORROW_DETAIL_SIGMA_PX,
        "soften_sigma_px": BORROW_DETAIL_SOFTEN_PX,
        "luma_weights": [float(value) for value in BORROW_LUMA],
        "measured_std": round(spread, 4),
        "tanh_knee_at_sigmas": BORROW_DETAIL_SIGMAS,
        "why_tanh": (
            "the artwork is a drawing and a drawing has strokes -- every rock formation is "
            "outlined in hard dark ink. A soft clip lets the mid-tones (the shading) through "
            "almost linearly and saturates the outliers (the ink), which is the difference "
            "between borrowing light and tracing lines"
        ),
        "stored_as": "int8, +-127 at full saturation",
    }


def coarse_province(field) -> tuple[np.ndarray, dict]:
    """Where the field is coarser than the artwork, as a feathered 0..255 mask at 1 m.

    ``BORROW_PROVENANCE`` is a hard label on a 1 m grid and the borrow is a change of
    shading rule, so switching on it directly would draw the LABEL: a visible coastline of
    style around every island the fill layer covers. The mask is therefore blurred before it
    is sampled, at the field's own resolution and once, rather than per band with a halo
    wide enough to hold the kernel.

    Stored as uint8 because it is a weight in [0, 1] that is about to be multiplied by a
    detail term that is itself quantised to 1/127.
    """
    inside = np.isin(field._prov, BORROW_PROVENANCE)
    share = float(inside.mean())
    feather = ndimage.gaussian_filter(
        inside.astype(np.float32), BORROW_FEATHER_M * 100.0 / field.spacing_cm, mode="nearest"
    )
    return (np.clip(feather, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8), {
        "provinces": [hf.PROV_NAMES[value] for value in BORROW_PROVENANCE],
        "share_of_the_field": round(100 * share, 2),
        "feather_m": BORROW_FEATHER_M,
        "role": (
            "1 where the field's own province is coarser than the artwork -- rasterised "
            "collision hulls, or a 3.9 m block raster -- 0 over the landscape layer, which "
            "is continuous geometry and keeps shading of its own, and a Gaussian ramp "
            "between them so the provenance byte is never itself drawn"
        ),
    }


# --------------------------------------------------------------------------------------
# The direct regime: which texels the triangles are allowed to answer, and the surface
# underneath them where they are not.
# --------------------------------------------------------------------------------------


def direct_weight(field, spacing_m: float) -> tuple[np.ndarray | None, dict]:
    """Where the geometry outvotes the kernel, as a feathered 0..255 mask at 1 m.

    ``None`` when the field carries no ``density.u8.z`` -- which is not "no samples
    anywhere" and must never be read as one. A field written before the plane existed knows
    nothing about its own density, and the caller's job is to refuse rather than to assume.

    The rule is the generator's: **one source vertex under the output texel**. The plane
    counts per 1 m texel, so the test is scaled by the output texel's own area, and that is
    the whole of why fewer texels qualify at 0.229 m than at 0.458 m -- the same geometry,
    asked a harder question.

    A **mask and not a weight**, and that is the whole of what changed about this plane.
    It was the cross-fade's own weight for one draft, feathered over metres and multiplied
    into the height, and the measurement that ended that is in the module docstring: on the
    texels where the worst rims are, the density is **zero by construction**, so a weight
    built from it cannot reach them however wide the feather is. What decides that the rocks
    are drawn is their own coverage of the pixel. What this decides is what to CALL the
    answer -- a measurement, or the plane of a triangle wider than a texel -- and a
    provenance label is a yes or a no per texel, so it is read nearest and never blurred.
    """
    density = field.density_raster()
    if density is None:
        return None, {
            "absent": (
                f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its "
                "texels are measurements and which are the rasteriser interpolating across "
                "a triangle wider than a texel. That is the only thing the two-regime "
                "sampler switches on."
            )
        }
    need = DIRECT_SAMPLES_PER_TEXEL / (spacing_m * spacing_m)
    qualifies = density >= min(need, 255.0)
    share = float(qualifies.mean())
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    return (qualifies.astype(np.uint8) * 255), {
        "plane": hf.DENSITY_NAME,
        "rule": (
            f"at least {DIRECT_SAMPLES_PER_TEXEL:g} source vertex under an output texel of "
            f"{spacing_m:.4f} m, i.e. density >= {need:.2f} per 1 m texel"
        ),
        "samples_min_per_output_texel": DIRECT_SAMPLES_PER_TEXEL,
        "density_min_per_field_texel": round(float(need), 2),
        "qualifying_share_of_the_field": round(100 * share, 3),
        "qualifying_share_of_the_cliff_province": round(
            100 * float(qualifies[cliff].mean()) if cliff.any() else 0.0, 2
        ),
        "role": (
            "1 where the cliff geometry sampled the ground finer than this render draws it, "
            "0 where it did not. Provenance and not a gate: what decides that the rocks are "
            "drawn is their own coverage of the pixel, and this decides what to CALL what "
            "was drawn -- a measurement, or the plane of a triangle wider than a texel."
        ),
    }


def ground_lattice(field, heights: np.ndarray) -> tuple[np.ndarray, dict]:
    """The same heights with the CLIFF province removed, which is the surface underneath.

    This is the kernel regime's real input, and getting it wrong is what left the ragged rim
    in place through two attempts at this. Interpolating the whole field over a rim
    reconstructs the **fold**: a texel just outside a rock is still a cliff-top height,
    because a cliff-top texel is one of the four the stencil reads, so the drop stays where
    the 1 m lattice put it and no amount of output resolution moves it. Interpolating the
    lattice UNDERNEATH -- the landscape and the fill, which are continuous surfaces the game
    evaluates itself -- puts the ground where the ground is, and lets the rasterised rock
    decide its own silhouette on top of it. That is the composition
    ``gen_world_heightmap.py`` performs at 1 m, performed here at 0.229 m instead of read
    back from its own output.

    The holes this leaves are real and are handled by the sampler that already exists: where
    the 4x4 stencil is not whole it falls back to 2x2, where nothing under it is known it
    says so, and the caller substitutes the whole field's fold there -- which is inside a
    formation, where the rock covers the pixel and answers it anyway.
    """
    cliff = np.isin(field._prov, hf.PROV_CLIFF_VALUES)
    ground = np.where(cliff, np.float32(hf.NODATA), heights).astype(np.float32)
    known = field._height_dm != hf.NODATA
    return ground, {
        "role": (
            "the landscape and fill lattices with the cliff province removed, which is what "
            "the kernel regime interpolates. Interpolating the composed field instead "
            "reconstructs its 1 m fold, and a rim reconstructed from a fold is a 1 m "
            "staircase at any output resolution."
        ),
        "removed_share_of_the_field": round(100 * float(cliff.mean()), 2),
        "lattice_share_of_the_field": round(100 * float((known & ~cliff).mean()), 2),
        "where_it_knows_nothing": (
            "inside a formation big enough that no landscape texel survives under it. There "
            "the whole field's own fold stands in, and the rock's coverage is 1, so the rock "
            "is the answer either way"
        ),
    }


def deterraced_height(field) -> tuple[np.ndarray, dict]:
    """The field's heights in decimetres, with the fill province's terraces low-passed out.

    Returned as float32 rather than int16 on purpose: the terracing this removes is 3.9 m
    tall and the decimetre container it lives in would put it straight back as a 0.1 m
    staircase under a hillshade computed at 0.229 m. ``hf.NODATA`` survives as itself, so
    every sampler below reads this raster with exactly the test it read the int16 one with.

    The low pass is **normalised over the province and faded by its own weight**. Both
    halves are load-bearing. Normalised, because a Gaussian that ran over the landscape
    beside a fill texel would drag a 1 m measurement into a 3.9 m raster's answer;
    weighted, because a hard edge at the province boundary is exactly the artifact this is
    supposed to remove, one province over.

    And it is one convolution rather than a contour trace, which is worth a sentence. The
    design this implements was "marching squares over each terrace, spline along the
    polyline, rasterise back". Smoothing every level's indicator with one kernel and adding
    them up is, by the linearity of a convolution, the same array as smoothing the level
    field itself -- so the two are one operation, and the one that does not need a polyline
    library is the one that is here.
    """
    height = field._height_dm.astype(np.float32)
    known = field._height_dm != hf.NODATA
    fill = (field._prov == hf.PROV_FILL) & known
    sigma = FILL_DETERRACE_SIGMA_M * 100.0 / field.spacing_cm
    weight = ndimage.gaussian_filter(fill.astype(np.float32), sigma, mode="nearest")
    total = ndimage.gaussian_filter(np.where(fill, height, 0.0), sigma, mode="nearest")
    smooth = total / np.maximum(weight, 1e-6)
    alpha = np.where(fill, np.clip(weight, 0.0, 1.0), 0.0)
    # Clamped to one quantisation step, and that bound is the definition of the artifact
    # rather than a safety margin. A terrace is a 3.9 m step where the world has a ramp, so
    # un-terracing moves a texel by at most one step; a correction larger than that is not
    # de-terracing, it is a low pass erasing a scarp the fill raster really did resolve --
    # and the fill province holds a 300 m drop at the map's edge that would otherwise be
    # rounded off by half of itself.
    limit = FILL_QUANTISATION_M * hf.DM_PER_M
    moved = np.clip(alpha * (smooth - height), -limit, limit)
    out = np.where(known, height + moved, np.float32(hf.NODATA)).astype(np.float32)
    shifted = np.abs(moved[fill]) / hf.DM_PER_M if fill.any() else np.zeros(1, np.float32)
    clamped = float((shifted >= FILL_QUANTISATION_M - 1e-6).mean()) if fill.any() else 0.0
    return out, {
        "province": hf.PROV_NAMES[hf.PROV_FILL],
        "share_of_the_field": round(100 * float(fill.mean()), 2),
        "cell_m": round(FILL_DETERRACE_SIGMA_M, 4),
        "quantisation_m": round(FILL_QUANTISATION_M, 4),
        "sigma_field_texels": round(sigma, 3),
        "moved_median_m": round(float(np.median(shifted)), 4),
        "moved_p99_m": round(float(np.percentile(shifted, 99)), 4),
        "moved_max_m": round(float(shifted.max()), 4),
        "clamped_share_of_the_province": round(100 * clamped, 3),
        "clamp_m": round(FILL_QUANTISATION_M, 4),
        "role": (
            "the interface raster is 3.66 m cells quantised to 3.9 m in Z, so it draws the "
            "ocean shelf and the map's edge as terraces -- flat plateaus with blocky "
            "outlines that no kernel can un-terrace, because those steps are real in the "
            "data and are not in the world. Low-passed at its own cell size, normalised "
            "over the province so no landscape measurement is dragged into it, and faded by "
            "its own weight so the province boundary is not drawn either."
        ),
        "why_not_marching_squares": (
            "smoothing each level's indicator by one kernel and summing them is, by the "
            "linearity of a convolution, the same array as smoothing the level field "
            "itself. The spline along the marching-squares contour and this convolution are "
            "the same operation, and only one of them needs a polyline extracted from "
            "56 million texels."
        ),
    }


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


def calibrate_biome(biome: dict, sheet, image_mod) -> dict:
    """Score the pin by the artwork's own edges, and sweep for one that beats it.

    The statistic is the ratio of the sheet's mean edge strength ON the biome raster's area
    boundaries to its mean edge strength everywhere. A boundary that is pinned right lies on
    a shore or a scarp the map draws; one that is pinned wrong lies on flat fill. Being a
    ratio, it cannot be won by a pin that simply produces more boundary.

    The ruler is the sheet this run decoded out of the container, not the PNG a different
    tool may or may not have written beside it. That used to be a skip path -- no
    ``map.png``, no measurement -- and it is gone: the sheet is now read for the detail
    borrow anyway, so the pin is scored against the artwork every single run.
    """
    grey = np.asarray(
        sheet.convert("L").resize((CALIBRATION_PX, CALIBRATION_PX), image_mod.LANCZOS),
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
        "ruler": (
            f"the game's own {SHEET_PX} px map sheet, decoded from its four BC1 slices in "
            "this same run"
        ),
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


def region_table_is_current(biome: dict) -> dict:
    """Is the committed 256 m region table still this raster's own majority downsample?

    It used to be a different question. ``data/region_names.json`` was a hand trace of a wiki
    image, so its 68% agreement with the raster was independent evidence -- weak evidence,
    reported and never used to pin anything, but somebody else's reading of the same world.
    It is derived from THIS asset now, by ``tools/gen_region_names.py``, so agreement is no
    longer evidence about anything: it is either 100% or the committed table is stale.

    Which makes it worth more than it was. A regenerated render and a committed region table
    that disagree mean the game changed under one of them, and this is the run that notices.
    The name policy is read out of the table's own ``_meta`` rather than imported, so this
    stays a check on the artifact rather than a second copy of the rules that made it.
    """
    if not REGION_TABLE.is_file():
        return {"skipped": f"{REGION_TABLE.name} is not present, so nothing was compared"}
    table = json.loads(REGION_TABLE.read_text(encoding="utf-8"))
    display = (table.get("_meta") or {}).get("area_display_names")
    if not isinstance(display, dict):
        return {"skipped": f"{REGION_TABLE.name} carries no _meta.area_display_names to read"}
    meta, grid, legend = table["grid_meta"], table["region_grid"], table["legend"]
    cell, gx0, gy0 = meta["cell"], meta["x0"], meta["y0"]
    x0, x1 = BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100
    y0, y1 = BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100
    width, assets = biome["width"], biome["assets_by_index"]

    compared = agree = 0
    disagreements: dict[str, int] = {}
    for j, row in enumerate(grid):
        for i, letter in enumerate(row):
            if letter == meta["void"]:
                continue
            u = [round((gx0 + (i + k) * cell - x0) / (x1 - x0) * width) for k in (0, 1)]
            v = [round((gy0 + (j + k) * cell - y0) / (y1 - y0) * width) for k in (0, 1)]
            u = [max(0, min(width, value)) for value in u]
            v = [max(0, min(width, value)) for value in v]
            if u[1] <= u[0] or v[1] <= v[0]:
                continue
            values, counts = np.unique(biome["area"][v[0] : v[1], u[0] : u[1]], return_counts=True)
            asset = assets[int(values[counts.argmax()])]
            compared += 1
            want = legend[letter]
            got = display.get(asset or "", display.get("", want))
            if got == want:
                agree += 1
            else:
                key = f"{want} -> {got}"
                disagreements[key] = disagreements.get(key, 0) + 1
    worst = sorted(disagreements.items(), key=lambda kv: -kv[1])[:5]
    return {
        "source": "data/region_names.json, derived from this same asset by gen_region_names.py",
        "cells_compared": compared,
        "cells_agreeing": agree,
        "agreement_pct": round(100 * agree / compared, 1) if compared else None,
        "largest_disagreements": [f"{key} ({count} cells)" for key, count in worst],
        "table_is_current": compared > 0 and agree == compared,
        "reading": (
            "100% or the committed region table was cut from a different build of this "
            "asset, and the fix is to re-run tools/gen_region_names.py. Not a pin and never "
            "was: the corners come from the edge ratio next door."
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


def grid_position(coordinate: np.ndarray, origin: float, spacing: float, limit: int):
    """Where a run of world coordinates falls on a raster's index axis, clamped to it.

    Clamped rather than masked: the frame is half a metre wider than the field's last vertex
    on two sides, and a strip of no-data half a pixel wide down the east and south edges
    would be a hole this file invented. Past the edge the nearest vertex is the honest
    answer, and it is the same answer the field's own reader gives.
    """
    return np.clip((coordinate - origin) / spacing, 0.0, limit - 1.0)


def taps_linear(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The two flanking indices and their weights: ``(2, N)`` each. Plain bilinear.

    Kept, and not as a legacy: it is what the cubic kernel below falls back to wherever its
    own stencil runs off the data, and it is what every category plane is sampled with,
    because a coverage fraction outside [0, 1] is not a coverage fraction.
    """
    low = np.minimum(np.floor(position).astype(np.int64), max(limit - 2, 0))
    fraction = (position - low).astype(np.float32)
    index = np.stack([low, np.minimum(low + 1, limit - 1)])
    return index, np.stack([1.0 - fraction, fraction])


def taps_cubic(position: np.ndarray, limit: int) -> tuple[np.ndarray, np.ndarray]:
    """The four indices around a position and their Catmull-Rom weights: ``(4, N)`` each.

    Cubic convolution with a = -1/2, which is the interpolating member of that family: it
    passes through every sample it is given, and -- the reason it is here rather than
    bilinear -- it is **C1**, so the surface it draws has a continuous first derivative. The
    hillshade is a function of that derivative, and a C0 kernel sampled at half its own
    texel spacing rules the relief into 1 m squares. The weights sum to exactly one, which
    is what lets the no-data bookkeeping below use their sum as a completeness test.

    Indices are clamped to the grid, so a stencil hanging off the edge repeats the edge
    vertex -- the same answer the field's own reader gives past its last row.
    """
    base = np.floor(position).astype(np.int64)
    t = (position - base).astype(np.float32)
    index = np.stack([np.clip(base + offset, 0, limit - 1) for offset in (-1, 0, 1, 2)])
    weight = np.stack(
        [
            0.5 * t * (t * (2.0 - t) - 1.0),
            0.5 * (t * t * (3.0 * t - 5.0) + 2.0),
            0.5 * t * (t * (4.0 - 3.0 * t) + 1.0),
            0.5 * t * t * (t - 1.0),
        ]
    )
    return index, weight


def resample(raster: np.ndarray, rows, cols, nodata: int | None):
    """Separable interpolation of ``raster`` onto the output grid. Returns (sum, weight).

    Separable and in that order for a reason that is arithmetic rather than taste: the
    output rows a band needs come from one CONTIGUOUS run of source rows, so the row axis is
    a slice and only the column axis is a gather. Interpolating in x first over that short
    slab and in y second over the result is four gathers of the small array and four of the
    large one, against sixteen of the large one if the 4x4 stencil were evaluated directly.

    The no-data bookkeeping rides along: every tap is multiplied by whether its texel had a
    value, and the weights come back separately, so the caller can tell a whole stencil
    (weight exactly one) from a partial one from nothing at all. ``nodata`` of ``None`` says
    the raster has no holes -- a category coverage plane -- and skips it.
    """
    (row_index, row_weight), (col_index, col_weight) = rows, cols
    low, high = int(row_index.min()), int(row_index.max())
    slab = raster[low : high + 1]
    values = slab.astype(np.float32)
    known = None if nodata is None else (slab != nodata).astype(np.float32)
    if known is not None:
        values = values * known

    across = np.zeros((slab.shape[0], col_index.shape[1]), np.float32)
    across_weight = np.zeros_like(across)
    for tap in range(col_index.shape[0]):
        across += col_weight[tap] * values[:, col_index[tap]]
        if known is None:
            across_weight += col_weight[tap]
        else:
            across_weight += col_weight[tap] * known[:, col_index[tap]]

    total = np.zeros((row_index.shape[1], col_index.shape[1]), np.float32)
    total_weight = np.zeros_like(total)
    for tap in range(row_index.shape[0]):
        picked = row_index[tap] - low
        total += row_weight[tap][:, None] * across[picked]
        total_weight += row_weight[tap][:, None] * across_weight[picked]
    return total, total_weight


#: How far the cubic stencil's own weights may fall from one before this file stops
#: believing it. They sum to one exactly wherever every texel under the stencil has a value,
#: so anything below this is a stencil straddling the edge of the data, where a kernel with
#: negative lobes has no business extrapolating.
STENCIL_WHOLE = 1.0 - 1e-4


def sample_surface(raster: np.ndarray, cubic, linear, nodata: int):
    """A height raster on the output grid: cubic inside the data, bilinear at its edge.

    Returns ``(values, missing)``. Three cases, and the middle one is the whole reason this
    is not one call: where the 4x4 stencil is whole, the C1 answer is used and the hillshade
    is smooth; where it is not -- a fifth of this field is no-data, so that boundary is real
    and long -- the 2x2 answer is used instead, because a cubic kernel has negative lobes
    and one straddling a hole overshoots; and where even that has nothing under it, the
    render says nothing and the caller paints the page's own sea.
    """
    smooth, smooth_weight = resample(raster, *cubic, nodata)
    flat, flat_weight = resample(raster, *linear, nodata)
    missing = flat_weight <= 0.0
    near = flat / np.where(missing, 1.0, flat_weight)
    return np.where(smooth_weight >= STENCIL_WHOLE, smooth, near), missing


def sample_plain(raster: np.ndarray, taps) -> np.ndarray:
    """A raster with no holes in it, interpolated onto the output grid. Nothing clipped.

    The weights of either kernel sum to one and there is no no-data to renormalise around,
    so the weighted sum IS the answer. Used for the two rasters this file makes itself --
    the artwork's signed high pass and the feathered province mask -- neither of which is a
    coverage and neither of which may be clipped into [0, 1] on the way through.
    """
    return resample(raster, *taps, None)[0]


def sample_coverage(plane: np.ndarray, taps) -> np.ndarray:
    """What fraction of the ground under each output pixel is in some category, in [0, 1].

    Bilinear and never cubic: a category is a yes or a no on a 1 m grid, and what is wanted
    from it is coverage. A kernel with negative lobes would answer -0.06 of a texel wet,
    which is not a thing a texel can be.
    """
    return np.clip(sample_plain(plane, taps), 0.0, 1.0)


# --------------------------------------------------------------------------------------
# The direct pass: the same rocks the field is built from, rasterised into THIS grid.
# --------------------------------------------------------------------------------------


def read_cliff_geometry(store, scripts, index, classes, progress: bool = True) -> dict:
    """The world's placements and the finest triangles every placed rock ships.

    Two calls into ``tools/gen_world_heightmap.py`` and no third opinion about either. The
    sweep is the same pass over the same 4,521 ``*.umap`` the field was cut from, and the
    decode takes the same finest-source ladder -- the Nanite leaf where there is one, LOD 0
    where there is not, the collision hull where neither parses -- over the same
    hull-equivalent mesh set.

    The one thing done here is the generator's own per-triangle bounds clamp, hoisted OUT
    of the placement loop. It is a test in the mesh's own local space against the mesh's own
    padded ``ExtendedBounds``, so it gives the same answer for all two hundred copies of a
    rock, and doing it once per mesh instead of once per placement is a deletion rather than
    a change: the surviving triangles are the same triangles.
    """
    sweep = gen.sweep_levels(
        store, scripts, classes, gen.MeshBounds(store, scripts, index), progress
    )
    read = gen.read_mesh_geometry(store, scripts, index, sweep["meshes"], progress)
    geometry: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    clamped = 0
    for mesh, (verts, tris, low, high) in read["geometry"].items():
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
        if tris.size:
            geometry[mesh] = (verts, np.ascontiguousarray(tris))
    return {
        "sweep": sweep,
        "geometry": geometry,
        "meshes": len(geometry),
        "by_source": read["by_source"],
        "verts": int(sum(v.shape[0] for v, _t in geometry.values())),
        "tris": int(sum(t.shape[0] for _v, t in geometry.values())),
        "triangles_out_of_bounds": clamped,
        "seconds_sweep": round(sweep["seconds"], 1),
        "seconds_decode": round(read["seconds"], 1),
    }


def direct_placements(sweep: dict, geometry: dict) -> tuple[list, dict]:
    """Every placement the field's own cliff layer rasterises, with its world Y span.

    The four culls are the generator's, in the generator's order and for the generator's
    reasons -- an excluded owner, a mesh with no cooked geometry, an arch (a max-Z field
    puts an arch ROOF over the ground beneath it), an oversized shell (the sky dome and the
    ocean). Any of them applied differently here would draw a render of a different world
    from the field it is blended with, which is the one failure this pass cannot be allowed.

    What is added is the **Y span**, in world centimetres, of the placement's transformed
    vertex box. That is the whole of the band selection: a band is a run of output rows, a
    rock is tens of metres, and a bounding-interval test over 24,000 placements is a numpy
    comparison rather than a search.
    """
    meshes, owners = sweep["meshes"], sweep["owners"]
    arch_ids = {i for i, m in enumerate(meshes) if gen.ARCH_MARK in m.rsplit("/", 1)[-1]}
    windings = {mesh: gen.winding_sign(v, t) for mesh, (v, t) in geometry.items()}
    corners = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], np.float32)
    prepared: list[tuple] = []
    dropped = {"owner": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    for row in sweep["placements"]:
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in gen.EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh not in geometry:
            dropped["no_geometry"] += 1
            continue
        if mesh_id in arch_ids:
            dropped["arch"] += 1
            continue
        verts, _tris = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > gen.OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = gen.rotation_matrix(*row[5:8]).astype(np.float32)
        offset = row[2:5].astype(np.float32)
        low, high = verts.min(0), verts.max(0)
        box = low + corners * (high - low)
        world_y = ((box * scale) @ matrix + offset)[:, 1]
        facing = windings[mesh] * float(np.sign(scale[0] * scale[1] * scale[2]))
        prepared.append(
            (
                mesh,
                mesh_id,
                matrix,
                scale,
                offset,
                facing,
                float(world_y.min()),
                float(world_y.max()),
            )
        )
    return prepared, dropped


def rasterise_direct_band(
    prepared: list,
    geometry: dict,
    x0_cm: float,
    y0_cm: float,
    scale_cm: float,
    rows: int,
    cols: int,
    subsamples: int,
) -> np.ndarray:
    """One band of the output, max-Z rasterised from the triangles. ``nan`` where none fell.

    The rasteriser is ``gen_world_heightmap.MaxZRaster`` itself, pointed at a grid whose
    origin is this band's north-west corner and whose spacing is this render's, divided by
    the sub-sampling. Its convention -- sample at ``col + 0.5`` in grid units, write to
    ``col`` -- is exactly ``frame_coordinates``' pixel centres when the origin is the
    frame's own corner, so the two grids are the same grid and nothing is half a texel out.

    The facing cull runs per placement, as it does in the generator, and then the triangles
    are cut down to the ones whose own Y interval reaches this band. Both are ``numpy``
    over the placement's whole mesh, which is the cheap end of the only Python loop here.
    """
    raster = gen.MaxZRaster(
        cols * subsamples, rows * subsamples, x0_cm, y0_cm, scale_cm / subsamples
    )
    y_lo = y0_cm
    y_hi = y0_cm + rows * scale_cm
    for mesh, mesh_id, matrix, scale, offset, facing, span_lo, span_hi in prepared:
        if span_hi < y_lo or span_lo > y_hi:
            continue
        verts, tris = geometry[mesh]
        world = (verts * scale) @ matrix + offset
        if facing != 0.0:
            corner = world[tris[:, 0]]
            normals = np.cross(world[tris[:, 1]] - corner, world[tris[:, 2]] - corner)
            tris = tris[(normals[:, 2] * facing) > 0]
            if not tris.size:
                continue
        ty = world[:, 1][tris]
        tris = tris[(ty.max(1) >= y_lo) & (ty.min(1) <= y_hi)]
        if not tris.size:
            continue
        raster.add(world[tris], mesh_id + 1)
    return raster.result()[0]


def reduce_direct(sub_z: np.ndarray, rows: int, cols: int, subsamples: int):
    """A sub-sampled band folded onto the output grid: mean height and coverage count.

    The mean is over the sub-samples that HIT something, not over all of them, and the
    count is returned beside it -- which is the difference between "this texel is half a
    rock and half the ground behind it" and "this texel is a rock at half its height".
    """
    if subsamples == 1:
        hit = np.isfinite(sub_z)
        return np.where(hit, sub_z, 0.0).astype(np.float32), hit.astype(np.uint8)
    block = sub_z.reshape(rows, subsamples, cols, subsamples)
    hit = np.isfinite(block)
    count = hit.sum((1, 3)).astype(np.uint8)
    total = np.where(hit, block, 0.0).sum((1, 3), dtype=np.float32)
    return (total / np.maximum(count, 1)).astype(np.float32), count


def tent_coverage(z_cm: np.ndarray, coverage: np.ndarray):
    """The 3x3 tent that antialiases a direct silhouette, heights carried by coverage.

    Separable 1-2-1 in each axis over the coverage, and the same kernel over ``z*coverage``
    divided back by it. That last part is the whole of it: a plain blur of the heights would
    pull zeros in from outside the rock and draw a trench around every silhouette, while a
    coverage-weighted one gives a texel one quarter covered the rock's own edge height at a
    quarter weight, which is what a quarter-covered texel is.

    Rows only for the halo's sake: the band arrives with ``BAND_HALO`` rows on each side, so
    a 3-tap in Y never sees a band edge, and the columns are the full width already.
    """
    weighted = z_cm * coverage
    for axis in (0, 1):
        coverage = ndimage.convolve1d(coverage, COVERAGE_TENT, axis=axis, mode="nearest")
        weighted = ndimage.convolve1d(weighted, COVERAGE_TENT, axis=axis, mode="nearest")
    return weighted / np.maximum(coverage, 1e-6), coverage


def direct_cache_dir(out_dir: Path) -> Path:
    return out_dir / RENDERS_DIR_NAME / DIRECT_CACHE_DIR_NAME


def direct_cache_stamp(size: int, subsamples: int, build: str | None) -> dict:
    """What a cached direct raster has to agree with before it is drawn from.

    Three things, and each one of them is a different picture if it moves: the grid it was
    rasterised onto, how finely it sampled each texel of that grid, and the build of the
    game whose rocks it is. Anything else about it -- how long it took, how many triangles
    fell in -- is a record rather than a key.
    """
    return {"size": int(size), "subsamples": int(subsamples), "game_version_pinned": build}


def cached_direct(directory: Path, stamp: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """The cached raster as two read-only memory maps, or ``None`` if it is not this one."""
    try:
        recorded = json.loads((directory / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(recorded, dict) or {k: recorded.get(k) for k in stamp} != stamp:
        return None
    size = stamp["size"]
    try:
        return (
            np.memmap(directory / DIRECT_Z_NAME, np.float32, "r", shape=(size, size)),
            np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "r", shape=(size, size)),
        )
    except (OSError, ValueError):
        return None


def rasterise_direct(
    prepared: list,
    geometry: dict,
    directory: Path,
    size: int,
    subsamples: int,
    stamp: dict,
    progress: bool,
) -> dict:
    """Rasterise every placed rock into the render's own grid, banded, onto disk.

    Banded because the alternative is not a slower run, it is no run: a 32768 square of
    float32 is 4.3 GB, the render already holds 3.2 GB of output, and a machine that has to
    hold both to draw a picture is a machine-dependent generator. 256 rows of it is 34 MB.

    On disk because the answer is the same for both layers and rasterising 216 M triangles
    is not something to do twice for a change of palette. The two maps are written into
    place beside a sidecar naming what they are of, and ``cached_direct`` refuses anything
    that does not match rather than drawing last week's rocks under this week's field.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / DIRECT_CACHE_SIDECAR).unlink(missing_ok=True)
    z = np.memmap(directory / DIRECT_Z_NAME, np.float32, "w+", shape=(size, size))
    coverage = np.memmap(directory / DIRECT_COVERAGE_NAME, np.uint8, "w+", shape=(size, size))
    step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / size
    x0_cm = BOUNDS_M["x_min_m"] * 100
    covered = 0
    started = time.time()
    for band, top in enumerate(range(0, size, DIRECT_BAND_ROWS)):
        bottom = min(top + DIRECT_BAND_ROWS, size)
        rows = bottom - top
        sub = rasterise_direct_band(
            prepared,
            geometry,
            x0_cm,
            BOUNDS_M["y_min_m"] * 100 + top * step_cm,
            step_cm,
            rows,
            size,
            subsamples,
        )
        band_z, band_coverage = reduce_direct(sub, rows, size, subsamples)
        z[top:bottom] = band_z
        coverage[top:bottom] = band_coverage
        covered += int(np.count_nonzero(band_coverage))
        if progress and band % 8 == 0:
            print(
                f"  direct: {bottom / size:5.1%} of {size}x{size} at "
                f"{step_cm / 100 / subsamples:.4f} m, {covered / 1e6:.1f} M texels, "
                f"{time.time() - started:5.1f}s",
                flush=True,
            )
    z.flush()
    coverage.flush()
    del z, coverage
    stats = {
        **stamp,
        "sub_texel_m": round(step_cm / 100 / subsamples, 5),
        "texels_with_geometry": covered,
        "share_of_the_sheet": round(100 * covered / (size * size), 3),
        "seconds": round(time.time() - started, 1),
        "band_rows": DIRECT_BAND_ROWS,
        "role": (
            "max-Z of the cliff geometry on this render's own grid, in world centimetres, "
            "with the count of sub-samples that hit something beside it. Written once and "
            "read by every layer; deleted at the end of the run unless --keep-direct."
        ),
    }
    (directory / DIRECT_CACHE_SIDECAR).write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


# --------------------------------------------------------------------------------------
# The seam, measured along a line rather than at a probe.
# --------------------------------------------------------------------------------------


class SeamTrace:
    """Second differences of the drawn height at the join, and what they can be read against.

    The parked design was explicit that the seam had to be validated by a **trace** and not
    by probes: probes are sparse relative to a seam, and a ridge one texel wide along a
    density contour is invisible to all of them. That part held and is what this measures --
    ``|d2z/dx2|`` along every row of every band, over the whole 32768 square, with each
    3-texel stencil sorted by the weights under all three of its texels.

    What did not hold is the **reference**, and it is worth writing down what happened,
    because three of them were tried and each one turned out to be measuring something other
    than the sampler:

    1. *A window around w = 0.5.* A feather's second derivative is zero at its own midpoint
       by symmetry -- it lives at the shoulders -- so this pooled the one place a hard join
       has nothing to show. It passed a switch.
    2. *The two pure regimes beside the join.* The right reference when the two regimes are
       two RECONSTRUCTIONS of one surface, which is what the design assumed. This file
       composites a rock onto a lattice by the rock's own coverage, so the join IS the rock's
       silhouette: a real cliff edge, where enormous curvature is the correct answer. It
       reads 138 on the shipped render and every bit of that is terrain.
    3. *The same comparison restricted to where the two surfaces agree.* Which moves the join
       from the silhouette to the rock's BASE -- also a real feature, also real curvature.

    4. And the counterfactual, the hard ``max`` over the same texels, which is the only
       reference here that is not the terrain. It is reported and it is **not a bound**,
       because it cannot fail: a convex blend of two surfaces is bounded by the extreme
       points of that blend, and rounding the weight to 0 or 1 is exactly those. What the
       number says is how much of that ceiling the fade actually spends -- 0.5 on the shipped
       render, so the blend is half as curved at the join as the switch it replaces -- and
       that is a description rather than a gate.

    What guarantees the smoothness this design was after is therefore the arithmetic and not
    this statistic: ``blend_regimes`` is a convex combination in a coverage that the tent
    reconstructs continuously, plus a positive part smoothed to C-infinity by
    ``DIRECT_LIFT_KNEE_M``. The trace is kept because it is evidence, and because the
    as-designed composition it was written for did pass it -- 0.6155 against the design's
    bound of 1.5, recorded in ``docs/spatial-and-map.md``.
    """

    def __init__(self) -> None:
        self.pools: dict[str, list[np.ndarray]] = {
            "seam": [],
            "switch": [],
            "pure_direct": [],
            "pure_kernel": [],
            "seam_same_surface": [],
            "pure_same_surface": [],
        }
        self.rows = 0

    @staticmethod
    def _thin(values: np.ndarray) -> np.ndarray:
        """A systematic sample of a pool, so the whole sheet costs a bounded number of MB.

        Every k-th value of a selection that is already in raster order, which for a
        percentile is a sample and not a filter: the pools run to tens of millions of texels
        over 32768 rows and the statistic taken from them moves in the fourth decimal.
        """
        stride = max(1, values.size // SEAM_SAMPLE_MAX_PER_BAND)
        return values[::stride].astype(np.float32)

    def _keep(self, name: str, curvature: np.ndarray, mask: np.ndarray) -> None:
        if mask.any():
            self.pools[name].append(self._thin(curvature[mask]))

    def add(self, z_m, z_switched, w, spacing_m: float, delta=None) -> None:
        blended = np.abs(np.diff(z_m, n=2, axis=1)) / (spacing_m * spacing_m)
        switched = np.abs(np.diff(z_switched, n=2, axis=1)) / (spacing_m * spacing_m)
        # A second difference reads three texels, so it belongs to the regime all three of
        # them are in -- and to the join if they are not all in one. Classifying it by the
        # middle weight alone is what let a sharp join hide: the curvature of the join lands
        # one texel to the side of it, in a stencil whose middle texel is still pure, and is
        # counted as evidence that the pure regime is rough.
        low, middle, high = w[:, :-2], w[:, 1:-1], w[:, 2:]
        top = np.maximum(np.maximum(low, middle), high)
        bottom = np.minimum(np.minimum(low, middle), high)
        at_seam = (top > SEAM_PURE) & (bottom < 1.0 - SEAM_PURE)
        self.rows += z_m.shape[0]
        if not at_seam.any():
            return
        near = ndimage.maximum_filter1d(at_seam, 2 * SEAM_NEAR_TEXELS + 1, axis=1, mode="nearest")
        self._keep("seam", blended, at_seam)
        self._keep("switch", switched, at_seam)
        self._keep("pure_direct", blended, near & (bottom >= 1.0 - SEAM_PURE))
        self._keep("pure_kernel", blended, near & (top <= SEAM_PURE))
        if delta is None:
            return
        gap = np.abs(delta)
        same = np.minimum(np.minimum(gap[:, :-2], gap[:, 1:-1]), gap[:, 2:]) <= SEAM_SAME_SURFACE_M
        self._keep("seam_same_surface", blended, at_seam & same)
        self._keep("pure_same_surface", blended, near & same & ~at_seam)

    def result(self) -> dict:
        pooled = {
            name: (np.concatenate(values) if values else np.zeros(0, np.float32))
            for name, values in self.pools.items()
        }
        p99 = {
            name: float(np.percentile(values, 99)) if values.size else None
            for name, values in pooled.items()
        }
        if p99["seam"] is None or not p99["switch"]:
            return {
                "measured": False,
                "why": (
                    "no 3-texel stencil straddled the join, which is what a render with no "
                    "rocks in it looks like"
                ),
            }

        def ratio(over: str) -> float | None:
            return None if not p99[over] else round(p99["seam"] / p99[over], 4)

        reference = [p99["pure_direct"], p99["pure_kernel"]]
        beside = max([v for v in reference if v is not None], default=None)
        return {
            "measured": True,
            "method": (
                "|d2z/dx2| along every row of the drawn height, in 1/m. Every 3-texel "
                "stencil is sorted by the weights under ALL THREE of its texels: straddling "
                f"the join, wholly direct (every weight within {SEAM_PURE} of 1) or wholly "
                f"kernel (every weight within {SEAM_PURE} of 0). The two pure pools are "
                f"further restricted to within {SEAM_NEAR_TEXELS} texels of a straddling "
                "stencil. A fourth pool is the height a HARD MAX would have drawn over the "
                "straddling stencils themselves."
            ),
            "texels": {name: int(values.size) for name, values in pooled.items()},
            "p99_curvature": {
                name: (None if value is None else round(value, 5)) for name, value in p99.items()
            },
            "share_of_a_hard_switch": ratio("switch"),
            "share_of_a_hard_switch_ceiling": SEAM_SWITCH_CEILING,
            "against_the_pure_regimes": (None if not beside else round(p99["seam"] / beside, 4)),
            "against_the_terrain_where_the_surfaces_agree": ratio("pure_same_surface"),
            "surfaces_agree_within_m": SEAM_SAME_SURFACE_M,
            "reading": (
                "share_of_a_hard_switch is the number to read and it is a DESCRIPTION, not a "
                "gate: a convex blend of two surfaces cannot be rougher than the switch "
                "between them, because rounding the weight to 0 or 1 is the extreme point of "
                "that blend, so this can never exceed 1 and never fail. What it says is how "
                "much of that ceiling the fade spends. The two numbers beside it are the "
                "design's own reference and a repair of it, and both measure the TERRAIN "
                "rather than the sampler once the composition is by coverage, because then "
                "every join lies on a geometric feature -- the rock's silhouette, or its "
                "base. They are recorded because they were tried. What guarantees the "
                "smoothness is the arithmetic: a convex combination in a continuously "
                "reconstructed coverage, plus a positive part smoothed to C-infinity."
            ),
        }


class RegimeCoverage:
    """How much of the sheet each regime drew, per province of the field underneath it.

    Counted rather than argued, because "the geometry answers this pixel" is a claim about
    how much of a picture. The provinces are the field's own, sampled nearest at output
    resolution -- a province is a name and the average of two names is not one.

    The direct bucket is **split by the density plane**, and that split is the whole of what
    that plane does here now. It does not decide whether the triangles are drawn -- the
    rock's own coverage decides that, because whether a pixel stands on a rock is what
    settles whether the rock is its surface. What the plane settles is what the drawn answer
    IS: a texel a source vertex landed in is a measurement, and a texel the rasteriser
    reached by interpolating the plane of a triangle wider than itself is a facet. Both are
    the geometry and both beat a reconstruction of the 1 m fold of that same geometry; only
    one of them is a measurement, and the sidecar says which is which rather than letting a
    reader assume.
    """

    def __init__(self) -> None:
        self.counts: dict[int, list[int]] = {}
        self.weight: dict[int, float] = {}

    def add(self, prov: np.ndarray, w: np.ndarray, measured: np.ndarray) -> None:
        regime = np.where(
            w >= 1.0 - SEAM_PURE,
            np.where(measured, 0, 1),
            np.where(w > SEAM_PURE, 2, 3),
        )
        for value in np.unique(prov):
            key = int(value)
            row = self.counts.setdefault(key, [0, 0, 0, 0])
            here = prov == value
            picked = regime[here]
            for index in range(4):
                row[index] += int(np.count_nonzero(picked == index))
            self.weight[key] = self.weight.get(key, 0.0) + float(w[here].sum())

    NAMES = ("direct_measured", "direct_facet", "faded", "kernel")

    def result(self) -> dict:
        total = sum(sum(row) for row in self.counts.values()) or 1
        out = {
            "definition": (
                f"direct: coverage >= {1 - SEAM_PURE}, split by density.u8.z into the texels "
                "a source vertex landed in (a measurement) and the texels the rasteriser "
                "reached across a triangle wider than the output texel (a facet); faded: "
                f"{SEAM_PURE} < coverage < {1 - SEAM_PURE}, a silhouette; kernel: coverage "
                f"<= {SEAM_PURE}, the landscape and fill lattices alone. mean_w beside them "
                "is the unbucketed answer: how much of the height over that province the "
                "rasterised rocks contributed, averaged."
            ),
            "per_province_pct_of_sheet": {},
        }
        for value, row in sorted(self.counts.items()):
            name = hf.PROV_NAMES.get(value, f"layer {value}")
            here = sum(row) or 1
            out["per_province_pct_of_sheet"][name] = {
                **{key: round(100 * row[i] / total, 4) for i, key in enumerate(self.NAMES)},
                "province_pct_of_sheet": round(100 * here / total, 4),
                "mean_w": round(self.weight.get(value, 0.0) / here, 5),
            }
        pooled = [sum(row[i] for row in self.counts.values()) for i in range(4)]
        out["sheet_pct"] = {
            **{key: round(100 * pooled[i] / total, 4) for i, key in enumerate(self.NAMES)},
            "mean_w": round(sum(self.weight.values()) / total, 5),
        }
        return out


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


def water_alpha(z_m, water_m, wet: np.ndarray, measured: np.ndarray, blur_px: float):
    """How much of each pixel is water, in [0, 1]. Feathered three ways, for three reasons.

    ``wet`` is the coverage the quality byte gives -- what fraction of the ground under this
    pixel the channel calls water at all -- and it is already a fraction rather than a step,
    because it was sampled as coverage. ``measured`` is the share of that water whose DEPTH
    was measured against 1 m terrain.

    The depth feather is the first, and it now applies only where there is a depth: the
    water thins out over the last ``WATER_EDGE_M`` and the blend goes with it, which is what
    a beach looks like. Where the byte says the level is known and the depth is not, that
    ramp is meaningless -- the "depth" there is a 3.9 m block raster's rounding error, and
    running the ramp on it is precisely what used to erase three and a half square
    kilometres of ocean -- so those texels are drawn at **full alpha** and the ramp is not
    consulted.

    The third is in SPACE, because a beach is not what most of this world's water has: a
    great deal of it sits in box-shaped bodies against a cliff, where the depth goes from
    nothing to metres across one texel and there is no band for the depth feather to work
    in. A blur under a metre wide is what antialiases those, and being under a metre it
    cannot move a shoreline, only stop it being a staircase.
    """
    ramp_alpha = np.clip((water_m - z_m) / WATER_EDGE_M, 0.0, 1.0)
    alpha = wet * (measured * ramp_alpha + (1.0 - measured))
    return ndimage.gaussian_filter(alpha, blur_px, mode="nearest")


def water_depth_fraction(z_m, water_m, measured: np.ndarray) -> np.ndarray:
    """How dark the water reads, in [0, 1]: measured depth where there is one, deep where not.

    The colour ramp wants a depth and a level-only texel has none. Drawing those at the
    shallow end -- which is what subtracting a 3.9 m raster from a sea surface gives -- would
    paint the open ocean the pale green of an ankle-deep sheet. They are drawn at the deep
    end instead, and that is a measurement rather than a preference: on the shipped field
    95.2% of level-only water stands over the fill province and 98% of its surface levels lie
    inside a 0.7 m band around the ocean's own -16.99 m. It *is* the ocean.
    """
    known = np.clip((water_m - z_m) / WATER_DEPTH_FULL_M, 0.0, 1.0)
    return measured * known + (1.0 - measured)


def water_over(rgb, depth, alpha, shade, shallow, deep):
    """Lay water over ground, tinted by its own depth. Both layers want this arithmetic.

    Shallow reads pale, deep reads dark, and the light touches it a little -- but only a
    little, because the hillshade under a lake is computed from the lake BED and that is not
    what a water surface looks like from above.
    """
    tint = depth[..., None]
    colour = (shallow * (1 - tint) + deep * tint) * (
        WATER_SHADE_FLOOR + WATER_SHADE_RANGE * shade[..., None]
    )
    weight = alpha[..., None]
    return rgb * (1 - weight) + colour * weight


def terrain_colours(depth, wet, missing, shade, borrow, z_m, ramp_lo, ramp_hi, **_unused):
    """The approved preview at full resolution: ramp, shade, borrowed detail, water, silence.

    Deliberately the same arithmetic as the preview the owner picked, scaled up rather than
    re-tuned. Two things are not the same. The resolution the hillshade is computed at is
    0.458 m to the pixel instead of 4, which is not a change of recipe -- it is the recipe
    finally seeing the field it was always sampling. And ``borrow`` multiplies in the
    artwork's own light over the provinces where the field has none of its own, which is a
    change of recipe and is why this is recipe 2.
    """
    height = np.clip((z_m - ramp_lo) / max(ramp_hi - ramp_lo, 1e-6), 0.0, 1.0)
    rgb = ramp(height, RAMP_STOPS) * (shade * borrow)[..., None]
    rgb = water_over(rgb, depth, wet, shade, WATER_SHALLOW, WATER_DEEP)
    return np.where(missing[..., None], SEA_RGB, rgb)


def satellite_colours(depth, wet, missing, shade, borrow, z_m, slope, biome_rgb, noise, **_unused):
    """Ground colour from the biome, then rock, then altitude, then light, then water.

    In that order, and the order is the argument. The biome says what grows there; the slope
    overrules it, because nothing grows on a cliff face and every real image of one is rock;
    the altitude bleaches what is left, because the high plateaus are thin soil and sun; the
    hillshade lights all of it at once, because a shadow falls on rock and canopy alike; and
    the water goes on top, because it is a different surface rather than a different ground.

    ``borrow`` rides with the hillshade and not with the colour, which is the whole design of
    it: what is taken from the artwork is light, and it lands exactly where the field's own
    light is uninformative.
    """
    rock = np.clip((slope - ROCK_LO_DEG) / (ROCK_HI_DEG - ROCK_LO_DEG), 0.0, 1.0)[..., None]
    rgb = biome_rgb * (1 - rock) + ROCK_RGB * rock
    lift = np.clip((z_m - HIGH_LO_M) / (HIGH_HI_M - HIGH_LO_M), 0.0, 1.0)[..., None] * HIGH_LIFT
    rgb = rgb * (1 - lift) + HIGH_RGB * lift
    rgb = rgb * noise[..., None] * (shade * borrow)[..., None]
    rgb = water_over(rgb, depth, wet, shade, SATELLITE_WATER_SHALLOW, SATELLITE_WATER_DEEP)
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


def water_planes(field) -> tuple[np.ndarray | None, np.ndarray | None, str]:
    """The two 0/1 planes the water composite is sampled from, and where they came from.

    ``wet`` is "the channel calls this texel water" and ``measured`` is "and it measured the
    depth". Both come off ``waterq.u8.z`` when the field has one, which is the whole point:
    the comparison they replace -- a water surface standing above the ground -- reads the
    open ocean as dry, because over the fill province the ground is a 3.9 m raster that
    rounds above a sea surface 17 m down.

    A field written before that byte existed still renders, and falls back to exactly what
    it meant then. Missing is not the same as dry and is not read as dry; it is read as "all
    this field can say", which is the comparison.
    """
    water = field._water_raster()
    if water is None:
        return None, None, "no water raster in this field; nothing is drawn as water"
    grades = field._water_quality_raster()
    if grades is None:
        wet = ((water != hf.NODATA) & (water > field._height_dm)).astype(np.uint8)
        return (
            wet,
            wet,
            (
                "no waterq.u8.z: this field predates the quality byte, so submersion falls "
                "back to a water surface standing above the ground, which is all such a "
                "field can say"
            ),
        )
    return (
        (grades != hf.WATER_DRY).astype(np.uint8),
        (grades == hf.WATER_MEASURED).astype(np.uint8),
        "waterq.u8.z: dry / depth measured against 1 m terrain / level known and depth not",
    )


def blend_regimes(base_m, missing, direct, linear, subsamples):
    """The two-regime height and what it was made of: ``(z_m, missing, w, switched)``.

    This is the field's **own composition rule**, performed at the render's spacing instead
    of read back from the 1 m fold that rule already produced. ``gen_world_heightmap.py``
    lays the landscape and the fill down and then lets the cliff overlay win any texel where
    real geometry stands above them; ``base_m`` is that same landscape-and-fill lattice
    interpolated here, and this adds the same rocks on top of it -- rasterised at 0.229 m
    rather than folded onto a metre first.

    ``z = base + w * lift(z_direct - base)`` and every part of it is deliberate.

    ``w`` is the direct raster's **coverage** of the pixel, reconstructed by the tent above
    -- never a choice, never a threshold. It is what makes a rock's silhouette fade over one
    texel instead of stepping over one, and it is the whole of the weight because the
    question "is this pixel standing on the rock" is the whole of what decides whether the
    rock is the surface here.

    ``lift`` is a **smoothed positive part**, and it is the other half of the field's rule:
    a rock may raise the ground and may never lower it. Without it, the tail of a coverage
    that reaches a texel the rock passes UNDER would draw a trench around the base of every
    formation. A hard ``max`` would do the job and put a first-derivative discontinuity
    exactly where the rock meets the ground -- a line the hillshade would draw around the
    base of every formation on the map. The smoothed form is C-infinity, is never negative,
    and sits at most ``DIRECT_LIFT_KNEE_M / 2`` above the hard answer: it rounds the corner
    off the max rather than moving it.

    Where the lattice knows nothing -- inside a formation big enough that no landscape texel
    survives under it -- the caller passes the whole field's own fold as ``base_m`` instead,
    which is what this file drew before. There the coverage is 1 and the rock is the answer
    either way, so the substitution is invisible rather than merely small.
    """
    z_cm, coverage = direct
    coverage = coverage.astype(np.float32)
    if subsamples > 1:
        coverage /= float(subsamples * subsamples)
        fraction = coverage
    else:
        z_cm, fraction = tent_coverage(z_cm, coverage)
    w = np.clip(fraction, 0.0, 1.0).astype(np.float32)
    z_direct_m = z_cm / np.float32(100.0)
    delta = z_direct_m - base_m
    knee = np.float32(DIRECT_LIFT_KNEE_M)
    lift = 0.5 * (delta + np.sqrt(delta * delta + knee * knee))
    z_m = base_m + w * lift
    # Where the field has nothing at all and the geometry does -- a rock standing off the
    # edge of the landscape -- the geometry is the whole answer and the pixel stops being
    # no-data. A switch rather than a fade, and allowed to be one: the no-data boundary is
    # already a hard edge the render paints the page's own sea against.
    only_rock = missing & (fraction > 0.0)
    z_m = np.where(only_rock, z_direct_m, z_m)
    w = np.where(only_rock, np.float32(1.0), w)
    # And the counterfactual, for the seam trace to measure the blend against: the same two
    # surfaces joined by the switch this design refused. Computed here rather than there so
    # the trace never has to be handed two arrays it could pair up wrongly.
    switched = np.where(w >= SEAM_MID, np.maximum(z_direct_m, base_m), base_m)
    return (
        z_m.astype(np.float32),
        missing & (fraction <= 0.0),
        w,
        switched.astype(np.float32),
    )


def render_layer(
    layer,
    field,
    biome_rgb,
    biome,
    borrow,
    size,
    progress,
    height_dm=None,
    direct=None,
    seam=None,
    regimes=None,
    measured_plane_u8=None,
) -> np.ndarray:
    """One whole layer, drawn a band of rows at a time. Returns ``(size, size, 3)`` uint8.

    Banded because the sheet is a billion pixels at 32768 and this recipe holds a dozen
    float32 intermediates over it: whole-sheet arrays would be four gigabytes apiece and the
    run would live or die on how much memory the reader's machine happened to have. Each
    band is computed with BAND_HALO extra rows on both sides and cropped afterwards, so
    neither the hillshade's gradient nor the cubic sampler's stencil nor the water blur's
    kernel nor the direct coverage's tent ever sees a band edge -- a one-sided difference at
    every 256th row would draw 127 horizontal lines across the world.

    ``direct`` is the pair of memory maps the direct pass wrote, with the weight plane and
    the sub-sampling beside them; ``None`` draws recipe 2's single-regime picture. ``seam``
    and ``regimes`` are the two accumulators, passed for the first layer only -- both layers
    draw the identical surface and measuring it twice would be measuring nothing twice.
    """
    painter = LAYER_PAINTERS[layer]
    x_cm, y_cm = frame_coordinates(size)
    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / size
    blur_px = WATER_EDGE_BLUR_M / spacing_m
    detail, province = borrow
    heights = field._height_dm if height_dm is None else height_dm
    ramp_lo, ramp_hi = ramp_range(field)
    noise = noise_fields(NOISE_SEED) if layer == "satellite" else None
    wet_plane, measured_plane, _source = water_planes(field)
    water = field._water_raster()
    out = np.empty((size, size, 3), np.uint8)
    column_index = np.arange(size)

    # The column taps are the same for every band, on both grids the bands sample: the
    # field's 1 m lattice and the artwork's 8192 sheet. Built once.
    field_x = grid_position(x_cm, field.x0_cm, field.spacing_cm, field.width)
    cols_cubic = taps_cubic(field_x, field.width)
    cols_linear = taps_linear(field_x, field.width)
    art_step_cm = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) * 100 / SHEET_PX
    art_x0_cm = BOUNDS_M["x_min_m"] * 100 + art_step_cm / 2
    art_cols = taps_linear(grid_position(x_cm, art_x0_cm, art_step_cm, SHEET_PX), SHEET_PX)
    art_y0_cm = BOUNDS_M["y_min_m"] * 100 + art_step_cm / 2
    biome_cols = biome_index(x_cm, BOUNDS_M["x_min_m"], BOUNDS_M["x_max_m"], biome["width"])
    # Nearest, and never in between: a province is a name, and the regime table counts how
    # much of each one the triangles answered.
    prov_cols = np.clip(
        np.round((x_cm - field.x0_cm) / field.spacing_cm).astype(np.int64), 0, field.width - 1
    )

    started = time.time()
    for top in range(0, size, BAND_ROWS):
        bottom = min(top + BAND_ROWS, size)
        lo = max(top - BAND_HALO, 0)
        hi = min(bottom + BAND_HALO, size)
        field_y = grid_position(y_cm[lo:hi], field.y0_cm, field.spacing_cm, field.height)
        cubic = (taps_cubic(field_y, field.height), cols_cubic)
        linear = (taps_linear(field_y, field.height), cols_linear)

        z_dm, missing = sample_surface(heights, cubic, linear, hf.NODATA)
        z_m = z_dm / np.float32(hf.DM_PER_M)
        weight = None
        if direct is not None:
            direct_z, direct_coverage, ground, subsamples = direct
            # The base the rocks are composited onto is the lattice UNDERNEATH them, not the
            # field's own fold -- see ``ground_lattice``. Where that lattice knows nothing,
            # which is inside a formation, the fold stands in and the rock covers the pixel
            # anyway.
            ground_dm, ground_missing = sample_surface(ground, cubic, linear, hf.NODATA)
            base_m = np.where(ground_missing, z_m, ground_dm / np.float32(hf.DM_PER_M))
            z_m, missing, weight, switched = blend_regimes(
                base_m,
                missing,
                (np.asarray(direct_z[lo:hi], np.float32), np.asarray(direct_coverage[lo:hi])),
                linear,
                subsamples,
            )
            if seam is not None:
                keep = slice(top - lo, bottom - lo)
                seam.add(
                    z_m[keep],
                    switched[keep],
                    weight[keep],
                    spacing_m,
                    (np.asarray(direct_z[lo:hi], np.float32) / 100.0 - base_m)[keep],
                )
            if regimes is not None:
                prov_rows = np.clip(
                    np.round((y_cm[top:bottom] - field.y0_cm) / field.spacing_cm).astype(np.int64),
                    0,
                    field.height - 1,
                )
                picked = np.ix_(prov_rows, prov_cols)
                regimes.add(
                    field._prov[picked],
                    weight[top - lo : bottom - lo],
                    measured_plane_u8[picked] > 0,
                )
        if wet_plane is None:
            wet = measured = np.zeros(z_m.shape, np.float32)
            water_m = z_m
        else:
            water_dm, _dry = sample_surface(water, cubic, linear, hf.NODATA)
            water_m = water_dm / np.float32(hf.DM_PER_M)
            wet = sample_coverage(wet_plane, linear)
            measured = sample_coverage(measured_plane, linear) / np.where(wet <= 0.0, 1.0, wet)
            measured = np.clip(measured, 0.0, 1.0)

        art_rows = taps_linear(
            grid_position(y_cm[lo:hi], art_y0_cm, art_step_cm, SHEET_PX), SHEET_PX
        )
        strength = sample_plain(province, linear) / 255.0
        lift = 1.0 + BORROW_GAIN * strength * (sample_plain(detail, (art_rows, art_cols)) / 127.0)

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
            depth=water_depth_fraction(z_m, water_m, measured),
            wet=water_alpha(z_m, water_m, wet, measured, blur_px),
            missing=missing,
            shade=shade,
            borrow=np.clip(lift, *BORROW_CLAMP),
            ramp_lo=ramp_lo,
            ramp_hi=ramp_hi,
            **extra,
        )
        out[top:bottom] = np.clip(rgb[top - lo : bottom - lo], 0, 255).astype(np.uint8)
        if progress and (top // BAND_ROWS) % 16 == 0:
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


#: Where a layer sidecar records the heightfield build it was drawn from. The same shape
#: as ``gen_map_image.PIN_PATH`` and ``gen_world_heightmap.PIN_PATH``, read by the same
#: walk -- three guards, three paths, one implementation.
FIELD_PIN_PATH = ("sources", "heightfield", "game_version_pinned")


def pinned_field_build(sidecar: dict) -> str | None:
    """The heightfield build an existing layer sidecar names, or None if it names none."""
    return read_str_path(sidecar.get("_meta"), FIELD_PIN_PATH)


def build_sidecar(
    *,
    layer: str,
    field_meta: dict,
    tiles: dict,
    render: dict,
    extra: dict,
    recipe: int = RECIPE,
    tiles_2x: dict | None = None,
) -> dict:
    """The file the web API reads for this layer, plus the provenance to date it by.

    The four corner keys sit at the top exactly as ``map.json``'s do, and ``_meta.tiles``
    carries the same block, so the endpoint reads a render layer with the code it already
    had for the artwork one. ``_meta.tiles_2x`` is the same block again for the denser tree
    beside it, and its absence is how a layer says it has none -- which is what the artwork
    pyramid, cut by another tool, still says.
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
            "recipe": recipe,
            "recipe_description": RECIPES[recipe],
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
            **({"tiles_2x": tiles_2x} if tiles_2x else {}),
            "staleness": (
                "sources.heightfield.game_version_pinned is the build the field under these "
                "pixels was cut from. tools/gen_map_renders.py refuses to replace this layer "
                "unless the field now on disk names the same build; --force says it anyway. "
                "Terrain moves every patch, and a render that quietly disagrees with the "
                "node tables beside it is exactly the drift this project announces."
            ),
        },
    }


def install_layer(
    sheet_rgb, image_mod, out_dir: Path, layer: str, workers: int, recipe: int = RECIPE
) -> tuple[dict, dict, float]:
    """Cut one layer's two pyramids into place, and say what they wrote and how long it took.

    Two trees from one sheet, in the order a reader would want them if the run died between:
    ``tiles/`` first, because that is what every client can read, and ``tiles@2x/`` after,
    because a client that cannot find it simply asks for the 1x it already had. Each is
    renamed into place on its own, so the pair is never half-swapped in a way that leaves
    the page without a base map.

    The @2x tree is cut from a **downscale** of the sheet rather than from the sheet, and
    that is the one asymmetry between them. Cutting it from a 32768 sheet would give it a z6
    of 512 px tiles weighing as much as the entire 1x pyramid, for pixels a hi-DPI client
    already has: the retina path every tile client ships asks for ``z + 1`` at 1x and draws
    it at half size, and the 1x tree is now a level deeper than it was. So the dense tree
    stays exactly where it has always been, at ``RENDER_2X_PX``, and a sheet that is already
    that size or smaller is handed over unchanged.
    """
    directory = layer_dir(out_dir, layer)
    directory.mkdir(parents=True, exist_ok=True)
    sheet = image_mod.fromarray(sheet_rgb)
    source = f"tools/gen_map_renders.py, {layer} recipe {recipe}, Lanczos"
    started = time.time()
    stats = install_pyramid(sheet, image_mod, directory, source=source, workers=workers)
    dense_px = min(sheet.width, RENDER_2X_PX)
    dense_sheet = (
        sheet if dense_px == sheet.width else sheet.resize((dense_px, dense_px), image_mod.LANCZOS)
    )
    dense = install_pyramid(
        dense_sheet,
        image_mod,
        directory,
        tile_px=PYRAMID_TILE_2X_PX,
        source=source,
        workers=workers,
        dir_name=TILES_2X_DIR_NAME,
    )
    return stats, dense, time.time() - started


def check_parallel(sheet_rgb, image_mod, scratch: Path, workers: int) -> dict:
    """Cut one level twice -- serially and in parallel -- and compare every tile's SHA-256.

    The claim the parallel cutter makes is not "equivalent" or "within tolerance", it is
    **identical bytes**, and that is a claim a hash settles rather than an argument. Run on
    demand rather than every time: it costs one extra cut of one level, and what it is
    guarding against is a change to the cutter rather than a flaky machine.
    """
    from hashlib import sha256

    sheet = image_mod.fromarray(sheet_rgb)
    level = sheet.resize((PYRAMID_TILE_PX << CHECK_PARALLEL_Z,) * 2, image_mod.LANCZOS)
    digests = {}
    timings = {}
    for name, jobs in (("serial", 1), ("parallel", workers)):
        dest = scratch / name
        dest.mkdir(parents=True, exist_ok=True)
        if jobs > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                # Wake every worker before the clock starts. Spawning thirty-two Python
                # interpreters that each import numpy is a second and a half of startup,
                # and folding that into the measurement would understate the cutting by
                # more than the cutting costs.
                list(pool.map(int, range(jobs)))
                started = time.time()
                cut_square_parallel(level, dest, CHECK_PARALLEL_Z, PYRAMID_TILE_PX, pool)
                timings[name] = round(time.time() - started, 2)
        else:
            started = time.time()
            cut_square(level, dest, CHECK_PARALLEL_Z, 0, 0, PYRAMID_TILE_PX)
            timings[name] = round(time.time() - started, 2)
        digests[name] = {
            str(path.relative_to(dest)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
            for path in sorted(dest.rglob("*.png"))
        }
    same = digests["serial"] == digests["parallel"]
    shutil.rmtree(scratch, ignore_errors=True)
    return {
        "level": CHECK_PARALLEL_Z,
        "tiles": len(digests["serial"]),
        "seconds_serial": timings["serial"],
        "seconds_parallel": timings["parallel"],
        "speedup": round(timings["serial"] / max(timings["parallel"], 1e-9), 2),
        "workers": workers,
        "byte_identical": same,
        "differing_tiles": sorted(
            name
            for name in digests["serial"]
            if digests["serial"][name] != digests["parallel"].get(name)
        )[:8],
        "method": (
            "the same level cut both ways into two scratch directories, SHA-256 of every "
            "tile compared name by name. The parallel path resamples nothing -- it is handed "
            "the level already resized -- so this is an identity, not a tolerance."
        ),
    }


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
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
        default=RENDER_PX,
        choices=[RENDER_PX, RENDER_PX // 2, RENDER_PX // 4, RENDER_PX // 8],
        help=(
            f"square edge of each render (default {RENDER_PX}, which is 0.229 m to the "
            "pixel -- see the module docstring for what that is and is not a claim about)"
        ),
    )
    parser.add_argument(
        "--direct-subsamples",
        type=int,
        default=DIRECT_SUBSAMPLES,
        choices=[1, 2, 4],
        help=(
            f"sub-samples per output texel per axis in the direct pass (default "
            f"{DIRECT_SUBSAMPLES}; each doubling costs 4x the rasterising and the silhouette "
            "is already reconstructed by a coverage tent)"
        ),
    )
    parser.add_argument(
        "--kernel-only",
        action="store_true",
        help=(
            f"draw recipe {RECIPE_KERNEL_ONLY} instead: the Catmull-Rom kernel everywhere, "
            "no geometry opened, no cross-fade and no de-terracing. The picture this file "
            "drew before, at whatever --size is asked for, and recorded as that recipe"
        ),
    )
    parser.add_argument(
        "--keep-direct",
        action="store_true",
        help="leave renders/direct.cache/ behind so the next run reuses it",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=(
            f"processes deflating tiles (default {DEFAULT_WORKERS}; 1 cuts serially). The "
            "resampling is single-threaded either way, so the tiles are the same bytes"
        ),
    )
    parser.add_argument(
        "--check-parallel",
        action="store_true",
        help=(
            f"cut z{CHECK_PARALLEL_Z} both serially and in parallel and compare every "
            "tile's SHA-256, then carry on"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace layers this run cannot show were drawn from the field now on disk",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-band progress lines")
    args = parser.parse_args()

    layers = tuple(dict.fromkeys(args.layer)) if args.layer else LAYERS
    workers = max(1, args.workers)

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    pillow_version, pyooz_version = versions["pillow"], versions["pyooz"]
    import texture2ddecoder as decoder

    image_mod = load_imaging()

    field = hf.load_field(args.field)
    if field is None:
        print(
            f"no heightfield at {args.field}. That field is the one input this file cannot "
            "invent -- every pixel of both layers is a height off it -- so there is nothing "
            "to draw. Write it first:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py\n"
            "It reads your own installed game and writes to the same gitignored directory."
        )
        return 4
    field_meta = field.meta
    field_build = field.build
    print(
        f"field: {field.width}x{field.height} at {field.spacing_cm / 100:g} m, build {field_build}"
    )

    spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
    weight_plane, weight_meta = (None, {}) if args.kernel_only else direct_weight(field, spacing_m)
    if weight_plane is None and not args.kernel_only:
        print(
            f"this field carries no {hf.DENSITY_NAME}, so it cannot say which of its texels "
            "are measurements and which are the cliff rasteriser interpolating across a "
            "triangle wider than a texel. That plane is the only thing the two-regime "
            f"sampler switches on, so recipe {RECIPE} has nothing to draw. Cut a field with "
            f"generator version {gen.GENERATOR_VERSION} or later:\n"
            "    uv run --extra gen python tools/gen_world_heightmap.py --force\n"
            "or pass --kernel-only to draw the single-regime picture and say so in the "
            "sidecar."
        )
        return 6
    if weight_plane is not None:
        print(
            f"  a measurement where {weight_meta['rule']} -- "
            f"{weight_meta['qualifying_share_of_the_field']}% of the field, "
            f"{weight_meta['qualifying_share_of_the_cliff_province']}% of its cliff "
            "province. Provenance, not a gate: the rocks are drawn wherever they cover a "
            "pixel"
        )
    recipe = RECIPE_KERNEL_ONLY if args.kernel_only else RECIPE
    heights, deterrace_meta = (None, {}) if args.kernel_only else deterraced_height(field)
    if heights is None:
        print(f"  --kernel-only: drawing recipe {recipe}, the picture before the two regimes")
    else:
        print(
            f"  fill terraces: {deterrace_meta['share_of_the_field']}% of the field low-passed "
            f"at {deterrace_meta['cell_m']} m, moving it a median "
            f"{deterrace_meta['moved_median_m']} m, p99 {deterrace_meta['moved_p99_m']} m, "
            f"clamped at one {deterrace_meta['clamp_m']} m step on "
            f"{deterrace_meta['clamped_share_of_the_province']}% of the province"
        )
    ground, ground_meta = (None, {}) if heights is None else ground_lattice(field, heights)
    if ground is not None:
        print(
            f"  the lattice under the rocks: {ground_meta['lattice_share_of_the_field']}% of "
            f"the field, with {ground_meta['removed_share_of_the_field']}% of it -- the cliff "
            "province -- taken out so the rocks are composited over the ground rather than "
            "over their own 1 m fold"
        )

    out_dir: Path = args.out_dir
    if not args.force:
        for layer in layers:
            sidecar_path = layer_dir(out_dir, layer) / RENDER_SIDECAR_NAME
            if not (layer_dir(out_dir, layer) / TILES_DIR_NAME).is_dir():
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

    # ---- the artwork sheet, which every layer now needs ------------------------------
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the game's own assets from {paks} with pyooz {pyooz_version}")
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    artwork = read_artwork_sheet(store, decoder, image_mod)
    detail, detail_meta = artwork_detail(artwork)
    print(
        f"  artwork sheet {SHEET_PX}x{SHEET_PX} from {len(SLICES)} BC1 slices; luminance "
        f"high pass at sigma {BORROW_DETAIL_SIGMA_PX} px, std {detail_meta['measured_std']}"
    )
    province, province_meta = coarse_province(field)
    print(
        f"  coarse provenance ({', '.join(province_meta['provinces'])}) is "
        f"{province_meta['share_of_the_field']}% of the field, feathered "
        f"{BORROW_FEATHER_M:g} m"
    )
    borrow = (detail, province)
    _wet_plane, _measured_plane, water_source = water_planes(field)
    print(f"  water: {water_source}")

    # The check cuts the ARTWORK, on purpose: a real picture with real entropy, so the PNGs
    # it produces are real PNGs rather than a run-length of one colour that would compare
    # equal however the two paths differed.
    parallel_check = None
    if args.check_parallel:
        parallel_check = check_parallel(
            np.asarray(artwork, np.uint8), image_mod, out_dir / "parallel.check", workers
        )
        print(
            f"  parallel cutter: z{parallel_check['level']}, {parallel_check['tiles']} tiles, "
            f"{parallel_check['seconds_serial']}s serial vs "
            f"{parallel_check['seconds_parallel']}s on {workers} workers "
            f"({parallel_check['speedup']}x) -- byte-identical: "
            f"{parallel_check['byte_identical']}"
        )
        if not parallel_check["byte_identical"]:
            print(
                "  the parallel cutter does not reproduce the serial one's bytes. That is "
                "the one thing it promises, so nothing is written: "
                + ", ".join(parallel_check["differing_tiles"])
            )
            return 5

    # ---- the biome raster ------------------------------------------------------------
    biome = None
    if "satellite" in layers:
        biome = read_biome(store, scripts)
        print(
            f"  {biome['width']}x{biome['width']} palette indices, "
            f"{len(biome['palette'])} entries, {len(biome['distinct_areas'])} named areas"
        )
        calibration = calibrate_biome(biome, artwork, image_mod)
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
        agreement = region_table_is_current(biome)
        if "skipped" in agreement:
            print(f"  region table: {agreement['skipped']}")
        else:
            print(
                f"  region table: {agreement['cells_agreeing']} of "
                f"{agreement['cells_compared']} committed cells match this raster "
                f"({agreement['agreement_pct']}%)"
            )
            if not agreement["table_is_current"]:
                print(
                    "  WARNING: data/region_names.json is no longer this asset's own "
                    "downsample, so it was cut from a different build. Re-run:\n"
                    "      uv run --extra gen python tools/gen_region_names.py"
                )
        table, drawn = biome_lookup(biome)
        biome_rgb = biome_colour_field(biome, table)
        biome_source = {
            "biome_raster": {
                "name": "/Game/"
                + MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
                "class": MAP_AREA_CLASS,
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
                "index_to_asset": {str(i): name for i, name in enumerate(biome["assets_by_index"])},
                "calibration": calibration,
                "region_table_check": agreement,
                "pyooz_version": pyooz_version,
            }
        }
    else:
        biome_rgb, biome_source = None, {}

    # ---- the cliff geometry, rasterised into this render's own grid -------------------
    direct = None
    direct_source: dict = {}
    if weight_plane is not None:
        cache = direct_cache_dir(out_dir)
        stamp = direct_cache_stamp(args.size, args.direct_subsamples, field_build)
        maps = cached_direct(cache, stamp)
        if maps is None:
            print(
                f"decoding the cliff geometry and rasterising it at {spacing_m:.4f} m"
                + (
                    f" with {args.direct_subsamples}x{args.direct_subsamples} sub-samples"
                    if args.direct_subsamples > 1
                    else ""
                )
            )
            index = AssetIndex(store)
            geometry = read_cliff_geometry(
                store, scripts, index, ClassFacts(store, index), not args.quiet
            )
            print(
                f"  {geometry['meshes']} rock meshes, {geometry['tris'] / 1e6:.2f} M triangles "
                f"{geometry['by_source']}, swept in {geometry['seconds_sweep']}s and decoded "
                f"in {geometry['seconds_decode']}s"
            )
            prepared, dropped = direct_placements(geometry["sweep"], geometry["geometry"])
            print(f"  {len(prepared)} placements rasterised, dropped {dropped}")
            cache_stats = rasterise_direct(
                prepared,
                geometry["geometry"],
                cache,
                args.size,
                args.direct_subsamples,
                stamp,
                not args.quiet,
            )
            print(
                f"  direct raster: {cache_stats['texels_with_geometry'] / 1e6:.1f} M texels "
                f"({cache_stats['share_of_the_sheet']}% of the sheet) in "
                f"{cache_stats['seconds']}s"
            )
            direct_source = {
                "cliff_geometry": {
                    "name": "the same placed rock meshes tools/gen_world_heightmap.py folds "
                    "into the 1 m field, decoded here a second time",
                    "licence": (
                        "Coffee Stain Studios' own cooked assets, read out of the reader's "
                        "installed copy of the game. Nothing is committed, redistributed or "
                        "served past localhost."
                    ),
                    "decoder": (
                        "tools/gen_world_heightmap.py's own sweep_levels, read_mesh_geometry, "
                        "rotation_matrix, winding_sign and MaxZRaster, imported and called. "
                        "The grid they are pointed at is the only thing this file changes."
                    ),
                    "meshes": geometry["meshes"],
                    "by_source": geometry["by_source"],
                    "source_triangles": geometry["tris"],
                    "triangles_out_of_bounds": geometry["triangles_out_of_bounds"],
                    "placements_rasterised": len(prepared),
                    "placements_dropped": dropped,
                    "raster": cache_stats,
                    "pyooz_version": pyooz_version,
                }
            }
            maps = cached_direct(cache, stamp)
            del geometry, prepared
        else:
            print(f"reusing the direct raster already in {cache}")
            direct_source = {
                "cliff_geometry": {
                    "reused": json.loads((cache / DIRECT_CACHE_SIDECAR).read_text(encoding="utf-8"))
                }
            }
        if maps is None:
            print(f"the direct raster in {cache} could not be read back after writing it")
            return 7
        direct = (maps[0], maps[1], ground, args.direct_subsamples)

    # ---- draw and cut ----------------------------------------------------------------
    borrow_source = {
        "artwork_detail": {
            "name": f"the game's own {SHEET_PX} px map sheet, from its four BC1 slices",
            "licence": (
                "Coffee Stain Studios' own artwork, read out of the reader's installed copy "
                "of the game. Its LUMINANCE only, high-passed, and multiplied into shading "
                "-- no pixel of it is drawn and no colour of it crosses. Not committed, not "
                "redistributed, and served to localhost only."
            ),
            **detail_meta,
            "applied_where": province_meta,
            "gain": BORROW_GAIN,
            "clamp": list(BORROW_CLAMP),
            "reading": (
                "the field is one resolution but not one accuracy. Over the landscape "
                "province -- 45.3% of it -- the geometry is continuous and its own shading "
                "is the best there is, so nothing is borrowed. Over cliff and fill it is "
                "rasterised hulls and 3.9 m blocks, which is why those provinces read as "
                "melted wax when drawn from the field alone, and the artwork drew the same "
                "ground at 0.92 m."
            ),
        }
    }
    total_started = time.time()
    seam = SeamTrace() if direct is not None else None
    regimes = RegimeCoverage() if direct is not None else None
    measured: dict = {}
    for layer in layers:
        print(f"drawing {layer} at {args.size}x{args.size}")
        started = time.time()
        sheet = render_layer(
            layer,
            field,
            biome_rgb,
            biome or {"width": 1, "area": np.zeros((1, 1), np.uint8)},
            borrow,
            args.size,
            not args.quiet,
            height_dm=heights,
            direct=direct,
            measured_plane_u8=weight_plane,
            # Both layers draw the identical surface, so the seam and the regime table are
            # measured on the first one and quoted for both. Measuring them twice would be
            # measuring the same thing twice and inviting the two answers to differ.
            seam=seam if not measured else None,
            regimes=regimes if not measured else None,
        )
        drew = time.time() - started
        if seam is not None and not measured:
            measured = {"seam_trace": seam.result(), "regimes": regimes.result()}
            trace = measured["seam_trace"]
            if trace.get("measured"):
                print(
                    f"  seam trace: p99 |d2z/dx2| {trace['p99_curvature']['seam']} over the "
                    f"blend against {trace['p99_curvature']['switch']} for the hard max on "
                    f"the same texels -- the fade spends "
                    f"{trace['share_of_a_hard_switch']} of that ceiling; against the terrain "
                    f"beside the join it reads {trace['against_the_pure_regimes']}, which is "
                    "the design's own reference and is measuring the silhouette"
                )
            print(f"  regimes: {measured['regimes']['sheet_pct']}")
        try:
            stats, dense, cut = install_layer(sheet, image_mod, out_dir, layer, workers, recipe)
        except PyramidError as exc:
            print(exc)
            return 1
        del sheet
        stats["game_version_pinned"] = field_build
        dense["game_version_pinned"] = field_build
        spacing_m = (BOUNDS_M["x_max_m"] - BOUNDS_M["x_min_m"]) / args.size
        render = {
            "width_px": args.size,
            "height_px": args.size,
            "metres_per_pixel": round(spacing_m, 4),
            "sampling": (
                "the field's own composition rule, at this render's spacing. KERNEL: "
                "Catmull-Rom (cubic convolution, a = -1/2) over the LANDSCAPE AND FILL "
                "lattices -- the cliff province taken out, because interpolating the "
                "composed field reconstructs its own 1 m fold and a rim reconstructed from "
                "a fold is a 1 m staircase at any output resolution -- falling back to "
                "bilinear where the 4x4 stencil straddles no data and to nothing where no "
                "texel under it has a value. DIRECT: the cliff geometry rasterised into "
                f"this grid at {spacing_m:.4f} m and composited on top of that lattice by "
                "its own coverage of the pixel, raising the ground and never lowering it, "
                "through a smoothed positive part so the line where a rock meets the ground "
                "is not a derivative discontinuity the hillshade would draw. density.u8.z "
                "does not gate any of this: it says which of the drawn texels are "
                "measurements and which are the plane of a triangle wider than a texel, and "
                "_meta.render.two_regime.regimes counts both"
            )
            if direct is not None
            else (
                "Catmull-Rom (cubic convolution, a = -1/2) over the 1 m field per output "
                "pixel wherever the 4x4 stencil is whole, bilinear where it straddles the "
                "edge of the data, and nothing at all where no texel under it has a value. "
                "--kernel-only: no geometry was opened and no direct regime was drawn"
            ),
            "two_regime": {
                "enabled": direct is not None,
                "subsamples_per_axis": args.direct_subsamples if direct is not None else None,
                "silhouette_antialiasing": (
                    "a 3x3 1-2-1 tent over the direct raster's binary coverage, with the "
                    "heights carried through the same kernel weighted by that coverage, so a "
                    "quarter-covered texel is a quarter of the rock's own edge height rather "
                    "than a quarter of zero"
                )
                if args.direct_subsamples == 1
                else (
                    f"{args.direct_subsamples}x{args.direct_subsamples} sub-samples per output "
                    "texel, box-folded"
                ),
                "composition": (
                    "the field's own rule at this render's spacing: the landscape and fill "
                    "lattices interpolated with the C1 kernel, and the cliff geometry "
                    "rasterised at 0.229 m composited over them by its own coverage, raising "
                    "the ground and never lowering it. What this replaced was interpolating "
                    "the 1 m FOLD of that composition, which reconstructs a rim as the 1 m "
                    "staircase the fold put it on however fine the output grid is"
                ),
                "ground_lattice": ground_meta,
                "measurement_rule": weight_meta,
                "lift_knee_m": DIRECT_LIFT_KNEE_M,
                "fill_deterrace": deterrace_meta,
                **measured,
            },
            "z7": (
                "interpolated-smooth. 32768 px is NOT a claim that the field has more to "
                "say -- that was measured twice on this pipeline and refused twice, and the "
                "high-frequency energy per pixel falls at every doubling. What z7 is, is the "
                "same surface evaluated by the same C1 kernel at half the spacing, which a "
                "client cannot produce for itself: a browser shown z6 at twice its scale "
                "upsamples it BILINEARLY, and bilinear is C0, so the relief it draws is "
                "ruled into 0.458 m squares. The exception is the direct regime, where the "
                "pixels are triangles rather than an interpolation and z7 genuinely resolves "
                "geometry the 1 m field folds away -- see two_regime.regimes for how much of "
                "the sheet that is."
            )
            if args.size >= RENDER_PX
            else None,
            "hillshade": (
                f"sun at azimuth {SUN_AZIMUTH_DEG} deg, altitude {SUN_ALTITUDE_DEG} deg, "
                f"shade in [{SHADE_FLOOR}, {SHADE_FLOOR + SHADE_RANGE}], computed at the "
                "output's own spacing"
            ),
            "water": {
                "source": water_source,
                "depth_ramp_m": WATER_DEPTH_FULL_M,
                "edge_feather_m": WATER_EDGE_M,
                "edge_blur_m": WATER_EDGE_BLUR_M,
                "edge_blur_px": round(WATER_EDGE_BLUR_M / spacing_m, 3),
                "level_only": (
                    "full alpha and the deep end of the ramp. 95.2% of level-only water "
                    "stands over the fill province and 98% of its surface levels lie in a "
                    "0.7 m band around the ocean's own -16.99 m, so it is the ocean, and a "
                    "depth ramp run on a 3.9 m raster's rounding error is what used to draw "
                    "3.572 km2 of it as land"
                ),
            },
            "seconds_to_draw": round(drew, 1),
            "seconds_to_cut": round(cut, 1),
            "cut_workers": workers,
            **({"parallel_cutter_check": parallel_check} if parallel_check else {}),
            "imaging": {"name": "pillow", "version": pillow_version},
        }
        sidecar = build_sidecar(
            layer=layer,
            recipe=recipe,
            field_meta=field_meta,
            tiles=stats,
            tiles_2x=dense,
            render=render,
            extra={
                **borrow_source,
                **direct_source,
                **(biome_source if layer == "satellite" else {}),
            },
        )
        path = layer_dir(out_dir, layer) / RENDER_SIDECAR_NAME
        path.write_text(json.dumps(sidecar, indent=1), encoding="utf-8")
        print(
            f"wrote {layer_dir(out_dir, layer)}  {stats['count']} tiles over "
            f"z0..z{stats['max_z']} ({stats['bytes'] / 1e6:.1f} MB) plus {dense['count']} "
            f"@2x over z0..z{dense['max_z']} ({dense['bytes'] / 1e6:.1f} MB)  "
            f"(drew {drew:.0f}s, cut {cut:.0f}s)"
        )
    if direct is not None:
        # The memory maps have to be let go before the files under them can be removed, and
        # on Windows that is not a nicety -- an open mapping refuses the unlink outright.
        direct = maps = None
        if not args.keep_direct:
            shutil.rmtree(direct_cache_dir(out_dir), ignore_errors=True)
    print(f"done in {time.time() - total_started:.0f}s")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
