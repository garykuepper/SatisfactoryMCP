"""factory_health: measured uptime, and why a stopped machine is stopped.

Every rule here was wrong before it was measured against the real save, so each test
pins the counter-example that corrected it.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.factories.health import (
    CONNECTION,
    FED,
    FLOW_RATE,
    HEAD_LIFT,
    JOINED,
    NOTHING,
    OK,
    OPEN,
    STATES,
    UNDETERMINED,
    assess,
)
from satisfactory_mcp.domain.world import headlift as H
from satisfactory_mcp.domain.world.headlift import Crest, HeadLift, head_lift
from satisfactory_mcp.domain.world.logistics import BY_ROLE, UNKNOWN, Link, build_physical_graph

pytestmark = pytest.mark.integration

WINDOW = 300.0


def _uptime(fraction: float) -> dict:
    return {
        "window_s": WINDOW,
        "produce_s": WINDOW * fraction,
        "cur_window_s": 100.0,
        "cur_produce_s": 0.0,
        "producing": fraction > 0,
    }


def _machine(name, recipe, *, uptime=None, buffers=None, **extra):
    record = {
        "instance": f"L:P.{name}",
        "cls": name.rsplit("_", 1)[0],
        "recipe": recipe,
        "pos": [0, 0, 0],
        **extra,
    }
    if uptime is not None:
        record["uptime"] = uptime
    if buffers is not None:
        record["buffers"] = buffers
    return record


class _Wires:
    """A ``FactoryGraph`` reduced to the one question `assess` asks it."""

    def __init__(self, wired):
        self.wired = set(wired)

    def neighbours(self, node, layer="material"):
        assert layer == "power", "health asks about electricity and nothing else"
        return ["a pole"] if node in self.wired else []


def _assess(game, machines=(), extractors=(), generators=(), graph=None, physical=None, heads=None):
    projection = {
        "machines": list(machines),
        "extractors": list(extractors),
        "generators": list(generators),
    }
    names = [r["instance"].rsplit(".", 1)[-1] for r in (*machines, *extractors, *generators)]
    return assess("test", names, game, projection, graph, physical, heads)


def _state_of(report, instance):
    return next(m.state for m in report.machines if m.instance == instance)


def test_starvation_is_a_missing_ingredient_not_an_empty_input(game):
    """THE correction. Black Powder takes Coal and Sulfur; the assembler that motivated
    this held 100 Sulfur and no Coal. An "is the input empty" test called it well-fed and
    left eight machines filed as unexplained stalls."""
    # The recipe CLASS is Gunpowder; only its display name is Black Powder.
    name = "Build_AssemblerMk1_C_1"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_Gunpowder_C",
                uptime=_uptime(0.0),
                buffers={
                    "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
                    "out": {"items": {}, "slots": 2},
                },
            )
        ],
    )
    assert _state_of(report, name) == "starved"
    entry = report.machines[0]
    assert entry.cause == ("Coal",), "the missing ingredient is the whole point"
    assert "Sulfur" not in entry.cause


def test_blocked_is_checked_before_starved(game):
    """A blocked machine's input backs up too. The sample reads input 100/100 Iron Ingot
    and output 199/200 Iron Plate -- testing the input first calls it well-fed and misses
    that nothing is taking its plates."""
    name = "Build_ConstructorMk1_C_2"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IronPlate_C",
                uptime=_uptime(0.0),
                buffers={
                    "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
                },
            )
        ],
    )
    assert _state_of(report, name) == "blocked"
    assert report.blocked_on["Iron Plate"] == 1


def test_a_nearly_full_stack_counts_as_backed_up(game):
    """199 of 200 is backed up. Demanding exactly 100% hides a bottleneck that has just
    ticked one item forward."""
    full = _machine(
        "Build_ConstructorMk1_C_3",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 199}, "slots": 2},
        },
    )
    half = _machine(
        "Build_ConstructorMk1_C_4",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 100}, "slots": 2},
        },
    )
    report = _assess(game, machines=[full, half])
    assert _state_of(report, "Build_ConstructorMk1_C_3") == "blocked"
    assert _state_of(report, "Build_ConstructorMk1_C_4") == "stalled"


def test_an_extractor_with_no_node_is_a_dead_node_not_starved(game):
    """A miner draws from its node and has no InputInventory, so an empty-input test
    reported every idle miner as starved. When mExtractableResource is ABSENT the miner
    is bound to nothing at all -- on the real save, three left behind when a game update
    removed their resource node."""
    name = "Build_MinerMk1_C_5"
    report = _assess(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_MinerMk1_C",
                "pos": [0, 0, 0],
                "uptime": _uptime(0.0),
            }
        ],
    )
    assert _state_of(report, name) == "dead node"
    assert report.machines[0].cause == ("no resource node",)


def test_an_extractor_with_an_untabled_node_is_not_dead(game):
    """A water pump's node IS set; it points at an FGWaterVolume that is not a
    purity-table key. That is a gap in our table, not a fault in the factory."""
    name = "Build_WaterPump_C_6"
    report = _assess(
        game,
        extractors=[
            {
                "instance": f"L:P.{name}",
                "cls": "Build_WaterPump_C",
                "pos": [0, 0, 0],
                "node": "L:P.FGWaterVolume42",
                "uptime": _uptime(1.0),
            }
        ],
    )
    assert _state_of(report, name) == "saturated"


def test_missing_produce_duration_is_a_real_zero(game):
    """UE omits SaveGame properties equal to their default, so an absent ProduceDuration
    means the machine produced for zero seconds -- not that the data is missing."""
    name = "Build_SmelterMk1_C_7"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IngotIron_C",
                uptime={"window_s": WINDOW, "cur_window_s": 10.0, "producing": False},
                buffers={
                    "in": {"items": {"Desc_OreIron_C": 50}, "slots": 1},
                    "out": {"items": {}, "slots": 1},
                },
            )
        ],
    )
    assert report.machines[0].uptime == 0.0
    assert _state_of(report, name) == "stalled"


def test_a_machine_with_no_monitor_is_unknown_not_zero(game):
    name = "Build_ConstructorMk1_C_8"
    report = _assess(game, machines=[_machine(name, "Recipe_IronPlate_C")])
    assert _state_of(report, name) == "unmonitored"
    assert report.machines[0].uptime is None
    assert report.mean_uptime is None, "an unknown must not be averaged in as a zero"


def test_paused_outranks_every_other_diagnosis(game):
    name = "Build_ConstructorMk1_C_9"
    report = _assess(
        game,
        machines=[
            _machine(
                name,
                "Recipe_IronPlate_C",
                uptime=_uptime(0.0),
                paused=True,
                buffers={
                    "in": {"items": {}, "slots": 1},
                    "out": {"items": {"Desc_IronPlate_C": 200}, "slots": 1},
                },
            )
        ],
    )
    assert _state_of(report, name) == "paused"


def test_states_are_ordered_worst_first_and_ok_is_a_subset(game):
    assert OK <= set(STATES)
    assert STATES.index("blocked") < STATES.index("saturated")
    assert STATES.index("dead node") < STATES.index("starved")


def test_worst_reports_only_what_needs_attention(game):
    good = _machine("Build_ConstructorMk1_C_10", "Recipe_IronPlate_C", uptime=_uptime(1.0))
    bad = _machine(
        "Build_ConstructorMk1_C_11",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = _assess(game, machines=[good, bad])
    assert [m.instance for m in report.worst()] == ["Build_ConstructorMk1_C_11"]
    assert report.mean_uptime == pytest.approx(0.5)


# ------------------------------------------------------------ power shards


def test_slug_yields_come_from_the_recipes_not_from_memory(game):
    """Blue 1, Yellow 2, Purple 5 are read off Power Shard (1)/(2)/(5). Hardcoding them
    would be exactly the game-knowledge guessing this project refuses elsewhere."""
    yields = {game.item_name(k): v for k, v in game.slug_yields().items()}
    assert yields == {"Blue Power Slug": 1.0, "Yellow Power Slug": 2.0, "Purple Power Slug": 5.0}


def test_the_synthetic_shard_recipe_is_not_treated_as_a_slug(game):
    """Synthetic Power Shard also makes shards, but from Time Crystal, Dark Matter
    Crystal, Quartz and Photonic Matter. That is a production chain, not something
    lying in a crate, so it must not inflate the craftable pool."""
    names = {game.item_name(k) for k in game.slug_yields()}
    assert "Quartz Crystal" not in names
    assert "Time Crystal" not in names
    assert all("Slug" in n for n in names)


def _budget(game, projection):
    from satisfactory_mcp.domain.world.state import WorldState

    return WorldState(projection=projection, game=game).shard_budget()


def _proj(depot=None, player=None, storage=None, machines=()):
    return {
        "depot": depot or {},
        "inventories": {"player": player or {}, "storage": storage or {}, "machine": {}},
        "machines": list(machines),
        "extractors": [],
        "generators": [],
    }


def test_slugs_in_the_depot_count_as_craftable_not_free(game):
    """The reference save holds 93 Blue, 58 Yellow and 39 Purple in the Dimensional
    Depot -- 404 shards against 22 already crafted. Reporting only the crafted pool
    understated what the player could overclock with by ~19x."""
    budget = _budget(
        game,
        _proj(
            depot={
                "Desc_Crystal_C": 93,
                "Desc_Crystal_mk2_C": 58,
                "Desc_Crystal_mk3_C": 39,
                "Desc_CrystalShard_C": 22,
            }
        ),
    )
    assert budget["free"] == 22
    assert budget["craftable"] == 404
    assert budget["potential"] == 426


def test_slugs_are_found_wherever_stock_looks(game):
    """Carried, in a storage container, or in the Depot -- all three are spendable, so
    all three count."""
    for place in ("player", "storage"):
        budget = _budget(game, _proj(**{place: {"Desc_Crystal_mk3_C": 4}}))
        assert budget["craftable"] == 20, place
        assert budget["by_place"], place


def test_shards_inside_machines_are_never_counted_as_free(game):
    """The trap this whole area exists to avoid: the 97 shards on the reference save are
    all in InventoryPotential components, so reading the machine bucket as stock
    overstates the free pool more than 4x."""
    projection = _proj()
    projection["inventories"]["machine"] = {"Desc_CrystalShard_C": 97}
    budget = _budget(game, projection)
    assert budget["free"] == 0
    assert budget["craftable"] == 0


def test_craftable_is_reported_apart_from_free(game):
    """Crafting is a manual step, so slugs are potential and must never be folded into
    a number the player reads as available now."""
    budget = _budget(game, _proj(depot={"Desc_Crystal_C": 10}))
    assert budget["free"] == 0
    assert budget["craftable"] == 10
    assert budget["potential"] == 10


# --------------------------------------------------------------- machines wired to nothing
#
# The save records no mHasPower and no mCircuitID -- 0 of 44,634 objects -- so "unpowered"
# is not knowable and is never claimed. A WIRE is: an actor either has a power edge or it
# does not, which makes "nothing at all reaches this machine" the one positive electrical
# fact in the file. `stalled` used to advise checking power because nothing here could.


def _fed(name, uptime=0.0):
    """A machine with input, room in its output, and not producing -- a bare stall."""
    return _machine(
        name,
        "Recipe_IronPlate_C",
        uptime=_uptime(uptime),
        buffers={
            "in": {"items": {"Desc_IronIngot_C": 100}, "slots": 1},
            "out": {"items": {"Desc_IronPlate_C": 1}, "slots": 2},
        },
    )


def test_a_stalled_machine_no_wire_reaches_says_so(game):
    dark = _fed("Build_ConstructorMk1_C_90")
    lit = _fed("Build_ConstructorMk1_C_91")
    report = _assess(game, machines=[dark, lit], graph=_Wires({"Build_ConstructorMk1_C_91"}))
    assert _state_of(report, "Build_ConstructorMk1_C_90") == "stalled"
    assert _state_of(report, "Build_ConstructorMk1_C_91") == "stalled"
    causes = {m.instance: m.cause for m in report.machines}
    assert causes["Build_ConstructorMk1_C_90"] == ("no power connection",)
    assert causes["Build_ConstructorMk1_C_91"] == (), "a wired stall has no such evidence"


def test_without_a_graph_nothing_is_called_unwired(game):
    """Absent evidence must not read as a finding: every caller that passes no graph --
    the whole of this module before this section -- would otherwise report every machine."""
    report = _assess(game, machines=[_fed("Build_ConstructorMk1_C_92")])
    assert report.unwired == []
    assert _state_of(report, "Build_ConstructorMk1_C_92") == "stalled"
    assert report.machines[0].cause == ()


def test_being_wired_to_nothing_is_reported_whatever_the_state_is(game):
    """It is not a state, and that is the design: on the reference world all eight machines
    wired to nothing are `no recipe`, `paused` or `unmonitored`, and NONE is stalled."""
    idle = _machine("Build_ConstructorMk1_C_93", "", uptime=_uptime(0.0))
    paused = _machine("Build_ConstructorMk1_C_94", "Recipe_IronPlate_C", paused=True)
    everything = {"Build_ConstructorMk1_C_93", "Build_ConstructorMk1_C_94"}
    report = _assess(game, machines=[idle, paused], graph=_Wires(set()))
    assert set(report.unwired) == everything
    assert _state_of(report, "Build_ConstructorMk1_C_93") == "no recipe"
    assert _state_of(report, "Build_ConstructorMk1_C_94") == "paused"


def test_a_fully_wired_factory_reports_none(game):
    report = _assess(
        game,
        machines=[_fed("Build_ConstructorMk1_C_95")],
        graph=_Wires({"Build_ConstructorMk1_C_95"}),
    )
    assert report.unwired == []


# ---- what feeds the input a starved machine lacks -------------------------


def _starved(name):
    """An assembler on Black Powder holding Sulfur and no Coal."""
    return _machine(
        name,
        "Recipe_Gunpowder_C",
        uptime=_uptime(0.0),
        buffers={
            "in": {"items": {"Desc_Sulfur_C": 100}, "slots": 2},
            "out": {"items": {}, "slots": 2},
        },
    )


class _Runs:
    """A ``PhysicalGraph`` reduced to the one question `assess` asks it."""

    def __init__(self, arriving):
        self.arriving = list(arriving)

    def feeds(self, actor):
        return list(self.arriving)


def _link(source, target, medium=ports.CONVEYOR, basis=BY_ROLE, pieces=1):
    return Link(source=source, target=target, medium=medium, basis=basis, pieces=pieces)


def test_a_starved_machine_names_what_feeds_the_input_it_lacks(game):
    name = "Build_AssemblerMk1_C_100"
    feeder = _machine(
        "Build_ConstructorMk1_C_101",
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 1}},
    )
    report = _assess(
        game,
        machines=[_starved(name), feeder],
        physical=_Runs([_link("Build_ConstructorMk1_C_101", name, pieces=7)]),
    )
    (feed,) = next(m for m in report.machines if m.instance == name).feeds
    assert (feed.item, feed.verdict) == ("Coal", FED)
    assert (feed.far, feed.pieces) == ("Build_ConstructorMk1_C_101", 7)
    assert feed.far_state == "starved", "the feeder's own health is the next thing to check"


def test_nothing_feeding_it_and_an_unjoined_run_are_different_findings(game):
    """The distinction the physical graph exists to keep. 24 runs on the reference world end
    at nothing, and reporting one of those as "no conduit feeds this" is a confident wrong
    claim rather than a finding."""
    empty = _assess(game, machines=[_starved("Build_AssemblerMk1_C_102")], physical=_Runs([]))
    (missing,) = empty.machines[0].feeds
    assert (missing.verdict, missing.far) == (NOTHING, "")

    torn = _assess(
        game,
        machines=[_starved("Build_AssemblerMk1_C_103")],
        physical=_Runs([_link(None, "Build_AssemblerMk1_C_103")]),
    )
    (open_end,) = torn.machines[0].feeds
    assert open_end.verdict == OPEN
    assert open_end.pieces == 1, "the run is real and measured; only its far end is unknown"


def test_a_fluid_ingredient_looks_at_pipes_and_a_solid_at_belts(game):
    """A run carries whatever is put on it, so the medium is the only separation the save
    supports -- a conveyor arriving cannot be delivering the missing Water."""
    name = "Build_OilRefinery_C_104"
    wet = _machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )
    report = _assess(
        game, machines=[wet], physical=_Runs([_link("Build_StorageContainerMk1_C_1", name)])
    )
    by_item = {f.item: f.verdict for f in report.machines[0].feeds}
    assert by_item["Water"] == NOTHING, "a conveyor cannot be delivering the water"
    assert by_item["Polymer Resin"] == FED


def test_an_undirected_run_is_not_reported_as_feeding(game):
    """A pipe between two fittings has no direction without the rates, so it JOINS the
    machine to the far end and the report must not claim which way it flows."""
    name = "Build_AssemblerMk1_C_105"
    report = _assess(
        game,
        machines=[_starved(name)],
        physical=_Runs([_link(name, "Build_PipelineJunction_C_9", basis=UNKNOWN)]),
    )
    (feed,) = report.machines[0].feeds
    assert feed.verdict == JOINED
    assert feed.far == "Build_PipelineJunction_C_9", "walked from the end that is not us"


def test_without_a_physical_graph_nothing_is_called_unfed(game):
    """Absent evidence must not read as a finding, on `unwired`'s terms."""
    report = _assess(game, machines=[_starved("Build_AssemblerMk1_C_106")])
    assert report.machines[0].state == "starved"
    assert report.machines[0].feeds == ()


def test_a_feeder_that_provably_makes_the_item_is_marked(game):
    """Which of two arriving belts brings the ingots is not in the save; which far end can
    produce them at all is."""
    name = "Build_ConstructorMk1_C_107"
    hungry = _machine(
        name,
        "Recipe_IronPlate_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 1}, "out": {"items": {}, "slots": 2}},
    )
    smelter = _machine(
        "Build_SmelterMk1_C_108",
        "Recipe_IngotIron_C",
        uptime=_uptime(1.0),
        buffers={"in": {"items": {"Desc_OreIron_C": 50}, "slots": 1}, "out": {"items": {}}},
    )
    report = _assess(
        game,
        machines=[hungry, smelter],
        physical=_Runs(
            [_link("Build_SmelterMk1_C_108", name), _link("Build_StorageContainerMk1_C_2", name)]
        ),
    )
    marked = {f.far: f.makes for f in report.machines[0].feeds}
    assert marked == {"Build_SmelterMk1_C_108": True, "Build_StorageContainerMk1_C_2": False}, (
        "a container is not established as making anything, and false must not read as 'does not'"
    )


# ------------------------------------------------- the plumbing manual's ladder, §24.5


def _heads(crests=(), unfed=()):
    return HeadLift(
        crests=tuple(crests),
        consumers=0,
        unfed_ports=tuple(unfed),
        networks=0,
        gas_networks=0,
        ambiguous_ports=0,
    )


def _crest(consumers, fluid="Desc_Water_C"):
    return Crest(
        fluid=fluid,
        crest_m=20.0,
        head_m=17.0,
        consumers=tuple(consumers),
        marginal=False,
        assumed=True,
        pos=(0.0, 0.0, 20.0),
    )


def _refinery(name):
    """A refinery on Residual Plastic holding neither of its inputs.

    Its ingredients are one solid and one fluid, which is what makes it the machine to test
    the ladder on: only the Water may ever carry a rung.
    """
    return _machine(
        name,
        "Recipe_ResidualPlastic_C",
        uptime=_uptime(0.0),
        buffers={"in": {"items": {}, "slots": 2}, "out": {"items": {}, "slots": 2}},
    )


def _rungs(report, instance):
    found = next(m for m in report.machines if m.instance == instance)
    return {f.item: f.rung for f in found.feeds}


def test_a_fluid_no_pipe_reaches_stops_at_connection(game):
    name = "Build_OilRefinery_C_200"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_StorageContainerMk1_C_1", name)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_pipe_whose_far_end_is_nothing_stops_at_connection(game):
    """A torn line is rung (1) even though a pipe of the right medium does arrive."""
    name = "Build_OilRefinery_C_201"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link(None, name, medium=ports.PIPE)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_network_no_source_reaches_is_connection_and_not_head_lift(game):
    """The rung (1) fact only the head-lift model can see: the run arrives from a real
    fitting, so the conduit graph is satisfied, and yet nothing anywhere puts fluid in it.

    This is the ten refineries of the owner's newest saves, and calling them a head-lift
    fault would be ten wrong answers to one unfinished pipe.
    """
    name = "Build_OilRefinery_C_202"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(unfed=[name]),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_fed_fluid_behind_a_crest_stops_at_head_lift(game):
    name = "Build_OilRefinery_C_203"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name)["Water"] == HEAD_LIFT


def test_a_crest_on_another_fluid_leaves_this_input_alone(game):
    """A machine takes several fluids; a crest names the one whose network it stands on."""
    name = "Build_OilRefinery_C_204"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name], fluid="Desc_LiquidOil_C")]),
    )
    assert _rungs(report, name)["Water"] == FLOW_RATE


def test_connection_wins_over_a_crest_behind_the_same_machine(game):
    """THE rule of the ladder. A machine can be both unplumbed and, on the model's reading,
    below a hill; the manual says answer the first rung and stop, because a pump is the wrong
    thing to build when no pipe arrives at all.
    """
    name = "Build_OilRefinery_C_205"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name)["Water"] == CONNECTION


def test_a_connected_fluid_the_model_clears_reaches_flow_rate(game):
    name = "Build_OilRefinery_C_206"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(),
    )
    assert _rungs(report, name)["Water"] == FLOW_RATE


def test_without_the_head_lift_model_nothing_is_called_a_flow_rate_problem(game):
    """Rung (3) is EARNED by ruling rung (2) out, and the manual's red box says attempting
    it first is the usual mistake. No model, no verdict -- on `unwired`'s terms."""
    name = "Build_OilRefinery_C_207"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
    )
    assert _rungs(report, name)["Water"] == UNDETERMINED


def test_a_solid_never_carries_a_rung_and_a_mixed_machine_reads_sensibly(game):
    """Head lift is not a thing that happens to Polymer Resin."""
    name = "Build_OilRefinery_C_208"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs([_link("Build_PipelineJunction_C_9", name, medium=ports.PIPE)]),
        heads=_heads(crests=[_crest([name])]),
    )
    assert _rungs(report, name) == {"Polymer Resin": UNDETERMINED, "Water": HEAD_LIFT}
    (machine,) = report.machines
    assert machine.cause == ("Polymer Resin", "Water (head lift)")


def test_one_ingredient_reached_by_several_runs_gets_one_rung(game):
    """The rung belongs to the ingredient, not to a run: two pipes into one input is one
    diagnosis, and rows disagreeing about it would be two."""
    name = "Build_OilRefinery_C_209"
    report = _assess(
        game,
        machines=[_refinery(name)],
        physical=_Runs(
            [
                _link("Build_PipelineJunction_C_9", name, medium=ports.PIPE),
                _link(None, name, medium=ports.PIPE),
            ]
        ),
        heads=_heads(crests=[_crest([name])]),
    )
    water = [f for f in report.machines[0].feeds if f.item == "Water"]
    assert len(water) == 2
    assert {f.rung for f in water} == {HEAD_LIFT}


def test_the_reference_world_puts_no_fluid_on_the_ladder_at_all(projection, game):
    """The calibration record, and it is a fact about the world rather than about the code.

    Across every save on the author's machine not one starved machine is short of a FLUID --
    a machine's fluid box is carried in its input inventory, so the ingredient is readable,
    and none of the 802 starved machines there is missing one. The ladder is correct and
    silent here, and this test fails the day that stops being true.
    """
    names = [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in projection.get(key, ())
    ]
    graph = build_graph(projection)
    report = assess(
        "all",
        names,
        game,
        projection,
        graph,
        build_physical_graph(projection, game),
        head_lift(projection, game, graph),
    )
    assert report.by_state["starved"] == 12
    assert [f.rung for m in report.machines for f in m.feeds if f.rung] == []


def test_the_same_machines_flip_from_flow_rate_to_head_lift_when_the_pumps_go_dark(
    projection, game
):
    """THE acceptance test for the ladder, on real geometry and both ways round.

    Take every generator's supplemental water away and 32 of them read starved of Water. As
    the world is actually wired the plumbing reaches all of them, so the answer is rung (3),
    the rates -- and cutting power to every pump moves the SAME 32 to rung (2) without one
    of them still being told to check its supply. That is the manual's red box, and it is
    what the silence in the test above is a verdict about.
    """
    thirsty = [
        dict(
            record,
            buffers={"fuel": {"items": {record["fuel"]: 100}, "slots": 2}},
            uptime=_uptime(0.0),
        )
        if record.get("fuel")
        else record
        for record in projection["generators"]
    ]
    patched = dict(projection, generators=thirsty)
    names = [
        record["instance"].rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for record in patched.get(key, ())
    ]
    graph = build_graph(projection)
    physical = build_physical_graph(projection, game)

    def rungs(heads):
        report = assess("all", names, game, patched, graph, physical, heads)
        assert report.by_state["starved"] == 44
        return Counter(
            rung for m in report.machines for rung in {f.rung for f in m.feeds if f.rung}
        )

    assert rungs(head_lift(projection, game, graph)) == {FLOW_RATE: 32}

    plumbing = H._build(projection, game, powered=set())
    reach, whence = H._spread(plumbing, H.MACHINE_MAX_HEAD_LIFT_M, True)
    cut = {n for n, _a in plumbing.sinks if n in H._fed(plumbing)} - set(reach)
    dark = dataclasses.replace(
        head_lift(projection, game, graph),
        crests=tuple(H._crests(plumbing, reach, whence, cut, marginal=False)),
    )
    assert len(dark.crests) == 5
    assert rungs(dark) == {HEAD_LIFT: 32}
