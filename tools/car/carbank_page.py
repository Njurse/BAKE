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

--transparent KEY marks the art's backdrop as transparent. The engine has no
per-texel alpha: its marker is a CLUT entry of 0x0000, which is what a black
texel reads as. So a sprite drawn on a pink (or any flat) backdrop bakes to an
OPAQUE square unless that backdrop's entry is cleared - every keyed texel is
forced onto one palette index and that entry is written 0x0000 in every row.
KEY is a hex colour (ff00ff), "auto" (the image's own top-left pixel) or
"alpha" (the PNG's alpha channel, for art exported with one).

    python carbank_page.py work/pages/page2.png --set 2 --bank . --transparent ff00ff
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
    """(RGB texels, alpha or None) for a 256x256 page image."""
    from PIL import Image

    with Image.open(path) as img:
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        alpha = None

        if has_alpha:
            rgba = img.convert("RGBA")
            src = rgba.getdata()
            pixels = [(p[0], p[1], p[2]) for p in src]
            alpha = [p[3] for p in src]
        else:
            pixels = list(img.convert("RGB").getdata())

        w, h = img.size

        if (w, h) != (fourbpp.PAGE_W, fourbpp.PAGE_H):
            raise SystemExit(
                "%s is %dx%d; a texture page is %dx%d. Resize the atlas to one "
                "page first - scaling it here would silently choose a filter."
                % (path, w, h, fourbpp.PAGE_W, fourbpp.PAGE_H))

        return pixels, alpha


def key_mask(pixels, key, tolerance=0):
    """Which texels are the backdrop: within `tolerance` of `key`, per channel."""
    return [max(abs(p[0] - key[0]), abs(p[1] - key[1]), abs(p[2] - key[2]))
            <= tolerance for p in pixels]


def bake_page(pixels, npal_keep=1, key=None, tolerance=0, mask=None):
    """RGB texels -> (page blob, colours, indices, error, background).

    `key` (an (r, g, b)) - or an explicit per-texel `mask` - marks the art's
    backdrop, and those texels are made TRANSPARENT: they are all forced onto one
    palette index and that entry is written 0x0000 in every CLUT row, which is
    the value the engine's draws read as transparent. A backdrop left alone bakes
    as an opaque square, which is what a pink/magenta sky behind a wheel sprite
    turns into.

    `background` is None when nothing was keyed, else {index, keyed, stray,
    colour}: how many texels went transparent, and how many texels that were NOT
    keyed share the transparent index (they vanish too - a stray here means the
    backdrop colour was too close to the art).
    """
    colours, indices = fourbpp.paletteize(pixels, fourbpp.PAGE_W, fourbpp.PAGE_H)
    indices = bytearray(indices)

    if mask is None and key is not None:
        mask = key_mask(pixels, key, tolerance)

    background = None

    if mask is not None and any(mask):
        # the index the backdrop occupies: whichever one most of its texels took.
        # The quantiser may invent several near-pink entries for one flat
        # backdrop, so all of them are folded onto that single index.
        votes = {}

        for i, keyed_texel in enumerate(mask):
            if keyed_texel:
                votes[indices[i]] = votes.get(indices[i], 0) + 1

        at = max(votes, key=lambda k: (votes[k], -k))
        cleared = tuple(colours[at])

        if key is not None and max(abs(cleared[c] - key[c]) for c in range(3)) > tolerance:
            raise SystemExit(
                "the backdrop #%02x%02x%02x is not in this page's 16 colours "
                "(its nearest is index %d, #%02x%02x%02x). Widen "
                "--transparent-tolerance, or give the sprite a page of its own."
                % (key[0], key[1], key[2], at, cleared[0], cleared[1], cleared[2]))

        for i, keyed_texel in enumerate(mask):
            if keyed_texel:
                indices[i] = at

        background = {"index": at,
                      "keyed": sum(1 for m in mask if m),
                      "stray": sum(1 for i, v in enumerate(indices)
                                   if v == at and not mask[i]),
                      "colour": cleared}

    raw = fourbpp.encode_indices(bytes(indices))
    # decomp_asm fills the buffer from the end, so the stream is the page reversed
    stream = fourbpp.encode_literal(raw[::-1])
    row = bytearray(fourbpp.rgb_to_clut(colours[:fourbpp.CLUT_ROW_ENTRIES]))

    if background is not None:
        # 0x0000: black, and the engine's transparent marker
        struct.pack_into("<H", row, background["index"] * 2, 0x0000)

    row = bytes(row)
    blob = struct.pack("<i", npal_keep) + row * npal_keep + stream

    # what the engine will actually show, so the error is measured not assumed.
    # Keyed texels are excluded: their colour is deliberately gone.
    q = fourbpp.clut_rgb(row)
    worst = 0
    total = 0
    counted = 0

    for i, rgb in enumerate(pixels):
        if mask is not None and mask[i]:
            continue

        got = q[indices[i]]
        d = max(abs(rgb[0] - got[0]), abs(rgb[1] - got[1]), abs(rgb[2] - got[2]))
        worst = max(worst, d)
        total += d
        counted += 1

    errors = (total / float(counted) if counted else 0.0, worst)

    return blob, colours, indices, errors, background


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


def _parse_key(text, pixels, alpha):
    """--transparent KEY -> (key colour or None, explicit mask or None)."""
    if text is None:
        return None, None

    value = text.strip().lower()

    if value == "alpha":
        if alpha is None:
            raise SystemExit("--transparent alpha: this image has no alpha channel")
        return None, [a < 128 for a in alpha]

    if value == "auto":
        if alpha is not None and any(a < 128 for a in alpha):
            return None, [a < 128 for a in alpha]
        return pixels[0], None            # the image's own top-left pixel

    digits = value[1:] if value.startswith("#") else value

    if len(digits) != 6:
        raise SystemExit("--transparent %r is not a hex colour (rrggbb), "
                         "\"auto\" or \"alpha\"" % text)

    try:
        rgb = tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise SystemExit("--transparent %r is not a hex colour (rrggbb)"
                         % text)

    return rgb, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="a 256x256 atlas (any mode)")
    ap.add_argument("--set", type=int, required=True, help="texture page set id")
    ap.add_argument("--bank", required=True,
                    help="the bank workspace (the folder holding bank.json)")
    ap.add_argument("--transparent", metavar="KEY", default=None,
                    help="make this colour transparent: a hex colour (ff00ff), "
                         "\"auto\" (the image's top-left pixel) or \"alpha\" "
                         "(the PNG's own alpha channel)")
    ap.add_argument("--transparent-tolerance", type=int, default=0, metavar="N",
                    help="per-channel slack when matching the colour, 0..255 "
                         "(default 0 = exact; a soft/anti-aliased edge needs more)")
    ap.add_argument("--no-preview", action="store_true",
                    help="skip pages/PAGE_<set>.png")
    args = ap.parse_args(argv)

    pixels, alpha = _load_rgb(args.image)
    key, mask = _parse_key(args.transparent, pixels, alpha)

    blob, colours, indices, error, background = bake_page(
        pixels, key=key, tolerance=args.transparent_tolerance, mask=mask)

    path = write_page(os.path.abspath(args.bank), args.set, blob, indices,
                      colours, preview=not args.no_preview)
    old = os.path.getsize(path) - len(blob)

    print("wrote %s (%d bytes: 1 CLUT row + stream)" % (path, len(blob)))
    print("  colours: %d used of 16" % len([c for c in colours if c != (0, 0, 0)]))
    print("  error vs source: mean %0.1f, peak %d (of 255)" % error)

    if background is None:
        if args.transparent:
            print("  transparent: nothing matched %s" % args.transparent)
    else:
        print("  transparent: index %d (#%02x%02x%02x) cleared in every row, "
              "%d texel(s)" % (background["index"], background["colour"][0],
                               background["colour"][1], background["colour"][2],
                               background["keyed"]))

        if background["stray"]:
            print("  warning: %d texel(s) that were NOT transparent share that "
                  "index and vanish too" % background["stray"])

    print("  other pages untouched%s" % (" (previous page was %+d bytes)" % old
                                          if old else ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
