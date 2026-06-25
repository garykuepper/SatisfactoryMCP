"""The extracted terrain field: its codec, its no-data rules, and who is allowed to see it.

The raster itself is 18 MB derived from Coffee Stain's cooked assets, so it is gitignored
and no test may need one. Everything here therefore runs on a **synthetic** field built in
``tmp_path`` -- three tiny rasters and a sidecar in the real format -- which is also the
only way to assert what the absent case does, since the machine that generated a real field
would otherwise never exercise it.

Five things are pinned, and each is a place a plausible-looking mistake would ship quietly:

* the codec round-trips exactly, including the wrap the row-delta relies on;
* the fill layer's no-data test is on the DECODED height, not on ``raw > 0``;
* a missing or broken field is ``None`` and never an exception;
* an elevation probe prefers the field where it has an answer, keeps the sampled
  populations intact, and says nothing where the field says nothing;
* the endpoint's JSON carries which layer answered and how good that layer is.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from satisfactory_mcp.domain.spatial import elevation
from satisfactory_mcp.domain.spatial import heightfield as hf

# --------------------------------------------------------------------------------------
# A field small enough to build in a test, in exactly the format the generator writes.
# --------------------------------------------------------------------------------------

#: The synthetic field's grid. Small, and deliberately NOT square: a width/height swap in
#: the codec or the sampler survives a square raster and nothing else.
FAKE_W, FAKE_H = 7, 5
FAKE_X0, FAKE_Y0, FAKE_SPACING = -300.0, -200.0, 100.0


def build_field(tmp_path: Path, *, water: bool = True) -> Path:
    """Write a whole synthetic field and return its directory.

    Row 0 is landscape, row 1 cliff, row 2 fill, row 3 no data, row 4 landscape under
    water -- one row per thing a reading can be, so a single field exercises all of them.
    """
    directory = tmp_path / hf.DIR_NAME
    directory.mkdir(parents=True)
    height = np.zeros((FAKE_H, FAKE_W), np.int16)
    prov = np.zeros((FAKE_H, FAKE_W), np.uint8)
    for row, (layer, value) in enumerate(
        [
            (hf.PROV_LANDSCAPE, 123),
            (hf.PROV_CLIFF, 2456),
            (hf.PROV_FILL, -78),
            (hf.PROV_NODATA, hf.NODATA),
            (hf.PROV_LANDSCAPE, -150),
        ]
    ):
        height[row, :] = value
        prov[row, :] = layer
    wet = np.full((FAKE_H, FAKE_W), hf.NODATA, np.int16)
    wet[4, :] = 20  # 2.0 m of water over ground at -15.0 m

    (directory / hf.HEIGHT_NAME).write_bytes(hf.encode_i16(height))
    (directory / hf.PROV_NAME).write_bytes(hf.encode_u8(prov))
    if water:
        (directory / hf.WATER_NAME).write_bytes(hf.encode_i16(wet))
    (directory / hf.META_NAME).write_text(
        json.dumps(
            {
                "grid": {
                    "width": FAKE_W,
                    "height": FAKE_H,
                    "spacing_cm": FAKE_SPACING,
                    "x0_cm": FAKE_X0,
                    "y0_cm": FAKE_Y0,
                },
                "nodata": hf.NODATA,
                "provenance": {
                    "0": {"name": "no data", "accuracy_m": None},
                    "1": {"name": "landscape", "accuracy_m": 0.205},
                    "3": {"name": "fill", "accuracy_m": 3.897},
                    "4": {"name": "cliff", "accuracy_m": 0.21},
                },
                "sources": {"game": {"game_version_pinned": "buildVersion 495413, a test"}},
            }
        ),
        encoding="utf-8",
    )
    return directory


def gen_module():
    """``tools/gen_world_heightmap.py``, imported by path rather than by name."""
    path = Path(__file__).resolve().parents[1] / "tools" / "gen_world_heightmap.py"
    spec = importlib.util.spec_from_file_location("gen_world_heightmap", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------------------
# The codec.
# --------------------------------------------------------------------------------------


def test_the_codec_round_trips_a_raster_exactly_including_the_wrap():
    """Row-delta plus zlib has to be lossless, and the lossy case is the interesting one.

    A delta between two int16 values does not fit in an int16 -- a cliff top beside a
    no-data texel is a swing of 65,535 -- so the format leans on two's-complement wrapping
    rather than a wider type. That is exact, and it is exact only if both halves wrap the
    same way, which no amount of ordinary data would ever reveal. The raster here therefore
    puts the extremes side by side on purpose, and a random middle everywhere else so the
    test is not just three special values.
    """
    rng = np.random.default_rng(20260731)
    grid = rng.integers(-32768, 32768, size=(37, 61)).astype(np.int16)
    grid[0, 0] = hf.NODATA
    grid[5, 10:14] = [32767, hf.NODATA, 32767, hf.NODATA]
    grid[6, :] = 0

    blob = hf.encode_i16(grid)
    assert np.array_equal(hf.decode_i16(blob, 37, 61), grid), "the round trip is not exact"

    prov = rng.integers(0, 5, size=(37, 61)).astype(np.uint8)
    assert np.array_equal(hf.decode_u8(hf.encode_u8(prov), 37, 61), prov)


def test_the_delta_is_what_makes_the_raster_small():
    """Not decoration: on the real field it is 16.45 MB against 26.72 MB, 38% of the file.

    Asserted on a synthetic surface with the property the real one has -- it is smooth, so
    neighbouring texels differ by a little where the absolute values differ by a lot. A
    codec that dropped the delta would pass every other test in this file and quietly cost
    ten megabytes, which is exactly the kind of regression nothing else here would catch.
    """
    import zlib

    y, x = np.mgrid[0:300, 0:500].astype(np.float64)
    surface = 800 * np.sin(x / 97) * np.cos(y / 61) + 400 * np.sin(x / 23 + y / 29)
    grid = surface.astype(np.int16)

    delta = len(hf.encode_i16(grid))
    plain = len(zlib.compress(grid.tobytes(), hf.ZLIB_LEVEL))
    assert delta < plain, f"the delta cost bytes instead of saving them: {delta} vs {plain}"
    assert np.array_equal(hf.decode_i16(hf.encode_i16(grid), 300, 500), grid)


def test_the_codec_refuses_a_raster_that_does_not_match_its_sidecar():
    """A shape mismatch is a mismatched pair, not a raster to reshape into whatever fits."""
    blob = hf.encode_i16(np.zeros((4, 5), np.int16))
    with pytest.raises(ValueError, match="not from one run"):
        hf.decode_i16(blob, 5, 5)
    with pytest.raises(ValueError, match="not from one run"):
        hf.decode_u8(hf.encode_u8(np.zeros((4, 5), np.uint8)), 5, 5)


def test_the_codec_refuses_the_wrong_dtype():
    """int16 in, int16 out. A float raster silently truncated is a field that is wrong."""
    with pytest.raises(TypeError):
        hf.encode_i16(np.zeros((2, 2), np.float32))
    with pytest.raises(TypeError):
        hf.encode_u8(np.zeros((2, 2), np.int16))


# --------------------------------------------------------------------------------------
# The generator's no-data rule for the fill layer.
# --------------------------------------------------------------------------------------


def test_the_fill_layers_no_data_test_is_on_the_decoded_height_not_the_raw_value():
    """The single easiest thing in the generator to get wrong, pinned.

    ``HeightData_Test``'s blank value is ``raw == 0``, and the calibration that makes it a
    height maps that to about **-522 m** -- not to zero. So ``raw > 0`` and
    ``decoded > FILL_FLOOR_CM`` look like the same test and are not, and the naive one
    admits every blank texel as a false sea floor at the bottom of the map. On the real
    2048 px raster that is 138,481 texels.

    Both halves are asserted: that the blank really does decode that low, and that the real
    ocean shelf -- which is also a small raw value, just not zero -- survives the rule that
    rejects it. A test that only checked the first would pass on a rule that rejected
    everything.
    """
    gen = gen_module()
    blank = np.zeros((2, 2), np.float32)
    z_cm, valid = gen.decode_baseline(blank)
    assert z_cm[0, 0] / 100.0 == pytest.approx(-522.8, abs=0.5), "the blank is not near -522 m"
    assert not valid.any(), "the blank value was admitted into the fill"
    assert (blank > 0).sum() == valid.sum() == 0

    # A raw value that is small but real: the ocean shelf sits just above the blank, and it
    # is exactly what the naive `raw > 0` test and the right one disagree about keeping.
    shelf = np.full(
        (2, 2),
        (gen.FILL_FLOOR_CM - gen.BASELINE_OFFSET_CM) / gen.BASELINE_SCALE_CM_PER_RAW + 0.01,
        np.float32,
    )
    _z, shelf_valid = gen.decode_baseline(shelf)
    assert shelf_valid.all(), "real low ground was rejected along with the blank"

    # And the world's own floor is above the cut, so nothing real is ever near it.
    assert gen.FILL_FLOOR_CM < -25500.0 < 0.0


# --------------------------------------------------------------------------------------
# Loading.
# --------------------------------------------------------------------------------------


def test_a_missing_field_is_none_and_never_an_error(tmp_path):
    """The default case on almost every machine, and it must cost the caller nothing."""
    assert hf.load_field(tmp_path / "nothing-here") is None


def test_a_broken_field_is_also_none_rather_than_an_exception(tmp_path):
    """A loader and only a loader: the generator diagnoses a broken field, not the server."""
    directory = build_field(tmp_path)
    (directory / hf.HEIGHT_NAME).write_bytes(b"not zlib at all")
    assert hf.load_field(directory) is None

    other = build_field(tmp_path / "second")
    (other / hf.META_NAME).write_text("{ this is not json", encoding="utf-8")
    assert hf.load_field(other) is None


def test_the_field_answers_with_the_layer_that_answered_and_its_measured_accuracy(tmp_path):
    """A reading carries its own uncertainty, because the field's layers are not alike."""
    field = hf.load_field(build_field(tmp_path))
    assert field is not None
    assert (field.width, field.height) == (FAKE_W, FAKE_H)
    assert field.build == "buildVersion 495413, a test"

    landscape = field.at(FAKE_X0, FAKE_Y0)
    assert (landscape.z_m, landscape.source, landscape.accuracy_m) == (12.3, "landscape", 0.205)
    cliff = field.at(FAKE_X0 + 3 * FAKE_SPACING, FAKE_Y0 + FAKE_SPACING)
    assert (cliff.z_m, cliff.source, cliff.accuracy_m) == (245.6, "cliff", 0.21)
    fill = field.at(FAKE_X0, FAKE_Y0 + 2 * FAKE_SPACING)
    assert (fill.z_m, fill.source, fill.accuracy_m) == (-7.8, "fill", 3.897)


def test_a_no_data_texel_and_an_off_grid_point_are_both_silence(tmp_path):
    """Two reasons to say nothing, and the caller is entitled to the same nothing for both.

    Zero is sea level and a real answer, so a no-data texel must never come back as one --
    which is the whole reason the sentinel is -32768 rather than 0.
    """
    field = hf.load_field(build_field(tmp_path))
    assert field.at(FAKE_X0, FAKE_Y0 + 3 * FAKE_SPACING) is None, "no-data read as a height"
    assert field.at(FAKE_X0 - 10 * FAKE_SPACING, FAKE_Y0) is None, "off the west edge"
    assert field.at(FAKE_X0, FAKE_Y0 + 40 * FAKE_SPACING) is None, "off the south edge"


def test_the_grid_is_vertex_aligned_so_a_reading_snaps_to_the_nearest_measurement(tmp_path):
    """Rounded, not floored. A texel IS the point ``x0 + col*spacing``, not a cell round it.

    Flooring would answer with the vertex up to a metre south-west of the question, which
    on a cliff edge is a different cliff -- and it would do it without ever looking wrong.
    """
    field = hf.load_field(build_field(tmp_path))
    assert field.texel(FAKE_X0 + 0.4 * FAKE_SPACING, FAKE_Y0) == (0, 0)
    assert field.texel(FAKE_X0 + 0.6 * FAKE_SPACING, FAKE_Y0) == (0, 1)
    assert field.texel(FAKE_X0 - 0.4 * FAKE_SPACING, FAKE_Y0) == (0, 0)


def test_water_is_a_second_surface_and_never_a_correction_to_the_ground(tmp_path):
    """The channel says a lake stands here; the ground stays exactly where the field put it.

    Measured, not stylistic: the generator's own docstring records that gating terrain on
    this detector made the field worse (nodes trim90 0.93 against 0.77), so a reading under
    water reports both numbers and moves neither.
    """
    field = hf.load_field(build_field(tmp_path))
    under = field.at(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert under.z_m == -15.0, "the ground was moved to the water surface"
    assert under.water_m == 2.0
    assert under.submerged is True
    assert field.at(FAKE_X0, FAKE_Y0).submerged is False


def test_a_field_without_a_water_channel_still_answers(tmp_path):
    """Water is decoded separately and only if asked, so its absence costs one attribute."""
    field = hf.load_field(build_field(tmp_path, water=False))
    reading = field.at(FAKE_X0, FAKE_Y0 + 4 * FAKE_SPACING)
    assert reading.z_m == -15.0
    assert reading.water_m is None and reading.submerged is False


# --------------------------------------------------------------------------------------
# The elevation probe: a fourth source, beside the populations rather than inside them.
# --------------------------------------------------------------------------------------


def _samples() -> list[elevation.Sample]:
    """Three nodes and a foundation, all within the probe radius of the field's origin."""
    return [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("node", FAKE_X0 + 100.0, FAKE_Y0, 1200.0),
        elevation.Sample("node", FAKE_X0, FAKE_Y0 + 100.0, 1400.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]


def test_a_probe_without_a_field_is_the_probe_it_always_was(tmp_path):
    """The default, and the case on almost every machine. Nothing may change for it."""
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    assert near.terrain is None and near.terrain_m is None
    assert near.ground == [10.0, 12.0, 14.0]
    assert near.built == [30.0]
    assert near.fill_m == 18.0


def test_the_field_is_a_fourth_answer_and_does_not_touch_the_sampled_populations(tmp_path):
    """The whole design in one assertion: a texel read is reported, never averaged in.

    The field says 12.3 m at this coordinate and the three nodes nearby median to 12.0 m.
    Folding the reading into ``ground`` would move that median, put a 0.2 m measurement in
    with points up to 200 m away, and cost the caller the ability to tell them apart. So
    ``ground``, ``built`` and ``fill_m`` come out bit for bit what they were without a
    field, and the reading arrives beside them with its own provenance.
    """
    field = hf.load_field(build_field(tmp_path))
    without = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0)
    near = elevation.probe(FAKE_X0, FAKE_Y0, _samples(), radius_m=200.0, terrain_field=field)

    assert near.terrain_m == 12.3
    assert near.terrain.source == "landscape"
    assert near.terrain.accuracy_m == 0.205
    assert (near.ground, near.built, near.fill_m) == (without.ground, without.built, without.fill_m)
    assert near.counts == without.counts == {"node": 3, "structure": 1}


def test_a_field_that_knows_nothing_here_leaves_the_probe_saying_nothing(tmp_path):
    """No-data must not become a number, and it must not disturb the samples either."""
    field = hf.load_field(build_field(tmp_path))
    near = elevation.probe(
        FAKE_X0, FAKE_Y0 + 3 * FAKE_SPACING, _samples(), radius_m=500.0, terrain_field=field
    )
    assert near.terrain is None and near.terrain_m is None
    assert near.ground == [10.0, 12.0, 14.0]


def test_the_field_does_not_lower_the_refusal_to_invent_a_ground_level(tmp_path):
    """``MIN_GROUND_SAMPLES`` survives the heightmap arriving, and that is deliberate.

    A terrain reading is not a ground sample. One node plus a field is still one node, and
    a fill depth quoted from it would be the invented number this module exists to refuse
    -- so ``fill_m`` stays ``None`` however good the terrain is.
    """
    field = hf.load_field(build_field(tmp_path))
    thin = [
        elevation.Sample("node", FAKE_X0, FAKE_Y0, 1000.0),
        elevation.Sample("structure", FAKE_X0, FAKE_Y0, 3000.0),
    ]
    near = elevation.probe(FAKE_X0, FAKE_Y0, thin, radius_m=200.0, terrain_field=field)
    assert near.terrain_m == 12.3, "the field answered"
    assert len(near.ground) < elevation.MIN_GROUND_SAMPLES
    assert near.fill_m is None, "one node became a ground level because a field turned up"


# --------------------------------------------------------------------------------------
# The endpoint.
# --------------------------------------------------------------------------------------


def test_the_inspect_endpoint_says_which_source_answered(tmp_path, monkeypatch):
    """The popup's whole claim, over the wire: a number, its layer, and that layer's error.

    The loader is replaced rather than the data directory pointed elsewhere, so this runs
    identically on a machine that has a real field and on one that has never had one.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import api as web_api
    from satisfactory_mcp.interfaces.web.app import create_app

    field = hf.load_field(build_field(tmp_path))
    monkeypatch.setattr(web_api, "_terrain_field", lambda: field)
    # The synthetic field is pinned at the map's south-west corner, so ask about a point
    # inside it in metres -- which is the unit the endpoint takes and the popup prints.
    x_m, y_m = FAKE_X0 / 100.0, FAKE_Y0 / 100.0

    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as client:
        body = client.get("/api/inspect", params={"x_m": x_m, "y_m": y_m}).json()

    e = body["elevation"]
    assert e["terrain_m"] == 12.3
    assert e["terrain_source"] == "landscape"
    assert e["terrain_accuracy_m"] == 0.205
    assert e["terrain_note"] is None
    assert e["terrain_water_m"] is None
    # And the populations are still there, still separate, still labelled.
    assert "ground_m" in e and "ground_count" in e and "fill_note" in e


def test_the_endpoint_says_WHY_there_is_no_terrain_rather_than_leaving_a_null(monkeypatch):
    """A null with no reason beside it reads as a bug, exactly as ``fill_note`` decided.

    Two causes, and they call for different actions from the reader: no field on this
    machine means "run the generator", and no data at this point means "there is nothing
    there". So they are different sentences.
    """
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from satisfactory_mcp.interfaces.web import api as web_api
    from satisfactory_mcp.interfaces.web.app import create_app

    monkeypatch.setattr(web_api, "_terrain_field", lambda: None)
    app = create_app(state_loader=lambda save=None, world=None: None, game_loader=lambda: None)
    with TestClient(app) as client:
        body = client.get("/api/inspect", params={"x_m": 0.0, "y_m": 0.0}).json()

    e = body["elevation"]
    assert e["terrain_m"] is None and e["terrain_source"] is None
    assert "no terrain field on this machine" in e["terrain_note"]
    assert "gen_world_heightmap.py" in e["terrain_note"]


def test_the_generator_and_the_loader_agree_on_the_file_names_and_the_grid():
    """One format, and the two halves of it must not drift apart.

    The generator imports the codec from the loader's own module rather than carrying a
    copy, which is what makes that true; this asserts the rest of the agreement -- the
    georeference the sidecar promises and the constants the generator writes it from.
    """
    gen = gen_module()
    assert gen.GRID_PX == 7500
    assert (gen.ORIGIN_X_CM, gen.ORIGIN_Y_CM, gen.SPACING_CM) == (-324700.0, -375000.0, 100.0)
    assert gen.hf is hf, "the generator must use the shipped codec, not a copy"

    # The sampler the run validates on has to be the sampler the server reads with, or the
    # validation measures something nobody ships.
    height = np.array([[10, 20, hf.NODATA]], np.int16)
    big = np.full((gen.GRID_PX, gen.GRID_PX), hf.NODATA, np.int16)
    big[0, 0:3] = height
    got = gen.sample_grid(
        big,
        np.array([gen.ORIGIN_X_CM, gen.ORIGIN_X_CM + 100.0, gen.ORIGIN_X_CM + 200.0]),
        np.array([gen.ORIGIN_Y_CM] * 3),
    )
    assert got[0] == 1.0 and got[1] == 2.0 and np.isnan(got[2])
