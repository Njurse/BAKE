#!/usr/bin/env python3
"""Encode a car-select portrait raster to one 4bpp page.

Port of the pack-vs-remap split in JERICHO-Mod-carhacks
``tools/portrait_encode.py`` (see that repo's ``docs/PORTRAITS.md``). This
module does not import the carhacks tool. The bake calls ``encode_path``
when ``portrait`` ends in ``.png``, ``.tga``, or ``.bmp``.

Two ways in, chosen from the file itself:

  * A mode-P image whose first 16 palette entries already ARE the frontend
    portrait CLUT. The indices are packed as they stand. Colours are not
    re-quantised, so the page is not crushed when the stock CLUT draws it.
  * Any other raster (RGB, or a palette that is not that CLUT). Texels are
    remapped onto those same 16 colours with a 4x4 Bayer stipple between
    the two nearest entries. A texel that already is a CLUT colour stays
    there, so a flat area is not noisier than it has to be.

The CLUT is the retail one ``LoadBackgroundFile`` uploads from
``DATA\\CARS\\CARBACK.RAW`` (page 11, the first 32 bytes) to VRAM
``(960, 256)``. It is not built with ``fourbpp.rgb555``: that helper's
rounding is what already-baked banks depend on, and rebuilding this row
would clear the STP bit on entry 0 (``0x8000``). Index packing goes
through ``fourbpp.encode_indices`` only, which is the low-nibble-first
layout and does not touch RGB555.

A picture is 256x219 (the rows the frontend uploads) or a full 256x256
page. The short one is padded to 256 rows with index 0. No private CLUT
is produced: the page is drawn with the stock car-select palette.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "common"))

import fourbpp


PAGE_W = fourbpp.PAGE_W
PAGE_H = fourbpp.PAGE_H
SHOWN_H = 219
CLUT_ENTRIES = 16

# 4x4 Bayer, values 0..15. Same matrix as portrait_encode.remap_indices.
BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)

# Retail DATA\CARS\CARBACK.RAW, file offset 11 * 0x8000, 32 bytes. The
# same row carhacks ships as assets/portraits/CARHACKS/carback.clut.
# Entry 0 is 0x8000: STP set, RGB black. Do not round-trip this through
# fourbpp.rgb555.
CARBACK_PORTRAIT_CLUT = bytes.fromhex(
    "0080ffffa590a590848c218400800080"
    "b081c7903adf53b6e894cfa94a9d0180")


class PortraitError(Exception):
    """A raster portrait that cannot be turned into one 4bpp page."""


def expand_rgb555(word):
    """One little-endian RGB555 word -> the 8-bit colour the engine shows.

    Bit 15 (STP) is ignored. This is ``(channel << 3)``, the inverse of
    a read, not ``fourbpp.snap555`` and not ``fourbpp.rgb555``.
    """
    return (
        ((word >> 0) & 0x1F) << 3,
        ((word >> 5) & 0x1F) << 3,
        ((word >> 10) & 0x1F) << 3,
    )


def trunc_rgb555(rgb):
    """Drop the low 3 bits. What a CLUT entry displays, not snap555.

    A PNG saver keeps those bits, so an entry authored as the CLUT colour
    is not always byte-identical, but it displays as that entry.
    """
    return tuple((int(c) >> 3) << 3 for c in rgb[:3])


def portrait_clut():
    """``(16 RGB triples, 32 raw bytes)`` of the CARBACK portrait CLUT."""
    raw = CARBACK_PORTRAIT_CLUT
    colors = []

    for i in range(CLUT_ENTRIES):
        word = struct.unpack_from("<H", raw, i * 2)[0]
        colors.append(expand_rgb555(word))

    return tuple(colors), raw


def _pixels(im):
    if hasattr(im, "get_flattened_data"):
        return list(im.get_flattened_data())

    return list(im.getdata())


def palette_entries(im):
    pal = im.getpalette()

    if not pal:
        return []

    return [tuple(pal[i:i + 3]) for i in range(0, len(pal) - 2, 3)]


def palette_matches_clut(entries, clut):
    """True when indices 0..15 already ARE the frontend CLUT, in order.

    Compared after dropping the low 3 bits. Order matters: packing the
    index only works when slot N is CLUT N.
    """
    if len(entries) < CLUT_ENTRIES:
        return False

    return tuple(trunc_rgb555(c) for c in entries[:CLUT_ENTRIES]) == tuple(clut)


def _fold_index(index, entries, clut):
    """Keep an authored index. A stray slot above 15 folds only on an exact
    RGB555 match (a duplicate near-black a PNG saver invented). Keeping the
    low nibble of that slot would paint a different CLUT colour. Anything
    else is not this CLUT and must not be packed.
    """
    if index < CLUT_ENTRIES:
        return index

    if index >= len(entries):
        return None

    shown = trunc_rgb555(entries[index])

    for i, color in enumerate(clut):
        if color == shown:
            return i

    return None


def pack_indices(im, clut):
    """Indices of a mode-P image whose palette is the frontend CLUT.

    Returns ``(indices, folded)``. ``folded`` counts texels whose palette
    slot was above 15 and landed on the matching CLUT entry. No remap.
    """
    if im.mode != "P":
        raise PortraitError("pack wants a mode-P image, got %s" % im.mode)

    entries = palette_entries(im)

    if not palette_matches_clut(entries, clut):
        raise PortraitError(
            "palette is not the CARBACK portrait CLUT in index order")

    folded = 0
    indices = []

    for index in _pixels(im):
        mapped = _fold_index(index, entries, clut)

        if mapped is None:
            raise PortraitError(
                "index %d is outside the portrait CLUT and is not the same "
                "RGB555 colour as any entry" % index)

        if mapped != index:
            folded += 1

        indices.append(mapped)

    return indices, folded


def _dist2(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


def _nearest_two(rgb, clut):
    best = (10 ** 9, 0)
    second = (10 ** 9, 0)

    for i, color in enumerate(clut):
        d = _dist2(rgb, color)

        if d < best[0]:
            second = best
            best = (d, i)
        elif d < second[0]:
            second = (d, i)

    return best, second


def remap_indices(rgb_rows, clut):
    """RGB texels -> one index per texel, Bayer between the two nearest.

    A texel that already is a CLUT colour stays on that index, so flat
    areas are not stippled. Identical CLUT entries (this CLUT repeats
    black, and one olive) never dither against each other. This is the
    carhacks ``remap`` path with dither ``bayer``, which is that tool's
    default for a screenshot.
    """
    out = []

    for n, rgb in enumerate(rgb_rows):
        (d0, i0), (d1, i1) = _nearest_two(tuple(rgb), clut)
        x = n % PAGE_W
        y = n // PAGE_W

        if d0 == 0 or clut[i0] == clut[i1] or d0 + d1 == 0:
            out.append(i0)
            continue

        # 0 when the texel is the first colour, ~0.5 when it sits halfway.
        t = d0 / float(d0 + d1)
        b = (BAYER4[y & 3][x & 3] + 0.5) / 16.0
        out.append(i1 if b < t else i0)

    return out


def image_rgb(im):
    """RGB triples. Alpha is composited over black (CLUT 0 is the dark entry)."""
    if im.mode == "P":
        rgb = im.convert("RGB")
    elif im.mode == "RGBA":
        from PIL import Image

        base = Image.new("RGB", im.size, (0, 0, 0))
        base.paste(im, mask=im.getchannel("A"))
        rgb = base
    else:
        rgb = im.convert("RGB")

    return _pixels(rgb)


def _canvas_ok(size):
    return size in ((PAGE_W, SHOWN_H), (PAGE_W, PAGE_H))


def _size_error(label, size):
    width, height = size
    return PortraitError(
        "%s is %dx%d; a portrait raster is %dx%d (the rows the frontend "
        "uploads) or one full %dx%d page"
        % (label, width, height, PAGE_W, SHOWN_H, PAGE_W, PAGE_H))


def encode_image(im, path=None):
    """One raster -> 32768 bytes of 4bpp. Auto: pack when the palette matches.

    ``path`` is only there so a size error can name the file.
    """
    label = os.path.basename(path) if path else "portrait"
    height = im.size[1]

    if not _canvas_ok(im.size):
        raise _size_error(label, im.size)

    clut, _raw = portrait_clut()
    entries = palette_entries(im) if im.mode == "P" else []
    matched = im.mode == "P" and palette_matches_clut(entries, clut)

    if matched:
        indices, _folded = pack_indices(im, clut)
    else:
        indices = remap_indices(image_rgb(im), clut)

    if height == SHOWN_H:
        indices = list(indices) + [0] * (PAGE_W * (PAGE_H - SHOWN_H))

    return fourbpp.encode_indices(bytes(indices))


def encode_path(path):
    """Open a PNG, TGA, or BMP with Pillow and encode it.

    Pillow is the loader car texture pages already use (``carbank_page``).
    """
    from PIL import Image

    name = os.path.basename(path)

    try:
        im = Image.open(path)
    except OSError as exc:
        raise PortraitError("%s is not a readable image (%s)" % (name, exc))

    try:
        if not _canvas_ok(im.size):
            raise _size_error(name, im.size)

        im.load()
        return encode_image(im, path)
    finally:
        im.close()
