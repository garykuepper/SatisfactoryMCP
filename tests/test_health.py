"""factory_health: measured uptime, and why a stopped machine is stopped.

Every rule here was wrong before it was measured against the real save, so each test
pins the counter-example that corrected it.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.factories.health import OK, STATES, assess

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


def _assess(game, machines=(), extractors=(), generators=()):
    projection = {
        "machines": list(machines),
        "extractors": list(extractors),
        "generators": list(generators),
    }
    names = [r["instance"].rsplit(".", 1)[-1] for r in (*machines, *extractors, *generators)]
    return assess("test", names, game, projection)


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
