"""Cut a real 1 m heightmap of this world out of the installed game.

    uv run --extra gen python tools/gen_world_heightmap.py

``src/satisfactory_mcp/domain/spatial/elevation.py`` used to open with "there is no
heightmap", and for as long as the project's only terrain evidence was scattered resource
nodes and the player's own foundations that was true. It is not true any more. The cooked
game ships two independent, exact descriptions of its own ground, and this file decodes
both, fuses them, measures the result against the 626 static resource nodes, and writes it
to ``data/local/heightmap/`` -- which is gitignored, because every byte of it is derived
from Coffee Stain's cooked assets and read out of the reader's own install. The generator
is committed; its output never is. Same posture, and the same precedent line for line, as
``tools/gen_map_image.py``.

The field this writes is **2.9x better** than the only heightmap the project had before it
-- ``HeightData_Test``, the 2048 px interface raster -- on trimmed RMS against those nodes,
**4.1x** on the median, at 3.66x the horizontal sampling and a 499x finer vertical step.
The comparison is conservative: the baseline's scale and offset were fitted on those very
nodes, so its score is optimistic, and the two new layers were never fitted to anything.

What the world actually ships
-----------------------------
**A cooked UE Landscape heightfield.** ``LandscapeComponent`` exports carry, past their
property tags, a ``GrassData`` blob whose first ``128*128`` uint16 are the component's
own height samples -- one per 1 m quad, 7.8 mm apart vertically. 2,289 components tile a
7113 x 6097 m frame, 85% of it covered. ``world_z_cm = (h - 32768)/128 * scale_z + origin_z``
with both taken from the ``LandscapeStreamingProxy``'s root transform rather than assumed,
and this run refuses if they are not the 100 cm / 100 cm it was measured against.

That heightfield is the sculpted terrain and **nothing else**, which is why it is excellent
in the middle of the map and catastrophic in the tail: the cliffs, mesas and boulders are
placed static meshes, and the landscape underneath them is whatever the artist left there.
Landscape alone scores *worse* than the interface raster on trimmed RMS.

**And the cliffs ship their own collision geometry.** Every rock ``StaticMesh`` carries a
``BodySetup`` whose trailing bytes are a cooked Chaos ``FTriangleMeshImplicitObject``:
after a ``267`` (``0x10B``) marker, ``NumVerts`` float32 triples then ``NumTris`` index
triples. Exact float32 geometry at about 1 m mean edge length, placed 21,234 times.
Rasterising it as a max-Z overlay over the landscape is what removes the tail: on 600,000
foliage ground samples -- an independent set 1,000x larger than the nodes -- P90 error
falls from 14.75 m to 3.13 m.

Five stages, and what each one is guarded by
--------------------------------------------
**1. Landscape.** One sweep of the 4,521 ``*.umap`` under ``Map/GameLevel01`` reads both
the landscape components and the placements, because two passes over the same packages is
twice the price of one. The georeference is measured, not assumed: the frame's world origin
comes out of the proxies, and the offset from it to this file's output origin is asserted to
be a whole number of texels. It is (199, 702), so the landscape drops into the output grid
index-aligned with **zero resampling** -- and if a future build moves the landscape half a
metre, the assert says so instead of a bilinear smear appearing silently.

**2. Placements.** ``StaticMeshComponent`` exports that are their own actor's
``RootComponent``, with the mesh path and ``RelativeLocation``/``Rotation``/``Scale3D``.
Three exclusions, each for a stated reason. ``NodeMeshActor_C`` is dropped because the
validation set is the resource-node table and predicting a node's Z from the mesh drawn
under that node would be circular. Foliage and trees are dropped because canopy is not
ground -- that is precisely the mistake the interface raster makes. And a mesh whose scaled
local extent exceeds ``OVERSIZE_CM`` is dropped: those are the sky dome and ocean shells,
which are not terrain and would tile the whole map at one height.

**3. Cliff collision.** The ``267`` marker is found **by search** inside a bounded window,
never at a fixed offset, and three independent checks have to pass before a mesh is
believed: every vertex finite, 90% of them inside the mesh's own ``ExtendedBounds``, and
``max(index) < NumVerts``. That last one is what picks uint16 from uint32 -- meshes at or
past 65,536 vertices use the wider index -- and it is also what makes a misaligned read
fail loudly rather than produce plausible noise, since a random offset blows it instantly.
103 of the 124 rock meshes decode; the closed ones satisfy the Euler relation
``NumTris == 2*NumVerts - 4`` exactly, and the ones that do not are exactly the open shells
(cave walls, floors, ceilings, merged arch pieces).

**Arch meshes are dropped from the max-Z layer**, and that is a measurement rather than a
choice: a max-Z field puts an arch *roof* over the ground beneath it, and masking them
improved every metric on all three validation sets (nodes P90 1.46 -> 1.23 m, trim95
9.25 -> 6.57 m, foliage P90 3.58 -> 3.30 m). They are dropped **before** the raster here
rather than masked out of it afterwards, which is the one place this file's arithmetic
differs from the workflow's: masking after the fold blanks a texel an arch happened to win
even when a real rock stood second in it, and skipping before the fold lets the rock win.

Each triangle is also clamped to its own mesh's padded ``ExtendedBounds``, against the
stray far vertices that drew thin "spider" streaks across the western canyon in the
prototype previews. On build 495413 it drops **zero** triangles -- the decode's own
90%-inside gate already turns those meshes away -- and ``triangles_outside_bounds`` in the
sidecar is what says so, run by run, rather than this paragraph.

**4. Fill.** Outside the landscape frame there is no landscape, so ``HeightData_Test`` --
2048 px of float16 over the same 7500 m box, ``z_cm = a*raw + b`` with the fit below --
carries the ocean shelf and the map's edge. Its no-data test is the one number in this file
most likely to be got wrong: the blank value is ``raw == 0``, which **decodes to -522 m**,
not to zero. Testing ``raw > 0`` instead of ``decoded > FILL_FLOOR_CM`` leaks 138,481 texels
of blank into the field as a false sea floor.

**5. Water.** A surface channel, derived from where the interface raster goes flat above
the terrain, shipped as **information only**. It does not gate the terrain, and that is a
measured decision rather than an omission: the world's ~700 water actors are far too sparse
to build a surface from (median 323 m to the nearest one over submerged ground) and their Z
is a pivot rather than a water level, and a lake gate built on this flatness detector was
measured to make the field *worse* -- nodes trim90 0.93 against 0.77. So the channel says
where water probably stands and nothing downstream is allowed to move ground because of it.

What it writes
--------------
``data/local/heightmap/``, four files, about 18 MB::

    height.i16.z  7500x7500 int16 decimetres, row-delta + zlib, -32768 = no data
    prov.u8.z     0 no-data, 1 landscape, 3 fill, 4 cliff collision
    water.i16.z   water surface Z, same grid and no-data
    meta.json     georeference, game build, generator version, coverage, measured accuracy

The georeference is fixed and recorded: ``x_cm = -324700 + col*100``,
``y_cm = -375000 + row*100``, **vertex-aligned** -- a texel's height belongs to that point
exactly, not to a cell around it. The codec itself lives in
``satisfactory_mcp.domain.spatial.heightfield`` and is imported from there rather than
copied, because a byte format with two implementations is a byte format with two opinions.

**The run validates itself and refuses to write if it fails.** The built field is sampled
at all 626 static resource nodes and the trimmed RMS about the median offset has to come in
under ``VALIDATION_TRIM_RMS_MAX_M``. The workflow that proved this pipeline measured
0.368 m; the gate is 0.5 m, which is comfortably clear of that and nowhere near the
baseline's 1.08 m. A decode regression -- a moved marker, a changed component size, an index
width guessed wrong -- has to fail loudly rather than ship a plausible-looking field that is
quietly two metres out. The same pass measures per-layer accuracy and puts it in
``meta.json``, so a reading can carry the uncertainty of the layer that answered it instead
of one number quoted for the whole map.

**Staleness.** The project's standing rule is that a pinned map artifact announces drift
rather than answering silently wrong, and this one is pinned twice over: ``meta.json``
records the installed build in the same shape ``data/resource_nodes.json`` uses, and a run
**refuses to overwrite** an existing field unless that sidecar names the build now
installed. ``--force`` says it anyway. The whole directory is written to
``heightmap.incoming`` and **renamed** into place, so a reader can never meet three files
from one build and one from another; an interrupted run leaves a staging directory nothing
loads.

**What opens the container.** Oodle-compressed container blocks are opened by ``pyooz``,
which is the project's ``gen`` extra: a generation-time tool, imported at module scope by
nothing here and by nothing under ``src/``, and asked for by name when a generator runs --
the same posture as ``tools/gen_map_image.py`` and ``tools/gen_world_collectibles.py``::

    uv run --extra gen python tools/gen_world_heightmap.py

numpy and scipy, unlike pyooz, are dependencies of this project outright and are imported
at the top of this file.

**Licence.** Everything this writes is derived from Coffee Stain's cooked assets, read out
of the reader's own installed copy of the game and left in a gitignored directory. Nothing
here is committed, uploaded or redistributed, and the server serves it to localhost only.
"""

from __future__ import annotations

import json
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    PackageView,
    ScriptObjects,
    _int32,
    class_name_of,
    property_tags,
)
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    install_directory,
    installed_build,
    read_str_path,
)
from satisfactory_mcp.domain.spatial import heightfield as hf
from tools._common import base_parser, require_gen

#: Which packages are swept. Everything terrain lives under one world.
LEVEL_DIR = "/GameLevel01/"
LEVEL_SUFFIX = ".umap"

#: The interface raster that used to be the project's only heightmap, and is now the fill.
BASELINE_PATH = (
    "../../../FactoryGame/Content/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test.ubulk"
)
BASELINE_PX = 2048

#: The mip chain of that raster, largest-first, at two bytes per texel: 2048 down to 128.
#: Derived so the length check below is arithmetic rather than a number typed in. A file of
#: another length means the raster was re-cooked, i.e. the game changed, and the run stops.
BASELINE_MIPS = tuple(((BASELINE_PX >> i), (BASELINE_PX >> i) ** 2 * 2) for i in range(5))
BASELINE_BYTES = sum(size for _px, size in BASELINE_MIPS)

#: The raster's own box, metres of world per texel column. The in-game map square.
BASELINE_BOX_CM = (-324700.0, 425300.0, -375000.0, 375000.0)

#: ``z_cm = scale*raw + offset``, from a robust three-pass fit of the float16 values against
#: the 626 static nodes: 569 inliers, 1.07 m RMS, 3.897 m per quantisation step. Recorded
#: here rather than re-fitted per run because a fit is a calibration and a calibration that
#: moves silently is not one -- and because this layer is only the fill, whose whole claim
#: is that it is the old baseline unchanged.
BASELINE_SCALE_CM_PER_RAW = 99364.40751843198
BASELINE_OFFSET_CM = -52282.12831764497

#: The fill's no-data test, and the trap in it. ``raw == 0`` is the blank value and decodes
#: to -522.8 m; the world's own floor is -255 m (landscape ``raw == 0``). Anything below
#: this is the raster's blank tail, not sea bed. Testing ``raw > 0`` instead leaks 138,481
#: texels. Stated as a decoded height precisely so it cannot be confused for a raw one.
FILL_FLOOR_CM = -26000.0

#: The interface raster's own resolution, for the accuracy the fill layer inherits.
FILL_HORIZONTAL_M = 7500.0 / BASELINE_PX
FILL_VERTICAL_M = BASELINE_SCALE_CM_PER_RAW / 255.0 / 100.0

#: ``ComponentSizeQuads`` is 127, so a landscape component is 128x128 height samples.
LANDSCAPE_N = 128

#: What the height encoding is, and what this run refuses to proceed without. The proxies
#: state their own scale and origin; these are what those statements were measured to be,
#: and a build that disagrees gets an error rather than a field a metre out.
LANDSCAPE_SCALE_CM = 100.0
LANDSCAPE_ORIGIN_Z_CM = 100.0
LANDSCAPE_ZERO = 32768.0
LANDSCAPE_PER_UNIT = 128.0

#: How far a measured proxy transform may sit from those numbers before the run stops. A
#: hundredth of a centimetre: floating-point noise in a double, and nothing else.
TRANSFORM_TOLERANCE = 0.01

#: The output grid. 7500 x 7500 at 1 m, vertex-aligned, over the in-game map square.
GRID_PX = 7500
SPACING_CM = 100.0
ORIGIN_X_CM = -324700.0
ORIGIN_Y_CM = -375000.0

#: Which mesh trees carry terrain geometry. Everything else placed in the world is a tree,
#: a plant, a building or a prop, and none of those are ground.
ROCK_DIRS = ("/World/Environment/Rock/", "/World/Environment/Caves/")

#: Actors whose meshes must never enter the field. The resource-node mesh is the whole list
#: and the reason is circularity: the field is validated against the node table.
EXCLUDED_OWNERS = frozenset({"NodeMeshActor_C"})

#: A mesh basename containing this is an arch, and an arch is a roof. Dropped from the
#: max-Z layer -- see the module docstring for the three validation sets that measured it.
ARCH_MARK = "Arc"

#: Scaled local extent past which a placement is scenery rather than terrain: the sky dome
#: and the ocean shells. 600 m is an order of magnitude above the largest real rock.
OVERSIZE_CM = 60000.0

#: The Chaos cooked-trimesh marker, and the window it is searched in. Searched, never
#: indexed: a fixed offset is a guess that keeps working until the day it does not.
TRIMESH_MARKER = struct.pack("<I", 267)
TRIMESH_SEARCH = (40, 400)

#: How far past its own ``ExtendedBounds`` a vertex may sit. Both a decode check -- 90% of
#: vertices must be inside, which a misread stream cannot manage -- and, per triangle, the
#: clamp guarding against the stray far vertices that drew spider streaks in the
#: prototypes. On build 495413 the clamp fires zero times; the sidecar records the count.
BOUNDS_PAD_CM = 4.0
BOUNDS_PAD_FRACTION = 0.05
BOUNDS_INSIDE_MIN = 0.90

#: The rasteriser's scatter buffer, in candidate texels. Bounded so a 21,000-placement run
#: holds a few hundred MB rather than the whole 120 M-triangle scatter at once.
RASTER_FLUSH = 6_000_000

#: The water detector, all of it, on the 2048 baseline grid. A region is water if the
#: baseline is flat there, stands more than WATER_MIN_DEPTH_M above the terrain, covers at
#: least WATER_MIN_TEXELS (~2,000 m2, so a flat rock is not a lake) and is level to within
#: WATER_LEVEL_STD_M. Information only: nothing downstream may move ground because of it.
WATER_FLAT_GRAD_M = 0.5
WATER_MIN_DEPTH_M = 1.0
WATER_MIN_TEXELS = 150
WATER_LEVEL_STD_M = 2.5

#: The node table the run validates against, and the gate it has to clear. The workflow
#: that proved this pipeline measured 0.368 m trimmed RMS; 0.5 m is clear of that and well
#: under the interface raster's own 1.08 m, so a decode regression cannot pass as a refresh.
NODE_TABLE = ROOT / "data" / "world_resource_nodes.mit.json"
VALIDATION_TRIM = 0.90
VALIDATION_TRIM_RMS_MAX_M = 0.5

#: How many nodes a provenance layer needs before its measured accuracy is believed rather
#: than its derived quantisation step quoted. Nodes are not spread evenly, and the fill
#: layer in particular is mostly ocean where nothing stands.
ACCURACY_MIN_SAMPLES = 30

#: Where it all goes. The staging directory it is renamed from is
#: ``core.gameassets.provenance.install_directory``'s business, not this file's.
LOCAL_DIR = ROOT / "data" / "local"

#: Bumped when the pipeline changes what it writes, so a sidecar dates its own field.
GENERATOR_VERSION = 1

#: Where the sidecar records the build, and what the staleness guard reads back.
PIN_PATH = ("sources", "game", "game_version_pinned")


# --------------------------------------------------------------------------------------
# Stages 1 and 2: one sweep of the world's packages, two harvests out of it.
# --------------------------------------------------------------------------------------


def rotation_matrix(pitch: float, yaw: float, roll: float) -> np.ndarray:
    """UE's ``FRotationMatrix``: rows are the local X, Y, Z axes in world space.

    Written out rather than composed from three rotations because UE's order and sign
    conventions are its own, and a matrix that is right for 21,000 placements is worth
    stating once in the form the engine states it.
    """
    p, y, r = np.radians([pitch, yaw, roll])
    sp, cp = np.sin(p), np.cos(p)
    sy, cy = np.sin(y), np.cos(y)
    sr, cr = np.sin(r), np.cos(r)
    return np.array(
        [
            [cp * cy, cp * sy, sp],
            [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
            [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp],
        ]
    )


def _grass_data_heights(tail: bytes) -> np.ndarray | None:
    """The ``128*128`` uint16 height samples out of a ``LandscapeComponent``'s tail.

    Past the property tags the export carries a bool, a GUID and a float, then the
    ``GrassData`` map: an element count, a ``TMap`` of that many 8-byte entries, and the
    ``TArray<uint8>`` whose first ``2*NumElements`` bytes are the heights. Everything is
    read from lengths in the blob; the only constant is that a component is 128 samples
    square, and a component that says otherwise is skipped rather than reinterpreted.
    """
    try:
        num = struct.unpack_from("<I", tail, 24)[0]
        entries = struct.unpack_from("<I", tail, 28)[0]
        pos = 32 + 8 * entries
        total = struct.unpack_from("<I", tail, pos)[0]
        pos += 4
    except struct.error:
        return None
    want = LANDSCAPE_N * LANDSCAPE_N
    if num != want or total < 2 * num or pos + 2 * num > len(tail):
        return None
    return np.frombuffer(tail, dtype="<u2", count=num, offset=pos).reshape(LANDSCAPE_N, LANDSCAPE_N)


def sweep_levels(store, scripts, progress: bool = True) -> dict:
    """One pass over every ``*.umap`` of the world: landscape components and placements.

    Both harvests need the same ``PackageView`` of the same 4,521 packages, and building
    that view is the whole cost of the pass, so they share it. Returns the raw material for
    stages 1 to 3 and nothing interpreted: the arithmetic that turns it into a field lives
    in the functions below, where it can be read next to the constants it uses.
    """
    paths = sorted(p for p in store.paths.values() if p.endswith(LEVEL_SUFFIX) and LEVEL_DIR in p)
    components: list[tuple[int, int, np.ndarray]] = []
    proxies: list[tuple[float, float, float, float, float, float]] = []
    #: (mesh id, owner id, x, y, z, pitch, yaw, roll, sx, sy, sz)
    placements: list[tuple[float, ...]] = []
    mesh_ids: dict[str, int] = {}
    owner_ids: dict[str, int] = {}
    unreadable = 0
    malformed = 0
    started = time.time()

    for index, path in enumerate(paths):
        try:
            view = PackageView(store.read_path(path), scripts)
        except Exception:
            unreadable += 1
            continue

        # An actor names its own root; a StaticMeshComponent that is not one is a
        # decoration hanging off something else, and its transform is relative to a parent
        # this sweep does not walk. Built first so the placement loop can just look up.
        root_owner: dict[int, str] = {}
        for slot, class_path in view.class_of.items():
            reference = view.props(slot).get("RootComponent")
            if reference is None:
                continue
            root = view.export_ref(reference)
            if root is not None:
                root_owner[root] = class_name_of(class_path)

        for slot, class_path in view.class_of.items():
            name = class_name_of(class_path)
            if name == "LandscapeStreamingProxy":
                props = view.props(slot)
                offset = props.get("LandscapeSectionOffset")
                root = view.export_ref(props.get("RootComponent", b""))
                if not offset or len(offset) != 8 or root is None:
                    continue
                section_x, section_y = struct.unpack("<2i", offset)
                location = view.props(root).get("RelativeLocation")
                scale = view.props(root).get("RelativeScale3D")
                if not location or len(location) != 24 or not scale or len(scale) != 24:
                    continue
                lx, ly, lz = struct.unpack("<3d", location)
                sx, sy, sz = struct.unpack("<3d", scale)
                proxies.append((section_x - lx / sx, section_y - ly / sy, lz, sx, sy, sz))
            elif name == "LandscapeComponent":
                props = view.props(slot)
                base_x = _int32(props.get("SectionBaseX", b"\0\0\0\0"))
                base_y = _int32(props.get("SectionBaseY", b"\0\0\0\0"))
                body = view.pkg.body(view.exports[slot])
                _tags, end = property_tags(body, view.pkg.names)
                heights = _grass_data_heights(body[end:])
                if heights is None:
                    malformed += 1
                    continue
                components.append((base_x, base_y, heights))
            elif name == "StaticMeshComponent":
                if slot not in root_owner:
                    continue
                props = view.props(slot)
                reference = props.get("StaticMesh")
                location = props.get("RelativeLocation")
                if reference is None or location is None or len(location) != 24:
                    continue
                mesh = view.import_path(reference)
                if not mesh:
                    continue
                rotation = props.get("RelativeRotation")
                scale = props.get("RelativeScale3D")
                x, y, z = struct.unpack("<3d", location)
                turn = (0.0, 0.0, 0.0)
                if rotation and len(rotation) == 24:
                    turn = struct.unpack("<3d", rotation)
                size = (1.0, 1.0, 1.0)
                if scale and len(scale) == 24:
                    size = struct.unpack("<3d", scale)
                pitch, yaw, roll = turn
                sx, sy, sz = size
                mesh_id = mesh_ids.setdefault(mesh, len(mesh_ids))
                owner_id = owner_ids.setdefault(root_owner[slot], len(owner_ids))
                placements.append((mesh_id, owner_id, x, y, z, pitch, yaw, roll, sx, sy, sz))

        if progress and index % 500 == 0:
            print(
                f"  {index}/{len(paths)} packages, {len(components)} landscape components, "
                f"{len(placements)} placements, {time.time() - started:.0f}s",
                flush=True,
            )

    return {
        "packages": len(paths),
        "unreadable": unreadable,
        "malformed_components": malformed,
        "components": components,
        "proxies": proxies,
        "placements": np.array(placements, dtype=np.float64) if placements else np.zeros((0, 11)),
        "meshes": [m for m, _ in sorted(mesh_ids.items(), key=lambda kv: kv[1])],
        "owners": [o for o, _ in sorted(owner_ids.items(), key=lambda kv: kv[1])],
        "seconds": time.time() - started,
    }


def landscape_frame(sweep: dict) -> dict:
    """Stitch the components into one raster and pin it to the world. Nothing resampled.

    The proxies all state the same origin, scale and Z offset; that they do is checked here
    rather than assumed, because a build that split the landscape into frames with different
    transforms would otherwise stitch into a plausible, wrong field.
    """
    components = sweep["components"]
    proxies = sweep["proxies"]
    if not components or not proxies:
        raise SystemExit(
            "no LandscapeComponent or no LandscapeStreamingProxy was found in "
            f"{sweep['packages']} packages. The landscape moved or was renamed, which means "
            "the game changed; nothing here can be trusted until that is looked at."
        )

    def _one(values, label: str) -> float:
        distinct = sorted({round(v, 3) for v in values})
        if len(distinct) != 1:
            raise SystemExit(
                f"the landscape proxies disagree about {label}: {distinct[:6]}. This file "
                "stitches one frame with one transform, and cannot stitch several."
            )
        return distinct[0]

    origin_x = _one((p[0] for p in proxies), "their world origin in X")
    origin_y = _one((p[1] for p in proxies), "their world origin in Y")
    origin_z = _one((p[2] for p in proxies), "their Z offset")
    scale_x = _one((p[3] for p in proxies), "their X scale")
    scale_y = _one((p[4] for p in proxies), "their Y scale")
    scale_z = _one((p[5] for p in proxies), "their Z scale")

    for measured, expected, label in (
        (scale_x, LANDSCAPE_SCALE_CM, "X scale"),
        (scale_y, LANDSCAPE_SCALE_CM, "Y scale"),
        (scale_z, LANDSCAPE_SCALE_CM, "Z scale"),
        (origin_z, LANDSCAPE_ORIGIN_Z_CM, "Z offset"),
    ):
        if abs(measured - expected) > TRANSFORM_TOLERANCE:
            raise SystemExit(
                f"the landscape's {label} is {measured}, not the {expected} this file was "
                "measured against. The height encoding depends on it, so decoding anyway "
                "would produce a field that is wrong by a factor rather than by an offset."
            )

    xs = [c[0] for c in components]
    ys = [c[1] for c in components]
    min_x, min_y = min(xs), min(ys)
    width = max(xs) + LANDSCAPE_N - min_x
    height = max(ys) + LANDSCAPE_N - min_y

    raw = np.zeros((height, width), dtype="<u2")
    covered = np.zeros((height, width), dtype=bool)
    for base_x, base_y, heights in components:
        row, col = base_y - min_y, base_x - min_x
        raw[row : row + LANDSCAPE_N, col : col + LANDSCAPE_N] = heights
        covered[row : row + LANDSCAPE_N, col : col + LANDSCAPE_N] = True

    # raw == 0 inside a component that IS present is a landscape hole -- a cave mouth or a
    # deliberately cut-out section -- not a height of -255 m. Left as no data for the cliff
    # layer to fill or for nothing to.
    hole = covered & (raw == 0)
    good = covered & ~hole
    _labelled, blobs = ndimage.label(hole)

    z_cm = (raw.astype(np.float32) - LANDSCAPE_ZERO) / LANDSCAPE_PER_UNIT * scale_z + origin_z
    return {
        "z_cm": z_cm,
        "good": good,
        "width": width,
        "height": height,
        "x0_cm": (min_x - origin_x) * scale_x,
        "y0_cm": (min_y - origin_y) * scale_y,
        "scale_cm": scale_x,
        "origin_z_cm": origin_z,
        "components": len(components),
        "coverage": float(covered.mean()),
        "hole_texels": int(hole.sum()),
        "hole_blobs": int(blobs),
    }


def drop_offsets(frame: dict) -> tuple[int, int]:
    """Where the landscape frame lands in the output grid, in whole texels.

    Asserted rather than rounded into. The landscape is a 1 m grid and so is the output, so
    an origin offset that is not a whole number of texels means one of the two moved, and
    the honest response is to stop -- a resample would smooth a real heightfield to hide an
    arithmetic problem, and it would do it silently.
    """
    dx = (frame["x0_cm"] - ORIGIN_X_CM) / SPACING_CM
    dy = (frame["y0_cm"] - ORIGIN_Y_CM) / SPACING_CM
    for value, axis in ((dx, "X"), (dy, "Y")):
        if abs(value - round(value)) > 1e-6:
            raise SystemExit(
                f"the landscape frame sits {value:.4f} texels from the output origin in "
                f"{axis}, which is not a whole number. The landscape and the output grid "
                "are both 1 m, so this cannot be dropped in index-aligned any more, and "
                "this file will not silently resample a real heightfield to hide that."
            )
    return round(dx), round(dy)


# --------------------------------------------------------------------------------------
# Stage 3: the cooked Chaos triangle meshes, and the max-Z overlay they rasterise into.
# --------------------------------------------------------------------------------------


def decode_trimesh(blob: bytes, low: np.ndarray, high: np.ndarray):
    """One cooked ``FTriangleMeshImplicitObject``, or ``(None, why)``.

    Layout, past the export's property tags::

        ... header ...  <u32 267>  <u8 flag>  <u32 NumVerts>  <NumVerts * 3 float32>
        <u32 0>  <u32 NumTris>  <NumTris * 3 uint16, or uint32 when NumVerts >= 65536>

    The marker is searched for inside ``TRIMESH_SEARCH`` rather than indexed at a fixed
    offset. Three checks then have to pass before the result is believed, and together they
    are what makes a misaligned read fail instead of returning plausible noise: the vertices
    must be finite, 90% of them must lie inside the mesh's own ``ExtendedBounds``, and every
    triangle index must be less than ``NumVerts``. The last one also picks the index width:
    a uint16 view of a uint32 array reads indices roughly twice the vertex count, so it
    fails and the uint32 view is tried.
    """
    at = blob.find(TRIMESH_MARKER, *TRIMESH_SEARCH)
    if at < 0:
        return None, "no 267 marker in the search window"
    count = struct.unpack_from("<I", blob, at + 5)[0]
    pos = at + 9
    if count <= 0 or pos + 12 * count > len(blob):
        return None, f"implausible vertex count {count}"
    verts = np.frombuffer(blob, "<f4", count=3 * count, offset=pos).reshape(count, 3)
    verts = verts.astype(np.float32)
    if not np.isfinite(verts).all():
        return None, "non-finite vertices"
    pad = BOUNDS_PAD_CM + BOUNDS_PAD_FRACTION * float(np.max(high - low))
    inside = ((verts >= low - pad) & (verts <= high + pad)).all(axis=1)
    if inside.mean() <= BOUNDS_INSIDE_MIN:
        return None, f"only {inside.mean():.1%} of vertices inside ExtendedBounds"
    pos += 12 * count
    _zero, tris = struct.unpack_from("<II", blob, pos)
    pos += 8
    for width, dtype in ((2, "<u2"), (4, "<u4")):
        if tris > 0 and pos + 3 * tris * width <= len(blob):
            candidate = np.frombuffer(blob, dtype, count=3 * tris, offset=pos).reshape(tris, 3)
            if candidate.max() < count:
                return (verts, candidate.astype(np.int32), pad), None
    return None, f"no index width fits {tris} triangles over {count} vertices"


def read_mesh_geometry(store, scripts, index, meshes: list[str], progress: bool = True) -> dict:
    """Decode the collision trimesh of every rock mesh the world places. Returns a dict.

    Only ``ROCK_DIRS`` are opened: a tree's collision is a tree, and the point of this layer
    is the geometry the landscape does not contain. A mesh with no cooked trimesh is
    recorded with the reason and skipped -- 21 of the 124 use ``CTF_UseSimpleAndComplex``
    and ship only convex hulls, which are small and rare enough to lose.
    """
    wanted = [m for m in meshes if any(d in m for d in ROCK_DIRS)]
    geometry: dict[str, tuple] = {}
    failures: dict[str, str] = {}
    closed = 0
    started = time.time()
    for count, mesh in enumerate(wanted):
        package = index.path_for(mesh)
        if not package:
            failures[mesh] = "not in the container"
            continue
        try:
            view = PackageView(store.read_path(package), scripts)
        except Exception as exc:
            failures[mesh] = f"unreadable package: {type(exc).__name__}"
            continue
        bounds = None
        for export in view.exports:
            if class_name_of(view.class_of.get(export["slot"])) != "StaticMesh":
                continue
            payload = view.props(export["slot"]).get("ExtendedBounds")
            if not payload:
                continue
            decoded = view.decode_struct(payload) or {}
            origin = decoded.get("Origin")
            extent = decoded.get("BoxExtent")
            if isinstance(origin, dict) and isinstance(extent, dict):
                o = struct.unpack("<3d", bytes.fromhex(origin["_raw"])[:24])
                e = struct.unpack("<3d", bytes.fromhex(extent["_raw"])[:24])
                bounds = (np.array(o), np.array(e))
            break
        if bounds is None:
            failures[mesh] = "no ExtendedBounds, so a decode could not be checked"
            continue
        origin, extent = bounds
        for export in view.exports:
            if class_name_of(view.class_of.get(export["slot"])) != "BodySetup":
                continue
            body = view.pkg.body(export)
            _tags, end = property_tags(body, view.pkg.names)
            result, why = decode_trimesh(body[end:], origin - extent, origin + extent)
            if result is None:
                failures[mesh] = why
            else:
                verts, tris, pad = result
                geometry[mesh] = (verts, tris, origin - extent - pad, origin + extent + pad)
                # The closed-manifold Euler relation. Not a gate -- cave walls, floors and
                # merged arch pieces are open shells and are meant to be -- but counting it
                # is the cheapest evidence that this is geometry and not pattern-matched
                # noise, since noise satisfies it essentially never.
                if tris.shape[0] == 2 * verts.shape[0] - 4:
                    closed += 1
            break
        if mesh not in geometry and mesh not in failures:
            failures[mesh] = "no BodySetup export"
        if progress and count % 25 == 0:
            print(f"  {count}/{len(wanted)} rock meshes, {time.time() - started:.0f}s", flush=True)
    return {
        "geometry": geometry,
        "failures": failures,
        "wanted": len(wanted),
        "closed_manifolds": closed,
        "verts": sum(v.shape[0] for v, _t, _lo, _hi in geometry.values()),
        "tris": sum(t.shape[0] for _v, t, _lo, _hi in geometry.values()),
        "seconds": time.time() - started,
    }


class MaxZRaster:
    """Scatter-max rasteriser over the landscape frame: the highest triangle wins a texel.

    Triangles arrive faster than they can be reduced -- 120 M of them across the placements
    -- so candidates are buffered and folded in batches. The fold is a lexsort by (texel,
    z) and a take-last, which is one pass over the batch rather than a Python loop over
    21,000 placements' worth of overlapping bounding boxes.
    """

    def __init__(self, width: int, height: int, x0_cm: float, y0_cm: float, scale: float) -> None:
        self.width, self.height = width, height
        self.x0, self.y0, self.scale = x0_cm, y0_cm, scale
        self.z = np.full(height * width, -np.inf, dtype=np.float32)
        self.src = np.zeros(height * width, dtype=np.uint16)
        self._idx: list[np.ndarray] = []
        self._z: list[np.ndarray] = []
        self._s: list[np.ndarray] = []
        self._n = 0

    def flush(self) -> None:
        if not self._idx:
            return
        idx = np.concatenate(self._idx)
        z = np.concatenate(self._z)
        src = np.concatenate(self._s)
        self._idx, self._z, self._s, self._n = [], [], [], 0
        order = np.lexsort((z, idx))
        idx, z, src = idx[order], z[order], src[order]
        last = np.empty(idx.size, bool)
        last[-1] = True
        last[:-1] = idx[1:] != idx[:-1]
        idx, z, src = idx[last], z[last], src[last]
        better = z > self.z[idx]
        self.z[idx[better]] = z[better]
        self.src[idx[better]] = src[better]

    def add(self, tri: np.ndarray, source_id: int) -> None:
        """Buffer every texel covered by ``tri`` (M, 3, 3) in world cm, with its plane Z.

        Triangles are bucketed by bounding-box span so one vectorised barycentric test runs
        over a whole bucket at a fixed candidate-grid size, instead of every triangle paying
        for the largest one's box.
        """
        fx = (tri[:, :, 0] - self.x0) / self.scale
        fy = (tri[:, :, 1] - self.y0) / self.scale
        z = tri[:, :, 2]
        x0 = np.floor(fx.min(1) - 0.5)
        x1 = np.ceil(fx.max(1) + 0.5)
        y0 = np.floor(fy.min(1) - 0.5)
        y1 = np.ceil(fy.max(1) + 0.5)
        span = np.maximum(x1 - x0, y1 - y0).astype(np.int32)
        for size in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            pick = (span <= size) & (span > (size // 2 if size > 1 else 0))
            if not pick.any():
                continue
            steps = np.arange(size + 1, dtype=np.float32)
            ox, oy = np.meshgrid(steps, steps)
            gx = x0[pick][:, None] + ox.ravel()[None, :] + 0.5
            gy = y0[pick][:, None] + oy.ravel()[None, :] + 0.5
            ax, ay = fx[pick, 0][:, None], fy[pick, 0][:, None]
            bx, by = fx[pick, 1][:, None], fy[pick, 1][:, None]
            cx, cy = fx[pick, 2][:, None], fy[pick, 2][:, None]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            den = np.where(np.abs(den) < 1e-12, 1e-12, den)
            l1 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
            l2 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
            l3 = 1.0 - l1 - l2
            col = np.floor(gx).astype(np.int32)
            row = np.floor(gy).astype(np.int32)
            ok = (
                (l1 >= -1e-6)
                & (l2 >= -1e-6)
                & (l3 >= -1e-6)
                & (col >= 0)
                & (col < self.width)
                & (row >= 0)
                & (row < self.height)
            )
            if not ok.any():
                continue
            plane = l1 * z[pick, 0][:, None] + l2 * z[pick, 1][:, None] + l3 * z[pick, 2][:, None]
            self._idx.append(row[ok].astype(np.int64) * self.width + col[ok])
            self._z.append(plane[ok].astype(np.float32))
            self._s.append(np.full(int(ok.sum()), source_id, np.uint16))
            self._n += int(ok.sum())
        if self._n > RASTER_FLUSH:
            self.flush()

    def result(self) -> tuple[np.ndarray, np.ndarray]:
        self.flush()
        z = self.z.reshape(self.height, self.width)
        return np.where(np.isfinite(z), z, np.nan).astype(np.float32), self.src.reshape(
            self.height, self.width
        )


def winding_sign(verts: np.ndarray, tris: np.ndarray) -> float:
    """+1 if this mesh's triangle normals point outward, -1 if inward, 0 if it cannot tell.

    A max-Z field wants only the up-facing half of a closed rock, and which half that is
    depends on the winding the cooker emitted. Measured per mesh from the divergence of the
    face normals about the centroid rather than assumed, and 0 -- an open shell where the
    question is meaningless -- means every triangle is kept, which is the safe answer for a
    max-Z fold.
    """
    a, b, c = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    normals = np.cross(b - a, c - a)
    centre = verts.mean(0)
    divergence = float((normals * ((a + b + c) / 3 - centre)).sum())
    scale = float(np.abs(normals).sum() * np.abs(verts - centre).max()) + 1e-9
    ratio = divergence / scale
    return 1.0 if ratio > 0.02 else (-1.0 if ratio < -0.02 else 0.0)


def rasterise_cliffs(sweep: dict, geometry: dict, frame: dict, progress: bool = True) -> dict:
    """Transform, cull and rasterise every placed rock into a 1 m max-Z overlay, in cm.

    Culling, in the order it costs least: an excluded owner, a mesh with no cooked geometry,
    an arch, an oversized shell, then the downward-facing half of the triangles, then the
    triangles that fall outside the mesh's own padded bounds. That last one is per triangle
    rather than per mesh, because the defect it removes is one stray vertex in an otherwise
    good mesh, and dropping the mesh for it would cost a real rock.
    """
    placements = sweep["placements"]
    meshes, owners = sweep["meshes"], sweep["owners"]
    windings = {m: winding_sign(v, t) for m, (v, t, _lo, _hi) in geometry.items()}
    raster = MaxZRaster(
        frame["width"], frame["height"], frame["x0_cm"], frame["y0_cm"], frame["scale_cm"]
    )
    arch_ids = {i for i, m in enumerate(meshes) if ARCH_MARK in m.rsplit("/", 1)[-1]}
    dropped = {"owner": 0, "no_geometry": 0, "arch": 0, "oversize": 0}
    used = 0
    triangles = 0
    clamped = 0
    started = time.time()
    for count, row in enumerate(placements):
        mesh_id, owner_id = int(row[0]), int(row[1])
        mesh = meshes[mesh_id]
        if owners[owner_id] in EXCLUDED_OWNERS:
            dropped["owner"] += 1
            continue
        if mesh not in geometry:
            dropped["no_geometry"] += 1
            continue
        if mesh_id in arch_ids:
            dropped["arch"] += 1
            continue
        verts, tris, low, high = geometry[mesh]
        scale = row[8:11].astype(np.float32)
        if float(np.abs(verts * scale).max()) > OVERSIZE_CM:
            dropped["oversize"] += 1
            continue
        matrix = rotation_matrix(*row[5:8]).astype(np.float32)
        # The bounds clamp is applied in LOCAL space, where the mesh's own ExtendedBounds
        # live, so it costs one comparison per vertex instead of a transformed box per
        # placement -- and it is the same box for all 200 copies of a rock.
        keep = ((verts >= low) & (verts <= high)).all(axis=1)
        if not keep.all():
            good = keep[tris].all(axis=1)
            clamped += int((~good).sum())
            tris = tris[good]
            if tris.size == 0:
                continue
        world = (verts * scale) @ matrix + row[2:5].astype(np.float32)
        tri = world[tris]
        facing = windings[mesh] * np.sign(scale[0] * scale[1] * scale[2])
        if facing != 0:
            normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            tri = tri[(normals[:, 2] * facing) > 0]
        if tri.shape[0]:
            raster.add(tri, mesh_id + 1)
            triangles += tri.shape[0]
        used += 1
        if progress and count % 4000 == 0:
            print(
                f"  {count}/{len(placements)} placements, {used} rasterised, "
                f"{triangles / 1e6:.1f} M triangles, {time.time() - started:.0f}s",
                flush=True,
            )
    z_cm, _src = raster.result()
    return {
        "z_cm": z_cm,
        "placements_total": len(placements),
        "placements_used": used,
        "dropped": dropped,
        "arch_meshes": len(arch_ids & set(np.unique(placements[:, 0]).astype(int))),
        "triangles": int(triangles),
        "triangles_out_of_bounds": clamped,
        "seconds": time.time() - started,
    }


# --------------------------------------------------------------------------------------
# Stage 4: the interface raster, as fill outside the landscape frame.
# --------------------------------------------------------------------------------------


def decode_baseline(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The interface raster's float16 texels to world centimetres, and where it says anything.

    Split out from the read so the rule below is a pure function a test can hold, because it
    is the single easiest thing in this file to get wrong. **The blank value is ``raw == 0``
    and it decodes to about -522 m, not to zero.** So the no-data test is on the DECODED
    height against ``FILL_FLOOR_CM``, which sits below the world's own floor of -255 m and
    above the blank. Testing ``raw > 0`` instead looks equivalent, is not, and leaks 138,481
    texels of blank into the field as a false sea floor at the bottom of the map.
    """
    z_cm = values.astype(np.float32) * BASELINE_SCALE_CM_PER_RAW + BASELINE_OFFSET_CM
    return z_cm, z_cm > FILL_FLOOR_CM


def read_baseline(store) -> tuple[np.ndarray, np.ndarray]:
    """``HeightData_Test`` as world centimetres, with the mask of where it says anything.

    The length check is the integrity check, exactly as it is for the map slices next door:
    2048 down to 128 at two bytes a texel is one number, and a file that is not that long
    was re-cooked at another size or mip count, i.e. the game changed.
    """
    if BASELINE_PATH not in store.by_path:
        raise SystemExit(
            "HeightData_Test is not in the container. The interface raster moved or was "
            "renamed, which means the game changed; the fill layer has no source."
        )
    raw = store.read_path(BASELINE_PATH)
    if len(raw) != BASELINE_BYTES:
        chain = ", ".join(f"{px}x{px}" for px, _size in BASELINE_MIPS)
        raise SystemExit(
            f"HeightData_Test.ubulk is {len(raw)} bytes, expected exactly {BASELINE_BYTES} "
            f"-- the mip chain {chain} at two bytes per float16 texel. A different length "
            "means the raster was re-cooked, so refusing to decode mip 0 out of a file "
            "whose layout is no longer known."
        )
    values = np.frombuffer(raw[: BASELINE_PX * BASELINE_PX * 2], dtype="<f2").reshape(
        BASELINE_PX, BASELINE_PX
    )
    return decode_baseline(values)


def baseline_indices() -> tuple[np.ndarray, np.ndarray]:
    """Which baseline texel each output column and row falls in. Nearest, never blended.

    The fill is 3.66 m data being read at 1 m, so an interpolation would draw a smooth
    surface out of a raster that has none and hide the coarseness the provenance byte
    exists to declare.
    """
    x0, x1, y0, y1 = BASELINE_BOX_CM
    columns = ORIGIN_X_CM + np.arange(GRID_PX) * SPACING_CM
    rows = ORIGIN_Y_CM + np.arange(GRID_PX) * SPACING_CM
    bi = np.clip(
        ((columns - x0) / (x1 - x0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    bj = np.clip(
        ((rows - y0) / (y1 - y0) * BASELINE_PX - 0.5).round().astype(int), 0, BASELINE_PX - 1
    )
    return bi, bj


# --------------------------------------------------------------------------------------
# Stage 5: water, as information.
# --------------------------------------------------------------------------------------


def water_surface(baseline_cm: np.ndarray, valid: np.ndarray, frame: dict) -> dict:
    """Flat, above-terrain, connected regions of the interface raster: probable water.

    Good to about two metres and not derived from water actors at all -- the ~700 of them
    are a median 323 m apart over submerged ground and their Z is a pivot rather than a
    surface, which is why this is a flatness detector instead. Everything it finds is
    reported and **nothing gates the terrain on it**: a lake gate built on this detector was
    measured to make the field worse, nodes trim90 0.93 against 0.77.
    """
    x0, x1, y0, y1 = BASELINE_BOX_CM
    centres_x = x0 + (np.arange(BASELINE_PX) + 0.5) * (x1 - x0) / BASELINE_PX
    centres_y = y0 + (np.arange(BASELINE_PX) + 0.5) * (y1 - y0) / BASELINE_PX
    li = np.round((centres_x - frame["x0_cm"]) / frame["scale_cm"]).astype(int)
    lj = np.round((centres_y - frame["y0_cm"]) / frame["scale_cm"]).astype(int)
    ok_x = (li >= 0) & (li < frame["width"])
    ok_y = (lj >= 0) & (lj < frame["height"])
    LI, LJ = np.clip(li, 0, frame["width"] - 1), np.clip(lj, 0, frame["height"] - 1)
    land_m = frame["z_cm"][np.ix_(LJ, LI)] / 100.0
    land_ok = frame["good"][np.ix_(LJ, LI)] & ok_y[:, None] & ok_x[None, :]

    base_m = baseline_cm / 100.0
    gradient = np.maximum(np.abs(np.gradient(base_m, axis=0)), np.abs(np.gradient(base_m, axis=1)))
    terrain = np.where(land_ok, land_m, np.where(valid, base_m, np.nan))
    candidate = (
        valid
        & (gradient < WATER_FLAT_GRAD_M)
        & (np.nan_to_num(base_m - terrain, nan=-1e9) > WATER_MIN_DEPTH_M)
    )
    labelled, count = ndimage.label(candidate)
    surface = np.full(base_m.shape, np.nan, np.float32)
    kept = 0
    if count:
        boxes = ndimage.find_objects(labelled)
        sizes = np.bincount(labelled.ravel())
        for label in range(1, count + 1):
            if sizes[label] < WATER_MIN_TEXELS:
                continue
            window = boxes[label - 1]
            mask = labelled[window] == label
            values = base_m[window][mask]
            if values.std() > WATER_LEVEL_STD_M:
                continue
            # Assigned through the mask, not over the bounding box: two bodies can share a
            # box, and painting the box would wipe one of them with the other's level.
            surface[window][mask] = np.median(values)
            kept += 1
    return {"surface_m": surface, "regions": int(count), "bodies": kept}


# --------------------------------------------------------------------------------------
# Composition, validation, and what gets written.
# --------------------------------------------------------------------------------------


def compose(frame: dict, cliffs: dict, baseline_cm: np.ndarray, valid: np.ndarray) -> dict:
    """Fuse the layers into the output grid: fill, then landscape, then cliff over both.

    The order is the argument. The fill is everywhere the interface raster says anything, so
    it goes down first and is the answer only where nothing better arrives. The landscape
    drops in index-aligned over its own frame. The cliff overlay then wins any texel where
    real geometry stands above the sculpted ground -- and also any texel the landscape left
    as a hole, because a cave mouth's rock is still a measurement.
    """
    dx, dy = drop_offsets(frame)
    bi, bj = baseline_indices()

    z_m = np.where(valid, baseline_cm / 100.0, np.nan).astype(np.float32)[np.ix_(bj, bi)]
    prov = np.where(np.isnan(z_m), hf.PROV_NODATA, hf.PROV_FILL).astype(np.uint8)

    land_m = np.where(frame["good"], frame["z_cm"] / 100.0, np.nan).astype(np.float32)
    sub_prov = np.where(frame["good"], hf.PROV_LANDSCAPE, hf.PROV_NODATA).astype(np.uint8)

    cliff_m = (cliffs["z_cm"] / 100.0).astype(np.float32)
    take = np.isfinite(cliff_m) & (~np.isfinite(land_m) | (cliff_m > land_m))
    sub_z = np.where(take, cliff_m, land_m)
    sub_prov = np.where(take, hf.PROV_CLIFF, sub_prov).astype(np.uint8)

    window = np.isfinite(sub_z)
    z_m[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_z[window]
    prov[dy : dy + frame["height"], dx : dx + frame["width"]][window] = sub_prov[window]

    known = np.isfinite(z_m)
    height_dm = np.where(known, np.clip(np.round(z_m * 10.0), -32767, 32767), hf.NODATA)
    error = np.abs(height_dm.astype(np.float32) / 10.0 - z_m)[known]
    return {
        "height_dm": height_dm.astype(np.int16),
        "prov": prov,
        "drop": (dx, dy),
        "coverage": {
            hf.PROV_NAMES[value]: float((prov == value).mean())
            for value in (hf.PROV_NODATA, hf.PROV_LANDSCAPE, hf.PROV_FILL, hf.PROV_CLIFF)
        },
        "z_range_m": [float(np.nanmin(z_m)), float(np.nanmax(z_m))],
        "quantisation_max_m": float(error.max()),
        "quantisation_rms_m": float(np.sqrt((error**2).mean())),
    }


def sample_grid(height_dm: np.ndarray, x_cm: np.ndarray, y_cm: np.ndarray) -> np.ndarray:
    """Read the field at world coordinates, in metres, ``nan`` where it knows nothing.

    Deliberately the same rounding ``Field.texel`` does on the other side, so the number
    this run validates on is the number the server will answer with -- a validation of a
    slightly different sampler would be a validation of something nobody ships.
    """
    col = np.round((x_cm - ORIGIN_X_CM) / SPACING_CM).astype(int)
    row = np.round((y_cm - ORIGIN_Y_CM) / SPACING_CM).astype(int)
    on = (col >= 0) & (col < GRID_PX) & (row >= 0) & (row < GRID_PX)
    values = height_dm[np.clip(row, 0, GRID_PX - 1), np.clip(col, 0, GRID_PX - 1)]
    return np.where(on & (values != hf.NODATA), values.astype(np.float64) / 10.0, np.nan)


def error_stats(errors: np.ndarray, total: int) -> dict:
    """Median offset, then the spread about it: median absolute, P90, and trimmed RMS.

    The offset is removed because a constant bias would be a georeference question rather
    than a decode one, and this pass is guarding the decode. The trim is what keeps the
    verdict honest about caves: about 44 nodes sit *under* the surface -- cave mouths,
    arches, overhangs -- and no single-valued heightmap can represent them, so an untrimmed
    RMS measures the map's topology rather than this file's arithmetic.
    """
    finite = errors[~np.isnan(errors)]
    if finite.size == 0:
        return {"n": 0, "coverage": 0.0}
    offset = float(np.median(finite))
    spread = np.sort(np.abs(finite - offset))
    trimmed = spread[: max(int(spread.size * VALIDATION_TRIM), 1)]
    return {
        "n": int(finite.size),
        "coverage": round(finite.size / total, 4),
        "offset_m": round(offset, 4),
        "medabs_m": round(float(np.median(spread)), 4),
        "p90_m": round(float(np.percentile(spread, 90)), 4),
        "trim90_rms_m": round(float(np.sqrt((trimmed**2).mean())), 4),
        "under_1m": int((spread < 1.0).sum()),
    }


def validate(height_dm: np.ndarray, prov: np.ndarray) -> dict:
    """Measure the built field against the static node table, whole and per layer.

    The whole-field number is the gate; the per-layer ones are what the sidecar carries so a
    reading can quote the accuracy of the layer that answered it rather than one number for
    a field that is a fifth of a metre good in the middle and four metres good at the edge.
    """
    nodes = json.loads(NODE_TABLE.read_text(encoding="utf-8"))["nodes"]
    x = np.array([n["x"] for n in nodes], float)
    y = np.array([n["y"] for n in nodes], float)
    z = np.array([n["z"] for n in nodes], float) / 100.0
    errors = z - sample_grid(height_dm, x, y)
    col = np.clip(np.round((x - ORIGIN_X_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    row = np.clip(np.round((y - ORIGIN_Y_CM) / SPACING_CM).astype(int), 0, GRID_PX - 1)
    layers = prov[row, col]
    per_layer = {}
    for value in (hf.PROV_LANDSCAPE, hf.PROV_FILL, hf.PROV_CLIFF):
        pick = layers == value
        per_layer[hf.PROV_NAMES[value]] = error_stats(
            np.where(pick, errors, np.nan), int(pick.sum())
        )
    return {
        "against": str(NODE_TABLE.relative_to(ROOT)).replace("\\", "/"),
        "nodes": len(nodes),
        "field": error_stats(errors, len(nodes)),
        "per_layer": per_layer,
        "method": (
            "every static resource node's own Z against the field read at its coordinate, "
            "with the median offset removed and the worst 10% trimmed. The trim is not "
            "cosmetic: about 44 nodes sit under the surface in caves, arches and overhangs, "
            "which no single-valued heightmap can represent and which the interface raster "
            "fails by the same test."
        ),
        "gate_m": VALIDATION_TRIM_RMS_MAX_M,
        "reference": (
            "the workflow that proved this pipeline measured 0.368 m trimmed RMS and "
            "0.210 m median absolute on this set; the interface raster alone measures "
            "1.080 m and 0.854 m, and its own scale and offset were fitted on these nodes."
        ),
    }


def accuracy_block(validation: dict) -> dict:
    """What each provenance value means, and how well it was measured to do.

    ``accuracy_m`` is the measured median absolute error where enough nodes fell on that
    layer to mean anything, and the layer's own vertical step where they did not -- which
    is honest in both directions, and says which it is.
    """
    derived = {
        hf.PROV_LANDSCAPE: (1.0, LANDSCAPE_SCALE_CM / LANDSCAPE_PER_UNIT / 100.0),
        hf.PROV_CLIFF: (1.0, 0.0),
        hf.PROV_FILL: (FILL_HORIZONTAL_M, FILL_VERTICAL_M),
    }
    notes = {
        hf.PROV_LANDSCAPE: (
            "the cooked UE Landscape heightfield: a true 1 m grid, 7.8 mm vertical "
            "quantisation, no resampling anywhere between the component and this texel"
        ),
        hf.PROV_CLIFF: (
            "rasterised Chaos collision geometry from a placed rock or cliff -- exact "
            "float32 triangles at about 1 m mean edge length, so the vertical step is "
            "continuous and the 0.1 m container is the only rounding"
        ),
        hf.PROV_FILL: (
            "the 2048 px HeightData_Test interface raster, outside the landscape frame. "
            "3.66 m horizontally and 3.897 m per quantisation step: this is the old "
            "baseline unchanged, and it is the coarsest thing in the field"
        ),
    }
    out: dict[str, dict] = {
        str(hf.PROV_NODATA): {
            "name": hf.PROV_NAMES[hf.PROV_NODATA],
            "accuracy_m": None,
            "note": (
                "open ocean past the landscape edge, and two cave-mouth blobs. Explicit, "
                "never zero-filled: say nothing here."
            ),
        }
    }
    for value, (horizontal, vertical) in derived.items():
        name = hf.PROV_NAMES[value]
        measured = validation["per_layer"].get(name, {})
        enough = measured.get("n", 0) >= ACCURACY_MIN_SAMPLES
        out[str(value)] = {
            "name": name,
            "horizontal_m": round(horizontal, 4),
            "vertical_step_m": round(vertical, 4),
            "accuracy_m": (measured["medabs_m"] if enough else round(max(vertical, 0.1), 3)),
            "accuracy_from": (
                f"measured: median absolute error over {measured.get('n', 0)} static "
                "resource nodes that fell on this layer"
                if enough
                else (
                    f"derived: this layer's own vertical step, because only "
                    f"{measured.get('n', 0)} nodes fell on it and that is fewer than "
                    f"{ACCURACY_MIN_SAMPLES}"
                )
            ),
            "measured": measured,
            "note": notes[value],
        }
    return out


def build_meta(
    *,
    build_pin: str,
    build_raw: dict,
    sweep: dict,
    frame: dict,
    meshes: dict,
    cliffs: dict,
    field: dict,
    water: dict,
    validation: dict,
    files: dict,
    pyooz_version: str,
    timings: dict,
) -> dict:
    """The sidecar the loader reads, plus the provenance a reader needs to date the field."""
    dx, dy = field["drop"]
    return {
        "description": (
            "A 1 m terrain heightfield of the Satisfactory world, cut from the reader's own "
            "installed game by tools/gen_world_heightmap.py. All of it is local: data/local/ "
            "is gitignored and no terrain raster is ever committed to this repository."
        ),
        "generator": "tools/gen_world_heightmap.py",
        "generator_version": GENERATOR_VERSION,
        "transcribed": datetime.now(UTC).date().isoformat(),
        "grid": {
            "width": GRID_PX,
            "height": GRID_PX,
            "spacing_cm": SPACING_CM,
            "x0_cm": ORIGIN_X_CM,
            "y0_cm": ORIGIN_Y_CM,
            "georeference": (
                f"x_cm = {ORIGIN_X_CM:.0f} + col*{SPACING_CM:.0f}, "
                f"y_cm = {ORIGIN_Y_CM:.0f} + row*{SPACING_CM:.0f}"
            ),
            "alignment": (
                "vertex-aligned: a texel's height belongs to that point exactly, not to a "
                "cell around it, so a reader rounds to the nearest vertex rather than "
                "flooring into a cell"
            ),
            "axes": "game axes -- +X east, +Y south, so row 0 is the northern edge",
        },
        "units": "decimetres above sea level, int16",
        "nodata": hf.NODATA,
        "files": files,
        "provenance": accuracy_block(validation),
        "coverage": {
            **{k: round(v, 6) for k, v in field["coverage"].items()},
            "known": round(1.0 - field["coverage"][hf.PROV_NAMES[hf.PROV_NODATA]], 6),
        },
        "z_range_m": [round(v, 2) for v in field["z_range_m"]],
        "container": {
            "quantisation_max_m": round(field["quantisation_max_m"], 4),
            "quantisation_rms_m": round(field["quantisation_rms_m"], 4),
            "why": (
                "int16 decimetres. The rounding costs the RMS above, which is an eighth of "
                "the field's own measured accuracy; int32 would double the file for nothing."
            ),
        },
        "validation": validation,
        "sources": {
            "game": {
                "install": "the reader's own Satisfactory install",
                "licence": (
                    "Coffee Stain Studios' own cooked assets, read locally. Not committed, "
                    "not redistributed, and served to localhost only."
                ),
                "game_version_pinned": build_pin,
                "game_version_raw": {
                    key: build_raw.get(key)
                    for key in ("Changelist", "BranchName", "BuildId", "GameVersion")
                },
            },
            "landscape": {
                "class": "LandscapeComponent",
                "derivation": (
                    f"GrassData height, {LANDSCAPE_N}x{LANDSCAPE_N} uint16 per component; "
                    f"world_z_cm = (h - {LANDSCAPE_ZERO:.0f})/{LANDSCAPE_PER_UNIT:.0f}"
                    f"*{frame['scale_cm']:.0f} + {frame['origin_z_cm']:.0f}"
                ),
                "components": frame["components"],
                "frame": [frame["width"], frame["height"]],
                "frame_origin_cm": [frame["x0_cm"], frame["y0_cm"]],
                "drop_texels": [dx, dy],
                "resampling": (
                    "none. The frame origin sits a whole number of texels from this grid's, "
                    "which the run asserts rather than rounds into, so every landscape "
                    "sample is its own texel"
                ),
                "coverage_of_frame": round(frame["coverage"], 4),
                "holes": {
                    "texels": frame["hole_texels"],
                    "blobs": frame["hole_blobs"],
                    "note": (
                        "raw == 0 inside a component that is present: a landscape hole or "
                        "cave mouth, left as no data rather than read as -255 m"
                    ),
                },
            },
            "cliffs": {
                "class": "BodySetup / FTriangleMeshImplicitObject",
                "derivation": (
                    "cooked Chaos collision trimesh, found by searching for the 267 (0x10B) "
                    "marker, never at a fixed offset; NumVerts float32 triples then NumTris "
                    "index triples, width chosen by validating max(index) < NumVerts"
                ),
                "rock_meshes_seen": meshes["wanted"],
                "meshes_decoded": len(meshes["geometry"]),
                "meshes_without_cooked_trimesh": len(meshes["failures"]),
                "closed_manifolds": meshes["closed_manifolds"],
                "closed_manifold_check": (
                    f"{meshes['closed_manifolds']} of {len(meshes['geometry'])} satisfy "
                    "NumTris == 2*NumVerts - 4 exactly. The rest are the open shells -- cave "
                    "walls, floors, ceilings and merged arch pieces -- and are meant to be"
                ),
                "vertices": meshes["verts"],
                "triangles_in_source": meshes["tris"],
                "placements_total": cliffs["placements_total"],
                "placements_rasterised": cliffs["placements_used"],
                "placements_dropped": cliffs["dropped"],
                "triangles_rasterised": cliffs["triangles"],
                "triangles_outside_bounds": cliffs["triangles_out_of_bounds"],
                "exclusions": (
                    "NodeMeshActor_C, because predicting a resource node's Z from the mesh "
                    "drawn under it would be circular and the node table is what validates "
                    f"this field; anything whose basename contains '{ARCH_MARK}', because a "
                    "max-Z field puts an arch roof over the ground beneath it and masking "
                    "them improved every metric on all three validation sets; and anything "
                    f"whose scaled extent exceeds {OVERSIZE_CM:.0f} cm, which is the sky "
                    "dome and the ocean shells"
                ),
            },
            "fill": {
                "asset": "/Game/FactoryGame/Interface/UI/Assets/MapTest/HeightData_Test",
                "derivation": (
                    f"{BASELINE_PX}x{BASELINE_PX} float16 mip 0; "
                    f"z_cm = {BASELINE_SCALE_CM_PER_RAW:.4f}*raw + {BASELINE_OFFSET_CM:.4f}, "
                    "from a robust fit against the 626 static nodes (569 inliers, 1.07 m RMS)"
                ),
                "nodata_rule": (
                    f"decoded z > {FILL_FLOOR_CM / 100:.0f} m, NOT raw > 0. The blank value "
                    "decodes to about -522 m, so the naive test leaks 138,481 texels of "
                    "blank into the field as a false sea floor"
                ),
                "role": "outside the landscape frame only; the old baseline, unchanged",
            },
            "water": {
                "derivation": (
                    "flat, above-terrain, connected regions of the interface raster: "
                    f"|gradient| < {WATER_FLAT_GRAD_M} m, more than {WATER_MIN_DEPTH_M} m "
                    f"above the terrain, at least {WATER_MIN_TEXELS} texels, level to "
                    f"within {WATER_LEVEL_STD_M} m"
                ),
                "regions_found": water["regions"],
                "bodies_kept": water["bodies"],
                "accuracy_m": 2.0,
                "role": (
                    "INFORMATION ONLY. It does not gate the terrain, and that is measured "
                    "rather than omitted: the world's ~700 water actors are a median 323 m "
                    "apart over submerged ground and their Z is a pivot rather than a "
                    "surface, and a lake gate built on this flatness detector was measured "
                    "to make the field worse -- nodes trim90 0.93 against 0.77."
                ),
            },
        },
        "sweep": {
            "packages": sweep["packages"],
            "unreadable": sweep["unreadable"],
            "malformed_components": sweep["malformed_components"],
            "placements": len(sweep["placements"]),
            "distinct_meshes": len(sweep["meshes"]),
        },
        "decoders": {
            "oodle": {
                "name": "pyooz",
                "version": pyooz_version,
                "import_name": "ooz",
                "licence": "GPL-3.0",
                "role": (
                    "container block decompression, offline, at generation time only. Not a "
                    "dependency of this project, never imported from src/ or sidecar/, and "
                    "no part of it is in the output."
                ),
            },
            "container": "tools/gen_world_collectibles.py's IoStore reader, imported by path",
            "codec": "satisfactory_mcp.domain.spatial.heightfield, imported so there is one",
        },
        "timings_s": timings,
        "known_defects": [
            (
                "about a fifth of the nominal box is no-data: open ocean past the landscape "
                "edge plus two cave-mouth blobs. Explicit, never zero-filled."
            ),
            (
                "the cave and overhang tail is irreducible. About 44 nodes sit UNDER the "
                "surface, and no single-valued heightmap can represent them; the interface "
                "raster fails 51 by the same test. A two-layer field is the principled fix "
                "and costs one extra rasteriser pass."
            ),
            (
                "21 of the rock meshes ship no cooked trimesh (CTF_UseSimpleAndComplex): "
                "SM_RockPile_*, SM_Cave_Pillar_*, SmoothRock_01 and a few others. Small and "
                "rare; their AggGeom convex hulls are still open for a later pass."
            ),
            "the water channel is good to about 2 m and is information only.",
        ],
        "staleness": (
            "sources.game.game_version_pinned is the build this field was cut from, in the "
            "same shape data/resource_nodes.json uses, so a field and a node table from "
            "different builds are comparable on sight. tools/gen_world_heightmap.py refuses "
            "to overwrite this directory unless the sidecar names the build then installed; "
            "--force says it anyway. Terrain moves every patch, and the standing rule is "
            "that a pinned artifact announces drift rather than answering silently wrong."
        ),
    }


def pinned_build(meta: dict) -> str | None:
    """The build an existing sidecar names, or None if it names none.

    ``PIN_PATH`` is this file's statement about its own sidecar; the walk that follows it
    is everyone's, and lives in ``core.gameassets.provenance``.
    """
    return read_str_path(meta, PIN_PATH)


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o",
        "--out-dir",
        type=Path,
        default=LOCAL_DIR / hf.DIR_NAME,
        help="destination directory for the rasters and meta.json (gitignored)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite a field this run cannot show was cut from the installed build",
    )
    parser.add_argument("--quiet", action="store_true", help="no per-stage progress lines")
    args = parser.parse_args()

    pyooz_version = require_gen("ooz")["pyooz"]

    try:
        build_pin, build_raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc} -- point --game at the install holding FactoryGame/ and Engine/")
        return 1
    print(f"installed build: {build_pin}")

    out_dir: Path = args.out_dir
    if out_dir.is_dir() and not args.force:
        existing: dict = {}
        try:
            loaded = json.loads((out_dir / hf.META_NAME).read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError, TypeError):
            existing = {}
        pinned = pinned_build(existing)
        if pinned != build_pin:
            print(
                f"{out_dir} already exists and this run cannot show it was cut from the "
                f"installed build.\n"
                f"  installed: {build_pin}\n"
                f"  that field: {pinned or 'no meta.json, or no build recorded in it'}\n"
                "Terrain moves every patch and the repository's own tables are pinned to a "
                "build the new field may no longer agree with, so drift is announced rather "
                "than overwritten. Pass --force to overwrite it anyway."
            )
            return 3

    if not NODE_TABLE.is_file():
        print(
            f"{NODE_TABLE} is not present, so this run could not validate the field it "
            "built. A heightmap nobody measured is not one this file will write."
        )
        return 4

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the world from {paks} with pyooz {pyooz_version}")
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    index = AssetIndex(store)
    print(
        f"  .utoc v{store.version}, {store.entry_count} entries, "
        f"{store.block_size // 1024} KiB blocks, methods {store.methods}"
    )
    loud = not args.quiet
    timings: dict[str, float] = {}

    # ---- stages 1 and 2: one sweep -----------------------------------------------------
    print("sweeping the world's packages for landscape components and placements")
    sweep = sweep_levels(store, scripts, loud)
    timings["sweep"] = round(sweep["seconds"], 1)
    print(
        f"  {sweep['packages']} packages in {sweep['seconds']:.0f}s: "
        f"{len(sweep['components'])} landscape components, {len(sweep['placements'])} "
        f"placements over {len(sweep['meshes'])} distinct meshes "
        f"({sweep['unreadable']} unreadable, {sweep['malformed_components']} malformed)"
    )

    started = time.time()
    frame = landscape_frame(sweep)
    dx, dy = drop_offsets(frame)
    timings["landscape"] = round(time.time() - started, 1)
    print(
        f"  landscape {frame['width']}x{frame['height']} m at "
        f"({frame['x0_cm']:.0f}, {frame['y0_cm']:.0f}) cm, {frame['coverage'] * 100:.1f}% "
        f"covered, {frame['hole_texels']} hole texels in {frame['hole_blobs']} blobs"
    )
    print(f"  drops into the output grid at texel ({dx}, {dy}), exactly -- no resampling")

    # ---- stage 3: cliff collision ------------------------------------------------------
    print("decoding the cooked collision trimesh of every placed rock")
    meshes = read_mesh_geometry(store, scripts, index, sweep["meshes"], loud)
    timings["mesh_decode"] = round(meshes["seconds"], 1)
    print(
        f"  {len(meshes['geometry'])}/{meshes['wanted']} rock meshes decoded in "
        f"{meshes['seconds']:.0f}s: {meshes['verts']} vertices, {meshes['tris']} triangles, "
        f"{meshes['closed_manifolds']} closed manifolds"
    )
    if not meshes["geometry"]:
        print(
            "not one rock mesh decoded. The cooked collision layout changed, which is the "
            "whole of what makes this field better than the interface raster. Refusing."
        )
        return 5
    print("rasterising them into a 1 m max-Z overlay")
    cliffs = rasterise_cliffs(sweep, meshes["geometry"], frame, loud)
    timings["rasterise"] = round(cliffs["seconds"], 1)
    print(
        f"  {cliffs['placements_used']}/{cliffs['placements_total']} placements, "
        f"{cliffs['triangles'] / 1e6:.1f} M triangles in {cliffs['seconds']:.0f}s; "
        f"dropped {cliffs['dropped']}"
    )

    # ---- stage 4: fill, and stage 5: water --------------------------------------------
    started = time.time()
    baseline_cm, baseline_valid = read_baseline(store)
    water = water_surface(baseline_cm, baseline_valid, frame)
    timings["fill_and_water"] = round(time.time() - started, 1)
    print(
        f"  interface raster decoded, {baseline_valid.mean() * 100:.1f}% of it says "
        f"something; water found {water['regions']} candidate regions and kept "
        f"{water['bodies']} bodies"
    )

    # ---- compose, validate, write ------------------------------------------------------
    started = time.time()
    field = compose(frame, cliffs, baseline_cm, baseline_valid)
    timings["compose"] = round(time.time() - started, 1)
    for name, fraction in field["coverage"].items():
        print(f"  {name:>10}: {fraction * 100:6.2f}% of the box")
    print(
        f"  z range {field['z_range_m'][0]:.1f} .. {field['z_range_m'][1]:.1f} m; "
        f"int16-decimetre quantisation RMS {field['quantisation_rms_m']:.4f} m"
    )

    started = time.time()
    validation = validate(field["height_dm"], field["prov"])
    timings["validate"] = round(time.time() - started, 1)
    whole = validation["field"]
    print(
        f"  validated on {whole['n']}/{validation['nodes']} nodes: trimmed RMS "
        f"{whole['trim90_rms_m']:.3f} m, median absolute {whole['medabs_m']:.3f} m, "
        f"P90 {whole['p90_m']:.2f} m, {whole['under_1m']} within a metre"
    )
    for name, stats in validation["per_layer"].items():
        if stats["n"]:
            print(
                f"    {name:>10}: n={stats['n']:3d} medabs {stats['medabs_m']:.3f} m, "
                f"trimmed RMS {stats['trim90_rms_m']:.3f} m"
            )
    if whole["trim90_rms_m"] > VALIDATION_TRIM_RMS_MAX_M:
        print(
            f"trimmed RMS is {whole['trim90_rms_m']:.3f} m against a gate of "
            f"{VALIDATION_TRIM_RMS_MAX_M} m. Something in the decode moved: the workflow "
            "that proved this pipeline measured 0.368 m, and a field this far out would be "
            "a plausible-looking raster that is quietly metres wrong. Refusing to write."
        )
        return 6

    started = time.time()
    water_dm = np.where(
        np.isfinite(water["surface_m"]),
        np.clip(np.round(np.nan_to_num(water["surface_m"]) * 10.0), -32767, 32767),
        hf.NODATA,
    ).astype(np.int16)
    bi, bj = baseline_indices()
    payload = {
        hf.HEIGHT_NAME: hf.encode_i16(field["height_dm"]),
        hf.PROV_NAME: hf.encode_u8(field["prov"]),
        hf.WATER_NAME: hf.encode_i16(np.ascontiguousarray(water_dm[np.ix_(bj, bi)])),
    }
    timings["encode"] = round(time.time() - started, 1)
    files = {
        hf.HEIGHT_NAME: {
            "content": f"{GRID_PX}x{GRID_PX} int16 decimetres, row-delta then zlib",
            "bytes": len(payload[hf.HEIGHT_NAME]),
        },
        hf.PROV_NAME: {
            "content": "which layer answered each texel: 0 no-data, 1 landscape, 3 fill, "
            "4 cliff collision. zlib, no delta",
            "bytes": len(payload[hf.PROV_NAME]),
        },
        hf.WATER_NAME: {
            "content": "water surface Z, same grid, same no-data. Information only",
            "bytes": len(payload[hf.WATER_NAME]),
        },
    }
    meta = build_meta(
        build_pin=build_pin,
        build_raw=build_raw,
        sweep=sweep,
        frame=frame,
        meshes=meshes,
        cliffs=cliffs,
        field=field,
        water=water,
        validation=validation,
        files=files,
        pyooz_version=pyooz_version,
        timings=timings,
    )
    payload[hf.META_NAME] = json.dumps(meta, indent=1).encode("utf-8")
    written = install_directory(out_dir, payload)
    total = sum(written.values())
    print(f"wrote {out_dir}  {total} B  ({total / 1e6:.1f} MB)")
    for name, size in written.items():
        print(f"  {name:>14}  {size:>10} B")
    print("none of it is committed: data/local/ is gitignored and stays that way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
