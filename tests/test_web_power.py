"""``/api/power``: every pole and tower, and the span of every wire between them.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture in ``conftest.py``,
or by building its own app around a hand-written projection -- so nothing in this file
spawns the sidecar, reads a ``.sav`` or needs the save directory to exist.
"""

from __future__ import annotations

import math

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web.app import create_app


def test_power_is_the_poles_and_the_span_of_every_wire(client, state):
    """The geometry half of a network whose connectivity has been here since schema 11."""
    body = client.get("/api/power").json()
    assert body["pole_count"] == 701
    assert body["wire_count"] == body["edge_count"] == len(state.projection["graph"]["power"])

    # Metres, like everything else that leaves this module, and both ends of every wire.
    for wire in body["wires"]:
        assert len(wire["a_m"]) == len(wire["b_m"]) == 3
    x = [w["a_m"][0] for w in body["wires"]]
    assert max(abs(v) for v in x) < 400_000 / 100, "centimetres reached the payload"

    # The poles are named, not spelled as engine ids, and the count is the mix the world has.
    names = {p["name"] for p in body["poles"]}
    assert "Power Pole Mk.1" in names
    assert sum(1 for p in body["poles"] if p["cls"] == "Build_PowerTowerPlatform_C") == 137


def test_a_wire_says_what_is_at_each_end_and_how_far_apart_they_are(client):
    """``from``/``to`` come off ``graph["power"]``, which is the one copy of the wiring."""
    body = client.get("/api/power").json()
    named = [w for w in body["wires"] if w["from"] and w["to"]]
    assert len(named) >= 1200, "almost every endpoint should resolve to a building"

    # The span is the straight line between the two published ends, in three dimensions.
    # Loosely, and the slack is the rounding: the server measures in centimetres and then
    # rounds six coordinates to a decimetre each, so a span recomputed from the ROUNDED
    # numbers can differ by a couple of them. Tightening this would be pinning the rounding.
    for wire in named[:200]:
        want = math.dist(wire["a_m"], wire["b_m"])
        assert wire["span_m"] == pytest.approx(want, abs=0.2)

    # A pole's connection count is a count off the edge list. 1,297 wires have 2,594 ends and
    # 1,991 of them land on a pole; the other 603 land straight on a machine, a generator or a
    # water pump, which is why the poles alone do not add up to twice the wires.
    assert sum(p["connections"] for p in body["poles"]) == 1_991


def test_a_pole_nothing_is_wired_to_reports_zero_rather_than_nothing(client):
    """2 of the reference world's 701 -- tower platforms built and never strung.

    Zero is a measurement here: the pole is in the geometry table and in no edge, which is
    exactly what an ``actor_index`` of -1 means. A null would read as "not recorded".
    """
    body = client.get("/api/power").json()
    unstrung = [p for p in body["poles"] if p["connections"] == 0]
    assert len(unstrung) == 2
    assert all(p["cls"] == "Build_PowerTowerPlatform_C" for p in unstrung)


def test_a_world_with_no_power_at_all_answers_with_an_empty_network(game):
    """A save from before schema 17, and a world nobody has wired, read the same way."""
    for projection in ({}, {"power": None}, {"power": {"poles": {}, "wires": []}}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            assert c.get("/api/power").json() == {
                "poles": [],
                "pole_count": 0,
                "wires": [],
                "wire_count": 0,
                "edge_count": 0,
            }


def test_a_wire_with_no_geometry_costs_that_span_and_not_the_join(game):
    """The one case the positional join has to survive: a null row in ``wires``.

    ``wires[i]`` is the span of ``graph["power"][i]``, so a wire that published no geometry
    must not renumber the ones after it -- the second wire below would otherwise be drawn
    between the first one's actors.
    """
    projection = {
        "graph": {
            "actors": ["Build_PowerPoleMk1_C_1", "Build_SmelterMk1_C_2", "Build_PowerPoleMk1_C_3"],
            "power": [[0, 1], [0, 2]],
        },
        "machines": [{"cls": "Build_SmelterMk1_C", "instance": "x.Build_SmelterMk1_C_2"}],
        "power": {
            "poles": {
                "classes": ["Build_PowerPoleMk1_C"],
                "instances": [[0, 100, 200, 300, 0.0, 0], [0, 400, 200, 300, 0.0, 2]],
            },
            "wires": [None, [100, 200, 1000, 400, 200, 1000]],
        },
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/power").json()
    assert (body["wire_count"], body["edge_count"]) == (1, 2)
    (wire,) = body["wires"]
    assert (wire["from"], wire["to"]) == ("Power Pole Mk.1", "Power Pole Mk.1")
    assert wire["span_m"] == 3.0
    assert [p["connections"] for p in body["poles"]] == [2, 1]


def test_power_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
    """The ``?save`` / ``?world`` contract every endpoint here shares."""
    asked: list[tuple] = []

    def loader(save=None, world=None):
        asked.append((save, world))
        if world == "nope":
            raise RuntimeError("no world matching 'nope'")
        return WorldState(projection={}, game=game)

    app = create_app(state_loader=loader, game_loader=lambda: game)
    with TestClient(app) as c:
        assert c.get("/api/power?world=Han%20Solo&save=x.sav").status_code == 200
        bad = c.get("/api/power?world=nope")
    assert asked[0] == ("x.sav", "Han Solo"), "the query never reached the loader"
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]
