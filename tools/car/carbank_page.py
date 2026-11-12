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

--protect keeps colours the 16 must hold (a headlight grey the quantiser would
otherwise fold into the body); --palette states all 16 outright and quantises
nothing; --report N prints the N worst-crushed source colours, which is the
measurement behind "the greys survived".

    python carbank_page.py work/pages/page2.png --set 2 --bank . --transparent ff00ff
"""

import argparse
import math
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


def stray_key_entries(rows, key, tolerance=0, chroma=None, transparent=None):
    """Palette entries that still PAINT the key colour, on a page that keys it.

    The check the bake makes after the fact: whatever the fold did, a row that
    can still draw the backdrop is a bug waiting for a texel to land on it. The
    transparent index itself is exempt - it is 0x0000, which paints nothing.
    """
    if rows is None or not rows or key is None:
        return []

    exempt = set(transparent or ())
    rgb = fourbpp.clut_rgb(rows[0])
    found = []

    for index in range(len(rgb)):
        if index in exempt:
            continue

        colour = rgb[index]

        if chroma is not None:
            match = (abs((colour[0] - colour[1]) - (key[0] - key[1])) <= chroma
                     and abs((colour[2] - colour[1]) - (key[2] - key[1]))
                     <= chroma)
        else:
            match = max(abs(colour[c] - key[c])
                        for c in range(3)) <= max(tolerance, 1)

        if match:
            found.append(index)

    return found


def key_mask(pixels, key, tolerance=0, chroma=None):
    """Which texels are the backdrop.

    `tolerance` is a per-channel distance from `key`. `chroma` instead compares
    the COLOUR CAST - (r - g, b - g) - which is what separates a flat magenta
    backdrop from the grey art on top of it, anti-aliased edge included. That
    matters because the two are close in one channel and nothing alike in the
    others: a grey tyre is 160 away from magenta in green, so a per-channel
    tolerance wide enough to swallow the blend ring also starts eating the tyre,
    which is why 96 keys the ring only partly and leaves stray magenta texels
    behind.
    """
    if chroma is not None:
        kr, kb = key[0] - key[1], key[2] - key[1]

        return [abs((p[0] - p[1]) - kr) <= chroma and abs((p[2] - p[1]) - kb) <= chroma
                for p in pixels]

    return [max(abs(p[0] - key[0]), abs(p[1] - key[1]), abs(p[2] - key[2]))
            <= tolerance for p in pixels]


def protect_mask(pixels, colours, tolerance=0):
    """Which texels are within `tolerance` of ANY protected colour."""
    out = [False] * len(pixels)

    for colour in colours:
        for i, p in enumerate(pixels):
            if out[i]:
                continue

            if (abs(p[0] - colour[0]) <= tolerance
                    and abs(p[1] - colour[1]) <= tolerance
                    and abs(p[2] - colour[2]) <= tolerance):
                out[i] = True

    return out


def _palette_with_protected(colours, pixels, protect, tolerance, key_index=None):
    """Put every protected colour into the 16, evicting the least-used rest.

    The quantiser works on the whole page and folds near-identical tones
    together, which is how a headlight grey becomes another body grey. Naming a
    colour here makes it survive: it takes the slot of the colour the fewest
    texels use (and never a slot that already holds a protected colour).
    """
    colours = list(colours)
    counts = {}

    for p in pixels:
        counts[tuple(p)] = counts.get(tuple(p), 0) + 1

    present = {tuple(c) for c in colours}

    for want in protect:
        want = tuple(want)

        if want in present:
            continue

        best = None

        for i, colour in enumerate(colours):
            if i == key_index:
                continue

            if any(abs(colour[c] - want[c]) <= tolerance for c in range(3)):
                continue

            weight = counts.get(tuple(colour), 0)

            if best is None or weight < best[0]:
                best = (weight, i)

        if best is None:
            continue

        present.discard(tuple(colours[best[1]]))
        colours[best[1]] = want
        present.add(want)

    return colours


def crush_report(pixels, indices, colours, background=None):
    """Per-colour damage: how far each source colour moved, and where it went.

    "The bake worked" and "the greys survived" are different claims. This is the
    measurement behind the second one: each distinct source colour, how many
    texels use it, the page colour it was given, and the worst channel error.
    """
    q = [tuple(c) for c in colours]
    per_colour = {}

    for i, p in enumerate(pixels):
        if background is not None and indices[i] == background["index"]:
            continue

        key = tuple(p)
        got = q[indices[i]]
        err = max(abs(key[c] - got[c]) for c in range(3))
        entry = per_colour.get(key)

        if entry is None:
            per_colour[key] = {"texels": 1, "colour": got, "error": err}
        else:
            entry["texels"] += 1
            entry["error"] = max(entry["error"], err)

    rows = [dict(source=k, **v) for k, v in per_colour.items()]
    rows.sort(key=lambda r: (-r["error"], -r["texels"]))

    return rows


def bake_page(pixels, npal_keep=1, key=None, tolerance=0, mask=None,
              palette=None, protect=None, protect_tolerance=0, chroma=None):
    """RGB texels -> (page blob, colours, indices, error, background).

    `key` (an (r, g, b)) - or an explicit per-texel `mask` - marks the art's
    backdrop, and those texels are made TRANSPARENT: they are all forced onto one
    palette index and that entry is written 0x0000 in every CLUT row, which is
    the value the engine's draws read as transparent. A backdrop left alone bakes
    as an opaque square, which is what a pink/magenta sky behind a wheel sprite
    turns into.

    `palette` (16 (r, g, b)) states the page's colours outright - the texels are
    then re-indexed to them and the quantiser never runs. `protect` (any number
    of colours) keeps those colours in an otherwise automatic palette.

    `background` is None when nothing was keyed, else {index, keyed, stray,
    colour}: how many texels went transparent, and how many texels that were NOT
    keyed share the transparent index (they vanish too - a stray here means the
    backdrop colour was too close to the art).
    """
    if palette is not None:
        placed = [(int(c[0]), int(c[1]), int(c[2])) for c in palette]

        if len(placed) != fourbpp.CLUT_ROW_ENTRIES:
            raise SystemExit("a fixed palette is %d colours, not %d"
                             % (fourbpp.CLUT_ROW_ENTRIES, len(placed)))

        colours = placed
        indices = bytearray(fourbpp.reindex_to_clut(pixels, colours))
    else:
        colours, indices = fourbpp.paletteize(pixels, fourbpp.PAGE_W,
                                              fourbpp.PAGE_H)
        indices = bytearray(indices)

        if protect:
            colours = _palette_with_protected(colours, pixels, protect,
                                              protect_tolerance, None)
            indices = bytearray(fourbpp.reindex_to_clut(pixels, colours))

    if mask is None and key is not None:
        mask = key_mask(pixels, key, tolerance, chroma)

    background = None
    folded = []

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

        if chroma is not None:
            matched = (abs((cleared[0] - cleared[1]) - (key[0] - key[1])) <= chroma
                       and abs((cleared[2] - cleared[1]) - (key[2] - key[1]))
                       <= chroma)
        else:
            # the palette is in 5-bit steps, so compare the key as the engine shows it
            key5 = None if key is None else fourbpp.snap555(key)
            matched = key is None or min(
                max(abs(cleared[c] - k[c]) for c in range(3))
                for k in (key, key5)) <= max(tolerance, 0)

        if not matched and key is not None:
            raise SystemExit(
                "the backdrop #%02x%02x%02x is not in this page's 16 colours "
                "(its nearest is index %d, #%02x%02x%02x). Widen "
                "--transparent-tolerance, or give the sprite a page of its own."
                % (key[0], key[1], key[2], at, cleared[0], cleared[1], cleared[2]))

        # Fold every OTHER palette entry that is still the backdrop's colour
        # onto the transparent one. An anti-aliased blend can quantise to its own
        # near-magenta entry, and those few texels are exactly the pink speckle
        # that survives keying the flat backdrop: if the entry is the backdrop's
        # cast it can never be drawn, so nothing is left to sample.
        reference = key if key is not None else cleared

        if chroma is not None:
            ref_r, ref_b = reference[0] - reference[1], reference[2] - reference[1]

            def same_cast(colour):
                return (abs((colour[0] - colour[1]) - ref_r) <= chroma
                        and abs((colour[2] - colour[1]) - ref_b) <= chroma)
        else:
            def same_cast(colour):
                return max(abs(colour[c] - reference[c])
                           for c in range(3)) <= max(tolerance, 1)

        for i in range(len(colours)):
            if i == at or not same_cast(tuple(colours[i])):
                continue

            colours[i] = cleared
            folded.append(i)

            for k, index in enumerate(indices):
                if index == i:
                    indices[k] = at

        for i, keyed_texel in enumerate(mask):
            if keyed_texel:
                indices[i] = at

        background = {"index": at,
                      "folded": folded,
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
        import math

        from PIL import Image

        png = os.path.join(pages_dir, "PAGE_%d.png" % setid)
        pal = fourbpp.clut_rgb(fourbpp.rgb_to_clut(colours[:16]))
        rows, _raw = pages1.read_page(blob, 0, len(blob))
        transparent = set()

        # The transparent index is whichever entry the page writes as 0x0000.
        # Its preview pixel must be FULLY TRANSPARENT rather than the colour
        # 0x0000 happens to paint - painting it black is what made a preview
        # look like art, and re-baking one re-introduced the backdrop colour.
        for index in range(16):
            word = struct.unpack_from("<H", rows[0], index * 2)[0] if rows else 1

            if word == 0:
                transparent.add(index)

        pix = bytearray()

        for i in indices:
            r, g, b = pal[i]
            pix.extend((r, g, b, 0 if i in transparent else 255))

        img = Image.frombytes("RGBA", (fourbpp.PAGE_W, fourbpp.PAGE_H), bytes(pix))
        img.save(png)

    return path


def read_page(path):
    """(blob, colours, indices) for a page already on disk - for verification."""
    with open(path, "rb") as f:
        blob = f.read()

    rows, raw = pages1.read_page(blob, 0, len(blob))
    indices = fourbpp.decode_indices(raw)

    return blob, fourbpp.clut_rgb(rows[0]) if rows else [], indices


# carTpages[city][8] (texture.c). A set past this draws the block's base row —
# the first page's palette. A later engine may have more slots; bank.json
# `clut_rows` overrides the count the diagnostic uses. It does not invent rows
# the running engine does not have.
CIV_CLUT_ROWS = 8
MAX_TEX_IDS = 32


def civ_row_map(sets, budget=CIV_CLUT_ROWS):
    """Texture sets in ascending order -> the civ_clut row the engine binds.

    `CarPalIndexInCity` walks `carTpages[city][0..budget)`. A set with no slot
    answers -1 and the draw falls back to row 0, so those panels wear the first
    page's colours. The map says so; it does not renumber a bank that already
    loads.
    """
    ordered = sorted({int(s) for s in sets})
    out = []

    for i, page_set in enumerate(ordered):
        out.append({"set": page_set,
                    "row": i if i < budget else 0,
                    "overflow": i >= budget})

    return out


def clut_dump_text(records, budget=CIV_CLUT_ROWS):
    """One block of text: set -> civ row, then every CLUT row as hex swatches.

    `records` are palette rows `{tpageindex, texnum, palette, clut}` with `clut`
    the 32 raw bytes (or already a list of rgb triples).
    """
    sets = [r.get("tpageindex", r.get("set")) for r in records]
    lines = ["civ_clut budget %d" % budget]

    for entry in civ_row_map(sets, budget):
        note = "  OVERFLOW -> row 0 (first page's palette)" if entry["overflow"] else ""
        lines.append("set %d -> civ row %d%s" % (entry["set"], entry["row"], note))

    for record in records:
        clut = record.get("clut")
        rgb = clut if clut and isinstance(clut[0], (tuple, list)) else fourbpp.clut_rgb(clut)
        swatch = " ".join("%02x%02x%02x" % tuple(c) for c in rgb)
        lines.append("set %s tex %s pal %s  %s" % (
            record.get("tpageindex", record.get("set")),
            record.get("texnum", 0), record.get("palette", 0), swatch))

    return "\n".join(lines) + "\n"


def _clip_rect(rect, width, height):
    x0, y0, x1, y1 = rect
    return (max(0, int(x0)), max(0, int(y0)),
            min(width, int(x1)), min(height, int(y1)))


def _bbox_uv(uvs):
    us = [p[0] for p in uvs]
    vs = [p[1] for p in uvs]
    return (min(us), min(vs), max(us) + 1, max(vs) + 1)


def _colours_in(pixels, width, rect, mask=None):
    """Distinct snapped colours in `rect`, insertion order.

    `mask` limits the count to texels a poly actually covers, so a leftover
    sprite inside the UV bbox does not join the body's row.
    """
    x0, y0, x1, y1 = _clip_rect(rect, width, len(pixels) // width)
    seen = []
    have = set()

    for y in range(y0, y1):
        row = y * width

        for x in range(x0, x1):
            if mask is not None and not mask[row + x]:
                continue

            colour = fourbpp.snap555(pixels[row + x])

            if colour not in have:
                have.add(colour)
                seen.append(colour)

    return seen


def _pad16(colours):
    """16 entries. Unused slots copy a real colour, never transparent black."""
    colours = [tuple(c) for c in colours]

    if not colours:
        return [(8, 8, 8)] * fourbpp.CLUT_ROW_ENTRIES

    filler = next((c for c in colours if c != (0, 0, 0)), (8, 8, 8))
    colours = colours[:fourbpp.CLUT_ROW_ENTRIES]
    return colours + [filler] * (fourbpp.CLUT_ROW_ENTRIES - len(colours))


def count_snapped(pixels, width=None):
    """How many RGB555 colours the engine will see in this image."""
    width = width or fourbpp.PAGE_W

    if width > 0 and len(pixels) % width == 0:
        return len(_colours_in(pixels, width,
                               (0, 0, width, len(pixels) // width)))

    return len({fourbpp.snap555(p) for p in pixels})


# A leftover retail sprite smaller than this is treated as a fringe, not a
# texture that needs its own CLUT row. 4x4 is the smallest sprite worth a row.
MIN_LEFTOVER_TEXELS = 16


def _cover_poly(mask, width, height, pts):
    """Mark the texels a poly's UVs actually cover.

    An axis-aligned quad fills its bbox (that is the rect the old colour count
    used). Anything else is rasterised, so a gutter sprite sitting inside the
    bbox but outside the triangles is not part of the poly.
    """
    if len(pts) < 3:
        return

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    if len(pts) == 4 and len(set(int(v) for v in xs)) <= 2 and len(
            set(int(v) for v in ys)) <= 2:
        rect = _clip_rect((min(xs), min(ys), max(xs) + 1, max(ys) + 1),
                          width, height)

        for y in range(rect[1], rect[3]):
            row = y * width

            for x in range(rect[0], rect[2]):
                mask[row + x] = 1

        return

    for i in range(1, len(pts) - 1):
        _fill_tri(mask, width, height, pts[0], pts[i], pts[i + 1])


def _fill_tri(mask, width, height, a, b, c):
    xs = (a[0], b[0], c[0])
    ys = (a[1], b[1], c[1])
    x0 = max(0, int(min(xs)))
    x1 = min(width - 1, int(max(xs)))
    y0 = max(0, int(min(ys)))
    y1 = min(height - 1, int(max(ys)))

    if x0 > x1 or y0 > y1:
        return

    ax, ay = a
    bx, by = b
    cx, cy = c
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)

    if area == 0:
        return

    for y in range(y0, y1 + 1):
        fy = y + 0.5
        row = y * width

        for x in range(x0, x1 + 1):
            fx = x + 0.5
            w0 = (bx - ax) * (fy - ay) - (fx - ax) * (by - ay)
            w1 = (cx - bx) * (fy - by) - (fx - bx) * (cy - by)
            w2 = (ax - cx) * (fy - cy) - (fx - cx) * (ay - cy)

            if ((w0 >= 0 and w1 >= 0 and w2 >= 0)
                    or (w0 <= 0 and w1 <= 0 and w2 <= 0)):
                mask[row + x] = 1


def page_coverage(width, height, islands):
    """1 where any poly UV covers the texel, else 0."""
    mask = bytearray(width * height)

    for lists in (islands or {}).values():
        for uvs in lists or []:
            _cover_poly(mask, width, height, uvs)

    return mask


def leftover_blobs(pixels, width, height, mask, backdrop):
    """Sprites on the page that no poly covers, and that are not the backdrop.

    A retail car page (the Buick's PAGE_1 is the case this is for) still holds
    the other sprites that shared that page. Folding them into the body's
    16-entry row is what crushes the body. Each blob is the texels of one
    connected sprite plus the snapped colours it uses.
    """
    seen = bytearray(width * height)
    blobs = []
    n = width * height

    for start in range(n):
        if seen[start] or mask[start]:
            continue

        if fourbpp.snap555(pixels[start]) == backdrop:
            seen[start] = 1
            continue

        stack = [start]
        seen[start] = 1
        texels = []
        cols = []
        have = set()

        while stack:
            i = stack.pop()
            colour = fourbpp.snap555(pixels[i])
            texels.append(i)

            if colour not in have:
                have.add(colour)
                cols.append(colour)

            x = i % width
            y = i // width

            for jx, jy in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if jx < 0 or jy < 0 or jx >= width or jy >= height:
                    continue

                j = jy * width + jx

                if seen[j] or mask[j]:
                    continue

                if fourbpp.snap555(pixels[j]) == backdrop:
                    seen[j] = 1
                    continue

                seen[j] = 1
                stack.append(j)

        if len(texels) >= MIN_LEFTOVER_TEXELS and cols:
            blobs.append({"texels": texels, "cols": cols})

    return blobs


def _cluster_uvs(pixels, width, height, uv_lists, split, mask=None):
    """Group UV islands so each group fits in one 16-entry row.

    An island whose own bbox already exceeds 16 colours cannot be cut (one poly
    has one texture id) and is marked for quantization. `split` false puts
    every island in that one quantized group — the old whole-page behaviour.
    """
    if not split:
        rects = [_bbox_uv(uvs) for uvs in uv_lists if uvs]
        return [{"rects": rects or [(0, 0, width, height)], "quantize": True,
                 "cols": []}]

    groups = []

    for uvs in uv_lists:
        if not uvs:
            continue

        rect = _clip_rect(_bbox_uv(uvs), width, height)

        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue

        cols = _colours_in(pixels, width, rect, mask)

        if not cols:
            continue

        if len(cols) > fourbpp.CLUT_ROW_ENTRIES:
            groups.append({"rects": [rect], "quantize": True, "cols": cols})
            continue

        placed = False

        for group in groups:
            if group["quantize"]:
                continue

            if len(set(group["cols"]) | set(cols)) <= fourbpp.CLUT_ROW_ENTRIES:
                group["cols"] = list(dict.fromkeys(list(group["cols"]) + cols))
                group["rects"].append(rect)
                placed = True
                break

        if not placed:
            groups.append({"rects": [rect], "quantize": False, "cols": list(cols)})

    return groups or [{"rects": [(0, 0, width, height)], "quantize": True, "cols": []}]


def should_split(pixels, width, height, islands, split="auto", max_error=None):
    """True when one 16-entry row would crush this page.

    A page whose snapped colours already fit is never split: a 256-colour PNG
    that only uses 16 RGB555 shades stays one row, byte for byte the old bake.
    An explicit `split` of "off" keeps that bake even when the colours do not
    fit. `max_error` (max channel, 0..255) accepts a single-row quantize whose
    worst texel is no further than that.
    """
    if split in ("off", False, None, "false", 0):
        return False

    if count_snapped(pixels) <= fourbpp.CLUT_ROW_ENTRIES:
        return False

    if max_error is not None:
        colours, indices = fourbpp.paletteize(pixels, width, height)
        worst = 0

        for pixel, index in zip(pixels, indices):
            got = colours[index]
            src = fourbpp.snap555(pixel)
            worst = max(worst, max(abs(src[c] - got[c]) for c in range(3)))

            if worst > max_error:
                break

        if worst <= max_error:
            return False

    mask = page_coverage(width, height, islands)

    if any(mask) and pixels:
        backdrop = fourbpp.snap555(pixels[0])
        blobs = leftover_blobs(pixels, width, height, mask, backdrop)

        if blobs:
            covered = set()

            for i, bit in enumerate(mask):
                if bit:
                    covered.add(fourbpp.snap555(pixels[i]))

            extra = {c for blob in blobs for c in blob["cols"]} - covered

            # The body's polys never sample these texels, but a whole-page
            # quantise still spends CLUT slots on them. That is the crush.
            if extra and len(covered | extra) > fourbpp.CLUT_ROW_ENTRIES:
                return True

    groups = 0

    for uv_lists in (islands or {}).values():
        groups += len(_cluster_uvs(pixels, width, height, uv_lists, True, mask))

    # One island that itself does not fit is the legacy whole-page quantize:
    # there is no second poly to give a row to. Two ids, or two polys whose
    # colours separate, each get a row.
    return groups > 1 or len(islands or {}) > 1


def _scatter_exact(indices, pixels, width, rects, colours, mask=None):
    index = {tuple(c): i for i, c in enumerate(colours)}

    for rect in rects:
        x0, y0, x1, y1 = _clip_rect(rect, width, len(pixels) // width)

        for y in range(y0, y1):
            row = y * width

            for x in range(x0, x1):
                if mask is not None and not mask[row + x]:
                    continue

                indices[row + x] = index[fourbpp.snap555(pixels[row + x])]


def _scatter_quantize(indices, pixels, width, height, rects, mask=None):
    x0 = min(r[0] for r in rects)
    y0 = min(r[1] for r in rects)
    x1 = max(r[2] for r in rects)
    y1 = max(r[3] for r in rects)
    x0, y0, x1, y1 = _clip_rect((x0, y0, x1, y1), width, height)
    rw, rh = x1 - x0, y1 - y0

    if rw < 1 or rh < 1:
        return _pad16([(8, 8, 8)])

    fill = None

    if mask is not None:
        for y in range(rh):
            for x in range(rw):
                i = (y0 + y) * width + (x0 + x)

                if mask[i]:
                    fill = pixels[i]
                    break

            if fill is not None:
                break

    if fill is None:
        fill = pixels[y0 * width + x0]

    sub = []

    for y in range(rh):
        for x in range(rw):
            i = (y0 + y) * width + (x0 + x)
            sub.append(pixels[i] if (mask is None or mask[i]) else fill)

    colours, local = fourbpp.paletteize(sub, rw, rh)

    for rect in rects:
        rx0, ry0, rx1, ry1 = _clip_rect(rect, width, height)

        for y in range(ry0, ry1):
            for x in range(rx0, rx1):
                if mask is not None and not mask[y * width + x]:
                    continue

                if x0 <= x < x1 and y0 <= y < y1:
                    indices[y * width + x] = local[(y - y0) * rw + (x - x0)]

    return colours


def _paginate_leftovers(indices, pixels, width, height, blobs, rows, next_id):
    """Give uncovered sprites their own CLUT rows. Returns the new pieces.

    Colours already on a body row stay off that row: the point is that a
    leftover retail sprite does not spend one of the body's 16 entries.
    """
    pieces = []
    groups = []

    for blob in blobs:
        cols = blob["cols"]

        if len(cols) > fourbpp.CLUT_ROW_ENTRIES:
            groups.append({"quantize": True, "blobs": [blob], "cols": cols})
            continue

        placed = False

        for group in groups:
            if group["quantize"]:
                continue

            if len(set(group["cols"]) | set(cols)) <= fourbpp.CLUT_ROW_ENTRIES:
                group["cols"] = list(dict.fromkeys(list(group["cols"]) + cols))
                group["blobs"].append(blob)
                placed = True
                break

        if not placed:
            groups.append({"quantize": False, "blobs": [blob],
                           "cols": list(cols)})

    for group in groups:
        if next_id >= MAX_TEX_IDS:
            break

        tex_id = next_id
        next_id += 1
        texels = [t for blob in group["blobs"] for t in blob["texels"]]

        if group["quantize"]:
            owned = [pixels[t] for t in texels]
            # ceil(sqrt), not floor: a square grid one texel short of the
            # sprite is a negative pad, and paletteize rejects the grid
            side = max(1, math.isqrt(len(owned) - 1) + 1)
            pad = side * side - len(owned)
            grid = owned + [owned[0]] * pad
            colours, local_idx = fourbpp.paletteize(grid, side, side)

            for n, t in enumerate(texels):
                indices[t] = local_idx[n]
        else:
            colours = _pad16(group["cols"])
            index = {tuple(c): i for i, c in enumerate(colours)}

            for t in texels:
                indices[t] = index[fourbpp.snap555(pixels[t])]

        rows[tex_id] = colours
        xs = [t % width for t in texels]
        ys = [t // width for t in texels]
        pieces.append({"id": tex_id,
                       "rect": [min(xs), min(ys), max(xs) + 1, max(ys) + 1],
                       "src": None})

    return pieces


def plan_islands(pixels, width, height, islands, split=True, extra_islands=None):
    """(indices, {id: 16 colours}, pieces) for a page that does not fit one row.

    `islands` is `{texture id: [uv list, ...]}` with uvs in page texels.
    The first group of each source id keeps that id (so set65_id13 stays id
    13). Further groups take the next free id, up to 32. Pieces carry the rect
    a poly's UV centroid is matched against.

    `extra_islands` are rects that must own a row of their own: a composited
    wheel sprite is not a body UV island, and folding it into the leftover
    piece is what let a whole-page leftover palette paint the tyre. Each entry
    is {"uv": [...], "kind": "wheel", "variant": "clean"}.
    """
    islands = islands or {0: [[(0, 0), (width - 1, 0),
                               (width - 1, height - 1), (0, height - 1)]]}
    indices = bytearray(width * height)
    rows = {}
    pieces = []
    next_id = (max(islands) + 1) if islands else 1
    mask = page_coverage(width, height, islands) if split else None

    if mask is not None and extra_islands:
        for extra in extra_islands:
            _cover_poly(mask, width, height, extra["uv"])

    for src_id in sorted(islands):
        groups = _cluster_uvs(pixels, width, height, islands[src_id], split,
                              mask)

        for number, group in enumerate(groups):
            if number == 0:
                tex_id = src_id
            elif next_id < MAX_TEX_IDS:
                tex_id = next_id
                next_id += 1
            else:
                tex_id = src_id

            rects = group["rects"]

            if group["quantize"] or len(group["cols"]) > fourbpp.CLUT_ROW_ENTRIES:
                colours = _scatter_quantize(indices, pixels, width, height,
                                            rects, mask)
            else:
                colours = _pad16(group["cols"])
                _scatter_exact(indices, pixels, width, rects, colours, mask)

            rows[tex_id] = colours

            for rect in rects:
                pieces.append({"id": tex_id, "rect": [int(v) for v in rect],
                               "src": src_id})

    # A wheel rect is its own island: it must not share a row with body art or
    # with the whole-page leftover piece. Give each one a fresh id and spread
    # its own colours over that row.
    for extra in extra_islands or ():
        rect = _clip_rect(_bbox_uv(extra["uv"]), width, height)

        if rect[2] <= rect[0] or rect[3] <= rect[1]:
            continue

        cols = _colours_in(pixels, width, rect)

        if not cols:
            continue

        if next_id >= MAX_TEX_IDS:
            raise SystemExit(
                "page: no free texture id left for the %s wheel island at %s"
                % (extra.get("variant") or "wheel", list(rect)))

        tex_id = next_id
        next_id += 1

        if len(cols) > fourbpp.CLUT_ROW_ENTRIES:
            colours = _scatter_quantize(indices, pixels, width, height, [rect])
        else:
            colours = _pad16(cols)
            _scatter_exact(indices, pixels, width, [rect], colours)

        rows[tex_id] = colours
        pieces.append({"id": tex_id, "rect": [int(v) for v in rect],
                       "src": None, "kind": extra.get("kind", "wheel"),
                       "variant": extra.get("variant")})

    if split and any(mask or []):
        backdrop = fourbpp.snap555(pixels[0]) if pixels else (0, 0, 0)
        blobs = leftover_blobs(pixels, width, height, mask, backdrop)
        pieces.extend(_paginate_leftovers(
            indices, pixels, width, height, blobs, rows, next_id))

    return indices, rows, pieces


def _key_matches(colour, key, tolerance, chroma):
    if key is None:
        return True

    if chroma is not None:
        return (abs((colour[0] - colour[1]) - (key[0] - key[1])) <= chroma
                and abs((colour[2] - colour[1]) - (key[2] - key[1])) <= chroma)

    snapped = fourbpp.snap555(key)
    return min(max(abs(colour[c] - candidate[c]) for c in range(3))
               for candidate in (key, snapped)) <= max(tolerance or 0, 0)


def pack_indexed(indices, rows_by_id, mask=None, key=None, tolerance=0,
                 chroma=None, pieces=None):
    """Page blob from per-id CLUT rows. Gaps copy the lowest id's colours.

    A keyed backdrop is cleared (0x0000) only on the index its texels actually
    took, and only when that entry still is the key colour — a near-black shade
    is not turned into the transparent marker. An explicit mask (a composited
    wheel sprite) does not need a key: the mask itself says those texels are
    transparent, so the index they took is cleared in that id's row.
    """
    if not rows_by_id:
        rows_by_id = {0: [(8, 8, 8)] * fourbpp.CLUT_ROW_ENTRIES}

    filler = list(rows_by_id[min(rows_by_id)])
    top = min(MAX_TEX_IDS - 1, max(rows_by_id))
    ordered = []

    for tex_id in range(top + 1):
        colours = list(rows_by_id.get(tex_id, filler))
        ordered.append(_pad16(colours))

    cleared = {}

    if mask is not None and any(mask):
        owned = {}

        for piece in pieces or []:
            owned.setdefault(piece["id"], []).append(piece["rect"])

        for tex_id, rects in owned.items():
            if tex_id >= len(ordered):
                continue

            used = set()

            for rect in rects:
                x0, y0, x1, y1 = _clip_rect(rect, fourbpp.PAGE_W, fourbpp.PAGE_H)

                for y in range(y0, y1):
                    row = y * fourbpp.PAGE_W

                    for x in range(x0, x1):
                        if mask[row + x]:
                            used.add(indices[row + x])

            for at in used:
                colour = ordered[tex_id][at]

                if key is None or _key_matches(colour, key, tolerance, chroma):
                    cleared.setdefault(tex_id, set()).add(at)

    raw = fourbpp.encode_indices(bytes(indices))
    stream = fourbpp.encode_literal(raw[::-1])
    body = bytearray()

    for tex_id, colours in enumerate(ordered):
        row = bytearray(fourbpp.rgb_to_clut(colours))

        for index in cleared.get(tex_id, ()):
            struct.pack_into("<H", row, index * 2, 0)

        body += row

    blob = struct.pack("<i", len(ordered)) + bytes(body) + stream
    return blob, ordered


def hex_colour(text):
    """'#rrggbb' / 'rrggbb' -> (r, g, b)."""
    value = str(text).strip().lower()
    digits = value[1:] if value.startswith("#") else value

    if len(digits) != 6:
        raise SystemExit("%r is not a hex colour (rrggbb)" % (text,))

    try:
        return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise SystemExit("%r is not a hex colour (rrggbb)" % (text,))


def load_pixels(path):
    """(width, height, RGB texels, alpha or None) for an image of ANY size.

    `_load_rgb` insists on a full 256x256 page because scaling one silently
    chooses a filter. A sprite is a different thing: a 60x60 wheel is meant to be
    60x60, and it gets composited into a page rather than being one.
    """
    from PIL import Image

    with Image.open(path) as img:
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        alpha = None

        if has_alpha:
            rgba = img.convert("RGBA")
            src = list(rgba.getdata())
            pixels = [(p[0], p[1], p[2]) for p in src]
            alpha = [p[3] for p in src]
        else:
            pixels = list(img.convert("RGB").getdata())

        return img.size[0], img.size[1], pixels, alpha


def sprite_mask(texels, alpha):
    """Which sprite texels are see-through: alpha, else the top-left colour.

    A wheel sprite is authored one of two ways and both have to work: an image
    with a real alpha channel, or a flat backdrop colour in the corner. The
    corner is the same convention `--transparent auto` uses for a page, so a
    sprite behaves like a little page.
    """
    if alpha is not None and any(a < 128 for a in alpha):
        return [a < 128 for a in alpha]

    if not texels:
        return []

    key = texels[0]
    return [max(abs(t[c] - key[c]) for c in range(3)) <= 8 for t in texels]


def wheel_sprite_mask(texels, alpha, width, height):
    """The sprite's backdrop only, not same-coloured interior art.

    A black tyre on a black backdrop is the STP case: `sprite_mask` keys every
    near-black texel, so the whole tyre becomes transparent. This flood-fills
    the corner colour inward from the sprite border instead, so only the
    border-connected backdrop is masked and a black tyre that does not touch
    the edge stays opaque.
    """
    if alpha is not None and any(a < 128 for a in alpha):
        return [a < 128 for a in alpha]

    if not texels or width <= 0 or height <= 0:
        return []

    key = texels[0]
    mask = [False] * (width * height)
    seen = [False] * (width * height)
    stack = []

    def matches(index):
        return max(abs(texels[index][c] - key[c]) for c in range(3)) <= 8

    def push(x, y):
        index = y * width + x

        if not seen[index] and matches(index):
            seen[index] = True
            mask[index] = True
            stack.append(index)

    for x in range(width):
        push(x, 0)
        push(x, height - 1)

    for y in range(1, height - 1):
        push(0, y)
        push(width - 1, y)

    while stack:
        index = stack.pop()
        x = index % width
        y = index // width

        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < width and 0 <= ny < height:
                push(nx, ny)

    return mask


def composite(pixels, width, height, sprite, sprite_w, sprite_h, island,
              sprite_transparent, backdrop):
    """Write a sprite into a page island; return the texels that are backdrop.

    The island's rect is filled completely: sprite inside it, and `backdrop`
    everywhere else. That is the point - a page that already carried *anything*
    at the islands rect (a stale sprite, the donor's pink square) is replaced
    rather than drawn over, so the declared sprite is the only thing that can
    end up in the wheel.

    Returns the set of page texel indices that must be masked transparent (the
    sprite's own see-through texels, plus the fill outside the sprite).
    """
    x0, y0, x1, y1 = [int(round(v)) for v in island]
    masked = set()

    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            index = y * width + x
            sx = x - x0
            sy = y - y0

            if sx < sprite_w and sy < sprite_h:
                src = sy * sprite_w + sx

                if not sprite_transparent[src]:
                    pixels[index] = sprite[src]
                    continue

            pixels[index] = backdrop
            masked.add(index)

    return masked


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
    ap.add_argument("--transparent-chroma", type=int, default=None, metavar="N",
                    help="key by COLOUR CAST instead of per-channel distance: a "
                         "texel whose (r-g, b-g) is within N of the backdrop's. "
                         "Catches a soft blended edge without eating grey art")
    ap.add_argument("--protect", metavar="RRGGBB[,RRGGBB...]", default=None,
                    help="colours the page's 16 MUST keep (headlight greys), "
                         "evicting the least-used colour instead")
    ap.add_argument("--protect-tolerance", type=int, default=8, metavar="N",
                    help="per-channel slack when matching a protected colour "
                         "(default 8)")
    ap.add_argument("--palette", metavar="RRGGBB[,...]", default=None,
                    help="a fixed 16-colour palette; nothing is quantised")
    ap.add_argument("--report", type=int, default=0, metavar="N",
                    help="print the N worst-crushed source colours")
    ap.add_argument("--no-preview", action="store_true",
                    help="skip pages/PAGE_<set>.png")
    args = ap.parse_args(argv)

    pixels, alpha = _load_rgb(args.image)
    key, mask = _parse_key(args.transparent, pixels, alpha)

    def colours_of(text):
        return [hex_colour(v) for v in text.replace(" ", "").split(",") if v]

    protect = colours_of(args.protect) if args.protect else []
    palette = colours_of(args.palette) if args.palette else None

    blob, colours, indices, error, background = bake_page(
        pixels, key=key, tolerance=args.transparent_tolerance, mask=mask,
        palette=palette, protect=protect,
        protect_tolerance=args.protect_tolerance,
        chroma=args.transparent_chroma)

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

    if args.report:
        rows = crush_report(pixels, indices, colours, background)
        print("  worst-crushed source colours:")

        for row in rows[:args.report]:
            src = row["source"]
            got = row["colour"]
            print("    #%02x%02x%02x -> #%02x%02x%02x  off by %3d  (%d texel(s))"
                  % (src[0], src[1], src[2], got[0], got[1], got[2],
                     row["error"], row["texels"]))

    print("  other pages untouched%s" % (" (previous page was %+d bytes)" % old
                                          if old else ""))

    return 0


if __name__ == "__main__":
    sys.exit(main())
