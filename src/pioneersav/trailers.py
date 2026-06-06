"""The class-specific bytes trailing an actor's property list, for the classes that have them.

Eight classes in a save write data after their properties that the property serialiser knows
nothing about. The biggest, ``FGLightweightBuildableSubsystem``, has its own module because it
holds every foundation in the world and the projection is built from it. This one covers the
other seven, all of which the projection reads nothing from:

* **conveyor chains** and their three ``RepSize`` variants -- the belts a chain spans, their
  spline geometry, and every item riding on them. 76.6 MB plus 30.4 MB across the 31 readable
  saves, easily the largest thing in a save after the lightweight blob.
* **power lines** -- the two connections each line joins. 8.0 MB.
* the **circuit** and **player-state** subsystems, 4.5 KB and 558 bytes across all 31 saves.

**Decoding is lazy, and that is a measured decision.** Reading every chain on the reference
save costs 0.46 s on top of a 2.10 s parse -- 22% -- for data no projection field uses. So
``ParsedObject.actorSpecificInfo`` decodes on first access and caches; a save read for the
projection never pays it, and a caller that wants belt contents gets them without a second
pass. A malformed trailer therefore raises inside the caller rather than at the save boundary,
which is safe because the sidecar's ``except ParseError`` wraps projection building too.

**Verification.** Every reader here is checked by *exact consumption*: a record read correctly
ends precisely where the object declared its trailing bytes end, and any wrong field width
leaves a remainder. All **87,973** records of these seven classes across all 31 readable saves
consume exactly, at both save versions -- 36,773 power lines, 51,200 chains, and one each of
the two subsystems per save. Field *meanings* are separately checked by relations that must
hold over every record; those that do are stated below, and the fields that stay unexplained
are named ``unknown`` rather than given a plausible name.
"""

from __future__ import annotations

from .errors import ParseError
from .properties import ObjectReference
from .reader import Reader

__all__ = ["TRAILER_READERS", "read_trailer"]

CONVEYOR_CHAIN = "/Script/FactoryGame.FGConveyorChainActor"
POWER_LINE = "/Game/FactoryGame/Buildable/Factory/PowerLine/Build_PowerLine.Build_PowerLine_C"
CIRCUIT_SUBSYSTEM = "/Game/FactoryGame/-Shared/Blueprint/BP_CircuitSubsystem.BP_CircuitSubsystem_C"
PLAYER_STATE = "/Game/FactoryGame/Character/Player/BP_PlayerState.BP_PlayerState_C"


def _reference(r: Reader) -> ObjectReference:
    return ObjectReference(r.string(), r.string())


def _chain(r: Reader, end: int) -> list:
    """A conveyor chain: the belts it spans, their splines, and the items on it.

    Structure, all of it derived from the bytes and every relation below true of all 51,200
    chains across the 31 saves::

        reference   the first belt in the chain
        reference   the last belt
        int32       segmentCount
        per segment:
            reference       the chain actor this segment belongs to
            reference       the belt
            int32           pointCount
            per point:      3 x (3 x double) -- location, then two tangents
            float32         the part of this segment's offset range with no spline behind
                            it -- 0 on 80,817 of 83,389 segments, and ~200/300/400 cm at a
                            conveyor lift junction, always at the low-offset end, where no
                            item ever sits
            float32         where this segment starts, centimetres along the chain
            float32         where it ends
            int32           ring index of the first item on this segment, or -1
            int32           ring index of the last, or -1
            int32           the segment's own index -- always equal to its position
        float32     the chain's length in centimetres -- always the first segment's end
        int32       the item ring's capacity, and it is DERIVED rather than independent:
                    floor(length / 120) + 2 * segmentCount + 1, on 51,200 of 51,200
        int32       ring index of the chain's first item, or -1 when the chain is empty
        int32       ring index of its last, or -1
        int32       itemCount
        per item:
            reference       the item class
            int32           the item's state, a length that is 0 on all 25,812 items
            float32         how far along the chain it is, centimetres

    **The items are a ring buffer**, which is what makes the two index fields readable:
    ``(last - first) mod capacity + 1`` equals the item count on **all 43,823 non-empty chains**.
    Note the qualifier -- ``-1`` is the sentinel, carried by 10,548 segments and by all 7,377
    empty chains, where that identity would return a nonsensical 1. Every index is ``-1`` or in
    ``[0, capacity)``.

    A chain's own pair matches the first and last segment **that actually holds items**, on
    43,823 of 43,823. Comparing against ``segments[0]`` and ``segments[-1]`` instead fails on 52
    and 68 chains, which is where an earlier note's "1,905 and 1,904 of 1,909" came from: it was
    measuring the wrong pair rather than observing a real exception.

    **Offsets increase along the direction of travel.** The last segment starts at 0 at the
    chain's INPUT; the first segment's end is the OUTPUT and equals the chain length on every
    chain. So ``segments[0]`` and ``first_belt`` are the DOWNSTREAM end -- which reads backwards
    from their names, and is why this paragraph exists.

    Settled by geometry rather than by inference. The spline points are in the chain actor's
    frame with ``p0`` at the segment's ``start`` end, so offset 0 sits at
    ``segments[-1].p0 + chainPos``; across three saves that point lies 806 cm from a
    ``Build_MinerMk1``/``Mk2`` -- a building with only an output connection -- on 27-29 chains,
    against 1/0/0 at the offset-``length`` end. The reverse holds too: 3/1/1 chains have their
    offset-``length`` end within 1,200 cm of a Space Elevator, AWESOME Sink or Trading Post,
    buildings with only inputs, against 0 at offset 0.

    **An offset is not confined to ``[0, length]`` and a consumer must clamp both ends.** Exactly
    one item per chain may sit ABOVE the length -- the one at ring index ``first``, 40,921 of
    40,921 across the folder, against 0 of the other 647,361, and never two in one chain. Its
    overshoot scales with belt speed: median 0.734 / 1.444 / 3.294 / 5.874 cm on Mk1 to Mk4,
    ratios of 1 : 1.967 : 4.487 : 8.000 against a published 1 : 2 : 4.5 : 8. And 844 items sit
    BELOW 0, down to -1024.46 cm, on 36 distinct chains present in every save: the pack is
    contiguous, so once the item count exceeds what 120 cm spacing allows the surplus hangs off
    the low end.
    """
    first_belt, last_belt = _reference(r), _reference(r)
    segments = []
    for _ in range(_count(r, end, 24, "chain segments")):
        owner, belt = _reference(r), _reference(r)
        points = [
            [
                [r.f64(), r.f64(), r.f64()],
                [r.f64(), r.f64(), r.f64()],
                [r.f64(), r.f64(), r.f64()],
            ]
            for _ in range(_count(r, end, 72, "spline points"))
        ]
        segments.append([owner, belt, points, r.f32(), r.f32(), r.f32(), r.i32(), r.i32(), r.i32()])
    chain = [r.f32(), r.i32(), r.i32(), r.i32()]
    items = [[_reference(r), r.i32(), r.f32()] for _ in range(_count(r, end, 12, "chain items"))]
    return [first_belt, last_belt, segments, chain, items]


def _power_line(r: Reader, end: int) -> list:
    """The two power connections a line joins.

    Nothing reads this: the projection's power graph comes from the connection components'
    own properties and agrees with the vendored parser on every save. Decoded because it is
    two references and because leaving one of eight classes out would keep the whole
    "an actor's trailer is not length-checked" hole open.
    """
    return [_reference(r), _reference(r)]


def _circuit_subsystem(r: Reader, end: int) -> list:
    """Every power circuit in the world, as ``(id, reference)`` pairs.

    One per save, 113 bytes on the reference save for a single circuit. The id is the same
    number the circuit's own ``mCircuitID`` property carries.
    """
    return [[r.i32(), _reference(r)] for _ in range(_count(r, end, 12, "power circuits"))]


def _player_state(r: Reader, end: int) -> list:
    """The player's account id.

    ``uint8`` of unknown meaning, then a one-byte id type, then a length-prefixed blob: 8
    bytes holding a 64-bit account id, which on this disk equals the name of the folder the
    save sits in. The length is checked against what is actually left, which is what makes
    reading a blob of unknown meaning safe.
    """
    unknown, id_type = r.i8(), r.i8()
    size = r.i32()
    if size < 0 or r.pos + size != end:
        raise ParseError(
            f"at body offset {r.pos - 4}: player id declares {size} bytes with {end - r.pos} left"
        )
    return [unknown, id_type, r.bytes(size)]


def _count(r: Reader, end: int, stride: int, what: str) -> int:
    """An int32 count, refused if the records it promises cannot fit.

    A count is the one field a torn file turns into an arbitrary number, and a loop over two
    billion iterations is a hang rather than an error. ``stride`` is the smallest a record of
    this kind can be, so the bound is generous and still catches nonsense.
    """
    at = r.pos
    n = r.i32()
    if n < 0 or r.pos + n * stride > end:
        raise ParseError(
            f"at body offset {at}: {n} {what} do not fit in the {end - r.pos} bytes left"
        )
    return n


#: Which classes this module can read, by the ``typePath`` their actor header carries. The
#: three ``RepSize`` variants are the same actor with a bigger replication budget and the
#: identical record -- verified, not assumed: all 540 of them across the 31 saves consume
#: exactly under this reader.
TRAILER_READERS = {
    CONVEYOR_CHAIN: _chain,
    f"{CONVEYOR_CHAIN}_RepSizeMedium": _chain,
    f"{CONVEYOR_CHAIN}_RepSizeLarge": _chain,
    f"{CONVEYOR_CHAIN}_RepSizeHuge": _chain,
    POWER_LINE: _power_line,
    CIRCUIT_SUBSYSTEM: _circuit_subsystem,
    PLAYER_STATE: _player_state,
}


def read_trailer(class_path: str, body: bytes, offset: int, length: int) -> list:
    """Decode one actor's trailing bytes. ``offset``/``length`` span the 4-byte trailer too.

    Refuses to return a short read: the reader must land exactly on the end the object
    declared. That is the only check available on a record with no separators and no
    self-describing length, and it is a strong one -- every field width has to be right for
    the walk to arrive there.
    """
    reader = TRAILER_READERS[class_path]
    end = offset + length
    r = Reader(body, offset + 4)
    out = reader(r, end)
    if r.pos != end:
        raise ParseError(
            f"at body offset {r.pos}: {class_path.rsplit('.', 1)[-1]} left "
            f"{end - r.pos} of its {length} trailing bytes unread"
        )
    return out
