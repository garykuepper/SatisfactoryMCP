"""Three gaps a large oil-power planning session found, and what each cost.

Each test pins the thing that was wrong, not the thing that now works, because the
failures were all quiet: a ban that did nothing, a constraint that was never modelled,
and a build instruction for a machine at 2% clock.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.planning.optimize import build_processes, solve
from satisfactory_mcp.planning.scenario import build_scenario

pytestmark = pytest.mark.integration

SPIRE = ["region:Spire Coast"]


# ------------------------------------------- 1. excluding a generator burn


def test_a_generator_burn_can_be_excluded_by_the_label_the_table_prints(game, state):
    """Generator burn is synthesised from building data, not from Docs.json -- there is
    no recipe object, so exclude_recipes could never match it. "Coal-Powered Generator
    on Coal" hit nothing, and the only recourse was deleting rows by hand and hoping
    the subgraph was isolated."""
    req = build_scenario(
        game,
        state,
        sources=SPIRE,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal-Powered Generator on Coal"],
    )
    assert not req.recipe_errors, req.recipe_errors
    labels = {p.label for p in build_processes(req.scenario)}
    assert "Coal-Powered Generator on Coal" not in labels
    assert any("Coal-Powered Generator" in x for x in labels), "only the coal burn goes"


def test_the_building_name_alone_bans_every_fuel_it_burns(game, state):
    req = build_scenario(
        game,
        state,
        sources=SPIRE,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal-Powered Generator"],
    )
    labels = {p.label for p in build_processes(req.scenario)}
    assert not any("Coal-Powered Generator" in x for x in labels)


def test_a_fuel_name_bans_burning_it_even_though_it_also_names_recipes(game, state):
    """Recipe-first precedence was wrong: "Coal" matches Biocoal/Charcoal/Compacted
    Coal, so under it the pattern never reached the generators and "do not burn coal
    here" quietly did the opposite. Every pattern is now offered to both."""
    req = build_scenario(
        game,
        state,
        sources=SPIRE,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Coal"],
    )
    labels = {p.label for p in build_processes(req.scenario)}
    assert not any(x.endswith("on Coal") for x in labels)
    assert any("Compacted Coal" in name for name in req.excluded), "recipes banned too"


def test_a_pattern_matching_neither_still_refuses(game, state):
    """The one behaviour the reporter asked to keep: it warned rather than silently
    ignoring the argument."""
    req = build_scenario(
        game,
        state,
        sources=SPIRE,
        objective="max_mw",
        exports=["MW"],
        exclude_recipes=["Nonsense Thing"],
    )
    assert any("nothing matches" in e for e in req.recipe_errors)


# ----------------------------------------------------- 2. water siting


def test_water_extractors_can_be_capped_to_what_a_site_holds(game, state):
    """Water was the binding constraint on the real build and the model had no opinion:
    a plan wanted 105 extractors and 12,400 m3/min -- more than its Fuel -- on a
    platform whose perimeter fits about 27."""
    kw = dict(sources=SPIRE, objective="max_mw", exports=["MW"])
    free = solve(build_scenario(game, state, **kw).scenario)
    capped = solve(build_scenario(game, state, water_extractors=5, **kw).scenario)
    assert free.ok and capped.ok

    def pumps(sol):
        return sum(
            p["machines"] for p in sol.processes if p.get("building_id") == "Build_WaterPump_C"
        )

    assert pumps(capped) <= 5
    assert pumps(capped) < pumps(free)
    assert capped.net_mw < free.net_mw, "a real constraint must cost something"


def test_the_default_water_cap_is_an_assumption_not_a_measurement(game):
    """It is the only number in the constants register with no data behind it, and it
    is high enough not to bind -- which makes reading it as capacity dangerous."""
    from satisfactory_mcp.docs.constants import WATER_EXTRACTOR_CAP_ASSUMED

    assert WATER_EXTRACTOR_CAP_ASSUMED >= 100


def test_a_water_pump_reports_its_resource_rather_than_a_question_mark(game, state):
    """A Water Extractor sits on an FGWaterVolume, which is not a node and has no
    purity, so both columns read "?" and a working pump looked broken."""
    from satisfactory_mcp.graph.query import build_view

    pumps = [
        r["instance"].rsplit(".", 1)[-1]
        for r in state.projection.get("extractors", ())
        if "WaterPump" in r["cls"]
    ]
    if not pumps:
        pytest.skip("this save has no water extractors")
    view = build_view("probe", pumps, state.graph, game, state.projection)
    resources = {row[1] for row in view.nodes}
    assert "?" not in resources
    assert "Water" in resources


# --------------------------------------------- 3. degenerate clock rows


def test_clock_modes_of_one_node_set_collapse_to_a_single_row(game, state):
    """Offering a node set at several clocks makes one column per mode sharing a node
    cap, so the LP may split arbitrarily: 0.615 machine-equivalents at 100% plus 0.0201
    at 150%. That printed as two rows with an IDENTICAL label, the second a whole miner
    at 2% clock, which reads as a real build instruction."""
    req = build_scenario(
        game,
        state,
        sources=SPIRE,
        objective="max_mw",
        exports=["MW"],
        extractor_clocks=[1, 1.5, 2, 2.5],
    )
    sol = solve(req.scenario)
    assert sol.ok
    built = [p for p in sol.processes if p["machines"]]
    labels = [p["label"] for p in built]
    assert len(labels) == len(set(labels)), f"duplicate rows: {labels}"


def test_folding_preserves_what_was_extracted(game, state):
    """Extraction is linear in clock, so pooling v*clock and re-emitting at one mode is
    exact. If it were not, the fold would quietly change the plan."""
    kw = dict(sources=SPIRE, objective="max_mw", exports=["MW"])
    one = solve(build_scenario(game, state, **kw).scenario)
    many = solve(build_scenario(game, state, extractor_clocks=[1], **kw).scenario)
    assert one.ok and many.ok
    assert one.net_mw == pytest.approx(many.net_mw, rel=1e-6)
