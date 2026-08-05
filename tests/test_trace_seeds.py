"""What ``trace_upstream`` accepts as a seed, and what it admits about the walk.

Its own parameter help lists a factory label FIRST, and both the label path and the
selector path raised ``TypeError`` -- ``resolve_factory`` hands back machine ids and they
were indexed as records. The live-save tests in ``test_trace.py`` never caught it because
they only ever pass a building name or one instance, so the seeds here are hermetic and
run in the default suite.
"""

from __future__ import annotations

import pytest

from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.mcp.tools import factories as ftools

SMELTER = "Build_SmelterMk1_C_1"
BELT = "Build_ConveyorBeltMk1_C_1"
CONSTRUCTOR = "Build_ConstructorMk1_C_1"


def _projection() -> dict:
    """A smelter belted into a constructor, with the connector roles that orient it."""
    actors = [SMELTER, BELT, CONSTRUCTOR]
    roles = ["Output1", "Input0", "ConveyorAny0", "ConveyorAny1"]
    return {
        "header": {"save_identifier": "TEST-trace-seeds", "session_name": "t"},
        "machines": [
            {
                "instance": f"L:P.{SMELTER}",
                "cls": "Build_SmelterMk1_C",
                "recipe": "Recipe_IngotIron_C",
                "pos": [0, 0, 0],
            },
            {
                "instance": f"L:P.{CONSTRUCTOR}",
                "cls": "Build_ConstructorMk1_C",
                "recipe": "Recipe_IronRod_C",
                "pos": [800, 0, 0],
            },
        ],
        "extractors": [],
        "generators": [],
        "graph": {
            "actors": actors,
            "roles": roles,
            "material": [[0, 1, 0, 2], [1, 2, 3, 1]],
            "power": [],
        },
    }


@pytest.fixture
def traced(game, monkeypatch) -> WorldState:
    st = WorldState(projection=_projection(), game=game)
    monkeypatch.setattr(ftools, "_state", lambda save=None, world=None: st)
    monkeypatch.setattr(ftools, "game", lambda: game)
    return st


def test_a_selector_is_a_seed(traced):
    """The path that crashed for every selector."""
    out = ftools.trace_upstream("building:Constructor")
    assert not out.startswith("! "), out
    assert "Smelter" in out


def test_a_factory_label_is_a_seed(traced):
    """The path the parameter help lists first, and the one that crashed hardest."""
    traced.labels.put("rod line", [CONSTRUCTOR])
    out = ftools.trace_upstream("rod line")
    assert "factory 'rod line' (1 machines)" in out
    assert "Smelter" in out


def test_an_instance_and_a_building_name_still_seed_it(traced):
    assert "Smelter" in ftools.trace_upstream(CONSTRUCTOR)
    assert "Smelter" in ftools.trace_upstream("Constructor")
