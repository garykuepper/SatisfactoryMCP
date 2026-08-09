"""The physical logistics graph, and the role filter that keeps hypertubes out of it.

Two properties are measured on the committed projection rather than asserted in the
abstract: a hypertube is not a belt, and contracting the conduit runs out of
``graph["material"]`` leaves node-to-node links that the save STATES rather than links
geometry guesses. The counts here move whenever the fixture is re-cut, on
``test_reference_counts``'s terms.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.core.saveio import ports
from satisfactory_mcp.domain.factories.build import build_graph
from satisfactory_mcp.domain.world.logistics import (
    BY_NATURE,
    BY_ROLE,
    UNKNOWN,
    build_physical_graph,
)

#: Hypertube couplings in the reference projection. They are published in the same list as
#: the belts and are separated by role alone.
HYPERTUBE_EDGES = 126


def test_the_vocabulary_separates_the_three_media() -> None:
    assert ports.medium("ConveyorAny0") == ports.CONVEYOR
    assert ports.medium("Output1") == ports.CONVEYOR
    assert ports.medium("PipelineConnection0") == ports.PIPE
    assert ports.medium("Connection2") == ports.PIPE
    assert ports.medium("PipeHyperConnection0") == ports.HYPERTUBE
    assert ports.medium("PipeHyperStartConnection") == ports.HYPERTUBE
    # A role this table has never seen is unknown, never guessed into a medium.
    assert ports.medium("SomeFutureConnection") is None


def test_a_hypertube_role_is_not_matched_by_substring() -> None:
    """``PipeHyperConnection0`` contains ``Pipe``; the fluid classifier must not take it."""
    assert ports.medium("PipeHyperConnection0") != ports.PIPE
    assert ports.is_hypertube_edge("PipeHyperConnection0", "PipeHyperConnection1")
    assert not ports.is_hypertube_edge("PipelineConnection0", "PipelineConnection1")
    assert not ports.is_hypertube_edge("Output1", "ConveyorAny0")


def test_the_factory_graph_keeps_hypertubes_off_the_material_layer(projection) -> None:
    graph = build_graph(projection)
    assert len(graph.hyper) == HYPERTUBE_EDGES
    assert not any(ports.is_hypertube_edge(e.role_a, e.role_b) for e in graph.material), (
        "a hypertube on the material layer is a pedestrian route read as a belt"
    )


def test_every_material_edge_in_the_fixture_names_a_medium(projection) -> None:
    """A role the vocabulary cannot place would leave an edge silently unclassified."""
    roles = projection["graph"]["roles"]
    unplaced = sorted(r for r in roles if ports.medium(r) is None)
    assert not unplaced, f"connector roles this vocabulary does not know: {unplaced}"


def test_the_runs_contract_to_two_ended_links(projection, game) -> None:
    """The join is by actor identity, so almost every run names both of its ends."""
    graph = build_physical_graph(projection, game)
    assert len(graph.links) == 2198
    both_ended = [link for link in graph.links if link.target and link.source]
    assert len(both_ended) == 2174
    # What could not be joined, stated rather than guessed at.
    assert len(graph.dangling) == 24
    assert graph.orphan_runs == 2


def test_direction_is_declined_only_between_fittings(projection, game) -> None:
    """A pipe between two junctions has no direction without the rates; a belt always has
    one, because the machine end of it names itself an input or an output."""
    graph = build_physical_graph(projection, game)
    assert graph.undirected == 128
    undirected = [link for link in graph.links if link.basis == UNKNOWN and link.target]
    assert {link.medium for link in undirected} == {ports.PIPE}
    conveyors = [link for link in graph.links if link.medium == ports.CONVEYOR]
    assert all(link.basis in (BY_ROLE, BY_NATURE) for link in conveyors)


def test_an_undirected_link_is_walked_from_either_end(projection, game) -> None:
    """``other`` is the only safe accessor on an undirected link: it is indexed from both
    ends, so reading ``source`` after ``feeds`` can hand the caller its own node back."""
    graph = build_physical_graph(projection, game)
    undirected = next(
        link for link in graph.links if link.basis == UNKNOWN and link.source and link.target
    )
    assert link_in(graph.inbound.get(undirected.source), undirected)
    assert link_in(graph.inbound.get(undirected.target), undirected)
    assert undirected.other(undirected.source) == undirected.target
    assert undirected.other(undirected.target) == undirected.source


def link_in(links, wanted) -> bool:
    return any(link is wanted for link in links or ())


def test_no_hypertube_reaches_the_physical_graph(projection, game) -> None:
    graph = build_physical_graph(projection, game)
    assert {link.medium for link in graph.links} == {ports.CONVEYOR, ports.PIPE}


def test_a_machines_recipe_finds_its_feeders(projection, game) -> None:
    """The coverage measurement the graph is worth building for: of the machines with a
    recipe set, how many have a physical feeder for every ingredient."""
    graph = build_physical_graph(projection, game)
    with_recipe = fed = 0
    for record in projection["machines"]:
        recipe = game.recipes.get(record.get("recipe") or "")
        if recipe is None:
            continue
        with_recipe += 1
        short = record["instance"].rsplit(".", 1)[-1]
        arriving = {link.medium for link in graph.feeds(short)}
        wanted = {
            ports.PIPE
            if (game.items.get(flow.item) and game.items[flow.item].is_fluid)
            else ports.CONVEYOR
            for flow in recipe.ingredients
        }
        fed += wanted <= arriving
    assert (with_recipe, fed) == (426, 423)


def test_a_pipe_run_is_followable_and_a_belt_run_is_not(projection, game) -> None:
    """``ident`` is the id ``search_conduits`` prints. A pipe has one because the pipe table
    carries the actor the graph names it by; ``belts["segments"]`` carries no actor at all,
    and matching a chain geometrically is unique for only 58% of runs, so a belt link says
    nothing rather than naming the wrong chain."""
    graph = build_physical_graph(projection, game)
    named = {link.medium for link in graph.links if link.ident}
    assert named == {ports.PIPE}
    pipes = [link for link in graph.links if link.medium == ports.PIPE]
    assert all(link.ident.startswith("pipe:") for link in pipes)
    rows = {int(link.ident.split(":")[1]) for link in pipes}
    assert len(rows) == len(pipes), "two runs sharing a row would send both to one piece"


@pytest.mark.integration
def test_the_live_world_contracts_too(live) -> None:
    """The reference fixture is one save; the shape has to hold on whatever is newest."""
    graph = build_physical_graph(live.projection, live.game)
    assert graph.links, "a world with belts in it contracts to no links at all"
    joined = sum(1 for link in graph.links if link.source and link.target)
    assert joined / len(graph.links) > 0.95
