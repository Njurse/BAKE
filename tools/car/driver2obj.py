#!/usr/bin/env python3
"""d2cars_to_obj.py — extract Driver 2 car models to OBJ (and optional UV sheets).

Walks a Driver 2 LEVELS/*.LEV, finds LUMP_CAR_MODELS, and writes one Wavefront
OBJ per clean model (model 0..12). UVs are written when the poly is textured
(FT3/FT4/GT3/GT4). This gives you the exact vertex/UV layout the engine uses
so you can format your placeholder fallback cars to match.

Texture pages themselves are not decoded here (they live in the level's page
plan and need the full page decoder). For a solid reference you can open the
OBJ in Blender and paint a simple UV sheet, or force grey in the bake.

Usage:
    python d2cars_to_obj.py CHICAGO.LEV -o out/d2cars
    python d2cars_to_obj.py LEVELS/ --all -o out/d2cars
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1
import scalemodels


def extract_model(blob, body, slot):
    clean, _dam, _low = struct.unpack_from("<3i", blob, body + 4 + slot * 12)
    if clean == -1:
        return None
    mo = body + 164 + clean
    model = lev1.decode_model(blob, mo)
    return model


def write_obj(path, model, name):
    with open(path, "w") as f:
        f.write("# extracted from Driver 2 car model\n")
        f.write("o %s\n" % name)
        for x, y, z in model.vertices:
            f.write("v %d %d %d\n" % (x, y, z))
        # UVs: collect unique (u,v) and emit vt
        uv_map = {}
        uv_list = []
        for p in model.polys:
            if p.uv:
                for u, v in p.uv:
                    key = (u, v)
                    if key not in uv_map:
                        uv_map[key] = len(uv_list)
                        uv_list.append(key)
                        # OBJ vt is 0..1; engine UV is 0..255
                        f.write("vt %.6f %.6f\n" % (u / 255.0, 1.0 - v / 255.0))
        # faces
        for p in model.polys:
            if len(p.v) < 3:
                continue
            # triangulate quads
            tris = [p.v[:3]]
            if len(p.v) == 4:
                tris.append((p.v[0], p.v[2], p.v[3]))
            for tri in tris:
                parts = []
                for vi in tri:
                    # 1-based
                    s = "%d" % (vi + 1)
                    if p.uv and len(p.uv) == len(p.v):
                        # map this vertex's UV
                        # (simple: use the UV that corresponds by position in the poly)
                        # For a proper per-vertex UV we need the original index mapping;
                        # here we emit the face with vertex only if no clean UV match.
                        pass
                    parts.append(s)
                f.write("f %s\n" % " ".join(parts))
        # second pass for faces that have matching UVs (rewrite simpler)
    # rewrite with proper UV indices
    with open(path, "w") as f:
        f.write("# extracted from Driver 2 car model %s\n" % name)
        f.write("o %s\n" % name)
        for x, y, z in model.vertices:
            f.write("v %d %d %d\n" % (x, y, z))
        # emit all UVs referenced
        uv_index = {}
        next_vt = 1
        face_lines = []
        for p in model.polys:
            if len(p.v) < 3:
                continue
            tris = [(p.v[:3], p.uv[:3] if p.uv else ())]
            if len(p.v) == 4:
                tris.append(((p.v[0], p.v[2], p.v[3]),
                             (p.uv[0], p.uv[2], p.uv[3]) if p.uv and len(p.uv) >= 4 else ()))
            for (vs, uvs) in tris:
                parts = []
                for i, vi in enumerate(vs):
                    vt = ""
                    if uvs and i < len(uvs):
                        key = uvs[i]
                        if key not in uv_index:
                            uv_index[key] = next_vt
                            next_vt += 1
                        vt = "/%d" % uv_index[key]
                    parts.append("%d%s" % (vi + 1, vt))
                face_lines.append("f %s\n" % " ".join(parts))
        for (u, v), idx in sorted(uv_index.items(), key=lambda x: x[1]):
            f.write("vt %.6f %.6f\n" % (u / 255.0, 1.0 - v / 255.0))
        for line in face_lines:
            f.write(line)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a Driver 2 .LEV or a directory of them")
    ap.add_argument("-o", "--out", default="out/d2cars", help="output directory")
    ap.add_argument("--all", action="store_true", help="process every .LEV in a directory")
    ap.add_argument("--slots", default="0-12",
                    help="model slots to extract (e.g. 0-4 or 1,2,3)")
    args = ap.parse_args()

    if os.path.isdir(args.path) and args.all:
        levs = [os.path.join(args.path, f) for f in os.listdir(args.path)
                if f.upper().endswith(".LEV")]
    else:
        levs = [args.path]

    os.makedirs(args.out, exist_ok=True)
    slots = []
    if "-" in args.slots:
        a, b = args.slots.split("-")
        slots = list(range(int(a), int(b) + 1))
    else:
        slots = [int(x) for x in args.slots.split(",")]

    count = 0
    for lev in levs:
        with open(lev, "rb") as f:
            blob = f.read()
        got = scalemodels.d2_car_lump(blob)
        if not got:
            print("no car models in %s" % lev)
            continue
        body, _ = got
        city = os.path.splitext(os.path.basename(lev))[0]
        for slot in slots:
            model = extract_model(blob, body, slot)
            if model is None or not model.vertices:
                continue
            name = "%s_model%d" % (city, slot)
            path = os.path.join(args.out, name + ".obj")
            write_obj(path, model, name)
            print("  %s  (%d verts, %d polys)" % (path, len(model.vertices), len(model.polys)))
            count += 1
    print("wrote %d models to %s" % (count, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
