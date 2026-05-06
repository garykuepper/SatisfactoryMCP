"""Footprints and the layout schematic.

The layout is a schematic, not a blueprint, so these tests pin the things that are
genuinely derived -- footprints, block splitting, chain depth, floor stacking -- and
not the aesthetic choices.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from satisfactory_mcp.docs.footprint import FOUNDATION_M, extract_footprint
from satisfactory_mcp.planning.layout import LOGISTICS_FLOOR_M, build_layout
from satisfactory_mcp.planning.optimize import MW, Scenario, solve

pytestmark = pytest.mark.integration

CRUDE = "Desc_LiquidOil_C"
WATER = "Desc_Water_C"


# ------------------------------------------------------------- footprints


@pytest.mark.parametrize(
    "cls,w,d",
    [
        ("Build_ConstructorMk1_C", 8, 10),
        ("Build_AssemblerMk1_C", 9, 16),
        ("Build_OilRefinery_C", 10, 22),
        ("Build_Blender_C", 18, 16),
        ("Build_ManufacturerMk1_C", 18, 20),
    ],
)
def test_footprints_match_known_dimensions(game, cls, w, d):
    fp = game.buildings[cls].footprint
    assert fp is not None
    assert (fp.width_m, fp.depth_m) == pytest.approx((w, d), abs=0.5)


def test_rotated_clearance_boxes_are_transformed(game):
    """The Fuel Generator's clearance is thin boxes at 45-degree increments around a
    round machine. Taking the largest box naively gives 22x4; the real footprint is
    ~20x20, a ~1000-foundation error across a 176-generator plan."""
    fp = game.buildings["Build_GeneratorFuel_C"].footprint
    assert fp.width_m == pytest.approx(20, abs=1)
    assert fp.depth_m == pytest.approx(20, abs=1)
    assert fp.foundations == 9  # 3x3 at 8 m


def test_every_production_building_has_a_footprint(game):
    """A missing footprint silently under-sizes a layout, so normalize warns and this
    keeps the warning honest."""
    for b in game.buildings.values():
        if b.is_manufacturer or b.is_generator:
            assert b.footprint is not None, b.cls


def test_foundations_round_up_to_the_grid(game):
    fp = game.buildings["Build_OilRefinery_C"].footprint
    # 10 x 22 m spans 2 x 3 eight-metre foundations.
    assert fp.foundations == 6
    assert FOUNDATION_M == 8.0


def test_no_clearance_data_returns_none():
    assert extract_footprint(None) is None
    assert extract_footprint("") is None


# ----------------------------------------------------------------- layout


@pytest.fixture(scope="module")
def oil_layout(game):
    sol = solve(
        Scenario(
            game=game,
            recipes=[
                "Recipe_Alternate_HeavyOilResidue_C",
                "Recipe_Alternate_DilutedFuel_C",
                "Recipe_ResidualPlastic_C",
            ],
            objective="max_mw",
            exports=(MW, "Desc_Plastic_C"),
            raw_caps={CRUDE: 1200.0, WATER: 1e5},
        )
    )
    assert sol.ok
    return sol, build_layout(game, sol)


def test_blocks_are_split_by_throughput_not_taste(oil_layout, game):
    """Line count IS block count: a manifold cannot be fed by three pipes."""
    _sol, lay = oil_layout
    split = [b for b in lay.blocks if b.parts > 1]
    assert split, "expected at least one process to exceed a single line"
    for b in split:
        # Each sub-block's own flow must now fit within its carriers.
        for item, rate in {**b.inputs, **b.outputs}.items():
            capacity = 600.0 if game.items[item].is_fluid else 780.0
            assert rate <= capacity * 1.0001, (b.name, item, rate)


def test_split_blocks_conserve_machines(oil_layout):
    sol, lay = oil_layout
    by_label: dict[str, int] = {}
    for b in lay.blocks:
        by_label[b.label] = by_label.get(b.label, 0) + b.machines
    for proc in sol.processes:
        assert by_label[proc["label"]] == proc["machines"]


def test_stages_follow_the_chain(oil_layout, game):
    """Extractors at the bottom, generators at the top."""
    _sol, lay = oil_layout
    stages = {b.label: b.stage for b in lay.blocks}
    hor = next(s for lbl, s in stages.items() if "Heavy Oil Residue" in lbl)
    fuel = next(s for lbl, s in stages.items() if "Diluted Fuel" in lbl)
    gen = next(s for lbl, s in stages.items() if "Generator" in lbl)
    assert hor < fuel < gen


def test_stage_assignment_terminates_on_a_cyclic_recipe_graph(game):
    """Recycled Plastic and Recycled Rubber consume each other's output, so a
    topological sort would fail. Relaxation must settle instead of looping."""
    sol = solve(
        Scenario(
            game=game,
            recipes=[
                "Recipe_Alternate_Plastic_1_C",
                "Recipe_Alternate_RecycledRubber_C",
                "Recipe_Alternate_HeavyOilResidue_C",
                "Recipe_Alternate_DilutedFuel_C",
            ],
            objective="max_item",
            target_item="Desc_Plastic_C",
            exports=("Desc_Plastic_C",),
            raw_caps={CRUDE: 600.0, WATER: 1e5},
            grid_import_mw=1e6,
        )
    )
    assert sol.ok
    lay = build_layout(game, sol)
    assert lay.blocks
    assert all(b.stage < len(lay.blocks) + 1 for b in lay.blocks)


def test_floors_alternate_production_and_logistics(oil_layout):
    _sol, lay = oil_layout
    kinds = [f.kind for f in lay.floors]
    assert kinds[0] == "production"
    assert kinds[-1] == "production"
    for a, b in pairwise(kinds):
        assert a != b, kinds


def test_floor_height_clears_the_tallest_machine(oil_layout):
    _sol, lay = oil_layout
    for f in lay.floors:
        if f.kind == "production" and f.blocks:
            assert f.height_m >= max(b.height_m for b in f.blocks)
        else:
            assert f.height_m == LOGISTICS_FLOOR_M


def test_site_is_sized_by_the_largest_floor_not_the_sum(oil_layout):
    """Floors stack, so the site footprint is the peak floor, not the total."""
    _sol, lay = oil_layout
    assert lay.foundations == max(f.foundations for f in lay.floors)
    assert lay.foundations <= lay.total_foundations
    assert lay.site_side_m() > 0


def test_buses_never_flow_backwards_down_the_stack(oil_layout):
    """An item with no on-site consumer leaves at the level it is made. Defaulting
    its destination to stage 0 rendered as flowing back down."""
    _sol, lay = oil_layout
    for bus in lay.buses:
        assert bus.to_stage >= bus.from_stage, (bus.name, bus.from_stage, bus.to_stage)


def test_bus_rates_match_the_solve(oil_layout, game):
    sol, lay = oil_layout
    by_item = {b.item: b for b in lay.buses}
    for entry in sol.logistics:
        if entry["item"] in by_item and not by_item[entry["item"]].external:
            assert by_item[entry["item"]].rate == pytest.approx(entry["rate"], rel=1e-3)


def test_layout_excludes_power_from_buses(oil_layout):
    """MW is a pseudo-item for the balance; it does not ride a belt."""
    _sol, lay = oil_layout
    assert MW not in {b.item for b in lay.buses}
    for b in lay.blocks:
        assert MW not in b.inputs and MW not in b.outputs
