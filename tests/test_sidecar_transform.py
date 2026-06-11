"""Schemas 12 and 13: the placement yaw, the belt splines and the pipe splines.

All three are geometry the parser already decoded and the projection threw away, and all three
are here for the same reason -- a client could only draw the world axis-aligned, beltless and
unplumbed, so an angled platform came out as a staircase and a factory came out as a scatter of
rectangles.

**What is actually worth testing about a coordinate.** That a number is present says nothing;
the failure modes are all silent and all about convention. A yaw with the wrong sign, or read
off the wrong axis, is still a plausible float on every record. A belt or pipe spline left in
its actor's own frame is still a plausible polyline -- 1,500 m from where the thing is. So the
tests below check the numbers against *other* numbers in the same save: yaw against the
direction neighbouring foundations are actually laid out in, belt and pipe points against where
the buildings are. Each one fails on the mistake it is named for.

Fixture-only, plus the committed trailer bytes, so this runs with no game install.
"""

from __future__ import annotations

import math
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

import pytest

from pioneersav import ParseError, Reader, read_trailer
from pioneersav.trailers import CONVEYOR_CHAIN
from satisfactory_mcp.core.saveio.extract import (
    PIPE_CLASSES,
    _belts,
    _conveyor_class,
    _pipes,
    yaw_of,
)

FIXTURES = Path(__file__).parent / "fixtures"

#: The 8 m grid every foundation sits on. Two pieces exactly this far apart are neighbours in
#: one row of one platform, which is what makes their bearing a measurement of that platform's
#: rotation rather than of anything else.
TILE_CM = 800.0

#: Records that carry a placed building's own transform.
PLACED = ("machines", "extractors", "generators")


class Chain:
    """A stand-in conveyor chain: ``_belts`` reads nothing off an actor but this attribute.

    The camel case is the parser's spelling, not a slip -- ``ParsedObject`` exposes
    ``actorSpecificInfo``, and a stand-in has to answer to the same name.
    """

    def __init__(self, info) -> None:
        self._info = info

    @property
    def actorSpecificInfo(self):
        return self._info


class Unreadable(Chain):
    """A chain whose trailing bytes raise on access, the way a torn trailer does."""

    @property
    def actorSpecificInfo(self):
        raise ParseError("at body offset 0: conveyor chain left 12 trailing bytes unread")


def _trailer_chains() -> list:
    """The real chain records out of ``fixtures/save_trailers.bin``.

    Container format, as ``test_savparse_trailers`` documents it: an int32 count, then per
    entry a length-prefixed class path, an int32 length and that many bytes.
    """
    path = FIXTURES / "save_trailers.bin"
    if not path.is_file():
        pytest.skip("trailer fixture not committed")
    raw = path.read_bytes()
    r = Reader(raw)
    out = []
    for _ in range(r.i32()):
        cls = r.string()
        blob = r.bytes(r.i32())
        if cls == CONVEYOR_CHAIN:
            out.append(read_trailer(CONVEYOR_CHAIN, blob, 0, len(blob)))
    return out


# --------------------------------------------------------------------------------- yaw


def test_yaw_reads_the_axis_and_the_sign_the_world_is_actually_built_on(projection):
    """The convention, held against the geometry instead of against the formula.

    Foundations that share a yaw and sit exactly one 8 m tile apart are two pieces in one row
    of one platform, so the bearing from one to the other IS that platform's rotation, modulo
    the 90 degrees of the square grid. On the reference world 4,631 of 8,347 pieces sit at a
    yaw that is not a multiple of 90 -- the angled platforms this whole field exists for -- so
    there is plenty to check against.

    This is the test that fails if the yaw is negated, taken about X or Y, or measured from the
    wrong axis: every one of those still produces a tidy float on every record, and all of them
    disagree with where the pieces are.
    """
    classes = projection["structures"]["classes"]
    rows = [r for r in projection["structures"]["instances"] if "Foundation" in classes[r[0]]]
    groups: dict[float, list] = defaultdict(list)
    for row in rows:
        groups[row[4]].append(row)

    angled = sorted(
        ((yaw, g) for yaw, g in groups.items() if yaw % 90 and len(g) >= 100),
        key=lambda kv: -len(kv[1]),
    )[:4]
    assert len(angled) == 4, "the fixture no longer has four angled platforms to check against"

    for yaw, group in angled:
        bearings = []
        for i, a in enumerate(group[:300]):
            for b in group[i + 1 : 300]:
                dx, dy = b[1] - a[1], b[2] - a[2]
                if abs(math.hypot(dx, dy) - TILE_CM) < 2.0:
                    bearings.append(math.degrees(math.atan2(dy, dx)) % 90.0)
        assert len(bearings) >= 50, f"yaw {yaw}: only {len(bearings)} tile-spaced pairs"
        worst = max(abs(b - yaw % 90.0) for b in bearings)
        # The bound is the projection's own rounding floor, not a fudge: positions are whole
        # centimetres, so a bearing over an 800 cm baseline can be up to
        # degrees(atan(2/800)) = 0.143 off. Against the raw float positions the same pairs
        # agree to 0.002. The mistakes this catches miss by 20 to 140 degrees.
        assert worst < 0.15, f"yaw {yaw}: a tile-spaced neighbour lies {worst} degrees off it"


def test_every_placed_record_carries_a_yaw(projection):
    """Present on all four record kinds, and always present rather than only when non-zero.

    An absent yaw has to mean "this projection predates schema 12", never "this building is
    unrotated" -- a consumer cannot tell those apart after the fact, and the world is full of
    genuinely unrotated buildings.
    """
    for key in PLACED:
        records = projection[key]
        assert records, f"{key} is empty in the fixture"
        assert all("yaw" in r for r in records), f"{key}: a record with no yaw"
        assert all(-180.0 <= r["yaw"] <= 180.0 for r in records), f"{key}: yaw out of range"
        assert any(r["yaw"] for r in records), f"{key}: every yaw is zero, which is not this save"

    rows = projection["structures"]["instances"]
    assert rows and all(len(r) == 5 for r in rows), "a structure row without its yaw column"
    assert all(-180.0 <= r[4] <= 180.0 for r in rows)
    assert sum(1 for r in rows if r[4] % 90) == 4631, "the angled pieces of the reference world"


def test_a_half_turn_has_one_spelling(projection):
    """``-180`` and ``180`` are the same facing, and only one of them is emitted.

    Actor headers store the quaternion as float32, so a half turn lands a hair below 180 and
    rounds to ``-180.0``; lightweight buildables store float64 and land on ``180.0``. Without
    the fold the same rotation reads as two different numbers depending on which list a piece
    came from, and anything grouping by yaw sees both.
    """
    assert yaw_of((0.0, 0.0, 1.0, -4.371139e-08)) == 180.0, "the float32 half turn"
    assert yaw_of((0.0, 0.0, 1.0, 0.0)) == 180.0
    yaws = {r["yaw"] for key in PLACED for r in projection[key]}
    yaws |= {r[4] for r in projection["structures"]["instances"]}
    assert 180.0 in yaws and -180.0 not in yaws


def test_yaw_of_a_quaternion_turns_x_towards_y():
    """The unit statement of the convention the fixture test measures."""
    assert yaw_of((0.0, 0.0, 0.0, 1.0)) == 0.0, "identity is unrotated, not unknown"
    assert yaw_of((0.0, 0.0, math.sin(math.radians(45)), math.cos(math.radians(45)))) == 90.0
    assert yaw_of((0.0, 0.0, math.sin(math.radians(-45)), math.cos(math.radians(-45)))) == -90.0
    assert yaw_of((0.0, 0.0, math.sin(math.radians(-10)), math.cos(math.radians(-10)))) == -20.0


def test_a_rotation_that_is_not_one_costs_a_yaw_and_not_the_projection():
    """``yaw_of`` runs on whatever the decoder produced, so it must answer for any shape."""
    assert yaw_of(None) == 0.0
    assert yaw_of(()) == 0.0
    assert yaw_of((0.0, 0.0, 1.0)) == 0.0
    assert yaw_of(("x", "y", "z", "w")) == 0.0


# ------------------------------------------------------------------------------- belts


def test_the_belts_key_is_interned_polylines_in_whole_centimetres(projection):
    """The shape, field by field: ``[chainIndex, classIndex, [[x, y, z], ...]]``."""
    belts = projection["belts"]
    classes = belts["classes"]
    rows = belts["segments"]
    assert classes and all(c.startswith("Build_Conveyor") and c.endswith("_C") for c in classes)
    assert rows, "the reference world has 3,085 belt pieces"

    seen_chains = set()
    for chain, ci, points in rows:
        seen_chains.add(chain)
        assert 0 <= ci < len(classes)
        assert len(points) >= 2, "a polyline needs two points"
        for p in points:
            assert len(p) == 3 and all(isinstance(c, int) for c in p), p
    assert seen_chains == set(range(len(seen_chains))), "chain indices are dense and start at 0"
    assert [r[0] for r in rows] == sorted(r[0] for r in rows), "a chain's rows are contiguous"


def test_belts_are_placed_in_the_world_and_not_in_the_chains_own_frame(projection):
    """The mistake this field is one line away from, and it looks like nothing downstream.

    A chain's spline is stored relative to the chain actor, so the points arrive within a few
    tens of metres of the origin -- a perfectly well-formed polyline drawn on empty ocean, a
    kilometre or more from the factory. Checking them against the foundations is what catches
    it: belts run over floor.
    """
    points = [p for _, _, pts in projection["belts"]["segments"] for p in pts]
    rows = projection["structures"]["instances"]
    for axis in (0, 1, 2):
        lo = min(r[axis + 1] for r in rows) - 20_000
        hi = max(r[axis + 1] for r in rows) + 20_000
        assert lo <= min(p[axis] for p in points), f"belts run off axis {axis} at the low end"
        assert max(p[axis] for p in points) <= hi, f"belts run off axis {axis} at the high end"
    # And they are nowhere near the origin, which is where the un-translated points would be.
    assert min(math.hypot(p[0], p[1]) for p in points) > 20_000


def test_belts_come_out_in_travel_order(projection):
    """Segments are stored output-first and are emitted reversed, so a chain reads as a line.

    The falsifier is in the test: put the rows of a chain back in file order and the joins
    that were coincident become thousands of centimetres apart. The 80 joins that are not
    exactly coincident are the conveyor-lift junctions ``pioneersav.trailers`` measures at
    200/300/400 cm of offset range with no spline behind it.
    """
    by_chain: dict[int, list] = defaultdict(list)
    for chain, ci, points in projection["belts"]["segments"]:
        by_chain[chain].append(points)

    joins = [math.dist(a[-1], b[0]) for segs in by_chain.values() for a, b in pairwise(segs)]
    assert len(joins) > 1000
    assert max(joins) <= 401.0, "a chain breaks apart in the middle"
    assert sum(1 for g in joins if g == 0.0) / len(joins) > 0.9

    backwards = [
        math.dist(a[-1], b[0]) for segs in by_chain.values() for a, b in pairwise(segs[::-1])
    ]
    assert sorted(backwards)[len(backwards) // 2] > 1000.0, "file order is not travel order"


def test_lifts_are_carried_as_belts_are(projection):
    """A conveyor lift is the vertical connector between two floors, and §16b needs it.

    It is not a separate record: a lift is a segment of an ordinary chain, told apart by its
    class alone, which is why the class is interned per segment and not per chain.
    """
    classes = projection["belts"]["classes"]
    lifts = [r for r in projection["belts"]["segments"] if "Lift" in classes[r[1]]]
    assert len(lifts) == 302, "the reference world's conveyor lifts"
    rises = [abs(r[2][-1][2] - r[2][0][2]) for r in lifts]
    assert sum(1 for r in rises if r > 100) > len(rises) // 2, "a lift should mostly go up"


def test_belts_out_of_real_trailing_bytes(projection):
    """``_belts`` against the committed chain records, not against a mock of them.

    ``fixtures/save_trailers.bin`` holds two real ``FGConveyorChainActor`` trailers, which is
    what makes this a test of the decoder-to-projection seam rather than of a hand-built list.
    """
    chains = _trailer_chains()
    assert chains, "no chain records in the trailer fixture"
    out = _belts([([1000.0, 2000.0, 3000.0], Chain(info)) for info in chains])
    assert out["classes"] and out["segments"]
    assert {r[0] for r in out["segments"]} == set(range(len(chains)))
    for chain, ci, points in out["segments"]:
        assert out["classes"][ci].startswith("Build_Conveyor")
        assert len(points) >= 2

    # The same records with the actor at the origin: every point moves by exactly the offset,
    # which is the whole of what the frame correction does.
    at_origin = _belts([([0.0, 0.0, 0.0], Chain(info)) for info in chains])
    for moved, base in zip(out["segments"], at_origin["segments"]):
        assert [[p[0] - 1000, p[1] - 2000, p[2] - 3000] for p in moved[2]] == base[2]


def test_a_chain_that_will_not_decode_costs_that_chain_and_not_the_save():
    """Belts are new, so a save that projected yesterday must still project today.

    A trailer decodes lazily, so a malformed one raises here rather than at the save boundary.
    Letting it out would turn one unreadable belt into a world the server cannot open at all.
    """
    chains = _trailer_chains()
    out = _belts(
        [
            ([0.0, 0.0, 0.0], Unreadable(None)),
            *(([0.0, 0.0, 0.0], Chain(info)) for info in chains),
        ]
    )
    assert {r[0] for r in out["segments"]} == set(range(len(chains))), "indices stay dense"


def test_a_chain_of_nothing_recognisable_is_dropped_rather_than_raising():
    """Same reasoning as ``_placed``: this runs on whatever the decoder produced."""
    assert _belts([]) == {"classes": [], "segments": []}
    assert _belts([(None, Chain(None))]) == {"classes": [], "segments": []}
    assert _belts([([0.0, 0.0, 0.0], Chain([1, 2]))]) == {"classes": [], "segments": []}
    assert _belts([(["x", 0.0, 0.0], Chain([1, 2, []]))]) == {"classes": [], "segments": []}


def test_a_belts_class_comes_off_the_instance_name_with_its_C_intact():
    """The class names have to match the ones ``structures`` and ``building_counts`` use."""
    assert (
        _conveyor_class("Persistent_Level:PersistentLevel.Build_ConveyorBeltMk3_C_1264")
        == "Build_ConveyorBeltMk3_C"
    )
    assert _conveyor_class("Build_ConveyorLiftMk4_C_2147") == "Build_ConveyorLiftMk4_C"
    assert _conveyor_class("") == ""


# ------------------------------------------------------------------------------- pipes


def _spline(*points) -> list:
    """An ``mSplineData`` the way the parser hands it over: a list of struct entries, each
    ``[values, propertyTypes]``, with a ``Location`` among the values.

    The tangents are in here because the real property has them and ``_pipes`` has to ignore
    them: a reader that took field 0 positionally rather than by name would pass every test
    written against a Location-only stand-in and draw the world's curvature as its geometry.
    """
    out = []
    for p in points:
        values = [
            ["Location", list(p)],
            ["ArriveTangent", [0.0, 50.0, 0.0]],
            ["LeaveTangent", [0.0, 50.0, 0.0]],
        ]
        types = [
            [n, "StructProperty", 1, "Vector", 1, "/Script/CoreUObject", 0, 8] for n, _ in values
        ]
        out.append([values, types])
    return out


def test_the_pipes_key_is_interned_polylines_in_whole_centimetres(projection):
    """The shape, field by field: ``[networkIndex, classIndex, [[x, y, z], ...]]``."""
    pipes = projection["pipes"]
    classes = pipes["classes"]
    networks = pipes["networks"]
    rows = pipes["segments"]
    assert set(classes) <= set(PIPE_CLASSES) and classes
    assert rows, "the reference world has 503 pipes"
    assert networks, "and 19 pipe networks"

    for net, ci, points in rows:
        assert 0 <= ci < len(classes)
        assert -1 <= net < len(networks)
        assert len(points) >= 2, "a polyline needs two points"
        for p in points:
            assert len(p) == 3 and all(isinstance(c, int) for c in p), p


def test_a_pipe_is_a_fluid_pipe_and_a_hypertube_is_not(projection):
    """``Build_PipeHyper_C`` carries the identical ``mSplineData`` and belongs to no plumbing.

    This is the test that fails if the class filter becomes a substring match on ``Pipe``:
    the reference world has 60 hypertube segments and 215 pipeline supports, both of which
    would then be drawn as pipes, and one of them even has geometry to draw.
    """
    classes = set(projection["pipes"]["classes"])
    assert "Build_PipeHyper_C" not in classes
    assert "Build_PipelineSupport_C" not in classes
    assert projection["building_counts"]["Build_PipeHyper_C"] == 60, "and they are in the world"
    # Every pipeline class the census found IS drawn, so the filter is not merely narrow.
    built = {c for c in projection["building_counts"] if c in PIPE_CLASSES}
    assert classes == built
    assert sum(projection["building_counts"][c] for c in built) == len(
        projection["pipes"]["segments"]
    ), "every pipe in the census is a row"


def test_pipes_are_placed_in_the_world_and_not_in_the_actors_own_frame(projection):
    """The mistake this field is one line away from, and it looks like nothing downstream.

    Every pipe's spline is stored relative to its own actor -- the first point of all 503 is
    exactly ``(0, 0, 0)`` -- so an untranslated network is 503 perfectly well-formed polylines
    piled on the map origin, out at sea. Checking them against the foundations is what catches
    it, the same way the belts above are checked.
    """
    points = [p for _, _, pts in projection["pipes"]["segments"] for p in pts]
    rows = projection["structures"]["instances"]
    for axis in (0, 1, 2):
        lo = min(r[axis + 1] for r in rows) - 20_000
        hi = max(r[axis + 1] for r in rows) + 20_000
        assert lo <= min(p[axis] for p in points), f"pipes run off axis {axis} at the low end"
        assert max(p[axis] for p in points) <= hi, f"pipes run off axis {axis} at the high end"
    assert min(math.hypot(p[0], p[1]) for p in points) > 20_000


def test_every_pipe_belongs_to_a_network_that_names_a_fluid(projection):
    """What a pipe has and a belt does not: the game's own answer to what is inside it."""
    networks = projection["pipes"]["networks"]
    rows = projection["pipes"]["segments"]
    assert all(n["fluid"] for n in networks), "a network with no fluid on this world"
    assert all(isinstance(n["id"], int) for n in networks)
    assert all(net >= 0 for net, _, _ in rows), "every pipe here is claimed by a network"
    fluids = {networks[net]["fluid"] for net, _, _ in rows}
    assert len(fluids) > 1 and all(f.startswith("Desc_") for f in fluids)


def test_no_pipe_is_vertical_so_none_needs_a_glyph(projection):
    """The measurement the client's drawing rests on, and the counterpart of the lifts above.

    A belt network needs a ring for its lifts because a lift's top-down polyline is a single
    point. Pipes have no such piece: the tightest of the 503 still spans 11.6 cm horizontally,
    so every one of them is drawable as a line and the layer needs no second glyph.
    """
    spans = [
        math.dist(
            (min(p[0] for p in pts), min(p[1] for p in pts)),
            (max(p[0] for p in pts), max(p[1] for p in pts)),
        )
        for _, _, pts in projection["pipes"]["segments"]
    ]
    assert min(spans) > 10.0, "a pipe with no horizontal extent would draw as nothing"


def test_pipes_are_translated_by_their_actor_and_not_rotated_by_it():
    """The frame correction itself, isolated: move the actor and every point moves with it.

    All 18,069 pipeline actors across the 66 saves on this disk carry an identity rotation, so
    the correction is a translation and nothing else. This pins that: the same spline read at
    two actor positions differs by exactly the offset, on every axis, with no rounding drift --
    which is what makes the whole-centimetre rounding commutative with the translation.
    """
    spline = _spline((0.0, 0.0, 0.0), (0.0, 100.0, 0.0), (0.0, 100.0, 250.0))
    nets = [(3, "Desc_Water_C", ["Persistent_Level:PersistentLevel.Build_Pipeline_C_1"])]
    base = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (0, 0, 0),
                spline,
            )
        ],
        nets,
    )
    moved = _pipes(
        [
            (
                "Build_Pipeline_C",
                "Persistent_Level:PersistentLevel.Build_Pipeline_C_1",
                (1000.5, -2000.5, 3000.0),
                spline,
            )
        ],
        nets,
    )
    assert base["segments"][0][2] == [[0, 0, 0], [0, 100, 0], [0, 100, 250]]
    assert [[p[0] - 1000, p[1] + 2000, p[2] - 3000] for p in moved["segments"][0][2]] == base[
        "segments"
    ][0][2]
    # And the network the member list claims it for, resolved by instance name.
    assert base["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]
    assert base["segments"][0][0] == 0


def test_a_pipe_no_network_claims_is_still_drawn():
    """``-1``, not dropped: an unclaimed pipe is a pipe on real ground whose contents are
    unknown, and a half-built or drained network is exactly how one arises."""
    out = _pipes(
        [
            (
                "Build_PipelineMK2_C",
                "x.Build_PipelineMK2_C_9",
                (0, 0, 0),
                _spline((0, 0, 0), (0, 800, 0)),
            )
        ],
        [(3, "Desc_Water_C", ["x.Build_Pipeline_C_1"])],
    )
    assert out["segments"] == [[-1, 0, [[0, 0, 0], [0, 800, 0]]]]
    assert out["networks"] == [{"id": 3, "fluid": "Desc_Water_C"}]


def test_a_pipe_of_nothing_recognisable_is_dropped_rather_than_raising():
    """Same reasoning as ``_belts``: this runs on whatever the property decoder produced."""
    empty = {"classes": [], "networks": [], "segments": []}
    assert _pipes([], []) == empty
    assert _pipes([("Build_Pipeline_C", "i", (0, 0, 0), None)], []) == empty
    assert _pipes([("Build_Pipeline_C", "i", None, _spline((0, 0, 0), (1, 1, 1)))], []) == empty
    assert _pipes([("Build_Pipeline_C", "i", ("x", 0, 0), _spline((0, 0, 0)))], []) == empty
    # One point is not a route, the same bar the belts set.
    assert _pipes([("Build_Pipeline_C", "i", (0, 0, 0), _spline((0, 0, 0)))], []) == empty
    # A struct with no Location among its fields costs that point, not the pipe.
    assert _pipes(
        [
            (
                "Build_Pipeline_C",
                "i",
                (0, 0, 0),
                [[[["ArriveTangent", [1, 2, 3]]], []], *_spline((0, 0, 0), (0, 400, 0))],
            )
        ],
        [],
    )["segments"] == [[-1, 0, [[0, 0, 0], [0, 400, 0]]]]
    # A network whose id is not an integer keeps its fluid and loses its id.
    assert _pipes([], [(None, "Desc_Water_C", [])])["networks"] == [
        {"id": None, "fluid": "Desc_Water_C"}
    ]
