"""find_resource_node: nearest nodes to a point, and how that point is resolved.

The location grammar is the part worth pinning. Accepting a factory name is the reason
the tool exists in this form -- "the nearest free coal to the coal powerplant" is the
question actually asked, and hand-copying a centroid out of another tool's output is how
the wrong coordinate gets used.
"""

from __future__ import annotations

import math

import pytest

from satisfactory_mcp import server as srv
from satisfactory_mcp.graph.labels import LabelStore
from satisfactory_mcp.save.state import WorldState

pytestmark = pytest.mark.integration


class _Fake(WorldState):
    """A WorldState with labels we control, rather than whatever is on this machine."""

    def __init__(self, projection, game, labels):
        super().__init__(projection=projection, game=game)
        self._labels = labels

    @property
    def labels(self):  # type: ignore[override]
        return self._labels


def _state_with_label(game, name="probe", positions=((1000.0, 2000.0), (3000.0, 4000.0))):
    machines = [
        {
            "instance": f"L:P.Build_SmelterMk1_C_{i}",
            "cls": "Build_SmelterMk1_C",
            "recipe": "Recipe_IngotIron_C",
            "pos": [x, y, 0.0],
        }
        for i, (x, y) in enumerate(positions)
    ]
    projection = {"machines": machines, "extractors": [], "generators": [], "players": []}
    store = LabelStore(world_id="TEST")
    store.put(name, [m["instance"].rsplit(".", 1)[-1] for m in machines])
    return _Fake(projection, game, store)


def test_a_coordinate_is_read_as_metres(game):
    """Every coordinate in this MCP is quoted in metres; the save stores centimetres.
    Getting this wrong silently searches 100x too far away."""
    origin, where = srv._origin_for(_state_with_label(game), "-1069,-1273")
    assert origin == (-106_900.0, -127_300.0)
    assert where == "-1069,-1273"


def test_a_factory_name_resolves_to_its_centroid(game):
    st = _state_with_label(game)
    origin, where = srv._origin_for(st, "probe")
    assert origin == (2000.0, 3000.0)
    assert where == "probe"


def test_an_unknown_location_lists_what_is_known(game):
    st = _state_with_label(game, name="steel factory")
    with pytest.raises(ValueError, match="steel factory"):
        srv._origin_for(st, "nowhere")


def test_a_bad_coordinate_is_rejected_rather_than_guessed(game):
    with pytest.raises(ValueError, match="x,y pair"):
        srv._origin_for(_state_with_label(game), "12,north")


def test_me_needs_a_player_pawn(game):
    st = _state_with_label(game)
    with pytest.raises(ValueError, match="no player pawn"):
        srv._origin_for(st, "me")


def test_nodes_come_back_nearest_first(game):
    """The whole point of the tool: search_resource_nodes sorts by yield instead."""
    out = srv.find_resource_node("0,0", resource="Coal", limit=8)
    assert "! " not in out.splitlines()[0]
    distances = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) > 2 and parts[2].endswith("m") and parts[2][:-1].isdigit():
            distances.append(int(parts[2][:-1]))
    assert distances, out
    assert distances == sorted(distances), distances


def test_distance_is_measured_from_the_given_origin(game):
    """A node's reported distance must match its reported coordinate."""
    out = srv.find_resource_node("0,0", resource="Coal", limit=3)
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) > 4 and parts[2].endswith("m") and parts[2][:-1].isdigit():
            x_m, y_m = (float(v) for v in parts[3].split(","))
            assert int(parts[2][:-1]) == pytest.approx(math.dist((x_m, y_m), (0, 0)), abs=2)


def test_only_unused_hides_tapped_nodes(game):
    everything = srv.find_resource_node("0,0", resource="Coal", limit=25)
    free_only = srv.find_resource_node("0,0", resource="Coal", limit=25, only_unused=True)
    assert "tapped" in everything
    assert "tapped" not in free_only


def test_an_unknown_resource_is_an_error_not_an_empty_table(game):
    assert srv.find_resource_node("0,0", resource="Unobtanium").startswith("! no resource")
