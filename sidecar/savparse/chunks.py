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
"""

from __future__ import annotations

import zlib

from .reader import Reader

__all__ = ["CHUNK_TAG", "ZLIB", "decompress_body"]

#: PACKAGE_FILE_TAG, as the game writes it into each chunk preamble: Unreal's
#: 0x9E2A83C1 in the low half, 0x22222222 in the high.
CHUNK_TAG = 0x222222229E2A83C1

#: The only compressor seen. Named rather than assumed so an unexpected one is refused
#: with its value, which is a far better bug report than a zlib error.
ZLIB = 3


def decompress_body(data: bytes, offset: int) -> bytes:
    """Inflate every chunk from ``offset`` to the end of ``data``."""
    r = Reader(data, offset)
    out: list[bytes] = []
    while r.remaining >= 49:
        start = r.pos
        tag = r.u64()
        if tag != CHUNK_TAG:
            raise ValueError(
                f"chunk at {start} has tag {tag:#x}, expected {CHUNK_TAG:#x} -- the body "
                "is not a chunk stream here, which usually means the file was still "
                "being written"
            )
        r.i64()  # max chunk size, informational
        algo = r.i8()
        if algo != ZLIB:
            raise ValueError(
                f"chunk at {start} uses compressor {algo}, only {ZLIB} (zlib) is known"
            )
        first_compressed, first_plain = r.i64(), r.i64()
        compressed, plain = r.i64(), r.i64()
        if (first_compressed, first_plain) != (compressed, plain):
            raise ValueError(
                f"chunk at {start} disagrees with itself: {first_compressed}/{first_plain} "
                f"then {compressed}/{plain}"
            )

        blob = r.bytes(compressed)
        try:
            chunk = zlib.decompress(blob)
        except zlib.error as exc:
            raise ValueError(f"chunk at {start} failed to inflate: {exc}") from exc
        if len(chunk) != plain:
            raise ValueError(
                f"chunk at {start} inflated to {len(chunk)} bytes, header said {plain}"
            )
        out.append(chunk)

    if r.remaining:
        raise ValueError(f"{r.remaining} trailing byte(s) after the last chunk at {r.pos}")
    return b"".join(out)
