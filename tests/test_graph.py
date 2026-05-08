"""The factory graph, selectors and labels.

Built on a hand-written projection rather than the committed save fixture, which
predates the graph field and whose source autosave has since rotated. The synthetic
world is a miniature of the real one and is shaped around the two measured failures
this system exists to fix:

* a **grown-together base** -- everything belt-connected, so material components
  cannot tell a steel site from an iron site;
* an **over-collecting product** -- Concrete made in three places, only one of which
  the player calls "the concrete setup".
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from satisfactory_mcp.graph import identity
from satisfactory_mcp.graph.build import build_graph, class_of
from satisfactory_mcp.graph.labels import Label, LabelStore
from satisfactory_mcp.graph.select import SelectorError, select_machines

pytestmark = pytest.mark.integration

# Two sites 800 m apart, wired into one belt web and one power grid.
STEEL = [f"Build_FoundryMk1_C_{100 + i}" for i in range(4)]
IRON = [f"Build_SmelterMk1_C_{200 + i}" for i in range(4)]
STEEL_CONCRETE = [f"Build_ConstructorMk1_C_{300 + i}" for i in range(3)]
BASE_CONCRETE = ["Build_ConstructorMk1_C_400"]
ORPHAN = "Build_AssemblerMk1_C_500"  # built, wired to nothing at all
POLE = "Build_PowerPole_C_600"
TOWER = "Build_PowerTower_C_700"
OUTPOST = ["Build_SmelterMk1_C_800"]
OUTPOST_POLE = "Build_PowerPole_C_810"


def _machine(name: str, recipe: str, x: float, y: float) -> dict:
    return {
        "instance": f"Persistence_Level:PersistentLevel.{name}",
        "recipe": recipe,
        "pos": [x, y, 0.0],
    }


@pytest.fixture(scope="module")
def projection() -> dict:
    machines = []
    for i, m in enumerate(STEEL):
        machines.append(_machine(m, "Recipe_IngotSteel_C", -100_000 + i * 1_000, -120_000))
    for i, m in enumerate(IRON):
        machines.append(_machine(m, "Recipe_IngotIron_C", -20_000 + i * 1_000, -120_000))
    for i, m in enumerate(STEEL_CONCRETE):
        machines.append(_machine(m, "Recipe_Concrete_C", -100_000 + i * 1_000, -121_000))
    machines.append(_machine(BASE_CONCRETE[0], "Recipe_Concrete_C", -20_000, -121_000))
    machines.append(_machine(ORPHAN, "Recipe_Wire_C", -20_000, -122_000))
    machines.append(_machine(OUTPOST[0], "Recipe_IngotIron_C", 500_000, 500_000))

    actors = [*STEEL, *IRON, *STEEL_CONCRETE, *BASE_CONCRETE, POLE, TOWER, *OUTPOST, OUTPOST_POLE]
    index = {a: i for i, a in enumerate(actors)}
    roles = ["Output1", "Input1"]

    # One belt web spanning both sites: this is what defeats material components.
    chain = [*STEEL, *STEEL_CONCRETE, *IRON, *BASE_CONCRETE]
    material = [[index[a], index[b], 0, 1] for a, b in pairwise(chain)]

    # One pole feeds everything in the base; a tower reaches the outpost's own pole.
    power = [[index[m], index[POLE]] for m in chain]
    power += [[index[POLE], index[TOWER]], [index[TOWER], index[OUTPOST_POLE]]]
    power += [[index[OUTPOST[0]], index[OUTPOST_POLE]]]

    return {
        "header": {"save_identifier": "TESTWORLD", "session_name": "Test"},
        "machines": machines,
        "extractors": [],
        "generators": [],
        "graph": {"actors": actors, "roles": roles, "material": material, "power": power},
    }


@pytest.fixture(scope="module")
def graph(projection):
    return build_graph(projection)


# ---------------------------------------------------------------- model


def test_class_of_strips_the_instance_id_but_not_the_class_suffix():
    assert class_of("Build_ConstructorMk1_C_2147441119") == "Build_ConstructorMk1_C"
    # No trailing number: leave it alone rather than eating "_C".
    assert class_of("Build_ConstructorMk1_C") == "Build_ConstructorMk1_C"


def test_a_machine_wired_to_nothing_is_still_a_node(graph):
    """The interned actor list comes from EDGES, so an isolated machine would vanish
    -- and an isolated machine is precisely what a coverage report must surface."""
    assert ORPHAN in graph.cls
    assert ORPHAN in graph.machines()


def test_poles_and_towers_are_not_machines(graph):
    assert graph.kind(POLE) == "pole"
    assert graph.kind(TOWER) == "tower"
    assert graph.towers() == {TOWER}
    assert POLE not in graph.machines()


def test_material_components_cannot_split_a_grown_together_base(graph):
    """The premise of the whole design: one belt web means one component, however
    many distinct factories the player built inside it."""
    comps = graph.machine_components("material")
    biggest = max(comps, key=len)
    assert set(STEEL) <= set(biggest)
    assert set(IRON) <= set(biggest)


def test_dropping_towers_separates_the_outpost(graph):
    with_towers = identity.bases.__wrapped__ if hasattr(identity.bases, "__wrapped__") else None
    assert with_towers is None  # bases() is plain; guard against it growing a cache
    islands = identity.bases(graph)
    assert len(islands) == 2, islands
    assert set(OUTPOST) in [set(i) for i in islands]
    # Without the skip, the tower welds them into one.
    assert len(graph.machine_components("power")) == 1


# ------------------------------------------------------------- identity


def test_product_clusters_split_concrete_by_position(graph, game, projection):
    """17 machines make Concrete on the real save; only one is "the concrete setup"."""
    clusters = identity.product_clusters(graph, game, projection, ["Concrete"])
    assert [c.size for c in clusters] == [3, 1]
    assert set(clusters[0].machines) == set(STEEL_CONCRETE)
    assert set(clusters[1].machines) == set(BASE_CONCRETE)


def test_name_hint_falls_back_to_buildings_when_there_is_no_recipe(graph, game, projection):
    """Generators run no recipe, so a coal plant would otherwise render blank."""
    cand = identity.describe([TOWER], graph, game, projection, "test")
    assert cand.name_hint().startswith("1x ")


def test_unassigned_is_every_machine_no_label_covers(graph):
    loose = identity.unassigned(graph, set(STEEL))
    assert ORPHAN in loose
    assert not set(STEEL) & set(loose)


# ------------------------------------------------------------- selectors


def _sel(terms, graph, game, projection, store=None, **kw):
    return select_machines(terms, graph, game, projection, store, **kw)


def test_terms_are_anded(graph, game, projection):
    picked = _sel(["product:Concrete", "near:-1000,-1210@100"], graph, game, projection)
    assert set(picked) == set(STEEL_CONCRETE)


def test_commas_inside_a_term_are_ored(graph, game, projection):
    picked = _sel(["product:Steel Ingot,Iron Ingot"], graph, game, projection)
    assert set(picked) == set(STEEL) | set(IRON) | set(OUTPOST)


def test_a_leading_minus_excludes(graph, game, projection):
    picked = _sel(["product:Concrete", "-near:-1000,-1210@100"], graph, game, projection)
    assert set(picked) == set(BASE_CONCRETE)


def test_split_keeps_only_the_largest_site(graph, game, projection):
    picked = _sel(["product:Concrete"], graph, game, projection, split=True)
    assert set(picked) == set(STEEL_CONCRETE)


def test_building_matches_the_display_name_not_only_the_class(graph, game, projection):
    assert set(_sel(["building:Foundry"], graph, game, projection)) == set(STEEL)
    assert set(_sel(["building:Build_FoundryMk1_C"], graph, game, projection)) == set(STEEL)


def test_near_is_in_metres(graph, game, projection):
    """The save stores centimetres and every tool in this MCP quotes metres. Getting
    this wrong silently returns everything or nothing."""
    # The foundries sit at -1000,-1200 m and every 10 m eastward from there.
    assert set(_sel(["building:Foundry", "near:-1000,-1200@50"], graph, game, projection)) == set(
        STEEL
    )
    assert _sel(["building:Foundry", "near:-1000,-1200@15"], graph, game, projection) == sorted(
        STEEL[:2]
    )
    # Read as centimetres this would still match everything; read as metres it does not.
    assert _sel(["building:Foundry", "near:-1000,-1200@5"], graph, game, projection) == [STEEL[0]]


def test_selectors_explain_themselves_when_they_fail(graph, game, projection):
    with pytest.raises(SelectorError, match="nothing is making"):
        _sel(["product:Turbo Motor"], graph, game, projection)
    with pytest.raises(SelectorError, match="not a selector"):
        _sel(["steel factory"], graph, game, projection)
    with pytest.raises(SelectorError, match="out of range"):
        _sel(["base:99"], graph, game, projection)
    with pytest.raises(SelectorError, match="add something to start from"):
        _sel(["-product:Concrete"], graph, game, projection)


def test_label_and_near_label_resolve_through_the_store(graph, game, projection):
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    assert set(_sel(["label:steel factory"], graph, game, projection, store)) == set(STEEL)
    near = _sel(["product:Concrete", "near:steel factory@100"], graph, game, projection, store)
    assert set(near) == set(STEEL_CONCRETE)


# ---------------------------------------------------------------- labels


def test_recall_survives_growth_where_jaccard_would_not():
    """The reason for recall. A factory that doubles in size is still that factory."""
    label = Label(id="steel", name="steel", anchors=list(STEEL))
    grown = set(STEEL) | set(STEEL_CONCRETE) | set(IRON)
    assert label.recall(grown) == 1.0
    jaccard = len(set(STEEL) & grown) / len(set(STEEL) | grown)
    assert jaccard < 0.5, "Jaccard would have dropped this below the match threshold"


def test_recall_degrades_gracefully_as_machines_are_removed():
    label = Label(id="steel", name="steel", anchors=list(STEEL))
    assert label.recall(set(STEEL[:3])) == 0.75
    assert label.recall(set(STEEL[:1])) == 0.25
    assert label.recall(set()) == 0.0


def test_review_reports_shrinkage_without_acting_on_it():
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel", list(STEEL))
    store.put("iron", list(IRON))
    issues = {d["name"]: d for d in store.review(set(STEEL[:1]) | set(IRON))}
    assert "iron" not in issues, "intact labels must not be reported"
    assert issues["steel"]["status"] == "needs confirmation"
    assert issues["steel"]["missing"] == 3
    # The label is still there: reporting is not deleting.
    assert store.find("steel") is not None


def test_put_re_anchors_an_existing_label_rather_than_duplicating_it():
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    store.put("Steel Factory", STEEL + STEEL_CONCRETE)
    assert len(store.labels) == 1
    assert len(store.find("steel").anchors) == len(STEEL) + len(STEEL_CONCRETE)


def test_labels_round_trip_through_disk(tmp_path, monkeypatch):
    from satisfactory_mcp import config
    from satisfactory_mcp.graph import labels as labels_mod

    monkeypatch.setattr(labels_mod.config, "labels_dir", lambda: tmp_path)
    assert config is not None
    store = LabelStore(world_id="TEST_WORLD/1", session_name="Test")
    label = store.put("steel factory", STEEL, notes="ingots")
    label.centroid = (-100_000.0, -120_000.0)
    store.save()

    again = LabelStore.load("TEST_WORLD/1")
    assert [x.name for x in again.labels] == ["steel factory"]
    assert again.labels[0].anchors == sorted(STEEL)
    assert again.labels[0].notes == "ingots"
    assert again.labels[0].centroid == (-100_000.0, -120_000.0)


def test_label_files_are_not_written_into_the_cache():
    """cache_prune deletes the whole cache tree; a name the player typed is not
    regenerable and must not live there."""
    from satisfactory_mcp import config

    assert config.cache_dir() not in config.labels_dir().parents
    assert config.labels_dir() != config.cache_dir()
