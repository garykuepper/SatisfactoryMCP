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
two generators in ``test_map_generators.py``, ``/api/machines`` and ``/api/structures`` in
``test_web_placements.py``, ``/api/belts`` and ``/api/pipes`` in ``test_web_routes.py``,
``/api/storage`` in ``test_web_storage.py`` and ``/api/power`` in ``test_web_power.py``.
What is left here is whatever ``interfaces/web/api.py`` still serves.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from conftest import _explode

fastapi = pytest.importorskip("fastapi")

from fastapi import Request
from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web import api as web_api
from satisfactory_mcp.interfaces.web import terrain as web_terrain
from satisfactory_mcp.interfaces.web.app import STATIC_DIR, create_app

# --------------------------------------------------------------------- floors


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
