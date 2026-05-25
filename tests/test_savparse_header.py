"""The .sav header, parsed by our own code.

Written to replace a vendored GPL-3.0 parser. The format is a fact about what the game
writes, not a creative work, so this implements the format and verifies black-box: same
file in, same values out. Measured across 67 saves on the author's disk, this agreed with
the old parser on all 31 it could read and failed on exactly the same 36 -- pre-1.0 saves
whose header the old parser also refuses.

Tests here run against a COMMITTED 2 KiB fixture rather than a live save, so they keep
working once the vendored library is gone and on a machine with no Satisfactory install.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from savparse import PACKAGE_FILE_TAG, Reader, read_info_bytes

FIXTURE = Path(__file__).parent / "fixtures" / "save_header.bin"


@pytest.fixture(scope="module")
def raw() -> bytes:
    if not FIXTURE.is_file():
        pytest.skip("header fixture not committed")
    return FIXTURE.read_bytes()


@pytest.fixture(scope="module")
def info(raw):
    return read_info_bytes(raw)


# ------------------------------------------------------------- the primitives


def test_a_positive_length_is_one_byte_per_character():
    # 5, "Han\0" is 4 -- length counts the terminator.
    data = (4).to_bytes(4, "little") + b"Han\x00"
    assert Reader(data).string() == "Han"


def test_a_negative_length_is_utf16():
    """The sign of the length IS the encoding, which is the one thing about Unreal's
    strings that is not guessable."""
    text = "Häns"
    data = (-(len(text) + 1)).to_bytes(4, "little", signed=True) + (text + "\x00").encode(
        "utf-16-le"
    )
    assert Reader(data).string() == text


def test_a_zero_length_string_consumes_nothing():
    r = Reader((0).to_bytes(4, "little") + b"rest")
    assert r.string() == ""
    assert r.remaining == 4


def test_reading_past_the_end_raises_rather_than_truncating(raw):
    """A silently short read would produce a header that parsed into plausible nonsense."""
    r = Reader(raw[:8])
    r.i32()
    with pytest.raises(ValueError, match="runs past end"):
        r.i64()


# ------------------------------------------------------------- the header


def test_it_reads_the_fields_the_projection_uses(info):
    """These are exactly the nine the sidecar puts in `header`."""
    assert info.session_name == "Han Solo"
    assert info.save_identifier == "X2faPVKjX06VaRzClNv5KQ"
    assert info.save_header_type == 14
    assert info.save_version == 60
    assert info.build_version == 495413
    assert info.play_duration_s == 1_151_711
    assert info.save_datetime_ticks == 639_208_697_277_810_000
    assert info.is_modded is False
    assert info.is_creative is False


def test_it_reads_the_rest_of_the_header_too(info):
    assert info.save_name == "Han Solo_280726-230847"
    assert info.map_name == "Persistent_Level"
    assert info.map_options.startswith("?skiponboarding")
    assert info.editor_object_version == 40
    assert info.mod_metadata == ""
    assert info.save_data_hash == (6_096_361_947_348_211_065, 9_325_011_144_171_762_175)


def test_the_header_ends_exactly_at_the_compressed_body(info, raw):
    """The proof that every field width above it is right. Each is positional, so one
    wrong width lands here at the wrong byte -- which is why the tag is checked rather
    than assumed."""
    assert Reader(raw, info.body_offset).u32() == PACKAGE_FILE_TAG


def _shifted(raw: bytes) -> bytes:
    """A header with four extra bytes before the body -- a field added by a patch.

    This is the realistic drift and the one the tag guard exists for. Corrupting a LENGTH
    instead desynchronises the very next string and blows up on the bounds check, which is
    a different failure; both are refusals and both are pinned, separately.
    """
    at = read_info_bytes(raw).body_offset
    return raw[:at] + bytes(4) + raw[at:]


def test_a_shifted_header_is_refused_not_guessed(raw):
    """Returning a plausible header from a format that moved is the failure worth
    preventing: every field is positional, so one wrong width silently rereads the rest."""
    with pytest.raises(ValueError, match="did not end at the compressed body"):
        read_info_bytes(_shifted(raw))


def test_the_error_names_the_versions_that_would_explain_it(raw):
    """A future patch shifting the layout should say which version it was reading, since
    that is the first thing anyone debugging it needs."""
    with pytest.raises(ValueError) as exc:
        read_info_bytes(_shifted(raw))
    assert "saveHeaderType is 14" in str(exc.value)
    assert "saveVersion 60" in str(exc.value)


def test_a_wild_length_is_refused_by_the_bounds_check(raw):
    """The other refusal, so both paths are pinned rather than one shadowing the other."""
    broken = bytearray(raw)
    broken[12:16] = (999_999).to_bytes(4, "little")
    with pytest.raises(ValueError, match="runs past end"):
        read_info_bytes(bytes(broken))
