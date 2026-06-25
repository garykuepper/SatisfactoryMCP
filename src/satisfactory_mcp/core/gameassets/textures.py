"""What a cooked texture's bytes are: how long the mip chain is, and BC1 unpacked.

Two generators read a ``.ubulk`` out of the container -- the map sheet's four BC1 slices
and the interface heightfield's float16 raster -- and both make the same check before they
decode a byte of it. **The file's length is the integrity check.** A mip chain is stored
largest-first with nothing between the levels, so its total is arithmetic: derive it, and a
file that is exactly that long still has the size and mip count the reader knows how to
read, while one of any other length was re-cooked at another size, i.e. *the game changed*,
and is not decoded on a guess. Deriving it rather than typing the number in is what stops
the check from drifting away from the thing it checks.

That is the whole of the shared knowledge, and it is why this module is three functions
long. The decoders themselves are not here: ``decode_bc1_rgba`` takes ``texture2ddecoder``
and Pillow as arguments, the same seam ``IoStore`` takes its Oodle callable through, so
this file imports neither and a machine without the ``gen`` extra still imports it.
"""

from __future__ import annotations


def bc1_mip_sizes(px: int, count: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a BC1 (DXT1) chain of ``count`` levels from ``px``.

    BC1 stores whole 4x4 blocks at 8 bytes each, and a level narrower than four texels
    still costs one block -- which is what ``max(side, 4)`` is, and why this cannot be
    :func:`raw_mip_sizes` with a fractional texel size.
    """
    return tuple(((px >> i), (max(px >> i, 4) // 4) ** 2 * 8) for i in range(count))


def raw_mip_sizes(px: int, count: int, texel_bytes: int) -> tuple[tuple[int, int], ...]:
    """The same, for a chain stored texel by texel: float16 heights, say, at two bytes.

    Each level halves the side, so level ``i`` is ``px >> i`` square and costs its own area
    times ``texel_bytes``. No block floor and no padding: an uncompressed level is exactly
    as long as it looks.
    """
    return tuple(((px >> i), (px >> i) ** 2 * texel_bytes) for i in range(count))


def decode_bc1_rgba(decoder, image_mod, raw: bytes, px: int):
    """One square BC1 level as an image. ``decode_bc1`` returns **BGRA**, which is the trick.

    ``texture2ddecoder.decode_bc1`` hands back raw bytes in B, G, R, A order; handing those
    to Pillow as ``"RGBA"`` swaps red and blue, which turns an ocean orange and looks enough
    like a stylised map to survive a glance. The explicit ``"raw", "BGRA"`` is what makes
    the water blue, and it is the one line of this that is worth having in one place.

    Both modules are parameters rather than imports, so the ``gen`` extra stays optional at
    import time and a test can drive this with a stand-in.
    """
    return image_mod.frombytes("RGBA", (px, px), decoder.decode_bc1(raw, px, px), "raw", "BGRA")
