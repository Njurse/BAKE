#!/usr/bin/env python3
"""obj2geom.py — convert a Wavefront OBJ into a d1cars geom.json.

The output matches the record d1pool.py writes and bake.py reads:
  vertices, polys (kind/set/id/v/n/uv/color), face_normals, point_normals, scale.

Usage:
    python obj2geom.py car.obj -o car_geom.json
    python obj2geom.py car.obj -o car_geom.json --scale 0.68267 --grey

Faces without UVs (or when --grey is given) are emitted as solid grey
GT3/GT4 so they do not pull garbage into VRAM.
"""

import argparse
import json
import os
import sys


def parse_obj(path, with_materials=False):
    """(verts, uvs, faces) - plus a per-face material name when asked.

    The material name is what carries a poly's texture page: carbank_export
    writes "set<S>_id<T>" per (set, id) group and "flat" for untextured panels,
    so reading it back is what keeps an edited OBJ's materials intact instead of
    collapsing every poly onto one grey page.
    """
    verts = []
    uvs = []
    faces = []          # list of (v_indices, uv_indices)  (0-based)
    mats = []           # per-face material name ("" when the OBJ names none)
    current = ""
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "v" and len(parts) >= 4:
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "vt" and len(parts) >= 3:
                uvs.append((float(parts[1]), float(parts[2])))
            elif parts[0] == "usemtl":
                current = parts[1] if len(parts) > 1 else ""
            elif parts[0] == "f":
                vi, ti = [], []
                for tok in parts[1:]:
                    bits = tok.split("/")
                    vi.append(int(bits[0]) - 1)
                    ti.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
                if len(vi) >= 3:
                    faces.append((vi, ti))
                    mats.append(current)

    if with_materials:
        return verts, uvs, faces, mats

    return verts, uvs, faces


def face_normal(verts, idxs):
    a, b, c = (verts[i] for i in idxs[:3])
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx = uy * vz - uz * vy
    ny = uz * vx - ux * vz
    nz = ux * vy - uy * vx
    L = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
    return (nx / L, ny / L, nz / L)


def build_geom(verts, uvs, faces, scale, force_grey, name, mats=None):
    # point normals: average of face normals meeting at each vertex
    acc = [[0.0, 0.0, 0.0] for _ in verts]
    face_n = []
    for vi, _ in faces:
        n = face_normal(verts, vi)
        face_n.append([int(round(c * 4096)) for c in n])
        for i in vi:
            acc[i][0] += n[0]
            acc[i][1] += n[1]
            acc[i][2] += n[2]
    point_n = []
    for a in acc:
        L = (a[0] * a[0] + a[1] * a[1] + a[2] * a[2]) ** 0.5 or 1.0
        point_n.append([int(round(a[0] / L * 4096)),
                        int(round(a[1] / L * 4096)),
                        int(round(a[2] / L * 4096))])

    polys = []
    for fi, (vi, ti) in enumerate(faces):
        page = mats[fi] if mats else None
        has_uv = (not force_grey) and any(t >= 0 for t in ti) and uvs
        kind = "GT3" if len(vi) == 3 else "GT4"
        uv_list = []
        if has_uv:
            for t in ti:
                if 0 <= t < len(uvs):
                    # OBJ UV is 0..1; engine expects 0..255-ish. Scale simply.
                    u, v = uvs[t]
                    uv_list.append([int(round(u * 255)) & 0xFF,
                                    int(round((1.0 - v) * 255)) & 0xFF])
                else:
                    uv_list.append([0, 0])
            setid, texid = (page if page else (1, 0))
        else:
            uv_list = [[0, 0]] * len(vi)
            setid, texid = (page if page else (1, 0))   # solid grey page

        polys.append({
            "kind": kind,
            "set": setid,
            "id": texid,
            "v": list(vi),
            "n": list(vi),               # index point normals by vertex
            "uv": uv_list,
            "color": [127, 127, 127],
        })

    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return {
        "version": 1,
        "pool_index": 0,
        "sources": [["custom", 0, "clean"]],
        "name": name,
        "verts": len(verts),
        "tris": sum(1 if len(f[0]) == 3 else 2 for f in faces),
        "bbox_raw": [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)],
        "bounding_sphere": int(max(max(abs(x), abs(y), abs(z)) for x, y, z in verts)) + 1,
        "scale": scale,
        "normals_source": "synthesised",
        "face_normals": face_n,
        "point_normals": point_n,
        "vertices": [list(v) for v in verts],
        "polys": polys,
    }


MATERIAL_PREFIX = "set"


def material_to_page(name, where, default=None, unmapped=None):
    """'set65_id12' -> (65, 12); 'flat' or '' -> None (solid grey).

    A name this tool does not recognise is an error rather than a silent grey
    page: a typo in a material would otherwise quietly put a wing on the wrong
    texture.
    """
    if name in ("", "flat"):
        return None

    if not name.startswith(MATERIAL_PREFIX):
        # An author's OBJ (Blender and friends) names its materials its own way.
        # Without an explicit --default-page that is a refusal, because guessing
        # a page puts a body panel on the wrong texture; with one it is taken and
        # the name is reported.
        if default is not None:
            if unmapped is not None:
                unmapped.append(name)

            return default

        raise ValueError("%s: material %r is neither \"flat\" nor "
                         "set<number>_id<number> (pass --default-page "
                         "set:id to map author material names)" % (where, name))

    try:
        body = name[len(MATERIAL_PREFIX):]
        set_txt, id_txt = body.split("_id")
        return (int(set_txt), int(id_txt))
    except ValueError:
        raise ValueError("%s: material %r is not of the form set<S>_id<T>"
                         % (where, name))


def materials_to_pages(names, where, default=None, unmapped=None):
    """Per-face (set, id) or None, from the OBJ's usemtl groups."""
    cache = {}
    out = []

    for name in names:
        if name not in cache:
            cache[name] = material_to_page(name, where, default, unmapped)

        out.append(cache[name])

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obj", help="input .obj")
    ap.add_argument("-o", "--out", help="output geom.json (default: <obj>_geom.json)")
    ap.add_argument("--scale", type=float, default=0.68267,
                    help="bake scale (D2_CELL/D1_CELL, default 0.68267)")
    ap.add_argument("--grey", action="store_true",
                    help="force every poly onto a solid grey page")
    ap.add_argument("--materials", action="store_true",
                    help="take each poly's set/id from its usemtl group "
                         "(set<S>_id<T>; anything else is refused)")
    args = ap.parse_args()

    if args.materials:
        verts, uvs, faces, names = parse_obj(args.obj, with_materials=True)
        mats = materials_to_pages(names, args.obj)
    else:
        verts, uvs, faces = parse_obj(args.obj)
        mats = None
    if not verts or not faces:
        sys.exit("no vertices or faces in %s" % args.obj)

    name = os.path.splitext(os.path.basename(args.obj))[0]
    rec = build_geom(verts, uvs, faces, args.scale, args.grey, name, mats)

    out = args.out or (os.path.splitext(args.obj)[0] + "_geom.json")
    with open(out, "w") as f:
        json.dump(rec, f, indent=1)
    print("wrote %s  (%d verts, %d polys, scale %.5f%s)"
          % (out, len(verts), len(rec["polys"]), args.scale,
             ", grey fallback" if args.grey else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
