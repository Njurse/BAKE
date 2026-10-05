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
LUMP_PALLET = 25
LUMP_TEXTUREINFO = 34
LUMP_CAR_DATA = 35          # the DATA1 container's own type
LUMP_DESC = 37
MAX_CAR_MODELS = 13
DATA1_OFF = 2048            # as in the real level files
MODEL_HDR = 36              # sizeof(MODEL): 9 fields, 36 bytes
FLAT_COLOR = (0x7F, 0x7F, 0x7F)

# Where the source levels live; set from --levels. Driver 1's data is gitignored
# / local-only, so nothing here may hard-code a machine path.
LEVELS_DIR = "src_rebuild/bin/Release_dev/DRIVER/LEVELS"


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


def build_textureinfo(page_list):
    """LUMP_TEXTUREINFO for the pool, in the layout ProcessTextureInfo and
    ParseImportedTextureInfoForCity both walk:

        [int tpage_amount][int texamount]
        [TP tpage_position[tpage_amount + 1]]
        per page: [int texamount][TEXINF x texamount]
        [int nperms][XYPAIR permlist[16-slot reserved region]]
        [int nspecpages][XYPAIR speclist[nspecpages]]

    tpage_amount is 0: the pool's pages need no TEXINF detail lists, because the
    bake has no name table. The generated sets are the permlist.
    """
    body = bytearray()
    body += struct.pack("<ii", 0, 0)                 # tpage_amount, texamount
    body += b"\x00" * 8 * (0 + 1)                    # tpage_position[tpage_amount+1]
    body += struct.pack("<i", len(page_list))        # nperms
    for new_set, _lvl, _old, _bytes, size in page_list:
        body += struct.pack("<ii", new_set, size)    # XYPAIR {int x; int y}
    body += b"\x00" * (16 * 8)                       # the fixed 16-entry region
    body += struct.pack("<i", 0)                     # nspecpages
    return bytes(body)


def build_pallet(page_list, levels_dir):
    """LUMP_PALLET for the pool, in the ENGINE's record shape.

    Note the direction of translation: Driver 1's own lump uses a 44-byte record
    (three ints plus an inline CLUT, count exact, no terminator), but this lump
    is read by the engine's ProcessPalletLumpForRows, which wants Driver 2's
    shape - four ints whose fourth is clut_number, -1 selecting the inline form,
    the run ended by a lone -1. So the bake converts rather than copies.
    """
    import pages1

    body = bytearray()
    body += struct.pack("<i", 0)                     # total_cluts: a CLUT count
    count = 0
    for new_set, level, old_set, _bytes, _size in page_list:
        blob = open(os.path.join(levels_dir, level + ".LEV"), "rb").read()
        ofs, entries, _ti = pages1.level_pages(blob)
        ent = {s: (o, z) for s, o, z in entries}
        if old_set not in ent:
            continue
        o, z = ent[old_set]
        rows, _page = pages1.read_page(blob, o, z)
        for k, row in enumerate(rows):
            body += struct.pack("<4i", k, k, new_set, -1)   # palette, texnum, tpage, inline
            body += row
            count += 1
    body += struct.pack("<i", -1)                    # the lone terminator
    struct.pack_into("<i", body, 0, count)           # total_cluts := rows written
    return bytes(body)


def build_page_plan(items, levels_dir):
    """Assign every distinct (source level, texture set) a fresh set number.

    Set numbers are per-level, so the same number means a different page in
    another level - a pool that flattens seven levels must renumber, or two
    cities' cars would share a page. Returns (remap, page_list).
    """
    import pages1

    remap = {}
    page_list = []
    for it in items:
        g = it["geom"]
        level = g["sources"][0][0]
        for p in g["polys"]:
            if p["kind"] not in ("FT3", "FT4", "GT3", "GT4"):
                continue
            key = (level, p["set"])
            if key in remap:
                continue
            blob = open(os.path.join(levels_dir, level + ".LEV"), "rb").read()
            ofs, entries, _ti = pages1.level_pages(blob)
            ent = {s: (o, z) for s, o, z in entries}
            if p["set"] not in ent:
                continue
            o, z = ent[p["set"]]
            entry = blob[o:o + z]
            new_set = len(page_list)
            remap[key] = new_set
            page_list.append((new_set, level, p["set"], entry, z))
    return remap, page_list


def build_file(items, page_list, levels_dir):
    car = build_car_models_lump(items)

    segs = bytearray()
    tex = build_textureinfo(page_list)
    segs += struct.pack("<ii", LUMP_TEXTUREINFO, len(tex)) + tex
    segs += b"\x00" * ((4 - len(segs) % 4) % 4)
    pal = build_pallet(page_list, levels_dir)
    segs += struct.pack("<ii", LUMP_PALLET, len(pal)) + pal
    segs += b"\x00" * ((4 - len(segs) % 4) % 4)

    seg = struct.pack("<ii", LUMP_CAR_MODELS, len(car)) + car
    seg += b"\x00" * ((4 - len(seg) % 4) % 4)
    seg += segs

    data1 = struct.pack("<ii", LUMP_CAR_DATA, len(seg) + 8) + seg
    data1 += b"\x00" * ((2048 - len(data1) % 2048) % 2048)     # pad to 2048

    out = bytearray()
    out += struct.pack("<ii", LUMP_DESC, DATA1_OFF - 8)        # mimic the header
    table = [DATA1_OFF, len(data1)] + [0] * 6
    out += struct.pack("<8I", *table)
    out += b"\x00" * (DATA1_OFF - len(out))
    out += data1
    # The permanent page data sits here: pageBase is
    # (data1Off/2048 + data1Size/2048)*2048 (models.c:391), i.e. the sector after
    # DATA1. Each entry is sector-aligned and they run in permlist order, which
    # is exactly how ProcessTextureInfo's callers carve them.
    for _new_set, _lvl, _old, entry, _size in page_list:
        out += entry
        out += b"\x00" * ((2048 - len(entry) % 2048) % 2048)
    return bytes(out)


def verify(blob):
    """Re-read the blob the way the engine's D2 reader would (scalemodels'), and
    check the normal half of it: every GT index must resolve inside the model's
    point_normals table, and the table must face the same way as the geometry.

    Returns (problem_or_None, stats).
    """
    import math
    import lev1
    import scalemodels as sm

    got = sm.d2_car_lump(blob)
    if not got:
        return "no car-models segment found", {}
    body, _size = got
    n = 0
    ngt = 0
    same = 0
    for m in range(MAX_CAR_MODELS):
        st = sm.d2_model_stats(blob, body, m)
        if not st:
            continue
        n += 1
        if st["npol"] == 0 or st["nv"] == 0:
            return "model %d decoded but is empty" % m, {}

        model = lev1.decode_model(blob, body + 164 +
                                  struct.unpack_from("<i", blob, body + 4 + m * 12)[0])
        if model.errors:
            return "model %d decoded with %d poly errors" % (m, len(model.errors)), {}
        if not model.normals:
            return "model %d ships no point_normals table" % m, {}
        table = model.normals
        for p in model.polys:
            if not p.n:
                continue
            ngt += 1
            if any(i >= len(table) for i in p.n):
                return "model %d: a GT normal index is outside the table" % m, {}
            ns = model.poly_normals(p)
            tx = sum(x[0] for x in ns) / len(ns)
            ty = sum(x[1] for x in ns) / len(ns)
            tz = sum(x[2] for x in ns) / len(ns)
            tl = math.sqrt(tx * tx + ty * ty + tz * tz) or 1.0
            g = model._geo_normal(p)
            if (tx * g[0] + ty * g[1] + tz * g[2]) / tl > 0:
                same += 1
    if not n:
        return "no models decoded", {}
    return None, {"models": n, "gt": ngt, "hemisphere": same}


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
    ap.add_argument("--levels", default=LEVELS_DIR,
                    help="directory of the source DRIVER/LEVELS/*.LEV files")
    args = ap.parse_args(argv)
    levels_dir = args.levels

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

    # One set number means a different page in another level, so a pool that
    # flattens seven levels has to renumber - otherwise two cities' cars would
    # share a page. Do it before anything reads the polys.
    remap, page_list = build_page_plan(items, levels_dir)
    for it in items:
        level = it["geom"]["sources"][0][0]
        for p in it["geom"]["polys"]:
            key = (level, p["set"])
            if key in remap:
                p["set"] = remap[key]

    blob = build_file(items, page_list, levels_dir)

    if not os.path.isdir(args.out):
        os.makedirs(args.out)
    path = os.path.join(args.out, args.name)
    with open(path, "wb") as f:
        f.write(blob)

    print("baked %d shapes -> %s (%d bytes)" % (len(items), path, len(blob)))
    for n, it in enumerate(items):
        g = it["geom"]
        print("   model %2d <- pool %2d (%s): %d verts, %d tris, normals %s"
              % (n, it["pool_index"], g["sources"][0][0], g["verts"], g["tris"],
                 g.get("normals_source")))
    print("   texture sets: %d, from %d source levels"
          % (len(page_list), len({lvl for _s, lvl, _o, _b, _z in page_list})))
    problem, stats = verify(blob)
    if stats:
        print("   normals: %d GT polys, table faces the geometry in %.1f%%"
              % (stats["gt"], 100.0 * stats["hemisphere"] / max(stats["gt"], 1)))
    print("   self-check: %s" % (problem if problem else "the blob re-reads cleanly"))
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
