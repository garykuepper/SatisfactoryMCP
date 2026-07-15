"""The web adapter's JSON surface, driven off the committed fixture projection.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders -- through the ``client`` fixture, which lives in
``conftest.py`` now that more than one module needs it -- so nothing in this file spawns
the sidecar, reads a ``.sav`` or needs the save directory to exist. The one exception is
the watcher test, which needs a directory precisely so it can be pointed at an empty one.

**This file is being emptied**, one concern at a time, alongside the module it covers:
``/api/worlds`` and ``/api/summary`` are in ``test_web_world.py``, ``/api/nodes`` in
``test_web_nodes.py``, ``/api/inspect`` in ``test_web_inspect.py``, ``/api/regions`` in
``test_web_regions.py``, the base map in ``test_web_tiles.py`` and its agreement with the
two generators in ``test_map_generators.py``. What is left here is whatever
``interfaces/web/api.py`` still serves.
"""

from __future__ import annotations

import asyncio
import json
import math
import types

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi import Request
from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.footprint import FOUNDATION_M
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web import api as web_api
from satisfactory_mcp.interfaces.web import terrain as web_terrain
from satisfactory_mcp.interfaces.web.app import STATIC_DIR, create_app


def test_machines_split_by_kind_and_name_their_buildings(client, state):
    body = client.get("/api/machines").json()
    assert set(body) == {"machines", "extractors", "generators"}
    for kind in body:
        assert len(body[kind]) == len(state.projection[kind])
    row = body["machines"][0]
    assert set(row) == {
        "instance_leaf",
        "cls",
        "name",
        "x_m",
        "y_m",
        "z_m",
        "recipe",
        "recipe_name",
        "clock",
        "paused",
        "yaw",
        "w_m",
        "l_m",
        "h_m",
    }
    assert row["name"] != row["cls"], "the class id was not joined to a building name"
    assert "." not in row["instance_leaf"]


def test_a_machine_carries_the_height_a_top_down_map_cannot_draw(client):
    """``h_m`` is the third side of the clearance box, and it is the floor view's evidence.

    All three sides come from one ``mClearanceData`` and go null together, so a row with a
    width and no height would mean the extractor had read two thirds of a measurement.
    """
    rows = [row for kind in client.get("/api/machines").json().values() for row in kind]
    for row in rows:
        assert (row["h_m"] is None) == (row["w_m"] is None) == (row["l_m"] is None)
    tall = {row["name"]: row["h_m"] for row in rows if row["h_m"] and row["h_m"] >= 12}
    # The case the floor view exists for: taller than this world's 12 m storey module, so
    # it is physically through the deck above and only the deck below can say so.
    assert tall.get("Refinery") == 15.0, tall


def test_machine_popup_rows_never_degrade_to_engine_ids(client):
    """The popup teaches one vocabulary. A recipe resolves to its docs name; a building
    the docs dump has no entry for (both biomass burners here) still comes back as words
    rather than as ``Build_GeneratorIntegratedBiomass_C``."""
    body = client.get("/api/machines").json()
    rows = [row for kind in body for row in body[kind]]
    with_recipe = [r for r in rows if r["recipe"]]
    assert with_recipe
    for r in with_recipe:
        assert r["recipe_name"], r
        assert not r["recipe_name"].startswith("Recipe_"), r
    for r in rows:
        assert r["name"], r
        assert not str(r["name"]).startswith("Build_"), r


def test_machines_carry_their_own_footprint_so_the_map_can_draw_true_size(client, state):
    """A Manufacturer is not a Constructor, and the map may not draw them the same.

    The numbers are the docs dump's own clearance union, so they are asserted as the
    ordering that makes the drawing worth doing rather than as literals that move with a
    game patch. The null case is asserted too: a building with no clearance data must
    come back as null, because a 6x6 guess sent from here would be indistinguishable
    from a measurement once it reached the page.
    """
    body = client.get("/api/machines").json()
    sizes = {
        row["cls"]: (row["w_m"], row["l_m"])
        for kind in body
        for row in body[kind]
        if row["w_m"] is not None
    }
    assert sizes["Build_ManufacturerMk1_C"] > sizes["Build_ConstructorMk1_C"]
    for w, l in sizes.values():
        assert 1 <= w <= 40 and 1 <= l <= 40, "a footprint in centimetres, or none at all"
    unmeasured = {
        row["cls"]
        for kind in body
        for row in body[kind]
        if row["w_m"] is None or row["l_m"] is None
    }
    assert unmeasured, "the fixture world has buildings the docs dump gives no clearance for"
    for cls in unmeasured:
        building = state.game.buildings.get(cls)
        assert building is None or building.footprint is None, (
            f"{cls} has a footprint and was dropped on the way out"
        )


def test_positions_are_metres_not_centimetres(client, state):
    """The one unit rule, pinned against a known fixture position.

    The save stores centimetres. A regression here is silent -- the map still draws,
    100x out -- so it is checked against the projection's own first machine rather than
    against a magnitude.
    """
    raw = state.projection["machines"][0]
    row = client.get("/api/machines").json()["machines"][0]
    assert row["instance_leaf"] == raw["instance"].rsplit(".", 1)[-1]
    assert row["x_m"] == pytest.approx(round(raw["pos"][0] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw["pos"][1] / 100.0, 1))
    assert row["z_m"] == pytest.approx(round(raw["pos"][2] / 100.0, 1))


def test_structures_are_the_floor_plan_the_player_actually_built(client, state):
    """Every lightweight buildable, un-interned, in metres, with the grid it snaps to."""
    body = client.get("/api/structures").json()
    raw = state.projection["structures"]
    assert body["count"] == len(raw["instances"]) == len(body["structures"])
    assert body["count"] > 8000, "the reference world is a 320-hour base, not a starter camp"
    # The page draws one tile per piece and must not hardcode its edge.
    assert body["tile_m"] == FOUNDATION_M == 8.0

    row = body["structures"][0]
    assert set(row) == {"cls", "x_m", "y_m", "z_m", "yaw"}
    # The class index is resolved here, or the page would have to carry the legend.
    assert row["cls"] == raw["classes"][raw["instances"][0][0]]
    assert row["cls"].startswith("Build_")
    assert {r["cls"] for r in body["structures"]} <= set(raw["classes"])
    # Metres, like every other coordinate on this surface. A regression is silent.
    assert row["x_m"] == pytest.approx(round(raw["instances"][0][1] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw["instances"][0][2] / 100.0, 1))
    assert row["z_m"] == pytest.approx(round(raw["instances"][0][3] / 100.0, 1))
    assert max(abs(r["x_m"]) for r in body["structures"]) < 5000


def test_a_world_with_nothing_built_answers_with_an_empty_floor_plan(game):
    """A young save has no lightweight subsystem at all, and that is not an error.

    Asserted through three shapes, because the projection has carried all three: the key
    absent, the key present but empty, and a row too short to be a transform. Anything
    but a 200 with an empty list here draws a red banner on a save whose only fault is
    that the player has not poured concrete yet.
    """
    for projection in ({}, {"structures": {}}, {"structures": {"classes": [], "instances": []}}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            body = c.get("/api/structures").json()
        assert body == {"structures": [], "count": 0, "tile_m": FOUNDATION_M}


def test_a_malformed_structure_row_costs_one_piece_not_the_endpoint(game):
    """Raw projection data, read guarded field by field -- the same rule elevation uses."""
    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [
                [0, 100, 200, 300, 33.5],
                [0, 100, 200],  # short: no z
                [0, "x", 200, 300],  # unparseable
                [7, 400, 500, 600, "sideways"],  # bad index, and an unreadable yaw
                [0, 700, 800, 900],  # a schema-11 row: no yaw column at all
                "not a row",
            ],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/structures").json()
    assert body["count"] == 3
    assert body["structures"][0] == {
        "cls": "Build_Foundation_8x1_01_C",
        "x_m": 1.0,
        "y_m": 2.0,
        "z_m": 3.0,
        "yaw": 33.5,
    }
    # An index with no class is still a real piece at a real place: it keeps its position
    # and loses only its name, which is the honest half-answer. Same for a yaw that will
    # not parse -- the piece survives, unrotated and saying so.
    assert body["structures"][1] == {"cls": None, "x_m": 4.0, "y_m": 5.0, "z_m": 6.0, "yaw": None}
    # A four-column row is what every projection cut before schema 12 holds. It is not
    # short and it is not broken: it is a piece whose facing was never recorded, and null
    # is the only answer that does not turn that into a claim of "axis-aligned".
    assert body["structures"][2] == {
        "cls": "Build_Foundation_8x1_01_C",
        "x_m": 7.0,
        "y_m": 8.0,
        "z_m": 9.0,
        "yaw": None,
    }


def test_a_save_that_cannot_be_read_has_no_floor_plan_either(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/structures")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


def test_placements_carry_the_yaw_the_map_has_to_draw_them_at(client, state):
    """Schema 12's rotation, on both surfaces that draw a rectangle.

    The endpoint's docstring used to promise the opposite -- "the quaternion is dropped,
    a client can only draw these axis-aligned" -- so this is asserted as a fact about the
    world rather than as a field being present: an angled slab has to survive the trip, or
    the map goes back to drawing staircases with no test noticing.
    """
    structures = client.get("/api/structures").json()["structures"]
    raw = state.projection["structures"]["instances"]
    assert structures[0]["yaw"] == pytest.approx(round(raw[0][4], 1))
    for row in structures:
        assert row["yaw"] is None or -180.0 <= row["yaw"] <= 180.0, row

    # The whole reason the drawing changes. A world built only on the cardinal grid would
    # let a broken rotation look perfect.
    angled = [r for r in structures if r["yaw"] and round(r["yaw"] % 90.0, 3) not in (0.0, 90.0)]
    assert len(angled) > 1000, "the reference world has several slabs laid at an angle"

    machines = client.get("/api/machines").json()
    rows = [row for kind in machines for row in machines[kind]]
    assert rows and all("yaw" in row for row in rows)
    assert any(row["yaw"] for row in rows), "every machine on this world faces north?"
    for row in rows:
        assert row["yaw"] is None or -180.0 <= row["yaw"] <= 180.0, row


def test_a_projection_from_before_schema_12_says_unknown_rather_than_zero(game):
    """``null``, not ``0.0``. The two draw the same and only one of them is a measurement,
    and an endpoint that filled the gap in with zero would make a world whose facings were
    never recorded indistinguishable from a world built entirely on the cardinal grid."""
    projection = {
        "structures": {"classes": ["Build_Foundation_8x1_01_C"], "instances": [[0, 1, 2, 3]]},
        "machines": [{"cls": "Build_ConstructorMk1_C", "instance": "x.y", "pos": [1, 2, 3]}],
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        assert c.get("/api/structures").json()["structures"][0]["yaw"] is None
        assert c.get("/api/machines").json()["machines"][0]["yaw"] is None


def test_belts_are_the_network_as_it_was_actually_routed(client, state):
    """Every conveyor piece, un-interned, in metres, in travel order."""
    body = client.get("/api/belts").json()
    raw = state.projection["belts"]
    assert body["count"] == len(raw["segments"]) == len(body["belts"])
    assert body["count"] > 3000, "the reference world is a 320-hour base"
    assert 0 < body["chains"] < body["count"], "pieces group into fewer chains than pieces"

    row = body["belts"][0]
    assert set(row) == {"chain", "cls", "name", "lift", "items_per_min", "points_m", "curve_m"}
    # The class legend is resolved here, or the page would have to carry it.
    assert row["cls"] == raw["classes"][raw["segments"][0][1]]
    assert {r["cls"] for r in body["belts"]} <= set(raw["classes"])
    assert row["name"] == state.game.buildings[row["cls"]].name
    assert not row["name"].startswith("Build_")

    # Metres, like every other coordinate on this surface, and in the projection's order.
    assert len(row["points_m"]) == len(raw["segments"][0][2])
    for out, cm in zip(row["points_m"], raw["segments"][0][2], strict=True):
        assert out == [pytest.approx(round(v / 100.0, 1)) for v in cm]
    for r in body["belts"]:
        assert r["points_m"], "a piece with no geometry is not a piece"
        for x_m, y_m, _z_m in r["points_m"]:
            assert abs(x_m) < 5000 and abs(y_m) < 5000


def test_a_lift_is_told_apart_by_its_native_class_not_by_its_name(client):
    """The one structural distinction the map draws, and where it comes from.

    A lift's top-down polyline is a single point, so the map has to know which pieces need
    a glyph instead of a line. That is asserted here twice over: the flag agrees with the
    docs dump's own native class, and the geometry agrees with the flag.
    """
    body = client.get("/api/belts").json()
    lifts = [r for r in body["belts"] if r["lift"]]
    belts = [r for r in body["belts"] if r["lift"] is False]
    assert lifts and belts, "the reference world has both"
    assert all("Lift" in r["cls"] for r in lifts)
    assert not any("Lift" in r["cls"] for r in belts)

    # Zero horizontal extent, measured: this is why a lift cannot be drawn as a line.
    for r in lifts:
        first, last = r["points_m"][0], r["points_m"][-1]
        assert (first[0], first[1]) == (last[0], last[1])
    assert any(r["points_m"][0][2] != r["points_m"][-1][2] for r in lifts), "lifts rise"
    assert any(r["points_m"][0][:2] != r["points_m"][-1][:2] for r in belts), "belts run"

    # The tier's own rate, rather than a "Mk3" the page would have to parse out of a name.
    rates = {r["items_per_min"] for r in body["belts"]}
    assert rates <= {60.0, 120.0, 270.0, 480.0, 780.0}


def test_a_world_with_no_belts_answers_with_an_empty_network(game):
    """Asserted through the three shapes the projection has carried, exactly as the floor
    plan next door is: a young save has laid no belt, and that is not an error."""
    for projection in ({}, {"belts": {}}, {"belts": {"classes": [], "segments": []}}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            assert c.get("/api/belts").json() == {
                "belts": [],
                "count": 0,
                "chains": 0,
                "attachments": [],
                "attachment_count": 0,
            }


def test_a_malformed_belt_segment_costs_one_piece_not_the_network(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                [0, 0, [[100, 200, 300], [400, 500, 600]]],
                [1, 0],  # short: no points
                ["chain", 0, [[100, 200, 300]]],  # unparseable chain index
                [2, 9, [[700, 800, 900], [1, 2, 3]]],  # class index off the end
                [3, 0, [[100, 200], "not a point", [100, 200, 300]]],  # one usable point
                [4, 0, []],  # no geometry at all
                "not a segment",
            ],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/belts").json()
    assert body["count"] == 3
    assert body["belts"][0] == {
        "chain": 0,
        "cls": "Build_ConveyorBeltMk1_C",
        "name": "Conveyor Belt Mk.1",
        "lift": False,
        "items_per_min": 60.0,
        "points_m": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        # Three columns wide, so it predates the curve column or has no bend: either way the
        # client draws the polyline it always drew.
        "curve_m": None,
    }
    # A piece whose class the legend cannot name is still a piece on real ground: it keeps
    # its route and loses the two things the class would have told us. `lift` is null, not
    # false -- "not a lift" would be a guess, and the two are drawn differently.
    unnamed = body["belts"][1]
    assert (unnamed["cls"], unnamed["lift"], unnamed["items_per_min"]) == (None, None, None)
    assert unnamed["points_m"] == [[7.0, 8.0, 9.0], [0.0, 0.0, 0.0]]
    assert body["belts"][2]["points_m"] == [[1.0, 2.0, 3.0]]
    assert body["chains"] == 3


def test_the_splitters_and_mergers_ride_with_the_belts_and_with_nothing_else(client, state):
    """A belt run passes THROUGH a splitter, so the belts payload carries them.

    Two claims, and the second is the one that keeps the map honest. First: every splitter
    and merger the projection holds comes back as a placement -- where it stands, which way
    it faces, what it is -- because that is all a splitter is. Second: it comes back HERE and
    nowhere else, so the belts layer can draw them without any risk of the machines layer
    drawing the same square underneath.
    """
    body = client.get("/api/belts").json()
    raw = state.projection["attachments"]
    assert body["attachment_count"] == len(raw) == len(body["attachments"])
    assert body["attachment_count"] > 800, "the reference world splits and merges a great deal"

    row = body["attachments"][0]
    assert set(row) == {"instance_leaf", "cls", "name", "x_m", "y_m", "z_m", "yaw", "w_m", "l_m"}
    assert "." not in row["instance_leaf"]
    # Metres, like every other coordinate on this surface. A regression here is silent.
    assert row["x_m"] == pytest.approx(round(raw[0]["pos"][0] / 100.0, 1))
    assert row["y_m"] == pytest.approx(round(raw[0]["pos"][1] / 100.0, 1))
    for r in body["attachments"]:
        assert abs(r["x_m"]) < 5000 and abs(r["y_m"]) < 5000
        assert r["name"] and not r["name"].startswith("Build_")
        # No clearance data for any of these classes, so the map draws its own square and
        # the server says so with a null rather than inventing one.
        assert (r["w_m"], r["l_m"]) == (None, None)
        assert r["yaw"] is None or -180 <= r["yaw"] <= 180

    kinds = {r["name"] for r in body["attachments"]}
    assert kinds == {"Conveyor Splitter", "Conveyor Merger", "Smart Splitter"}

    # The no-double-draw claim, checked rather than asserted in a comment.
    machines = client.get("/api/machines").json()
    drawn = {r["instance_leaf"] for kind in machines for r in machines[kind]}
    assert not drawn & {r["instance_leaf"] for r in body["attachments"]}


def test_a_world_that_split_no_belt_answers_with_no_attachments(game):
    """A young save has laid no belt and split nothing, and neither is an error."""
    for projection in ({}, {"attachments": []}, {"attachments": ["not a record"]}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            body = c.get("/api/belts").json()
        assert (body["attachments"], body["attachment_count"]) == ([], 0)


def test_a_save_that_cannot_be_read_has_no_belt_network_either(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/belts")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


def test_pipes_are_the_plumbing_as_it_was_actually_routed(client, state):
    """Every fluid pipe, un-interned, in metres, in the order the file stores it."""
    body = client.get("/api/pipes").json()
    raw = state.projection["pipes"]
    assert body["count"] == len(raw["segments"]) == len(body["pipes"])
    assert body["count"] > 400, "the reference world plumbs oil, water and fuel"
    assert 0 < body["networks"] < body["count"], "pipes group into fewer networks than pipes"

    row = body["pipes"][0]
    assert set(row) == {
        "row",
        "network",
        "fluid",
        "fluid_name",
        "cls",
        "name",
        "flow_m3_min",
        "points_m",
        "curve_m",
        "direction",
        "basis",
    }
    # The join /api/floors keys a pipe run by. It is the position in the raw table, which is
    # the position in this list only for as long as nothing is torn -- which is exactly why
    # it is sent rather than counted.
    assert [r["row"] for r in body["pipes"]] == list(range(len(raw["segments"])))
    # The class legend is resolved here, or the page would have to carry it.
    assert row["cls"] == raw["classes"][raw["segments"][0][1]]
    assert {r["cls"] for r in body["pipes"]} <= set(raw["classes"])
    assert row["name"] == state.game.buildings[row["cls"]].name
    assert not row["name"].startswith("Build_")

    # Metres, like every other coordinate on this surface, and in the projection's order.
    assert len(row["points_m"]) == len(raw["segments"][0][2])
    for out, cm in zip(row["points_m"], raw["segments"][0][2], strict=True):
        assert out == [pytest.approx(round(v / 100.0, 1)) for v in cm]
    for r in body["pipes"]:
        assert len(r["points_m"]) >= 2, "a pipe is a line; there are no vertical pipes"
        for x_m, y_m, _z_m in r["points_m"]:
            assert abs(x_m) < 5000 and abs(y_m) < 5000


def test_every_pipe_says_which_fluid_it_carries(client, state):
    """The thing a belt cannot say, and it comes from the world rather than from a guess.

    The game keeps an ``FGPipeNetwork`` per connected plumbing system with the fluid on it,
    so this is the save's own answer. Asserted against the projection's own network table
    rather than against a list of fluid names, which would pin this world's contents.
    """
    body = client.get("/api/pipes").json()
    by_id = {n["id"]: n["fluid"] for n in state.projection["pipes"]["networks"]}
    assert all(r["fluid"] == by_id[r["network"]] for r in body["pipes"])
    assert all(r["fluid"] is not None for r in body["pipes"]), (
        "every pipe on this world is claimed by a network"
    )
    # Resolved against the dump, so a popup never shows a reader a `Desc_…_C`.
    for r in body["pipes"]:
        assert r["fluid_name"] == state.game.items[r["fluid"]].name
        assert state.game.items[r["fluid"]].is_fluid
    assert len({r["fluid"] for r in body["pipes"]}) > 1, "this world plumbs more than one"

    # The tier's own rate, rather than an "MK2" the page would have to parse out of a name.
    assert {r["flow_m3_min"] for r in body["pipes"]} <= {300.0, 600.0}
    assert len({r["flow_m3_min"] for r in body["pipes"]}) == 2, "Mk1 and Mk2 both built here"


def test_every_pipe_row_says_which_way_it_flows_or_says_it_does_not_know(client, state):
    """The refusal replaced by a labelled answer, and the label is the point.

    There is still no direction stored ON a pipe -- its two connectors are numbered rather
    than named input and output. What there IS, and what the old refusal never interrogated,
    is the rest of the network: the save serialises every fluid coupling and TYPES a machine's
    ports. So a row now carries a direction where the plumbing admits only one, and ``unknown``
    where it admits two, with ``basis`` naming which of those a reader is looking at.

    Pinned here so that a direction can never arrive unlabelled, which is the invention the
    old test was guarding against: ``unknown`` and ``unresolved`` go together in both
    directions, and nothing else does.
    """
    body = client.get("/api/pipes").json()
    rows = body["pipes"]
    for r in rows:
        assert r["direction"] in {"forward", "reverse", "unknown"}
        assert r["basis"] in {"machine port", "pump", "propagated", "unresolved"}
        assert (r["direction"] == "unknown") == (r["basis"] == "unresolved"), r

    directed = [r for r in rows if r["direction"] != "unknown"]
    assert body["directed"] == len(directed)
    assert directed, "this world's plumbing is not one giant ambiguity"
    assert len(directed) < len(rows), "nor is any of it free"
    # Both readings occur: the spline's own order is the order the player dragged it, so a
    # projection where every pipe came out `forward` would mean the direction was being read
    # off the point order rather than off the network.
    assert {r["direction"] for r in directed} == {"forward", "reverse"}
    # And all three warrants are exercised, or a basis is dead code nobody would notice.
    assert {r["basis"] for r in directed} == {"machine port", "pump", "propagated"}
    assert len(directed) == sum(1 for f in state.pipe_flow if f["direction"] != "unknown"), (
        "the surface reports exactly what the domain service decided"
    )


def test_a_world_with_no_pipes_answers_with_empty_plumbing(game):
    """Asserted through the shapes the projection has carried, exactly as the belts next
    door are: a young save has laid no pipe, and that is not an error."""
    for projection in (
        {},
        {"pipes": {}},
        {"pipes": {"classes": [], "networks": [], "segments": []}},
    ):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            assert c.get("/api/pipes").json() == {
                "pipes": [],
                "count": 0,
                "networks": 0,
                "directed": 0,
            }


def test_a_malformed_pipe_segment_costs_one_piece_not_the_plumbing(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 7, "fluid": "Desc_Water_C"}, "not a network"],
            "segments": [
                [0, 0, [[100, 200, 300], [400, 500, 600]]],
                [1, 0],  # short: no points
                ["net", 0, [[100, 200, 300]]],  # unparseable network index
                [9, 0, [[700, 800, 900], [1, 2, 3]]],  # network index off the end
                [1, 0, [[10, 20, 30], [40, 50, 60]]],  # network entry is not a dict
                [0, 9, [[700, 800, 900], [1, 2, 3]]],  # class index off the end
                [0, 0, [[100, 200], "not a point", [100, 200, 300]]],  # one usable point
                [0, 0, []],  # no geometry at all
                "not a segment",
            ],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/pipes").json()
    assert body["count"] == 5
    # And the join survives the tearing, which is the whole reason `row` is a field rather
    # than this list's index: the five that decoded sat at 0, 3, 4, 5 and 6 in the raw table,
    # so counting would have renumbered four of them -- and `/api/floors` keys a pipe run by
    # that number, so four runs would have been drawn on somebody else's floor.
    assert [r["row"] for r in body["pipes"]] == [0, 3, 4, 5, 6]
    assert body["pipes"][0] == {
        "row": 0,
        "network": 7,
        "fluid": "Desc_Water_C",
        "fluid_name": "Water",
        "cls": "Build_Pipeline_C",
        "name": "Pipeline Mk.1",
        "flow_m3_min": 300.0,
        "points_m": [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        # No fourth column and no graph, so nothing to join and nothing to infer -- which is
        # exactly the schema-13 projection's answer too, rather than a crash or a guess. And
        # no fifth column either, so no curve: the same degradation, one schema later.
        "curve_m": None,
        "direction": "unknown",
        "basis": "unresolved",
    }
    # A pipe whose network the table cannot name is still a pipe on real ground: it keeps
    # its route and loses what the network would have told us. Null, not a guessed fluid.
    for orphan in (body["pipes"][1], body["pipes"][2]):
        assert (orphan["network"], orphan["fluid"], orphan["fluid_name"]) == (None, None, None)
        assert orphan["points_m"], "an unclaimed pipe is still drawn"
    # A piece whose class the legend cannot name keeps its route and its fluid.
    unnamed = body["pipes"][3]
    assert (unnamed["cls"], unnamed["flow_m3_min"]) == (None, None)
    assert unnamed["fluid"] == "Desc_Water_C"
    assert body["pipes"][4]["points_m"] == [[1.0, 2.0, 3.0]]
    assert body["networks"] == 1


def test_a_save_that_cannot_be_read_has_no_plumbing_either(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/pipes")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


# --------------------------------------------------------------------- floors


# ------------------------------------------------------------------ route curvature


def test_a_route_sends_its_curve_alongside_its_points(client, state):
    """``curve_m``: one entry per span, in step with ``points_m``, metres like everything else.

    The pairing is the thing to pin. A span is drawn between ``points_m[i]`` and
    ``points_m[i+1]``, and its entry is ``[leave, arrive]`` -- the tangent leaving the first
    and the tangent arriving at the second. Off by one, or with the two swapped, every bend on
    the map is still a bend and is the wrong one, which is precisely the kind of fault a
    screenshot does not catch.
    """
    for path, key, at in (("/api/belts", "belts", 3), ("/api/pipes", "pipes", 4)):
        body = client.get(path).json()
        raw = state.projection[key]["segments"]
        assert len(body[key]) == len(raw)
        curved = 0
        for row, seg in zip(body[key], raw, strict=True):
            if len(seg) <= at:
                assert row["curve_m"] is None, "a straight route claims a curve"
                continue
            curved += 1
            assert len(row["curve_m"]) == len(row["points_m"]) - 1, "one entry per span"
            for entry, stored in zip(row["curve_m"], seg[at], strict=True):
                if stored == 0:
                    assert entry is None, "a flat span is null, not a zero vector"
                    continue
                assert entry == [
                    [pytest.approx(round(v / 100.0, 1)) for v in stored[:3]],
                    [pytest.approx(round(v / 100.0, 1)) for v in stored[3:]],
                ]
        assert curved > 200, f"{key}: nothing in the reference world bends"


def test_a_curve_is_the_metres_a_position_is_and_not_the_flip_a_client_applies(client):
    """A tangent is a displacement in the same space as the points, so it takes the same
    divide-by-100 and NOTHING else -- no y-flip, no re-origin. The flip belongs to the client
    and is applied to both together, which is what makes the pair usable as it stands.

    Checked by scale rather than by value: a tangent that had been through a coordinate
    transform of its own would not land in the same order of magnitude as the span it bends.
    """
    body = client.get("/api/belts").json()
    checked = 0
    for r in body["belts"]:
        if not r["curve_m"]:
            continue
        for i, entry in enumerate(r["curve_m"]):
            if entry is None:
                continue
            span = math.dist(r["points_m"][i][:2], r["points_m"][i + 1][:2])
            for vec in entry:
                assert len(vec) == 3
                # A tangent is metres of the same size as the span it bends: the game stores
                # roughly half the chord and never more than a few times it.
                assert math.hypot(vec[0], vec[1]) < max(20.0, span * 12), (span, vec)
            checked += 1
    assert checked > 500


def test_a_world_whose_routes_predate_the_curve_column_still_draws_them(game):
    """A schema-14 projection through a schema-15 server: polylines, and no error.

    The degradation that matters, because the disk cache is keyed on the schema and a stale
    pickle is refused rather than served -- but a save re-read by an older sidecar is not, and
    a client that got a 500 here would show an empty map instead of the map it had yesterday.
    """
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [[0, 0, [[0, 0, 0], [400, 0, 0]]]],
        },
        "pipes": {
            "classes": ["Build_Pipeline_C"],
            "networks": [{"id": 1, "fluid": "Desc_Water_C"}],
            "segments": [[0, 0, [[0, 0, 0], [400, 0, 0]], -1]],
        },
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        assert c.get("/api/belts").json()["belts"][0]["curve_m"] is None
        assert c.get("/api/pipes").json()["pipes"][0]["curve_m"] is None


def test_a_malformed_curve_costs_the_curve_and_not_the_route(game):
    """Read guarded entry by entry, the rule every other field on this surface follows.

    A route whose curve column is the wrong length, or holds something that is not six
    numbers, still has its points -- and the points are what put it on the map. Losing the
    piece to save the bend would be the wrong trade in every case.
    """
    projection = {
        "belts": {
            "classes": ["Build_ConveyorBeltMk1_C"],
            "segments": [
                # Right length, one good span and three refusals of different kinds.
                [
                    0,
                    0,
                    [[0, 0, 0], [400, 0, 0], [800, 0, 0], [1200, 0, 0], [1600, 0, 0]],
                    [[100, 200, 0, 300, 400, 0], 0, "not a span", [1, 2, 3]],
                ],
                # Column present and the wrong length for the points: unusable as a whole,
                # because there is no way to tell which span each entry belongs to.
                [1, 0, [[0, 0, 0], [400, 0, 0], [800, 0, 0]], [[1, 2, 3, 4, 5, 6]]],
                # Column present and entirely unusable: null, not an empty list, so a client
                # takes the same branch it takes for a straight run.
                [2, 0, [[0, 0, 0], [400, 0, 0]], ["rubbish"]],
                [3, 0, [[0, 0, 0], [400, 0, 0]], "not a column"],
            ],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        rows = c.get("/api/belts").json()["belts"]
    assert len(rows) == 4, "every piece kept its geometry"
    assert rows[0]["curve_m"] == [[[1.0, 2.0, 0.0], [3.0, 4.0, 0.0]], None, None, None]
    assert rows[1]["curve_m"] is None
    assert rows[2]["curve_m"] is None
    assert rows[3]["curve_m"] is None


# --------------------------------------------------------------------------- storage


def test_storage_is_every_container_and_buffer_with_what_is_in_it(client, state):
    """The shape, and the join that is the whole point of the endpoint.

    A container's contents are ITS OWN -- the projection joins each ``StorageInventory``
    component to the actor that owns it -- so this is the first payload able to answer "where
    is the steel" rather than only "how much steel". Checked against the projection row by row
    so that a re-ordering or an off-by-one join is visible here rather than on the map.
    """
    body = client.get("/api/storage").json()
    raw = state.projection["storage"]
    assert body["count"] == len(raw) == len(body["storage"]) == 151
    assert body["filled"] == 125, "the containers the player has actually put something in"
    assert body["items_total"] > 100_000

    for row, source in zip(body["storage"], raw, strict=True):
        assert row["cls"] == source["cls"]
        assert row["instance_leaf"] == source["instance"].rsplit(".", 1)[-1]
        assert row["kind"] in ("solid", "fluid")
        assert row["x_m"] == pytest.approx(round(source["pos"][0] / 100.0, 1))
        assert row["yaw"] == pytest.approx(round(source["yaw"], 1))
        # No display name is ever an engine id, here or anywhere on this surface.
        assert row["name"] and not row["name"].startswith("Build_")


def test_a_solid_container_names_its_contents_and_says_what_it_left_out(client):
    """Items resolved to display names, biggest first, truncated with a count.

    The truncation is the part worth pinning: a popup that showed six of twelve kinds and
    stopped would read as a container holding six things. ``more`` and ``item_kinds`` are what
    let a client say so, and they have to agree with the list actually sent.
    """
    rows = [r for r in client.get("/api/storage").json()["storage"] if r["kind"] == "solid"]
    assert len(rows) == 146
    truncated = 0
    for r in rows:
        assert set(r) >= {"items", "more", "item_kinds", "total", "slots"}
        assert "stored_m3" not in r and "fluid" not in r, "a box has no fluid level"
        assert len(r["items"]) == min(r["item_kinds"], 6)
        assert r["more"] == r["item_kinds"] - len(r["items"])
        truncated += r["more"] > 0
        counts = [i["count"] for i in r["items"]]
        assert counts == sorted(counts, reverse=True), "biggest first"
        for item in r["items"]:
            assert item["cls"].startswith("Desc_")
            assert item["name"] and not item["name"].startswith("Desc_")
        assert r["total"] >= sum(counts)
    assert truncated, "no container here holds more kinds than the popup shows"


def test_a_fluid_buffer_reports_a_level_against_the_capacity_that_makes_it_a_reading(client):
    """``stored_m3`` alone is a number; ``fill`` is the answer.

    The capacity comes off the docs dump -- ``mStorageCapacity``, 400 on a Fluid Buffer and
    2,400 on an Industrial one -- because the save records only the level. Without it a popup
    saying "1,730.6 m3" leaves the reader to know how big the tank is.
    """
    rows = [r for r in client.get("/api/storage").json()["storage"] if r["kind"] == "fluid"]
    assert len(rows) == 5
    for r in rows:
        assert set(r) >= {"fluid", "fluid_name", "stored_m3", "capacity_m3", "fill"}
        assert "items" not in r and "slots" not in r, "a tank has no slots"
        assert r["capacity_m3"] in (400.0, 2400.0)
        assert r["fill"] == pytest.approx(r["stored_m3"] / r["capacity_m3"], abs=1e-4)
        assert 0.0 <= r["fill"] <= 1.0
        assert r["fluid_name"] and not r["fluid_name"].startswith("Desc_")
    assert max(r["fill"] for r in rows) > 0.9, "one of them is nearly full"


def test_a_storage_row_carries_the_footprint_it_is_drawn_at_or_says_it_cannot(client):
    """Same contract as the machines: a measured footprint, or null and the client's fallback.

    Three of the eight classes here are absent from the docs dump entirely -- the HUB's
    built-in container, the Blueprint Designer's, and the Dimensional Depot uploader -- so they
    get null rather than a number invented server-side, which would arrive indistinguishable
    from a measurement.
    """
    rows = client.get("/api/storage").json()["storage"]
    measured = {r["cls"] for r in rows if r["w_m"] is not None}
    unmeasured = {r["cls"] for r in rows if r["w_m"] is None}
    assert "Build_StorageContainerMk1_C" in measured
    assert unmeasured == {
        "Build_CentralStorage_C",
        "Build_StorageBlueprint_C",
        "Build_StorageIntegrated_C",
    }
    for r in rows:
        assert (r["w_m"] is None) == (r["l_m"] is None), "half a footprint is not a footprint"
        if r["w_m"] is not None:
            assert 0 < r["w_m"] < 100 and 0 < r["l_m"] < 100


def test_a_world_with_nothing_in_store_answers_with_an_empty_payload(game):
    """A young save has built no container, and that is not an error -- the belts' rule."""
    for projection in ({}, {"storage": []}, {"storage": None}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            assert c.get("/api/storage").json() == {
                "storage": [],
                "count": 0,
                "filled": 0,
                "items_total": 0,
            }


def test_a_malformed_storage_row_costs_that_row_and_not_the_warehouse(game):
    """Raw projection data, read guarded field by field -- the structures rule, again."""
    projection = {
        "storage": [
            {
                "cls": "Build_StorageContainerMk1_C",
                "instance": "x.Build_StorageContainerMk1_C_1",
                "pos": [100, 200, 300],
                "yaw": 90.0,
                "items": [["Desc_IronPlate_C", 4800], "not an entry", ["Desc_Cement_C"]],
                "slots": 24,
            },
            {"cls": "Build_StorageContainerMk1_C", "instance": "i", "pos": None, "yaw": None},
            "not a row",
            {
                "cls": "Build_PipeStorageTank_C",
                "instance": "t",
                "pos": [0, 0, 0],
                "yaw": 0.0,
                "fluid": None,
                "stored_m3": None,
            },
        ]
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        body = c.get("/api/storage").json()
    assert body["count"] == 3
    first = body["storage"][0]
    assert first["items"] == [{"cls": "Desc_IronPlate_C", "name": "Iron Plate", "count": 4800}]
    assert (first["item_kinds"], first["more"], first["total"]) == (1, 0, 4800)
    assert first["x_m"] == 1.0
    # A row with no position at all is still sent: the projection knows the container exists,
    # and a client that skips it on x is making that call for itself.
    assert body["storage"][1]["x_m"] is None
    assert body["storage"][1]["items"] == []
    # An unreadable fluid level is null, and `fill` refuses rather than dividing by it.
    assert body["storage"][2]["stored_m3"] is None
    assert body["storage"][2]["fill"] is None
    assert body["storage"][2]["capacity_m3"] == 400.0


def test_storage_takes_the_save_and_world_parameters_and_404s_on_an_unreadable_one(game):
    """The ``?save`` / ``?world`` contract every endpoint here shares.

    Both halves matter: the parameters have to REACH the loader -- an endpoint that quietly
    ignored ``?world`` would serve the default world under another world's name -- and a
    loader that refuses has to come back as a 404 with a message rather than as a 500.
    """
    asked: list[tuple] = []

    def loader(save=None, world=None):
        asked.append((save, world))
        if world == "nope":
            raise RuntimeError("no world matching 'nope'")
        return WorldState(projection={"storage": []}, game=game)

    app = create_app(state_loader=loader, game_loader=lambda: game)
    with TestClient(app) as c:
        assert c.get("/api/storage?world=Han%20Solo&save=x.sav").status_code == 200
        bad = c.get("/api/storage?world=nope")
    assert asked[0] == ("x.sav", "Han Solo"), "the query never reached the loader"
    assert bad.status_code == 404
    assert "no world matching" in bad.json()["error"]


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


@pytest.fixture
def blind_floors(client, monkeypatch):
    """The floors endpoint with no terrain field, which is what most machines have.

    Pinned rather than left to the machine: this repository ships no heightfield, so a test
    that passed only where somebody had run the generator would be a test of the generator.
    The one case that needs a field builds its own.
    """
    monkeypatch.setattr(web_terrain, "field", lambda: None)
    return client


def test_floors_decompose_the_world_into_platforms_and_bands(blind_floors):
    """The endpoint's own shape, over the fixture world's 132 platforms."""
    body = blind_floors.get("/api/floors").json()
    assert body["note"] is None
    assert body["selection"] is None
    assert body["counts"]["platforms"] == 132
    assert body["counts"]["bands"] == 93
    assert len(body["platforms"]) == 132

    tower = next(p for p in body["platforms"] if p["index"] == 1)
    assert tower["cells"] == 335
    assert tower["area_m2"] == 335 * 64.0
    # Metres, one decimal, like every other coordinate on this surface.
    assert tower["centre_m"] == [pytest.approx(-474.0, abs=1.0), pytest.approx(-1489.0, abs=1.0)]
    # The deck heights themselves, one decimal: six storeys 12 m apart, the module this
    # world is actually built on. Not the 4 m the design assumed before it was measured.
    assert [b["top_m"] for b in tower["bands"]] == [-5.8, 30.2, 42.2, 54.2, 66.2, 78.2]
    assert [round(b["top_m"] - tower["bands"][1]["top_m"]) for b in tower["bands"][1:]] == [
        0,
        12,
        24,
        36,
        48,
    ]
    assert [b["ordinal"] for b in tower["bands"]] == [0, 1, 2, 3, 4, 5]
    assert [b["cells"] for b in tower["bands"]] == [111, 175, 175, 117, 218, 132]
    # A band is a level, not a smear -- so the spread of its own members is zero.
    assert all(b["span_m"] == 0.0 for b in tower["bands"])
    # And the decomposition's own premise travels with it, per platform.
    assert tower["clean"] == 1.0
    assert body["rules"]["tile_m"] == 8.0
    assert body["rules"]["riser_m"] == 6.0


def test_a_band_carries_instance_ids_rather_than_a_second_copy_of_the_geometry(blind_floors, state):
    """The shape stage 3 needs, and the one it must not be given.

    The page already holds every machine and splitter from ``/api/machines`` and
    ``/api/belts``. What it cannot derive is which floor each is on, so a band ships ids and
    nothing else -- no positions, no footprints, no yaw. Re-sending those would double a
    payload the client already has in order to filter the copy.
    """
    body = blind_floors.get("/api/floors").json()
    tower = next(p for p in body["platforms"] if p["index"] == 1)
    band = tower["bands"][4]
    assert set(band) == {
        "ordinal",
        "top_m",
        "low_m",
        "high_m",
        "span_m",
        "pieces",
        "cells",
        "area_m2",
        "share",
        "minor",
        "machines",
        "attachments",
        "deck_rows",
        "machine_count",
        "attachment_count",
        "deck_row_count",
    }
    assert band["machine_count"] == len(band["machines"]) > 0
    assert band["attachment_count"] == len(band["attachments"]) > 0
    assert band["deck_row_count"] == len(band["deck_rows"]) > 0

    # Every id resolves against a payload the page already has, which is the whole point.
    known = {
        str(r["instance"]).rsplit(".", 1)[-1]
        for key in ("machines", "extractors", "generators")
        for r in state.projection[key]
    }
    assert set(band["machines"]) <= known
    splitters = {str(r["instance"]).rsplit(".", 1)[-1] for r in state.projection["attachments"]}
    assert set(band["attachments"]) <= splitters
    # No id is on two floors at once.
    everywhere = [
        i for p in body["platforms"] for b in p["bands"] for i in b["machines"] + b["attachments"]
    ]
    assert len(everywhere) == len(set(everywhere))


def test_a_deck_is_listed_by_position_because_concrete_has_no_instance_id(blind_floors):
    """The one join the concrete can carry, checked against the payload it points into.

    A lightweight buildable has no instance name, so ``deck_rows`` indexes
    ``/api/structures`` -- and this is the test that the two walks really are one order.
    Every row named must be a foundation whose top surface is the band's own level, which
    would break the moment either side started or stopped skipping a piece.
    """
    body = blind_floors.get("/api/floors").json()
    pieces = blind_floors.get("/api/structures").json()["structures"]
    tower = next(p for p in body["platforms"] if p["index"] == 1)

    seen: set[int] = set()
    for band in tower["bands"]:
        assert band["deck_rows"], "a band with no deck is not a band"
        for row in band["deck_rows"]:
            piece = pieces[row]
            assert "Foundation" in piece["cls"] or "Platform" in piece["cls"], piece["cls"]
            # z is the piece's CENTRE, so the deck is half a thickness up -- the stage-0
            # correction, read here from the other end of the wire.
            thickness = 4.0 if "8x4" in piece["cls"] else 2.0 if "8x2" in piece["cls"] else 1.0
            assert piece["z_m"] + thickness / 2 == pytest.approx(
                band["top_m"], abs=body["rules"]["band_eps_m"]
            )
        seen |= set(band["deck_rows"])
    # One piece, one floor: overlapping decks would draw a storey twice.
    assert sum(len(b["deck_rows"]) for b in tower["bands"]) == len(seen) == 949


def test_a_mezzanine_is_reported_minor_rather_than_merged_away(blind_floors):
    """Cell area is what tells a six-cell ledge from a 276-cell deck, so it is sent."""
    body = blind_floors.get("/api/floors").json()
    plat = next(p for p in body["platforms"] if p["index"] == 2)
    ledge, deck = plat["bands"]
    assert (ledge["cells"], deck["cells"]) == (6, 276)
    assert ledge["area_m2"] == 384.0
    assert ledge["minor"] is True and deck["minor"] is False
    assert ledge["share"] < body["rules"]["minor_share"] <= deck["share"]


def test_runs_are_grouped_by_what_they_do_to_a_floor(blind_floors, state):
    """Four groups, keyed by the join the belts and pipes payloads already carry."""
    body = blind_floors.get("/api/floors").json()
    runs = body["runs"]
    assert set(runs) == {"same-deck", "connector", "terrain", "mixed"}
    assert sum(len(v) for v in runs.values()) == body["counts"]["runs"] == 2412

    belts = [r for group in runs.values() for r in group if r["kind"] == "belt"]
    assert len(belts) == 1909 < len(state.projection["belts"]["segments"]), (
        "pieces are grouped into chains before anything vertical is asked of them"
    )
    assert {r["key"] for r in belts} == {s[0] for s in state.projection["belts"]["segments"]}
    pipes = [r for group in runs.values() for r in group if r["kind"] == "pipe"]
    assert sorted(r["key"] for r in pipes) == list(
        range(len(state.projection["pipes"]["segments"]))
    )

    # Connectors are how you leave a floor, and they name both ends.
    assert len(runs["connector"]) == 130
    for row in runs["connector"]:
        head, tail = row["ends"]
        assert head is not None and tail is not None
        assert (head["platform"], head["ordinal"]) != (tail["platform"], tail["ordinal"])
        assert head["top_m"] == pytest.approx(round(head["top_m"], 1))
    # A terrain run has no deck at either end, and says so with nulls rather than zeros.
    assert all(row["ends"] == [None, None] for row in runs["terrain"])
    # The riser flag is not the lift flag: a quarter of lift chains never change floor.
    jogs = [r for r in runs["same-deck"] if r["lift"]]
    assert jogs and all(not r["riser"] for r in jogs)
    assert body["violations"] == []


def test_placements_are_only_the_things_that_did_not_land_on_a_floor(blind_floors):
    """On-floor things are listed inside their band, so listing them here too would be the
    same 1,252 rows twice. What is here is the three ways of not being on a floor."""
    body = blind_floors.get("/api/floors").json()
    assert set(body["placements"]) == {"exempt", "terrain", "off-deck"}
    exempt = body["placements"]["exempt"]
    assert {r["cls"] for r in exempt} == {
        "Build_MinerMk1_C",
        "Build_MinerMk2_C",
        "Build_OilPump_C",
        "Build_WaterPump_C",
    }
    # Resolved to a display name like every other class on this surface.
    assert all(row["name"] and not row["name"].startswith("Build_") for row in exempt)
    assert all(row["x_m"] is not None and abs(row["x_m"]) < 5000 for row in exempt)

    # Without a field nothing can be MEASURED onto the ground, and that is stated rather
    # than left to be inferred from an empty list.
    assert body["terrain_measured"] is False
    assert body["placements"]["terrain"] == []
    assert body["placements"]["off-deck"]
    assert all(r["above_terrain_m"] is None for r in body["placements"]["off-deck"])


def test_a_terrain_field_turns_off_deck_into_a_measurement(client, monkeypatch):
    """With a field, "on the ground" stops being a guess -- and the flag says a field ran."""

    class _Flat:
        def at(self, x, y):
            del x, y
            return types.SimpleNamespace(z_m=80.0)

    monkeypatch.setattr(web_terrain, "field", _Flat)
    body = client.get("/api/floors").json()
    assert body["terrain_measured"] is True
    grounded = body["placements"]["terrain"]
    assert grounded, "a flat field at 80 m catches this world's ground-built machines"
    assert all(abs(r["above_terrain_m"]) <= 2.0 for r in grounded)
    assert all(r["above_terrain_m"] is not None for r in body["placements"]["off-deck"])


def test_floors_narrow_to_one_platform_and_to_a_named_factory(blind_floors, state):
    """A floor picker asks about one factory, and the two ways of naming it agree."""
    one = blind_floors.get("/api/floors?platform=1").json()
    assert one["selection"] == "platform 1"
    assert [p["index"] for p in one["platforms"]] == [1]
    assert one["counts"]["bands"] == 6
    assert one["counts"]["runs"] < 2412

    label = state.labels.labels[0].name if state.labels.labels else None
    if label:
        named = blind_floors.get("/api/floors", params={"factory": label}).json()
        assert named["selection"] == f"factory {label!r}"
        assert named["platforms"], f"{label} stands on something"


def test_asking_for_a_platform_or_a_factory_that_is_not_there_is_a_4xx(blind_floors):
    """An empty 200 would draw an empty floor picker and say nothing about why."""
    r = blind_floors.get("/api/floors?platform=9999")
    assert r.status_code == 404
    assert "no platform matches" in r.json()["error"]

    r = blind_floors.get("/api/floors", params={"factory": "no such factory"})
    assert r.status_code == 400
    assert "error" in r.json()
    assert "Named factories" in r.json()["error"]


def test_a_save_too_old_for_floors_says_so_with_a_200(game):
    """Not an error and not an empty list. The world has floors; this file cannot show them.

    Three shapes, because the projection has carried all three, exactly as
    ``/api/structures`` next door is asserted.
    """
    for projection in ({}, {"structures": {}}, {"structures": {"classes": [], "instances": []}}):
        app = create_app(
            state_loader=lambda save=None, world=None, p=projection: WorldState(
                projection=p, game=game
            ),
            game_loader=lambda: game,
        )
        with TestClient(app) as c:
            r = c.get("/api/floors")
        assert r.status_code == 200
        body = r.json()
        assert "predates lightweight buildables" in body["note"]
        assert body["platforms"] == []
        assert body["counts"]["bands"] == 0
        assert all(rows == [] for rows in body["runs"].values())


def test_a_save_that_cannot_be_read_has_no_floors_either(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/floors")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


def test_factories_report_named_labels_and_proposals(client, state):
    body = client.get("/api/factories").json()
    assert len(body["labels"]) == len(state.labels.labels)
    assert body["proposals"], "the fixture world has proposable factories"
    first = body["proposals"][0]
    assert set(first) >= {"index", "label", "centroid_m", "machines", "score"}
    # The index is the row's position in the FULL proposal list -- named clusters are
    # filtered out ahead of it -- so it must still resolve as a proposal:N selector.
    assert first["machines"] == state.proposals[first["index"]].size
    assert len(first["centroid_m"]) == 2
    # Centroids are metres too, and the world is roughly 7 km across.
    assert abs(first["centroid_m"][0]) < 5000


def test_a_factory_carries_the_box_its_machines_occupy(client, state):
    """``bbox_m`` is what turns a label into a button: the map flies to a factory's own
    extent, and a centroid alone cannot decide a zoom. Every anchor still standing has to
    lie inside the box, in metres, or the viewport it produces cuts machines off.
    """
    from satisfactory_mcp.domain.factories import identity as fidentity

    body = client.get("/api/factories").json()
    placed = fidentity.positions(state.projection)
    by_name = {label.name: label for label in state.labels.labels}

    for row in body["labels"]:
        anchors = [a for a in by_name[row["name"]].anchors if a in placed]
        if not anchors:
            assert row["bbox_m"] is None
            continue
        x_min, y_min, x_max, y_max = row["bbox_m"]
        assert abs(x_min) < 5000 and abs(y_max) < 5000, "metres, not the save's centimetres"
        cx, cy = row["centroid_m"]
        assert x_min <= cx <= x_max and y_min <= cy <= y_max
        for name in anchors:
            x, y = placed[name][0] / 100.0, placed[name][1] / 100.0
            assert x_min - 0.1 <= x <= x_max + 0.1
            assert y_min - 0.1 <= y <= y_max + 0.1

    proposal = body["proposals"][0]
    x_min, y_min, x_max, y_max = proposal["bbox_m"]
    # A box no wider than the diameter the same row already reports: the two are computed
    # from the same machines, so a disagreement means one of them is stale.
    assert max(x_max - x_min, y_max - y_min) <= proposal["spread_m"] + 0.2


def test_a_proposal_the_player_already_named_is_not_proposed_again(client, state, monkeypatch):
    """The clusterer re-discovers every named factory; the endpoint must not re-offer
    them. A proposal whose machines are majority-covered by a label's anchors would draw
    a machine-generated recipe string exactly on top of the player's own name -- and take
    its clicks, since the proposal layer is added later."""
    labelled = {a for label in state.labels.labels for a in label.anchors}
    body = client.get("/api/factories").json()
    for row in body["proposals"]:
        pr = state.proposals[row["index"]]
        overlap = sum(1 for m in pr.machines if m in labelled)
        assert 2 * overlap <= len(pr.machines), (row["index"], overlap, len(pr.machines))
    # The indices keep their position in the full list, so proposal:N still resolves.
    shown = [row["index"] for row in body["proposals"]]
    assert shown == sorted(shown)
    if len(shown) < len(state.proposals):
        assert set(shown) < set(range(len(state.proposals)))


def test_a_factory_whose_machines_are_all_gone_has_no_box_to_fly_to(client, monkeypatch):
    """A label outlives its machines -- that is the point of anchoring to instance ids --
    so the honest answer is a name with nowhere to go, not a zero box at the world centre
    that would fly the map to (0, 0) and read as a bug in the projection."""
    monkeypatch.setattr(web_api.fidentity, "positions", lambda projection: {})
    body = client.get("/api/factories").json()
    assert body["labels"], "the labels survive; only their positions are gone"
    assert all(row["bbox_m"] is None for row in body["labels"])


def test_collectibles_list_remaining_placements(client):
    body = client.get("/api/collectibles", params={"mode": "remaining"}).json()
    assert body["mode"] == "remaining"
    assert body["rows"]
    row = body["rows"][0]
    assert set(row) >= {"category", "name", "x_m", "y_m", "z_m", "collected", "observed"}
    assert all(r["collected"] is False for r in body["rows"])


def test_collectibles_can_be_scoped_to_one_group_and_to_collected(client):
    body = client.get(
        "/api/collectibles", params={"mode": "collected", "group": "power_slug_blue"}
    ).json()
    assert body["group"] == "power_slug_blue"
    assert {r["category"] for r in body["rows"]} == {"power_slug_blue"}
    assert all(r["collected"] for r in body["rows"])


def test_an_unknown_mode_is_refused_with_the_tools_own_wording(client):
    r = client.get("/api/collectibles", params={"mode": "sideways"})
    assert r.status_code == 400
    assert r.json()["error"].startswith("! unknown mode 'sideways'")


def test_remaining_is_refused_when_the_map_table_is_absent(state, game, monkeypatch):
    """No table, no answer -- the same refusal the MCP tool gives, not a shorter list."""
    monkeypatch.setattr(type(state), "collectibles", property(lambda self: None))
    app = create_app(
        state_loader=lambda save=None, world=None: state,
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        r = c.get("/api/collectibles", params={"mode": "remaining"})
    assert r.status_code == 400
    assert "needs the map's own placement table" in r.json()["error"]


def test_a_save_that_cannot_be_read_is_a_404_with_a_reason(game):
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/summary")
    assert r.status_code == 404
    assert "sidecar produced no output" in r.json()["error"]


def _first_sse_chunk(app) -> bytes:
    """Open the event stream, take one chunk, hang up.

    Driven through the endpoint rather than through ``TestClient``: an SSE response is
    an endless generator, and TestClient's portal deadlocks on teardown waiting for one
    to finish. This exercises the real generator -- the real watcher, the real ping
    constant, the real unsubscribe on close -- and terminates.
    """

    async def pull() -> bytes:
        await app.state.watcher.start()
        try:
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/api/events",
                    "headers": [],
                    "query_string": b"",
                    "app": app,
                }
            )
            response = await web_api.events(request)
            assert response.media_type == "text/event-stream"
            async for chunk in response.body_iterator:
                return chunk  # closes the generator, which unsubscribes
            raise AssertionError("the stream ended without sending anything")
        finally:
            await app.state.watcher.stop()

    return asyncio.run(pull())


def test_events_keep_the_stream_alive_with_a_ping_comment(game, tmp_path, monkeypatch):
    """An empty save directory produces no events, so the keepalive is what arrives.

    The watcher is pointed at ``tmp_path`` rather than the player's real save root: this
    test is about the stream's shape, and a real directory would make it about timing.
    """
    monkeypatch.setattr(config, "saves_root", lambda: tmp_path)
    monkeypatch.setattr(web_api, "PING_SECONDS", 0.05)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)
    assert _first_sse_chunk(app) == b": ping\n\n"


def test_a_written_save_becomes_a_save_event(game, tmp_path, monkeypatch):
    """The watcher's whole job: a new mtime under the save root reaches the browser."""
    monkeypatch.setattr(config, "saves_root", lambda: tmp_path)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "Han Solo_autosave_0.sav").write_bytes(b"not really a save")
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: game)

    async def watch_once():
        found = await app.state.watcher.poll_once()
        assert found is not None
        return found

    event = asyncio.run(watch_once())
    assert event.filename == "Han Solo_autosave_0.sav"
    assert event.mtime > 0

    # A stream that opens after the change still learns about it: the watcher replays
    # its latest event to a new subscriber, so a browser started mid-session draws the
    # current world instead of waiting for the next autosave.
    chunk = _first_sse_chunk(app).decode()
    assert chunk.startswith("event: save\ndata: ")
    assert json.loads(chunk.split("data: ", 1)[1]) == {
        "filename": "Han Solo_autosave_0.sav",
        "mtime": event.mtime,
    }


def test_the_static_bundle_ships_the_page_and_the_vendor_licence():
    """Redistributing Leaflet means shipping its BSD-2-Clause text next to it.

    Leaflet is compiled into ``app.js`` now rather than served as ``vendor/leaflet.js``, so
    there is no file to point at any more -- which is exactly why the licence text still has
    to be here, and why the bundle names the library in its own banner. The obligation did
    not move when the packaging did.
    """
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "app.css").is_file()
    licence = (STATIC_DIR / "vendor" / "LEAFLET-LICENSE").read_text(encoding="utf-8")
    assert "BSD 2-Clause License" in licence
    assert "Leaflet" in (STATIC_DIR / "app.js").read_text(encoding="utf-8")[:1000]


def test_the_page_is_served_from_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "app.js" in r.text
