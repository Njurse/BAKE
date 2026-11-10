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

LEVELS_DIR = "src_rebuild/bin/Release_dev/DRIVER/LEVELS"

YAW = math.radians(34.0)
PITCH = math.radians(18.0)
# The game's y axis is DOWN-positive (REDRIVER2 convention: smaller y is higher),
# so the light that comes from above has a NEGATIVE y here, and the projection
# must not negate y. Getting either backwards renders the cars upside down and
# lit from below.
LIGHT = (0.36, -0.86, -0.36)

# docs/DRIVER1.md §8. A Driver 2 car of this size fills one preview cell; a
# Driver 1 mesh that skipped 2048/3000 is about 1.47× longer and draws larger
# than the cell, which is the in-game size difference the old per-car fit hid.
D2_MEDIAN_BOX = (284, 176, 752)   # width, height, length
D1_MEDIAN_LENGTH = 1104
BAKE_SCALE = 2048.0 / 3000.0


def median_box_corners(box=D2_MEDIAN_BOX):
    """The eight corners of a box centred on the origin, in engine units."""
    w, h, length = box
    corners = []

    for x in (-w / 2.0, w / 2.0):
        for y in (-h / 2.0, h / 2.0):
            for z in (-length / 2.0, length / 2.0):
                corners.append((x, y, z))

    return corners


def projected_span(points):
    """Max of the projected width and height. `points` are already projected."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return max(max(xs) - min(xs), max(ys) - min(ys), 1.0)


def gl_uv(u, v, width, height):
    """OBJ 0..1 → the OpenGL coordinate at the centre of that texel.

    Export writes engine texel n as n/(size-1), so 0 is the first texel and
    1 is the last. Feeding that straight to GL puts texel 0 on the left edge
    and texel size-1 on the wrap edge (u=1 samples texel 0 under GL_REPEAT),
    and every texel in between is n/(size-1) of a pixel off. The centre of
    texel n is (n+0.5)/size.
    """
    if width > 1:
        u = (float(u) * (width - 1) + 0.5) / float(width)

    if height > 1:
        v = (float(v) * (height - 1) + 0.5) / float(height)

    return u, v


def reference_span(project=None):
    """Projected span of the Driver 2 median car under `project` (default camera)."""
    project = project or _project
    return projected_span([project(v) for v in median_box_corners()])


def preview_bake_scale(cli_scale, declared, extents, col_box=None):
    """OBJ units to engine units for the authoring preview.

    ``cli_scale`` is ``--scale`` (``--bake-scale`` is the old name of that
    flag). ``declared`` is ``declare.scale``. Both are the bake multiplier.
    With neither, guess from the mesh the way ``infer_bake_scale`` does.
    """
    if cli_scale is not None:
        return float(cli_scale)

    if declared is not None:
        return float(declared)

    return infer_bake_scale(extents, col_box)


def infer_bake_scale(extents, col_box=None):
    """OBJ units -> engine units, for placing handling-space wheels on the mesh.

    `col_box` is the handling record's half-extents (engine units). A Driver 1
    OBJ is about 3000/2048 times that box; a Driver 2 export already matches it.
    Without a box, a long axis past the Driver 2 band (median length 752, the
    longest ordinary cars sit near 850) is treated as Driver 1. A mesh under 50
    units is authoring metres, and 265 is the historic metres->engine factor.
    """
    long = max(extents) if extents else 0.0

    if col_box:
        engine = max(abs(float(v)) for v in col_box) * 2.0

        if engine > 1.0:
            ratio = long / engine

            if ratio >= 1.25:
                return BAKE_SCALE

            if ratio <= 1.15:
                return 1.0

    if long > 980:
        return BAKE_SCALE

    if long > 50:
        return 1.0

    return 265.0


def world_lengths(vert_lists):
    """(min, max) of each mesh's longest axis, for the size line the preview prints."""
    lengths = []

    for verts in vert_lists:
        if not verts:
            continue

        span = max(max(v[i] for v in verts) - min(v[i] for v in verts) for i in range(3))
        lengths.append(span)

    if not lengths:
        return None

    return min(lengths), max(lengths)


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


def attach_wheels(items, levels_dir, pool_path):
    """The same items with the shared wheel on each, or None if it cannot be.

    The flat renderer needs no page at all - it shades by geometry - so unlike
    the textured path there is nothing to load but the wheel model itself.
    """
    import json
    import wheelgeom
    import wheelrender

    pl = os.path.join(os.path.dirname(pool_path), "wheels", "placement.json")
    if not os.path.exists(pl):
        print("  (no %s - run wheels1.py --place first)" % pl)
        return None
    recs = {i["pool_index"]: i for i in json.load(open(pl))["items"]}
    level, model = wheelgeom.canonical_wheel(levels_dir)
    if model is None:
        print("  (no wheel model under %s)" % levels_dir)
        return None
    out = []
    for n, item in enumerate(items):
        r = recs.get(n)
        if not r:
            out.append(item)
            continue
        # this renderer's items are (label, verts, faces[, normals]), NOT the
        # textured path's (label, key, verts, faces) - so the tuple is rebuilt
        # here rather than through wheelrender.wheeled_item.
        wv, wtris, _wtex = wheelrender.wheel_copies(
            model, r.get("render_centres") or r["centres"], r["scale"])
        off = len(item[1])
        faces = list(item[2]) + [tuple(off + i for i in t) for t in wtris]
        if len(item) > 3 and item[3] is not None:
            # the wheels are flat, so a geometric normal is already right - but the
            # list has to stay aligned with the faces.
            out.append((item[0], list(item[1]) + list(wv), faces,
                        list(item[3]) + [None] * len(wtris)))
        else:
            out.append((item[0], list(item[1]) + list(wv), faces))
    print("  (wheels from %s)" % level)
    return out


def render(items, path, cols, cell=128, pad=10):
    """items: list of (label, vertices, faces) with faces = list of index tuples.

    An item may carry a 4th element, one OUTWARD normal per face. When it does,
    that is what the shading uses - the model's own normals, so a curved panel
    reads as curved instead of faceted. Without it the face's geometric normal
    is used, which is the same vector for flat geometry.
    """
    rows = (len(items) + cols - 1) // cols
    w, h = cols * cell, rows * cell
    pix = bytearray(w * h * 3)

    for idx, item in enumerate(items):
        _label, verts, faces = item[0], item[1], item[2]
        fnormals = item[3] if len(item) > 3 else None
        if not verts:
            continue
        proj = [_project(v) for v in verts]
        xs = [p[0] for p in proj]
        ys = [p[1] for p in proj]
        zs = [p[2] for p in proj]
        # One scale for every car: the Driver 2 median. Fitting each mesh to
        # its own span made an unscaled Driver 1 body (REDBUICK at ~1100) look
        # the same size as the baked car the game draws at ~750.
        span = reference_span()
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
            n = fnormals[_fi] if (fnormals and fnormals[_fi]) else None
            if n is not None:
                ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
            else:
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
    got = world_lengths(item[1] for item in items)
    size = ""

    if got:
        size = "  longest axis %.0f..%.0f (D2 median length %d)" % (
            got[0], got[1], D2_MEDIAN_BOX[2])

    print("  %s  (%dx%d, %d shapes%s)" % (path, w, h, len(items), size))
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


def _mean_normal(model, poly):
    """One outward normal per face, averaged from the model's own per-vertex
    normals - what the shading should use."""
    ns = model.poly_normals(poly)
    sx = sum(n[0] for n in ns) / len(ns)
    sy = sum(n[1] for n in ns) / len(ns)
    sz = sum(n[2] for n in ns) / len(ns)
    L = (sx * sx + sy * sy + sz * sz) ** 0.5 or 1.0
    return (sx / L, sy / L, sz / L)


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
        normals = [_mean_normal(model, p) for p in model.polys]
        items.append(("model %d" % m, list(model.vertices), faces, normals))
    return items


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=os.path.join(here, "out", "pool.json"))
    ap.add_argument("--baked", default=os.path.join(here, "out", "bake", "D1POOL.LEV"))
    ap.add_argument("--wheeled", action="store_true",
                    help="write the wheels-on sheet as an EXTRA copy, leaving the plain ones alone")
    ap.add_argument("--levels-dir", default=LEVELS_DIR,
                    help="source DRIVER/LEVELS, for the wheel model")
    args = ap.parse_args(argv)

    out = []
    if os.path.exists(args.baked):
        items = baked_items(args.baked)
        out.append(render(items, os.path.join(here, "out", "preview_baked.png"), cols=7))
        if args.wheeled:
            w = attach_wheels(items, args.levels_dir, args.pool)
            if w is not None:
                out.append(render(w, os.path.join(here, "out", "preview_wheeled.png"),
                                  cols=7))
    if os.path.exists(args.pool):
        items = pool_items(args.pool)
        out.append(render(items, os.path.join(here, "out", "preview_pool.png"), cols=8))
        if args.wheeled:
            w = attach_wheels(items, args.levels_dir, args.pool)
            if w is not None:
                out.append(render(w, os.path.join(here, "out",
                                                  "preview_pool_wheeled.png"), cols=8))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
