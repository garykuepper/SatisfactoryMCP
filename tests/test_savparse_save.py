"""``read_full_save``: the composition, and the switch that lets the sidecar pick a parser.

The four layers below were each tested against their own committed fixture. Nothing tested
them *together*, and the composition is where the two decisions that can silently ruin a
parse live: the inflated body has to stay alive (every object slice is an absolute index
into it, and nothing is copied), and every failure below has to arrive as one exception
type, because the sidecar catches exactly one at the save boundary.

These tests build a complete .sav in memory out of the fixtures already committed -- the
real header prefix from ``save_header.bin``, and ``save_body.bin`` re-compressed into the
chunk stream the game writes. That is the only way to exercise the whole path with no game
install, and it means a change in any one layer that breaks the seam between two of them
fails here rather than on a machine that happens to have a 2.9 MB save.

Parity is measured elsewhere and not repeated here: the same save through both parsers
agrees on every projection field except ``lightweight_counts`` and ``structures``, which
come from the trailing class-specific bytes this parser hands on undecoded. What *is*
pinned here is that those two degrade to empty rather than to something plausible and
wrong -- see the ``actorSpecificInfo`` test, which is the whole reason that attribute is
deliberately absent.
"""

from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path

import pytest
from savparse import CHUNK_TAG, ParseError, read_full_save_bytes, read_info_bytes

FIXTURES = Path(__file__).parent / "fixtures"
HEADER_FIXTURE = FIXTURES / "save_header.bin"
BODY_FIXTURE = FIXTURES / "save_body.bin"
SIDECAR = Path(__file__).resolve().parents[1] / "sidecar" / "extract_save.py"

#: The chunk size the game writes on every save seen. Reproduced rather than shortened so
#: the assembled file is a real chunk stream and not a special case of one.
MAX_CHUNK = 131072


def _chunk_stream(body: bytes) -> bytes:
    """Compress ``body`` into the game's chunk framing.

    The 49-byte preamble writes the compressed and uncompressed sizes TWICE, identically;
    ``decompress_body`` compares the two copies, so a helper that wrote only one would be
    testing a file shape the game never produces.
    """
    out = b""
    for start in range(0, len(body), MAX_CHUNK):
        piece = body[start : start + MAX_CHUNK]
        blob = zlib.compress(piece)
        out += struct.pack(
            "<qqBqqqq",
            CHUNK_TAG,
            MAX_CHUNK,
            3,  # zlib
            len(blob),
            len(piece),
            len(blob),
            len(piece),
        )
        out += blob
    return out


@pytest.fixture(scope="module")
def header_prefix() -> bytes:
    if not HEADER_FIXTURE.is_file():
        pytest.skip("header fixture not committed")
    raw = HEADER_FIXTURE.read_bytes()
    # Exactly up to where the compressed body starts -- the fixture is a 2 KiB file prefix,
    # so it carries some of the real first chunk, which must not be left in front of ours.
    return raw[: read_info_bytes(raw).body_offset]


@pytest.fixture(scope="module")
def body() -> bytes:
    if not BODY_FIXTURE.is_file():
        pytest.skip("body fixture not committed")
    return BODY_FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def sav(header_prefix, body) -> bytes:
    return header_prefix + _chunk_stream(body)


@pytest.fixture(scope="module")
def save(sav):
    return read_full_save_bytes(sav)


def _load_sidecar(name: str):
    """Import ``extract_save`` under a private module name.

    ``importlib.reload`` would mutate the copy in ``sys.modules`` and leak the engine
    choice into every later test, and the engine is resolved at import time on purpose.
    """
    spec = importlib.util.spec_from_file_location(name, SIDECAR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- the composition


def test_a_whole_save_parses_into_the_shape_the_projection_reads(save):
    """The adapter surface, asserted as a shape rather than trusted.

    ``extract_save.iter_objects`` zips ``level.actorAndComponentObjectHeaders`` with
    ``level.objects`` and reads ``obj.properties`` as ``[name, value]`` pairs. Every one of
    those three names is an alias over a differently-spelled field, so a rename anywhere
    below would leave the sidecar walking zero objects and reporting an empty factory with
    no error at all -- which is the failure this file exists to prevent.
    """
    assert [lv.name for lv in save.levels][-1] == "Persistent_Level"
    assert len(save.levels) == 5
    assert save.object_count == 6
    assert save.warnings == []

    for level in save.levels:
        assert level.actorAndComponentObjectHeaders is level.headers
        assert len(level.headers) == len(level.objects)
        for header, obj in zip(level.headers, level.objects, strict=True):
            assert getattr(header, "instanceName", None)
            for pair in obj.properties:
                assert isinstance(pair, list) and len(pair) == 2
                assert isinstance(pair[0], str)

    # The fixture was cut to cover all three object versions; the composition must handle
    # a save that mixes them, because every real save does.
    versions = {obj.version for lv in save.levels for obj in lv.objects}
    assert versions == {36, 52, 60}


def test_properties_that_the_projection_actually_reads_come_through(save):
    """One end-to-end value, not just a shape.

    The fixture's version-36 inventory component is the single most load-bearing property
    in the projection: ``mInventoryStacks`` feeds every inventory bucket, the machine
    buffers and the slotted-shard census. A composition that returned well-formed but
    empty property lists would satisfy the shape test above and report a world with
    nothing in it.
    """
    named = {
        header.instanceName: obj
        for level in save.levels
        for header, obj in zip(level.headers, level.objects, strict=True)
    }
    stacks = [
        obj
        for obj in named.values()
        if any(pair[0] == "mInventoryStacks" for pair in obj.properties)
    ]
    assert stacks, "the fixture's FGInventoryComponent lost its stacks"
    props = dict(stacks[0].properties)
    assert isinstance(props["mInventoryStacks"], list)
    assert props["mInventoryStacks"], "an inventory with zero slots is not what was cut"


def test_the_inflated_body_is_retained_so_undecoded_bytes_stay_addressable(save, body):
    """``extra_offset`` is an index into the body, not a copy of it.

    3,209 actors on the reference save carry class-specific bytes after their property
    list -- conveyor chains, power lines, and the lightweight-buildable subsystem's 3.1 MB.
    Whoever decodes those next needs the bytes they point at. If ``ParsedSave`` stopped
    holding the body, every one of those offsets would address a buffer that had been
    freed, and the failure would look like a decoding bug rather than a lifetime bug.
    """
    assert save.body == body
    for level in save.levels:
        for obj in level.objects:
            assert obj.extra_length > 0, "every object has at least a 4-byte trailer"
            chunk = save.body[obj.extra_offset : obj.extra_offset + obj.extra_length]
            assert len(chunk) == obj.extra_length


def test_there_is_deliberately_no_actorSpecificInfo(save):
    """The two projection fields this parser cannot fill must come out EMPTY, not wrong.

    ``_lightweight`` and ``_structures`` both read ``getattr(obj, "actorSpecificInfo",
    None)``. Its absence is what makes them return ``{}`` and ``{"classes": [],
    "instances": []}`` -- a visible gap. Adding the attribute with a partial decode would
    turn that into a silent undercount: a foundation census that reports 40 slabs where
    8,347 pieces are built reads exactly like a real answer.
    """
    sidecar = _load_sidecar("_extract_save_no_asi")
    for level in save.levels:
        for obj in level.objects:
            assert getattr(obj, "actorSpecificInfo", None) is None
            assert sidecar._lightweight(obj) == {}
            assert sidecar._structures(obj) == {"classes": [], "instances": []}


# --------------------------------------------------------------- one failure type


def test_a_truncated_header_is_a_parse_error_that_names_the_save_version(sav):
    """Every layer's failure has to arrive as ``ParseError``, and say where it was.

    ``header`` and ``chunks`` predate ``ParseError`` and raised bare ``ValueError``. The
    sidecar catches one type at the save boundary, so half the failures fell through to its
    bare-``Exception`` handler and were reported as ``{"error": "ValueError"}`` -- a class
    name with no offset, for a file that is simply mid-rewrite.

    The version fields in the message are what makes a refusal actionable. 36 of the 67
    saves on the author's disk are pre-1.0, with ``saveHeaderType`` 1, 8, 9 and 10, and they
    fail here because their field layout differs -- so the reason a player sees for half
    their save folder has to be "saveHeaderType 10 (only 14 is understood)" rather than an
    offset in a file they will never open.
    """
    with pytest.raises(ParseError, match=r"saveHeaderType 14.*saveVersion 60"):
        read_full_save_bytes(sav[:100])

    fake_old = bytearray(sav[:100])
    fake_old[0:4] = struct.pack("<i", 10)
    with pytest.raises(ParseError, match=r"saveHeaderType 10 \(only 14 is understood\)"):
        read_full_save_bytes(bytes(fake_old))


def test_a_torn_chunk_stream_is_a_parse_error_with_an_offset(sav, header_prefix):
    """An autosave rewrites the file in place every few minutes, so a half-written chunk
    stream is routine. It must fail at the tear rather than inflating garbage into the object
    walk and failing somewhere unrelated a level later.

    This asserted only "runs past end" until truncating two real saves at fourteen fractions
    of their length showed that every single one of those twenty-eight tears lands here, and
    that the message named neither the chunk nor the shortfall. It now has to name both,
    because the whole point of failing at the tear is being able to see that it *was* a tear.
    """
    with pytest.raises(ParseError, match="chunk at 453 declares 1506 compressed bytes"):
        read_full_save_bytes(sav[: len(sav) - 200])
    with pytest.raises(ParseError, match="shortfall of 200"):
        read_full_save_bytes(sav[: len(sav) - 200])

    # A chunk whose tag is wrong is the same class of damage from the other direction. The
    # byte flipped is in the tag's HIGH half: the low half is PACKAGE_FILE_TAG, which the
    # header checks as the proof it walked to the right place, so damaging that instead
    # would never reach the chunk reader at all.
    mangled = bytearray(sav)
    mangled[len(header_prefix) + 4] ^= 0xFF
    with pytest.raises(ParseError, match="not a chunk stream here"):
        read_full_save_bytes(bytes(mangled))


def test_a_body_that_lies_about_its_own_size_is_a_parse_error(header_prefix, body):
    """The body's leading int64 must equal the bytes that follow it.

    This is the cheapest possible check that the chunk stream inflated to a whole body, and
    it is the one that catches a save whose last chunk was never written -- the sizes all
    agree per chunk, so nothing below this notices.
    """
    short = struct.pack("<q", len(body) - 8) + body[8:-64]
    with pytest.raises(ParseError, match="the body says it is"):
        read_full_save_bytes(header_prefix + _chunk_stream(short))


def test_a_read_past_the_end_of_the_body_is_a_parse_error_too(header_prefix, body):
    """The dullest failure and the commonest: the walk runs off the end.

    It comes from ``Reader._take``, the bottom layer, which is why ``ParseError`` is defined
    below the reader rather than beside the level walk. A bare ``ValueError`` here was the
    other half of the sidecar's misreported failures.
    """
    truncated = body[:-64]
    payload = struct.pack("<q", len(truncated) - 8) + truncated[8:]
    with pytest.raises(ParseError):
        read_full_save_bytes(header_prefix + _chunk_stream(payload))


# --------------------------------------------------------------- the engine switch


def test_the_sidecar_still_defaults_to_the_vendored_parser(monkeypatch):
    """The default must not move without a decision.

    ``lightweight_counts`` and ``structures`` are wrong-but-empty under the own parser, and
    ``structures`` is what the factory-site clustering is built on. Flipping the default is
    the user's call; this test is what makes an accidental flip a failing test rather than a
    quietly emptier world summary.
    """
    monkeypatch.delenv("SATISFACTORY_SAVPARSE", raising=False)
    sidecar = _load_sidecar("_extract_save_default")
    assert sidecar.ENGINE == sidecar.VENDOR
    assert sidecar.read_full_save.__module__.startswith("sav_parse")


def test_the_switch_selects_the_own_parser(monkeypatch):
    """Both parsers have to be reachable from one process boundary, or the parity diff is
    an argument rather than a measurement."""
    monkeypatch.setenv("SATISFACTORY_SAVPARSE", "own")
    sidecar = _load_sidecar("_extract_save_own")
    assert sidecar.ENGINE == sidecar.OWN
    assert sidecar.read_full_save is not None
    assert ParseError in sidecar.PARSE_ERROR


def test_an_unknown_engine_name_fails_loudly_at_import(monkeypatch):
    """A typo must not fall back to the vendored parser.

    Silently defaulting would make a parity run report perfect agreement between the
    vendored parser and itself, which is the one wrong answer this whole exercise cannot
    afford to produce.
    """
    monkeypatch.setenv("SATISFACTORY_SAVPARSE", "onw")
    with pytest.raises(RuntimeError, match="expected"):
        _load_sidecar("_extract_save_typo")
