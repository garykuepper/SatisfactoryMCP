"""Where the format changed, as named thresholds rather than magic numbers.

Six ``saveVersion``s exist on the author's disk and they are not six formats. They are one
format with fields added, so every layer below reads **one** walk with version-gated fields
-- the way ``lightweight.RECORD_BYTES`` handles blob versions 2 and 4. A second parallel
parser for the old files would drift out of step with this one the first time either was
touched.

What is on the disk, and what each layer has to do differently:

| saveHeaderType | saveVersion | saves | header ends at | body shape |
|---|---|---|---|---|
| 8 | 25 | 12 | 146 | no level list |
| 9 | 28 | 9 | 159 | no level list |
| 10 | 30 | 6 | 186 | named levels, int32 sizes |
| 10 | 36 | 8 | 186 | named levels, int32 sizes |
| 14 | 52 | 25 | 283 or 297 | grid table, int64 sizes |
| 14 | 60 | 6 | 450, 453 or 457 | + archive version header |

"Header ends at" is the offset of ``header.PACKAGE_FILE_TAG``, and it is the referee for the
header layout: a candidate field list is right when the walk lands exactly there. The four
old types each have **one** value, measured over all 35 files; saveHeaderType 14 does not,
because ``save_name`` is variable-length.

**The thresholds are where a change was observed, not where it happened.** Nothing on this
disk was written at a saveVersion between 36 and 51, so every difference in that gap arrives
at once and this module cannot say which patch introduced which. That is why there is one
constant covering all of them rather than five pretending to be independent.
"""

from __future__ import annotations

__all__ = [
    "FIRST_LEVEL_LIST",
    "FIRST_MODERN_BODY",
    "HEADER_TYPE_SAVE_IDENTIFIER",
    "HEADER_TYPE_SAVE_NAME",
    "KNOWN_HEADER_TYPES",
]

#: The saveVersion at which the body gained a **list of levels**. Below it there is no level
#: list at all: one flat run of object headers, each naming its own level in ``root_object``.
#: Verified on 21 saves at saveVersion 25 and 28 -- the flat walk lands on the last byte of
#: every one of them, and reading a level count instead fails on the first record.
FIRST_LEVEL_LIST = 30

#: The saveVersion at and above which the body is the layout ``objects.py`` was first written
#: for. Everything in this list changes here, and all of it together, because no save exists
#: between 36 and 51 to separate them:
#:
#: * the body's own size and every level's two block sizes widen from **int32 to int64**;
#: * a **world-partition grid table** appears before the level list;
#: * an object **header** gains its ``EObjectFlags`` word;
#: * an object **entry** gains a leading version int32 and a flag int32, so below this the
#:   entry is a bare size and the object's version is not in the bytes at all -- it has to be
#:   taken from the save;
#: * a level **trailer** gains its leading version int32;
#: * the persistent level's destroyed-actor list becomes **grouped by partition cell**;
#: * ``FVector``, ``FQuat`` and ``FBox`` are written as **float32** below this and float64 at
#:   and above it, and ``FInventoryItem`` is two object references below it.
#:
#: Verified in both directions: all 35 old bodies walk to their last byte under the old
#: reading, and all 31 modern ones produce byte-identical projections under the new.
FIRST_MODERN_BODY = 52

#: saveHeaderType at which the header started with the save's own name. 8, 9 and 10 do not
#: carry it, and that is measured rather than inferred from the byte budget: all 12
#: saveHeaderType 8 saves land on offset 146 although their file names run from 18 to 36
#: characters. An 18-character spread with a constant tag offset means the field is not there.
HEADER_TYPE_SAVE_NAME = 14

#: saveHeaderType at which ``save_identifier`` appeared. All 14 saveHeaderType 10 saves carry
#: the same value the 31 modern ones do -- ``'X2faPVKjX06VaRzClNv5KQ'`` -- which is the same
#: world identity across four years and three header versions.
HEADER_TYPE_SAVE_IDENTIFIER = 10

#: Every saveHeaderType whose field list has been derived from real bytes. **Not a gate**: an
#: unknown type is still walked, because ``PACKAGE_FILE_TAG`` is the verdict and a patch may
#: bump the type without moving a field -- which is exactly what happened between 8 and 9, where
#: nothing in the header moved at all. It is here so a refusal can say what IS known.
#:
#: There is no matching list of save versions, deliberately. The body walk has no whitelist
#: either, and for the same reason: its referee is that the walk consumes the body exactly, so a
#: version nobody has seen either lands or fails loudly on a declared length. A constant naming
#: the six versions on this disk would be a list nothing reads, and those go stale.
KNOWN_HEADER_TYPES = (8, 9, 10, 14)
