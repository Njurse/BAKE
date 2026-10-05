#!/usr/bin/env python3
"""bake.py - bake pooled Driver 1 shapes into an engine-shaped car-data blob.

Phase 5 of the d1cars roadmap. The blob is what carhacks' cross-city import
already reads, so no new loader is needed (see docs/DRIVER1.md section 4).

Emitted file layout (measured off the engine; models.c LoadCarImport /
ProcessCarModelLump / FindLumpSegment):

    offset 0     LUMP {u32 type, u32 size}        - skipped, the table follows
    offset 8     table[8]: eight u32, [0] = DATA1 byte offset, [1] = its size
    DATA1        a container LUMP whose SEGMENTS start at +8:
       LUMP_CAR_MODELS (28)   the geometry, this bake's whole payload
    (DATA1 is zero-padded to a 2048 multiple so the engine's pageBase lands at
     the end of the file - there are no texture pages to read.)

The car-models segment body:

    +0    int32 model_count
    +4    13 x 3 int32 offsets (clean, damaged, low; -1 absent), relative +164
    +164  per model: MODEL(36) + SVECTOR vertices + the poly stream

BLOCKOUT: the poly encoder is now full GT3/GT4 - each record carries the source
(texture set, texture id, u, v) AND the normal indices, and every model ships
both normal tables, so a baked car is smooth-shaded rather than faceted. Only
the texture PAGES are still absent (Phase C). Vertices are scaled by the
D1->D2 cell ratio.

Usage:
    python bake.py --pool DIR/pool.json [--out DIR] [--first N]
"""

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LUMP_CAR_MODELS = 28
LUMP_CAR_DATA = 35          # the DATA1 container's own type
LUMP_DESC = 37
MAX_CAR_MODELS = 13
DATA1_OFF = 2048            # as in the real level files
MODEL_HDR = 36              # sizeof(MODEL): 9 fields, 36 bytes
FLAT_COLOR = (0x7F, 0x7F, 0x7F)


def build_model_record(vertices, polys, face_normals, point_normals, scale):
    """One MODEL + vertices + BOTH normal tables + a GT3/GT4 poly stream.

    The table order is the models' own and is load-bearing: the offsets are
    cumulative, so vertices -> normals (one per poly) -> point_normals (the table
    the GT indices resolve in) -> the poly stream.
    """
    verts = [(int(round(x * scale)), int(round(y * scale)), int(round(z * scale)))
             for x, y, z in vertices]
    nv = len(verts)
    fnorm = [tuple(n) for n in (face_normals or [])]
    pnorm = [tuple(n) for n in (point_normals or [])]
    npol = len(polys)

    stream = bytearray()
    npolys = 0
    for k, p in enumerate(polys):
        v = list(p["v"])
        uv = [tuple(u) for u in p.get("uv", [])]
        ns = list(p.get("n") or [])
        setid = p.get("set", 0) & 0xFF
        texid = p.get("id", 0) & 0xFF
        col = tuple(p.get("color", FLAT_COLOR))
        if len(v) == 3:
            rec = bytearray(24)
            rec[0] = 22                                  # GT3
            rec[1], rec[2] = setid, texid
            rec[4:7] = bytes(x & 0xFF for x in v[:3])
            rec[8:11] = bytes((ns[i] if i < len(ns) else 0) & 0xFF for i in range(3))
            for i in range(3):
                u = uv[i] if i < len(uv) else (0, 0)
                rec[12 + i * 2] = u[0] & 0xFF
                rec[13 + i * 2] = u[1] & 0xFF
            rec[20:23] = bytes(col)
            stream += rec
            npolys += 1
        elif len(v) == 4:
            rec = bytearray(24)
            rec[0] = 23                                  # GT4
            rec[1], rec[2] = setid, texid
            rec[4:8] = bytes(x & 0xFF for x in v[:4])
            rec[8:12] = bytes((ns[i] if i < len(ns) else 0) & 0xFF for i in range(4))
            for i in range(4):
                u = uv[i] if i < len(uv) else (0, 0)
                rec[12 + i * 2] = u[0] & 0xFF
                rec[13 + i * 2] = u[1] & 0xFF
            rec[20:23] = bytes(col)
            stream += rec
            npolys += 1

    vofs = MODEL_HDR
    nofs = vofs + nv * 8
    pnoffs = nofs + len(fnorm) * 8
    poly_block = pnoffs + len(pnorm) * 8

    bound = 0
    if verts:
        bound = int(max(max(abs(x), abs(y), abs(z)) for x, y, z in verts)) + 1

    hdr = struct.pack("<HHhBBhHHHiiiii",
                      0,                 # shape_flags (0 = no flags; >0x800000 bails)
                      0,                 # flags2
                      0,                 # instance_number
                      0,                 # tri_verts
                      0,                 # zBias
                      bound,             # bounding_sphere
                      len(pnorm),        # num_point_normals
                      nv,                # num_vertices
                      npolys,            # num_polys
                      vofs, poly_block, nofs, pnoffs,
                      0)                 # collision_block
    out = bytearray(hdr)
    for x, y, z in verts:
        out += struct.pack("<hhhh", x, y, z, 0)
    for n in fnorm:
        out += struct.pack("<hhhh", n[0], n[1], n[2], 0)
    for n in pnorm:
        out += struct.pack("<hhhh", n[0], n[1], n[2], 0)
    out += stream
    return bytes(out)


def build_damaged_record(vertices, scale, clean_size):
    """A damaged block is MODEL + its own vertices ONLY (its polys are the clean
    model's). lev1.py measures block size == 36 + nv*8 for every real one."""
    verts = [(int(round(x * scale)), int(round(y * scale)), int(round(z * scale)))
             for x, y, z in vertices]
    nv = len(verts)
    bound = 0
    if verts:
        bound = int(max(max(abs(x), abs(y), abs(z)) for x, y, z in verts)) + 1
    hdr = struct.pack("<HHhBBhHHHiiiii",
                      0, 0, 0, 0, 0, bound, 0, nv, 0,
                      MODEL_HDR, clean_size, 0, 0, 0)
    out = bytearray(hdr)
    for x, y, z in verts:
        out += struct.pack("<hhhh", x, y, z, 0)
    return bytes(out)


def build_car_models_lump(items):
    """items: list of {'geom': <geom.json record>, 'scale': k}."""
    blocks = []          # (clean_bytes, dam_bytes)
    for it in items:
        g = it["geom"]
        clean = build_model_record(g["vertices"], g["polys"],
                                   g.get("face_normals"), g.get("point_normals"),
                                   it["scale"])
        dam = build_damaged_record(g["vertices"], it["scale"], len(clean))
        blocks.append((clean, dam))

    body = bytearray()
    body += struct.pack("<i", len(items))                     # model count @ +0
    entries_at = len(body)                                    # +4
    body += b"\x00" * (MAX_CAR_MODELS * 12)                   # 13 x 3 int32
    body += b"\x00" * (164 - len(body))                       # pad to +164

    offsets = []
    for clean, dam in blocks:
        clean_ofs = len(body) - 164
        body += clean
        dam_ofs = len(body) - 164
        body += dam
        offsets.append((clean_ofs, dam_ofs, -1))

    for i, (c, d, l) in enumerate(offsets):
        struct.pack_into("<3i", body, entries_at + i * 12, c, d, l)
    return bytes(body)


def build_file(items):
    car = build_car_models_lump(items)
    seg = struct.pack("<ii", LUMP_CAR_MODELS, len(car)) + car
    seg += b"\x00" * ((4 - len(seg) % 4) % 4)                 # 4-byte align

    data1 = struct.pack("<ii", LUMP_CAR_DATA, len(seg) + 8) + seg
    data1 += b"\x00" * ((2048 - len(data1) % 2048) % 2048)     # pad to 2048

    out = bytearray()
    out += struct.pack("<ii", LUMP_DESC, DATA1_OFF - 8)        # mimic the header
    table = [DATA1_OFF, len(data1)] + [0] * 6
    out += struct.pack("<8I", *table)
    out += b"\x00" * (DATA1_OFF - len(out))
    out += data1
    return bytes(out)


def verify(blob):
    """Re-read the blob the way the engine's D2 reader would (scalemodels')."""
    import scalemodels as sm
    got = sm.d2_car_lump(blob)
    if not got:
        return "no car-models segment found"
    body, size = got
    n = 0
    for m in range(MAX_CAR_MODELS):
        st = sm.d2_model_stats(blob, body, m)
        if st:
            n += 1
            if st["npol"] == 0 or st["nv"] == 0:
                return "model %d decoded but is empty" % m
    return None if n else "no models decoded"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True, help="a pool.json from d1pool.py")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "out", "bake"),
                    help="output directory (gitignored)")
    ap.add_argument("--first", type=int, default=MAX_CAR_MODELS,
                    help="how many pool items to bake (max %d)" % MAX_CAR_MODELS)
    ap.add_argument("--name", default="D1POOL.LEV", help="output file name")
    args = ap.parse_args(argv)

    index = json.load(open(args.pool))
    keep = index["items"][:args.first]
    if not keep:
        print("pool has no items")
        return 1

    # the pool items carry geometry in their geom.json, not inline (pool.json is
    # the index) - load it
    items = []
    pooldir = os.path.dirname(args.pool)
    for it in keep:
        g = json.load(open(os.path.join(pooldir, it["geom"])))
        items.append({"pool_index": it["pool_index"], "geom": g,
                      "scale": g["scale"]})

    blob = build_file(items)

    if not os.path.isdir(args.out):
        os.makedirs(args.out)
    path = os.path.join(args.out, args.name)
    with open(path, "wb") as f:
        f.write(blob)

    print("baked %d shapes -> %s (%d bytes)" % (len(items), path, len(blob)))
    for n, it in enumerate(items):
        g = it["geom"]
        print("   model %2d <- pool %2d (%s): %d verts, %d tris"
              % (n, it["pool_index"], g["sources"][0][0], g["verts"], g["tris"]))
    problem = verify(blob)
    print("   self-check: %s" % (problem if problem else "the blob re-reads cleanly"))
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
