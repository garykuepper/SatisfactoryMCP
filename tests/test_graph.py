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


def test_expand_pulls_in_the_whole_belt_component(graph, game, projection):
    """Some factories are defined by what feeds them. The player's concrete setup is a
    miner into storage into one constructor -- no product or radius term describes it,
    but the belt component delimits it exactly."""
    picked = _sel(["product:Concrete"], graph, game, projection, expand=True)
    assert set(STEEL) <= set(picked), "the belt web reaches the foundries"
    assert ORPHAN not in picked, "an unconnected machine has no component to expand into"


def test_exclusions_are_applied_after_expanding(graph, game, projection):
    """Otherwise expansion would silently undo the exclusion that was the whole point
    of writing '-label:...'."""
    store = LabelStore(world_id="TESTWORLD")
    store.put("steel factory", STEEL)
    picked = _sel(
        ["product:Concrete", "-label:steel factory"], graph, game, projection, store, expand=True
    )
    assert not set(STEEL) & set(picked)


# ------------------------------------------------------------- structures


def _slab_projection():
    """Two platforms sharing a footprint at different heights, plus a detached one."""
    tiles = []
    for gx in range(3):
        for gy in range(3):
            tiles.append([0, gx * 800, gy * 800, 0])  # ground floor
            tiles.append([0, gx * 800, gy * 800, 1200])  # upper floor, 12 m up
    for gx in range(2):  # detached, 40 m east
        tiles.append([0, 4000 + gx * 800, 0, 0])
    ramp = [1, 3200, 0, 600]  # bridges nothing on its own
    return {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C", "Build_Ramp_8x4_01_C"],
            "instances": [*tiles, ramp],
        },
        "machines": [
            {
                "instance": "L:P.Build_FoundryMk1_C_1",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [800, 800, 100],
            },
            {
                "instance": "L:P.Build_FoundryMk1_C_2",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [800, 800, 1300],
            },
            {
                "instance": "L:P.Build_SmelterMk1_C_3",
                "recipe": "Recipe_IngotIron_C",
                "pos": [4000, 0, 100],
            },
            {
                "instance": "L:P.Build_SmelterMk1_C_4",
                "recipe": "Recipe_IngotIron_C",
                "pos": [90000, 90000, 0],
            },  # out in a field, on no foundation
        ],
        "extractors": [],
        "generators": [],
    }


def test_stacked_floors_are_one_structure():
    """A multi-storey factory is one build. Without this the tor factory reads as three
    platforms that merely share a footprint."""
    from satisfactory_mcp.graph.structure import build_structures

    sx = build_structures(_slab_projection())
    assert len(sx.slabs) == 2, [s.tiles for s in sx.slabs]
    upper = sx.slab_of["Build_FoundryMk1_C_2"]
    lower = sx.slab_of["Build_FoundryMk1_C_1"]
    assert upper == lower
    assert sx.slabs[upper].storeys > 1


def test_a_detached_platform_stays_detached():
    from satisfactory_mcp.graph.structure import build_structures

    sx = build_structures(_slab_projection())
    assert sx.slab_of["Build_SmelterMk1_C_3"] != sx.slab_of["Build_FoundryMk1_C_1"]


def test_ground_built_machines_belong_to_no_slab():
    """Two of the player's twelve factories are built straight on the ground, which is
    why slabs are a candidate signal and never the arbiter."""
    from satisfactory_mcp.graph.structure import build_structures

    sx = build_structures(_slab_projection())
    assert "Build_SmelterMk1_C_4" not in sx.slab_of
    assert all("Build_SmelterMk1_C_4" not in g for g in sx.groups())


def test_no_structures_block_degrades_to_empty_rather_than_raising():
    """Projections from schema 6 and earlier have no structures at all."""
    from satisfactory_mcp.graph.structure import build_structures

    sx = build_structures({"machines": []})
    assert sx.slabs == [] and sx.slab_of == {}
    assert sx.groups() == []


def test_walls_bridge_slabs_only_when_chained():
    """Two platforms joined by a run of walls. Asking whether any SINGLE wall touches
    both finds nothing -- the real shape is slab -> wall -> wall -> slab. On the
    reference save that distinction is 0 joins against 4."""
    from satisfactory_mcp.graph.structure import build_structures

    left = [[0, 0, 0, 0], [0, 800, 0, 0]]
    right = [[0, 3200, 0, 0], [0, 4000, 0, 0]]
    walls = [[1, 1400, 0, 0], [1, 2000, 0, 0], [1, 2600, 0, 0]]
    classes = ["Build_Foundation_8x1_01_C", "Build_Wall_8x4_01_C"]
    machines = [
        {"instance": f"L:P.Build_FoundryMk1_C_{i}", "recipe": "Recipe_IngotSteel_C", "pos": p}
        for i, p in enumerate([[0, 0, 100], [4000, 0, 100]])
    ]
    base = {"machines": machines, "extractors": [], "generators": []}

    apart = build_structures(
        {**base, "structures": {"classes": classes, "instances": [*left, *right]}}
    )
    assert len(apart.slabs) == 2

    joined = build_structures(
        {**base, "structures": {"classes": classes, "instances": [*left, *right, *walls]}}
    )
    assert len(joined.slabs) == 1, "a run of walls is a structural connection"
    assert len(set(joined.slab_of.values())) == 1


def test_slab_selector_uses_the_index_factory_map_prints():
    """Slabs are numbered by tile count; groups() is ordered by machine count. Indexing
    into the wrong one silently returns a different platform -- it once re-anchored the
    speedwire factory onto the aluminium site."""
    from satisfactory_mcp.graph.structure import build_structures

    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            # A big platform carrying one machine, and a small one carrying three.
            "instances": [[0, x * 800, 0, 0] for x in range(6)]
            + [[0, 40000 + x * 800, 0, 0] for x in range(2)],
        },
        "machines": [
            {
                "instance": "L:P.Build_FoundryMk1_C_1",
                "recipe": "Recipe_IngotSteel_C",
                "pos": [0, 0, 100],
            },
            *[
                {
                    "instance": f"L:P.Build_SmelterMk1_C_{i}",
                    "recipe": "Recipe_IngotIron_C",
                    "pos": [40000 + i * 100, 0, 100],
                }
                for i in range(3)
            ],
        ],
        "extractors": [],
        "generators": [],
    }
    sx = build_structures(projection)
    big, small = sx.slabs[0], sx.slabs[1]
    assert big.tiles > small.tiles
    # groups() puts the 3-machine platform first, so the two orderings disagree here.
    assert len(sx.groups()[0]) == 3
    assert len(sx.machines_on(0)) == 1, "slab 0 is the one with the most TILES"


# ---------------------------------------------------------------- cohere


def test_complete_linkage_refuses_to_chain(graph, game, projection):
    """THE reason this is not a threshold plus connected components. The steel and iron
    sites are belt-connected and 800 m apart; single linkage welds them through the
    concrete machines sitting between, complete linkage does not. Measured on the real
    save the same score scores F1 0.521 chained against 0.987 unchained."""
    from satisfactory_mcp.graph import cohere
    from satisfactory_mcp.graph.structure import build_structures

    props = cohere.propose(graph, game, projection, build_structures(projection))
    for p in props:
        assert not (set(STEEL) & set(p.machines) and set(IRON) & set(p.machines))


def test_the_span_cap_bounds_a_proposal(graph, game, projection):
    """The one load-bearing constant: removing it drops precision 1.000 -> 0.776."""
    import math

    from satisfactory_mcp.graph import cohere
    from satisfactory_mcp.graph.structure import build_structures

    pos = {
        r["instance"].rsplit(".", 1)[-1]: r["pos"] for r in projection["machines"] if r.get("pos")
    }
    props = cohere.propose(graph, game, projection, build_structures(projection), max_span_m=50.0)
    for p in props:
        pts = [pos[m][:2] for m in p.machines if m in pos]
        span = max((math.dist(a, b) for a in pts for b in pts), default=0.0) / 100.0
        assert span <= 50.0 + 1e-6, f"{p.machines} spans {span:.0f}m"


def test_every_machine_lands_in_exactly_one_proposal(graph, game, projection):
    from satisfactory_mcp.graph import cohere
    from satisfactory_mcp.graph.structure import build_structures

    props = cohere.propose(graph, game, projection, build_structures(projection))
    seen = [m for p in props for m in p.machines]
    assert sorted(seen) == sorted(graph.machines())
    assert len(seen) == len(set(seen))


def test_weakening_the_weights_only_ever_refines(graph, game, projection):
    """The ablation's real claim. Flattening every weight to 1 costs 0.025 F1 on the
    real save (0.986 -> 0.961) and takes 15 clusters to 20 -- it splits, it never merges.
    That is the direction that matters: precision stayed 1.000 under both. A partition
    that got COARSER as evidence got weaker would mean the score is not doing what it
    claims."""
    from satisfactory_mcp.graph import cohere
    from satisfactory_mcp.graph.structure import build_structures

    sx = build_structures(projection)
    base = [set(p.machines) for p in cohere.propose(graph, game, projection, sx)]
    flat = cohere.propose(graph, game, projection, sx, weights=dict.fromkeys(cohere.WEIGHTS, 1.0))
    for p in flat:
        assert any(set(p.machines) <= b for b in base), (
            f"{sorted(p.machines)} is not contained in any strongly-weighted cluster"
        )


def test_proposals_carry_the_evidence_that_made_them(graph, game, projection):
    from satisfactory_mcp.graph import cohere
    from satisfactory_mcp.graph.structure import build_structures

    props = cohere.propose(graph, game, projection, build_structures(projection))
    multi = [p for p in props if p.size > 1]
    assert multi, "expected at least one machine to join another"
    assert all(p.evidence for p in multi)
    assert set(multi[0].evidence) <= set(cohere.WEIGHTS)


def test_exclusive_dependents_are_absorbed_across_a_pipe_boundary():
    """The coal plant case. Its water pumps are 21-184 m away, inside the span cap, and
    94% of what their pipes reach is that plant -- but the plant runs on TWO separate
    pipe networks, so every pump against a generator in the other network scores
    negative and complete linkage takes the MINIMUM. One blind pair vetoes the merge.
    Exclusivity is a property of a cluster, not of a pair, so it needs a second pass."""
    from satisfactory_mcp.graph.cohere import attach_dependents
    from satisfactory_mcp.graph.model import Edge, FactoryGraph

    gens_a = [f"Build_GeneratorCoal_C_{i}" for i in range(3)]
    gens_b = [f"Build_GeneratorCoal_C_{10 + i}" for i in range(3)]
    pumps = [f"Build_WaterPump_C_{20 + i}" for i in range(2)]
    cls = {n: n.rsplit("_", 1)[0] for n in gens_a + gens_b + pumps}
    graph = FactoryGraph(cls=cls)
    # Pumps share a pipe network with gens_a only; gens_b is a second, separate network.
    for p in pumps:
        for g in gens_a:
            graph.material.append(Edge(a=p, b=g))
    for x, y in pairwise(gens_b):
        graph.material.append(Edge(a=x, b=y))

    plant = gens_a + gens_b
    out = attach_dependents([plant, list(pumps)], graph)
    assert len(out) == 1, "the pumps exist only to feed that plant"
    assert set(out[0]) == set(plant) | set(pumps)


def test_a_peer_is_not_absorbed_however_exclusive():
    """The size guard. Without it precision falls 1.000 -> 0.709, because two large
    factories that mostly feed each other get welded into one."""
    from satisfactory_mcp.graph.cohere import attach_dependents
    from satisfactory_mcp.graph.model import Edge, FactoryGraph

    left = [f"Build_SmelterMk1_C_{i}" for i in range(4)]
    right = [f"Build_ConstructorMk1_C_{10 + i}" for i in range(4)]
    cls = {n: n.rsplit("_", 1)[0] for n in left + right}
    graph = FactoryGraph(cls=cls)
    for a in left:
        for b in right:
            graph.material.append(Edge(a=a, b=b))  # 100% exclusive, both directions

    out = attach_dependents([list(left), list(right)], graph)
    assert len(out) == 2, "equals stay equals; only dependents are absorbed"


def test_name_hint_does_not_let_one_recipe_outvote_a_power_plant():
    """32 generators run no recipe. One absorbed concrete constructor must not rename
    the coal plant to 'Concrete'."""
    from collections import Counter

    from satisfactory_mcp.graph.identity import Candidate

    cand = Candidate(
        machines=[f"m{i}" for i in range(33)],
        source="test",
        products=Counter({"Concrete": 1}),
        buildings=Counter({"Build_GeneratorCoal_C": 32, "Build_ConstructorMk1_C": 1}),
    )
    hint = cand.name_hint()
    assert hint.startswith("32x GeneratorCoal")
    assert "Concrete" in hint, "the stray recipe is still worth mentioning, just not first"
