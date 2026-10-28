#!/usr/bin/env python3
"""carbank_page.py - bake one 256x256 atlas into a bank's texture page.

    python carbank_page.py textures/firebird.png --set 1 --bank banks/firebird

A Driver 2 page entry is:

    [int npalettes][npalettes x 32-byte CLUT rows][decomp_asm stream]

which is what `pages/PAGE_<set>.bin` in a bank workspace holds, and what
pages1.read_page parses. This writes exactly that shape from an image, keeping
every other page in the bank untouched: a page the caller does not name is never
read or rewritten.

The image is reduced to what the engine can actually draw - 4bpp indices (16
colours) and a 16-entry RGB555 CLUT - and the tool reports the quantisation
error, because "it baked" and "it looks right" are different claims.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))

import fourbpp
import pages1


def _load_rgb(path):
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size

        if (w, h) != (fourbpp.PAGE_W, fourbpp.PAGE_H):
            raise SystemExit(
                "%s is %dx%d; a texture page is %dx%d. Resize the atlas to one "
                "page first - scaling it here would silently choose a filter."
                % (path, w, h, fourbpp.PAGE_W, fourbpp.PAGE_H))

        return list(img.getdata())


def bake_page(pixels, npal_keep=1):
    """RGB texels -> (page blob, colours, indices, error)."""
    colours, indices = fourbpp.paletteize(pixels, fourbpp.PAGE_W, fourbpp.PAGE_H)

    raw = fourbpp.encode_indices(indices)
    # decomp_asm fills the buffer from the end, so the stream is the page reversed
    stream = fourbpp.encode_literal(raw[::-1])
    row = fourbpp.rgb_to_clut(colours[:fourbpp.CLUT_ROW_ENTRIES])

    blob = struct.pack("<i", npal_keep) + row * npal_keep + stream

    # what the engine will actually show, so the error is measured not assumed
    q = fourbpp.clut_rgb(row)
    worst = 0
    total = 0

    for i, rgb in enumerate(pixels):
        got = q[indices[i]]
        d = max(abs(rgb[0] - got[0]), abs(rgb[1] - got[1]), abs(rgb[2] - got[2]))
        worst = max(worst, d)
        total += d

    return blob, colours, indices, (total / float(len(pixels)), worst)


def write_page(bank_dir, setid, blob, indices, colours, preview=True):
    pages_dir = os.path.join(bank_dir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    path = os.path.join(pages_dir, "PAGE_%d.bin" % setid)

    with open(path, "wb") as f:
        f.write(blob)

    if preview:
        import exportd2

        png = os.path.join(pages_dir, "PAGE_%d.png" % setid)
        pal = fourbpp.clut_rgb(fourbpp.rgb_to_clut(colours[:16]))
        pix = bytearray()

        for i in indices:
            r, g, b = pal[i]
            pix.extend((r, g, b))

        exportd2.write_png_rgb(png, fourbpp.PAGE_W, fourbpp.PAGE_H, pix)

    return path


def read_page(path):
    """(blob, colours, indices) for a page already on disk - for verification."""
    with open(path, "rb") as f:
        blob = f.read()

    rows, raw = pages1.read_page(blob, 0, len(blob))
    indices = fourbpp.decode_indices(raw)

    return blob, fourbpp.clut_rgb(rows[0]) if rows else [], indices


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="a 256x256 atlas (any mode)")
    ap.add_argument("--set", type=int, required=True, help="texture page set id")
    ap.add_argument("--bank", required=True,
                    help="the bank workspace (the folder holding bank.json)")
    ap.add_argument("--no-preview", action="store_true",
                    help="skip pages/PAGE_<set>.png")
    args = ap.parse_args(argv)

    pixels = _load_rgb(args.image)
    blob, colours, indices, error = bake_page(pixels)

    path = write_page(os.path.abspath(args.bank), args.set, blob, indices,
                      colours, preview=not args.no_preview)
    old = os.path.getsize(path) - len(blob)

    print("wrote %s (%d bytes: 1 CLUT row + stream)" % (path, len(blob)))
    print("  colours: %d used of 16" % len([c for c in colours if c != (0, 0, 0)]))
    print("  error vs source: mean %0.1f, peak %d (of 255)" % error)
    print("  other pages untouched%s" % (" (previous page was %+d bytes)" % old
                                          if old else ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
