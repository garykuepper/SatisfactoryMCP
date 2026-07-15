"""Where ``tools/gen_map_image.py`` and ``tools/gen_map_renders.py`` meet the server.

A generator writes a tree and a sidecar; ``/api/maptiles`` reads them back. Nothing else
joins the two -- no shared module, no schema -- so the join is a set of file names, key names
and one piece of arithmetic, and it is asserted here against the tools' OWN output rather
than against a hand-typed sample that could drift away from what they really write.

Split out of ``test_web_tiles.py`` rather than living beside the routes: this is the tools
side of the contract, it needs numpy and scipy where the route tests need neither, and
keeping the two apart is what keeps either file readable.

``importorskip`` at module scope, not a marker: half of these tests drive the endpoint
through the ``client`` fixture, so the optional ``web`` extra is needed to collect the file.
The tree builders come from ``test_web_tiles`` -- ONE definition, and it is the one built
through the server's own directory names.
"""

from __future__ import annotations

import json
import types

import pytest

fastapi = pytest.importorskip("fastapi")

from test_web_tiles import _PNG, _fake_pyramid

from satisfactory_mcp import config
from satisfactory_mcp.core.gameassets.pyramid import (
    PYRAMID_TILE_2X_PX,
    PYRAMID_TILE_PX,
    TILES_2X_DIR_NAME,
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
from satisfactory_mcp.domain.spatial import heightfield as hf
from satisfactory_mcp.interfaces.web.routers import tiles as web_tiles
from tools import gen_map_image, gen_map_renders


def test_the_render_generator_writes_where_the_layered_route_looks(tmp_path, monkeypatch):
    """``tools/gen_map_renders.py`` and this endpoint agree about names, or nothing works.

    Nothing else joins the two, so the join is asserted against the tool's own constants and
    its own sidecar builder -- the same posture the artwork's sidecar test takes next door.
    Four names have to match, and the shape of the record the endpoint reads back has to be
    the one the tool actually writes rather than a hand-typed sample that could drift.
    """
    assert gen_map_renders.RENDERS_DIR_NAME == web_tiles.MAP_RENDERS_DIR_NAME
    assert gen_map_renders.RENDER_SIDECAR_NAME == web_tiles.MAP_RENDER_SIDECAR_NAME
    assert set(gen_map_renders.LAYERS) == set(web_tiles.MAP_RENDER_LAYERS)
    assert gen_map_renders.BOUNDS_M == web_tiles.DEFAULT_MAP_BOUNDS_M
    # The tile grid is the cutter's, not this generator's: it hands its sheet to
    # ``core.gameassets.pyramid`` and the endpoint has to be configured for what THAT cuts.
    # The tile SIZE and the directory names are no longer asserted equal, because the
    # endpoint imports them from the cutter and an assertion that a name equals itself
    # cannot fail. What is still worth pinning is the arithmetic, which is a real claim
    # about two different sheets: the default depth stays z5, which is what an 8192 sheet
    # divides into and what a pyramid whose sidecar says nothing is assumed to be, while
    # the renders are 16384 and say so in their own sidecar.
    assert pyramid_top_z(gen_map_renders.SHEET_PX) == web_tiles.MAP_TILE_MAX_Z == 5
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
    directory = tmp_path / web_tiles.LOCAL_DIR_NAME / web_tiles.MAP_RENDERS_DIR_NAME / "terrain"
    directory.mkdir(parents=True)
    (directory / web_tiles.MAP_RENDER_SIDECAR_NAME).write_text(
        json.dumps(sidecar), encoding="utf-8"
    )
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    read_back = web_tiles._map_pyramid("terrain")
    assert (read_back["tile_px"], read_back["max_z"]) == (256, 6)
    assert (read_back["tile_2x_px"], read_back["max_2x_z"]) == (512, 5)
    assert web_tiles._map_bounds("terrain") == web_tiles.DEFAULT_MAP_BOUNDS_M

    # A layer with no @2x block says so with None rather than with a zero, because zero is
    # a depth a real pyramid can have and "there is no such tree" is not a depth.
    plain = json.loads(json.dumps(sidecar))
    del plain["_meta"]["tiles_2x"]
    (directory / web_tiles.MAP_RENDER_SIDECAR_NAME).write_text(json.dumps(plain), encoding="utf-8")
    without = web_tiles._map_pyramid("terrain")
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
    # Pinned by name rather than against a subset. The old form compared this table with
    # ``REGION_PAIRS``, a list of wiki names that meant the same place as a game area; that
    # list is gone with the wiki trace, and what replaces it is the stronger claim: these
    # are the seventeen area stems build 495413 names, and every one of them has a colour.
    # ``tests/test_gameassets_maparea.py`` pins the same seventeen against the container.
    assert set(gen_map_renders.BIOME_COLOURS) == {
        "Area_AbyssCliffs",
        "Area_DesertCanyons",
        "Area_DuneDesert",
        "Area_GrassFields",
        "Area_LakeForest",
        "Area_MazeCanyons",
        "Area_NorthernForest",
        "Area_RedBambooFields",
        "Area_RedJungle",
        "Area_RockyDesert",
        "Area_Savanna",
        "Area_SouthernForest",
        "Area_SpireCoast",
        "Area_Swamp",
        "Area_TitanForest",
        "Area_WesternDuneForest",
        "Area_crater",
    }
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
    local = tmp_path / web_tiles.LOCAL_DIR_NAME
    local.mkdir()
    (local / web_tiles.MAP_IMAGE_NAME).write_bytes(b"\x89PNG\r\n\x1a\n")
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")

    # The provenance rides along unread: the corners still come through the probe.
    assert client.head("/api/mapimage").headers["x-map-bounds-m"] == "-3247.0,-3750.0,4253.0,3750.0"

    # The tool writes where this endpoint looks, under the names it looks for.
    assert gen_map_image.LOCAL_DIR.name == web_tiles.LOCAL_DIR_NAME
    assert gen_map_image.IMAGE_NAME == web_tiles.MAP_IMAGE_NAME
    assert gen_map_image.SIDECAR_NAME == web_tiles.MAP_BOUNDS_NAME
    # And it pins the same square, rather than holding a second opinion about it.
    assert gen_map_image.BOUNDS_M == web_tiles.DEFAULT_MAP_BOUNDS_M

    # The build survives the round trip through JSON, which is the whole of what lets a
    # stale picture be announced instead of silently drawn.
    written = json.loads((local / web_tiles.MAP_BOUNDS_NAME).read_text(encoding="utf-8"))
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
    assert pyramid_top_z(gen_map_image.SHEET_PX) == web_tiles.MAP_TILE_MAX_Z
    assert tile_relpath(3, 5, 6) == "3/5_6.png"
    assert web_tiles.map_tile_path(3, 5, 6, 5) == local / TILES_DIR_NAME / tile_relpath(3, 5, 6)

    read_back = web_tiles._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (PYRAMID_TILE_PX, 5)
    # And the build tag moves when the pyramid does, because that tag is what a browser
    # holding an immutable tile keys on.
    _fake_pyramid(local, max_z=0)
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] == read_back["build"]
    sidecar["_meta"]["tiles"]["count"] = 1364
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(sidecar), encoding="utf-8")
    assert client.head("/api/maptiles/0/0/0").headers["x-map-build"] != read_back["build"]

    # A sidecar that says nothing about tiles still serves them, at the defaults.
    (local / web_tiles.MAP_BOUNDS_NAME).write_text("{}", encoding="utf-8")
    bare = web_tiles._map_pyramid()
    assert (bare["tile_px"], bare["max_z"]) == (web_tiles.MAP_TILE_PX, web_tiles.MAP_TILE_MAX_Z)


def test_the_artwork_tool_writes_the_dense_tree_the_endpoint_serves(client, tmp_path, monkeypatch):
    """``gen_map_image.py`` cuts ``tiles@2x/`` now, and the endpoint reads its record of it.

    The renders have written both trees since ``/api/maptiles`` learned to serve two, and the
    artwork tool wrote only the 1x one -- so the game's own map was the single layer a hi-dpi
    display saw soft, on a client that had been density-aware for a day. This holds the join
    the same way the sidecar test above does: against the tool's OWN output, because the two
    sides agree by a key name in a JSON file and nothing else.

    Both directions are asserted. A run that cut the tree must produce a block the endpoint
    turns into a second depth; a run that did not -- ``--no-tiles-2x``, or any pyramid from
    before this -- must produce NO block at all, because ``_map_pyramid`` reads the key's
    absence as "serve every client the 1x tile" and a block saying "absent" is still a block.
    """
    pin = "buildVersion 495413 (engine branch ++FactoryGame+rel-main-1.2.0), the installed build"
    common = {
        "build_pin": pin,
        "build_raw": {"Changelist": 495413},
        "image": {"file": gen_map_image.IMAGE_NAME, "width_px": gen_map_image.SHEET_PX},
        "integrity": {},
        "layout": {},
        "calibration": {},
        "versions": {},
        "tiles": {
            "tile_px": PYRAMID_TILE_PX,
            "max_z": pyramid_top_z(gen_map_image.SHEET_PX),
            "count": 1365,
            "bytes": 21_000_000,
            "game_version_pinned": pin,
        },
    }

    # The arithmetic the tool leans on rather than typing in: the same sheet, cut into tiles
    # twice the size, is exactly one level shallower.
    dense_top = pyramid_top_z(gen_map_image.SHEET_PX, PYRAMID_TILE_2X_PX)
    assert dense_top == pyramid_top_z(gen_map_image.SHEET_PX) - 1 == 4

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_tiles.LOCAL_DIR_NAME
    local.mkdir()
    _fake_pyramid(local, max_z=0)  # something for the probe to answer about

    with_dense = gen_map_image.build_sidecar(
        **common,
        tiles_2x={
            "tile_px": PYRAMID_TILE_2X_PX,
            "max_z": dense_top,
            "count": 341,
            "bytes": 20_000_000,
            "game_version_pinned": pin,
        },
    )
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(with_dense), encoding="utf-8")
    read_back = web_tiles._map_pyramid()
    assert (read_back["tile_px"], read_back["max_z"]) == (PYRAMID_TILE_PX, 5)
    assert (read_back["tile_2x_px"], read_back["max_2x_z"]) == (PYRAMID_TILE_2X_PX, 4)
    assert client.head("/api/maptiles/0/0/0").headers["x-map-tile-2x-max-z"] == "4"

    # ...and the same run with the tree skipped writes no key, which is what makes the
    # endpoint fall back rather than advertise a depth for a directory that is not there.
    without = gen_map_image.build_sidecar(**common, tiles_2x=None)
    assert "tiles_2x" not in without["_meta"]
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(json.dumps(without), encoding="utf-8")
    bare = web_tiles._map_pyramid()
    assert bare["max_2x_z"] is None and bare["tile_2x_px"] is None
    assert "x-map-tile-2x-px" not in client.head("/api/maptiles/0/0/0").headers

    # The two trees' numbers ride in one cache tag, so re-cutting either moves every URL.
    assert read_back["build"] != bare["build"]


def test_the_artwork_tool_offers_the_opt_out_it_documents(tmp_path):
    """The flag is spelled one way in the help and one way in ``main``, and they must agree.

    Run as ``--help`` in a child, which is the only place argparse's own answer lives: the
    parser is built inside ``main`` and there is no object to interrogate from here. It is
    also the cheapest possible run of the tool -- argparse exits before ``require_gen``, so
    this needs neither the ``gen`` extra nor a game install.
    """
    import subprocess
    import sys

    from conftest import REPO_ROOT

    out = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "gen_map_image.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "--no-tiles-2x" in out.stdout
    # Named after the directory it skips rather than after a spelling of its own.
    assert TILES_2X_DIR_NAME in out.stdout


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
    assert web_tiles.map_tile_path(7, 127, 127, 7) is not None
    assert web_tiles.map_tile_path(7, 128, 0, 7) is None
    assert web_tiles.map_tile_path(8, 0, 0, 7) is None
    # ... and the same coordinate is off the end of a pyramid that was never enhanced.
    assert web_tiles.map_tile_path(7, 0, 0, 5) is None

    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    local = tmp_path / web_tiles.LOCAL_DIR_NAME
    local.mkdir()
    for z, x, y in ((0, 0, 0), (7, 127, 127)):
        path = local / web_tiles.MAP_TILES_DIR_NAME / str(z)
        path.mkdir(parents=True)
        (path / f"{x}_{y}.png").write_bytes(_PNG)
    (local / web_tiles.MAP_BOUNDS_NAME).write_text(
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
