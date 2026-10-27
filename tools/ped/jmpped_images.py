#!/usr/bin/env python3
"""jmpped_images.py - PNG, CLUT and texture-page helpers."""

from __future__ import annotations

import os
import struct
import zlib
from typing import Iterable, Sequence

from jmpped_core import *
from jmpped_archive import Page


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
    if len(row) < CLUT_ROW_BYTES:
        raise JMPPEDError("short CLUT row")
    out = []
    for i in range(CLUT_ROW_ENTRIES):
        v = struct.unpack_from("<H", row, i * 2)[0]
        out.append((((v >> 0) & 0x1F) << 3,
                    ((v >> 5) & 0x1F) << 3,
                    ((v >> 10) & 0x1F) << 3))
    return out


def rgb_to_clut(colors: Sequence[tuple[int, int, int]]) -> bytes:
    out = bytearray(CLUT_ROW_BYTES)
    for i, (r, g, b) in enumerate(colors[:CLUT_ROW_ENTRIES]):
        v = ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)
        struct.pack_into("<H", out, i * 2, v)
    return bytes(out)


def decomp_asm(src: bytes, out_bytes: int = PAGE_INDEX_BYTES) -> bytes:
    """Mirror of the engine's decomp_asm (first decoded byte at the end)."""
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
    return bytes(dest)


def encode_literal(data: bytes) -> bytes:
    """Literal-only RLE stream; always valid input for decomp_asm."""
    out = bytearray()
    for o in range(0, len(data), 128):
        chunk = data[o:o + 128]
        out.append(len(chunk) - 1)
        out += chunk
    return bytes(out)


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
    idx = bytearray(PAGE_BYTES)
    for y in range(PAGE_H):
        row = raw[y * (PAGE_W // 2):(y + 1) * (PAGE_W // 2)]
        for xb, b in enumerate(row):
            idx[y * PAGE_W + xb * 2] = b & 0x0F
            idx[y * PAGE_W + xb * 2 + 1] = (b >> 4) & 0x0F
    return bytes(idx)


def encode_page_indices(indices: bytes) -> bytes:
    if len(indices) != PAGE_BYTES:
        raise JMPPEDError("index map is %d bytes, expected %d"
                          % (len(indices), PAGE_BYTES))
    raw = bytearray(PAGE_INDEX_BYTES)
    for y in range(PAGE_H):
        for xb in range(PAGE_W // 2):
            lo = indices[y * PAGE_W + xb * 2] & 0x0F
            hi = indices[y * PAGE_W + xb * 2 + 1] & 0x0F
            raw[y * (PAGE_W // 2) + xb] = lo | (hi << 4)
    return bytes(raw)


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

    A page is 4bpp: every edited RGB pixel must become one index 0..15. Using a
    nearest-colour match keeps a painted PNG usable without requiring the editor
    to preserve an indexed palette.

    Pure black is the engine's transparent entry when the page is drawn with it,
    so a texel that is NOT pure black must not collapse onto that entry. When the
    nearest match is black, use the nearest other shade instead (the next black
    shade when the crop has one) - otherwise an anti-aliased dark edge becomes a
    transparent hole.
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
