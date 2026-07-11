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
import math
import types
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Request
from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_DIR_NAME,
    TILES_RETIRED,
    TILES_STAGING,
    PyramidError,
    enhanced_top_z,
    install_pyramid,
    merge_enhanced,
    pyramid_top_z,
    tile_relpath,
)
from satisfactory_mcp.core.gamedata.footprint import FOUNDATION_M
from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web import api as web_api
from satisfactory_mcp.interfaces.web.app import STATIC_DIR, create_app
from tools import gen_map_image, gen_map_renders


def _explode(save=None, world=None):
    """A loader that fails the way the real one fails when the sidecar produces nothing."""
    raise RuntimeError("sidecar produced no output")


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
    # The you-are-here marker's only source. Metres, like everything else here.
    pos = state.player_position()
    if pos is None:
        assert body["player"] == {"x_m": None, "y_m": None, "z_m": None}
    else:
        assert body["player"]["x_m"] == pytest.approx(round(pos[0] / 100.0, 1))
        assert body["player"]["y_m"] == pytest.approx(round(pos[1] / 100.0, 1))


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


def test_nodes_survive_a_save_that_cannot_be_read(game):
    """The node table is static and needs no ``.sav`` -- the rule /api/inspect already
    follows. A failed save costs the occupancy join, never the geography, and the loss
    is said out loud: ``save_error`` set, ``occupied`` null rather than a measured 0."""
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/nodes")
    assert r.status_code == 200
    body = r.json()
    assert "sidecar produced no output" in body["save_error"]
    assert body["occupied"] is None
    assert len(body["nodes"]) > 500
    assert all(row["occupied"] is False for row in body["nodes"])


def test_nodes_resolve_their_occupant_to_a_display_name(client):
    """One popup, one vocabulary: 'occupied by Miner Mk.2', not Build_MinerMk2_C."""
    rows = client.get("/api/nodes").json()["nodes"]
    held = [r for r in rows if r["occupied"]]
    assert held
    for row in held:
        assert row["occupant_name"], row
        assert not row["occupant_name"].startswith("Build_")
    assert all(r["occupant_name"] is None for r in rows if not r["occupied"])


def test_nodes_can_be_filtered_by_resource(client):
    body = client.get("/api/nodes", params={"resource": "Desc_OreIron_C"}).json()
    assert body["resource"] == "Desc_OreIron_C"
    assert body["nodes"]
    assert {r["resource"] for r in body["nodes"]} == {"Desc_OreIron_C"}


def test_nodes_carry_the_region_they_sit_in(client):
    """Joined server-side, so the raster -- and its orientation trap -- lives in one place.

    ``label_for_node``, not ``label_for``: the hand-verified override table is the reason
    ``verified`` shows up at all, and a plain cell lookup would silently downgrade every
    node someone actually checked to a 256 m guess.
    """
    rows = client.get("/api/nodes").json()["nodes"]
    named = [r for r in rows if r["region"]]
    assert len(named) > 500, "the raster covers the nodes; a handful of nulls is the limit"
    known = set(client.get("/api/regions").json()["regions"])
    assert {r["region"]["name"] for r in named} <= known
    # The confidence word travels with the name, or a boundary guess reads as a fact.
    assert {r["region"]["confidence"] for r in named} <= {
        "sparse",
        "boundary",
        "interior",
        "verified",
    }
    assert any(r["region"]["confidence"] == "verified" for r in named), (
        "the override table is what verified means, and it was not consulted"
    )


# ---------------------------------------------------------------- inspector

#: The same three coordinates ``test_elevation`` probes, and for the same reasons: a
#: developed platform where the built population swamps the ground one, open field with
#: nodes and nothing built, and water far enough out that nothing is in range.
ON_PLATFORM = (-1216.0, -1127.0)
IN_THE_FIELD = (2000.0, -2400.0)
OPEN_OCEAN = (-3500.0, 3500.0)


def test_inspect_answers_the_three_questions_a_site_starts_with(client):
    """Region, measured ground, nearest nodes -- none of which was on the map before."""
    x_m, y_m = IN_THE_FIELD
    body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()
    assert set(body) >= {"region", "elevation", "nearest"}
    assert body["at"] == {"x_m": x_m, "y_m": y_m}

    # Advisory, and never separable from how far it can be trusted.
    assert body["region"]["name"] == "Spire Coast"
    assert body["region"]["confidence"] == "interior"
    assert body["region"]["accuracy_m"] == 256

    e = body["elevation"]
    assert e["radius_m"] == web_api.INSPECT_RADIUS_M == 200.0
    assert e["ground_m"] is not None and e["ground_spread_m"] is not None
    assert e["counts"] == {"node": e["ground_count"]}, "open field: nothing built out here"
    assert e["built_m"] is None and e["built_count"] == 0

    near = body["nearest"]
    assert len(near) == web_api.INSPECT_NEAREST == 5
    assert [n["distance_m"] for n in near] == sorted(n["distance_m"] for n in near)
    row = near[0]
    assert set(row) >= {"resource", "purity", "occupied", "distance_m", "id", "x_m", "y_m"}
    assert row["resource"].startswith("Desc_")
    # Metres, like every other coordinate here, and the distance agrees with the position.
    assert row["distance_m"] == pytest.approx(
        ((row["x_m"] - x_m) ** 2 + (row["y_m"] - y_m) ** 2) ** 0.5, abs=0.15
    )


def test_inspect_measures_a_platform_and_still_refuses_the_fill_depth(client):
    """The trap this whole feature could have walked into, pinned at the coordinate where
    it is worst: 805 built samples against one node. The built median is real and is
    reported; the fill depth is a difference from a single ground point, so it is refused
    -- and the refusal carries its reason, because a bare null reads as a broken endpoint.
    """
    x_m, y_m = ON_PLATFORM
    e = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()["elevation"]
    assert e["built_count"] > 500 and e["built_m"] is not None
    assert e["counts"]["structure"] > e["counts"]["node"] * 100
    assert e["ground_count"] < 3
    assert e["fill_m"] is None, "a fill from one ground sample is an invented number"
    assert e["fill_note"] == "not enough ground samples (1 of 3 within 200 m)"


def test_inspect_quotes_a_fill_depth_once_both_populations_are_real(game):
    """And when it can be measured, it is: the difference of the two medians, in metres.

    Driven off a synthetic floor plan, for two reasons. The reference world has no spot
    where three nodes and a platform share a 200 m circle, so the refusal above is the
    only thing it can exercise; and a world with nothing else built makes the built median
    a number this test chose, which is what turns "some fill" into an exact 25.0.
    """
    from satisfactory_mcp.domain.spatial import geo
    from satisfactory_mcp.domain.spatial import nodes as nodes_mod

    table = nodes_mod.load_nodes()
    radius = web_api.INSPECT_RADIUS_M
    found = None
    for n in table.nodes:
        # The same circle the probe will use, or the ground median under test is not the
        # ground median the endpoint computes.
        near = [
            m
            for m in table.nodes
            if geo.distance_m((n["x"], n["y"]), (m["x"], m["y"])) <= radius and m.get("z")
        ]
        if len(near) % 2 == 1 and len(near) >= 3:
            found = (n, sorted(float(m["z"]) for m in near))
            break
    assert found, "the node table has no odd cluster of three within the probe radius"
    centre, heights = found
    ground_cm = heights[len(heights) // 2]

    projection = {
        "structures": {
            "classes": ["Build_Foundation_8x1_01_C"],
            "instances": [[0, centre["x"], centre["y"], ground_cm + 2500.0]],
        }
    }
    app = create_app(
        state_loader=lambda save=None, world=None: WorldState(projection=projection, game=game),
        game_loader=lambda: game,
    )
    with TestClient(app) as c:
        e = c.get(
            "/api/inspect", params={"x_m": centre["x"] / 100.0, "y_m": centre["y"] / 100.0}
        ).json()["elevation"]
    assert e["ground_count"] == len(heights)
    assert e["built_count"] == 1
    assert e["fill_m"] == pytest.approx(25.0, abs=0.1)
    assert e["fill_note"] is None


def test_inspect_says_off_the_map_rather_than_naming_the_nearest_land(client):
    """Ocean and off-map are the same answer, and it is a refusal, not a nearby biome."""
    for x_m, y_m in (OPEN_OCEAN, (99999.0, 99999.0)):
        body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()
        assert body["region"] is None
        e = body["elevation"]
        assert e["ground_count"] == 0 and e["ground_m"] is None
        assert e["counts"] == {}
        assert e["fill_m"] is None and "not enough ground samples" in e["fill_note"]
        # The node table is map-wide, so "nearest" is always answerable -- and the
        # distances say plainly how far away the answer is.
        assert len(body["nearest"]) == 5
        assert all(n["distance_m"] > 1000 for n in body["nearest"])


def test_inspect_still_answers_when_the_save_cannot_be_read(game):
    """The node table needs no ``.sav``, so a broken world keeps its geography.

    What it loses is the built population and the occupancy join, and ``save_error`` has
    to say so: without it every node would read as free because nothing was there to say
    otherwise, which is the exact failure mode this project keeps refusing.
    """
    app = create_app(state_loader=_explode, game_loader=lambda: game)
    with TestClient(app) as c:
        r = c.get("/api/inspect", params={"x_m": IN_THE_FIELD[0], "y_m": IN_THE_FIELD[1]})
    assert r.status_code == 200
    body = r.json()
    assert "sidecar produced no output" in body["save_error"]
    assert body["region"]["name"] == "Spire Coast"
    assert body["elevation"]["ground_count"] > 0, "the node table is the ground truth"
    assert body["elevation"]["built_count"] == 0
    assert body["nearest"] and all(n["occupied"] is False for n in body["nearest"])


def test_inspect_needs_a_coordinate(client):
    assert client.get("/api/inspect").status_code == 422


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
    # The HEAD probe runs on every page load and an absent optional file is the normal
    # answer, so it must not be a status the devtools console logs red.
    assert client.head("/api/mapimage").status_code == 204

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


#: A 1x1 PNG. Small enough to write a whole fake pyramid out of, which is the point: these
#: tests need a tiles/ tree with the right SHAPE, not any picture.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ=="
)


def _fake_pyramid(local: Path, max_z: int = 2, payload: bytes = _PNG) -> int:
    """A tiles/ tree of tiny PNGs at the layout the generator writes. Returns the count.

    ``payload`` is how the layer tests tell one tree from another: three pyramids of the
    same bytes could not show that a request reached the tree it named.
    """
    count = 0
    for z in range(max_z + 1):
        (local / web_api.MAP_TILES_DIR_NAME / str(z)).mkdir(parents=True)
        for x in range(1 << z):
            for y in range(1 << z):
                (local / web_api.MAP_TILES_DIR_NAME / str(z) / f"{x}_{y}.png").write_bytes(payload)
                count += 1
    return count


def _fake_layer(
    local: Path,
    layer: str,
    max_z: int = 2,
    sidecar: dict | None = None,
    payload: bytes = _PNG,
) -> int:
    """The same tree one level down, where ``tools/gen_map_renders.py`` writes a layer.

    Deliberately built through ``web_api``'s own names rather than a hand-typed path: what
    these tests are checking is that the endpoint finds a layer where the generator puts
    one, and a fixture that spelled the directory itself would agree with the endpoint by
    construction rather than by the module having got it right.
    """
    directory = local / web_api.MAP_RENDERS_DIR_NAME / layer
    directory.mkdir(parents=True, exist_ok=True)
    if sidecar is not None:
        (directory / web_api.MAP_RENDER_SIDECAR_NAME).write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
    return _fake_pyramid(directory, max_z, payload)


def test_the_tile_pyramid_is_a_loader_too_and_names_the_tool_that_writes_it(
    client, tmp_path, monkeypatch
):
    """Absent, present, and the probe the page opens with -- the same posture as map.png.

    The pyramid is the page's first choice for the base map, so "no pyramid" is asked on
    every clean load and must not be a console error: HEAD says 204 while GET keeps the
    404 that names the generator.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    r = client.get("/api/maptiles/0/0/0")
    assert r.status_code == 404
    message = r.json()["error"]
    assert str(tmp_path / "local" / "tiles") in message
    assert "gen_map_image.py" in message
    assert client.head("/api/maptiles/0/0/0").status_code == 204

    (tmp_path / "local").mkdir()
    _fake_pyramid(tmp_path / "local")

    r = client.get("/api/maptiles/2/3/1")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == _PNG
    # The probe answers every question the tile layer is built from at once.
    head = client.head("/api/maptiles/0/0/0")
    assert head.status_code == 200
    assert head.headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"
    assert head.headers["x-map-tile-px"] == str(web_api.MAP_TILE_PX)
    assert head.headers["x-map-tile-max-z"] == str(web_api.MAP_TILE_MAX_Z)
    # ``immutable`` is earned by the ``?v=`` build tag alone. The probe carries no tag, and
    # caching IT hard is the measured failure: a regenerated pyramid stayed invisible in
    # Firefox behind a year-old probe until the browser cache was disabled by hand.
    assert head.headers["cache-control"] == "no-cache"
    versioned = client.get("/api/maptiles/2/3/1?v=" + head.headers["x-map-build"])
    assert "immutable" in versioned.headers["cache-control"]
    # A tile is immutable per build, so the tag it is fetched under has to revalidate free.
    etag = head.headers["etag"]
    assert client.get("/api/maptiles/0/0/0", headers={"If-None-Match": etag}).status_code == 304


def test_a_tile_outside_the_pyramid_is_a_404_and_cannot_name_a_file(client, tmp_path, monkeypatch):
    """Off the grid, off the end, and shaped like an escape -- all answered, none served.

    The endpoint takes three ints, so the only strings that reach it are integers: a
    segment with a slash, a dot-dot or an encoded one never matches the route at all.
    That is the whole traversal argument, and it is asserted rather than asserted-to.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    (tmp_path / "local").mkdir()
    _fake_pyramid(tmp_path / "local")

    # z0 is one tile, so (1, 0) is off its grid; z6 is past the top of this pyramid.
    for path in ("/api/maptiles/0/1/0", "/api/maptiles/0/0/1", "/api/maptiles/2/4/0"):
        assert client.get(path).status_code == 404, path
        assert "2**z" in client.get(path).json()["error"]
    assert client.get("/api/maptiles/6/0/0").status_code == 404
    assert client.get("/api/maptiles/-1/0/0").status_code == 404

    # And nothing that is not an integer is even routed to the handler.
    for path in (
        "/api/maptiles/0/0/..%2f..%2f..%2fmap",
        "/api/maptiles/0/..%2f..%2fmap/0",
        "/api/maptiles/0/0/0.png",
        "/api/maptiles/0/0/%2e%2e",
    ):
        assert client.get(path).status_code in (404, 422), path

    # The path builder says the same thing on its own, which is what the endpoint leans on.
    assert web_api.map_tile_path(0, 0, 0, 5).name == "0_0.png"
    assert web_api.map_tile_path(0, 1, 0, 5) is None
    assert web_api.map_tile_path(6, 0, 0, 5) is None
    assert web_api.map_tile_path(3, 7, 7, 5) is not None
    assert web_api.map_tile_path(3, 8, 0, 5) is None


#: Three pyramids of identical bytes could not show that a request reached the tree it
#: named, so each fixture layer gets a PNG of its own. All three are valid 1x1 PNGs -- the
#: point is which one comes back, not what is in it.
_PNG_TERRAIN = _PNG[:-4] + b"TERR"
_PNG_SATELLITE = _PNG[:-4] + b"SATL"


def test_a_named_layer_is_served_from_its_own_tree_and_the_bare_route_is_still_map(
    client, tmp_path, monkeypatch
):
    """Three pictures of one world, one grid, one path segment between them.

    The design is that a layer is a directory and nothing else: same frame, same tile size,
    same ``{z}/{x}_{y}.png``. So what has to hold is that naming a layer reaches THAT tree
    -- proven with three different payloads rather than three identical ones -- and that the
    route which existed before layers did still answers exactly what ``map`` answers, byte
    for byte and header for header. That last part is not a nicety: the live page addresses
    the base map through the bare route, and this branch must not be able to break it.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_api.LOCAL_DIR_NAME
    local.mkdir()
    _fake_pyramid(local)
    _fake_layer(local, "terrain", payload=_PNG_TERRAIN)
    _fake_layer(local, "satellite", payload=_PNG_SATELLITE)

    for layer, payload in (
        ("map", _PNG),
        ("terrain", _PNG_TERRAIN),
        ("satellite", _PNG_SATELLITE),
    ):
        r = client.get(f"/api/maptiles/{layer}/2/3/1")
        assert r.status_code == 200, layer
        assert r.headers["content-type"] == "image/png"
        assert r.content == payload, layer
        assert r.headers["x-map-layer"] == layer

    # The alias is the same answer, not a similar one.
    bare = client.get("/api/maptiles/2/3/1")
    named = client.get("/api/maptiles/map/2/3/1")
    assert bare.status_code == named.status_code == 200
    assert bare.content == named.content == _PNG
    for header in (
        "x-map-bounds-m",
        "x-map-layer",
        "x-map-tile-px",
        "x-map-tile-max-z",
        "x-map-build",
        "cache-control",
        "etag",
    ):
        assert bare.headers[header] == named.headers[header], header

    # And the two routes cannot collide: three segments has no layer to name.
    assert web_api.map_tile_path(2, 3, 1) == local / web_api.MAP_TILES_DIR_NAME / "2" / "3_1.png"
    assert web_api.map_tile_path(2, 3, 1, layer="terrain") == (
        local
        / web_api.MAP_RENDERS_DIR_NAME
        / "terrain"
        / web_api.MAP_TILES_DIR_NAME
        / "2"
        / "3_1.png"
    )


def test_a_layer_that_does_not_exist_and_one_that_was_never_generated_are_told_apart(
    client, tmp_path, monkeypatch
):
    """Two different absences, two different answers, and neither is a stack trace.

    A layer this server has never heard of is a 404 that lists the ones it has, because the
    reader mistyped a name and the useful reply is the vocabulary. A layer it knows but
    nobody has generated is the ordinary state of a fresh checkout: HEAD says 204 so a
    probing page leaves no red line in the console, and GET says which tool would write it
    -- gen_map_renders.py for a render, gen_map_image.py for the artwork, because "run the
    generator" is not help when there are two.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)

    unknown = client.get("/api/maptiles/bathymetry/0/0/0")
    assert unknown.status_code == 404
    message = unknown.json()["error"]
    assert "bathymetry" in message
    for layer in web_api.MAP_LAYERS:
        assert layer in message

    for layer, tool in (
        ("map", "gen_map_image.py"),
        ("terrain", "gen_map_renders.py"),
        ("satellite", "gen_map_renders.py"),
    ):
        assert client.head(f"/api/maptiles/{layer}/0/0/0").status_code == 204, layer
        absent = client.get(f"/api/maptiles/{layer}/0/0/0")
        assert absent.status_code == 404
        assert tool in absent.json()["error"], layer

    # An unknown layer is refused before any of that, so it can never name a file.
    assert web_api.map_tile_path(0, 0, 0, layer="bathymetry") is None
    assert web_api._layer_dir("bathymetry") is None
    assert web_api._layer_sidecar("bathymetry") is None
    # Including when it is shaped like an escape: there is no join for it to escape through.
    for shape in ("..", "../..", "map/../..", ".", ""):
        assert web_api.map_tile_path(0, 0, 0, layer=shape) is None, shape
    for path in (
        "/api/maptiles/..%2f..%2ftiles/0/0/0",
        "/api/maptiles/%2e%2e/0/0/0",
        "/api/maptiles/terrain/0/0/..%2f..%2fmap",
        "/api/maptiles/terrain/0/0/0.png",
    ):
        assert client.get(path).status_code in (404, 422), path


def test_every_layer_answers_with_its_own_depth_build_and_corners(client, tmp_path, monkeypatch):
    """A layer's headers come from the sidecar beside its own tiles, never from another's.

    They are generated by different tools at different times: the artwork can be two levels
    deeper than the renders if it was enhanced, and a satellite recut this morning must not
    invalidate the terrain a browser cached last week. So depth, corners and build tag are
    read per layer -- and the tag folds the layer's NAME in, because two pyramids that
    happen to agree on every recorded number would otherwise share a cache key and serve
    each other's ``immutable`` tiles.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_api.LOCAL_DIR_NAME
    local.mkdir()
    _fake_pyramid(local, max_z=1)
    (local / web_api.MAP_BOUNDS_NAME).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 5}}}), encoding="utf-8"
    )
    # Deliberately the SAME recorded numbers as the artwork, and a different depth for the
    # third, so both halves of the claim are exercised at once.
    same = {"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 5}}}
    _fake_layer(local, "terrain", max_z=1, sidecar=same)
    _fake_layer(
        local,
        "satellite",
        max_z=3,
        sidecar={
            "x_min_m": -100.0,
            "_meta": {"tiles": {"tile_px": 256, "max_z": 3, "count": 85}},
        },
    )

    heads = {layer: client.head(f"/api/maptiles/{layer}/0/0/0") for layer in web_api.MAP_LAYERS}
    assert [
        heads[layer].headers["x-map-tile-max-z"] for layer in ("map", "terrain", "satellite")
    ] == ["1", "1", "3"]
    # The artwork stops at z1 while the satellite goes to z3, from the same request shape.
    assert client.get("/api/maptiles/map/2/0/0").status_code == 404
    assert client.get("/api/maptiles/satellite/3/7/7").status_code == 200

    tags = {layer: head.headers["x-map-build"] for layer, head in heads.items()}
    assert len(set(tags.values())) == 3, "identical sidecars must still not share a cache tag"
    assert tags["map"] != tags["terrain"], "and the name is what separates these two"

    # Corners are the layer's own as well: the satellite's sidecar moves its western edge
    # and the other two stay where the default puts them.
    assert heads["satellite"].headers["x-map-bounds-m"].startswith("-100.0,")
    assert heads["map"].headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"
    assert heads["terrain"].headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"

    # And the tag still moves when that layer's pyramid does, which is what makes a
    # ``?v=``-tagged tile safe to cache forever.
    versioned = client.get(f"/api/maptiles/terrain/1/1/1?v={tags['terrain']}")
    assert "immutable" in versioned.headers["cache-control"]
    etag = heads["terrain"].headers["etag"]
    assert (
        client.get("/api/maptiles/terrain/0/0/0", headers={"If-None-Match": etag}).status_code
        == 304
    )
    (local / web_api.MAP_RENDERS_DIR_NAME / "terrain" / web_api.MAP_RENDER_SIDECAR_NAME).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 1, "count": 6}}}), encoding="utf-8"
    )
    assert client.head("/api/maptiles/terrain/0/0/0").headers["x-map-build"] != tags["terrain"]
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] == tags["map"]


#: A tile out of the @2x tree, distinguishable from the 1x one by its bytes rather than by
#: its size -- what is under test is which DIRECTORY a request reached, and a fixture whose
#: two trees held the same bytes could not tell.
_PNG_DENSE = _PNG[:-4] + b"AT2X"


def test_a_hi_dpi_client_asks_for_the_same_tile_and_gets_twice_the_pixels(
    client, tmp_path, monkeypatch
):
    """``?px=512`` is one directory hop, and everything else about the request is unchanged.

    The @2x tree is the identical tile GRID -- level z is still 2**z tiles a side over the
    identical squares of the world -- at 512 px a tile instead of 256. So the whole of the
    serving design is that one query parameter picks a directory: same route, same
    coordinates, same corners, same cache tag.

    Three things have to hold, and each of them is a way the feature could be quietly wrong.
    The probe has to advertise BOTH depths, because a client builds its layer from that one
    response and cannot ask for a tree it has not been told about. A layer with no @2x tree --
    the artwork, cut by a tool that writes none -- has to serve the 1x tile rather than a 404,
    or asking for density on the wrong layer takes the base map down. And the @2x tree's own
    depth has to be the one enforced, since it is one level shallower and a request past its
    top must be refused against ITS grid rather than the 1x one's.
    """
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_api.LOCAL_DIR_NAME
    local.mkdir()
    _fake_pyramid(local, max_z=2)  # the artwork: 1x only, like the tool that writes it
    directory = local / web_api.MAP_RENDERS_DIR_NAME / "terrain"
    _fake_layer(
        local,
        "terrain",
        max_z=2,
        sidecar={
            "_meta": {
                "tiles": {"tile_px": 256, "max_z": 2, "count": 21},
                "tiles_2x": {"tile_px": 512, "max_z": 1, "count": 5},
            }
        },
        payload=_PNG_TERRAIN,
    )
    for z in range(2):
        for x in range(1 << z):
            for y in range(1 << z):
                path = directory / web_api.MAP_TILES_2X_DIR_NAME / str(z) / f"{x}_{y}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(_PNG_DENSE)

    head = client.head("/api/maptiles/terrain/0/0/0")
    assert head.headers["x-map-tile-px"] == "256"
    assert head.headers["x-map-tile-max-z"] == "2"
    assert head.headers["x-map-tile-2x-px"] == "512"
    assert head.headers["x-map-tile-2x-max-z"] == "1"

    # The same coordinates, one parameter apart, reach the two trees.
    assert client.get("/api/maptiles/terrain/1/1/1").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/terrain/1/1/1?px=512").content == _PNG_DENSE
    # Corners and cache tag are untouched by the density: it is the same picture.
    dense = client.head("/api/maptiles/terrain/0/0/0?px=512")
    assert dense.headers["x-map-bounds-m"] == head.headers["x-map-bounds-m"]
    assert dense.headers["x-map-build"] == head.headers["x-map-build"]

    # The @2x tree's own depth is what a request is checked against, and it is shallower.
    assert client.get("/api/maptiles/terrain/2/3/3").status_code == 200
    off = client.get("/api/maptiles/terrain/2/3/3?px=512")
    assert off.status_code == 404 and "z0..z1" in off.json()["error"]

    # A size this layer has no tree for, and a layer with no dense tree at all, both answer
    # with the 1x tile -- which every client can draw at any density.
    assert client.get("/api/maptiles/terrain/1/1/1?px=1024").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/terrain/1/1/1?px=nonsense").content == _PNG_TERRAIN
    assert client.get("/api/maptiles/map/1/1/1?px=512").content == _PNG
    assert "x-map-tile-2x-px" not in client.head("/api/maptiles/map/0/0/0").headers


def test_the_render_generator_writes_where_the_layered_route_looks(tmp_path, monkeypatch):
    """``tools/gen_map_renders.py`` and this endpoint agree about names, or nothing works.

    Nothing else joins the two, so the join is asserted against the tool's own constants and
    its own sidecar builder -- the same posture the artwork's sidecar test takes next door.
    Four names have to match, and the shape of the record the endpoint reads back has to be
    the one the tool actually writes rather than a hand-typed sample that could drift.
    """
    assert gen_map_renders.RENDERS_DIR_NAME == web_api.MAP_RENDERS_DIR_NAME
    assert gen_map_renders.RENDER_SIDECAR_NAME == web_api.MAP_RENDER_SIDECAR_NAME
    assert set(gen_map_renders.LAYERS) == set(web_api.MAP_RENDER_LAYERS)
    assert gen_map_renders.BOUNDS_M == web_api.DEFAULT_MAP_BOUNDS_M
    # The tile grid is the cutter's, not this generator's: it hands its sheet to
    # ``core.gameassets.pyramid`` and the endpoint has to be configured for what THAT cuts.
    # The tile SIZE and the directory names are no longer asserted equal, because the
    # endpoint imports them from the cutter and an assertion that a name equals itself
    # cannot fail. What is still worth pinning is the arithmetic, which is a real claim
    # about two different sheets: the default depth stays z5, which is what an 8192 sheet
    # divides into and what a pyramid whose sidecar says nothing is assumed to be, while
    # the renders are 16384 and say so in their own sidecar.
    assert pyramid_top_z(gen_map_renders.SHEET_PX) == web_api.MAP_TILE_MAX_Z == 5
    assert pyramid_top_z(gen_map_renders.RENDER_PX) == 6
    # And the @2x tree is the same grid one level shallower, by arithmetic rather than by
    # anybody's choice: 512 * 2**z runs out of sheet before 256 * 2**z does.
    assert PYRAMID_TILE_2X_PX == 2 * PYRAMID_TILE_PX
    assert pyramid_top_z(gen_map_renders.RENDER_PX, PYRAMID_TILE_2X_PX) == 5

    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    sidecar = gen_map_renders.build_sidecar(
        layer="terrain",
        field_meta={
            "generator": "tools/gen_world_heightmap.py",
            "sources": {"game": {"game_version_pinned": pin}},
        },
        tiles={
            "tile_px": 256,
            "max_z": 6,
            "count": 5461,
            "bytes": 240_000_000,
            "game_version_pinned": pin,
        },
        tiles_2x={"tile_px": 512, "max_z": 5, "count": 1365, "bytes": 240_000_000},
        render={"width_px": 16384},
        extra={},
    )
    assert gen_map_renders.pinned_field_build(sidecar) == pin
    assert gen_map_renders.pinned_field_build({}) is None
    assert gen_map_renders.pinned_field_build({"_meta": {"sources": {}}}) is None

    # The endpoint reads that file, unmodified, out of the place the tool writes it to.
    directory = tmp_path / web_api.LOCAL_DIR_NAME / web_api.MAP_RENDERS_DIR_NAME / "terrain"
    directory.mkdir(parents=True)
    (directory / web_api.MAP_RENDER_SIDECAR_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    read_back = web_api._map_pyramid("terrain")
    assert (read_back["tile_px"], read_back["max_z"]) == (256, 6)
    assert (read_back["tile_2x_px"], read_back["max_2x_z"]) == (512, 5)
    assert web_api._map_bounds("terrain") == web_api.DEFAULT_MAP_BOUNDS_M

    # A layer with no @2x block says so with None rather than with a zero, because zero is
    # a depth a real pyramid can have and "there is no such tree" is not a depth.
    plain = json.loads(json.dumps(sidecar))
    del plain["_meta"]["tiles_2x"]
    (directory / web_api.MAP_RENDER_SIDECAR_NAME).write_text(json.dumps(plain), encoding="utf-8")
    without = web_api._map_pyramid("terrain")
    assert (without["tile_2x_px"], without["max_2x_z"]) == (None, None)
    # ...and the two trees' numbers are in one cache tag, so recutting either changes both.
    assert without["build"] != read_back["build"]


def test_the_sun_is_in_the_north_west_and_the_shore_is_not_a_staircase():
    """The two rules of the render a wrong answer would still look like terrain.

    Hillshade first. An inverted light source draws every valley as a ridge and the picture
    is still a plausible relief map, so the direction is asserted on slopes whose answer is
    known by construction: a hill face tilted toward the north-west must come back brighter
    than the same face tilted toward the south-east, and a flat plain must sit between them.
    Rows run south and columns run east, which is the half of it that compass angles hide.

    Then the shore. ``submerged`` is a step function on a 1 m grid, so a hard composite
    draws every coastline as metre blocks; the feather has to be a real blend -- water where
    it is deep, ground where there is none, and strictly between the two in the band -- or
    it is decoration rather than the antialiasing it is there to be.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    rows, cols = numpy.mgrid[0:9, 0:9].astype(numpy.float32)
    flat = gen_map_renders.hillshade(numpy.zeros((9, 9), numpy.float32), 1.0)
    # Ground falling away to the north-west: high in the south-east, so the face looks at
    # the sun. The opposite sign is the same slope turned away from it.
    toward = gen_map_renders.hillshade(rows + cols, 1.0)
    away = gen_map_renders.hillshade(-(rows + cols), 1.0)
    assert toward.mean() > flat.mean() > away.mean()
    assert away.min() >= gen_map_renders.SHADE_FLOOR, (
        "a shadowed face keeps its colour, it does not go black"
    )
    assert toward.max() <= gen_map_renders.SHADE_FLOOR + gen_map_renders.SHADE_RANGE + 1e-6
    # A north-facing slope and a west-facing one are lit alike; north-east and south-west
    # are the two the azimuth has to separate.
    assert gen_map_renders.hillshade(rows, 1.0).mean() == pytest.approx(
        gen_map_renders.hillshade(cols, 1.0).mean()
    )

    # And the shore. A wide lake with a beach on one side: the depth feather has a band to
    # work in, so the coverage climbs through it rather than switching.
    ground = numpy.zeros((7, 40), numpy.float32)
    depth = numpy.linspace(-2.0, 6.0, 40, dtype=numpy.float32)
    water = numpy.broadcast_to(depth, (7, 40)).copy()
    wet = (water > ground).astype(numpy.float32)
    measured = numpy.ones_like(wet)
    alpha = gen_map_renders.water_alpha(ground, water, wet, measured, 0.8)
    assert alpha.min() == pytest.approx(0.0, abs=0.02), "dry ground is not tinted"
    assert alpha.max() == pytest.approx(1.0, abs=0.02), "open water is not half-painted"
    assert numpy.all(numpy.diff(alpha[3]) >= -1e-6), "coverage rises with depth, never falls"
    assert 0.05 < alpha[3][numpy.argmin(numpy.abs(depth - gen_map_renders.WATER_EDGE_M / 2))] < 0.95

    # A cliff into deep water has no depth band at all, and the spatial blur is what keeps
    # that edge from being a staircase: the pixels either side of it are partial.
    cliff = numpy.zeros((7, 40), numpy.float32)
    cliff[:, :20] = 50.0
    level = numpy.full((7, 40), 20.0, numpy.float32)
    hard = gen_map_renders.water_alpha(
        cliff, level, (cliff < 20.0).astype(numpy.float32), numpy.ones((7, 40), numpy.float32), 0.8
    )
    assert set(numpy.round(hard[3, :14], 3)) == {0.0} and hard[3, -1] == pytest.approx(
        1.0, abs=0.02
    )
    assert any(0.05 < value < 0.95 for value in hard[3, 18:22]), "the edge is antialiased"

    dry_rgb = numpy.full((7, 40, 3), 200.0, numpy.float32)
    shade = numpy.ones((7, 40), numpy.float32)
    shallow_rgb, deep_rgb = gen_map_renders.WATER_SHALLOW, gen_map_renders.WATER_DEEP
    tint = gen_map_renders.water_depth_fraction(ground, water, measured)
    out = gen_map_renders.water_over(dry_rgb, tint, alpha, shade, shallow_rgb, deep_rgb)
    assert (out[3, 0] == 200.0).all(), "ground above the water is untouched"
    # And where it IS water it is water and only water, tinted by its own depth.
    shallow = shallow_rgb * (gen_map_renders.WATER_SHADE_FLOOR + gen_map_renders.WATER_SHADE_RANGE)
    assert out[3, -1] == pytest.approx(shallow, abs=12.0)


def test_water_whose_depth_was_never_measured_is_still_drawn_as_water():
    """The rule that stopped 3.572 km2 of ocean being rendered as land.

    Over the fill province the ground under the water is a 3.9 m-quantised raster that
    routinely rounds ABOVE a sea surface 17 m down, so ``water_m - z_m`` there is a negative
    number and the depth feather run on it answers "no water". The quality byte exists to say
    that the level is known and the depth is not, and the two consequences are asserted here
    because both of them are invisible in a picture that is merely plausible: such a texel is
    drawn at **full alpha**, and it is tinted at the **deep** end rather than the shallow one.

    The second is a measurement rather than a preference -- 95.2% of level-only water on the
    shipped field stands over the fill province and 98% of its surface levels sit in a 0.7 m
    band around the ocean's own -16.99 m -- but what has to hold in code is only that the
    unknown depth is never run through the ramp.
    """
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("scipy")

    # A sea surface at -17 over "ground" the fill layer rounded to -15: above the water.
    ground = numpy.full((7, 40), -15.0, numpy.float32)
    surface = numpy.full((7, 40), -17.0, numpy.float32)
    wet = numpy.ones((7, 40), numpy.float32)
    unknown = numpy.zeros((7, 40), numpy.float32)

    drowned = gen_map_renders.water_alpha(ground, surface, wet, unknown, 0.8)
    assert drowned.min() == pytest.approx(1.0, abs=1e-3), (
        "water whose depth is unknown is fully water; the comparison that says otherwise is "
        "the arithmetic the quality byte was added to stop being the answer"
    )
    assert gen_map_renders.water_depth_fraction(ground, surface, unknown).min() == pytest.approx(
        1.0
    ), "and it is tinted deep, not the pale green of an ankle-deep sheet"

    # The same texels with the depth MEASURED are the old behaviour exactly: dry.
    known = numpy.ones((7, 40), numpy.float32)
    assert gen_map_renders.water_alpha(ground, surface, wet, known, 0.8).max() == pytest.approx(
        0.0, abs=1e-3
    )

    # And a field with no quality byte at all falls back to the comparison rather than
    # reading missing as dry -- which is all such a field can say.
    class _Old:
        _height_dm = numpy.array([[0, 0], [0, 0]], numpy.int16)
        _prov = numpy.zeros((2, 2), numpy.uint8)

        def _water_raster(self):
            return numpy.array([[5, hf.NODATA], [5, 5]], numpy.int16)

        def _water_quality_raster(self):
            return None

    plane, measured, note = gen_map_renders.water_planes(_Old())
    assert plane.tolist() == [[1, 0], [1, 1]] and measured is plane
    assert "predates the quality byte" in note


def test_the_biome_palette_is_this_file_s_own_and_covers_what_the_game_ships():
    """The satellite layer's colours are designed, and every area the game names has one.

    The asset ships 37 RGBA entries and they are a minimap legend -- flat primaries, cyan,
    magenta, pure white -- so they are decoded for the record and never drawn. What has to
    hold is that the replacement is complete (an area with no colour would fall back to a
    neutral and quietly vanish into the coast) and that it really is a satellite palette
    rather than the legend under another name: nothing saturated, nothing at full white.
    """
    assert set(gen_map_renders.REGION_PAIRS.values()) <= set(gen_map_renders.BIOME_COLOURS), (
        "every game area this file checks against the region grid must also have a colour"
    )
    for name, colour in gen_map_renders.BIOME_COLOURS.items():
        assert len(colour) == 3 and all(0 <= c <= 255 for c in colour), name
        assert max(colour) - min(colour) <= 110, f"{name} is more saturated than imagery gets"
        assert max(colour) <= 220, f"{name} is brighter than imagery gets"
    # The fallbacks are the same kind of colour, so an area a later build adds looks
    # unremarkable rather than wrong.
    for colour in (gen_map_renders.NO_MANS_LAND_RGB, gen_map_renders.UNKNOWN_BIOME_RGB):
        assert max(colour) - min(colour) <= 40


class _FakeSheet:
    """The three things ``cut_pyramid`` asks of a Pillow image, and nothing else.

    Pillow is the generators' dependency -- the optional ``gen`` extra -- and this suite
    runs whether or not it is installed, so the cutting is exercised against a stand-in:
    what is under test here is the tree that comes out -- the levels, the names, the
    count -- not anybody's Lanczos filter.
    """

    def __init__(self, width: int):
        self.width = width

    def resize(self, size, _filter):
        return _FakeSheet(size[0])

    def crop(self, box):
        return _FakeTile(box)


class _FakeTile:
    def __init__(self, box):
        self.box = box

    def save(self, path, **_kwargs):
        path.write_bytes(_PNG)


def test_the_pyramid_is_renamed_into_place_so_a_reader_never_meets_half_of_one(tmp_path):
    """An interrupted run must leave no tree at all rather than a tree missing levels.

    So the cut goes to a staging directory and is renamed over the old one, and the count
    is checked against what is really on disk before the swap. Both leftovers of a run that
    died mid-swap -- the staging tree and the retired one -- are cleared rather than merged
    into, and a level the new pyramid does not have cannot survive from the old one.
    """
    assert pyramid_top_z(8192) == 5
    assert pyramid_top_z(2048) == 3
    assert pyramid_top_z(256) == 0
    with pytest.raises(PyramidError):
        pyramid_top_z(5000)

    tiles = tmp_path / TILES_DIR_NAME
    (tiles / "9").mkdir(parents=True)
    (tiles / "9" / "0_0.png").write_bytes(b"a level the new cut does not have")
    (tmp_path / TILES_STAGING / "3").mkdir(parents=True)
    (tmp_path / TILES_STAGING / "3" / "0_0.png").write_bytes(b"half of a dead run")

    imaging = types.SimpleNamespace(LANCZOS="the filter, which the stand-in ignores")
    stats = install_pyramid(_FakeSheet(1024), imaging, tmp_path)

    assert (stats["max_z"], stats["count"]) == (2, 1 + 4 + 16)
    assert stats["tile_px"] == PYRAMID_TILE_PX
    assert not (tmp_path / TILES_STAGING).exists(), "staging is not left behind"
    assert not (tmp_path / TILES_RETIRED).exists(), "nor is the tree it replaced"
    assert sorted(p.name for p in tiles.iterdir()) == ["0", "1", "2"]
    assert len(list(tiles.rglob("*.png"))) == stats["count"]
    assert (tiles / tile_relpath(2, 3, 3)).read_bytes() == _PNG


def test_the_generated_sidecar_is_read_by_the_server_provenance_and_all(
    client, tmp_path, monkeypatch
):
    """``tools/gen_map_image.py`` writes the sidecar and this endpoint reads it.

    Nothing else joins those two, and they agree on four key names, two file names and a
    directory. So the join is asserted against the tool's OWN output rather than a
    hand-typed sample that could drift away from what it really writes -- and in
    particular against the ``_meta`` block it puts beside the corners, which the reader
    has to walk past rather than trip over.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    sidecar = gen_map_image.build_sidecar(
        build_pin=pin,
        build_raw={"Changelist": 495413, "BranchName": "++FactoryGame+rel-main-1.2.0"},
        image={"file": gen_map_image.IMAGE_NAME, "width_px": gen_map_image.SHEET_PX},
        integrity={"ubulk_bytes_expected": gen_map_image.UBULK_BYTES},
        layout={"layout_holds": True},
        calibration={"pin_holds": True},
        versions={"pyooz": "0.0.8", "texture2ddecoder": "1.0.6", "pillow": "12.3.0"},
        tiles={
            "tile_px": PYRAMID_TILE_PX,
            "max_z": pyramid_top_z(gen_map_image.SHEET_PX),
            "count": 1365,
            "bytes": 21_000_000,
            "game_version_pinned": pin,
        },
    )

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_api.LOCAL_DIR_NAME
    local.mkdir()
    (local / web_api.MAP_IMAGE_NAME).write_bytes(b"\x89PNG\r\n\x1a\n")
    (local / web_api.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")

    # The provenance rides along unread: the corners still come through the probe.
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"

    # The tool writes where this endpoint looks, under the names it looks for.
    assert gen_map_image.LOCAL_DIR.name == web_api.LOCAL_DIR_NAME
    assert gen_map_image.IMAGE_NAME == web_api.MAP_IMAGE_NAME
    assert gen_map_image.SIDECAR_NAME == web_api.MAP_BOUNDS_NAME
    # And it pins the same square, rather than holding a second opinion about it.
    assert gen_map_image.BOUNDS_M == web_api.DEFAULT_MAP_BOUNDS_M

    # The build survives the round trip through JSON, which is the whole of what lets a
    # stale picture be announced instead of silently drawn.
    written = json.loads((local / web_api.MAP_BOUNDS_NAME).read_text(encoding="utf-8"))
    assert gen_map_image.pinned_build(written) == pin
    assert gen_map_image.pinned_build({}) is None
    assert gen_map_image.pinned_build({"_meta": {"sources": {}}}) is None

    # The pyramid half of the same join. The names and the arithmetic belong to the cutter
    # in ``core.gameassets.pyramid`` -- what the tool contributes is the sheet and the
    # record of what came out of it -- and the endpoint configures the page's tile grid
    # from that record, so the two cannot hold different opinions about what is served.
    # The names are now IMPORTED by the endpoint rather than retyped, so the two assertions
    # that used to check them agreed have gone: they compared a name with itself. The
    # arithmetic is the part that is still a claim.
    assert pyramid_top_z(gen_map_image.SHEET_PX) == web_api.MAP_TILE_MAX_Z
    assert tile_relpath(3, 5, 6) == "3/5_6.png"
    assert web_api.map_tile_path(3, 5, 6, 5) == local / TILES_DIR_NAME / tile_relpath(3, 5, 6)

    read_back = web_api._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (PYRAMID_TILE_PX, 5)
    # And the build tag moves when the pyramid does, because that tag is what a browser
    # holding an immutable tile keys on.
    _fake_pyramid(local, max_z=0)
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] == read_back["build"]
    sidecar["_meta"]["tiles"]["count"] = 1364
    (local / web_api.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] != read_back["build"]

    # A sidecar that says nothing about tiles still serves them, at the defaults.
    (local / web_api.MAP_BOUNDS_NAME).write_text("{}", encoding="utf-8")
    bare = web_api._map_pyramid()
    assert (bare["tile_px"], bare["max_z"]) == (web_api.MAP_TILE_PX, web_api.MAP_TILE_MAX_Z)


def test_the_enhanced_pyramid_is_two_levels_deeper_and_the_server_follows_it_there(
    client, tmp_path, monkeypatch
):
    """``--enhance`` adds z6 and z7, and nothing on the serving side is hardcoded to z5.

    The depth is the sidecar's to state and the endpoint's to read: ``MAP_TILE_MAX_Z`` is
    only the answer for a sidecar that says nothing, and a pyramid that says seven must be
    served to seven. So the arithmetic is asserted where it is written down -- two levels
    for a 4x upscale, 16,384 tiles at z7, 21,845 in the whole tree -- and then a tile at
    the far corner of z7 is actually fetched through the route, with the level above it and
    the column past its edge both refused.
    """
    # Two levels for 4x, none for 1x, and a scale that is not a power of two divides no grid.
    assert enhanced_top_z(gen_map_image.SHEET_PX) == pyramid_top_z(gen_map_image.SHEET_PX) + 2 == 7
    assert enhanced_top_z(gen_map_image.SHEET_PX, 1) == 5
    assert enhanced_top_z(2048, 4) == 5
    with pytest.raises(PyramidError):
        enhanced_top_z(gen_map_image.SHEET_PX, 3)

    # z7 is 128 tiles a side of the 32768 px sheet, and the whole tree is (4**8 - 1) / 3.
    assert (1 << 7) * PYRAMID_TILE_PX == gen_map_image.SHEET_PX * gen_map_image.ENHANCE_SCALE
    assert (1 << 6) ** 2 == 4096
    assert (1 << 7) ** 2 == 16384
    assert sum(4**z for z in range(8)) == 21845

    # And the two halves of the record are merged, not appended to by hand: the count the
    # installer checks the tree against is re-summed from the levels a reader could count.
    plain = {
        "max_z": 5,
        "enhanced": False,
        "count": 1365,
        "bytes": 100,
        "levels": [{"z": z, "tiles": 4**z, "bytes": 10} for z in range(6)],
    }
    merged = merge_enhanced(
        plain,
        {
            "levels": [{"z": 6, "tiles": 4096, "bytes": 40}, {"z": 7, "tiles": 16384, "bytes": 50}],
            "enhancement": {"model": gen_map_image.ENHANCE_MODEL},
        },
    )
    assert (merged["max_z"], merged["enhanced"], merged["count"]) == (7, True, 21845)
    assert merged["bytes"] == 60 + 90
    assert merged["enhancement"]["model"] == gen_map_image.ENHANCE_MODEL
    assert plain["max_z"] == 5, "the plain record is not mutated under the caller"

    # The layout at the new depth, on both sides of the wire.
    assert tile_relpath(7, 127, 127) == "7/127_127.png"
    assert web_api.map_tile_path(7, 127, 127, 7) is not None
    assert web_api.map_tile_path(7, 128, 0, 7) is None
    assert web_api.map_tile_path(8, 0, 0, 7) is None
    # ... and the same coordinate is off the end of a pyramid that was never enhanced.
    assert web_api.map_tile_path(7, 0, 0, 5) is None

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_api.LOCAL_DIR_NAME
    local.mkdir()
    for z, x, y in ((0, 0, 0), (7, 127, 127)):
        path = local / web_api.MAP_TILES_DIR_NAME / str(z)
        path.mkdir(parents=True)
        (path / f"{x}_{y}.png").write_bytes(_PNG)
    (local / web_api.MAP_BOUNDS_NAME).write_text(
        json.dumps({"_meta": {"tiles": {"tile_px": 256, "max_z": 7, "enhanced": True}}}),
        encoding="utf-8",
    )

    # The probe the page builds its tile grid from now says seven, which is the whole of
    # what makes the browser ask for the two new levels at all.
    assert client.head("/api/maptiles/0/0/0").headers["x-map-tile-max-z"] == "7"
    assert client.get("/api/maptiles/7/127/127").status_code == 200
    assert client.get("/api/maptiles/7/128/127").status_code == 404
    assert client.get("/api/maptiles/8/0/0").status_code == 404


def test_the_faint_mask_covers_weak_strokes_and_leaves_everything_else_to_the_ai(tmp_path):
    """The hybrid's one rule, on arrays where the answer is known by construction.

    ``faint_mask`` decides where the upscaler is overruled, so what has to hold is that it
    is a blend weight (in [0, 1] everywhere, or the blend is not a blend) and that it fires
    on exactly the band it claims: nothing on flat fill, nothing on a stroke deep enough
    that the model renders it well, something on a stroke shallow enough that the model
    drops it. No GPU and no upscaler is involved -- this is the mask, not the pipeline.
    """
    numpy = pytest.importorskip("numpy")

    flat = numpy.full((48, 48), 200.0, numpy.float32)
    assert gen_map_image.faint_mask(flat).max() == 0.0, "there is nothing to protect on flat fill"

    faint = flat.copy()
    band_middle = (gen_map_image.FAINT_LO + gen_map_image.FAINT_HI) / 2
    faint[:, 24] = 200.0 - band_middle  # squarely inside the band
    weights = gen_map_image.faint_mask(faint)
    assert weights.min() >= 0.0 and weights.max() <= 1.0, "a blend weight, or it is not one"
    assert weights.max() > 0.0, "a faint stroke is exactly what the mask exists for"
    assert weights[:, 24].max() == weights.max(), "and it is centred on the stroke"
    assert weights[:, 0].max() == 0.0, "while the flat fill four columns away stays untouched"

    strong = flat.copy()
    strong[:, 24] = 40.0  # far past FAINT_HI: the AI renders this better than Lanczos does
    assert gen_map_image.faint_mask(strong).max() == 0.0

    # The band is a band, not a threshold: the same stroke at both ends of it is out.
    below = flat.copy()
    below[:, 24] = 200.0 - gen_map_image.FAINT_LO / 2
    assert gen_map_image.faint_mask(below).max() == 0.0


def test_the_presharpen_raises_the_weak_band_and_nothing_else():
    """The stage that runs BEFORE the model, on arrays where the answer is known.

    Repairing the output cannot put back a stroke the model never drew, so the faintest
    marks are raised on the way in. Three things have to hold for that to be a rule rather
    than a wash of contrast: it fires only on the weak band, it deepens the mark it fires
    on, and past the mask and the couple of pixels its feather reaches the source comes
    through untouched -- not "nearly", exactly, because the blend weight there is zero.

    And the fourth, which is the whole reason there are two masks: the pre-sharpen's band
    stops short of the repair's. A stroke between the two ceilings is protected on the way
    out and must NOT be amplified on the way in, because handing the model more contrast on
    a mid stroke is handing it something to expand.
    """
    numpy = pytest.importorskip("numpy")

    def square(depth):
        """A flat 200 fill with one column drawn ``depth`` luma below it, as RGB."""
        page = numpy.full((64, 64, 3), 200.0, numpy.float32)
        page[:, 32] = 200.0 - depth
        return page

    # A box mean over FAINT_WINDOW turns a drawn depth d into a measured 8d/9, which is
    # what both bands are expressed in -- so the drawn numbers here are scaled to land
    # squarely inside the intervals rather than near their ends.
    weak = square(6.5 * 9 / 8)
    mid = square(12.0 * 9 / 8)
    strong = square(60.0)

    # The band is a blend weight wherever it is used, so it is bounded like one.
    for page in (weak, mid, strong):
        band = gen_map_image.faint_band(
            gen_map_image.faint_depth(page.mean(2)), gen_map_image.PRESHARPEN_HI
        )
        assert band.min() >= 0.0 and band.max() <= 1.0, "a blend weight, or it is not one"

    assert not gen_map_image.presharpen_mask(numpy.full((64, 64), 200.0, numpy.float32)).any(), (
        "there is nothing to lift on flat fill"
    )
    assert gen_map_image.presharpen_mask(weak.mean(2))[:, 32].all(), (
        "the weak band is what this exists for"
    )
    assert not gen_map_image.presharpen_mask(strong.mean(2)).any(), (
        "the model renders a strong stroke well"
    )

    # The two ceilings, and the gap between them that only one mask covers.
    assert gen_map_image.PRESHARPEN_HI < gen_map_image.FAINT_HI
    assert gen_map_image.faint_mask(mid.mean(2)).max() > 0.0, (
        "the repair still protects a mid stroke"
    )
    assert not gen_map_image.presharpen_mask(mid.mean(2)).any(), (
        "and the pre-sharpen leaves it alone"
    )

    lifted, mask = gen_map_image.presharpen_pixels(weak)
    assert lifted.min() >= 0.0 and lifted.max() <= 255.0
    assert lifted[:, 32, 0].max() < weak[:, 32, 0].min(), "the mark it fires on comes out deeper"
    assert 0.0 < mask.mean() < 0.25, "and on a small part of the square, not most of it"
    # Past the mask and its feather it is the identity, not an approximation of one.
    marked = numpy.flatnonzero(mask.any(0))
    far = numpy.ones(weak.shape[1], bool)
    far[marked.min() - 3 : marked.max() + 4] = False
    assert far.sum() > 40, "and there is a real fill left over to check that on"
    assert numpy.array_equal(lifted[:, far], weak[:, far])

    untouched, empty = gen_map_image.presharpen_pixels(strong)
    assert not empty.any()
    assert numpy.array_equal(untouched, strong), "an empty mask blends nothing at all"


def test_the_colour_fix_hands_the_flat_fills_back_to_the_source():
    """The stage that runs after the repair, on a drift constructed to be recognised.

    The model's other measured defect is that it moves the colour of a flat fill -- by up
    to a whole level of the map's own palette, which is a visible step in a picture whose
    fills ARE its levels. So the output's low frequencies are replaced by the source's, and
    the two things that has to do are: put a flat fill back exactly where the source had
    it, and leave the detail the model was entitled to invent alone.
    """
    numpy = pytest.importorskip("numpy")

    source = numpy.full((128, 128, 3), 180.0, numpy.float32)
    source[:, 60:68] = 120.0  # a stroke, at the source's own depth

    drifted = source + 9.0  # the whole fill has wandered nine levels of grey
    drifted[:, 60:68] = 100.0  # and the model has deepened the stroke, which is its business

    fixed = gen_map_image.colour_fix_pixels(drifted, source)
    assert fixed.min() >= 0.0 and fixed.max() <= 255.0

    # Well away from the stroke and from the edges -- further than the blur reaches -- the
    # fill is back at the source's own value, and the drift is gone rather than reduced.
    fill = fixed[40:88, 100:124]
    assert abs(float(fill.mean()) - 180.0) < 0.01
    assert float(numpy.abs(fill - 180.0).max()) < 0.01
    assert abs(float(drifted[40:88, 100:124].mean()) - 189.0) < 0.01, "there was a drift to fix"

    # The stroke is still deeper than the source drew it: the fix protects the sharpening
    # rather than blurring it back, which is what sigma exceeding a stroke's width buys.
    assert fixed[:, 64, 0].max() < source[:, 64, 0].min()
    assert gen_map_image.COLOUR_FIX_SIGMA > 4.0, "or the blur would sit inside a stroke at 4x"

    # A source that never drifted is left where it is, to within rounding.
    assert float(numpy.abs(gen_map_image.colour_fix_pixels(source, source) - source).max()) < 1e-3


def test_an_enhanced_pyramid_is_not_quietly_replaced_by_a_plain_one(tmp_path):
    """The no-silent-downgrade rule, and the sidecar round trip it reads through.

    The cross-build guard already refuses to overwrite somebody else's artwork. This is its
    other half: a re-run that would cost the reader the two zoom levels they generated last
    time is drift too, and the same posture applies -- announce it, do not perform it. Only
    one of the four combinations is a downgrade, and asserting all four is what keeps the
    rule from quietly becoming "refuse whenever anything was enhanced".

    The flag has to survive JSON to be worth anything, so it is read back out of the file
    the tool really writes rather than out of the dict it built.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    common = dict(
        build_pin=pin,
        build_raw={"Changelist": 495413},
        image={"file": gen_map_image.IMAGE_NAME},
        integrity={},
        layout={"layout_holds": True},
        calibration={"pin_holds": True},
        versions={"pillow": "12.3.0"},
    )
    enhancement = {
        "recipe": gen_map_image.ENHANCE_RECIPE,
        "recipe_name": gen_map_image.ENHANCE_RECIPES[gen_map_image.ENHANCE_RECIPE],
        "model": gen_map_image.ENHANCE_MODEL,
        "scale": gen_map_image.ENHANCE_SCALE,
        "source_tile_px": gen_map_image.ENHANCE_TILE_PX,
        "overlap_px": gen_map_image.ENHANCE_OVERLAP_PX,
        "binary": {"url": gen_map_image.ENHANCE_URL, "sha256": gen_map_image.ENHANCE_SHA256},
        "presharpen": {
            "rounds": gen_map_image.PRESHARPEN_ROUNDS,
            "amount": gen_map_image.PRESHARPEN_AMOUNT,
            "band": [gen_map_image.FAINT_LO, gen_map_image.PRESHARPEN_HI],
            "mask_coverage": 0.0431,
        },
        "hybrid": {
            "band": [gen_map_image.FAINT_LO, gen_map_image.FAINT_HI],
            "mask_coverage": 0.0355,
        },
        "colour_fix": {"sigma_px": gen_map_image.COLOUR_FIX_SIGMA},
        "timings_s": {"upscale": 66.7, "colour_fix": 320.4, "total": 400.0},
    }
    sharp = gen_map_image.build_sidecar(
        tiles={"tile_px": 256, "max_z": 7, "enhanced": True, "enhancement": enhancement},
        **common,
    )
    plain = gen_map_image.build_sidecar(
        tiles={"tile_px": 256, "max_z": 5, "enhanced": False}, **common
    )

    path = tmp_path / gen_map_image.SIDECAR_NAME
    path.write_text(json.dumps(sharp, indent=1, allow_nan=False), encoding="utf-8")
    read_back = json.loads(path.read_text(encoding="utf-8"))
    assert gen_map_image.pinned_enhanced(read_back) is True
    # Everything the sidecar promised about that stage is still in it, and pinned.
    written = read_back["_meta"]["tiles"]["enhancement"]
    assert written["binary"]["sha256"] == gen_map_image.ENHANCE_SHA256
    assert written["binary"]["url"].endswith(".zip")
    assert (written["model"], written["scale"]) == (gen_map_image.ENHANCE_MODEL, 4)
    assert (written["source_tile_px"], written["overlap_px"]) == (1024, 96)
    assert written["timings_s"]["total"] == 400.0
    # Both new stages, with the parameters that make them reproducible, and the recipe that
    # names the whole of it -- a reader must be able to tell which pipeline cut these tiles.
    assert written["recipe"] == gen_map_image.ENHANCE_RECIPE
    assert written["recipe_name"] == gen_map_image.ENHANCE_RECIPES[gen_map_image.ENHANCE_RECIPE]
    assert (written["presharpen"]["rounds"], written["presharpen"]["amount"]) == (3, 0.14)
    assert written["presharpen"]["band"] == [gen_map_image.FAINT_LO, gen_map_image.PRESHARPEN_HI]
    assert written["hybrid"]["band"] == [gen_map_image.FAINT_LO, gen_map_image.FAINT_HI]
    assert written["presharpen"]["band"][1] < written["hybrid"]["band"][1]
    assert written["colour_fix"]["sigma_px"] == gen_map_image.COLOUR_FIX_SIGMA
    # The build pin still reads through the same file, so the two guards do not shadow.
    assert gen_map_image.pinned_build(read_back) == pin

    # Anything that is not a literal true is a plain pyramid, including every sidecar
    # written before this stage existed.
    assert gen_map_image.pinned_enhanced(plain) is False
    assert gen_map_image.pinned_enhanced({}) is False
    assert gen_map_image.pinned_enhanced({"_meta": {"tiles": {}}}) is False
    assert gen_map_image.pinned_enhanced({"_meta": {"tiles": {"enhanced": "yes"}}}) is False
    assert gen_map_image.pinned_enhanced({"_meta": {"tiles": "not a mapping"}}) is False

    # The recipe survives the same round trip, and a sidecar from before recipes existed
    # reads as the one pipeline the bare boolean can have meant.
    older = json.loads(json.dumps(gen_map_image.build_sidecar(tiles={"enhanced": True}, **common)))
    assert gen_map_image.pinned_recipe(read_back) == gen_map_image.ENHANCE_RECIPE
    assert gen_map_image.pinned_recipe(older) == gen_map_image.UNNUMBERED_RECIPE == 1
    assert gen_map_image.pinned_recipe(plain) == 0
    assert gen_map_image.pinned_recipe({}) == 0
    # Nothing but a whole number above zero is believed; the boolean decides the rest.
    for junk in (True, "2", 2.0, 0, -1, None):
        assert (
            gen_map_image.pinned_recipe({"_meta": {"tiles": {"enhancement": {"recipe": junk}}}})
            == 0
        )

    # And the rule itself, which compares recipes rather than a flag: only a run BEHIND
    # what is on disk is refused.
    assert gen_map_image.enhancement_downgrades(read_back, enhance_now=False) is True
    assert gen_map_image.enhancement_downgrades(read_back, enhance_now=True) is False
    assert gen_map_image.enhancement_downgrades(plain, enhance_now=False) is False
    assert gen_map_image.enhancement_downgrades(plain, enhance_now=True) is False
    assert gen_map_image.enhancement_downgrades({}, enhance_now=False) is False
    # An amended pipeline over the recipe it amends is an upgrade, and must not be called
    # a downgrade -- that refusal is what a re-cut with this file would otherwise hit.
    assert gen_map_image.ENHANCE_RECIPE > gen_map_image.UNNUMBERED_RECIPE
    assert gen_map_image.enhancement_downgrades(older, enhance_now=True) is False
    assert gen_map_image.enhancement_downgrades(older, enhance_now=False) is True
    # ... and the same tiles re-cut by the recipe that drew them is a refresh, not a loss.
    assert (
        gen_map_image.enhancement_downgrades(
            read_back, enhance_now=True, recipe=gen_map_image.ENHANCE_RECIPE
        )
        is False
    )
    # The one case the number adds: an older checkout over a newer recipe's tiles.
    assert gen_map_image.enhancement_downgrades(read_back, enhance_now=True, recipe=1) is True


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
    }
    assert row["name"] != row["cls"], "the class id was not joined to a building name"
    assert "." not in row["instance_leaf"]


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
    assert body["pipes"][0] == {
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
    monkeypatch.setattr(web_api, "_terrain_field", lambda: None)
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
        "machine_count",
        "attachment_count",
    }
    assert band["machine_count"] == len(band["machines"]) > 0
    assert band["attachment_count"] == len(band["attachments"]) > 0

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

    monkeypatch.setattr(web_api, "_terrain_field", _Flat)
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


def test_region_label_anchors_land_on_their_own_regions_ground(client):
    """A centroid can fall in a neighbour's cell (Titan Forest's lands in the Swamp).
    The label anchor may not: printed names and the right-click inspector must agree."""
    body = client.get("/api/regions").json()
    cell = body["cell_m"]
    letters = {name: ch for ch, name in body["legend"].items()}
    for name, entry in body["regions"].items():
        x, y = entry["label_m"]
        i = int((x - body["x0_m"]) // cell)
        j = int((y - body["y0_m"]) // cell)
        assert body["grid"][j][i] == letters[name], (name, entry["label_m"])


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
