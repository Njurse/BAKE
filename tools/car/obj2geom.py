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


def parse_obj(path):
    verts = []
    uvs = []
    faces = []          # list of (v_indices, uv_indices)  (0-based)
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
            elif parts[0] == "f":
                vi, ti = [], []
                for tok in parts[1:]:
                    bits = tok.split("/")
                    vi.append(int(bits[0]) - 1)
                    ti.append(int(bits[1]) - 1 if len(bits) > 1 and bits[1] else -1)
                if len(vi) >= 3:
                    faces.append((vi, ti))
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


def build_geom(verts, uvs, faces, scale, force_grey, name):
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
    for vi, ti in faces:
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
            setid, texid = 1, 0
        else:
            uv_list = [[0, 0]] * len(vi)
            setid, texid = 1, 0          # solid grey page (non-zero)

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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("obj", help="input .obj")
    ap.add_argument("-o", "--out", help="output geom.json (default: <obj>_geom.json)")
    ap.add_argument("--scale", type=float, default=0.68267,
                    help="bake scale (D2_CELL/D1_CELL, default 0.68267)")
    ap.add_argument("--grey", action="store_true",
                    help="force every poly onto a solid grey page")
    args = ap.parse_args()

    verts, uvs, faces = parse_obj(args.obj)
    if not verts or not faces:
        sys.exit("no vertices or faces in %s" % args.obj)

    name = os.path.splitext(os.path.basename(args.obj))[0]
    rec = build_geom(verts, uvs, faces, args.scale, args.grey, name)

    out = args.out or (os.path.splitext(args.obj)[0] + "_geom.json")
    with open(out, "w") as f:
        json.dump(rec, f, indent=1)
    print("wrote %s  (%d verts, %d polys, scale %.5f%s)"
          % (out, len(verts), len(rec["polys"]), args.scale,
             ", grey fallback" if args.grey else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
