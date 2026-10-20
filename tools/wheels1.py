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
DEFAULT_DONOR_DIR = "src_rebuild/bin/Release_dev/DRIVER2/LEVELS"

import cosmetics1   # noqa: E402
import wheelgeom    # noqa: E402

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
        if lev1.has_car_models(blob):
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


def _render_push(centres, body_verts, scale, half_tyre,
                 proud=wheelgeom.WHEEL_PROUD):
    """Push the wheels out for the RENDER only.

    The cosmetics put the wheels where Driver 2 puts its own - which assumes the
    body has wheel arches. A D1 body does not: it is a solid shell, so a tyre
    placed there is covered by the body's own side face and cannot be seen at all.
    The preview needs them visible; the records must stay faithful. So the record
    keeps both, and only the renderer uses this one.
    """
    bmin, bmax = wheelgeom.body_bbox(body_verts)
    xc = (bmin[0] + bmax[0]) / 2.0 * scale
    half_w = (bmax[0] - bmin[0]) / 2.0 * scale
    out = []
    for (x, y, z) in centres:
        side = 1.0 if x >= xc else -1.0
        want = half_w + half_tyre * proud
        out.append((side * max(abs(x), want), y, z))
    return out


def placements(levels_dir, pool_path, out_root, donor_dir=None):
    """Per pool item: which wheel model it wears and where the four wheels go.

    Two sets of centres, because two things need them and they differ on purpose:
    `centres` is where the COSMETICS put the wheel, re-proportioned from the
    matched Driver 2 car so the wheelbase, the track and the ride height all come
    from a car of that shape; `render_centres` is that pushed out to the body's
    side so a preview can see it at all. See _render_push.
    """
    import wheelgeom

    pool = json.load(open(pool_path))
    pd = os.path.dirname(pool_path)
    canonical, wheel = wheelgeom.canonical_wheel(levels_dir)
    index = {"version": 2, "wheel_model": "CLEANWHEEL", "wheel_level": canonical,
             "wheel_radii_raw": wheelgeom.wheel_radii(levels_dir), "items": []}
    if wheel is None:
        return index
    radius = wheelgeom.wheel_radius(wheel, 1.0)
    half_tyre = max(abs(v[0]) for v in wheel.vertices)

    donor_dir = donor_dir or DEFAULT_DONOR_DIR
    donors = cosmetics1.load_donors(donor_dir)
    donor_city = "CHICAGO" if "CHICAGO" in donors else sorted(donors)[0]
    donor_recs = donors[donor_city]
    donor_bb = cosmetics1.donor_bodies(
        os.path.join(donor_dir, donor_city + ".LEV"))
    slots = sorted(donor_bb)
    donor_halves = [cosmetics1.halves_from_bbox(donor_bb[k]) for k in slots]
    index["donor"] = "%s/%s" % (os.path.basename(donor_dir.rstrip("/\\")), donor_city)

    for it in pool["items"]:
        g = json.load(open(os.path.join(pd, it["geom"])))
        body = g["vertices"]
        scale = g["scale"]
        bmin, bmax = wheelgeom.body_bbox(body)
        half = [(bmax[i] - bmin[i]) / 2.0 * scale for i in range(3)]
        di = slots[cosmetics1.donor_index(half, donor_halves)]
        drec = donor_recs[di]
        dcentres = cosmetics1.engine_wheel_positions(drec["wheelDisp"],
                                                     drec["sA"][5])
        centres = wheelgeom.placement_from_donor(body, donor_bb[di], dcentres,
                                                 scale)
        index["items"].append({
            "pool_index": it["pool_index"],
            "level": g["sources"][0][0], "model": g["sources"][0][1],
            "wheel_model": "CLEANWHEEL", "wheel_level": canonical,
            "donor": di, "scale": scale, "radius": radius,
            "half_tyre": half_tyre,
            "wheelbase": max(c[2] for c in centres) - min(c[2] for c in centres),
            "track": (max(c[0] for c in centres) - min(c[0] for c in centres)) / 2.0,
            "centres": [list(c) for c in centres],
            "render_centres": [list(c) for c in _render_push(
                centres, body, scale, half_tyre)],
        })
    d = os.path.join(out_root, "wheels")
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(os.path.join(d, "placement.json"), "w") as f:
        json.dump(index, f, indent=1)
    return index


def _clusters(vals):
    """How many distinct groups a few near-equal values fall into.

    Driver 2's own wheelDisp is NOT perfectly symmetric - its model 0 has x of 122,
    125, -123 and -126 - so re-proportioning a donor faithfully gives four distinct
    x values rather than two. What has to hold is that they form two SIDES, not that
    the two are identical.
    """
    vs = sorted(vals)
    tol = max(1.0, (vs[-1] - vs[0]) * 0.06)
    n = 1
    for a, b in zip(vs, vs[1:]):
        if b - a > tol:
            n += 1
    return n


def check_placement(pool_path, out_root):
    """Every car gets four wheels: two axles, two sides, all on its own ground.

    The invariants the per-car placement is held to. Two of them are bands taken
    from Driver 2's own cars rather than chosen here:

      * the wheelbase is a FRACTION OF THE BODY - 1.04..1.28 of the half-length on
        Driver 2's ordinary cars, 0.97..1.60 over all its slots - so a check that
        demanded one shared value was checking for the bug;
      * the body sits BELOW the wheel contact on Driver 2's cars too: on its model
        0 the sill is 0.90 of a tyre radius past the contact point, across its five
        ordinary cars -0.66..-1.17. A car whose wheels hang below the body instead
        is the "sill on the ground" bug coming back.
    """
    pl = os.path.join(out_root, "wheels", "placement.json")
    if not os.path.exists(pl):
        print("no %s - run --place first" % pl)
        return 1
    doc = json.load(open(pl))
    items = doc["items"]
    pd = os.path.dirname(pool_path)
    pool = {i["pool_index"]: i for i in json.load(open(pool_path))["items"]}

    WB_MIN, WB_MAX = 0.95, 1.60          # D2: 1.04..1.28 cars, 0.97..1.60 all
    DROP_MIN, DROP_MAX = -1.45, -0.35    # D2's ordinary cars: -1.17..-0.66

    problems = []
    ratios = []
    drops = []
    for i in items:
        c = i["centres"]
        tag = "pool %d (%s)" % (i["pool_index"], i["level"])
        if len(c) != 4:
            problems.append("%s: %d centres, not 4" % (tag, len(c)))
            continue
        zs = [q[2] for q in c]
        xs = [q[0] for q in c]
        ys = [q[1] for q in c]
        if _clusters(zs) != 2:
            problems.append("%s: %d axle groups at z, not 2 (both axles?)"
                            % (tag, _clusters(zs)))
        if _clusters(xs) != 2:
            problems.append("%s: %d side groups at x, not 2 (both sides?)"
                            % (tag, _clusters(xs)))
        if _clusters(ys) != 1:
            problems.append("%s: wheels at %d different heights"
                            % (tag, _clusters(ys)))
        if len(i.get("render_centres") or []) != 4:
            problems.append("%s: no render_centres" % tag)
        g = json.load(open(os.path.join(pd, pool[i["pool_index"]]["geom"])))
        scale = i["scale"]
        half_len = (max(v[2] for v in g["vertices"])
                    - min(v[2] for v in g["vertices"])) / 2.0 * scale
        wb = i["wheelbase"]
        if half_len:
            ratios.append(wb / half_len)
            if not (WB_MIN <= wb / half_len <= WB_MAX):
                problems.append("%s: wheelbase/body %.2f outside %.2f..%.2f"
                                % (tag, wb / half_len, WB_MIN, WB_MAX))
        # the body must reach BELOW the tyre contact, as Driver 2's does
        drop = (c[0][1] + i["radius"] * scale
                - max(v[1] for v in g["vertices"]) * scale) / (i["radius"] * scale)
        drops.append(drop)
        if not (DROP_MIN <= drop <= DROP_MAX):
            problems.append("%s: tyre sits %.2f radii from the sill, outside %.2f..%.2f"
                            % (tag, drop, DROP_MIN, DROP_MAX))

    print("%d items checked" % len(items))
    print("  wheelbase        %.0f .. %.0f  (%d distinct - per car, not one value)"
          % (min(i["wheelbase"] for i in items),
             max(i["wheelbase"] for i in items),
             len({round(i["wheelbase"]) for i in items})))
    print("  wheelbase / body %.2f .. %.2f  (D2's ordinary cars 1.04 .. 1.28)"
          % (min(ratios), max(ratios)) if ratios else "  no ratios")
    print("  sill vs contact  %.2f .. %.2f radii below  (D2's cars -1.17 .. -0.66)"
          % (min(drops), max(drops)) if drops else "  no drops")
    print("  donors used      %s" % sorted({i["donor"] for i in items}))
    for p in problems[:12]:
        print("  PROBLEM: %s" % p)
    print("VERDICT: %s" % ("clean" if not problems else "%d problem(s)"
                           % len(problems)))
    return 1 if problems else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--levels",
                    default="src_rebuild/bin/Release_dev/DRIVER/LEVELS")
    ap.add_argument("--donor", default=DEFAULT_DONOR_DIR,
                    help="Driver 2 LEVELS, for the cars the wheelbase and ride height come from")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--verify", action="store_true",
                    help="check what has been exported instead of writing it")
    ap.add_argument("--place", action="store_true",
                    help="write the per-car wheel placement record and stop")
    ap.add_argument("--check", action="store_true",
                    help="check the placement record against the plan's invariants")
    ap.add_argument("--pool", default=os.path.join(HERE, "out", "pool.json"))
    args = ap.parse_args(argv)

    if args.check:
        return check_placement(args.pool, args.out)

    if args.verify:
        return 1 if verify(args.levels, args.out) else 0

    if args.place:
        idx = placements(args.levels, args.pool, args.out, args.donor)
        if not idx["items"]:
            print("no placeable pool items")
            return 1
        rad = [i["radius"] for i in idx["items"]]
        trk = [i["track"] for i in idx["items"]]
        wbs = [i["wheelbase"] for i in idx["items"]]
        dons = sorted({i["donor"] for i in idx["items"]})
        print("%d items placed; wheel from %s; donors from %s" %
              (len(idx["items"]), idx.get("wheel_level"), idx.get("donor")))
        print("  wheelbase %.0f .. %.0f  (per car, from the matched Driver 2 car)"
              % (min(wbs), max(wbs)))
        print("  track     %.0f .. %.0f  (per car, from its own width)"
              % (min(trk), max(trk)))
        print("  radius    %.1f raw (%.1f at the bake scale - the axle height)"
              % (rad[0], rad[0] * idx["items"][0]["scale"]))
        print("  Driver 2 donor slots used: %s" % dons)
        print("  per-level raw wheel radii: %s" %
              {k: int(v) for k, v in sorted(idx["wheel_radii_raw"].items())})
        print("wrote %s" % os.path.join(args.out, "wheels", "placement.json"))
        return 0

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
