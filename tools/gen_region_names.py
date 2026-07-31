"""Generate data/region_names.json -- the ADVISORY region-name layer, from the game's areas.

    uv run --extra gen python tools/gen_region_names.py

This is layer 2 of the spatial design. Layer 1 (grid cells, cones, radii, clustering) is exact
and derived, and every calculation uses it. This layer only puts human names on coordinates.
A name must never feed a computation.

What changed, and why the file is worth re-reading
--------------------------------------------------
This artifact used to be a raster of ``data/satisfactory_regions.json``, whose region geometry
was hand-traced off the wiki's Biome Map image. That made the boundaries somebody's reading of
a picture, good to about one 256 m cell, and it put a CC BY-SA obligation on a derived table in
a repository whose whole licence posture is that no copyleft reaches it. It also carried a
claim that was simply false: *"the game ships no biome geometry"*.

The game ships biome geometry. ``FGMapAreaTexture`` at
``/Game/FactoryGame/Interface/UI/Minimap/MapAreaPersistenLevel/MapareatexturePersistentLevel``
is a 4096x4096 raster of palette indices at 1.83 m to the texel, and ``mColorToArea`` resolves
each index to a ``UFGMapArea`` object -- which carries the game's own name for the place. That
is what this file reads now, through ``core.gameassets.maparea``, which
``tools/gen_map_renders.py`` reads the same asset through.

So the boundaries are the game's own polygon edges rather than a trace of them, the names come
out of the assets rather than off a wiki page, and nothing here is licensed by anyone but Coffee
Stain -- the same posture as every other derived table in ``data/``: coordinates and identifiers
read out of the reader's own install, no artwork shipped.

The names are the game's, including the surprising ones
--------------------------------------------------------
Each ``Area_*`` asset states ``mDisplayName`` as a string-table reference, ``World_Data`` /
``Locations/<Something>``. :data:`DISPLAY_NAMES` maps those nineteen keys to the labels this
file emits, and the mapping is nearly the identity -- the familiar names ARE the game's names.
Three facts only the assets could have supplied:

* ``Area_Savanna_1`` and ``Area_Savanna_2`` both announce ``Locations/RockyDesert``. The game
  has a Savanna asset and no Savanna region; those two areas are Rocky Desert.
* ``Area_RedJungle_1`` is ``Locations/RedJungle`` and ``Area_RedJungle_2`` is
  ``Locations/JungleSpires``. Two regions, one asset name.
* ``Area_crater_1`` is ``Locations/BlueCrater`` and ``Area_crater_2`` is
  ``Locations/CraterLakes``. Likewise.

Two of the nineteen labels are not the key verbatim: ``Locations/DesertCanyon`` and
``Locations/MazeCanyon`` are emitted as the plurals every player and every tool in this
repository already uses. That is a spelling, recorded in ``_meta.naming_rules``, and it is the
only place a familiar name is preferred to the key.

Two names the retired trace carried are gone, because the game does not name them:
**Western Beaches** and **Snaketree Forest**. **Eastern Dune Forest** is a third case and a
different one -- the game HAS an ``Area_EasternDuneForest_1`` asset with that display name, and
the raster never references it, so there is no ground to put the label on.

No-man's-land, and what "void" now means
----------------------------------------
43% of the raster is ``Area_NoMansLand``, which is the ocean and the outer coast. It is not
"unknown": the game has an object for it with its own display name, so this file labels it
**No Man's Land** rather than blanking it. That is the honest answer for a player standing on
the outer coast, where the retired trace answered with a wiki region name and a raster with no
no-man's-land class would have answered "off-map or ocean".

Void is now the narrower thing it always should have been: **the game names no area here AND no
known static object stands within 1 km**. Ocean, and off-map. The land mask decides only that
last clause -- it no longer decides what anything is called, because the raster does.

Grid resolution: two, and the measurement that chose them
----------------------------------------------------------
``region_grid`` stays 30x30 at 256 m. That is the payload ``/api/regions`` serves and the
rectangles the map paints, and keeping it is what makes this re-derivation cost the frontend
nothing but a legend.

A majority downsample is lossy, and how lossy was measured rather than guessed -- 5,054 known
static world objects looked up in the grid versus in the raster itself:

===========  ============  ===============  ===================
cell         grid          characters       mislabelled probes
===========  ============  ===============  ===================
256 m        30x30                 900      714 (14.13%)
128 m        60x60               3,600      421 (8.33%)
**64 m**     **120x120**    **14,400**      **268 (5.30%)**
32 m         240x240            57,600      132 (2.61%)
===========  ============  ===============  ===================

So a second grid is carried at 64 m, ``fine_grid``, and ``domain.spatial.regions`` reads names
out of it. It is NOT served: the payload contract is unchanged, and 14 kB in a committed table
buys a lookup that is wrong a third as often. 32 m was refused -- four times the bytes for
twice the accuracy, and past the size where this stays a table a person can open. Neither is
exact, and the confidence word says so; exactness at a point needs the 4096x4096 raster, which
is 16 MB and belongs in the game rather than in ``data/``.

Each grid has a confidence of its own shape -- ``confidence_grid`` beside ``region_grid`` at
256 m, ``fine_confidence`` beside ``fine_grid`` at 64 m -- rather than one confidence shared
between two resolutions, which would be an index that reads fine and answers wrong.

The confidence grid changed meaning, and the letters kept their names
----------------------------------------------------------------------
It used to be neighbour agreement standing in for "the trace might be off here". The boundaries
are exact now, so each 256 m cell's flag is a MEASUREMENT of that cell:

* ``l`` interior -- one area covers the whole cell.
* ``b`` boundary -- an exact area boundary runs through this cell.
* ``u`` unnamed -- the cell is No Man's Land: labelled, but with the game's name for ground it
  does not otherwise name.
* ``.`` void -- no name at all.

Staleness
---------
``_meta.game_version_pinned`` is the installed build, and the map areas move when the map does.
The run refuses to write over a table cut from a different build unless ``--force`` says so,
and it re-measures the calibration every time rather than trusting the last one.

What is NOT here any more
-------------------------
``node_region_overrides``, and with it the ``verified`` confidence. It held 48 oil nodes whose
region had been read off the wiki's image by eye and was trusted over the raster; the raster is
first-party now and the overrides' basis is gone. Keying a correction by instance name would be
the wrong repair in any case -- this repository already knows a map update renames instances,
which is what the node skew gate exists for. A node is labelled by where it stands, like every
other coordinate.

Licence
-------
Everything here is derived from Coffee Stain's cooked assets, read out of the reader's own
installed copy of the game: palette indices, area identifiers and localisation keys. No
artwork is decoded, none is committed, and no third-party table contributes. The land mask
reads two committed tables, both non-copyleft, and contributes no names.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.maparea import (
    MAP_AREA_CLASS,
    MAP_AREA_PATH,
    MapAreaError,
    read_map_areas,
)
from satisfactory_mcp.core.gameassets.packages import ScriptObjects
from satisfactory_mcp.core.gameassets.provenance import (
    InstallNotFound,
    installed_build,
    installed_build_from_exe,
    read_path,
)
from satisfactory_mcp.domain.spatial import geo
from tools._common import base_parser, require_gen

# The frame, the artwork sheet and the calibration all come from the two generators that
# MEASURED them, rather than being typed again here. ``gen_map_image`` owns the corners of the
# in-game map square; ``gen_map_renders`` owns the edge-ratio statistic that placed the area
# raster on those corners, and re-running it here is how this file re-checks the pin instead of
# inheriting it. Sibling-generator imports, the same arrangement ``gen_map_renders`` already has
# with ``gen_map_image`` and for the same reason: one implementation, so three artifacts cannot
# drift into three opinions about where the world is.
from tools.gen_map_image import BOUNDS_M
from tools.gen_map_renders import calibrate_biome, read_artwork_sheet

DEST = ROOT / "data" / "region_names.json"

VOID = "."

#: The 256 m grid this artifact has always published, and the one ``/api/regions`` serves.
#: Unchanged so that the payload, the drawing client and every consumer's arithmetic are
#: exactly what they were.
GRID_X0, GRID_Y0 = -336_000.0, -380_000.0
GRID_CELL = 25_600.0
GRID_NX = GRID_NY = 30

#: And the finer grid names are looked up in. See the module docstring for the measurement
#: that chose 64 m; it divides the 256 m cell exactly, which is what keeps the coarse grid a
#: majority of this one rather than a second independent downsample.
FINE_CELL = 6_400.0

#: The game's own name for each place, keyed by the localisation key its ``Area_*`` asset
#: states. Nineteen keys, nineteen labels, and the mapping is the identity apart from the two
#: plurals noted in the module docstring. A key that is not in here stops the run: an
#: unnamed region is a region this file would have to invent a label for, and inventing one
#: is the failure the whole re-derivation exists to end.
DISPLAY_NAMES = {
    "Locations/AbyssCliffs": "Abyss Cliffs",
    "Locations/BlueCrater": "Blue Crater",
    "Locations/CraterLakes": "Crater Lakes",
    "Locations/DesertCanyon": "Desert Canyons",
    "Locations/DuneDesert": "Dune Desert",
    "Locations/EasternDuneForest": "Eastern Dune Forest",
    "Locations/GrassFields": "Grass Fields",
    "Locations/JungleSpires": "Jungle Spires",
    "Locations/LakeForest": "Lake Forest",
    "Locations/MazeCanyon": "Maze Canyons",
    "Locations/NoMansLand": "No Man's Land",
    "Locations/NorthernForest": "Northern Forest",
    "Locations/RedBambooFields": "Red Bamboo Fields",
    "Locations/RedJungle": "Red Jungle",
    "Locations/RockyDesert": "Rocky Desert",
    "Locations/SouthernForest": "Southern Forest",
    "Locations/SpireCoast": "Spire Coast",
    "Locations/Swamp": "Swamp",
    "Locations/TitanForest": "Titan Forest",
    "Locations/WesternDuneForest": "Western Dune Forest",
}

#: The two labels that are not their key verbatim, stated as data so ``_meta`` can carry the
#: deviation rather than a reader having to diff two lists to find it.
PLURALISED = {"Locations/DesertCanyon": "Desert Canyons", "Locations/MazeCanyon": "Maze Canyons"}

#: What the game calls ground it does not otherwise name. Labelled rather than blanked.
UNNAMED_LABEL = DISPLAY_NAMES["Locations/NoMansLand"]

#: Collectible categories that stand on land and so mark it. Unchanged from the mask this
#: file has always used, and it no longer decides what anything is CALLED -- only whether an
#: unnamed cell is coast or ocean.
LAND_MASK_CATEGORIES = (
    "crashed_drop_pod",
    "mercer_shrine",
    "mercer_sphere",
    "power_slug_blue",
    "power_slug_purple",
    "power_slug_yellow",
    "somersloop",
)

#: A cell the game leaves unnamed and that has no known static object within this far is
#: ocean or off-map. The distribution behind the number is unchanged -- cell-to-nearest
#: distances are strongly bimodal, median 123 m on land against p90 1212 m -- and so is the
#: number, so that the void class this artifact has always published does not move for a
#: reason unrelated to the re-derivation.
VOID_DISTANCE_M = 1000.0

#: The confidence letters, and what each one now MEASURES. See the module docstring.
CONFIDENCE_LEGEND = {
    "l": "interior cell: one area covers the whole 256 m cell",
    "b": "boundary cell: an exact area boundary runs through it",
    "u": "unnamed cell: the game names no region here, so the label is its No Man's Land",
    ".": "void: the game names no region and no known object is within 1 km -- ocean or off-map",
}

#: Measured once, on 2026-07-30, against the committed wiki trace at the commit before it was
#: retired -- and it cannot be measured again, because the source it compares against is
#: deleted. Carried as history because it is the size of the change: what the two sources
#: disagreed about is the reason this re-derivation was worth doing.
RETIRED_TRACE_COMPARISON = {
    "what": (
        "data/satisfactory_regions.json, a hand trace of satisfactory.wiki.gg's Biome Map "
        "image (CC BY-SA 4.0), rasterised at 256 m. Retired in the same commit as this "
        "measurement; the geometry it supplied is now read from the game."
    ),
    "non_void_cells": 768,
    "cells_landing_on_a_named_game_area": 481,
    "cells_landing_on_a_named_game_area_pct": 62.6,
    "cells_comparable_by_name": 401,
    "cells_agreeing": 273,
    "agreement_pct": 68.1,
    "spire_coast": (
        "62 of the 128 misses. The wiki draws one coastal ring; the game draws a smaller "
        "Spire Coast and gives the rest of that ring to four other areas -- Rocky Desert "
        "(40 cells), Desert Canyons (11), Dune Desert (10) and Swamp (1). The game's "
        "granularity is kept: those cells now carry the name the game gives them, which is "
        "the whole point of re-deriving the geometry."
    ),
    "names_the_wiki_had_and_the_game_does_not": ["Western Beaches", "Snaketree Forest"],
    "names_the_game_has_and_the_wiki_did_not": ["Blue Crater is a game name, not a wiki one"],
}


def load(name: str, key: str) -> list[dict]:
    path = ROOT / "data" / name
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} missing -- the land mask needs it")
    rows = json.loads(path.read_text(encoding="utf-8"))[key]
    missing = [r for r in rows if "x" not in r or "y" not in r]
    if missing:
        raise SystemExit(f"{name}: {len(missing)} of {len(rows)} {key} carry no x/y")
    return rows


def reference_points() -> tuple[np.ndarray, dict[str, int]]:
    """Static world objects, used purely to tell coast from ocean.

    Positions only, and now with a much smaller job than they used to have: the raster names
    every cell, so this decides one thing, which is whether an UNNAMED cell is ground a
    player can stand on or open water.
    """
    pts: list[tuple[float, float]] = []
    counts: dict[str, int] = {}
    nodes = load("world_resource_nodes.json", "nodes")
    pts.extend((node["x"], node["y"]) for node in nodes)
    counts["resource_nodes"] = len(nodes)
    for row in load("world_collectibles.json", "collectibles"):
        category = row.get("category")
        if category in LAND_MASK_CATEGORIES:
            pts.append((row["x"], row["y"]))
            counts[category] = counts.get(category, 0) + 1
    for category in LAND_MASK_CATEGORIES:
        if category not in counts:
            raise SystemExit(f"world_collectibles.json: no {category} rows -- schema changed?")
    return np.array(pts, np.float64), dict(sorted(counts.items()))


def texel_bounds(lo: float, hi: float, span: tuple[float, float], width: int) -> tuple[int, int]:
    """The half-open texel range a world-space interval covers, clamped to the raster."""
    start, end = span
    first = max(0, min(width, round((lo - start) / (end - start) * width)))
    last = max(0, min(width, round((hi - start) / (end - start) * width)))
    return first, last


def rasterise(
    area: np.ndarray, names: list[str | None], cell: float, nx: int, ny: int
) -> tuple[list[list[str | None]], list[list[bool]]]:
    """Majority display name per grid cell, and whether that cell is one area throughout.

    Two answers from one pass because they are one fact about the cell: the name is what most
    of it is, and the purity flag is whether "most" was "all". Cells whose box falls outside
    the raster come back as ``None`` -- the grid is 7,680 m square and the map is 7,500 m, so
    a margin of it is off the texture entirely.
    """
    width = area.shape[0]
    x_span = (BOUNDS_M["x_min_m"] * 100, BOUNDS_M["x_max_m"] * 100)
    y_span = (BOUNDS_M["y_min_m"] * 100, BOUNDS_M["y_max_m"] * 100)
    grid: list[list[str | None]] = []
    pure: list[list[bool]] = []
    for j in range(ny):
        v0, v1 = texel_bounds(GRID_Y0 + j * cell, GRID_Y0 + (j + 1) * cell, y_span, width)
        row: list[str | None] = []
        row_pure: list[bool] = []
        band = area[v0:v1] if v1 > v0 else None
        for i in range(nx):
            u0, u1 = texel_bounds(GRID_X0 + i * cell, GRID_X0 + (i + 1) * cell, x_span, width)
            if band is None or u1 <= u0:
                row.append(None)
                row_pure.append(False)
                continue
            values, counts = np.unique(band[:, u0:u1], return_counts=True)
            labels = {names[int(v)] for v in values}
            row.append(names[int(values[counts.argmax()])])
            row_pure.append(len(labels) == 1)
        grid.append(row)
        pure.append(row_pure)
    return grid, pure


def nearest_distance_m(points: np.ndarray, x: float, y: float) -> float:
    return float(np.sqrt(((points[:, 0] - x) ** 2 + (points[:, 1] - y) ** 2).min())) / 100.0


def classify(
    grid: list[list[str | None]],
    pure: list[list[bool]],
    points: np.ndarray,
    cell: float,
    inherit: tuple[set[tuple[int, int]], int] | None,
) -> tuple[set[tuple[int, int]], list[str], dict[str, int]]:
    """Void set, confidence letters and cell counts for one grid, in place.

    Mutates ``grid``: a cell the game leaves unnamed and that the mask calls land is written
    back as the No Man's Land label, so the grid that comes out has no ``None`` in it that is
    not also void. Four outcomes and each is a different KIND of answer -- see
    :data:`CONFIDENCE_LEGEND` -- rather than four grades of one.

    ``inherit`` is how the fine grid gets the coarse grid's sea: ``(void cells, ratio)``, and
    a fine cell is void exactly when the coarse cell over it is. Without it the mask is asked
    at this grid's own centres.
    """
    void: set[tuple[int, int]] = set()
    rows: list[str] = []
    counts = {"interior": 0, "boundary": 0, "unnamed": 0, "void": 0}
    for j, line in enumerate(grid):
        row = ""
        for i, name in enumerate(line):
            if name is not None and name != UNNAMED_LABEL:
                key = "interior" if pure[j][i] else "boundary"
                counts[key] += 1
                row += "l" if pure[j][i] else "b"
                continue
            if inherit is not None:
                coarse_void, ratio = inherit
                is_void = (i // ratio, j // ratio) in coarse_void
            else:
                cx, cy = GRID_X0 + (i + 0.5) * cell, GRID_Y0 + (j + 0.5) * cell
                is_void = nearest_distance_m(points, cx, cy) > VOID_DISTANCE_M
            if is_void:
                void.add((i, j))
                counts["void"] += 1
                row += VOID
            else:
                grid[j][i] = UNNAMED_LABEL
                counts["unnamed"] += 1
                row += "u"
        rows.append(row)
    return void, rows, counts


def letters_for(names: list[str]) -> dict[str, str]:
    """One character per region, assigned in name order.

    Alphabetical rather than by area or by size, because the letters are what the frontend's
    colour table is keyed by and a stable, obvious rule is what lets a person check that
    table by eye. 26 letters against 19 regions; the run stops rather than wrapping.
    """
    if len(names) > 26:
        raise SystemExit(
            f"{len(names)} regions and 26 letters. The grid is a character raster and the "
            "legend has run out of alphabet; the encoding has to change before this can."
        )
    return {name: chr(ord("A") + i) for i, name in enumerate(sorted(names))}


def encode(
    grid: list[list[str | None]], letters: dict[str, str], void: set[tuple[int, int]]
) -> list[str]:
    return [
        "".join(
            VOID if (i, j) in void or name is None else letters[name] for i, name in enumerate(row)
        )
        for j, row in enumerate(grid)
    ]


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "-o", "--out", type=Path, default=DEST, help=f"destination (default {DEST})"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite a table this run cannot show was cut from the installed build",
    )
    args = parser.parse_args()

    versions = require_gen("ooz", "texture2ddecoder", "PIL.Image")
    import texture2ddecoder as decoder
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None

    try:
        build_pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game if the install is somewhere else.")
        return 1
    exe_build = installed_build_from_exe(args.game)
    print(f"installed build: {build_pin}")

    dest: Path = args.out
    if dest.is_file() and not args.force:
        try:
            existing = json.loads(dest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        pinned = read_path(existing, ("_meta", "game_version_pinned"))
        if pinned is not None and pinned != build_pin:
            print(
                f"{dest.relative_to(ROOT)} was cut from another build, and a region table from "
                "another build describes another world's coastline.\n"
                f"  installed:   {build_pin}\n"
                f"  that table:  {pinned}\n"
                "Drift is announced rather than overwritten. Pass --force to replace it."
            )
            return 3

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the game's own assets from {paks} with pyooz {versions['pyooz']}")
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    try:
        areas = read_map_areas(store, scripts)
    except MapAreaError as exc:
        print(f"{exc}\nThere is no other source for where a region is, so nothing was written.")
        return 4

    raster = np.frombuffer(areas.texels, np.uint8).reshape(areas.width, areas.width)
    unknown = sorted(
        {a.key or f"<{a.asset} states no display name>" for a in areas.areas if a is not None}
        - set(DISPLAY_NAMES)
    )
    if unknown:
        print(
            "the game names a region this file has no label for: "
            + ", ".join(unknown)
            + "\nA label cannot be invented -- add it to DISPLAY_NAMES with the name the game "
            "shows for it. Nothing was written."
        )
        return 5
    names: list[str | None] = [
        None if area is None else DISPLAY_NAMES[area.key] for area in areas.areas
    ]
    print(
        f"  {areas.width}x{areas.width} palette indices, {len(areas.areas)} entries, "
        f"{len(areas.assets)} area assets, {len({n for n in names if n})} named regions"
    )

    # ---- the pin, re-measured rather than inherited ----------------------------------
    biome = {"width": areas.width, "area": raster}
    artwork = read_artwork_sheet(store, decoder, Image)
    calibration = calibrate_biome(biome, artwork, Image)
    print(
        f"  calibration: edge ratio {calibration['edge_ratio_at_the_pin']} at the pin against "
        f"{calibration['edge_ratio_at_the_best_rival_shift']} for the best rival shift "
        f"-- margin {calibration['margin_over_the_best_rival']}x"
    )
    if not calibration["pin_holds"]:
        print(
            "  the raster no longer sits on the map square by the required margin, so every "
            "cell below would be named from a picture pinned to the wrong place. Nothing "
            "was written; look at the asset."
        )
        return 6

    # ---- the two grids, each with a confidence of its own shape ----------------------
    coarse, coarse_pure = rasterise(raster, names, GRID_CELL, GRID_NX, GRID_NY)
    fine_nx = int(GRID_NX * GRID_CELL / FINE_CELL)
    fine_ny = int(GRID_NY * GRID_CELL / FINE_CELL)
    fine, fine_pure = rasterise(raster, names, FINE_CELL, fine_nx, fine_ny)

    points, mask_counts = reference_points()
    print(f"land mask from {len(points)} static world objects (coast or ocean, nothing else)")

    # The mask is asked once, at 256 m, and the fine grid inherits the answer. Asking it
    # twice at two resolutions is how one grid comes to think the sea starts somewhere the
    # other does not, and a coastline that moves between two views of one file is worse than
    # either version of it.
    void_coarse, coarse_conf, counts = classify(coarse, coarse_pure, points, GRID_CELL, None)
    per_fine = int(GRID_CELL / FINE_CELL)
    void_fine, fine_conf, fine_counts = classify(
        fine, fine_pure, points, FINE_CELL, (void_coarse, per_fine)
    )
    print(
        f"cells at {GRID_CELL / 100:.0f} m: {counts['interior']} interior, "
        f"{counts['boundary']} boundary, {counts['unnamed']} unnamed, {counts['void']} void"
    )
    print(
        f"cells at {FINE_CELL / 100:.0f} m: {fine_counts['interior']} interior, "
        f"{fine_counts['boundary']} boundary, {fine_counts['unnamed']} unnamed, "
        f"{fine_counts['void']} void"
    )

    present = sorted(
        {name for row in coarse for name in row if name} | {n for r in fine for n in r if n}
    )
    letters = letters_for(present)
    region_rows = encode(coarse, letters, void_coarse)
    fine_rows = encode(fine, letters, void_fine)

    # ---- per-region extents, from the published grid so containment holds -------------
    regions: dict[str, dict] = {}
    for name, letter in letters.items():
        cells = [
            (i, j) for j in range(GRID_NY) for i in range(GRID_NX) if region_rows[j][i] == letter
        ]
        if not cells:
            continue
        xs = [GRID_X0 + i * GRID_CELL for i, _ in cells] + [
            GRID_X0 + (i + 1) * GRID_CELL for i, _ in cells
        ]
        ys = [GRID_Y0 + j * GRID_CELL for _, j in cells] + [
            GRID_Y0 + (j + 1) * GRID_CELL for _, j in cells
        ]
        cx = sum(GRID_X0 + (i + 0.5) * GRID_CELL for i, _ in cells) / len(cells)
        cy = sum(GRID_Y0 + (j + 0.5) * GRID_CELL for _, j in cells) / len(cells)
        regions[name] = {
            "letter": letter,
            "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "centroid": [int(cx), int(cy)],
            "cells": len(cells),
            "area_km2": round(len(cells) * (GRID_CELL / 100_000) ** 2, 2),
            "grid_cells": sorted(
                {
                    geo.grid_cell(GRID_X0 + (i + 0.5) * GRID_CELL, GRID_Y0 + (j + 0.5) * GRID_CELL)
                    for i, j in cells
                }
            ),
            "areas": sorted(
                {
                    area.asset
                    for index, area in enumerate(areas.areas)
                    if area is not None and names[index] == name
                }
            ),
        }

    name_map = {
        area.asset: {
            "package": area.stem,
            "display_name_key": area.key,
            "string_table": area.string_table,
            "display_name": DISPLAY_NAMES[area.key],
            "palette_indices": [i for i, a in enumerate(areas.areas) if a == area],
            "texels": sum(areas.texels.count(i) for i, a in enumerate(areas.areas) if a == area),
        }
        for area in sorted({a for a in areas.areas if a is not None}, key=lambda a: a.asset)
    }
    unreferenced = sorted(_unreferenced_assets(store, areas))

    out = {
        "_meta": _meta(
            build_pin=build_pin,
            exe_build=exe_build,
            areas=areas,
            calibration=calibration,
            counts=counts,
            fine_counts=fine_counts,
            mask_counts=mask_counts,
            mask_points=len(points),
            name_map=name_map,
            unreferenced=unreferenced,
            fine=(fine_nx, fine_ny),
            versions=versions,
        ),
        "grid_meta": {
            "x0": GRID_X0,
            "y0": GRID_Y0,
            "cell": GRID_CELL,
            "nx": GRID_NX,
            "ny": GRID_NY,
            "void": VOID,
            "fine_cell": FINE_CELL,
            "fine_nx": fine_nx,
            "fine_ny": fine_ny,
        },
        "legend": {letter: name for name, letter in sorted(letters.items(), key=lambda kv: kv[1])},
        # Four grids in two PAIRS, and the pairing is the whole readability of this file:
        # region_grid/confidence_grid are 30x30 at 256 m, fine_grid/fine_confidence are
        # 120x120 at 64 m, and each confidence is indexed exactly like the grid it is named
        # after. One confidence shared between two shapes would be a trap that reads fine.
        "region_grid": region_rows,
        "confidence_grid": coarse_conf,
        "fine_grid": fine_rows,
        "fine_confidence": fine_conf,
        "regions": dict(sorted(regions.items())),
    }

    # The trailing newline is not cosmetic: without it this generator rewrote the committed
    # blob one byte SHORTER than the blob it was meant to reproduce, so "run the generator and
    # check the diff is empty" reported a change on every run and therefore said nothing on
    # any of them. ``write_text`` keeps translating it to the platform's line ending, which is
    # what the committed file already carries.
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}  {dest.stat().st_size} B  {len(regions)} regions")
    return 0


def _unreferenced_assets(store, areas) -> set[str]:
    """Area assets that exist beside the texture and that no palette index reaches.

    Recorded because one of them is a NAME a reader will look for and not find:
    ``Area_EasternDuneForest_1`` states the display name Eastern Dune Forest, the retired trace
    carried that name, and the raster puts no ground under it. "The game has the name and no
    geometry for it" is a different answer from "the name is gone", and only this says which.
    """
    from satisfactory_mcp.core.gameassets.maparea import MAP_AREA_DIR

    on_disk = {
        path.rsplit("/", 1)[-1].removesuffix(".uasset")
        for path in store.by_path
        if path.startswith(MAP_AREA_DIR) and "/Area_" in path
    }
    return on_disk - {a.asset for a in areas.areas if a is not None}


def _meta(**kw) -> dict:
    areas = kw["areas"]
    fine_nx, fine_ny = kw["fine"]
    return {
        "purpose": (
            "ADVISORY region names only. Layer 1 (grid cells, cones, radii, clustering) is "
            "exact and is what every calculation uses. A name from this file must never feed "
            "a computation."
        ),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "generator": "tools/gen_region_names.py",
        "game_version_pinned": kw["build_pin"],
        "game_build": kw["exe_build"],
        "units": "centimetres; north is -Y, east is +X, up is +Z",
        "accuracy_m": int(FINE_CELL / 100),
        "source": {
            "asset": "/Game/" + MAP_AREA_PATH.split("/FactoryGame/Content/")[1].rsplit(".", 1)[0],
            "class": MAP_AREA_CLASS,
            "derivation": (
                f"mAreaData, {areas.width}x{areas.width} palette indices at "
                f"{round((BOUNDS_M['x_max_m'] - BOUNDS_M['x_min_m']) / areas.width, 4)} m to "
                "the texel, row 0 north; mColorToArea resolves each index to one UFGMapArea "
                "asset by PublicExportHash, and that asset's mDisplayName states the game's "
                "own name for the place"
            ),
            "read_by": "core.gameassets.maparea, shared with tools/gen_map_renders.py",
            "frame_m": dict(BOUNDS_M),
            "shipped_palette_rgba": [list(entry) for entry in areas.palette],
            "shipped_palette_role": (
                "the game's own minimap legend -- flat primaries, cyan, magenta, white. "
                "Decoded for the record and never drawn or shipped; this file emits no "
                "colour at all."
            ),
            "pyooz_version": kw["versions"]["pyooz"],
        },
        "licence": (
            "First-party. Every value here is derived from Coffee Stain's cooked assets, read "
            "out of the reader's own installed copy of the game: palette indices, area "
            "identifiers and localisation keys. The same posture as every other derived table "
            "in data/ -- facts and coordinates, no artwork. No copyleft and no share-alike "
            "obligation reaches this file; the CC BY-SA wiki trace it used to rasterise is "
            "retired, and data/satisfactory_regions.json is deleted."
        ),
        "calibration": kw["calibration"],
        "area_display_names": {
            asset: entry["display_name"] for asset, entry in kw["name_map"].items()
        },
        "name_map": kw["name_map"],
        "naming_rules": {
            "rule": (
                "the display name is the game's own, taken from each Area_* asset's "
                "mDisplayName localisation key. Geometry is game-granular: where two assets "
                "carry one key they are one region, and where two assets with the same "
                "package name carry different keys they are two."
            ),
            "one_key_two_assets": (
                "Area_Savanna_1 and Area_Savanna_2 both state Locations/RockyDesert, so the "
                "game has a Savanna asset and no Savanna region. Same for the pairs behind "
                "Dune Desert, Grass Fields, Lake Forest, Maze Canyons, Northern Forest, "
                "Rocky Desert, Southern Forest, Swamp, Titan Forest and Western Dune Forest."
            ),
            "one_asset_name_two_keys": (
                "Area_crater_1 is Blue Crater and Area_crater_2 is Crater Lakes; "
                "Area_RedJungle_1 is Red Jungle and Area_RedJungle_2 is Jungle Spires. "
                "Resolving by package name would answer one of each pair at random."
            ),
            "no_mans_land": (
                "Area_NoMansLand is 43% of the raster and is the ocean and the outer coast. "
                "It is labelled No Man's Land -- the game's own display name for it -- rather "
                "than blanked, because a player standing on the outer coast is standing "
                "somewhere and the game has a name for it. A cell is void instead only when "
                f"the game names no region AND no known static object is within "
                f"{VOID_DISTANCE_M:.0f} m, which is ocean and off-map."
            ),
            "spellings": {
                key: {"key": key, "emitted": label, "why": "the plural every player uses"}
                for key, label in PLURALISED.items()
            },
            "names_with_no_ground": (
                "Area assets that exist beside the texture and that no palette index reaches. "
                "Eastern Dune Forest is a name the game has and puts nowhere."
            ),
            "unreferenced_area_assets": kw["unreferenced"],
        },
        "grids": {
            "published": (
                f"region_grid and confidence_grid are {GRID_NX}x{GRID_NY} at "
                f"{GRID_CELL / 100:.0f} m -- what /api/regions serves and what the map paints."
            ),
            "fine": (
                f"fine_grid and fine_confidence are {fine_nx}x{fine_ny} at "
                f"{FINE_CELL / 100:.0f} m and are what domain.spatial.regions looks names up "
                "in. Not served: the payload contract is the coarse pair and nothing else."
            ),
            "pairing": (
                "each confidence grid is indexed exactly like the grid it is named after. "
                "confidence_grid goes with region_grid at 256 m; fine_confidence goes with "
                "fine_grid at 64 m. They are different shapes and swapping them reads fine "
                "and answers wrong."
            ),
            "why_64_m": (
                "measured on 5,054 known static world objects, looked up in a majority "
                "downsample against the raster itself: 256 m mislabels 714 (14.13%), 128 m "
                "421 (8.33%), 64 m 268 (5.30%), 32 m 132 (2.61%). 64 m costs 14,400 "
                "characters; 32 m costs 57,600 for twice the accuracy and was refused."
            ),
            "not_exact": (
                "a majority downsample of an exact boundary is still a downsample. The "
                "boundaries in the source are exact; these two grids are not, and the "
                "confidence letter says which cells that bites in."
            ),
        },
        "confidence_legend": CONFIDENCE_LEGEND,
        "cell_counts": {
            f"{GRID_CELL / 100:.0f}m": kw["counts"],
            f"{FINE_CELL / 100:.0f}m": kw["fine_counts"],
        },
        "void_distance_m": VOID_DISTANCE_M,
        "land_mask": {
            "role": (
                "positions only, and one decision only: whether a cell the GAME leaves "
                "unnamed is coast or open water. It supplies no name, and no calculation "
                "reads it."
            ),
            "sources": {
                "data/world_resource_nodes.json": (
                    "first-party: the game's own Persistent_Level.umap node actors, read "
                    "from the installed game by tools/gen_world_resource_nodes.py"
                ),
                "data/world_collectibles.json": (
                    "the game's own cooked map packages, read from the installed game"
                ),
            },
            "reference_points": kw["mask_points"],
            "categories": kw["mask_counts"],
        },
        "node_region_overrides": (
            "gone, key and all. It held 48 oil nodes whose region had been read off a wiki "
            "image by eye and was trusted over the raster; the raster is the game's own "
            "geometry now, so there is nothing for a hand correction to correct. Keying one "
            "by instance name would be the wrong repair in any case -- a map update renames "
            "instances, which is what the node skew gate exists for. A node is labelled by "
            "where it stands, like every other coordinate."
        ),
        "retired_wiki_trace": RETIRED_TRACE_COMPARISON,
        "known_limitations": [
            (
                "The published grid is 256 m and the lookup grid is 64 m, so a name near a "
                "boundary can be one region out. The SOURCE boundaries are exact -- this is "
                "the cost of publishing a small table, not of not knowing."
            ),
            (
                "No Man's Land is a label, not a region with edges anybody drew: it is "
                "everything the game does not name, from the outer coast to open ocean, and "
                "the land mask is what separates the two here."
            ),
            (
                "The corners are MEASURED, not stated in the asset. Nothing in the texture "
                "says where its 4096 texels go; calibration above re-measures it every run "
                "and this file refuses to write if the pin stops holding."
            ),
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
