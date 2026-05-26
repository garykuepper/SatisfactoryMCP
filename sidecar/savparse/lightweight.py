"""The buildables that exist without an actor: FGLightweightBuildableSubsystem's blob.

Foundations, walls, ramps, catwalks and pillars are not saved as actors. One subsystem
actor carries every one of them in the class-specific bytes trailing its (empty) property
list -- 3.10 MB and 8,347 pieces on the reference save, 224,530 across the 31 readable ones.
Nothing else in the save mentions them, which is why a header-only census reported 86 built
classes where 103 are built, and why ``structures`` and ``lightweight_counts`` were the last
two projection fields this parser could not fill.

Layout, derived from the bytes::

    int32   0                    the object's own trailer, read by the caller
    int32   2 or 4               version of what follows
    int32   classCount
    per class:
        reference   buildable class: an empty level name, then the class path
        int32       instanceCount
        per instance, 162 fixed bytes (157 at version 2) plus two reference paths:
            4 x double   rotation quaternion
            3 x double   position, world centimetres
            3 x double   scale
            reference    swatch: the paint slot, the only populated one on any save here
            reference    x3     empty on all 224,530 instances
            2 x 4 float  override colours, primary and secondary
            reference           empty everywhere
            uint8               0 everywhere
            reference    recipe: what the piece was built from
            reference           empty everywhere
            int32               0 everywhere
            uint8               version 4 only, and the two are ONE field: an
            int32               FPlayerInfoHandle naming who placed the piece. `06 00 00 00 00`
                                is a set handle and `00 ff ff ff ff` the unset one -- so (6, 0)
                                on a piece this player placed, and (0, -1) on everything
                                migrated from a version-2 save. 2,976 set against 47,131 unset
                                over the 50,107 version-4 records; on the reference save 496
                                are set, across TWO classes -- 33 Build_Foundation_8x1_01 and
                                all 463 Build_Foundation_ConcretePolished_8x1

**Why the record is trustworthy despite that many always-zero fields.** The instance count
and both class paths are length-prefixed, so a record read one byte short desynchronises the
walk and the next class path fails its own length check immediately -- 18 classes deep on the
reference save, 488 across the folder, and the blob has to be consumed to its last byte. What
that check cannot see is how the always-zero runs are *grouped*: the 24 bytes after the
swatch are three empty references here, and three int32 zeros would read identically. Read
as references they cost nothing if a future save populates one, which is the reason to prefer
that reading, not evidence for it.

Verified black-box: the class census and every instance position match the vendored parser's
output on all 31 readable saves. The transform's scale is decoded here and absent from that
parser's output; it is 1,1,1 on every instance seen, and kept because the format has it.
"""

from __future__ import annotations

from .errors import ParseError
from .properties import ObjectReference
from .reader import Reader

__all__ = ["LIGHTWEIGHT_SUBSYSTEM", "VERSION", "read_lightweight"]

#: The one actor whose trailing bytes this module reads.
LIGHTWEIGHT_SUBSYSTEM = "/Script/FactoryGame.FGLightweightBuildableSubsystem"

#: Fixed bytes per instance record, by blob version -- the transform, the always-empty
#: references and the colours, everything but the two length-prefixed reference paths.
#:
#: Version 2 goes with saveVersion 52 and version 4 with saveVersion 60, on all 31 readable
#: saves here: 25 and 6 respectively. The two differ by exactly the trailing ``(uint8,
#: int32)`` pair, which version 2 does not have -- 370 bytes per foundation against 375. A
#: version this does not know is refused rather than read as the nearest one, because the
#: walk would then desynchronise and report a class path that is not one, several thousand
#: records from the field that actually moved.
RECORD_BYTES = {2: 157, 4: 162}

#: The version the current game writes. Kept as a name because the tests assert on it.
VERSION = 4

#: Longest class path seen is 112 bytes; the cap is a sanity bound on a length that a
#: desynchronised walk would otherwise turn into a multi-megabyte read.
MAX_PATH = 512


def _reference(r: Reader) -> ObjectReference:
    return ObjectReference(r.string(), r.string())


def _instance(r: Reader, version: int) -> list:
    """One buildable. Field order is the file's; index 0 is the rotation, 1 the position.

    The projection reads ``inst[1]`` for the position and walks the rest generically, so the
    order here is load-bearing.
    """
    out = [
        [r.f64(), r.f64(), r.f64(), r.f64()],
        [r.f64(), r.f64(), r.f64()],
        [r.f64(), r.f64(), r.f64()],
        _reference(r),
        _reference(r),
        _reference(r),
        _reference(r),
        [[r.f32(), r.f32(), r.f32(), r.f32()], [r.f32(), r.f32(), r.f32(), r.f32()]],
        _reference(r),
        r.i8(),
        _reference(r),
        _reference(r),
        r.i32(),
    ]
    if version >= 4:
        out += [r.i8(), r.i32()]
    return out


def read_lightweight(body: bytes, offset: int, length: int) -> list:
    """Decode the subsystem's trailing bytes into ``[version, [classPath, [instance, ...]], ...]``.

    ``offset``/``length`` are the object's ``extra`` span including its 4-byte trailer, so the
    version is read at ``offset + 4``. Returning the version as element 0 keeps the shape the
    projection already consumes.
    """
    r = Reader(body, offset + 4)
    end = offset + length

    version = r.i32()
    if version not in RECORD_BYTES:
        raise ParseError(
            f"at body offset {r.pos - 4}: lightweight buildables are version {version}, "
            f"this reads {sorted(RECORD_BYTES)} -- the instance record has probably changed"
        )
    fixed = RECORD_BYTES[version]
    class_count = r.i32()
    if not 0 <= class_count <= 100_000:
        raise ParseError(
            f"at body offset {r.pos - 4}: {class_count} buildable classes is implausible"
        )

    out: list = [version]
    for k in range(class_count):
        at = r.pos
        # The class arrives as a reference, not a bare string: an empty level name and then
        # the path. Reading it as a string instead lands on that empty name and reports a
        # zero-length class path, which is how this was found.
        #
        # Both lengths are checked BEFORE being consumed. A walk that has lost its place is
        # reading a quaternion here, whose second int32 is 0x80000000 -- a negative length,
        # which is a four-gigabyte UTF-16 read. Letting that fail on the bounds check would
        # report the buffer size instead of the class the walk was looking for.
        if r.remaining < 8:
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found only {r.remaining} bytes left"
            )
        level_len, path_len = Reader(body, at).i32(), Reader(body, at + 4).i32()
        if level_len != 0 or not 0 < path_len < MAX_PATH:
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found string lengths {level_len} and {path_len} -- the walk is out of "
                "step. Every class here has an empty level name and a path under 512 bytes"
            )
        path = _reference(r).path_name
        if not path.startswith("/"):
            raise ParseError(
                f"at body offset {at}: expected buildable class {k + 1} of {class_count}, "
                f"found {path[:40]!r} -- the walk is out of step"
            )
        instance_count = r.i32()
        if instance_count < 0 or r.pos + instance_count * fixed > end:
            raise ParseError(
                f"at body offset {r.pos - 4}: {path} claims {instance_count} instances, "
                f"which does not fit in the {end - r.pos} bytes left"
            )
        out.append([path, [_instance(r, version) for _ in range(instance_count)]])

    if r.pos != end:
        raise ParseError(
            f"at body offset {r.pos}: {class_count} buildable classes ended "
            f"{end - r.pos} bytes short of the object's {length}"
        )
    return out
