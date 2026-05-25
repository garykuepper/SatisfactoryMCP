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
            float32         unknown; 0 on 1,844 of the reference save's 1,909 chains
            float32         where this segment starts, centimetres along the chain
            float32         where it ends
            int32           index of the first item on this segment, in the ring below
            int32           index of the last
            int32           the segment's own index -- always equal to its position
        float32     the chain's length in centimetres -- always the first segment's end
        int32       the item ring's capacity -- always >= the item count
        int32       index of the chain's first item
        int32       index of its last
        int32       itemCount
        per item:
            reference       the item class
            int32           the item's state, a length that is 0 on all 25,812 items
            float32         how far along the chain it is, centimetres

    **The items are a ring buffer**, which is what makes the two index fields readable:
    ``(last - first) mod capacity + 1`` equals the item count on every chain measured, without
    exception, and no index is ever >= the capacity. Segments partition that ring in order, and
    a chain's own pair matches its first and last segment's on 1,905 and 1,904 of 1,909 -- the
    handful that differ are presumably mid-transfer, and nothing here depends on them agreeing.

    Segment offsets run *backwards*: the last segment starts at 0 and the first one's end is the
    whole chain's length, on every chain. Item offsets descend in step, 120 cm apart on a Mk1
    belt. They are not bounded by the chain length on 1,089 of 1,909 chains, so "distance from
    the output end" is the shape of it but not a claim this makes.
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
