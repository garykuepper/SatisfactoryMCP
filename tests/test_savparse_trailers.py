"""The class-specific bytes trailing an actor's property list, for the seven non-foundation classes.

Conveyor chains and their three RepSize variants, power lines, and the circuit and player-state
subsystems. These were decoded so that all eight classes carrying trailing bytes are accounted
for, which is what lets an actor's trailer be length-checked the way a component's already is;
the projection read none of them at the time. It reads the chains' spline geometry now, as
``belts`` -- ``test_sidecar_transform`` covers that seam, and everything below stays a test of
the bytes rather than of what anyone does with them.

**How the layout was established, and therefore what these tests are really pinning.** There
are no separators and no per-field lengths: a record is right only if the walk ends exactly
where the object declared its trailing bytes end. All 87,973 records of these classes across
the 31 readable saves consume exactly, at both save versions. Field *meanings* came from
relations that have to hold over every record -- the item ring's arithmetic, the segment offsets
chaining end-to-start -- and the fields where no relation was found are called ``unknown``
rather than named plausibly.

``fixtures/save_trailers.bin``, 1,895 bytes, holds one real record per class, smallest of each,
plus a second chain that actually carries items: an empty item ring and a populated one are
different readings. Container format, read only here: an int32 count, then per entry the class
path as a length-prefixed string, an int32 length, and that many bytes.

**One byte range is not the game's.** The player-state record ends with a real 64-bit account
id, so the fixture's is zeroed. Every field width, and the length check that guards the blob,
survive that; the repository does not carry someone's account number.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pioneersav import ParsedObject, ParseError, Reader, read_trailer
from pioneersav.trailers import (
    CIRCUIT_SUBSYSTEM,
    CONVEYOR_CHAIN,
    PLAYER_STATE,
    POWER_LINE,
    TRAILER_READERS,
)

FIXTURE = Path(__file__).parent / "fixtures" / "save_trailers.bin"


@pytest.fixture(scope="module")
def blobs() -> list[tuple[str, bytes]]:
    """Every fixture entry as ``(classPath, bytes)``, in file order."""
    if not FIXTURE.is_file():
        pytest.skip("trailer fixture not committed")
    r = Reader(FIXTURE.read_bytes())
    out = []
    for _ in range(r.i32()):
        cls = r.string()
        out.append((cls, r.bytes(r.i32())))
    return out


def _one(blobs, class_path: str, *, index: int = 0):
    picked = [b for cls, b in blobs if cls == class_path]
    blob = picked[index]
    return read_trailer(class_path, blob, 0, len(blob))


@pytest.fixture(scope="module")
def chains(blobs) -> list:
    return [read_trailer(CONVEYOR_CHAIN, b, 0, len(b)) for cls, b in blobs if cls == CONVEYOR_CHAIN]


# ------------------------------------------------------------- the registry


def test_the_repsize_variants_share_the_chain_reader():
    """The three variants are the same actor with a bigger replication budget. Verified over
    all 540 of them on disk rather than assumed, and kept out of the fixture because their
    smallest record is 5.8 KB and adds no code path."""
    for suffix in ("_RepSizeMedium", "_RepSizeLarge", "_RepSizeHuge"):
        assert TRAILER_READERS[f"{CONVEYOR_CHAIN}{suffix}"] is TRAILER_READERS[CONVEYOR_CHAIN]


def test_the_registry_covers_exactly_the_classes_that_carry_trailing_bytes():
    """Eight classes in a save carry them; the lightweight subsystem has its own module, so
    seven belong here. A class appearing that this does not know is the interesting failure,
    and it shows up as `actorSpecificInfo` being None rather than as an error."""
    assert len(TRAILER_READERS) == 7
    assert set(TRAILER_READERS) >= {POWER_LINE, CIRCUIT_SUBSYSTEM, PLAYER_STATE}


# ------------------------------------------------------------- conveyor chains


def test_a_chain_names_the_belts_it_spans(chains):
    for first, last, _segments, _chain, _items in chains:
        assert "Build_ConveyorBelt" in str(first)
        assert "Build_ConveyorBelt" in str(last)


def test_a_segment_carries_a_spline_of_three_vectors_per_point(chains):
    """Location, then two tangents, each three doubles. Read as float32 the walk would still
    consume the right number of bytes for a while and then desynchronise, so the check that
    matters is on the values: coordinates, not denormals."""
    for _f, _l, segments, _c, _i in chains:
        for _owner, _belt, points, *_rest in segments:
            assert points, "a segment always has spline points"
            for point in points:
                assert len(point) == 3
                assert all(len(vec) == 3 for vec in point)
                assert all(abs(x) < 1e7 for vec in point for x in vec)


def test_segment_offsets_chain_end_to_start_and_end_at_the_length(chains):
    """The relation that made these three floats readable: the last segment starts at 0, each
    segment's start is the previous one's end, and the first one's end is the whole chain's
    length. True of every chain on disk."""
    for _f, _l, segments, chain, _i in chains:
        starts = [s[4] for s in segments]
        ends = [s[5] for s in segments]
        assert starts[-1] == pytest.approx(0.0, abs=1e-3)
        assert chain[0] == pytest.approx(ends[0], abs=1e-3)
        for k in range(len(segments) - 1):
            assert starts[k] == pytest.approx(ends[k + 1], abs=1e-3)


def test_a_segments_index_is_its_position(chains):
    for _f, _l, segments, _c, _i in chains:
        assert [s[8] for s in segments] == list(range(len(segments)))


def test_the_items_are_a_ring_buffer(chains):
    """``(last - first) mod capacity + 1 == count`` on every chain measured, which is what
    identifies the capacity and the two indices. A chain with nothing on it reports -1 for
    both, so the empty case is not that arithmetic and is asserted separately."""
    for _f, _l, _s, chain, items in chains:
        _length, capacity, first, last = chain
        if not items:
            assert (first, last) == (-1, -1)
            continue
        assert 0 <= first < capacity and 0 <= last < capacity
        assert (last - first) % capacity + 1 == len(items)


def test_an_empty_chain_is_empty_rather_than_full_of_capacity(chains):
    """The failure this rules out: reading the ring's capacity as its count, which would put
    ten phantom items on every empty belt in the world."""
    empty = [c for c in chains if not c[4]]
    assert empty, "the fixture keeps one chain with nothing on it"
    for _f, _l, _s, chain, items in empty:
        assert chain[1] > 0, "capacity is still declared"
        assert items == []


def test_items_name_a_class_and_a_descending_position(chains):
    loaded = [c for c in chains if c[4]]
    assert loaded, "the fixture keeps one chain carrying items"
    for _f, _l, _s, _c, items in loaded:
        offsets = [offset for _cls, _state, offset in items]
        assert offsets == sorted(offsets, reverse=True)
        for cls, state, _offset in items:
            assert str(cls).startswith("/Game/")
            assert state == 0, "the item's state is an empty length on every item measured"


# ------------------------------------------------------------- the other three


def test_a_power_line_joins_two_connections(blobs):
    a, b = _one(blobs, POWER_LINE)
    assert "PowerConnection" in str(a)
    assert "PowerConnection" in str(b)
    assert str(a) != str(b), "a line joins two different connections"


def test_the_circuit_subsystem_lists_every_circuit(blobs):
    circuits = _one(blobs, CIRCUIT_SUBSYSTEM)
    assert circuits, "at least one circuit exists once anything is powered"
    for circuit_id, reference in circuits:
        assert circuit_id >= 0
        assert "FGPowerCircuit" in str(reference)


def test_the_player_state_carries_a_length_checked_account_id(blobs):
    unknown, id_type, ident = _one(blobs, PLAYER_STATE)
    assert (unknown, id_type) == (241, 6)
    assert len(ident) == 8, "a 64-bit account id"
    assert ident == bytes(8), "zeroed in the fixture on purpose -- see the module docstring"


# ------------------------------------------------------------- decoding on demand


def test_a_trailer_is_decoded_once_and_only_when_asked(blobs):
    """Decoding every chain costs 22% of a whole save's parse, and a header-only scan wants
    none of it, so it happens on first access. Two things have to hold for that to be safe: it
    must not happen during the parse, and it must not happen twice."""
    cls, blob = next((c, b) for c, b in blobs if c == POWER_LINE)
    calls = []

    def decode():
        calls.append(1)
        return read_trailer(cls, blob, 0, len(blob))

    obj = ParsedObject(version=60, decode_trailer=decode)
    assert calls == [], "constructing the object must not decode anything"
    assert obj.actor_specific_info is None, "the cache is empty until asked"
    first = obj.actorSpecificInfo
    assert len(calls) == 1
    assert obj.actorSpecificInfo is first, "the second access returns the cache"
    assert len(calls) == 1


def test_an_object_with_no_reader_stays_None(blobs):
    """The distinction the projection depends on: a class nobody decoded reads as None, which
    is not what a decoded-but-empty blob looks like."""
    obj = ParsedObject(version=60)
    assert obj.actorSpecificInfo is None
    assert obj.decode_trailer is None


# ------------------------------------------------------------- the refusals


def test_a_short_read_is_refused_rather_than_returned(blobs):
    """The whole verification method, as a test: the reader must land exactly on the declared
    end. Extra bytes mean some field above was read too narrow."""
    cls, blob = next((c, b) for c, b in blobs if c == POWER_LINE)
    with pytest.raises(ParseError, match="left 8 of its .* trailing bytes unread"):
        read_trailer(cls, blob + bytes(8), 0, len(blob) + 8)


def test_a_truncated_record_is_refused(blobs):
    cls, blob = next((c, b) for c, b in blobs if c == CONVEYOR_CHAIN)
    with pytest.raises(ParseError, match="runs past end|do not fit"):
        read_trailer(cls, blob[:-40], 0, len(blob) - 40)


def test_an_absurd_count_is_refused_without_looping_over_it(blobs):
    """A torn file makes a count arbitrary, and two billion segments must be an error rather
    than a hang. The bound is the smallest a record of that kind can be."""
    cls, blob = next((c, b) for c, b in blobs if c == CONVEYOR_CHAIN)
    broken = bytearray(blob)
    at = blob.index(b"\x01\x00\x00\x00", 180)  # the segment count on this record
    broken[at : at + 4] = struct.pack("<i", 2_000_000_000)
    with pytest.raises(ParseError, match="do not fit in the"):
        read_trailer(cls, bytes(broken), 0, len(broken))


def test_a_player_id_that_lies_about_its_length_is_refused(blobs):
    """The one blob read without understanding its contents, so the length is the only guard
    on it and has to be checked against what is actually left."""
    cls, blob = next((c, b) for c, b in blobs if c == PLAYER_STATE)
    broken = bytearray(blob)
    broken[6:10] = struct.pack("<i", 64)
    with pytest.raises(ParseError, match="player id declares 64 bytes"):
        read_trailer(cls, bytes(broken), 0, len(broken))
