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
import types
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Request
from fastapi.testclient import TestClient

from satisfactory_mcp import config
from satisfactory_mcp.core.gamedata.footprint import FOUNDATION_M
from satisfactory_mcp.core.saveio.projection import World
from satisfactory_mcp.domain.world.state import WorldState
from satisfactory_mcp.interfaces.web import api as web_api
from satisfactory_mcp.interfaces.web.app import STATIC_DIR, create_app


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


def _fake_pyramid(local: Path, max_z: int = 2) -> int:
    """A tiles/ tree of tiny PNGs at the layout the generator writes. Returns the count."""
    count = 0
    for z in range(max_z + 1):
        (local / web_api.MAP_TILES_DIR_NAME / str(z)).mkdir(parents=True)
        for x in range(1 << z):
            for y in range(1 << z):
                (local / web_api.MAP_TILES_DIR_NAME / str(z) / f"{x}_{y}.png").write_bytes(_PNG)
                count += 1
    return count


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
    assert "immutable" in head.headers["cache-control"]
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


class _FakeSheet:
    """The three things ``cut_pyramid`` asks of a Pillow image, and nothing else.

    Pillow is the generator's dependency, deliberately not this project's, so the cutting
    is exercised against a stand-in: what is under test here is the tree that comes out --
    the levels, the names, the count -- not anybody's Lanczos filter.
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
    gen = _gen_map_image()
    assert gen.pyramid_top_z(8192) == 5
    assert gen.pyramid_top_z(2048) == 3
    assert gen.pyramid_top_z(256) == 0
    with pytest.raises(SystemExit):
        gen.pyramid_top_z(5000)

    tiles = tmp_path / gen.TILES_DIR_NAME
    (tiles / "9").mkdir(parents=True)
    (tiles / "9" / "0_0.png").write_bytes(b"a level the new cut does not have")
    (tmp_path / gen.TILES_STAGING / "3").mkdir(parents=True)
    (tmp_path / gen.TILES_STAGING / "3" / "0_0.png").write_bytes(b"half of a dead run")

    imaging = types.SimpleNamespace(LANCZOS="the filter, which the stand-in ignores")
    stats = gen.install_pyramid(_FakeSheet(1024), imaging, tmp_path)

    assert (stats["max_z"], stats["count"]) == (2, 1 + 4 + 16)
    assert stats["tile_px"] == gen.PYRAMID_TILE_PX
    assert not (tmp_path / gen.TILES_STAGING).exists(), "staging is not left behind"
    assert not (tmp_path / gen.TILES_RETIRED).exists(), "nor is the tree it replaced"
    assert sorted(p.name for p in tiles.iterdir()) == ["0", "1", "2"]
    assert len(list(tiles.rglob("*.png"))) == stats["count"]
    assert (tiles / gen.tile_relpath(2, 3, 3)).read_bytes() == _PNG


def _gen_map_image():
    """``tools/gen_map_image.py``, imported by path -- ``tools/`` is not a package."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "tools" / "gen_map_image.py"
    spec = importlib.util.spec_from_file_location("gen_map_image", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    gen = _gen_map_image()
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    sidecar = gen.build_sidecar(
        build_pin=pin,
        build_raw={"Changelist": 495413, "BranchName": "++FactoryGame+rel-main-1.2.0"},
        image={"file": gen.IMAGE_NAME, "width_px": gen.SHEET_PX},
        integrity={"ubulk_bytes_expected": gen.UBULK_BYTES},
        layout={"layout_holds": True},
        calibration={"pin_holds": True},
        versions={"pyooz": "0.0.8", "texture2ddecoder": "1.0.6", "pillow": "12.3.0"},
        tiles={
            "tile_px": gen.PYRAMID_TILE_PX,
            "max_z": gen.pyramid_top_z(gen.SHEET_PX),
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
    assert gen.LOCAL_DIR.name == web_api.LOCAL_DIR_NAME
    assert gen.IMAGE_NAME == web_api.MAP_IMAGE_NAME
    assert gen.SIDECAR_NAME == web_api.MAP_BOUNDS_NAME
    # And it pins the same square, rather than holding a second opinion about it.
    assert gen.BOUNDS_M == web_api.DEFAULT_MAP_BOUNDS_M

    # The build survives the round trip through JSON, which is the whole of what lets a
    # stale picture be announced instead of silently drawn.
    written = json.loads((local / web_api.MAP_BOUNDS_NAME).read_text(encoding="utf-8"))
    assert gen.pinned_build(written) == pin
    assert gen.pinned_build({}) is None
    assert gen.pinned_build({"_meta": {"sources": {}}}) is None

    # The pyramid half of the same join: the tool records how it cut the tree and the
    # endpoint configures the page's tile grid from that record, so the two cannot hold
    # different opinions about the shape of the thing being served.
    assert gen.TILES_DIR_NAME == web_api.MAP_TILES_DIR_NAME
    assert gen.PYRAMID_TILE_PX == web_api.MAP_TILE_PX
    assert gen.pyramid_top_z(gen.SHEET_PX) == web_api.MAP_TILE_MAX_Z
    assert gen.tile_relpath(3, 5, 6) == "3/5_6.png"
    assert web_api.map_tile_path(3, 5, 6, 5) == local / gen.TILES_DIR_NAME / gen.tile_relpath(
        3, 5, 6
    )

    read_back = web_api._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (gen.PYRAMID_TILE_PX, 5)
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
    assert set(row) == {"chain", "cls", "name", "lift", "items_per_min", "points_m"}
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
            assert c.get("/api/belts").json() == {"belts": [], "count": 0, "chains": 0}


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
    }
    # A piece whose class the legend cannot name is still a piece on real ground: it keeps
    # its route and loses the two things the class would have told us. `lift` is null, not
    # false -- "not a lift" would be a guess, and the two are drawn differently.
    unnamed = body["belts"][1]
    assert (unnamed["cls"], unnamed["lift"], unnamed["items_per_min"]) == (None, None, None)
    assert unnamed["points_m"] == [[7.0, 8.0, 9.0], [0.0, 0.0, 0.0]]
    assert body["belts"][2]["points_m"] == [[1.0, 2.0, 3.0]]
    assert body["chains"] == 3


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
    assert set(row) == {"network", "fluid", "fluid_name", "cls", "name", "flow_m3_min", "points_m"}
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


def test_no_pipe_row_claims_a_flow_direction(client):
    """The refusal, pinned. There is no direction in the save -- a pipe's two connectors are
    numbered rather than named input and output, and which way a fluid moves is decided at
    runtime by head lift and demand. A field here would be an invention, and a client that
    found one would draw arrows with it."""
    row = client.get("/api/pipes").json()["pipes"][0]
    assert not {"direction", "from", "to", "flow_direction", "reversed"} & set(row)


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
            assert c.get("/api/pipes").json() == {"pipes": [], "count": 0, "networks": 0}


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
