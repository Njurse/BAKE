#!/usr/bin/env python3
"""wheels1.py - export Driver 1's wheel models, to the side.

THE WHEELS ARE NOT PART OF THE CAR MESH. Every car level names its own set in the
level's model table, and the engine finds them by name -

    main.c:191-194   gHubcapModelPtr   = FindModelPtrWithName("HUBCAP1");
                     gCleanWheelModelPtr = FindModelPtrWithName("CLEANWHEEL");
                     gFastWheelModelPtr  = FindModelPtrWithName("FASTWHEEL");
                     gDamWheelModelPtr   = FindModelPtrWithName("DAMWHEEL");
    cars.c:991       DrawWheelObject(...)

- and draws them per wheel. So a preview that shows only the body is showing half
the vehicle. Measured: the standard wheel is 24 vertices / 8 polys, HUBCAP1 is
24 / 28.

This exports them as .obj + geom.json with an index, and does NOT touch the bake -
the wheels are kept aside for now, ready for when the cosmetics say where they go.

Usage:
    python wheels1.py --levels <dir> [--out <dir>]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import d1pool     # noqa: E402  (the .obj writer, so both exports match)
import lev1       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# The names the engine looks up. TRAIN spells one HUBCAP without the 1; MIAMI's
# TEXTURENAMES carry NEWWHEEL/NEWWHCAP, which are textures rather than models,
# but they are in the list so the tool reports them if a level ever ships them as
# models too.
WHEEL_NAMES = ("CLEANWHEEL", "DAMWHEEL", "FASTWHEEL",
               "HUBCAP", "HUBCAP1", "NEWWHEEL", "NEWWHCAP")


def wheels_in(blob):
    """[(slot, name, Model)] for every wheel-named model in a level."""
    out = []
    for slot, name, model in lev1.level_models(blob):
        if name in WHEEL_NAMES:
            out.append((slot, name, model))
    return out


def wheel_record(level, slot, name, model):
    """The neutral record, shaped like the pool's geometry records so the same
    readers apply."""
    vs = model.vertices
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    _face, point_n, pn, nsource = d1pool.normals_for(model)
    polys = []
    for k, p in enumerate(model.polys):
        polys.append({"kind": p.kind, "set": p.texture_set, "id": p.texture_id,
                      "v": list(p.v), "n": pn[k], "uv": [list(u) for u in p.uv],
                      "color": list(p.color)})
    return {
        "version": 1,
        "level": level,
        "slot": slot,
        "name": name,
        "verts": len(vs),
        "tris": sum(len(d1pool._triangulate(p)) for p in model.polys),
        "bbox_raw": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "bounding_sphere": model.bounding_sphere,
        "errors": [list(e) for e in model.errors],
        "normals_source": nsource,
        "point_normals": point_n,
        "vertices": [list(v) for v in vs],
        "polys": polys,
    }


def export(levels_dir, out_root):
    index = {"version": 1, "wheels": []}
    wheel_dir = os.path.join(out_root, "wheels")
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LEV"):
            continue
        level = fn[:-4]
        path = os.path.join(levels_dir, fn)
        blob = open(path, "rb").read()
        if lev1.models_lump(blob) is None:
            continue
        got = wheels_in(blob)
        if not got:
            continue
        d = os.path.join(wheel_dir, level)
        if not os.path.isdir(d):
            os.makedirs(d)
        for slot, name, model in got:
            stem = "%s_%02d_%s" % (level, slot, name)
            obj = os.path.join(d, stem + ".obj")
            geom = os.path.join(d, stem + "_geom.json")
            d1pool.write_obj(obj, model)
            rec = wheel_record(level, slot, name, model)
            with open(geom, "w") as f:
                json.dump(rec, f, indent=1)
            index["wheels"].append({
                "level": level, "slot": slot, "name": name,
                "obj": os.path.relpath(obj, out_root).replace("\\", "/"),
                "geom": os.path.relpath(geom, out_root).replace("\\", "/"),
                "verts": rec["verts"], "tris": rec["tris"],
                "bbox_raw": rec["bbox_raw"], "bounding_sphere": rec["bounding_sphere"],
            })
    if not os.path.isdir(out_root):
        os.makedirs(out_root)
    with open(os.path.join(out_root, "wheels.json"), "w") as f:
        json.dump(index, f, indent=1)
    return index


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels",
                    default="src_rebuild/bin/Release_dev/DRIVER/LEVELS")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    args = ap.parse_args(argv)

    index = export(args.levels, args.out)
    if not index["wheels"]:
        print("no wheel models found under %s" % args.levels)
        return 1
    by_level = {}
    for w in index["wheels"]:
        by_level.setdefault(w["level"], []).append(w)
    for level in sorted(by_level):
        ws = by_level[level]
        print("%-9s %d wheels: %s" % (level, len(ws),
              [(w["name"], w["verts"], w["tris"]) for w in ws]))
    print("wrote %d wheel models to %s" % (len(index["wheels"]),
                                           os.path.join(args.out, "wheels")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
