"""What a cooked texture's bytes are: how long the mip chain is, and the blocks unpacked.

Three generators read a ``.ubulk`` out of the container -- the map sheet's four BC1 slices,
the interface heightfield's float16 raster, and the item icons' BC3 and raw BGRA squares --
and all of them make the same check before they decode a byte of it. **The file's length is
the integrity check.** A mip chain is stored largest-first with nothing between the levels,
so its total is arithmetic: derive it, and a file that is exactly that long still has the
size and mip count the reader knows how to read, while one of any other length was re-cooked
at another size, i.e. *the game changed*, and is not decoded on a guess. Deriving it rather
than typing the number in is what stops the check from drifting away from the thing it
checks.

That is the whole of the shared knowledge, and it is why this module is short. The decoders
themselves are not here: ``decode_bc1_rgba`` takes ``texture2ddecoder`` and Pillow as
arguments, the same seam ``IoStore`` takes its Oodle callable through, so this file imports
neither and a machine without the ``gen`` extra still imports it.
"""

from __future__ import annotations

#: Bytes per 4x4 block, per compressed format this project has met in the container.
#:
#: BC1 (``PF_DXT1``) spends 8 bytes on a block and carries at most one bit of alpha; BC3
#: (``PF_DXT5``) spends 16, the extra eight being an interpolated alpha block. Both are the
#: same grid of 4x4 blocks over the same mip chain, which is why one arithmetic serves both
#: and only this number changes.
BC1_BLOCK_BYTES = 8
BC3_BLOCK_BYTES = 16

#: The side of a BC block, and the floor a level cannot go below. A 2x2 level is still one
#: whole block on disk, which is the only part of this arithmetic that is not multiplication.
BLOCK_PX = 4


def block_mip_sizes(px: int, count: int, block_bytes: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a block-compressed chain of ``count`` levels from ``px``.

    The shared arithmetic behind :func:`bc1_mip_sizes` and :func:`bc3_mip_sizes`: whole 4x4
    blocks, and a level narrower than four texels still costs one block -- which is what
    ``max(side, BLOCK_PX)`` is, and why this cannot be :func:`raw_mip_sizes` with a
    fractional texel size.
    """
    return tuple(
        ((px >> i), (max(px >> i, BLOCK_PX) // BLOCK_PX) ** 2 * block_bytes) for i in range(count)
    )


def bc1_mip_sizes(px: int, count: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a BC1 (DXT1) chain of ``count`` levels from ``px``."""
    return block_mip_sizes(px, count, BC1_BLOCK_BYTES)


def bc3_mip_sizes(px: int, count: int) -> tuple[tuple[int, int], ...]:
    """``((side, bytes), ...)`` for a BC3 (DXT5) chain of ``count`` levels from ``px``.

    Twice BC1's bytes for the identical grid: 629 of the game's 742 item icons are cooked
    ``PF_DXT5``, because an icon is a cut-out and needs the interpolated alpha BC1 has not
    got. Same chain, same largest-first layout, same length check.
    """
    return block_mip_sizes(px, count, BC3_BLOCK_BYTES)


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


def decode_bc3_rgba(decoder, image_mod, raw: bytes, px: int):
    """One square BC3 level as an image. ``decode_bc3`` returns **BGRA**, same as BC1.

    The channel order is the trick here for the same reason and with a worse failure mode:
    an item icon read as ``"RGBA"`` comes out with red and blue swapped, and a blue Iron
    Plate against a transparent background looks like a deliberate palette rather than like
    a bug. The explicit ``"raw", "BGRA"`` is the whole of the difference.

    The alpha is the reason these are BC3 and not BC1 at all -- an icon is a cut-out, and
    ``decode_bc1`` would give it one bit of transparency and a hard fringe.
    """
    return image_mod.frombytes("RGBA", (px, px), decoder.decode_bc3(raw, px, px), "raw", "BGRA")


def decode_bgra8_rgba(image_mod, raw: bytes, px: int):
    """One square ``PF_B8G8R8A8`` level as an image. No decoder: it is already texels.

    113 of the game's 742 item icons are cooked uncompressed, four bytes a texel, in the
    same B, G, R, A order the two block decoders hand back -- so this is the same
    ``"raw", "BGRA"`` argument pair with the decompression step removed, and it takes no
    ``decoder`` at all because there is nothing to decompress.

    Kept beside the block decoders rather than inlined in the generator precisely because
    the channel order is the thing that goes wrong, and it should go wrong or not go wrong
    in one place for all three formats.
    """
    return image_mod.frombytes("RGBA", (px, px), raw, "raw", "BGRA")
