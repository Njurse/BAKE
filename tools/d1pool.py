#!/usr/bin/env python3
"""d1pool.py - build the Driver 1 traffic-car pool: extract, dedupe, emit.

Phase 4 of the d1cars roadmap. Reads every Driver 1 LEVELS/*.LEV, decodes each
level's car models (lev1.py), drops the non-car outliers, dedupes the shapes
across levels, and writes one catalogue:

    <out>/pool/<n>_<level>_<model>.obj        the shape, Wavefront .obj
    <out>/pool/<n>_<level>_<model>_geom.json  the neutral record (polys + uv)
    <out>/pool.json                           the index a bake reads

The plan's "civilian CIVCAR*" selector was retired in Phase 2: this D1 build
names only LEVEL models, so a pool item is identified by (level, model index)
and the CAR_MODELS lump IS the traffic set. Levels share cars, so the same shape
appears in several levels and is stored once.

Usage:
    python d1pool.py --levels LEVELS_DIR [--out DIR] [--variants clean]
"""

import argparse
import glob
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1  # noqa: E402

D1_CELL_SIZE = 3000
D2_CELL_SIZE = 2048
SCALE = float(D2_CELL_SIZE) / D1_CELL_SIZE      # 0.68267 - see docs/DRIVER1.md §8

# A D1 traffic car is ~370..460 x ~220..300 x ~940..1202 raw units. Anything
# much smaller is not a car (CREDITS model 0 is 52x49x48).
MIN_LENGTH_RAW = 600


def _triangulate(poly):
    """A poly -> 1 or 2 triangles, as tuples of vertex indices."""
    v = poly.v
    if len(v) == 3:
        return [(v[0], v[1], v[2])]
    return [(v[0], v[1], v[2]), (v[0], v[2], v[3])]


def shape_key(model):
    """A dedupe key over the geometry - vertices and the poly stream's shape."""
    h = hashlib.sha1()
    for x, y, z in model.vertices:
        h.update(struct.pack("<hhh", x, y, z))
    for p in model.polys:
        h.update(p.kind.encode())
        h.update(bytes(bytearray([p.texture_set & 0xFF, p.texture_id & 0xFF])))
        h.update(bytes(bytearray(p.v)))
    return h.hexdigest()


def write_obj(path, model):
    with open(path, "w") as f:
        f.write("o %s\n" % os.path.basename(path)[:-4])
        for x, y, z in model.vertices:
            f.write("v %d %d %d\n" % (x, y, z))
        for p in model.polys:
            for tri in _triangulate(p):
                f.write("f %d %d %d\n" % (tri[0] + 1, tri[1] + 1, tri[2] + 1))


def normals_for(model):
    """(face_normals, point_normals, n_per_poly, source) for one model.

    The engine lights a GT poly from the model's point_normals table, indexed by
    that poly's own normal indices (cars.c:650 indexes nlist = point_normals with
    them, and cars.c:964 recomputes each entry's pad from the table's direction).
    So a baked model must ship such a table AND indices that resolve inside it.

      * when the source model ships one, copy it and keep its indices verbatim -
        measured valid: on all 46 variants that have a table, every GT index is
        in range and max(index) == npn - 1;
      * when it ships none (65 of 111 variants), synthesise one normal per
        VERTEX - the average of the face normals meeting there - and index it by
        the vertex, which is what smooth shading means.

    Both tables are stored the way the engine wants them, i.e. in the models'
    own INWARD convention (lev1.NORMAL_SIGN), not the outward one the renderers
    shade with - the engine derives its lighting from the direction.
    """
    if model.face_normals:
        face = [list(n) for n in model.face_normals]
    else:
        face = [[int(round(c * 4096)) for c in model._geo_normal(p)]
                for p in model.polys]

    if model.normals:
        return face, [list(n) for n in model.normals], \
            [list(p.n) for p in model.polys], "model"

    acc = [[0.0, 0.0, 0.0] for _ in model.vertices]
    for p in model.polys:
        g = model._geo_normal(p)                 # outward
        for vi in p.v:
            acc[vi][0] += g[0]
            acc[vi][1] += g[1]
            acc[vi][2] += g[2]
    pt = []
    for a in acc:
        a = [c * lev1.NORMAL_SIGN for c in a]    # store the inward convention
        L = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5 or 1.0
        pt.append([int(round(a[0] / L * 4096)), int(round(a[1] / L * 4096)),
                   int(round(a[2] / L * 4096))])
    return face, pt, [list(p.v) for p in model.polys], "synthesised"


def geom_record(index, sources, model):
    vs = model.vertices
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    face_n, point_n, pn, nsource = normals_for(model)
    polys = []
    for k, p in enumerate(model.polys):
        polys.append({"kind": p.kind, "set": p.texture_set, "id": p.texture_id,
                      "v": list(p.v), "n": pn[k], "uv": [list(u) for u in p.uv],
                      "color": list(p.color)})
    return {
        "version": 1,
        "pool_index": index,
        "sources": sources,
        "verts": len(vs),
        "tris": sum(len(_triangulate(p)) for p in model.polys),
        "bbox_raw": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "bounding_sphere": model.bounding_sphere,
        "scale": SCALE,
        "normals_source": nsource,
        "face_normals": face_n,
        "point_normals": point_n,
        "vertices": [list(v) for v in vs],
        "polys": polys,
    }


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
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


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


def render_sheet(items, path, cell=112, cols=8):
    """Orthographic side-view wireframe of every shape, one cell each.

    Returns the fraction of ink pixels, which is the automated sanity check that
    the sheet is neither blank nor a solid blob.
    """
    n = len(items)
    rows = (n + cols - 1) // cols
    W, H = cols * cell, rows * cell
    pix = bytearray(W * H * 3)
    for idx, item in enumerate(items):
        model = item["model"]
        if not model.vertices:
            continue
        cx, cy = (idx % cols) * cell, (idx // cols) * cell
        zs = [v[2] for v in model.vertices]
        ys = [v[1] for v in model.vertices]
        zmin, zmax = min(zs), max(zs)
        ymin, ymax = min(ys), max(ys)
        sc = min((cell - 12.0) / max(1, zmax - zmin),
                 (cell - 12.0) / max(1, ymax - ymin))

        def proj(v):
            return (cx + 6 + int((v[2] - zmin) * sc),
                    cy + cell - 6 - int((v[1] - ymin) * sc))

        for p in model.polys:
            pts = [proj(model.vertices[i]) for i in p.v]
            for a in range(len(pts) - 1):
                _line(pix, W, H, pts[a][0], pts[a][1],
                      pts[a + 1][0], pts[a + 1][1], (200, 200, 200))
            _line(pix, W, H, pts[-1][0], pts[-1][1], pts[0][0], pts[0][1],
                  (200, 200, 200))
    _png(path, W, H, pix)
    ink = sum(1 for i in range(0, len(pix), 3) if pix[i])
    frac = float(ink) / (W * H)
    print("  preview: %s (%dx%d, %d shapes, ink %.1f%%)"
          % (path, W, H, n, frac * 100.0))
    return frac


# A car-data city is not always named after its file. Newcastle - the Driver 1
# level reachable only by cheat, whose geometry is the credits drive - is stored in
# CREDITS.LEV. This mapping lives HERE and only here: the bake and the cosmetics
# file both read it, because if they disagreed the geometry and the colours would
# describe different cars.
CITY_SOURCE_FILE = {
    "NEWCASTLE": "CREDITS",
    # The EURO-43 beta level lives beside DRIVER/, not inside its LEVELS folder.
    # From DRIVER/LEVELS this reaches <install>/EURO43/BETAMIAMI.LEV.
    "BETAMIAMI": "../../EURO43/BETAMIAMI",
}


def source_file(city):
    """The .LEV a car-data city is actually stored in."""
    return CITY_SOURCE_FILE.get(city, city)


def collect(levels_dir, variants):
    """Return (items, skipped): items are deduped, in first-seen order."""
    items = []
    seen = {}
    skipped = []
    for path in sorted(glob.glob(os.path.join(levels_dir, "*.LEV"))):
        level = os.path.basename(path)[:-4]
        with open(path, "rb") as f:
            blob = f.read()
        if not lev1.has_car_models(blob):
            continue
        for m, models in lev1.car_models(blob):
            for variant in variants:
                model = models.get(variant)
                if model is None:
                    continue
                bb = model.bbox()
                length = bb[5] - bb[2]
                if length < MIN_LENGTH_RAW:
                    skipped.append((level, m, variant,
                                    (bb[3] - bb[0], bb[4] - bb[1], length),
                                    "too small to be a car"))
                    continue
                key = shape_key(model)
                src = [level, m, variant]
                if key in seen:
                    items[seen[key]]["sources"].append(src)
                    continue
                seen[key] = len(items)
                items.append({"sources": [src], "model": model})
    return items, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels", required=True, help="a Driver 1 LEVELS directory")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "out"),
                    help="output directory (gitignored)")
    ap.add_argument("--variants", default="clean",
                    help="D1 variants to pool (clean,damaged,low); default clean")
    ap.add_argument("--render", action="store_true",
                    help="write out/pool_preview.png (orthographic side views)")
    args = ap.parse_args(argv)

    variants = tuple(args.variants.split(","))
    items, skipped = collect(args.levels, variants)

    pooldir = os.path.join(args.out, "pool")
    if not os.path.isdir(pooldir):
        os.makedirs(pooldir)

    index = {"version": 1, "scale": SCALE,
             "source_cell": D1_CELL_SIZE, "target_cell": D2_CELL_SIZE,
             "variants": list(variants), "count": len(items), "items": []}

    for i, item in enumerate(items):
        src = item["sources"][0]
        stem = "%04d_%s_%d_%s" % (i, src[0], src[1], src[2])
        obj = os.path.join(pooldir, stem + ".obj")
        geom = os.path.join(pooldir, stem + "_geom.json")
        write_obj(obj, item["model"])
        rec = geom_record(i, item["sources"], item["model"])
        with open(geom, "w") as f:
            json.dump(rec, f, indent=1)
        index["items"].append({
            "pool_index": i,
            "name": stem,
            "sources": item["sources"],
            "shared_by": len(item["sources"]),
            "bbox_raw": rec["bbox_raw"],
            "scaled_bbox": [int(round(v * SCALE)) for v in rec["bbox_raw"]],
            "verts": rec["verts"], "tris": rec["tris"],
            "obj": os.path.relpath(obj, args.out).replace("\\", "/"),
            "geom": os.path.relpath(geom, args.out).replace("\\", "/"),
        })

    with open(os.path.join(args.out, "pool.json"), "w") as f:
        json.dump(index, f, indent=1)

    print("pool: %d unique shapes from %d (level, model, variant) sources"
          % (len(items), sum(len(i["sources"]) for i in items)))
    print("  levels: %s" % ", ".join(sorted(set(
        s[0] for i in items for s in i["sources"]))))
    shared = [i for i in items if len(i["sources"]) > 1]
    print("  shared across levels: %d shapes" % len(shared))
    for level, m, variant, dims, why in skipped:
        print("  skipped %s model %d %s %dx%dx%d (%s)"
              % (level, m, variant, dims[0], dims[1], dims[2], why))
    print("  -> %s" % os.path.join(args.out, "pool.json"))
    if args.render:
        render_sheet(items, os.path.join(args.out, "pool_preview.png"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
