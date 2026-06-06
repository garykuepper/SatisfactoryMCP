"""Decompressing the body, which the game writes as a run of zlib chunks.

Everything after the header is a sequence of independently-compressed blocks, each with a
49-byte preamble. Concatenating the inflated blocks gives one flat byte stream, and that
stream is what the level and object walk reads.

The preamble, derived by finding the ``78 9c`` zlib start and reading backwards from it::

    int64  tag                 0x222222229E2A83C1
    int64  max chunk size      131072 on every save seen
    uint8  compressor          3
    int64  compressed size     \\
    int64  uncompressed size    | written twice, identically
    int64  compressed size      |
    int64  uncompressed size   /

The duplicated pair is not a mistake in the reading -- both copies are present and equal
on every chunk of every save checked. The second is used and the first is verified against
it, because two fields that must agree are a free integrity check on a format with no
other one.

The tag is checked per chunk rather than once. A save torn mid-write by an autosave -- which
this project hits routinely, since autosaves land every few minutes -- fails here on the
chunk where the tear is, with the offset, instead of inflating garbage into the object
walk and failing somewhere unrelated.

## The old preamble is 48 bytes, and the difference is exactly one field

Pre-1.0 saves -- saveHeaderType 8, 9 and 10, saveVersion 25 to 36 -- write the same preamble
without the **compressor byte**, and with the tag's high half zero::

    int64  tag                 0x9E2A83C1, NOT 0x222222229E2A83C1
    int64  max chunk size      131072, as on every modern chunk
    int64  compressed size     \\
    int64  uncompressed size   /  the same duplicated pair, minus the byte between them

Which of the two a file uses is not guessed from the bytes: ``old=`` is passed in from the
header's ``save_version``, and the tag then confirms it -- a 48-byte reading of a 49-byte
chunk puts the compressor byte into the low end of the max-chunk-size field and the *second*
chunk's tag check fails immediately. Measured over all 35 old saves: **5,196 chunks, 678,532,692
inflated bytes, every file ending exactly on a chunk boundary**, every tag with a zero high
half, every ``max chunk size`` 131072, every blob starting ``78 9c``.
"""

from __future__ import annotations

import zlib

from .errors import ParseError
from .reader import Reader

__all__ = [
    "CHUNK_TAG",
    "OLD_CHUNK_TAG",
    "OLD_PREAMBLE_BYTES",
    "PREAMBLE_BYTES",
    "ZLIB",
    "decompress_body",
]

#: PACKAGE_FILE_TAG, as the game writes it into each chunk preamble: Unreal's
#: 0x9E2A83C1 in the low half, 0x22222222 in the high.
CHUNK_TAG = 0x222222229E2A83C1

#: The same field on a pre-1.0 save: the bare tag, with the high half left zero. Read as one
#: int64 rather than as a u32 and a skipped u32, because that is what makes the two preambles
#: one walk differing in a single field.
OLD_CHUNK_TAG = 0x9E2A83C1

#: The only compressor seen. Named rather than assumed so an unexpected one is refused
#: with its value, which is a far better bug report than a zlib error.
ZLIB = 3

#: Preamble length: six int64s, plus the compressor byte on a modern save. The loop needs it
#: only to decide whether a whole preamble is left to read before it stops.
PREAMBLE_BYTES = 49
OLD_PREAMBLE_BYTES = 48


def decompress_body(data: bytes, offset: int, *, old: bool = False) -> bytes:
    """Inflate every chunk from ``offset`` to the end of ``data``.

    ``old`` selects the 48-byte preamble of a pre-1.0 save. It is a parameter rather than a
    sniff because the caller already knows: ``save.py`` has the header's ``save_version`` in
    hand, and the tag check below then confirms the choice on every chunk.
    """
    r = Reader(data, offset)
    tag_wanted = OLD_CHUNK_TAG if old else CHUNK_TAG
    preamble = OLD_PREAMBLE_BYTES if old else PREAMBLE_BYTES
    out: list[bytes] = []
    while r.remaining >= preamble:
        start = r.pos
        tag = r.u64()
        if tag != tag_wanted:
            raise ParseError(
                f"chunk at {start} has tag {tag:#x}, expected {tag_wanted:#x} -- the body "
                "is not a chunk stream here, which usually means the file was still "
                "being written"
            )
        max_plain = r.i64()
        # The compressor byte arrived with the modern preamble. Below saveVersion 52 there is
        # no such field: the sizes follow the maximum directly.
        algo = ZLIB if old else r.i8()
        if algo != ZLIB:
            raise ParseError(
                f"chunk at {start} uses compressor {algo}, only {ZLIB} (zlib) is known"
            )
        first_compressed, first_plain = r.i64(), r.i64()
        compressed, plain = r.i64(), r.i64()
        if (first_compressed, first_plain) != (compressed, plain):
            raise ParseError(
                f"chunk at {start} disagrees with itself: {first_compressed}/{first_plain} "
                f"then {compressed}/{plain}"
            )

        # The tear itself, and the reason this check is here rather than left to the
        # reader. Truncating a real save at 14 different fractions of its length put the
        # failure on THIS read every single time, in both a saveVersion 52 and a 60 save:
        # the preamble of the last chunk survives the cut and its blob does not. Left to
        # `Reader._take` the report was "read of 5131 at 974874 runs past end (978615)",
        # which names neither the chunk nor the shortfall and reads like a bug in the walk
        # rather than a file the game is halfway through writing. A negative size is folded
        # into the same guard because `_take` renders that one as "read of -5 ... runs past
        # end", which is worse than useless.
        if not 0 <= compressed <= r.remaining:
            raise ParseError(
                f"chunk at {start} declares {compressed} compressed bytes but "
                f"{r.remaining} are left in the file, a shortfall of "
                f"{compressed - r.remaining} -- the file is almost certainly still being "
                "written, since the game rewrites a save in place every few minutes"
            )
        # The one preamble field that was read and thrown away, and it showed: overwriting
        # any single byte of a chunk preamble and re-parsing the save, 588 mutations over
        # three chunks, caught every field except this one -- 99 undetected changes, all of
        # them in these eight bytes. A maximum is checkable against the sizes beside it
        # without assuming its value: 131072 on all 9,125 chunks of all 31 readable saves,
        # and on every one of them both sizes fit inside it. Requiring the constant instead
        # would refuse a save the day the game picks a different block size, which is a
        # writer's choice and not a format change.
        if not 0 < max_plain or plain > max_plain or compressed > max_plain:
            raise ParseError(
                f"chunk at {start} declares a maximum of {max_plain} bytes but holds "
                f"{compressed} compressed / {plain} uncompressed. The preamble contradicts "
                "itself, so this is not a chunk header"
            )
        blob = r.bytes(compressed)
        try:
            chunk = zlib.decompress(blob)
        except zlib.error as exc:
            raise ParseError(f"chunk at {start} failed to inflate: {exc}") from exc
        if len(chunk) != plain:
            raise ParseError(
                f"chunk at {start} inflated to {len(chunk)} bytes, header said {plain}"
            )
        out.append(chunk)

    if r.remaining:
        raise ParseError(f"{r.remaining} trailing byte(s) after the last chunk at {r.pos}")
    if not out:
        # Reachable, not defensive: a save truncated to exactly its header length leaves no
        # chunks at all, and returning an empty body sent the refusal down to the level walk,
        # which reported "read of 8 at 0 runs past end (0)" -- an offset into a body that was
        # never there. `header` now refuses that file first; this is the same statement made
        # where the chunks are, for any other caller that lands here.
        raise ParseError(f"no chunk at {offset}: nothing follows the header")
    return b"".join(out)
