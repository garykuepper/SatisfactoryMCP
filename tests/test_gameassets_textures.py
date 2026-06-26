"""The mip chain's arithmetic, and the one line that keeps the ocean blue.

Both are checks a generator makes before it decodes anything, and both are the kind of
thing that is right until somebody simplifies it. The chain totals are asserted against the
numbers the two container tools actually check their ``.ubulk`` files against -- 11,182,080
bytes of map slice, 11,173,888 of interface heightfield -- because deriving the total is
only worth anything if the derivation still lands on the file's real length.

``decode_bc1_rgba`` is exercised with stand-ins for ``texture2ddecoder`` and Pillow. That
is not a convenience: this suite runs on a machine with neither installed, and what is
under test is the argument pair, not anybody's block decoder.
"""

from __future__ import annotations

from satisfactory_mcp.core.gameassets.textures import (
    bc1_mip_sizes,
    decode_bc1_rgba,
    raw_mip_sizes,
)


def test_the_map_slices_chain_is_the_length_the_generator_refuses_to_read_past():
    """Four 4096 px BC1 slices: six levels, 11,182,080 bytes, mip 0 the first 8,388,608."""
    chain = bc1_mip_sizes(4096, 6)

    assert [px for px, _size in chain] == [4096, 2048, 1024, 512, 256, 128]
    assert chain[0][1] == 8_388_608
    assert sum(size for _px, size in chain) == 11_182_080


def test_a_bc1_level_never_costs_less_than_one_block():
    """Below 4 px a side the level is still one 4x4 block, which is why this is not a ratio.

    The floor is the only part of BC1's arithmetic that is not multiplication, so it is the
    only part a rewrite can get wrong while every number a real texture produces stays
    right. Levels that small never appear in this project's chains -- which is exactly why
    the property is asserted here rather than left to be discovered by a re-cook.
    """
    assert bc1_mip_sizes(8, 4) == ((8, 32), (4, 8), (2, 8), (1, 8))
    assert bc1_mip_sizes(4096, 1) == ((4096, 8_388_608),)
    assert bc1_mip_sizes(4096, 0) == ()


def test_the_interface_heightfield_chain_is_texel_by_texel():
    """2048 down to 128 at two bytes a float16 texel, and no block floor anywhere in it."""
    chain = raw_mip_sizes(2048, 5, 2)

    assert [px for px, _size in chain] == [2048, 1024, 512, 256, 128]
    assert chain[0][1] == 2048 * 2048 * 2
    assert sum(size for _px, size in chain) == 11_173_888
    # Every level is exactly its own area: no padding, no minimum, unlike BC1 above.
    assert all(size == px * px * 2 for px, size in chain)
    assert raw_mip_sizes(4, 3, 1) == ((4, 16), (2, 4), (1, 1))


class _Decoder:
    """``texture2ddecoder`` as far as this module is concerned: one function."""

    def __init__(self) -> None:
        self.calls: list[tuple[bytes, int, int]] = []

    def decode_bc1(self, raw: bytes, width: int, height: int) -> bytes:
        self.calls.append((raw, width, height))
        return b"%s as %dx%d BGRA" % (raw, width, height)


class _Imaging:
    """Pillow as far as this module is concerned: ``frombytes`` and what it was told."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def frombytes(self, mode, size, data, decoder_name, args):
        self.calls.append((mode, size, data, decoder_name, args))
        return "an image"


def test_the_decode_says_bgra_out_loud_because_rgba_also_produces_a_picture():
    """The raw/BGRA pair is the whole trick, and dropping it fails silently.

    ``decode_bc1`` hands back B, G, R, A. Read as ``"RGBA"`` the red and blue channels
    swap: the ocean goes orange and the result still looks like a stylised map, which is
    why this is asserted rather than eyeballed. The square is passed as one number because
    every level in this project is one.
    """
    decoder, imaging = _Decoder(), _Imaging()

    assert decode_bc1_rgba(decoder, imaging, b"blocks", 4096) == "an image"

    assert decoder.calls == [(b"blocks", 4096, 4096)]
    mode, size, data, decoder_name, args = imaging.calls[0]
    assert (mode, size) == ("RGBA", (4096, 4096))
    assert (decoder_name, args) == ("raw", "BGRA"), "reading BGRA as RGBA turns the ocean orange"
    assert data == b"blocks as 4096x4096 BGRA", "the decoder's own bytes, unmodified"
