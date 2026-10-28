#!/usr/bin/env python3
"""jmpped_images.py - PNG, CLUT and texture-page helpers."""

from __future__ import annotations

import os
import struct
import sys
import zlib
from typing import Iterable, Sequence

from jmpped_core import *
from jmpped_archive import Page

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
import fourbpp                 # the one 4bpp/CLUT conversion (tools/common)


# --------------------------------------------------------------------------
# Pages, CLUTs and the 4bpp index map
# --------------------------------------------------------------------------

def cluts_from_bytes(clut: bytes, npalettes: int) -> list[bytes]:
    if npalettes < 0 or npalettes > 128:
        raise JMPPEDError("bad CLUT count %d" % npalettes)
    rows = []
    for i in range(npalettes):
        o = (i // 4) * CLUT_GROUP_BYTES + (i % 4) * CLUT_ROW_BYTES
        row = clut[o:o + CLUT_ROW_BYTES]
        rows.append(row + b"\x00" * (CLUT_ROW_BYTES - len(row)))
    return rows


def cluts_to_bytes(rows: Sequence[bytes]) -> bytes:
    if len(rows) > 128:
        raise JMPPEDError("more than 128 CLUT rows")
    groups = (len(rows) + 3) // 4
    out = bytearray(groups * CLUT_GROUP_BYTES)
    for i, row in enumerate(rows):
        if len(row) != CLUT_ROW_BYTES:
            raise JMPPEDError("CLUT row %d is %d bytes, expected %d"
                              % (i, len(row), CLUT_ROW_BYTES))
        o = (i // 4) * CLUT_GROUP_BYTES + (i % 4) * CLUT_ROW_BYTES
        out[o:o + CLUT_ROW_BYTES] = row
    return bytes(out)


def clut_rgb(row: bytes) -> list[tuple[int, int, int]]:
    """32 bytes -> 16 (r, g, b) 8-bit tuples (tools/common/fourbpp.py)."""
    if len(row) < CLUT_ROW_BYTES:
        raise JMPPEDError("short CLUT row")
    return fourbpp.clut_rgb(row)


def rgb_to_clut(colors: Sequence[tuple[int, int, int]]) -> bytes:
    """Up to 16 colours -> one 32-byte CLUT row (tools/common/fourbpp.py)."""
    return fourbpp.rgb_to_clut(colors)


def decomp_asm(src: bytes, out_bytes: int = PAGE_INDEX_BYTES) -> bytes:
    """Mirror of the engine's decomp_asm (first decoded byte at the end)."""
    return fourbpp.decomp_asm(src, out_bytes)[0]


def encode_literal(data: bytes) -> bytes:
    """Literal-only RLE stream; always valid input for decomp_asm."""
    return fourbpp.encode_literal(data)


def page_indices(page: Page) -> bytes:
    """Decode a page to 256x256 4-bit indices (one byte per texel, 0..15)."""
    if page.flags & PAGE_FLAG_COMPRESSED:
        if len(page.image) < 4:
            raise JMPPEDError("short compressed page image")
        ncluts = struct.unpack_from("<i", page.image, 0)[0]
        if ncluts < 0 or ncluts > 128:
            raise JMPPEDError("compressed page has %d CLUTs" % ncluts)
        start = 4 + ncluts * CLUT_ROW_BYTES
        raw = decomp_asm(page.image[start:])
    else:
        raw = page.image
    if len(raw) < PAGE_INDEX_BYTES:
        raise JMPPEDError("page image is %d bytes, expected %d"
                          % (len(raw), PAGE_INDEX_BYTES))
    return fourbpp.decode_indices(raw, PAGE_W, PAGE_H)


def encode_page_indices(indices: bytes) -> bytes:
    """One index byte per texel -> the packed 4bpp page image (fourbpp)."""
    if len(indices) != PAGE_BYTES:
        raise JMPPEDError("index map is %d bytes, expected %d"
                          % (len(indices), PAGE_BYTES))
    return fourbpp.encode_indices(indices, PAGE_W, PAGE_H)


def page_cluts(page: Page) -> list[bytes]:
    if page.flags & PAGE_FLAG_COMPRESSED:
        if len(page.image) < 4:
            raise JMPPEDError("short compressed page image")
        ncluts = struct.unpack_from("<i", page.image, 0)[0]
        return [page.image[4 + i * CLUT_ROW_BYTES:4 + (i + 1) * CLUT_ROW_BYTES]
                for i in range(ncluts)]
    return cluts_from_bytes(page.clut, page.npalettes)


def make_raw_page(page_set: int, indices: bytes, cluts: Sequence[bytes]) -> Page:
    return Page(set=page_set, npalettes=len(cluts), clut=cluts_to_bytes(cluts),
                image=encode_page_indices(indices), flags=PAGE_FLAG_RAW)


def page_rgb(indices: bytes, clut: Sequence[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    return [clut[i & 0x0F] if i < len(clut) else (0, 0, 0) for i in indices]


def nearest_clut_index(rgb: tuple[int, int, int],
                       clut: Sequence[tuple[int, int, int]]) -> int:
    """Nearest 4-bit CLUT entry, exact matches first.

    Pure black is the engine's transparent entry when the page is drawn with it,
    so a texel that is NOT pure black must not collapse onto that entry: a
    near-black anti-aliased edge would become a transparent hole. The rule
    itself lives in tools/common/fourbpp.py, where the car tools use it too.
    """
    return fourbpp.nearest_clut_index(rgb, clut)


def save_rgb_png(path: str, width: int, height: int,
                 pixels: Sequence[tuple[int, int, int]]) -> None:
    from PIL import Image
    if len(pixels) != width * height:
        raise JMPPEDError("PNG %s has %d pixels, expected %d"
                          % (path, len(pixels), width * height))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.frombytes("RGB", (width, height),
                    bytes(c for rgb in pixels for c in rgb)).save(path)


def save_indexed_png(path: str, width: int, height: int,
                     indices: Sequence[int],
                     clut: Sequence[tuple[int, int, int]]) -> None:
    from PIL import Image
    if len(indices) != width * height:
        raise JMPPEDError("indexed PNG %s has %d indices, expected %d"
                          % (path, len(indices), width * height))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    img = Image.frombytes("P", (width, height), bytes(i & 0x0F for i in indices))
    palette = []
    for i in range(CLUT_ROW_ENTRIES):
        palette.extend(clut[i] if i < len(clut) else (0, 0, 0))
    palette.extend([0, 0, 0] * (256 - CLUT_ROW_ENTRIES))
    img.putpalette(palette)
    img.save(path)


def load_png_rgb(path: str) -> tuple[int, int, list[tuple[int, int, int]]]:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    return img.width, img.height, list(img.getdata())


def load_png_indices(path: str) -> tuple[int, int, bytes]:
    from PIL import Image
    img = Image.open(path)
    img.load()
    if img.mode not in ("P", "L"):
        raise JMPPEDError("%s is %s, not an indexed PNG; export the indexed "
                          "page or paint an RGB page" % (path, img.mode))
    return img.width, img.height, bytes(img.getdata())
