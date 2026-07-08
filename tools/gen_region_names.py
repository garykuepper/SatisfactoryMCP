"""Generate data/region_names.json -- the ADVISORY region-name layer.

    uv run python tools/gen_region_names.py

This is layer 2 of the spatial design. Layer 1 (grid cells, cones, radii, clustering)
is exact and derived, and every calculation uses it. This layer only puts human names
on coordinates, so it is explicitly approximate and carries a confidence per lookup.
A name must never feed a computation.

It takes data/satisfactory_regions.json as source material and fixes the four defects
that made that file unusable as-is:

1. **No void class.** All 900 raster cells carried a land label, so a lookup for open
   ocean confidently returned "Rocky Desert". Fixed by building a land mask from
   2,688 known static world objects (resource nodes, crashed drop pods, power slugs,
   somersloops, Mercer shrines/spheres) and blanking cells that are far from all of
   them.
2. **Raster spilling outside its own bboxes.** The shipped bboxes disagreed with the
   raster for 11 of 21 regions, so a bbox-AND-raster test returned False for points
   the raster itself assigned. Fixed by recomputing every bbox FROM the raster, so
   containment holds by construction.
3. **Boundary mislabels presented as certain.** Fixed by emitting a per-cell
   confidence derived from neighbour agreement, plus authoritative per-node overrides
   taken from the hand-verified oil clusters.
4. **A second implementation that contradicted the prose.** data/geo_reference.py is
   deleted; src/satisfactory_mcp/spatial/regions.py is the only implementation.

The land mask is drawn from two committed, non-copyleft tables: `world_resource_nodes.mit.json`
(MIT, extracted from the game's map assets) and `world_collectibles.json` (read out of the
installed game's own cooked map packages). Neither carries a copyleft licence, and no
third-party world table is imported here.

The mask is heavily over-determined at 256 m, which is why swapping its sources changed no
cell. Measured: the 2,062 collectibles alone reproduce the whole 900-cell raster, and so does
dropping any one resource class. The 626 resource nodes *alone* do not -- they move 27 cells --
so both tables stay, and the node table is the cheap redundancy that keeps the raster stable if
a collectibles rebuild changes shape.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

VOID = "."

#: Collectible categories that stand on land and so mark it. Mercer shrines and spheres,
#: all three slug colours, somersloops and crashed drop pods, matching the object set the
#: mask has always used. Somersloop shrines, tape and customization pickups are excluded
#: for that reason alone -- including them moves no cell (measured), because they sit
#: beside objects already in the set.
LAND_MASK_CATEGORIES = (
    "crashed_drop_pod",
    "mercer_shrine",
    "mercer_sphere",
    "power_slug_blue",
    "power_slug_purple",
    "power_slug_yellow",
    "somersloop",
)

#: A cell whose centre is farther than this from any known static object is treated
#: as void (ocean or off-map). Chosen from the measured distribution: cell-to-nearest
#: distances are strongly bimodal, median 123 m on land vs p90 1212 m, and 1000 m
#: blanks 14.7% of the raster -- close to the 26% of the raster that lies outside the
#: content bbox, while staying conservative about inland lakes and plateaus.
VOID_DISTANCE_M = 1000.0

#: Between these two a label is emitted but flagged low-confidence.
UNCERTAIN_DISTANCE_M = 400.0


def load(name: str, key: str) -> list[dict]:
    path = ROOT / "data" / name
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} missing -- the land mask needs it")
    rows = json.loads(path.read_text(encoding="utf-8"))[key]
    missing = [r for r in rows if "x" not in r or "y" not in r]
    if missing:
        raise SystemExit(f"{name}: {len(missing)} of {len(rows)} {key} carry no x/y")
    return rows


def reference_points() -> tuple[list[tuple[float, float]], dict[str, int]]:
    """Static world objects, used purely as a land mask.

    Positions only. Nothing else about these objects reaches the output: node purity is
    not consulted, and neither is a collectible's collected/present state, which is a
    fact about one save rather than about the world the mask describes.
    """
    pts: list[tuple[float, float]] = []
    counts: dict[str, int] = {}
    nodes = load("world_resource_nodes.mit.json", "nodes")
    for node in nodes:
        pts.append((node["x"], node["y"]))
    counts["resource_nodes"] = len(nodes)
    for row in load("world_collectibles.json", "collectibles"):
        category = row.get("category")
        if category in LAND_MASK_CATEGORIES:
            pts.append((row["x"], row["y"]))
            counts[category] = counts.get(category, 0) + 1
    for category in LAND_MASK_CATEGORIES:
        if category not in counts:
            raise SystemExit(f"world_collectibles.json: no {category} rows -- schema changed?")
    return pts, counts


def nearest_distance_m(x: float, y: float, pts: list[tuple[float, float]]) -> float:
    best = math.inf
    for px, py in pts:
        d = (px - x) ** 2 + (py - y) ** 2
        best = min(best, d)
    return math.sqrt(best) / 100.0


def main() -> int:
    src = json.loads((ROOT / "data" / "satisfactory_regions.json").read_text(encoding="utf-8"))
    legend: dict[str, str] = src["legend"]
    grid: list[str] = src["region_grid"]
    gm = src["grid_meta"]
    x0, y0, cell, nx, ny = gm["x0"], gm["y0"], gm["cell"], gm["nx"], gm["ny"]

    pts, pt_counts = reference_points()
    print(f"land mask from {len(pts)} static world objects")
    for category, n in sorted(pt_counts.items()):
        print(f"   {category:20s} {n}")

    # ---- 1. void mask ---------------------------------------------------
    rows: list[str] = []
    confidence_rows: list[str] = []
    counts = {"land": 0, "uncertain": 0, "void": 0}
    for j in range(ny):
        row_chars: list[str] = []
        conf_chars: list[str] = []
        for i in range(nx):
            cx, cy = x0 + (i + 0.5) * cell, y0 + (j + 0.5) * cell
            d = nearest_distance_m(cx, cy, pts)
            letter = grid[j][i]
            if d > VOID_DISTANCE_M:
                row_chars.append(VOID)
                conf_chars.append(VOID)
                counts["void"] += 1
            elif d > UNCERTAIN_DISTANCE_M:
                row_chars.append(letter)
                conf_chars.append("u")
                counts["uncertain"] += 1
            else:
                row_chars.append(letter)
                conf_chars.append("l")
                counts["land"] += 1
        rows.append("".join(row_chars))
        confidence_rows.append("".join(conf_chars))
    print(f"cells: {counts['land']} land, {counts['uncertain']} uncertain, {counts['void']} void")

    # ---- 2. neighbour agreement -> per-cell confidence -------------------
    # A cell whose 8 neighbours all share its label is interior; a cell on a
    # boundary is where the +-256 m raster error actually bites.
    for j in range(ny):
        conf = list(confidence_rows[j])
        for i in range(nx):
            if rows[j][i] == VOID:
                continue
            here = rows[j][i]
            disagree = False
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    nj, ni = j + dj, i + di
                    if 0 <= nj < ny and 0 <= ni < nx:
                        other = rows[nj][ni]
                        if other != VOID and other != here:
                            disagree = True
            if disagree and conf[i] == "l":
                conf[i] = "b"  # boundary
        confidence_rows[j] = "".join(conf)
    boundary = sum(r.count("b") for r in confidence_rows)
    print(f"boundary cells (neighbour disagreement): {boundary}")

    # ---- 3. recompute bboxes FROM the raster ----------------------------
    regions: dict[str, dict] = {}
    letter_of = {name: ch for ch, name in legend.items()}
    for name, ch in letter_of.items():
        cells = [(i, j) for j in range(ny) for i in range(nx) if rows[j][i] == ch]
        if not cells:
            continue
        xs = [x0 + i * cell for i, _ in cells] + [x0 + (i + 1) * cell for i, _ in cells]
        ys = [y0 + j * cell for _, j in cells] + [y0 + (j + 1) * cell for _, j in cells]
        cx = sum(x0 + (i + 0.5) * cell for i, _ in cells) / len(cells)
        cy = sum(y0 + (j + 0.5) * cell for _, j in cells) / len(cells)
        original = src["regions"].get(name, {})
        regions[name] = {
            "letter": ch,
            # Derived from the raster, so raster-in-bbox containment always holds.
            "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "centroid": [int(cx), int(cy)],
            "cells": len(cells),
            "area_km2": round(len(cells) * (cell / 100_000) ** 2, 2),
            "grid_cells": original.get("grid_cells", []),
        }

    # ---- 4. authoritative per-node overrides ----------------------------
    # The source file states these were verified by eye against the biome map and
    # should be trusted over a raster lookup.
    overrides: dict[str, str] = {}
    for cluster in src.get("oil_clusters", ()):
        region = cluster.get("region")
        if not region:
            continue
        for member in cluster.get("members", ()):
            inst = member.get("inst")
            if inst:
                overrides[inst] = region
    print(f"authoritative node overrides: {len(overrides)}")

    # ---- validation: raster vs the hand-verified clusters ---------------
    agree = disagree = void_hit = 0
    mismatches: list[str] = []
    for cluster in src.get("oil_clusters", ()):
        region = cluster.get("region")
        cx_, cy_ = cluster["centroid"][0], cluster["centroid"][1]
        i = int((cx_ - x0) // cell)
        j = int((cy_ - y0) // cell)
        if not (0 <= i < nx and 0 <= j < ny):
            continue
        ch = rows[j][i]
        if ch == VOID:
            void_hit += 1
        elif legend.get(ch) == region:
            agree += 1
        else:
            disagree += 1
            mismatches.append(f"{cluster['id']} {region} -> raster says {legend.get(ch)}")
    total = agree + disagree + void_hit
    print(
        f"validation vs {total} hand-verified oil clusters: {agree} agree, "
        f"{disagree} disagree, {void_hit} landed on void"
    )
    for m in mismatches:
        print("   mismatch:", m)

    out = {
        "_meta": {
            "purpose": (
                "ADVISORY region names only. Layer 1 (grid cells, cones, radii, "
                "clustering) is exact and is what every calculation uses. A name from "
                "this file must never feed a computation."
            ),
            "game_version": src["_meta"].get("game_version"),
            "units": "centimetres; north is -Y, east is +X, up is +Z",
            "accuracy_m": int(cell / 100),
            "confidence_legend": {
                "l": "interior cell, all neighbours agree",
                "b": "boundary cell, a neighbour disagrees -- may be off by one region",
                "u": f"sparse cell, >{UNCERTAIN_DISTANCE_M:.0f} m from any known object",
                ".": "void: ocean or off-map, no label emitted",
            },
            "void_distance_m": VOID_DISTANCE_M,
            "uncertain_distance_m": UNCERTAIN_DISTANCE_M,
            "land_mask_reference_points": len(pts),
            "land_mask": {
                "role": (
                    "positions only, used to tell land from ocean. No name, purity or "
                    "collected-state from these tables reaches this file, and no "
                    "calculation reads the mask -- it only decides which cells go void."
                ),
                "sources": {
                    "data/world_resource_nodes.mit.json": (
                        "MIT (Copyright (c) 2024 Leonardo Ascione), extracted from "
                        "FactoryGame/Map/GameLevel01/Persistent_Level.umap"
                    ),
                    "data/world_collectibles.json": (
                        "the game's own cooked map packages, read from the installed game"
                    ),
                },
                "categories": dict(sorted(pt_counts.items())),
                "licence": (
                    "no copyleft table contributed to this file. Nothing from "
                    "sat_sav_parse/sav_data was read; that vendored GPL-3.0 parser and "
                    "its world tables are deleted."
                ),
            },
            "cell_counts": counts,
            "boundary_cells": boundary,
            "validation_vs_hand_verified_oil_clusters": {
                "agree": agree,
                "disagree": disagree,
                "on_void": void_hit,
                "mismatches": mismatches,
            },
            "known_limitations": [
                (
                    "Boundaries are accurate to about one 256 m cell; adjacent-region "
                    "confusion is expected and is what the 'b' confidence flags."
                ),
                (
                    "Region geometry is hand-derived from the wiki biome map. The game "
                    "ships no biome geometry, so this cannot be regenerated from game "
                    "data and will not track map changes."
                ),
                (
                    "Per-crash-site validation needs the wiki Region column, which is not "
                    "shipped locally; validation here uses the 14 hand-verified oil "
                    "clusters instead."
                ),
                (
                    "source_file_provenance below is quoted from "
                    "data/satisfactory_regions.json and still credits a GPL-3.0 world "
                    "table for its coordinates. It describes how that file was built, not "
                    "this one: the land mask here reads only the two sources named under "
                    "land_mask. That stale credit belongs to satisfactory_regions.json and "
                    "has to be settled there."
                ),
            ],
            "source_file": "data/satisfactory_regions.json",
            "source_file_provenance": src["_meta"].get("provenance"),
            # Carried, not restated: the region geometry this file rasterises is a trace of
            # a CC BY-SA 4.0 image, and share-alike follows the derived layer. A consumer
            # holding only this artifact must be able to see that without opening the source.
            "source_file_licences": src["_meta"].get("licences"),
        },
        "grid_meta": {"x0": x0, "y0": y0, "cell": cell, "nx": nx, "ny": ny, "void": VOID},
        "legend": legend,
        "region_grid": rows,
        "confidence_grid": confidence_rows,
        "regions": dict(sorted(regions.items())),
        "node_region_overrides": dict(sorted(overrides.items())),
    }

    dest = ROOT / "data" / "region_names.json"
    # The trailing newline is not cosmetic here: without it this generator rewrote the
    # committed blob one byte SHORTER than the blob it was meant to reproduce, so "run the
    # generator and check the diff is empty" -- the only check that says the committed
    # artifact still matches the code that makes it -- reported a change on every run and
    # therefore said nothing on any of them. `write_text` keeps translating it to the
    # platform's line ending, which is what the committed file already carries.
    dest.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {dest.relative_to(ROOT)}  {dest.stat().st_size} B  {len(regions)} regions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
