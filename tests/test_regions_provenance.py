"""``data/satisfactory_regions.json`` says where its numbers came from. This checks it.

That file is source material for ``tools/gen_region_names.py`` and nothing else opens it, so
no runtime behaviour depends on it -- which is exactly why a wrong provenance line could sit
in it unnoticed. It used to credit ``GreyHak/sat_sav_parse sav_data/resourcePurity.py``, a
GPL-3.0 table that has since been deleted from this repository, for its node coordinates.

Two things are pinned here.

**No copyleft credit comes back.** A string scan, because the claim being protected is a
licensing claim about the whole file and not about one field.

**The node data is reproducible from the committed MIT table.** Every coordinate, purity and
node kind in ``oil_clusters`` is checked against ``data/world_resource_nodes.mit.json``, which
is MIT-licensed and derived from the game's own packaged map. That makes the provenance
falsifiable with nothing but the repository: if someone edits a purity or a position here, the
independent table disagrees and this fails.

The authoritative source is the game's assets, read out of ``Persistent_Level.umap``; the MIT
table is the copy of that data which can be committed. Measured 2026-07-30 against both:

* purity and node kind agree for all 48 members in both tables;
* every shipped coordinate is the componentwise integer rounding of its asset position, 48/48,
  and of its position in the reference save, 48/48 -- assets and save agree to a median of
  0.0001 cm;
* the MIT table is pinned to an older build and is 1 cm out on one node's Y
  (``BP_ResourceNode15``), which is why ``POSITION_TOLERANCE_CM`` below is not zero. It is the
  only member that is not exact.

The aggregates are checked as arithmetic on the members rather than against remembered
numbers, so a member edit cannot leave a stale total behind.
"""

from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[1] / "data"
REGIONS = json.loads((DATA / "satisfactory_regions.json").read_text(encoding="utf-8"))
MIT = json.loads((DATA / "world_resource_nodes.mit.json").read_text(encoding="utf-8"))["nodes"]
MIT_BY_ID = {n["id"]: n for n in MIT}

#: The MIT table is a stale-build copy of the same asset data, exact on 47 of the 48 members
#: and 1 cm out on one. Not a fitted tolerance: the deviation is a single integer centimetre
#: on one Y, and the assets and the save both round to the shipped value exactly.
POSITION_TOLERANCE_CM = 2.0

#: ``nodeType`` in the MIT table against ``type`` in the regions file.
KIND = {"node": "node", "frackingSatellite": "well_sat"}

#: Anything naming the deleted GPL-3.0 parser or its tables. ``sav_data`` is included because
#: the tables were the borrowing; the parser itself was only ever a tool.
FORBIDDEN = ("greyhak", "sat_sav_parse", "resourcepurity", "sav_data", "scim")

MEMBERS = [m for c in REGIONS["oil_clusters"] for m in c["members"]]


def rate(kind: str, purity: str) -> int:
    """The m3/min a member yields at 100%, out of the file's own rate table."""
    rates = REGIONS["extraction_rates_m3min_at_100pct"]
    key = (
        "oil_extractor(Build_OilPump_C)"
        if kind == "node"
        else "resource_well_extractor(Build_FrackingExtractor_C)"
    )
    return rates[key][purity]


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in _strings(k) + _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def test_the_deleted_gpl_table_is_named_only_as_history() -> None:
    """Two rules, because the history is worth keeping and the credit is not.

    Nothing outside ``_meta`` may name that table at all -- a credit could be reintroduced on
    any member or in any note. Inside ``_meta`` it may be named, but only in a sentence that
    also says it is deleted, so the mention cannot quietly turn back into a live source.
    """
    outside = [
        s
        for key, block in REGIONS.items()
        if key != "_meta"
        for s in _strings(block)
        if any(word in s.casefold() for word in FORBIDDEN)
    ]
    assert outside == []
    for s in _strings(REGIONS["_meta"]):
        if any(word in s.casefold() for word in FORBIDDEN):
            assert "deleted" in s.casefold(), s
    # And the removal is on the record rather than quietly tidied away.
    assert "gpl-3.0" in " ".join(_strings(REGIONS["_meta"])).casefold()


def test_the_members_are_exactly_the_oil_extractables_in_the_mit_table() -> None:
    """Completeness, not just agreement: a missing node would silently understate the map."""
    oil = {n["id"] for n in MIT if n["resource"] == "Desc_LiquidOil_C" and n["nodeType"] in KIND}
    assert {m["inst"] for m in MEMBERS} == oil
    assert len(MEMBERS) == 48


@pytest.mark.parametrize("member", MEMBERS, ids=lambda m: m["inst"])
def test_member_purity_kind_position_and_rate(member: dict) -> None:
    row = MIT_BY_ID[member["inst"]]
    assert KIND[row["nodeType"]] == member["type"]
    assert row["purity"].upper() == member["purity"]
    assert row["resource"] == "Desc_LiquidOil_C"
    delta = math.dist(member["pos"], (row["x"], row["y"], row["z"]))
    assert delta <= POSITION_TOLERANCE_CM, f"{member['inst']} is {delta:.2f} cm from the MIT row"
    assert member["m3min_100"] == rate(member["type"], member["purity"])


@pytest.mark.parametrize("cluster", REGIONS["oil_clusters"], ids=lambda c: c["id"])
def test_cluster_aggregates_are_arithmetic_on_its_members(cluster: dict) -> None:
    members = cluster["members"]
    nodes = [m for m in members if m["type"] == "node"]
    sats = [m for m in members if m["type"] == "well_sat"]
    assert cluster["n"] == len(members)
    assert cluster["n_nodes"] == len(nodes)
    assert cluster["n_well_satellites"] == len(sats)
    for key, group in (("purity_nodes", nodes), ("purity_well", sats)):
        counts: dict[str, int] = {}
        for m in group:
            counts[m["purity"]] = counts.get(m["purity"], 0) + 1
        assert cluster[key] == counts
    assert cluster["m3min_100"] == sum(m["m3min_100"] for m in members)
    assert cluster["m3min_250"] == cluster["m3min_100"] * 2.5
    for axis in range(3):
        mean = sum(m["pos"][axis] for m in members) / len(members)
        assert abs(cluster["centroid"][axis] - mean) <= 1
    spread = max(
        (math.dist(a["pos"][:2], b["pos"][:2]) / 100.0 for a, b in combinations(members, 2)),
        default=0.0,
    )
    assert abs(cluster["spread_m"] - spread) <= 1


def test_totals_are_arithmetic_on_the_clusters() -> None:
    totals = REGIONS["totals"]
    nodes = [m for m in MEMBERS if m["type"] == "node"]
    sats = [m for m in MEMBERS if m["type"] == "well_sat"]
    assert totals["oil_nodes"] == len(nodes)
    assert totals["oil_well_satellites"] == len(sats)
    for key, group in (("purity_nodes", nodes), ("purity_well_sats", sats)):
        counts: dict[str, int] = {}
        for m in group:
            counts[m["purity"]] = counts.get(m["purity"], 0) + 1
        assert totals[key] == counts
    assert totals["nodes_m3min_100"] == sum(m["m3min_100"] for m in nodes)
    assert totals["wells_m3min_100"] == sum(m["m3min_100"] for m in sats)
    assert totals["map_total_m3min_100"] == totals["nodes_m3min_100"] + totals["wells_m3min_100"]
    # Three wells, one per distinct fracking core; the members carry no core, so this is the
    # cluster count of well-satellite groups rather than a remembered 3.
    assert totals["oil_wells"] == sum(
        1 for c in REGIONS["oil_clusters"] if c["n_well_satellites"] > 0
    )


def test_content_bbox_is_the_extent_of_the_committed_object_tables() -> None:
    """Reproduces all six numbers from the two non-copyleft tables the mask uses.

    ``world_collectibles.json`` supplies four of the six extremes, so without it the bbox can
    only be bounded, not reproduced. It is read out of the installed game by
    ``tools/gen_world_collectibles.py``.
    """
    bbox = REGIONS["frame"]["content_bbox"]
    pts = [(n["x"], n["y"], n["z"]) for n in MIT]
    for axis, key in enumerate("xyz"):
        lo, hi = bbox[key]
        assert lo <= min(p[axis] for p in pts) and hi >= max(p[axis] for p in pts)
    assert REGIONS["frame"]["midline_y"] == sum(bbox["y"]) / 2

    collectibles = DATA / "world_collectibles.json"
    if not collectibles.is_file():
        pytest.skip("needs data/world_collectibles.json (run tools/gen_world_collectibles.py)")
    categories = {
        "crashed_drop_pod",
        "mercer_shrine",
        "mercer_sphere",
        "power_slug_blue",
        "power_slug_purple",
        "power_slug_yellow",
        "somersloop",
    }
    rows = json.loads(collectibles.read_text(encoding="utf-8"))["collectibles"]
    pts += [(r["x"], r["y"], r["z"]) for r in rows if r.get("category") in categories]
    assert len(pts) == 2688
    # Collectible positions are floats and the bbox is integers. ``round`` reproduces all six,
    # including a y-minimum that lands on an exact half (-314104.5 -> -314104), i.e. the author
    # used Python's round-half-to-even. Recorded rather than smoothed over with a tolerance.
    for axis, key in enumerate("xyz"):
        assert bbox[key] == [
            round(min(p[axis] for p in pts)),
            round(max(p[axis] for p in pts)),
        ]


def test_the_transform_is_anchored_on_the_printed_grid_not_on_a_node() -> None:
    """Two grid lines fix it, and a coordinate re-derivation cannot move what it produced.

    The scale is stated as the printed 1.024 km spacing, 684.5 px. Running the shipped offsets
    backwards puts the X0 west edge at px 95.011 and the Y0 south edge at px 4550.488 -- within
    0.012 px of the half-pixel values you get by reading grid lines off the image, which a fit
    to node icons would have no reason to land on.

    Its one product is each cluster's region, read off the image by eye. Re-deriving the
    coordinates from the game's assets moves a member by at most 0.71 cm; the closest cluster
    centroid is 158 m from the nearest raster cell of a different region.
    """
    cm_per_px = 102_400 / 684.5
    frame = REGIONS["frame"]
    x0, y0_south = frame["grid_X0_west_edge"], frame["grid_Y0_south_edge"]
    assert round(95.0 - x0 / cm_per_px, 1) == 2231.4
    assert round(4550.5 - y0_south / cm_per_px, 1) == 2526.4

    grid = REGIONS["region_grid"]
    gm = REGIONS["grid_meta"]
    cell, nx, ny = gm["cell"], gm["nx"], gm["ny"]
    centres = [
        (gm["x0"] + (i + 0.5) * cell, gm["y0"] + (j + 0.5) * cell, grid[j][i])
        for j in range(ny)
        for i in range(nx)
    ]
    margins = []
    for cluster in REGIONS["oil_clusters"]:
        cx, cy = cluster["centroid"][0], cluster["centroid"][1]
        i, j = int((cx - gm["x0"]) // cell), int((cy - gm["y0"]) // cell)
        here = grid[j][i]
        margins.append(
            min(math.dist((cx, cy), (px, py)) / 100.0 for px, py, ch in centres if ch != here)
        )
    # Measured 158 m; the bound is loose because the point is the order of magnitude against
    # 0.0071 m, not the exact figure.
    assert min(margins) > 100.0
