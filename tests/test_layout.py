"""Footprints and the layout schematic.

The layout is a schematic, not a blueprint, so these tests pin the things that are
genuinely derived -- footprints, block splitting, chain depth, floor stacking -- and
not the aesthetic choices.
"""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from conftest import REFERENCE_FIELD

from satisfactory_mcp.core.gamedata.footprint import (
    FOUNDATION_M,
    LANE_M,
    Footprint,
    extract_footprint,
)
from satisfactory_mcp.domain.planning.layout import LOGISTICS_FLOOR_M, build_layout
from satisfactory_mcp.domain.planning.optimize import MW, Scenario, solve

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


def test_footprint_height_includes_tops(game):
    """Height reaches the top of EVERY clearance box -- the ExcludeForSnapping and soft
    boxes carry the chimneys and tops -- while width/depth stay on the hard boxes."""
    asm = game.buildings["Build_AssemblerMk1_C"].footprint
    assert asm.height_m == pytest.approx(10.8, abs=0.1)
    assert (asm.width_m, asm.depth_m) == (9, 16)
    assert game.buildings["Build_GeneratorCoal_C"].footprint.height_m == pytest.approx(32.0, abs=0.1)
    assert game.buildings["Build_ConstructorMk1_C"].footprint.height_m == pytest.approx(8.5, abs=0.1)


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


@pytest.mark.parametrize(
    "cls,w,d,h",
    [
        ("Build_Foundation_8x1_01_C", 8, 8, 1),
        ("Build_Foundation_8x4_01_C", 8, 8, 4),
        ("Build_Wall_8x4_01_C", 0.5, 8, 4),
        ("Build_PillarBase_C", 8, 8, 4),  # the Big Pillar Support the player measured
        ("Build_Ramp_8x4_01_C", 8, 8, 4),
        ("Build_Beam_C", 4, 0.8, 1),  # the rotated soft box: 4 m LONG, not 4 m tall
    ],
)
def test_architecture_pieces_have_sizes(game, cls, w, d, h):
    """Architecture carries only CT_Soft clearance boxes -- soft is how the game lets
    pieces clip together -- so the old skip-all-soft rule reported every foundation,
    wall, pillar, ramp and beam as sizeless and a player measured one in-game. With no
    hard box, the soft union IS the piece. Heights asserted too: a 1 m and a 4 m
    foundation differ in nothing else."""
    fp = game.buildings[cls].footprint
    assert fp is not None
    assert (fp.width_m, fp.depth_m, fp.height_m) == pytest.approx((w, d, h), abs=0.5)


def test_soft_boxes_still_do_not_inflate_machines(game):
    """The fallback must never mix soft into hard: on a machine the soft boxes are
    overlap allowances AROUND the hard volume, and counting them would regress the
    Fuel Generator's 3x3-foundation answer that test_rotated_clearance_boxes pins."""
    fp = game.buildings["Build_GeneratorFuel_C"].footprint
    assert fp.foundations == 9


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


def test_plan_layout_takes_the_same_solve_arguments_as_plan_factory(game, state):
    """It re-solved at defaults, so it schematised a DIFFERENT plan than the one it was
    asked to draw -- measured at 15,043 MW against an 83,737 MW plan, because base
    extraction is roughly a sixth of overclocked. Four arguments were missing."""
    import inspect

    from satisfactory_mcp import server as srv

    factory_args = set(inspect.signature(srv.plan_factory).parameters)
    layout_args = set(inspect.signature(srv.plan_layout).parameters)
    shaping = {"clocks", "extractor_clocks", "machine_cost_mw", "water_extractors"}
    assert shaping <= factory_args
    assert shaping <= layout_args, f"plan_layout still cannot express: {shaping - layout_args}"


def test_the_two_tools_agree_on_the_same_request(game, state):
    from satisfactory_mcp import server as srv

    kw = dict(
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=64,
        limit=1,
    )
    f = srv.plan_factory(**kw)
    lay = srv.plan_layout(**kw)

    def mw(text):
        line = next(x for x in text.splitlines() if x.startswith("net_MW"))
        return line.split()[0].split("=")[1]

    assert mw(f) == mw(lay), "the layout must schematise the plan it was given"


def test_fluid_head_names_what_the_floor_order_costs(game, state):
    """Floors follow chain depth, which is a correctness property, not a physics one.
    On a measured oil plan it made every fluid climb -- water four floors at 11,500
    m3/min. The model has no terrain, so the cost is reported rather than optimised."""
    from satisfactory_mcp.domain.planning.layout import build_layout, fluid_head
    from satisfactory_mcp.domain.planning.optimize import solve
    from satisfactory_mcp.domain.planning.scenario import build_scenario

    req = build_scenario(
        game,
        state,
        sources=list(REFERENCE_FIELD),
        objective="max_mw",
        exports=["MW", "Plastic", "Rubber"],
        export_minimums={"Plastic": 2000, "Rubber": 300},
        extractor_clocks=[1, 1.5, 2, 2.5],
        water_extractors=64,
    )
    head = fluid_head(build_layout(game, solve(req.scenario)))
    assert head, "an oil plan moves fluids between floors"
    water = [d for d in head if d["item"] == "Water"]
    assert water and water[0]["direction"] == "climbs"
    assert head[0]["floors"] >= water[0]["floors"], "sorted by how far it is lifted"


def test_the_three_planning_tools_share_one_pipeline(game, state):
    """They ran the same seven-step prologue three times, and the copies drifted:
    plan_layout stopped accepting extractor_clocks and water_extractors and silently
    re-solved at defaults. One implementation cannot drift from itself."""
    import inspect

    from satisfactory_mcp.domain.planning import diff_service as diff_mod
    from satisfactory_mcp.domain.planning import layout_service as layout_mod
    from satisfactory_mcp.domain.planning import report as report_mod
    from satisfactory_mcp.interfaces.mcp.tools import planning

    # All three reach the pipeline through a domain service -- prepare plus the world
    # lookups each of them implies. Still one implementation of the prologue, not a
    # fourth copy of it.
    assert "prepare(" in inspect.getsource(report_mod)
    assert "prepare(" in inspect.getsource(layout_mod)
    assert "prepare(" in inspect.getsource(diff_mod)
    for name in ("plan_factory", "plan_layout", "diff_vs_save"):
        src = inspect.getsource(getattr(planning, name))
        assert any(
            call in src
            for call in (
                "prepare(",
                "build_plan_report(",
                "build_layout_report(",
                "build_diff_report(",
            )
        ), f"{name} does not use the shared pipeline"
        assert "build_scenario(" not in src, f"{name} still builds its own scenario"
        assert "= solve(" not in src, f"{name} still solves for itself"


def test_prepare_renders_nothing(game, state):
    """The pipeline is reusable only if it is free of presentation. A failure comes back
    as a headline plus notes and the TOOL decides how to show it."""
    import inspect

    from satisfactory_mcp.domain.planning import prepare as prepare_mod

    src = inspect.getsource(prepare_mod)
    assert "render." not in src
    assert "envelope" not in src


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (dict(sources=["region:Nowhere"], exports=["MW"]), "no sources selected"),
        (dict(exports=["Plastik"]), "unusable exports"),
    ],
)
def test_every_planning_tool_reports_a_bad_request_the_same_way(game, state, kwargs, expected):
    """Shared guards mean shared wording. Before, each tool spelled these out itself."""
    from satisfactory_mcp import server as srv

    for fn in (srv.plan_factory, srv.plan_layout, srv.diff_vs_save):
        out = fn(limit=1, **kwargs)
        assert expected in out.splitlines()[0], (fn.__name__, out.splitlines()[0])


def test_prepare_is_usable_without_the_mcp_layer(game, state):
    """The point of the extraction: a script or a batch planner can solve without going
    through a tool, and gets the same guards."""
    from satisfactory_mcp.domain.planning.prepare import prepare

    good = prepare(
        game, state, {"objective": "max_mw", "sources": list(REFERENCE_FIELD), "exports": ["MW"]}
    )
    assert good.ok and good.solution.net_mw > 0

    bad = prepare(
        game, state, {"objective": "max_mw", "sources": ["region:Nowhere"], "exports": ["MW"]}
    )
    assert not bad.ok
    assert bad.failure.headline == "no sources selected"


# ------------------------------------------- capping a deck's footprint


@pytest.fixture(scope="module")
def oil_solution(game, state):
    from satisfactory_mcp.domain.planning.prepare import prepare

    return prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW", "Plastic", "Rubber"],
            export_minimums={"Plastic": 2000, "Rubber": 300},
            extractor_clocks=[1, 1.5, 2, 2.5],
            water_extractors=64,
        ),
    ).solution


@pytest.mark.parametrize("cap", [1225, 900, 400])
def test_no_deck_exceeds_the_foundation_cap(game, oil_solution, cap):
    """The inverse of the default question. Uncapped, layout answers "how big a site
    does this need" -- 496x496 m. A player with a finished platform is asking the
    reverse: I have 30x30 foundations, how many decks?"""
    from satisfactory_mcp.domain.planning.layout import build_layout

    lay = build_layout(game, oil_solution, max_floor_foundations=cap)
    production = [f for f in lay.floors if f.kind == "production"]
    oversized = [f for f in production if f.foundations > cap and len(f.blocks) > 1]
    assert not oversized, [(f.index, f.foundations) for f in oversized]


def test_capping_adds_decks_without_changing_the_work(game, oil_solution):
    """Total foundations are conserved: the same machines, stacked differently. If the
    total moved, the cap would be silently dropping or duplicating blocks."""
    from satisfactory_mcp.domain.planning.layout import build_layout

    def totals(cap):
        lay = build_layout(game, oil_solution, max_floor_foundations=cap)
        production = [f for f in lay.floors if f.kind == "production"]
        return len(production), sum(f.foundations for f in production), lay.foundations

    open_decks, open_total, open_peak = totals(0)
    capped_decks, capped_total, capped_peak = totals(900)

    assert capped_total == open_total, "same machines, different stacking"
    assert capped_decks > open_decks
    assert capped_peak <= 900 < open_peak


def test_a_block_larger_than_the_cap_gets_its_own_deck(game):
    """It must not vanish, and it must not be split -- a block is one manifold. The
    honest answer is a deck of its own that exceeds the cap."""
    from satisfactory_mcp.domain.planning.layout import _decks_for

    class B:
        def __init__(self, f):
            self.foundations = f

    big, small = B(500), B(10)
    decks = _decks_for([small, big, small], cap=100)
    assert [len(d) for d in decks] == [1, 1, 1]
    assert decks[1][0] is big


def test_a_zero_cap_means_no_cap(game, oil_solution):
    from satisfactory_mcp.domain.planning.layout import build_layout

    assert [f.index for f in build_layout(game, oil_solution, max_floor_foundations=0).floors] == [
        f.index for f in build_layout(game, oil_solution).floors
    ]


# ------------------------------------------------- one sizing primitive


def test_a_block_is_packed_not_multiplied(game, state):
    """`Footprint.foundations` is per machine and says outright that it ignores shared
    edges, so `n x found` is an upper bound. plan_layout used to charge exactly that, and
    the water-siting note had independently grown its own copy of the same arithmetic --
    two places to be wrong instead of one."""
    from satisfactory_mcp.domain.planning.prepare import prepare

    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )
    lay = build_layout(game, prepared.solution)
    checked = 0
    for block in lay.blocks:
        fp = game.buildings[block.building_id].footprint
        if fp is None:
            continue
        building = game.buildings[block.building_id]
        expected = (
            fp.pack(block.machines) if building.is_extractor else fp.pack_manifold(block.machines)
        )
        assert block.foundations == expected.foundations
        if block.machines > 1:
            assert block.foundations <= fp.foundations * block.machines
            checked += 1
    assert checked, "no multi-machine block to compare"


def test_packing_shrank_the_site_rather_than_the_machine_count(game, state):
    """The correction must move floor area only. A foundation change that also moved
    machines would mean it had eaten part of the plan."""
    from satisfactory_mcp.domain.planning.prepare import prepare

    prepared = prepare(
        game,
        state,
        dict(
            sources=list(REFERENCE_FIELD),
            objective="max_mw",
            exports=["MW"],
            extractor_clocks=[1, 1.5, 2, 2.5],
        ),
    )
    lay = build_layout(game, prepared.solution)
    naive = sum(
        game.buildings[b.building_id].footprint.foundations * b.machines
        for b in lay.blocks
        if game.buildings.get(b.building_id) and game.buildings[b.building_id].footprint
    )
    assert lay.total_foundations < naive
    assert lay.machines == sum(p["machines"] for p in prepared.solution.processes)


def test_a_building_with_no_clearance_data_is_not_free(game, state):
    """packed is None only when the dump has no clearance boxes at all. Reporting 0
    foundations there is honest; silently sizing it as a point would not be, so
    build_layout names those buildings in its warnings."""
    from satisfactory_mcp.domain.planning.layout import Block

    bare = Block(
        key="k",
        label="l",
        building_id="x",
        building="X",
        recipe=None,
        machines=4,
        clock=1.0,
        part=1,
        parts=1,
    )
    assert bare.packed is None
    assert bare.foundations == 0
    assert bare.block_width_m == 0.0


def test_slab_zero_matches_upstream_exactly(oil_layout, game):
    """slab_foundations=0 is the documented off switch: it has to read the same as
    not passing the parameter at all, since every existing caller depends on that."""
    sol, default = oil_layout
    explicit_zero = build_layout(game, sol, slab_foundations=0)
    assert [f.height_m for f in explicit_zero.floors] == [f.height_m for f in default.floors]
    assert [f.kind for f in explicit_zero.floors] == [f.kind for f in default.floors]
    assert [f.foundations for f in explicit_zero.floors] == [f.foundations for f in default.floors]
    assert explicit_zero.off_slab == []


def test_slab_mode_moves_extractors_off_the_floor(oil_solution, game):
    """Water Extractors stand on their nodes, not on a factory floor -- once slabs
    are on, no production floor should hold one.

    Uses oil_solution (not oil_layout): oil_layout's Scenario supplies crude and
    water via raw_caps, so its solve has ZERO extractor buildings in it -- a test
    against it would pass whether or not the off-slab split worked at all. Verified
    directly: oil_solution (sourced from the committed reference save via the
    `state` fixture and REFERENCE_FIELD) has real Miner Mk.2/Oil Pump/Water Pump
    blocks, 28 of them landing in off_slab at slab_foundations=10."""
    lay = build_layout(game, oil_solution, slab_foundations=10)
    on_floor_ids = {b.building_id for f in lay.floors for b in f.blocks}
    assert "Build_WaterPump_C" not in on_floor_ids
    off_slab_ids = {b.building_id for b in lay.off_slab}
    assert "Build_WaterPump_C" in off_slab_ids
    # nothing lost: every block is either on a floor or in off_slab, once each
    on_floor_keys = [b.key for f in lay.floors for b in f.blocks]
    all_keys = sorted(on_floor_keys + [b.key for b in lay.off_slab])
    assert all_keys == sorted(b.key for b in lay.blocks)


def test_slab_mode_gives_blocks_real_positions_and_conserves_them(oil_layout, game):
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10, aisle_foundations=1)
    placed_keys = [b.key for f in lay.floors for b in f.blocks]
    assert sorted(placed_keys) == sorted(
        b.key for b in lay.blocks
        if not (game.buildings.get(b.building_id) and game.buildings[b.building_id].is_extractor)
    )
    for f in lay.floors:
        if f.slab_side_m is None:
            continue  # oversized block given its own floor at full size -- no slab to check
        for b in f.blocks:
            assert b.x_fnd + math.ceil(b.block_width_m / FOUNDATION_M) <= 10 or b.rotated
        # no two blocks on the same floor overlap
        for a, c in ((a, c) for i, a in enumerate(f.blocks) for c in f.blocks[i + 1 :]):
            aw = math.ceil((a.block_depth_m if a.rotated else a.block_width_m) / FOUNDATION_M)
            ad = math.ceil((a.block_width_m if a.rotated else a.block_depth_m) / FOUNDATION_M)
            cw = math.ceil((c.block_depth_m if c.rotated else c.block_width_m) / FOUNDATION_M)
            cd = math.ceil((c.block_width_m if c.rotated else c.block_depth_m) / FOUNDATION_M)
            overlap_x = a.x_fnd < c.x_fnd + cw and c.x_fnd < a.x_fnd + aw
            overlap_y = a.y_fnd < c.y_fnd + cd and c.y_fnd < a.y_fnd + ad
            assert not (overlap_x and overlap_y), (a.key, c.key)


def test_slab_mode_never_emits_a_logistics_floor(oil_layout, game):
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    assert not any(f.kind == "logistics" for f in lay.floors)


def test_slab_mode_attaches_crossing_buses_only_where_they_are_actually_consumed(oil_layout, game):
    """A crossing bus must attach only to a floor that (a) holds a consumer of it and
    (b) sits above the lowest floor holding a producer of it -- not by stage number,
    which breaks once a stage's blocks split across floors (the oversized-block escape
    hatch does this routinely) or once oversized floors get reordered into chain order."""
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    floor_of_block = {b.key: f.index for f in lay.floors for b in f.blocks}
    for f in lay.floors:
        for bus in f.buses:
            producer_floors = {floor_of_block[p] for p in bus.producers if p in floor_of_block}
            assert producer_floors, (f.index, bus.item)
            assert f.index > min(producer_floors), (f.index, bus.item, producer_floors)
            assert any(floor_of_block.get(c) == f.index for c in bus.consumers), (f.index, bus.item)


def test_slab_mode_floors_stay_in_chain_order_even_with_oversized_blocks(oil_layout, game):
    """Oversized blocks must slot into chain order by their own stage, not get stacked
    above every packed floor regardless of how early their stage is."""
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    min_stages = [min(f.stages) for f in lay.floors if f.stages]
    assert min_stages == sorted(min_stages)


def test_fluid_head_maps_a_stage_to_its_lowest_floor_in_slab_mode(oil_layout, game):
    """Regression for the bug the final review found: fluid_head mapped a stage to
    whichever floor last wrote floor.stage (a single value, not floor.stages), so a
    stage split across several floors (routine via the oversized-block escape hatch)
    either got credited to an arbitrary one of them or dropped out of the riser count
    entirely. Values below are measured directly against this fixture at
    slab_foundations=10, not estimated -- confirm they still hold after any future
    change to the packer or to fluid_head."""
    from satisfactory_mcp.domain.planning.layout import fluid_head

    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    climbs = {row["item"]: row for row in fluid_head(lay, pump_head_m=20.0)}
    residue = climbs.get("Heavy Oil Residue")
    assert residue is not None
    assert residue["floors"] == 3
    # The three Refinery floors are double height since the Refinery's real 30 m top:
    # 3 x 32 = 96 m, ceil(96 / 20) = 5 pumps a line, x 3 lines = 15.
    assert residue["metres"] == 96
    assert residue["pumps"] == 15
    fuel = climbs.get("Fuel")
    assert fuel is not None
    # 6, not 5, since the 1-foundation edge walkway: blocks that fill a bare 10x10
    # slab no longer fit its 8x8 interior, so one more floor stacks up.
    assert fuel["floors"] == 6
    assert fuel["metres"] == 96
    assert fuel["pumps"] == 30


def test_slab_floor_foundations_is_the_whole_slab_not_just_used_space(oil_layout, game):
    """You pour the whole slab, not just the part your blocks cover -- Floor.foundations
    (which total_foundations and the materials bill read) has to reflect that, while
    Floor.used_foundations keeps the real number for a percent-full figure."""
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    for f in lay.floors:
        if f.slab_side_m is not None:  # not an oversized-block floor
            assert f.foundations == 100  # 10x10
        assert f.used_foundations == sum(b.foundations for b in f.blocks)
        assert f.used_foundations <= f.foundations


def test_slab_mode_site_side_is_the_slab_not_the_used_area(oil_layout, game):
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=20)
    assert lay.site_side_m() == 20 * FOUNDATION_M


# ------------------------------------------------------------- shelf packer (_pack_slab)


def _fake_block(key, w_m, d_m, foundations):
    from satisfactory_mcp.domain.planning.layout import Block
    from satisfactory_mcp.core.gamedata.footprint import Packed

    return Block(
        key=key, label=key, building_id="x", building="X", recipe=None,
        machines=1, clock=1.0, part=1, parts=1,
        packed=Packed(count=1, columns=1, rows=1, width_m=w_m, depth_m=d_m, foundations=foundations),
    )


def test_pack_slab_no_overlaps_and_respects_the_aisle():
    from satisfactory_mcp.domain.planning.layout import _pack_slab

    blocks = [_fake_block(f"b{i}", 40, 16, 10) for i in range(6)]  # 5x2 fnd each
    floors, oversized, warnings = _pack_slab(blocks, slab_fnd=10, aisle_fnd=1)
    assert not oversized
    assert not warnings
    placed = [p for floor in floors for p in floor]
    assert len(placed) == 6
    # Check each floor separately: blocks at same y_fnd within a floor must have aisle
    for floor in floors:
        for a, b in ((a, b) for i, a in enumerate(floor) for b in floor[i + 1 :]):
            if a.y_fnd != b.y_fnd:
                continue  # different rows never overlap in y by construction
            lo, hi = (a, b) if a.x_fnd <= b.x_fnd else (b, a)
            assert lo.x_fnd + lo.w_fnd + 1 <= hi.x_fnd, (lo, hi)  # >= 1-foundation aisle


def test_pack_slab_starts_a_new_floor_when_full():
    from satisfactory_mcp.domain.planning.layout import _pack_slab

    # ten 8x2 blocks: each alone fills the 8-wide interior of a 10 slab (1-foundation
    # edge walkway each side), and ten rows of 2+1 aisle can't fit an 8-deep interior,
    # so this must span at least two floors.
    blocks = [_fake_block(f"b{i}", 64, 16, 10) for i in range(10)]
    floors, oversized, warnings = _pack_slab(blocks, slab_fnd=10, aisle_fnd=1)
    assert not oversized
    assert len(floors) >= 2
    for floor in floors:
        for p in floor:
            assert p.x_fnd + p.w_fnd <= 9
            assert p.y_fnd + p.d_fnd <= 10


def test_pack_slab_oversized_block_gets_flagged_not_placed():
    from satisfactory_mcp.domain.planning.layout import _pack_slab

    huge = _fake_block("huge", 200, 200, 625)  # 25x25 fnd, slab is 10x10
    floors, oversized, warnings = _pack_slab([huge], slab_fnd=10, aisle_fnd=1)
    assert oversized == [huge]
    assert not any(p for floor in floors for p in floor)
    assert warnings and "huge" in warnings[0]


def test_pack_slab_never_rotates_now_that_cells_are_square():
    from satisfactory_mcp.domain.planning.layout import _pack_slab

    # A uniform grid cell is a square sized to the biggest block on the floor, so
    # every block fits either way up -- rotation no longer earns its keep the way it
    # did for the old shelf-packer's leftover-row-space case, and the packer always
    # leaves rotated False.
    a = _fake_block("a", 40, 16, 10)
    b = _fake_block("b", 40, 16, 10)
    floors, oversized, _warnings = _pack_slab([a, b], slab_fnd=10, aisle_fnd=1)
    assert not oversized
    placed = [p for floor in floors for p in floor]
    assert all(p.rotated is False for p in placed)


def test_pack_slab_aligns_blocks_into_shared_columns_and_rows():
    from satisfactory_mcp.domain.planning.layout import _pack_slab

    # Five 1x1-fnd blocks (8x8m each) on a 10-wide slab (8-wide interior after the
    # edge walkway): with aisle_fnd=1 the grid holds a 4x4 arrangement of 1-fnd
    # cells (4*1 + 3*1 = 7 <= 8), so this must wrap after 4 -- block 5 starts a new
    # row, but its x_fnd must match block 1's column (both column 0), and blocks
    # 1-4 must share one y_fnd (row 0) while block 5 sits at a different, larger one.
    blocks = [_fake_block(f"b{i}", 8, 8, 1) for i in range(5)]
    floors, oversized, _warnings = _pack_slab(blocks, slab_fnd=10, aisle_fnd=1)
    assert not oversized
    placed = {p.block.key: p for floor in floors for p in floor}
    row0_y = placed["b0"].y_fnd
    assert placed["b1"].y_fnd == placed["b2"].y_fnd == placed["b3"].y_fnd == row0_y
    assert placed["b4"].y_fnd != row0_y  # wrapped to a new row
    assert placed["b4"].x_fnd == placed["b0"].x_fnd  # but shares b0's column


def test_slab_mode_keeps_a_one_foundation_walkway_at_every_slab_edge(oil_layout, game):
    from satisfactory_mcp.domain.planning.layout import EDGE_FND, FOUNDATION_M, _to_fnd

    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10)
    checked = 0
    for f in lay.floors:
        if f.slab_side_m is None:
            continue
        side = round(f.slab_side_m / FOUNDATION_M)
        for b in f.blocks:
            w, d = _to_fnd(b.block_width_m), _to_fnd(b.block_depth_m)
            if b.rotated:
                w, d = d, w
            assert b.x_fnd >= EDGE_FND and b.y_fnd >= EDGE_FND, (b.key, b.x_fnd, b.y_fnd)
            assert b.x_fnd + w <= side - EDGE_FND and b.y_fnd + d <= side - EDGE_FND, (b.key, side)
            checked += 1
    assert checked


def test_pack_slab_rectangular_slab_uses_its_depth_for_rows():
    from satisfactory_mcp.domain.planning.layout import EDGE_FND, _pack_slab

    # Eight 3x3-fnd blocks. A 10x10 slab (8x8 inner) holds a 2x2 grid of 3-cells; a
    # 10x16 slab (8x14 inner) holds 2 columns x 3 rows (3*3 + 2*1 = 11 <= 14).
    blocks = [_fake_block(f"b{i}", 24, 24, 9) for i in range(8)]
    square, _, _ = _pack_slab(blocks, slab_fnd=10, aisle_fnd=1)
    rect, oversized, _ = _pack_slab(blocks, slab_fnd=10, aisle_fnd=1, slab_depth_fnd=16)
    assert [len(f) for f in square] == [4, 4]
    assert [len(f) for f in rect] == [6, 2]
    assert not oversized
    for p in (p for f in rect for p in f):
        assert p.x_fnd + p.w_fnd <= 10 - EDGE_FND
        assert p.y_fnd + p.d_fnd <= 16 - EDGE_FND


def test_rectangular_slab_floor_pours_width_times_depth(oil_layout, game):
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10, slab_depth_foundations=16)
    slabbed = [f for f in lay.floors if f.slab_side_m is not None]
    assert slabbed
    assert all(f.foundations == 160 for f in slabbed)


def test_pack_rows_gives_each_manifold_its_own_row_along_the_long_side():
    from satisfactory_mcp.domain.planning.layout import EDGE_FND, _pack_rows

    # 10 wide x 16 deep: long side is y. A 3x1-fnd manifold (24x8 m, long along x)
    # must be turned to lie along y; a 1x3 one already does. Rows stack along x,
    # 1-foundation aisle apart: x = 1, 3, 5, 7 (1 + 1 + 1 ...), then the fifth
    # doesn't fit the 8-wide inner span (7 + 1 + 1 = 9 > 8) and starts floor 2.
    wide = [_fake_block(f"w{i}", 24, 8, 3) for i in range(2)]
    deep = [_fake_block(f"d{i}", 8, 24, 3) for i in range(3)]
    floors, oversized, _ = _pack_rows(wide + deep, slab_fnd=10, aisle_fnd=1, slab_depth_fnd=16)
    assert not oversized
    assert [len(f) for f in floors] == [4, 1]
    first = floors[0]
    assert [p.x_fnd for p in first] == [1, 3, 5, 7]
    assert all(p.y_fnd == EDGE_FND for p in first)
    assert all((p.w_fnd, p.d_fnd) == (1, 3) for p in first)  # long side along y
    assert [p.rotated for p in first] == [True, True, False, False]


def test_pack_rows_flags_a_manifold_longer_than_the_slab():
    from satisfactory_mcp.domain.planning.layout import _pack_rows

    long_one = _fake_block("long", 8, 8 * 15, 15)  # 15 fnd long, inner long side is 14
    floors, oversized, warnings = _pack_rows([long_one], slab_fnd=10, aisle_fnd=1, slab_depth_fnd=16)
    assert oversized == [long_one] and warnings


def test_rows_layout_puts_one_manifold_per_row(oil_layout, game):
    sol, _default = oil_layout
    lay = build_layout(game, sol, slab_foundations=10, slab_depth_foundations=16, slab_layout="rows")
    for f in lay.floors:
        if f.slab_side_m is None:
            continue
        xs = [b.x_fnd for b in f.blocks]
        assert len(xs) == len(set(xs))  # every manifold on its own row


# ------------------------------------------------- manifold folding (spec 2026-10-01)

CON = Footprint(width_m=8.0, depth_m=10.0, height_m=8.0)


def test_eight_machines_stay_one_row():
    p = CON.pack_manifold(8)
    assert (p.folded, p.columns, p.rows) == (False, 8, 1)


def test_nine_machines_fold_with_the_odd_one_on_row_a():
    p = CON.pack_manifold(9)
    assert (p.folded, p.columns, p.rows) == (True, 5, 2)
    assert p.width_m == 5 * 8.0
    assert p.depth_m == 2 * 10.0 + LANE_M
    assert p.lane_m == LANE_M
    assert p.foundations == 5 * 4  # 40 m -> 5 fnd, 28 m -> 4 fnd


def test_a_short_row_folds_when_too_long_for_the_slab():
    wide = Footprint(width_m=20.0, depth_m=10.0, height_m=9.0)  # 4 x 20 m = 10 fnd
    assert wide.pack_manifold(4, max_row_fnd=14).folded is False
    assert wide.pack_manifold(4, max_row_fnd=8).folded is True


def test_a_single_machine_never_folds():
    huge = Footprint(width_m=200.0, depth_m=10.0, height_m=9.0)
    assert huge.pack_manifold(1, max_row_fnd=8).folded is False


def test_block_ports_follow_the_fold():
    from satisfactory_mcp.domain.planning.layout import Block

    def blk(n):
        return Block(key="k", label="l", building_id="x", building="X", recipe=None,
                     machines=n, clock=1.0, part=1, parts=1, packed=CON.pack_manifold(n))

    assert (blk(4).input_sides, blk(4).output_sides) == (("S",), ("N",))
    assert (blk(12).input_sides, blk(12).output_sides) == (("lane",), ("N", "S"))


def test_extractors_keep_free_packing_and_never_fold(oil_solution, game):
    lay = build_layout(game, oil_solution)
    pumps = [b for b in lay.blocks if b.building_id == "Build_WaterPump_C"]
    assert pumps, "fixture should contain Water Extractors"
    fp = game.buildings["Build_WaterPump_C"].footprint
    for b in pumps:
        assert not b.packed.folded
        assert b.packed == fp.pack(b.machines)


def test_fit_slab_is_the_smallest_even_slab_and_respects_the_cap():
    from satisfactory_mcp.domain.planning.layout import _fit_slab, _Placed

    b = _fake_block("a", 8, 8, 1)
    # x 1..4 plus far walkway = 5 -> 6; y 1..3 plus walkway = 4 -> 4
    assert _fit_slab([_Placed(b, 1, 1, 3, 2, False)], 16) == (6, 4)
    assert _fit_slab([_Placed(b, 1, 1, 20, 2, False)], 16) == (16, 4)
    two = [_Placed(b, 1, 1, 2, 2, False), _Placed(b, 4, 1, 3, 5, False)]
    assert _fit_slab(two, 16) == (8, 8)  # 4+3+1=8, 1+5+1=7 -> 8


def _typed_block(key, building, fnd, stage=0, machines=1, packed=True):
    from satisfactory_mcp.core.gamedata.footprint import Packed
    from satisfactory_mcp.domain.planning.layout import Block

    b = Block(
        key=key, label=key, building_id=building, building=building, recipe=None,
        machines=machines, clock=1.0, part=1, parts=1,
        packed=Packed(count=machines, columns=machines, rows=1, width_m=8.0 * fnd,
                      depth_m=8.0, foundations=fnd) if packed else None,
    )
    b.stage = stage
    return b


def _bus(item, rate, producers, consumers):
    from satisfactory_mcp.domain.planning.layout import Bus

    return Bus(item=item, name=item, rate=rate, carrier="belt", unit="/min", lines=1,
               producers=list(producers), consumers=list(consumers))


def test_group_blocks_separates_building_types():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    blocks = [_typed_block("c1", "Constructor", 5), _typed_block("c2", "Constructor", 5),
              _typed_block("a1", "Assembler", 6)]
    groups = dict(_group_blocks(blocks, []))
    assert {k: sorted(b.key for b in v) for k, v in groups.items()} == {
        "Constructor": ["c1", "c2"], "Assembler": ["a1"]}


def test_group_blocks_merges_a_tiny_group_into_its_biggest_trading_partner():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    s = _typed_block("s", "Smelter", 2, stage=0)
    c = _typed_block("c", "Constructor", 6, stage=1, machines=4)
    a = _typed_block("a", "Assembler", 6, stage=2, machines=2)
    buses = [_bus("Ingot", 30.0, ["s"], ["c"]), _bus("Ingot2", 5.0, ["s"], ["a"])]
    groups = dict(_group_blocks([s, c, a], buses))
    assert sorted(b.key for b in groups["Constructor + Smelter"]) == ["c", "s"]
    assert [b.key for b in groups["Assembler"]] == ["a"]


def test_group_blocks_breaks_a_trade_tie_toward_the_lower_stage():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    s = _typed_block("s", "Smelter", 2, stage=1)
    lo = _typed_block("lo", "Constructor", 6, stage=0)
    hi = _typed_block("hi", "Assembler", 6, stage=3)
    groups = dict(_group_blocks([s, lo, hi], []))  # no trade at all: a tie
    assert "Constructor + Smelter" in groups


def test_group_blocks_single_type_is_one_group():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    blocks = [_typed_block(f"c{i}", "Constructor", 1) for i in range(3)]
    groups = _group_blocks(blocks, [])
    assert [(label, len(bs)) for label, bs in groups] == [("Constructor", 3)]


def test_group_blocks_all_tiny_terminates_in_one_group():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    groups = _group_blocks([_typed_block("s", "Smelter", 1), _typed_block("c", "Constructor", 1)], [])
    assert len(groups) == 1
    assert sorted(b.key for b in groups[0][1]) == ["c", "s"]


def test_group_blocks_tolerates_a_block_without_clearance_data():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    bare = _typed_block("x", "Mystery", 0, packed=False)
    groups = _group_blocks([bare, _typed_block("c", "Constructor", 6)], [])
    assert len(groups) == 1 and len(groups[0][1]) == 2


def test_building_mode_floors_hold_one_group_on_even_capped_slabs(game, oil_solution):
    lay = build_layout(game, oil_solution, group_by="building")
    off = {b.key for b in lay.off_slab}
    on_floor = sorted(b.key for f in lay.floors for b in f.blocks)
    assert on_floor == sorted(b.key for b in lay.blocks if b.key not in off)
    for f in lay.floors:
        assert f.kind == "production" and f.group
        assert {b.building for b in f.blocks} <= set(f.group.split(" + "))
        if f.slab_side_m is not None:
            w, d = f.slab_side_m / FOUNDATION_M, f.slab_depth_m / FOUNDATION_M
            assert w % 2 == 0 and d % 2 == 0 and 2 <= w <= 16 and 2 <= d <= 16


def test_building_mode_stacks_floors_by_mean_stage(game, oil_solution):
    from satisfactory_mcp.domain.planning.layout import _mean_stage

    lay = build_layout(game, oil_solution, group_by="building")
    means = [_mean_stage(f.blocks) for f in lay.floors]
    assert means == sorted(means)


def test_building_mode_folds_long_manifolds(game, oil_solution):
    lay = build_layout(game, oil_solution, group_by="building")
    long_ones = [b for b in lay.blocks if b.packed and b.machines > 8 and b.key not in {o.key for o in lay.off_slab}]
    assert all(b.packed.folded for b in long_ones)


def test_bad_group_by_or_slab_cap_is_an_error(game, oil_solution):
    with pytest.raises(ValueError, match="group_by"):
        build_layout(game, oil_solution, group_by="type")
    with pytest.raises(ValueError, match="even"):
        build_layout(game, oil_solution, group_by="building", max_slab_foundations=15)
    with pytest.raises(ValueError, match="even"):
        build_layout(game, oil_solution, group_by="building", max_slab_foundations=2)


def test_building_floors_stack_same_type_manifolds_on_one_floor():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    # four 8-machine Constructor rows, 8x2 foundations each: 4 rows + 3 aisles = 11 deep,
    # fits one 16x16 slab's 14-foundation interior
    blocks = [_typed_block(f"c{i}", "Constructor", 16) for i in range(4)]
    for b in blocks:
        b.packed = type(b.packed)(count=8, columns=8, rows=1, width_m=64.0, depth_m=16.0, foundations=16)
    floors, warnings = _building_floors(blocks, [], 16, 1)
    assert not warnings
    assert len(floors) == 1 and len(floors[0].blocks) == 4
    w, d = floors[0].slab_side_m / 8, floors[0].slab_depth_m / 8
    # x: 1 edge + 2 west bay + 8 long + 1 far edge + 2 east bay = 14; y: rows at 0,3,6,9 -> last ends 11,
    # 1 + 11 + 1 = 13 -> even 14
    assert (w, d) == (14, 14)


def test_plan_layout_groups_by_building_and_reports_bad_args(game, state):
    from satisfactory_mcp import server as srv

    out = srv.plan_layout(objective="min_raw", exports=["Versatile Framework"],
                          export_minimums={"Versatile Framework": 10}, group_by="building")
    assert "Assembler" in out and "Constructor (stage" in out
    bad = srv.plan_layout(objective="min_raw", exports=["Versatile Framework"],
                          export_minimums={"Versatile Framework": 10}, group_by="type")
    assert bad.startswith("! ") and "group_by" in bad
    bad = srv.plan_layout(objective="min_raw", exports=["Versatile Framework"],
                          export_minimums={"Versatile Framework": 10}, group_by="building",
                          max_slab_foundations=15)
    assert bad.startswith("! ") and "even" in bad


def test_building_floors_share_one_slab_size():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    con = _typed_block("c", "Constructor", 16, stage=1)
    con.packed = type(con.packed)(count=8, columns=8, rows=1, width_m=64.0, depth_m=16.0, foundations=16)
    asm = _typed_block("a", "Assembler", 6, stage=2)
    asm.packed = type(asm.packed)(count=3, columns=3, rows=1, width_m=24.0, depth_m=16.0, foundations=6)
    floors, _warnings = _building_floors([con, asm], [], 16, 1)
    sizes = {(f.slab_side_m / 8, f.slab_depth_m / 8) for f in floors}
    # alone (1 edge + 2 west bay + row + 1 edge): Constructor 1+2+8+1 = 12 x 4,
    # Assembler 1+2+3+1 = 7 -> 8 x 4; shared: 12 x 4
    assert len(floors) == 2 and sizes == {(14, 4)}


def test_smelters_and_foundries_always_share_a_floor_group():
    from satisfactory_mcp.domain.planning.layout import _group_blocks

    # both well above MERGE_BELOW_FND and no trade between them: still one group
    groups = dict(_group_blocks([_typed_block("f", "Foundry", 16, machines=6),
                                 _typed_block("s", "Smelter", 6, machines=3),
                                 _typed_block("c", "Constructor", 6)], []))
    assert sorted(b.key for b in groups["Foundry + Smelter"]) == ["f", "s"]
    assert [b.key for b in groups["Constructor"]] == ["c"]


def test_a_wide_group_packs_with_two_foundation_aisles():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    def rows(wide):
        bs = [_typed_block(f"c{i}", "Constructor", 16) for i in range(2)]
        for b in bs:
            b.packed = type(b.packed)(count=8, columns=8, rows=1, width_m=64.0, depth_m=16.0, foundations=16)
        floors, _ = _building_floors(bs, [], 16, 1, wide_groups=wide)
        return sorted(b.y_fnd for b in floors[0].blocks)

    assert rows(frozenset()) == [1, 4]                  # 2 deep + 1 aisle
    assert rows(frozenset({"Constructor"})) == [1, 5]   # 2 deep + 2 aisle


def test_building_rows_start_past_the_west_bay_stage_rows_do_not():
    from satisfactory_mcp.domain.planning.layout import EDGE_FND, WEST_BAY_FND, _building_floors, _pack_rows

    def con():
        b = _typed_block("c", "Constructor", 16)
        b.packed = type(b.packed)(count=8, columns=8, rows=1, width_m=64.0, depth_m=16.0, foundations=16)
        return b

    floors, _ = _building_floors([con()], [], 16, 1)
    assert [b.x_fnd for b in floors[0].blocks] == [EDGE_FND + WEST_BAY_FND] == [3]
    placed, _, _ = _pack_rows([con()], slab_fnd=16, aisle_fnd=1)
    assert [p.x_fnd for p in placed[0]] == [1]


def test_building_floors_leave_a_two_foundation_bay_on_both_sides():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    c = _typed_block("c", "Constructor", 16)
    c.packed = type(c.packed)(count=8, columns=8, rows=1, width_m=64.0, depth_m=16.0, foundations=16)
    floors, _ = _building_floors([c], [], 16, 1)
    assert c.x_fnd == 3                                   # 1 edge + 2 west bay
    assert floors[0].slab_side_m / 8 == 3 + 8 + 1 + 2     # block ends at 11, + edge + east bay = 14


def _tall_block(key, building, height):
    b = _typed_block(key, building, 4)
    b.height_m = height
    return b


def test_a_floor_with_a_tall_building_is_double_height():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    # A floor keeps FLOOR_HEADROOM_M (1 m) over its tallest machine: 15 m fits 16 m,
    # 15.5 m does not.
    floors, warnings = _building_floors(
        [_tall_block("r", "Refinery", 30.0), _tall_block("a", "Assembler", 15.0),
         _tall_block("x", "Tall Thing", 15.5)], [], 16, 1)
    assert not warnings
    assert {f.group: f.height_m for f in floors} == {
        "Refinery": 32.0, "Assembler": 16.0, "Tall Thing": 32.0}


def test_a_building_taller_than_a_double_floor_is_warned():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    floors, warnings = _building_floors([_tall_block("t", "Tower", 40.0)], [], 16, 1)
    assert floors[0].height_m == 32.0
    assert any("Tower" in w and "40" in w for w in warnings)


def test_a_building_with_no_headroom_under_a_double_floor_is_warned_once():
    from satisfactory_mcp.domain.planning.layout import _building_floors

    # 32 m + 1 m headroom > 32: flagged, and once for the building, not per manifold.
    blocks = [_tall_block(f"c{i}", "Coal-Powered Generator", 32.0) for i in range(2)]
    _floors, warnings = _building_floors(blocks, [], 16, 1)
    tall = [w for w in warnings if "Coal-Powered Generator" in w]
    assert tall == ["Coal-Powered Generator is 32 m tall: no headroom under a 32 m double "
                    "floor (needs 33 m)"]
