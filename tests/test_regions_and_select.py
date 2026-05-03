"""The advisory region-name layer and the source-selector DSL.

Each test here pins one of the four defects the shipped region dataset had, so a
regenerated dataset that reintroduces one fails loudly.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.spatial import geo
from satisfactory_mcp.spatial import nodes as nodes_mod
from satisfactory_mcp.spatial.regions import load_regions
from satisfactory_mcp.spatial.select import select_nodes

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rm():
    return load_regions()


@pytest.fixture(scope="module")
def table():
    return nodes_mod.load_nodes()


# ------------------------------------------------------- defect 1: void class


def test_ocean_returns_no_name(rm):
    """The original raster labelled every cell, so open ocean north-west of the map
    came back as 'Rocky Desert'."""
    label = rm.label_for(-300_000, -350_000)
    assert label.name is None
    assert label.confidence == "void"
    assert label.describe() == "off-map or ocean"


def test_off_grid_returns_no_name(rm):
    assert rm.label_for(10_000_000, 10_000_000).name is None


def test_land_is_still_labelled(rm):
    """The void mask must not eat real land."""
    label = rm.label_for(23_900, -192_800)  # a tapped northern oil field
    assert label.name == "Spire Coast"
    assert label.certain


def test_void_cells_exist_but_are_a_minority(rm):
    total = rm.nx * rm.ny
    void = sum(row.count(".") for row in rm.grid)
    assert 0 < void < total * 0.5


# ----------------------------------------------- defect 2: bbox/raster agreement


def test_every_raster_cell_lies_inside_its_regions_bbox(rm):
    """The shipped bboxes disagreed with the raster for 11 of 21 regions, so a
    bbox-AND-raster test returned False for points the raster itself assigned.
    Recomputing bboxes from the raster makes this hold by construction."""
    for name, info in rm.regions.items():
        x1, y1, x2, y2 = info["bbox"]
        letter = info["letter"]
        for j in range(rm.ny):
            for i in range(rm.nx):
                if rm.grid[j][i] != letter:
                    continue
                cx = rm.x0 + (i + 0.5) * rm.cell
                cy = rm.y0 + (j + 0.5) * rm.cell
                assert x1 <= cx <= x2 and y1 <= cy <= y2, f"{name} cell ({i},{j})"


# ------------------------------------------- defect 3: confidence and overrides


def test_boundary_cells_are_flagged_not_hidden(rm):
    codes = {c for row in rm.confidence for c in row}
    assert "b" in codes  # boundary cells are marked
    assert "l" in codes  # interior cells exist too


def test_hand_verified_override_beats_the_raster(rm, table):
    """These two nodes are really in Western Beaches; the raster puts them in Jungle
    Spires. The override table is authoritative and reports 'verified'."""
    for short in ("BP_ResourceNode86", "BP_ResourceNode88"):
        node = next(n for n in table.nodes if n["instance"].endswith("." + short))
        raster = rm.label_for(node["x"], node["y"])
        final = rm.label_for_node(node)
        assert raster.name == "Jungle Spires"
        assert final.name == "Western Beaches"
        assert final.confidence == "verified"


def test_western_beaches_finds_all_ten_oil_nodes(rm, table):
    """Raster alone found 8 of 10; the override table recovers the other two."""
    hits = rm.filter_nodes(table.by_resource("Desc_LiquidOil_C"), "Western Beaches")
    assert len(hits) == 10


def test_oil_bearing_regions(rm, table):
    oil = table.by_resource("Desc_LiquidOil_C")
    named = {rm.label_for_node(n).name for n in oil}
    named.discard(None)
    assert "Spire Coast" in named
    assert len(rm.filter_nodes(oil, "Spire Coast")) == 13


# --------------------------------------------------------- name resolution


def test_resolve_is_case_insensitive_and_accepts_prefixes(rm):
    assert rm.resolve("spire coast") == "Spire Coast"
    assert rm.resolve("Northern For") == "Northern Forest"
    assert rm.resolve("Nonexistent") is None


# ------------------------------------------------------------- selectors


def test_no_spec_means_whole_map(table):
    sel = select_nodes(None, table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == len(table.nodes)


def test_region_selector(table):
    sel = select_nodes(["region:Spire Coast", "resource:Desc_LiquidOil_C"], table.nodes)
    assert not sel.whole_map
    assert len(sel.nodes) == 13
    assert not sel.errors


def test_bare_region_name_works(table):
    sel = select_nodes(["Spire Coast"], table.nodes)
    assert len(sel.nodes) > 0
    assert not sel.errors


def test_node_id_selector_accepts_short_and_full(table):
    node = table.by_resource("Desc_LiquidOil_C")[0]
    short = node["instance"].rsplit(".", 1)[-1]
    for spec in (f"node:{short}", f"node:{node['instance']}", short):
        sel = select_nodes([spec], table.nodes)
        assert [n["instance"] for n in sel.nodes] == [node["instance"]], spec


def test_multiple_node_ids_union(table):
    oil = table.by_resource("Desc_LiquidOil_C")[:3]
    spec = [f"node:{n['instance'].rsplit('.', 1)[-1]}" for n in oil]
    sel = select_nodes(spec, table.nodes)
    assert len(sel.nodes) == 3


def test_near_selector(table):
    sel = select_nodes(["near:0,0,1000"], table.nodes)
    for n in sel.nodes:
        assert geo.distance_m((n["x"], n["y"]), (0.0, 0.0)) <= 1000


def test_bbox_selector(table):
    sel = select_nodes(["bbox:-100,-100,100,100"], table.nodes)
    for n in sel.nodes:
        assert -10_000 <= n["x"] <= 10_000
        assert -10_000 <= n["y"] <= 10_000


def test_grid_selector(table):
    sel = select_nodes(["grid:X3Y4"], table.nodes)
    assert sel.nodes
    for n in sel.nodes:
        assert geo.grid_cell(n["x"], n["y"]) == "X3Y4"


def test_direction_selector(table):
    sel = select_nodes(["north"], table.nodes)
    assert sel.nodes
    assert all(n["y"] < 400_000 for n in sel.nodes)
    assert len(sel.nodes) < len(table.nodes)


def test_filters_intersect_locations(table):
    both = select_nodes(["north", "resource:Desc_LiquidOil_C", "purity:pure"], table.nodes)
    assert both.nodes
    assert all(n["purity"] == "pure" for n in both.nodes)
    assert all(n["resource"] == "Desc_LiquidOil_C" for n in both.nodes)


def test_locations_union(table):
    a = select_nodes(["grid:X3Y4"], table.nodes)
    b = select_nodes(["grid:X3Y5"], table.nodes)
    both = select_nodes(["grid:X3Y4", "grid:X3Y5"], table.nodes)
    assert len(both.nodes) == len(a.nodes) + len(b.nodes)


def test_failed_location_returns_nothing_not_the_whole_map(table):
    """The dangerous failure mode: a typo'd region must not quietly widen the scope
    to the entire map, which would answer a completely different question."""
    sel = select_nodes(["region:Northern Forrest"], table.nodes)
    assert sel.nodes == []
    assert not sel.whole_map
    assert any("unknown region" in e for e in sel.errors)


def test_filters_only_still_means_whole_map(table):
    """A filter with no location is a legitimate whole-map query."""
    sel = select_nodes(["resource:Desc_LiquidOil_C"], table.nodes)
    assert sel.whole_map
    assert len(sel.nodes) == 48


def test_unknown_node_is_reported(table):
    sel = select_nodes(["node:BP_DoesNotExist"], table.nodes)
    assert sel.nodes == []
    assert any("unknown node" in e for e in sel.errors)


def test_malformed_near_is_reported(table):
    sel = select_nodes(["near:1,2"], table.nodes)
    assert any("near:" in e for e in sel.errors)
