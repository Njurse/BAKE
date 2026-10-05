#!/usr/bin/env python3
"""textured.py - textured 3/4 renders of the Driver 1 pool.

The offline half of the texture milestone, with NO engine involvement: it takes
the level's own texture pages (exported by OpenDriver2Tools' DriverLevelTool as
PAGE_<n>.tga, which are CLUT-applied) and affine-maps each car poly's UVs onto
them.

The mapping was measured, not guessed:
  - a car poly's texture_set indexes the page directly (FRISCO model 0's polys
    carry set=68, and PAGE_68.ini lists exactly the car textures - COP9/COP3/
    SIDE1/TOP1/WIN/POLICE - with the model's UVs landing inside a real rect);
  - UV_INFO is two UNSIGNED bytes in 0..255 into the 256x256 page.

Usage:
    python textured.py --pages DIR/FRISCO_textures --pool out/pool.json \
        --levels FRISCO,MIAMI [--first N] [--cell 160]
"""

import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preview  # noqa: E402


def load_tga_rgb(path):
    """Return (w, h, rgb_bytes_top_down) from an uncompressed 24/32-bit TGA."""
    d = open(path, "rb").read()
    w, h = struct.unpack_from("<HH", d, 12)
    bpp = d[16]
    desc = d[17]
    off = 18 + d[0]
    ch = bpp // 8
    flip = not (desc & 0x20)
    out = bytearray(w * h * 3)
    for y in range(h):
        sy = (h - 1 - y) if flip else y
        base = off + sy * w * ch
        o = y * w * 3
        for x in range(w):
            b = d[base + x * ch]
            g = d[base + x * ch + 1]
            r = d[base + x * ch + 2]
            out[o + x * 3] = r
            out[o + x * 3 + 1] = g
            out[o + x * 3 + 2] = b
    return w, h, out


def _line(pix, w, h, x0, y0, x1, y1, rgb):
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        if 0 <= x0 < w and 0 <= y0 < h:
            o = (y0 * w + x0) * 3
            pix[o], pix[o + 1], pix[o + 2] = rgb
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _fill_tex(pix, w, h, p, uv, tex, tw, th, pal=None):
    """Affine-map a triangle's uvs onto a page.

    `tex` is the page's RGB bytes when `pal` is None (a tool TGA, already
    CLUT-applied), or a 256x256 INDEX image when `pal` is given - which is the
    baked path, because there the page is 4bpp and the palette is one of the
    entry's own CLUT rows, chosen per poly by its texture_id.
    """
    x0 = max(0, int(min(v[0] for v in p)))
    x1 = min(w - 1, int(max(v[0] for v in p)) + 1)
    y0 = max(0, int(min(v[1] for v in p)))
    y1 = min(h - 1, int(max(v[1] for v in p)) + 1)
    if x0 > x1 or y0 > y1:
        return
    (ax, ay), (bx, by), (cx, cy) = p
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if area == 0:
        return
    for y in range(y0, y1 + 1):
        fy = y + 0.5
        for x in range(x0, x1 + 1):
            fx = x + 0.5
            w0 = ((by - cy) * (fx - cx) + (cx - bx) * (fy - cy)) / area
            w1 = ((cy - ay) * (fx - cx) + (ax - cx) * (fy - cy)) / area
            w2 = 1.0 - w0 - w1
            if w0 < 0 or w1 < 0 or w2 < 0:
                continue
            u = w0 * uv[0][0] + w1 * uv[1][0] + w2 * uv[2][0]
            v = w0 * uv[0][1] + w1 * uv[1][1] + w2 * uv[2][1]
            tu = 0 if u < 0 else (tw - 1 if u >= tw else int(u))
            tv = 0 if v < 0 else (th - 1 if v >= th else int(v))
            o = tv * tw + tu
            d = (y * w + x) * 3
            if pal is not None:
                if o >= len(tex):
                    continue
                pix[d], pix[d + 1], pix[d + 2] = pal[tex[o]]
            else:
                o3 = o * 3
                pix[d] = tex[o3]
                pix[d + 1] = tex[o3 + 1]
                pix[d + 2] = tex[o3 + 2]


def render(items, pages_root, path, cols=6, cell=170, pad=8, baked=None):
    rows = (len(items) + cols - 1) // cols
    w, h = cols * cell, rows * cell
    pix = bytearray(w * h * 3)
    pages = {}
    misses = 0

    for idx, (_label, level, verts, faces) in enumerate(items):
        if not verts:
            continue
        proj = [preview._project(v) for v in verts]
        xs = [q[0] for q in proj]
        ys = [q[1] for q in proj]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
        sc = (cell - 2 * pad) / float(span)
        ox = (idx % cols) * cell + cell / 2.0 - (min(xs) + max(xs)) / 2.0 * sc
        oy = (idx // cols) * cell + cell / 2.0 - (min(ys) + max(ys)) / 2.0 * sc
        scr = [(ox + q[0] * sc, oy + q[1] * sc) for q in proj]

        order = sorted(range(len(faces)),
                       key=lambda fi: -sum(proj[i][2] for i in faces[fi][0]))
        for fi in order:
            entry = faces[fi]
            ind, uv, setid = entry[0], entry[1], entry[2]
            texid = entry[3] if len(entry) > 3 else -1
            if len(ind) != 3 or setid < 0:
                continue                       # only textured triangles here
            page = pages.get((level, setid))
            if page is None:
                if baked is not None:
                    page = baked.get(setid) or False
                else:
                    f = os.path.join(pages_root, "%s_textures" % level,
                                     "PAGE_%d.tga" % setid)
                    page = load_tga_rgb(f) if os.path.exists(f) else False
                pages[(level, setid)] = page
            if page is False:
                misses += 1
                continue
            if baked is not None:
                idx, crows = page
                pal = crows[texid] if 0 <= texid < len(crows) else crows[0]
                _fill_tex(pix, w, h, [scr[ind[0]], scr[ind[1]], scr[ind[2]]],
                          uv, idx, 256, 256, pal=pal)
            else:
                tw, th, teximg = page
                _fill_tex(pix, w, h, [scr[ind[0]], scr[ind[1]], scr[ind[2]]],
                          uv, teximg, tw, th)
        # a thin outline so a miss is visible rather than a hole
        for ind, _uv, _s in ((f[0], f[1], f[2]) for f in faces):
            pts = [scr[i] for i in ind]
            for a in range(len(pts) - 1):
                _line(pix, w, h, int(pts[a][0]), int(pts[a][1]),
                      int(pts[a + 1][0]), int(pts[a + 1][1]), (32, 32, 32))
            _line(pix, w, h, int(pts[-1][0]), int(pts[-1][1]),
                  int(pts[0][0]), int(pts[0][1]), (32, 32, 32))

    preview._png(path, w, h, pix)
    print("  %s  (%dx%d, %d shapes, %d polys with no page)"
          % (path, w, h, len(items), misses))
    return path


def pool_items(pool_path, want_levels, first):
    d = json.load(open(pool_path))
    base = os.path.dirname(pool_path)
    items = []
    for it in d["items"]:
        g = json.load(open(os.path.join(base, it["geom"])))
        level = g["sources"][0][0]
        if want_levels and level not in want_levels:
            continue
        k = g["scale"]
        verts = [(v[0] * k, v[1] * k, v[2] * k) for v in g["vertices"]]
        faces = []
        for p in g["polys"]:
            if p["v"] and p["uv"]:
                faces.append((tuple(p["v"]), tuple(tuple(q) for q in p["uv"]), p["set"]))
            else:
                faces.append((tuple(p["v"]), ((0, 0),) * len(p["v"]), -1))
        items.append(("%s/%d" % (level, g["sources"][0][1]), level, verts, faces))
        if first and len(items) >= first:
            break
    return items


def baked_pages(blob):
    """set -> (256x256 index bytes, [CLUT rows as rgb tuples]) from the blob's
    OWN embedded pages. This is what makes the baked path self-supporting: no
    DriverLevelTool export and no source level is read."""
    import pages1
    d1o, d1s = struct.unpack_from("<2I", blob, 8)[:2]
    segs = {}
    o = d1o + 8
    while o + 8 <= d1o + d1s:
        t, s = struct.unpack_from("<2i", blob, o)
        if t == 0 and s == 0:
            break
        segs[t] = (o + 8, s)
        o += 8 + ((s + 3) & ~3)
    if 34 not in segs:
        return {}
    ti = pages1.parse_texture_info(blob[segs[34][0]:segs[34][0] + segs[34][1]])
    # pageBase, as the engine computes it (models.c:391)
    pb = (d1o // 2048 + d1s // 2048) * 2048
    out = {}
    off = 0
    for new_set, size in list(ti["permlist"]) + list(ti["speclist"]):
        rows, page = pages1.read_page(blob, pb + off, size)
        # the page is 4bpp: unpack it into a 256x256 index image, low nibble first
        idx = bytearray(65536)
        k = 0
        for y in range(256):
            for xb in page[y * 128:(y + 1) * 128]:
                idx[k] = xb & 0xF
                idx[k + 1] = xb >> 4
                k += 2
        out[new_set] = (bytes(idx), [pages1.clut_rgb(r) for r in rows])
        off += (size + 2047) & ~2047
    return out


def baked_items(lev_path):
    """The baked blob's own models as render items: (label, key, verts, faces)
    with faces carrying (indices, uvs, set, texture_id)."""
    import lev1
    import scalemodels as sm

    blob = open(lev_path, "rb").read()
    got = sm.d2_car_lump(blob)
    if not got:
        return []
    body, _size = got
    items = []
    for m in range(sm.MAX_CAR_MODELS):
        clean, _dam, _low = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        if clean == -1:
            continue
        model = lev1.decode_model(blob, body + 164 + clean)
        if not model.vertices:
            continue
        faces = []
        for p in model.polys:
            if len(p.v) != 3 or p.kind not in ("FT3", "GT3"):
                continue
            faces.append((tuple(p.v), tuple(tuple(q) for q in p.uv),
                          p.texture_set, p.texture_id))
        items.append(("model %d" % m, "baked", list(model.vertices), faces))
    return items


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=os.path.join(here, "out", "pool.json"))
    ap.add_argument("--pages-root", default="",
                    help="dir holding <LEVEL>_textures/PAGE_<n>.tga for each level")
    ap.add_argument("--baked", default="",
                    help="render the BAKED blob from its OWN embedded pages "
                         "(no tool export, no source level)")
    ap.add_argument("--levels", default="", help="comma-separated source levels to include")
    ap.add_argument("--first", type=int, default=0, help="limit to the first N shapes")
    ap.add_argument("--cell", type=int, default=170)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    if args.baked:
        blob = open(args.baked, "rb").read()
        baked = baked_pages(blob)
        items = baked_items(args.baked)
        if not items:
            print("the baked blob has no models")
            return 1
        out = args.out or os.path.join(here, "out", "textured_baked.png")
        render(items, "", out, cols=5, cell=args.cell, baked=baked)
        printed = sum(1 for _v in baked)
        print("  (pages read from the blob itself: %d sets)" % printed)
        return 0

    want = set(x for x in args.levels.split(",") if x)
    items = pool_items(args.pool, want, args.first)
    if not items:
        print("no pool items selected")
        return 1
    out = args.out or os.path.join(here, "out", "textured_%s.png"
                                   % ("_".join(sorted(want)) or "all"))
    render(items, args.pages_root, out, cell=args.cell)
    return 0


if __name__ == "__main__":
    sys.exit(main())
