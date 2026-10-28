#!/usr/bin/env python3
"""Tests for tools/common/fourbpp.py -- the shared 4bpp and CLUT pieces.

    python3 -m pytest tools/common -q
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fourbpp as f


# --- CLUT rows -------------------------------------------------------------

def test_clut_round_trip_keeps_the_high_bits():
    row = f.rgb_to_clut([(0, 0, 0), (255, 255, 255), (128, 64, 32)])
    back = f.clut_rgb(row)

    assert len(row) == f.CLUT_ROW_BYTES
    assert len(back) == f.CLUT_ROW_ENTRIES
    # 5 bits per channel: reading back masks the low three bits off
    assert back[0] == (0, 0, 0)
    assert back[1] == (248, 248, 248)
    assert back[2] == (128, 64, 32)


def test_flat_clut_row_fills_every_entry():
    row = f.flat_clut_row((255, 0, 0))
    colours = f.clut_rgb(row)

    assert len(colours) == f.CLUT_ROW_ENTRIES
    assert all(c == (248, 0, 0) for c in colours), colours


def test_short_clut_row_is_refused():
    try:
        f.clut_rgb(b"\x00" * 8)
    except ValueError as e:
        assert "short CLUT row" in str(e)
    else:
        raise AssertionError("a short CLUT row was accepted")


# --- the 4bpp index map ----------------------------------------------------

def test_index_codec_round_trips():
    indices = bytes((i * 7) & 0x0F for i in range(f.PAGE_BYTES))
    raw = f.encode_indices(indices)

    assert len(raw) == f.PAGE_INDEX_BYTES
    assert f.decode_indices(raw) == indices


def test_index_codec_packs_low_nibble_first():
    indices = bytearray(f.PAGE_BYTES)
    indices[0] = 1
    indices[1] = 2

    raw = f.encode_indices(bytes(indices))

    assert raw[0] == 0x21, "texel 0 must be the low nibble"


def test_index_codec_refuses_the_wrong_size():
    for bad, call in ((b"\x00" * 10, f.encode_indices),
                      (b"\x00" * 10, f.decode_indices)):
        try:
            call(bad)
        except ValueError as e:
            assert "expected" in str(e)
        else:
            raise AssertionError("a wrongly sized buffer was accepted")


# --- the page RLE ----------------------------------------------------------

def test_literal_stream_decodes_backwards():
    data = bytes(range(64))
    stream = f.encode_literal(data)
    out, consumed = f.decomp_asm(stream, len(data))

    # the engine's buffer is filled from the end, so the first source byte is
    # the LAST decoded byte
    assert out == data[::-1]
    assert consumed == len(stream)


def test_literal_stream_handles_a_tail_shorter_than_128():
    data = bytes(range(200))
    stream = f.encode_literal(data)
    out, _ = f.decomp_asm(stream, len(data))

    assert out == data[::-1]


# --- mapping colour back onto indices --------------------------------------

def test_nearest_index_prefers_an_exact_match():
    clut = f.clut_rgb(f.rgb_to_clut([(0, 0, 0), (255, 0, 0), (0, 255, 0)]))

    assert f.nearest_clut_index((248, 0, 0), clut) == 1
    assert f.nearest_clut_index((0, 248, 0), clut) == 2


def test_nearest_index_never_folds_a_non_black_texel_onto_black():
    clut = f.clut_rgb(f.rgb_to_clut([(0, 0, 0)] + [(8, 8, 8)] * 15))

    # (5,5,5) is nearest to black, but black means transparent to the engine
    assert f.nearest_clut_index((5, 5, 5), clut) != 0
    # a texel that really is black keeps it
    assert f.nearest_clut_index((0, 0, 0), clut) == 0


def test_reindex_keeps_the_palette_it_was_given():
    clut = f.clut_rgb(f.rgb_to_clut([(0, 0, 0), (255, 0, 0), (0, 0, 255)] * 5))
    indices = f.reindex_to_clut([(255, 0, 0), (0, 0, 255)], clut)

    assert list(indices) == [1, 2]


# --- the 16-colour quantiser ----------------------------------------------

def test_paletteize_takes_sixteen_colours_verbatim():
    pixels = [(i * 8, 0, 0) for i in range(16)]
    colours, patch = f.paletteize(pixels, 16, 1)

    assert colours[:16] == pixels
    assert list(patch) == list(range(16))


def test_paletteize_quantises_beyond_sixteen():
    pixels = [(i, i, i) for i in range(64)]
    colours, patch = f.paletteize(pixels, 8, 8)

    assert len(colours) == 16
    assert len(patch) == 64
    assert all(0 <= i < 16 for i in patch)


# --- editing one rectangle of a page --------------------------------------

def _page_with_white_corner():
    indices = bytearray(f.PAGE_BYTES)
    indices[0] = 1
    return f.encode_indices(bytes(indices))


def test_edit_page_changes_only_the_rectangle():
    cluts = [f.rgb_to_clut([(0, 0, 0), (255, 255, 255), (255, 0, 0)] * 6)]
    src = _page_with_white_corner()

    out = f.edit_page_rect(src, cluts, 0, [(255, 0, 0)] * 4, (4, 4, 2, 2))
    before = f.decode_indices(src)
    after = f.decode_indices(out)

    changed = [i for i in range(f.PAGE_BYTES) if before[i] != after[i]]

    assert len(changed) == 4, "exactly the 2x2 rectangle may change"
    assert all(after[i] == 2 for i in changed)
    assert after[0] == 1, "the rest of the page is untouched"


def test_edit_page_does_not_touch_the_clut():
    cluts = [f.rgb_to_clut([(0, 0, 0), (255, 255, 255)] * 8)]
    keep = bytes(cluts[0])

    f.edit_page_rect(_page_with_white_corner(), cluts, 0,
                     [(255, 255, 255)] * 4, (0, 0, 2, 2))

    assert cluts[0] == keep


def test_edit_page_refuses_bad_input():
    cluts = [f.rgb_to_clut([(0, 0, 0)] * 16)]
    src = _page_with_white_corner()

    cases = [
        ([(0, 0, 0)], (0, 0, 2, 2), 0, "pixels"),          # too few pixels
        ([(0, 0, 0)] * 4, (255, 255, 2, 2), 0, "outside"),  # 257 > 256
        ([(0, 0, 0)] * 4, (0, 0, 2, 2), 9, "palette row"),  # no such row
    ]

    for pixels, rect, row, want in cases:
        try:
            f.edit_page_rect(src, cluts, row, pixels, rect)
        except ValueError as e:
            assert want in str(e), (want, str(e))
        else:
            raise AssertionError("bad input accepted: %r %r %r"
                                 % (pixels[:2], rect, row))
