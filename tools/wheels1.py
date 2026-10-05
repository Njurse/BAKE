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

# The wheel TEXTURES. These are the names as FRISCO spells them; MIAMI uses
# NEWWHEEL / NEWWHCAP for the same job, so both spellings are looked up.
WHEEL_TEXTURE_NAMES = ("WHEEL1", "WHEEL2", "NEWWHEEL", "NEWWHCAP")


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


def texture_exports(levels_dir, out_root, scale=4):
    """Crop each level's wheel TEXTURES out of its own texture pages.

    A wheel texture is a named detail rect on a page - on FRISCO `WHEEL1` and
    `WHEEL2` sit on page 0 - found through the TEXINF nameoffset into the level's
    TEXTURENAMES. They are exported beside the models and, like them, are NOT
    wired into the bake yet.
    """
    import pages1

    index = {"version": 1, "textures": []}
    base = os.path.join(out_root, "wheels", "textures")
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LEV"):
            continue
        level = fn[:-4]
        blob = open(os.path.join(levels_dir, fn), "rb").read()
        if pages1.texture_info_lump(blob) is None:
            continue
        for name in WHEEL_TEXTURE_NAMES:
            for page_index, det in pages1.find_texture(blob, name):
                got = pages1.crop_detail(blob, page_index, det, 0)
                if not got or got[0] == 0 or got[1] == 0:
                    continue
                w, h, rgb = got
                d = os.path.join(base, level)
                if not os.path.isdir(d):
                    os.makedirs(d)
                png = os.path.join(d, "%s_%s_p%d.png" % (level, name, page_index))
                big = bytearray()
                for y in range(h):
                    row = rgb[y * w * 3:(y + 1) * w * 3]
                    row = b"".join(row[i:i + 3] * scale
                                   for i in range(0, len(row), 3))
                    for _ in range(scale):
                        big += row
                d1pool._png(png, w * scale, h * scale, big)
                index["textures"].append({
                    "level": level, "name": name, "page": page_index,
                    "id": det["id"],
                    "rect": [det["x"], det["y"], det["width"], det["height"]],
                    "png": os.path.relpath(png, out_root).replace("\\", "/"),
                    "scale": scale,
                })
    if not os.path.isdir(out_root):
        os.makedirs(out_root)
    with open(os.path.join(out_root, "wheel_textures.json"), "w") as f:
        json.dump(index, f, indent=1)
    return index


def verify(levels_dir, out_root):
    """Assert what this phase promised, and return a list of problems.

    Every car-bearing level must yield its wheel models with real geometry, and
    every exported crop must be a real image whose size is its detail rect times
    the scale.

    A wheel may be FLAT in one axis - TRAIN's HUBCAP is a plate, which is right for
    a hubcap decal - so the geometry test is that at most one extent is empty, not
    that none is.
    """
    from PIL import Image

    problems = []
    wi = os.path.join(out_root, "wheels.json")
    ti = os.path.join(out_root, "wheel_textures.json")
    if not os.path.exists(wi) or not os.path.exists(ti):
        return ["no wheels.json / wheel_textures.json under %s" % out_root]
    wheels = json.load(open(wi))["wheels"]
    textures = json.load(open(ti))["textures"]

    car_levels = set()
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LEV"):
            continue
        blob = open(os.path.join(levels_dir, fn), "rb").read()
        if lev1.car_models_lump(blob) is not None:
            car_levels.add(fn[:-4])

    by_level = {}
    for w in wheels:
        by_level.setdefault(w["level"], []).append(w)
    for lv in sorted(car_levels):
        got = by_level.get(lv)
        if not got:
            problems.append("%s: no wheel models exported" % lv)
            continue
        for w in got:
            b = w["bbox_raw"]
            ext = [b[3] - b[0], b[4] - b[1], b[5] - b[2]]
            if w["verts"] <= 0 or w["tris"] <= 0:
                problems.append("%s/%s: empty model" % (lv, w["name"]))
            elif sum(1 for e in ext if e > 0) < 2:
                problems.append("%s/%s: degenerate bbox %s" % (lv, w["name"], ext))

    tby = {}
    for t in textures:
        tby.setdefault(t["level"], []).append(t)
    for lv in sorted(car_levels):
        if not tby.get(lv):
            problems.append("%s: no wheel textures exported" % lv)
        for t in tby.get(lv, []):
            p = os.path.join(out_root, t["png"])
            if not os.path.exists(p):
                problems.append("%s/%s: missing %s" % (lv, t["name"], t["png"]))
                continue
            im = Image.open(p)
            want = (t["rect"][2] * t["scale"], t["rect"][3] * t["scale"])
            px = list(im.getdata())
            if im.size != want:
                problems.append("%s/%s: %s != rect %s" % (lv, t["name"], im.size, want))
            if not px or not any(c != (0, 0, 0) for c in px):
                problems.append("%s/%s: crop is blank" % (lv, t["name"]))
    print("  %d levels, %d wheel models, %d texture crops" % (len(car_levels), len(wheels), len(textures)))
    print("VERDICT: %s" % ("clean" if not problems else "%d problems" % len(problems)))
    for p in problems:
        print("   - %s" % p)
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels",
                    default="src_rebuild/bin/Release_dev/DRIVER/LEVELS")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--verify", action="store_true",
                    help="check what has been exported instead of writing it")
    args = ap.parse_args(argv)

    if args.verify:
        return 1 if verify(args.levels, args.out) else 0

    index = export(args.levels, args.out)
    tex = texture_exports(args.levels, args.out)
    if not index["wheels"] and not tex["textures"]:
        print("no wheel models or wheel textures found under %s" % args.levels)
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
    tby = {}
    for t in tex["textures"]:
        tby.setdefault(t["level"], []).append(t)
    for level in sorted(tby):
        print("%-9s %d wheel textures: %s" % (level, len(tby[level]),
              [(t["name"], "%dx%d" % (t["rect"][2], t["rect"][3]), "p%d" % t["page"])
               for t in tby[level]]))
    print("wrote %d wheel texture crops" % len(tex["textures"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
