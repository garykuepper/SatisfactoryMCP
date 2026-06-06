"""The uncompressed header at the front of a .sav file.

Everything here is read straight off the front of the file with no decompression, which is
why ``read_info`` is cheap enough to call on every save in a directory just to group them
by world. The projection's whole ``header`` block comes from this.

Field order was derived by walking the bytes of a real save and checking each value
against what the game shows -- ``saveVersion`` 60, ``buildVersion`` 495413, a
``playDurationInSeconds`` matching the in-game clock. It is a linear sequence with no
lengths or offsets to seek by, so a field added by a future patch shifts everything after
it; ``save_header_type`` is the version that would announce such a change.

The two int32s after ``save_identifier`` are not named because their meaning is not
established -- both read 1 on every save checked. They are skipped rather than guessed at,
and the fields on either side of them verify, which is what makes skipping safe.

## Four saveHeaderTypes, two field lists

**The old header is a strict prefix of the modern one**, plus one omission. Types 8 and 9
have an *identical* field list; type 10 adds ``save_identifier`` and nothing else, at both
saveVersion 30 and 36. Every field they do carry is the same width in the same order as type
14's, so this is one walk with two gated fields rather than four layouts:

* ``save_name`` -- type 14 only;
* ``save_identifier`` -- type 10 and up;
* the two unnamed int32s, ``save_data_hash`` and ``is_creative`` -- type 14 only.

All 35 old files land exactly on their tag offset, which is 146 on all 12 type-8 saves, 159
on all 9 type-9 saves and 186 on all 14 type-10 saves. The 146 -> 159 step is *entirely*
string growth and not a new field: ``map_options`` gains ``?skiponboarding`` and swaps
``SV_FriendsOnly`` for ``SV_Private`` (+12 net) while ``session_name`` grows by 1.

**Nothing in the header distinguishes saveHeaderType 8 from 9.** Whatever version 9 bumped
is not a field here. That is a limit of what these bytes can say, not a conclusion.

## ``session_visibility``: spelled out on the old saves, open on the new ones

**On the 35 pre-1.0 saves this byte is not merely positioned, it is explained.** ``map_options``
in the same header writes the visibility as text -- ``?Visibility=SV_Private`` or
``?Visibility=SV_FriendsOnly`` -- and the byte agrees with it on **35 of 35** with no exception:
``(0, 'SV_Private')`` on 23 and ``(1, 'SV_FriendsOnly')`` on 12. That makes it a rarity in this
header pinned by *meaning* as well as position, since every other one rests on the walk landing
on the tag; an off-by-one anywhere before it would break the correlation.

**On the 31 modern saves the same byte is a different animal, and the cross-check is gone.** No
modern ``map_options`` contains ``SV_`` at all -- it is ``?skiponboarding?ClientIdentity=...`` --
so nothing in the header states the answer. Measured: ``0x00`` on all 6 saveVersion-60 saves and
``0x18``/``0x58``/``0x98``/``0xD8`` on the 25 at saveVersion 52, so ``value & 0x3F`` is 24 on all
25 and 0 on all 6. Only bits 6 and 7 vary, and all four of their combinations occur.

**The four modern values cannot be reconciled with the old meaning.** 24 is not in the old value
space, which is 0 and 1, and the low six bits are *pinned* at 24 on all 25 while the two top bits
take all four combinations -- so whatever the byte now holds, its low bits are not a 0/1 enum.
Corroboration: all 31 modern saves are one session (same ``save_identifier``, same
``session_name``), yet the byte reads ``0x98`` at 2025-10-31 13:38 and ``0xD8`` 69 seconds later,
and a visibility setting that changed twice within a minute would be a strange way to play.

The one alignment available is too weak to be a bridge: bit 0 is 0 on all 31, and 0 meant
``SV_Private``. A byte whose bit 0 is never set cannot tell "still the enum, and always private"
from "no longer that field", so this is a coincidence that fits, not evidence. The modern meaning
is **unknown**, and it is not derivable from the rest of the header either: ``value >> 6`` matches
no 2-bit slice of ``save_datetime_ticks``, ``play_duration_s``, ``build_version``, the file size,
``body_offset`` or either half of ``save_data_hash`` -- 448 slices tested, zero hits.

Position is still sound on the modern layout: ``editor_object_version`` reads 40 in the four bytes
immediately after this one on all 54 saves from saveVersion 28 up (38 on the twelve saveVersion-25
ones), and the walk lands on the tag. It is the byte's *meaning* after 1.0 that is open, not its
offset.

Absent fields read as ``None`` rather than as ``0``/``False``. A zero says "not creative";
``None`` says "this header has no such field", and the two must not be confusable.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from .errors import ParseError
from .reader import Reader
from .versions import (
    HEADER_TYPE_SAVE_IDENTIFIER,
    HEADER_TYPE_SAVE_NAME,
    KNOWN_HEADER_TYPES,
)

__all__ = ["SaveInfo", "body_hash", "check_body_hash", "read_info", "read_info_bytes"]

#: Marks the start of the compressed body. Unreal's PACKAGE_FILE_TAG, and the check that
#: the header was walked to exactly the right place: land anywhere else and this is not it.
PACKAGE_FILE_TAG = 0x9E2A83C1


def body_hash(data: bytes, body_offset: int) -> tuple[int, int]:
    """The digest the header's ``save_data_hash`` holds, computed from the bytes.

    It is **the md5 of every byte from ``body_offset`` to the end of the file** -- the compressed
    body: all chunk preambles and all zlib blobs, and nothing of the header. Returned as the
    little-endian u64 pair the header stores, so it compares directly against
    ``SaveInfo.save_data_hash``.

    Measured, not inferred: equal on 31 of 31 saves that carry the field, and the boundary is
    tight rather than approximate -- md5 over ``body_offset + 1``, ``body_offset - 1`` and
    ``[body_offset:-1]`` each match 0 of 31, as do sha1, sha256, blake2b-128 and blake2s-128 over
    the same region, and the same five digests over the inflated body. Editing a header field
    leaves the value unchanged; flipping one bit 5,000 bytes into the compressed body breaks it.
    Absent from every pre-1.0 header -- the walk lands on the tag with no room for it.

    **Why this is here rather than in a note.** Reading a save never needs it: the projection is
    derived from the body, so a wrong hash cannot make the body parse differently, and the cache
    keys on the file's own mtime and size, which discriminate perfectly on every save measured.
    WRITING a save does need it. Anything that modifies a body and writes it back has to
    recompute this or the file carries a digest of bytes it no longer contains -- and nothing in
    the save says what the field covers, so the fact is only cheap to have while it is fresh.
    """
    digest = hashlib.md5(data[body_offset:]).digest()
    lo = int.from_bytes(digest[:8], "little")
    hi = int.from_bytes(digest[8:], "little")
    return (lo, hi)


def check_body_hash(data: bytes, info: SaveInfo) -> bool | None:
    """Does the header's stored digest match the body actually present?

    ``None`` rather than ``False`` when the header predates the field, because "this save has no
    hash to check" and "this save's hash is wrong" are different answers and a caller acting on
    the second should never be handed the first.

    The absence test is ``is None``, which is what the header reader sets on a pre-1.0 layout --
    not ``== (0, 0)``. Testing for the zero pair instead reported all 35 old saves as *mismatched*
    rather than as having no field, which is the exact confusion the return type exists to
    prevent, and it is also why a genuinely all-zero digest on a modern save would still be
    checked rather than waved through.
    """
    if info.save_data_hash is None:
        return None
    return body_hash(data, info.body_offset) == info.save_data_hash


@dataclass
class SaveInfo:
    save_header_type: int
    save_version: int
    build_version: int
    #: ``""`` on saveHeaderType 8, 9 and 10, which do not write it. Not the file name: the
    #: field is absent, and inventing it from the path would make a header report something
    #: the header does not contain.
    save_name: str
    map_name: str
    map_options: str
    session_name: str
    play_duration_s: int
    save_datetime_ticks: int
    #: One byte. On the 35 pre-1.0 saves it is the session's visibility and ``map_options``
    #: proves it -- 0 with ``SV_Private``, 1 with ``SV_FriendsOnly``, 35 of 35. On the 31 modern
    #: ones it takes five values (``0x00`` at saveVersion 60, ``0x18``/``0x58``/``0x98``/``0xD8``
    #: at 52), whose low six bits are 24 or 0 rather than the old 0 or 1, so it is not that enum
    #: any more and what it *is* is unknown. See the module docstring; do not read a visibility
    #: off this on a 1.0 save.
    session_visibility: int
    editor_object_version: int
    mod_metadata: str
    is_modded: bool
    #: ``""`` on saveHeaderType 8 and 9. Identical on all 14 saveHeaderType 10 saves and all
    #: 31 modern ones, so where it exists it identifies the world across four years of play.
    save_identifier: str
    #: ``None`` on every old header -- the field is absent, and the walk lands on the tag with
    #: no budget for it, so it cannot be there.
    save_data_hash: tuple[int, int] | None
    #: ``None`` on every old header, for the same reason. Creative mode postdates these saves,
    #: but that is a fact about the game and not something these bytes say.
    is_creative: bool | None
    #: Where the compressed body starts, so the caller need not re-walk the header.
    body_offset: int

    # The projection's `header` block reads these nine names off whatever the sidecar's
    # parser returned, so they are aliases rather than renamed fields: the module stays
    # readable in snake_case and `extract_save.header_info` stays identical under either
    # parser, which is what makes a byte-for-byte diff of the two possible at all.
    @property
    def saveHeaderType(self) -> int:
        return self.save_header_type

    @property
    def saveVersion(self) -> int:
        return self.save_version

    @property
    def buildVersion(self) -> int:
        return self.build_version

    @property
    def sessionName(self) -> str:
        return self.session_name

    @property
    def playDurationInSeconds(self) -> int:
        return self.play_duration_s

    @property
    def saveDateTimeInTicks(self) -> int:
        return self.save_datetime_ticks

    @property
    def saveIdentifier(self) -> str:
        return self.save_identifier

    @property
    def isModdedSave(self) -> bool:
        return self.is_modded

    @property
    def isCreativeModeEnabled(self) -> bool | None:
        return self.is_creative


def read_info_bytes(data: bytes) -> SaveInfo:
    try:
        return _walk_header(data)
    except ParseError as exc:
        raise ParseError(f"{_header_context(data)}: {exc}") from exc


def _header_context(data: bytes) -> str:
    """The two version fields, for a failure message. Guarded: the file may be shorter."""
    if len(data) < 8:
        return f"{len(data)}-byte file, too short to hold a save header"
    r = Reader(data)
    kind, version = r.i32(), r.i32()
    known = ""
    if kind not in KNOWN_HEADER_TYPES:
        known = f" (known: {', '.join(map(str, KNOWN_HEADER_TYPES))})"
    return f"saveHeaderType {kind}{known}, saveVersion {version}"


def _walk_header(data: bytes) -> SaveInfo:
    """One linear walk over all four field lists, gated on ``save_header_type``.

    A type this module has never seen is walked as the nearest layout below it and then judged
    by the tag, which is not the permissive fallback it looks like: **the tag is a referee an
    unknown version cannot pass by accident.** Landing on ``PACKAGE_FILE_TAG`` at exactly the
    offset the walk predicts means the field list was right; landing anywhere else refuses the
    file and names the type. Whitelisting instead would refuse a save on the day the game
    bumps the type without moving a field, and would gain nothing a 4-byte referee at a
    predicted offset does not already give.
    """
    r = Reader(data)
    kind = r.i32()
    modern = kind >= HEADER_TYPE_SAVE_NAME
    info = SaveInfo(
        save_header_type=kind,
        save_version=r.i32(),
        build_version=r.i32(),
        save_name=r.string() if modern else "",
        map_name=r.string(),
        map_options=r.string(),
        session_name=r.string(),
        play_duration_s=r.i32(),
        save_datetime_ticks=r.i64(),
        session_visibility=r.i8(),
        editor_object_version=r.i32(),
        # These eight bytes are `0000000000000000` on all 35 old saves, so no grouping of them
        # can be told from another on this disk: (str, i32) as read here, two int32s, one
        # int64, or two other fields entirely. The reading is the modern field order, which is
        # what keeps one walk for all four types and sums to the right width. None of the 35
        # is modded and none has mod metadata, so nothing here can settle it -- recorded as a
        # docstring risk, since no caller can be misled by a zero.
        mod_metadata=r.string(),
        is_modded=bool(r.i32()),
        save_identifier=r.string() if kind >= HEADER_TYPE_SAVE_IDENTIFIER else "",
        save_data_hash=None,
        is_creative=None,
        body_offset=0,
    )
    if modern:
        r.i32()  # unnamed, 1 on every save seen
        r.i32()  # unnamed, 1 on every save seen
        info.save_data_hash = (r.u64(), r.u64())
        info.is_creative = bool(r.i32())
    info.body_offset = r.pos

    # The tag is the proof. Every field above is positional, so a wrong width anywhere
    # lands here at the wrong byte -- failing loudly beats returning a header that parsed
    # into plausible nonsense.
    #
    # It used to be skipped when fewer than four bytes were left, which quietly threw the
    # proof away in the one case it is most needed. Truncating a real save to exactly its
    # 453-byte header returned a fully populated SaveInfo, and the refusal then came from
    # two layers down as "read of 8 at 0 runs past end (0)" -- an offset into a body that
    # does not exist, which reads like a corrupt save rather than a file the game has only
    # started writing. Too short to hold the tag is a refusal in its own right.
    if r.remaining < 4:
        raise ParseError(
            f"the file ends at offset {r.pos}, where the compressed body should start: "
            f"{r.remaining} byte(s) left, too few for the {PACKAGE_FILE_TAG:#x} tag. The "
            "header is complete, so this is a partly-written file rather than a bad layout"
        )
    tag = Reader(data, r.pos).u32()
    if tag != PACKAGE_FILE_TAG:
        # The version fields are NOT repeated here: read_info_bytes puts them in front
        # of every failure from this module, so naming them twice was noise.
        raise ParseError(
            f"header did not end at the compressed body: expected tag "
            f"{PACKAGE_FILE_TAG:#x} at offset {r.pos}, found {tag:#x}. The layout "
            f"likely changed"
        )
    return info


def read_info(path: str | os.PathLike[str]) -> SaveInfo:
    """Header only. Reads a bounded prefix, not the whole file.

    64 KiB is far more than any header seen (about 700 bytes with a long mapOptions) and
    far less than a save, which keeps "list the saves in this folder" cheap.
    """
    with open(path, "rb") as fh:
        return read_info_bytes(fh.read(65_536))
