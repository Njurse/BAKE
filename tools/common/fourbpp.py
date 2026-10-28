#!/usr/bin/env python3
"""fourbpp.py -- the 4bpp / CLUT primitives the tools share.

Driver 2 stores textures as 4bpp pages (8-bit indices, two per byte) with
16-entry CLUT rows of RGB555 words, 32 bytes each. This module is the single
implementation of that conversion:

  * the car pipeline (tools/car) builds pages and flat CLUT rows for imported
    cars (bake._flat_555, bake._flat_clut_row, pages1.clut_rgb),
  * the pedestrian pipeline (tools/ped) reads and writes them for authored
    pedestrians (jmpped_images.clut_rgb, .rgb_to_clut).

The two copies agreed byte for byte -- the same `>> 3` scaling in both
directions -- which is exactly why they were merged rather than one of them
being kept. Nothing here is allowed to change the bytes: the moment a conversion
rounds differently, every existing bank and pedestrian page shifts colour.

PSX RGB555 layout: R in bits 0-4, G in 5-9, B in 10-14, bit 15 = STP
("semi-transparent", clear for opaque). Reading back with `<< 3` loses the low
three bits, which is what every consumer of these values already expects.
"""

from __future__ import annotations

import struct
from typing import Iterable, Sequence

CLUT_ROW_BYTES = 32                  # 16 entries * 2 bytes
CLUT_ROW_ENTRIES = 16
CLUT_GROUP_BYTES = 128               # four CLUT rows, the PSX packing unit
PAGE_W = 256                         # a Driver 2 texture page
PAGE_H = 256
PAGE_BYTES = PAGE_W * PAGE_H         # one 8-bit index per texel, 65536
PAGE_INDEX_BYTES = PAGE_BYTES // 2   # two 4bpp indices per byte, 32768


def rgb555(rgb8: Sequence[int]) -> int:
    """8-bit (r, g, b) -> one PSX RGB555 word (STP clear, so opaque)."""
    r, g, b = rgb8[0], rgb8[1], rgb8[2]
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def clut_rgb(row: bytes) -> list[tuple[int, int, int]]:
    """One 32-byte CLUT row -> 16 (r, g, b) 8-bit tuples."""
    if len(row) < CLUT_ROW_BYTES:
        raise ValueError("short CLUT row: %d bytes, expected %d"
                         % (len(row), CLUT_ROW_BYTES))

    out = []
    for i in range(CLUT_ROW_ENTRIES):
        v = struct.unpack_from("<H", row, i * 2)[0]
        out.append((((v >> 0) & 0x1F) << 3,
                    ((v >> 5) & 0x1F) << 3,
                    ((v >> 10) & 0x1F) << 3))
    return out


def rgb_to_clut(colors: Iterable[Sequence[int]]) -> bytes:
    """Up to 16 8-bit colours -> one 32-byte CLUT row (index 0 first)."""
    out = bytearray(CLUT_ROW_BYTES)
    for i, rgb8 in enumerate(list(colors)[:CLUT_ROW_ENTRIES]):
        struct.pack_into("<H", out, i * 2, rgb555(rgb8))
    return bytes(out)


def flat_clut_row(rgb8: Sequence[int]) -> bytes:
    """A CLUT row whose every entry is the same opaque colour.

    A solid-colour page is filled with index 0, so only entry 0 matters, but
    filling all 16 guards against a UV that accidentally samples another index
    and draws transparent black instead.
    """
    return struct.pack("<16H", *([rgb555(rgb8)] * CLUT_ROW_ENTRIES))

# ---------------------------------------------------------------------------
# The 4bpp index map
#
# A page image is 256x256 8-bit indices with two texels packed into each byte,
# low nibble first. The tools work on one byte per texel; the engine wants the
# packed form.
# ---------------------------------------------------------------------------

def decode_indices(raw: bytes, width: int = PAGE_W, height: int = PAGE_H) -> bytes:
    """A packed 4bpp page image -> one index byte per texel (0..15)."""
    expect = width * height // 2

    if len(raw) < expect:
        raise ValueError("page image is %d bytes, expected %d" % (len(raw), expect))

    idx = bytearray(width * height)
    row_bytes = width // 2

    for y in range(height):
        row = raw[y * row_bytes:(y + 1) * row_bytes]

        for xb, b in enumerate(row):
            idx[y * width + xb * 2] = b & 0x0F
            idx[y * width + xb * 2 + 1] = (b >> 4) & 0x0F

    return bytes(idx)


def encode_indices(indices: bytes, width: int = PAGE_W, height: int = PAGE_H) -> bytes:
    """One index byte per texel -> the packed 4bpp page image."""
    expect = width * height

    if len(indices) != expect:
        raise ValueError("index map is %d bytes, expected %d" % (len(indices), expect))

    raw = bytearray(expect // 2)
    row_bytes = width // 2

    for y in range(height):
        for xb in range(row_bytes):
            lo = indices[y * width + xb * 2] & 0x0F
            hi = indices[y * width + xb * 2 + 1] & 0x0F
            raw[y * row_bytes + xb] = lo | (hi << 4)

    return bytes(raw)


# ---------------------------------------------------------------------------
# Mapping RGB back onto a 4bpp page
# ---------------------------------------------------------------------------

def nearest_clut_index(rgb: tuple[int, int, int],
                       clut: Sequence[tuple[int, int, int]]) -> int:
    """Nearest 4-bit CLUT entry, exact matches first.

    A page is 4bpp: every edited RGB texel must become one index 0..15, and a
    nearest-colour match keeps a painted PNG usable without requiring the editor
    to preserve an indexed palette.

    Pure black is the engine's transparent entry when a page is drawn with it,
    so a texel that is NOT pure black must not collapse onto that entry - an
    anti-aliased dark edge would become a transparent hole. When the nearest
    match is black, the nearest other shade wins instead.
    """
    best = 0
    bestd = None
    r, g, b = rgb

    for i, (cr, cg, cb) in enumerate(clut[:CLUT_ROW_ENTRIES]):
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2

        if bestd is None or d < bestd:
            best, bestd = i, d

            if d == 0:
                break

    if rgb == (0, 0, 0) or clut[best] != (0, 0, 0):
        return best

    alt = None
    altd = None

    for i, (cr, cg, cb) in enumerate(clut[:CLUT_ROW_ENTRIES]):
        if (cr, cg, cb) == (0, 0, 0):
            continue

        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2

        if altd is None or d < altd:
            alt, altd = i, d

    return best if alt is None else alt


def reindex_to_clut(pixels: Sequence[Sequence[int]],
                    clut: Sequence[tuple[int, int, int]]) -> bytes:
    """RGB texels -> one index byte per texel against a fixed 16-colour CLUT.

    This is the path for a page whose palette must not move (an edited crop on
    an existing page): the colours stay, only the indices are recomputed.
    """
    return bytes(nearest_clut_index(tuple(p), clut) for p in pixels)


def paletteize(pixels: Sequence[Sequence[int]], width: int, height: int):
    """RGB texels -> (16 colours, one index byte per texel).

    Used when a crop brings colours the existing palette cannot express. Sixteen
    or fewer distinct colours are taken verbatim (no quantiser can do better);
    beyond that Pillow's median cut picks the palette. Pillow may fold a
    near-black texel into the palette's pure-black entry, and black is the
    engine's transparent marker, so every texel that is not really pure black is
    moved to the next shade - otherwise an anti-aliased edge becomes a hole.
    """
    if len(pixels) != width * height:
        raise ValueError("crop has %d pixels, expected %d"
                         % (len(pixels), width * height))

    exact = list(dict.fromkeys(tuple(p) for p in pixels))

    if len(exact) <= CLUT_ROW_ENTRIES:
        colors = exact + [(0, 0, 0)] * (CLUT_ROW_ENTRIES - len(exact))
        index = {c: i for i, c in enumerate(exact)}
        return colors, bytes(index[tuple(p)] for p in pixels)

    from PIL import Image

    data = bytes(c for p in pixels for c in p)
    img = Image.frombytes("RGB", (width, height), data)

    try:
        q = img.quantize(colors=CLUT_ROW_ENTRIES,
                         method=Image.Quantize.MEDIANCUT,
                         dither=Image.Dither.NONE)
    except AttributeError:
        q = img.quantize(colors=CLUT_ROW_ENTRIES)

    pal = q.getpalette()[:48]
    colors = [(pal[i], pal[i + 1], pal[i + 2]) for i in range(0, 48, 3)]
    colors += [(0, 0, 0)] * (CLUT_ROW_ENTRIES - len(colors))
    patch = bytes(q.getdata())

    if any(c == (0, 0, 0) for c in colors):
        fixed = bytearray(patch)

        for i, p in enumerate(pixels):
            if tuple(p) == (0, 0, 0):
                continue

            if colors[patch[i]] == (0, 0, 0):
                fixed[i] = nearest_clut_index(tuple(p), colors)

        patch = bytes(fixed)

    return colors, patch

# ---------------------------------------------------------------------------
# The engine's page RLE (decomp_asm and its literal-only encoder)
#
# A compressed page image is this stream, decoded BACKWARDS: the first decoded
# byte lands at the end of the buffer. A byte with bit 7 set is a run of
# (258 - value) copies of the next byte; otherwise it is a literal run of
# (value + 1) bytes.
# ---------------------------------------------------------------------------

def decomp_asm(src: bytes, out_bytes: int):
    """Mirror of the engine's decomp_asm: (decoded buffer, bytes consumed)."""
    dest = bytearray(out_bytes)
    ptr = out_bytes - 1
    i = 0
    n = len(src)

    while ptr >= 0 and i < n:
        pix = src[i]
        i += 1

        if pix & 0x80:
            if i >= n:
                break

            p = src[i]
            i += 1

            for _ in range(258 - pix):
                if ptr < 0:
                    break

                dest[ptr] = p
                ptr -= 1
        else:
            for _ in range(pix + 1):
                if ptr < 0 or i >= n:
                    break

                dest[ptr] = src[i]
                i += 1
                ptr -= 1

    return bytes(dest), i


def encode_literal(data: bytes) -> bytes:
    """Literal-only RLE stream; always valid input for decomp_asm.

    About 1.008x the size, which is why the bakes never need a real matcher.
    """
    out = bytearray()

    for o in range(0, len(data), 128):
        chunk = data[o:o + 128]
        out.append(len(chunk) - 1)
        out += chunk

    return bytes(out)
