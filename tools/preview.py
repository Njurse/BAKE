#!/usr/bin/env python3
"""preview.py - shaded 3/4 renders of the pool and of the baked engine blob.

A quick visual check of what the pipeline produced. Two outputs:

    out/preview_baked.png   the models inside a baked blob (D1POOL.LEV) - i.e.
                            exactly what the engine would be handed
    out/preview_pool.png    every pool shape, for the overview

Flat-shaded with a painter's-algorithm sort and a stable light, so the shapes
read as cars rather than as noise.

Usage:
    python preview.py [--pool PATH/pool.json] [--baked PATH/D1POOL.LEV]
"""

import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1          # noqa: E402
import scalemodels   # noqa: E402

YAW = math.radians(34.0)
PITCH = math.radians(18.0)
# The game's y axis is DOWN-positive (REDRIVER2 convention: smaller y is higher),
# so the light that comes from above has a NEGATIVE y here, and the projection
# must not negate y. Getting either backwards renders the cars upside down and
# lit from below.
LIGHT = (0.36, -0.86, -0.36)


def _png(path, w, h, pix):
    import zlib

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += pix[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def _project(v):
    x, y, z = v
    # y is down-positive in the data, so screen y follows +y (no leading minus -
    # with one, the cars render upside down)
    cx = x * math.cos(YAW) + z * math.sin(YAW)
    cz = -x * math.sin(YAW) + z * math.cos(YAW)
    return cx, (y * math.cos(PITCH) - cz * math.sin(PITCH)), cz


def _fill(pix, w, h, pts, rgb):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = max(0, int(min(xs))), min(w - 1, int(max(xs)))
    y0, y1 = max(0, int(min(ys))), min(h - 1, int(max(ys)))
    if x0 > x1 or y0 > y1:
        return
    ax, ay = pts[0][0], pts[0][1]
    bx, by = pts[1][0], pts[1][1]
    cx, cy = pts[2][0], pts[2][1]
    area = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if area == 0:
        return
    for y in range(y0, y1 + 1):
        fy = y + 0.5
        for x in range(x0, x1 + 1):
            fx = x + 0.5
            w0 = (bx - ax) * (fy - ay) - (fx - ax) * (by - ay)
            w1 = (cx - bx) * (fy - by) - (fx - bx) * (cy - by)
            w2 = (ax - cx) * (fy - cy) - (fx - cx) * (ay - cy)
            if (w0 >= 0 and w1 >= 0 and w2 >= 0) or (w0 <= 0 and w1 <= 0 and w2 <= 0):
                o = (y * w + x) * 3
                pix[o], pix[o + 1], pix[o + 2] = rgb


def render(items, path, cols, cell=128, pad=10):
    """items: list of (label, vertices, faces) with faces = list of index tuples."""
    rows = (len(items) + cols - 1) // cols
    w, h = cols * cell, rows * cell
    pix = bytearray(w * h * 3)

    for idx, (_label, verts, faces) in enumerate(items):
        if not verts:
            continue
        proj = [_project(v) for v in verts]
        xs = [p[0] for p in proj]
        ys = [p[1] for p in proj]
        zs = [p[2] for p in proj]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1)
        sc = (cell - 2 * pad) / float(span)
        ox = (idx % cols) * cell + cell / 2.0 - (min(xs) + max(xs)) / 2.0 * sc
        oy = (idx // cols) * cell + cell / 2.0 - (min(ys) + max(ys)) / 2.0 * sc
        scr = [(ox + x * sc, oy + y * sc, z) for x, y, z in proj]

        order = []
        for fi, f in enumerate(faces):
            depth = sum(proj[i][2] for i in f) / float(len(f))
            order.append((depth, fi, f))
        order.sort(reverse=True)                       # back to front

        for _depth, _fi, f in order:
            # MEASURED: cross(u, v) points INWARD for both games' winding (D1 and
            # D2 agree), so swap the operands to get the outward normal and shade
            # directionally - no abs(), which was hiding the inversion.
            a, b, c = verts[f[0]], verts[f[1]], verts[f[2]]
            u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
            v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            n = (v[1] * u[2] - v[2] * u[1],
                 v[2] * u[0] - v[0] * u[2],
                 v[0] * u[1] - v[1] * u[0])
            ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
            d = max(0.0, (n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2]) / ln)
            shade = int(38 + 200 * d)
            rgb = (shade, shade, min(255, shade + 20))
            for tri in ((f[0], f[1], f[2]),) + (((f[0], f[2], f[3]),) if len(f) == 4 else ()):
                _fill(pix, w, h, [scr[tri[0]], scr[tri[1]], scr[tri[2]]], rgb)

    _png(path, w, h, pix)
    print("  %s  (%dx%d, %d shapes)" % (path, w, h, len(items)))
    return path


def pool_items(pool_path):
    d = json.load(open(pool_path))
    base = os.path.dirname(pool_path)
    items = []
    for it in d["items"]:
        g = json.load(open(os.path.join(base, it["geom"])))
        k = g["scale"]
        verts = [(v[0] * k, v[1] * k, v[2] * k) for v in g["vertices"]]
        faces = [tuple(p["v"]) for p in g["polys"]]
        items.append(("%s/%d" % (g["sources"][0][0], g["sources"][0][1]),
                      verts, faces))
    return items


def baked_items(lev_path):
    blob = open(lev_path, "rb").read()
    body, _size = scalemodels.d2_car_lump(blob)
    items = []
    for m in range(scalemodels.MAX_CAR_MODELS):
        clean, _dam, _low = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        if clean == -1:
            continue
        model = lev1.decode_model(blob, body + 164 + clean)
        if not model.vertices:
            continue
        faces = [tuple(p.v) for p in model.polys]
        items.append(("model %d" % m, list(model.vertices), faces))
    return items


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=os.path.join(here, "out", "pool.json"))
    ap.add_argument("--baked", default=os.path.join(here, "out", "bake", "D1POOL.LEV"))
    args = ap.parse_args(argv)

    out = []
    if os.path.exists(args.baked):
        out.append(render(baked_items(args.baked),
                          os.path.join(here, "out", "preview_baked.png"), cols=7))
    if os.path.exists(args.pool):
        out.append(render(pool_items(args.pool),
                          os.path.join(here, "out", "preview_pool.png"), cols=8))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
