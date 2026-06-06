"""The web adapter's JSON surface, driven off the committed fixture projection.

``importorskip`` at module scope, not a marker: ``fastapi`` lives in the optional
``web`` extra, so an install without it must skip this file rather than fail collection.

Every test here injects both loaders, so nothing in this file spawns the sidecar, reads
a ``.sav`` or needs the save directory to exist. The one exception is the watcher test,
which needs a directory precisely so it can be pointed at an empty one.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Request
from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.interfaces.web import api as web_api
from satisfactory_mcp.interfaces.web.app import STATIC_DIR, create_app


@pytest.fixture
def client(state, game):
    """The API over the fixture world. Both loaders are stubs; no save is ever read."""
    app = create_app(
        state_loader=lambda save=None, world=None: state,
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        yield c


def test_worlds_lists_the_save_picker_rows(client, monkeypatch):
    """The picker's only source. ``list_worlds`` is stubbed so no save tree is scanned."""
    world = World(
        world_id="X2faPVKjX06VaRzClNv5KQ",
        session_name="Han Solo",
        saves=[{"filename": "a.sav", "mtime_ns": 1_700_000_000_000_000_000, "play_duration_s": 42}],
    )
    monkeypatch.setattr(web_api.proj, "list_worlds", lambda: ([world], [{"file": "old.sav"}]))
    body = client.get("/api/worlds").json()
    assert len(body["worlds"]) == 1
    row = body["worlds"][0]
    assert row["world_id"] == "X2faPVKjX06VaRzClNv5KQ"
    assert row["session_name"] == "Han Solo"
    assert row["newest_filename"] == "a.sav"
    assert row["mtime"] == pytest.approx(1.7e9)
    assert row["play_duration_s"] == 42
    assert body["unsupported"] == [{"file": "old.sav"}]


def test_summary_reports_the_header_power_and_progression(client, state):
    body = client.get("/api/summary").json()
    assert body["header"]["session_name"] == state.header["session_name"]
    assert body["age_note"] == state.age_note
    assert body["power"]["generation_mw"] == pytest.approx(state.power_report()["generation_mw"])
    assert body["progression"]["game_phase"] == state.progression()["game_phase"]


def test_nodes_carry_the_occupancy_join_and_a_reusable_selector(client, state):
    body = client.get("/api/nodes").json()
    rows = body["nodes"]
    assert rows, "the committed node table is not empty"
    row = rows[0]
    assert set(row) >= {"id", "resource", "name", "purity", "x_m", "y_m", "z_m", "occupied"}
    # The id must survive intact: the page offers it as a `node:` selector for the MCP
    # tools, and a shortened one would not resolve there.
    assert row["id"].startswith("Persistent_Level:")
    assert row["name"] == row["id"].rsplit(".", 1)[-1]
    # Occupancy resolution is partial by design, but this save has extractors placed.
    assert body["occupied"] > 0
    assert any(r["occupant_cls"] for r in rows if r["occupied"])


def test_nodes_can_be_filtered_by_resource(client):
    body = client.get("/api/nodes", params={"resource": "Desc_OreIron_C"}).json()
    assert body["resource"] == "Desc_OreIron_C"
    assert body["nodes"]
    assert {r["resource"] for r in body["nodes"]} == {"Desc_OreIron_C"}


def test_regions_serve_the_biome_raster_in_metres(client):
    """The base map's only source: 30 rows of 30 cells, a legend, and the frame."""
    r = client.get("/api/regions")
    assert r.status_code == 200
    assert "max-age" in r.headers["cache-control"], "geography does not change per save"
    body = r.json()
    assert len(body["grid"]) == 30
    assert {len(row) for row in body["grid"]} == {30}
    assert body["cell_m"] == 256.0
    assert (body["x0_m"], body["y0_m"]) == (-3360.0, -3800.0)
    assert body["legend"]["E"] == "Dune Desert"
    # Every letter drawn has a name, or the page paints an unlabelled colour.
    drawn = {ch for row in body["grid"] for ch in row} - {"."}
    assert drawn <= set(body["legend"])
    entry = body["regions"]["Dune Desert"]
    assert len(entry["centroid_m"]) == 2
    assert len(entry["bbox_m"]) == 4
    # Metres, one decimal, like every other coordinate on this surface.
    assert abs(entry["centroid_m"][0]) < 5000


def test_every_region_cell_lands_inside_that_regions_own_bbox(client):
    """The orientation guard, and the reason this endpoint reports x0/y0 at all.

    Row 0 is the NORTHERN edge because ``y0_m`` is the smallest y and game +Y is south.
    Get that backwards and the map still draws -- mirrored -- so it is checked against
    the per-region bounding boxes in the same file rather than by eye: reconstruct each
    cell's extent from the frame and assert it is inside the region it claims to be.
    """
    body = client.get("/api/regions").json()
    cell = body["cell_m"]
    counted = 0
    for j, row in enumerate(body["grid"]):
        for i, letter in enumerate(row):
            if letter == ".":
                continue
            box = body["regions"][body["legend"][letter]]["bbox_m"]
            x, y = body["x0_m"] + i * cell, body["y0_m"] + j * cell
            assert box[0] <= x and x + cell <= box[2], (i, j, letter)
            assert box[1] <= y and y + cell <= box[3], (i, j, letter)
            counted += 1
    assert counted > 400, "a base map of a few dozen cells is not a base map"
    # And the north-east corner is desert, which is the one fact a mirrored map fails.
    north_east = {body["grid"][j][i] for j in range(4) for i in range(26, 30)}
    assert north_east == {"E"}


def test_the_map_image_is_a_loader_and_says_where_the_file_goes(client, tmp_path, monkeypatch):
    """No image is shipped, so the 404 has to be useful: it names the exact path.

    ``config.data_dir`` is redirected at the tmp tree, which is also what keeps this test
    honest about the repository never carrying one -- it writes the png itself.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    r = client.get("/api/mapimage")
    assert r.status_code == 404
    message = r.json()["error"]
    assert str(tmp_path / "local" / "map.png") in message
    assert "only ever read locally" in message
    assert client.head("/api/mapimage").status_code == 404

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
    )
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "map.png").write_bytes(png)

    r = client.get("/api/mapimage")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == png
    # The corners ride along on the probe the page already makes.
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"


def test_a_local_sidecar_can_repin_the_map_images_corners(client, tmp_path, monkeypatch):
    """Someone else's render will not share our corners, and a bad one is ignored."""
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    (tmp_path / "local").mkdir()
    (tmp_path / "local" / "map.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    (tmp_path / "local" / "map.json").write_text(json.dumps({"x_min_m": -4000, "y_max_m": 4000}))
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-4000.0,-3750.0,4253.0,4000.0"

    (tmp_path / "local" / "map.json").write_text("{not json")
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"


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
        "clock",
        "paused",
    }
    assert row["name"] != row["cls"], "the class id was not joined to a building name"
    assert "." not in row["instance_leaf"]


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


def test_factories_report_named_labels_and_proposals(client, state):
    body = client.get("/api/factories").json()
    assert len(body["labels"]) == len(state.labels.labels)
    assert body["proposals"], "the fixture world has proposable factories"
    first = body["proposals"][0]
    assert set(first) >= {"index", "label", "centroid_m", "machines", "score"}
    assert first["index"] == 0
    assert len(first["centroid_m"]) == 2
    # Centroids are metres too, and the world is roughly 7 km across.
    assert abs(first["centroid_m"][0]) < 5000


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
    def explode(save=None, world=None):
        raise RuntimeError("sidecar produced no output")

    app = create_app(state_loader=explode, game_loader=lambda: game)
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
    """Redistributing Leaflet means shipping its BSD-2-Clause text next to it."""
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "style.css").is_file()
    assert (STATIC_DIR / "vendor" / "leaflet.js").is_file()
    licence = (STATIC_DIR / "vendor" / "LEAFLET-LICENSE").read_text(encoding="utf-8")
    assert "BSD 2-Clause License" in licence


def test_the_page_is_served_from_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "leaflet.js" in r.text
