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
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .reader import Reader

__all__ = ["SaveInfo", "read_info", "read_info_bytes"]

#: Marks the start of the compressed body. Unreal's PACKAGE_FILE_TAG, and the check that
#: the header was walked to exactly the right place: land anywhere else and this is not it.
PACKAGE_FILE_TAG = 0x9E2A83C1


@dataclass
class SaveInfo:
    save_header_type: int
    save_version: int
    build_version: int
    save_name: str
    map_name: str
    map_options: str
    session_name: str
    play_duration_s: int
    save_datetime_ticks: int
    session_visibility: int
    editor_object_version: int
    mod_metadata: str
    is_modded: bool
    save_identifier: str
    save_data_hash: tuple[int, int]
    is_creative: bool
    #: Where the compressed body starts, so the caller need not re-walk the header.
    body_offset: int


def read_info_bytes(data: bytes) -> SaveInfo:
    r = Reader(data)
    info = SaveInfo(
        save_header_type=r.i32(),
        save_version=r.i32(),
        build_version=r.i32(),
        save_name=r.string(),
        map_name=r.string(),
        map_options=r.string(),
        session_name=r.string(),
        play_duration_s=r.i32(),
        save_datetime_ticks=r.i64(),
        session_visibility=r.i8(),
        editor_object_version=r.i32(),
        mod_metadata=r.string(),
        is_modded=bool(r.i32()),
        save_identifier=r.string(),
        save_data_hash=(0, 0),
        is_creative=False,
        body_offset=0,
    )
    r.i32()  # unnamed, 1 on every save seen
    r.i32()  # unnamed, 1 on every save seen
    info.save_data_hash = (r.u64(), r.u64())
    info.is_creative = bool(r.i32())
    info.body_offset = r.pos

    # The tag is the proof. Every field above is positional, so a wrong width anywhere
    # lands here at the wrong byte -- failing loudly beats returning a header that parsed
    # into plausible nonsense.
    if r.remaining >= 4:
        tag = Reader(data, r.pos).u32()
        if tag != PACKAGE_FILE_TAG:
            raise ValueError(
                f"header did not end at the compressed body: expected tag "
                f"{PACKAGE_FILE_TAG:#x} at offset {r.pos}, found {tag:#x}. The layout "
                f"likely changed -- saveHeaderType is {info.save_header_type}, "
                f"saveVersion {info.save_version}"
            )
    return info


def read_info(path: str | os.PathLike[str]) -> SaveInfo:
    """Header only. Reads a bounded prefix, not the whole file.

    64 KiB is far more than any header seen (about 700 bytes with a long mapOptions) and
    far less than a save, which keeps "list the saves in this folder" cheap.
    """
    with open(path, "rb") as fh:
        return read_info_bytes(fh.read(65_536))
