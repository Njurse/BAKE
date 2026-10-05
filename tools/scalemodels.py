#!/usr/bin/env python3
"""scalemodels.py - compare Driver 1 and Driver 2 car-model sizes.

Phase 3 of the d1cars roadmap: decide the bake scale. Reads both games' car
models, prints each clean model's vertex bbox and bounding_sphere, and reports
the ranges so a D1 shape can be normalised into Driver 2's car size band.

Both games put a car's MODEL at  car_lump_body + 164 + offset:
  D1  entry table at +4 (13 x 12), r_ofs = +160, MODEL at r_ofs + offset + 4
      (a 4-byte size precedes each MODEL)
  D2  entry table at +4 (13 x 12), MODEL at +164 + offset
so the MODEL struct and its SVECTOR vertex array are read the same way.

Usage:
    python scalemodels.py --d1 LEVELS_DIR --d2 LEVELS_DIR [--d2city CHICAGO]
"""

import argparse
import glob
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1  # noqa: E402

LUMP_CAR_MODELS = 28
MAX_CAR_MODELS = 13

# A level cell, in world units, per game. DriverLevelTool prints D1's ("cell
# size: 3000"); D2's is the engine's RC_LEVEL_CELL_SIZE.
D1_CELL_SIZE = 3000
D2_CELL_SIZE = 2048


def d2_car_lump(blob):
    """Driver 2: DATA1 starts at citylumps[0]; its sub-lumps have NO count."""
    d1_off = struct.unpack_from("<I", blob, 8)[0]
    off = d1_off + 8
    end = len(blob)
    while off + 8 <= end:
        t, s = struct.unpack_from("<ii", blob, off)
        if t == 0xFF or s < 0 or off + 8 + s > end:
            break
        if t == LUMP_CAR_MODELS:
            return off + 8, s
        off = off + 8 + ((s + 3) & ~3)
    return None


def d2_model_stats(blob, body, model_index):
    """Return dict or None for a Driver 2 car model (model struct at +164+ofs)."""
    (clean, _dam, _low) = struct.unpack_from("<3i", blob, body + 4 + model_index * 12)
    if clean == -1:
        return None
    mo = body + 164 + clean
    (sf, f2, _i, _t, _z, bound, _npn, nv, npol) = struct.unpack_from(lev1.MODEL_FMT,
                                                                     blob, mo)
    (vofs, _p, _n, _pn, _c) = struct.unpack_from(lev1.MODEL_OFFSETS_FMT, blob, mo + 16)
    xs = []
    ys = []
    zs = []
    for i in range(nv):
        x, y, z, _pad = struct.unpack_from("<hhhh", blob, mo + vofs + i * 8)
        xs.append(x)
        ys.append(y)
        zs.append(z)
    if not xs:
        return None
    return dict(nv=nv, npol=npol, bound=bound,
                dims=(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))


def d1_rows(levels_dir, variants=("clean",)):
    rows = []
    for path in sorted(glob.glob(os.path.join(levels_dir, "*.LEV"))):
        name = os.path.basename(path)[:-4]
        with open(path, "rb") as f:
            blob = f.read()
        if lev1.car_models_lump(blob) is None:
            continue
        for m, models in lev1.car_models(blob):
            for variant in variants:
                model = models.get(variant)
                if model is None:
                    continue
                bb = model.bbox()
                if not bb:
                    continue
                rows.append(("%s/%s" % (name, variant), m,
                             (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]),
                             model.bounding_sphere))
    return rows


def d2_rows(levels_dir):
    rows = []
    for path in sorted(glob.glob(os.path.join(levels_dir, "*.LEV"))):
        name = os.path.basename(path)[:-4]
        with open(path, "rb") as f:
            blob = f.read()
        got = d2_car_lump(blob)
        if not got:
            continue
        body, _size = got
        for m in range(MAX_CAR_MODELS):
            st = d2_model_stats(blob, body, m)
            if st:
                rows.append((name, m, st["dims"], st["bound"]))
    return rows


def summarize(label, rows):
    if not rows:
        print("%s: no models" % label)
        return None
    ws = [r[2][0] for r in rows]
    hs = [r[2][1] for r in rows]
    ls = [r[2][2] for r in rows]
    bs = [r[3] for r in rows]
    print("%s: %d models over %d levels" % (label, len(rows),
                                            len(set(r[0].split("/")[0] for r in rows))))
    print("   width  %4d..%-4d  (median %d)" % (min(ws), max(ws), sorted(ws)[len(ws)//2]))
    print("   height %4d..%-4d  (median %d)" % (min(hs), max(hs), sorted(hs)[len(hs)//2]))
    print("   length %4d..%-4d  (median %d)" % (min(ls), max(ls), sorted(ls)[len(ls)//2]))
    print("   bound  %4d..%-4d" % (min(bs), max(bs)))
    return dict(w=(min(ws), max(ws), sorted(ws)[len(ws)//2]),
                h=(min(hs), max(hs), sorted(hs)[len(hs)//2]),
                l=(min(ls), max(ls), sorted(ls)[len(ls)//2]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--d1", required=True, help="a Driver 1 LEVELS directory")
    ap.add_argument("--d2", required=True, help="a Driver 2 LEVELS directory")
    ap.add_argument("--detail", action="store_true", help="print every row")
    ap.add_argument("--variants", default="clean",
                    help="D1 variants to include (clean,damaged,low); default clean")
    args = ap.parse_args(argv)

    d1 = d1_rows(args.d1, tuple(args.variants.split(",")))
    d2 = d2_rows(args.d2)
    if args.detail:
        for label, rows in (("D1", d1), ("D2", d2)):
            for name, m, dims, bound in rows:
                print("%s %-9s model %2d  %4dx%4dx%4d  bound %d"
                      % (label, name, m, dims[0], dims[1], dims[2], bound))
    s1 = summarize("Driver 1 (clean models)", d1)
    s2 = summarize("Driver 2 (clean models)", d2)
    if s1 and s2:
        print("Driver 1 / Driver 2 length ratio (median): %.3f"
              % (float(s1["l"][2]) / s2["l"][2]))
        print("Driver 1 / Driver 2 width  ratio (median): %.3f"
              % (float(s1["w"][2]) / s2["w"][2]))
        # The principled factor: a Driver 1 level cell is 3000 units, a Driver 2
        # cell 2048 (DriverLevelTool prints D1's; D2's is RC_LEVEL_CELL_SIZE).
        # The SAME physical car is therefore modelled 3000/2048 larger in D1's
        # units, which is what the medians above show.
        k = float(D2_CELL_SIZE) / D1_CELL_SIZE
        print("cell sizes: D1 %d, D2 %d -> bake scale %.5f (D2/D1)"
              % (D1_CELL_SIZE, D2_CELL_SIZE, k))
        for axis, key in (("width", "w"), ("height", "h"), ("length", "l")):
            print("   %-7s D1 median %4d * %.5f = %4.0f   vs D2 median %4d"
                  % (axis, s1[key][2], k, s1[key][2] * k, s2[key][2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
