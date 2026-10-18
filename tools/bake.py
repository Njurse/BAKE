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

import d1pool                 # source_file(): a city is not always named after its file
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
# A segment Driver 2 itself never writes. It carries the source city's wheel
# models beside its car models, so an imported D1 car can wear D1 wheels instead
# of the host level's shared CLEANWHEEL/FASTWHEEL/DAMWHEEL. Unknown segment
# types are skipped by the engine's other walks, so this is additive.
LUMP_CAR_WHEELS = 200
MAX_CAR_MODELS = 13
WHEEL_ORDER = ("CLEANWHEEL", "DAMWHEEL", "FASTWHEEL")

# Driver 2's COMPLIANT model numbers, in the order a transplant fills them: the
# four its frontend actually offers first (carNumLookup, FEmain.c:639, maps
# select-slots 0..3 onto models 1,2,3,4), then the extras its own cities ship.
#
# MEASURED: the four Driver 2 cities hold models 0,1,2,3,4,8,9,10,11,12 and NOTHING
# else (CHICAGO ships 9, the rest 10). Model 0 is the police car, at a select slot
# the stock frontend keeps locked, so a transplant leaves it alone.
D2_COMPLIANT_MODELS = (1, 2, 3, 4, 8, 9, 10, 11, 12)

# Models NO Driver 2 city holds data for. A car written at one of these resolves
# nothing on the engine side and the import silently falls back. Driver 1's own
# cars sit on exactly these numbers (5, 6 and 7 carry 17 of the pool's 43 shapes),
# which is the whole reason the bake remaps instead of writing at D1's numbers.
D2_GAP_MODELS = (5, 6, 7)
# The first set number a bake may hand out. texture_set 0 is the engine's "no
# page" sentinel (the pin walk skips it and CarSetRemap sends it to a host slot),
# so a car page must never be numbered 0 - see build_page_plan.
PAGE_SET_BASE = 1
DATA1_OFF = 2048            # as in the real level files
MODEL_HDR = 36              # sizeof(MODEL): 9 fields, 36 bytes
FLAT_COLOR = (0x7F, 0x7F, 0x7F)


def _flat_555(rgb8):
    """8-bit (r,g,b) -> a PSX RGB555 word (STP bit clear, so opaque)."""
    r, g, b = rgb8
    return ((r >> 3) & 0x1F) | (((g >> 3) & 0x1F) << 5) | (((b >> 3) & 0x1F) << 10)


def _flat_clut_row(rgb8):
    """One 32-byte CLUT row whose every entry is the same opaque colour.

    A solid-colour page is filled with index 0, so only entry 0 matters, but
    filling all 16 guards against a UV accidentally sampling another index and
    drawing transparent black instead.
    """
    return struct.pack("<16H", *([_flat_555(rgb8)] * 16))

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

    # Keep TEXTURED polys AND the flat F3/F4 polys. Driver 1's cabin glass and
    # interior are flat untextured polys carrying a colour, and the engine's car
    # reader has no flat path Driver 2 ever exercises. build_page_plan gives the
    # flat polys a synthesized solid-colour page (their `set`/`id` name the CLUT
    # row holding their colour), so they are re-encoded here as GT3/GT4 with a
    # NEUTRAL grey tint - the CLUT row supplies the real colour, and grey is what
    # a D1 GT poly carries so the texture is not darkened.
    kept_polys = []
    kept_fnorm = []
    for k, p in enumerate(polys):
        if p.get("kind") not in ("F3", "F4", "FT3", "FT4", "GT3", "GT4"):
            continue
        kept_polys.append(p)
        if face_normals and k < len(face_normals):
            kept_fnorm.append(face_normals[k])
    polys = kept_polys

    fnorm = [tuple(n) for n in kept_fnorm]
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
                      0x8001,            # shape_flags. CONSTANT in Driver 2: all 57 of
                                        # its records across CHICAGO and HAVANA read
                                        # 32769, and our 0 was the last field that
                                        # differed on every variant. Not guessed - measured.
                                        # (tri_verts and npn DO vary there, including 0, so
                                        # ours are left alone.)
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


def build_damaged_record(vertices, scale, clean):
    """A damaged block is MODEL + its own vertices ONLY - its polys are the clean
    model's, which is why num_polys and poly_block must be CARRIED OVER from it
    rather than zeroed. Zeroing them does not fail a load: the block sits inside
    the lump, the offsets are valid, and the engine then builds a car whose
    damaged model has no polygons at all - which is an access violation, not an
    error. Measured against Driver 2's own damaged blocks: num_polys equals the
    clean model's there (248 for CHICAGO model 1), while ours read 0."""
    verts = [(int(round(x * scale)), int(round(y * scale)), int(round(z * scale)))
             for x, y, z in vertices]
    nv = len(verts)
    bound = 0
    if verts:
        bound = int(max(max(abs(x), abs(y), abs(z)) for x, y, z in verts)) + 1
    # EVERY structural field comes from the clean block; only the four the dented
    # vertices own are replaced. Writing zeros here is what crashed: the offsets
    # stayed in range so the load succeeded, and the engine then read a normals
    # table at offset 0. Driver 2's own damaged blocks keep the clean's nofs and
    # pnoffs (1052 and 3036 for CHICAGO model 1) and their real flags (32769) and
    # tri_verts (3); ours had all four as zero.
    (flags, flags2, inst, tri_verts, pad, _cbound, _cnpn, _cnv, npol,
     vofs, pblock, nofs, pnoffs, _last) = struct.unpack_from("<HHhBBhHHHiiiii", clean, 0)
    hdr = struct.pack("<HHhBBhHHHiiiii",
                      flags, flags2, inst, tri_verts, pad, bound, 0, nv, npol,
                      vofs, pblock, nofs, pnoffs, _last)
    out = bytearray(hdr)
    for x, y, z in verts:
        out += struct.pack("<hhhh", x, y, z, 0)
    return bytes(out)


def build_car_models_lump(items):
    """items: list of {'geom': <geom.json record> or None, 'scale': k}.

    A None geom is a slot the city has no car in. It still occupies its position -
    the .LCF's record for that slot has to line up with it - and gets -1 offsets,
    which is what an empty slot looks like in Driver 2's own files (and what
    lev1.decode_model tests for).
    """
    blocks = []          # (clean_bytes, dam_bytes) or None
    for it in items:
        g = it["geom"]
        if g is None:
            blocks.append(None)
            continue
        clean = build_model_record(g["vertices"], g["polys"],
                                   g.get("face_normals"), g.get("point_normals"),
                                   it["scale"])
        dam = build_damaged_record(g["vertices"], it["scale"], clean)
        # A THIRD block is not optional, however little it is used: the engine's
        # GetCarImportModels rejects a model outright unless all three offsets are
        # present AND strictly increasing (clean < damaged < low). Writing only two
        # is why every transplanted city reported "has no such model" for cars its
        # blob demonstrably held. Driver 1's own low-detail model is not carried
        # through the bake yet, so the clean record is repeated for it - the car is
        # then undamaged at distance rather than dented, which is the safer way to
        # be wrong.
        blocks.append((clean, dam, clean))

    body = bytearray()
    body += struct.pack("<i", len(items))                     # model count @ +0
    entries_at = len(body)                                    # +4
    body += b"\x00" * (MAX_CAR_MODELS * 12)                   # 13 x 3 int32
    body += b"\x00" * (164 - len(body))                       # pad to +164

    offsets = []
    for blk in blocks:
        if blk is None:
            offsets.append((-1, -1, -1))
            continue
        clean, dam, low = blk
        clean_ofs = len(body) - 164
        body += clean
        dam_ofs = len(body) - 164
        body += dam
        low_ofs = len(body) - 164
        body += low
        offsets.append((clean_ofs, dam_ofs, low_ofs))

    for i, (c, d, l) in enumerate(offsets):
        struct.pack_into("<3i", body, entries_at + i * 12, c, d, l)
    return bytes(body)


def build_wheel_record(geom, scale):
    """One wheel MODEL + vertices + an FT4 poly stream.

    Wheels are drawn by DrawWheelObject, which reads POLYFT4 records directly and
    never uses normals, so this deliberately differs from build_model_record:
    FT4 (20 bytes) instead of GT3/GT4 (24), and no normal tables. The returned
    block is exactly `poly_block` bytes - what GetCarModel copies.
    """
    verts = [(int(round(x * scale)), int(round(y * scale)), int(round(z * scale)))
             for x, y, z in geom["vertices"]]
    nv = len(verts)

    stream = bytearray()
    npolys = 0
    # Driver 1's untextured marker is set 255. A wheel still carries real UVs on
    # those quads (the edge-on faces), and the offline renderer draws them with
    # the wheel's own sprite. Do the same here: they share the wheel's textured
    # set, which is the only one the page plan can resolve.
    sprite_set = None
    for p in geom["polys"]:
        if p.get("kind") == "FT4" and p.get("set", 0) != 255:
            sprite_set = p.get("set", 0) & 0xFF
            break

    for p in geom["polys"]:
        if p.get("kind") != "FT4":
            raise SystemExit("wheel %s carries a %s poly; wheel records are FT4 only"
                             % (geom.get("name", "?"), p.get("kind")))
        v = list(p["v"])
        uv = [tuple(u) for u in (p.get("uv") or [(0, 0)] * 4)]
        setid = p.get("set", 0) & 0xFF
        if setid == 255 and sprite_set is not None:
            setid = sprite_set
        rec = bytearray(20)
        rec[0] = 21                                   # FT4
        rec[1] = setid
        rec[2] = p.get("id", 0) & 0xFF
        rec[4:8] = bytes(x & 0xFF for x in v[:4])
        for i in range(4):
            u = uv[i] if i < len(uv) else (0, 0)
            rec[8 + i * 2] = u[0] & 0xFF
            rec[9 + i * 2] = u[1] & 0xFF
        rec[16:19] = bytes(p.get("color", FLAT_COLOR))
        stream += rec
        npolys += 1

    vofs = MODEL_HDR
    poly_block = vofs + nv * 8
    bound = 0
    if verts:
        bound = int(max(max(abs(x), abs(y), abs(z)) for x, y, z in verts)) + 1

    hdr = struct.pack("<HHhBBhHHHiiiii",
                      0x8001,             # same shape_flags as the car records
                      0,                  # flags2
                      0,                  # instance_number
                      0,                  # tri_verts
                      0,                  # zBias
                      bound,              # bounding_sphere
                      0,                  # num_point_normals
                      nv,                 # num_vertices
                      npolys,             # num_polys
                      vofs, poly_block, 0, 0,
                      0)                  # collision_block
    out = bytearray(hdr)
    for x, y, z in verts:
        out += struct.pack("<hhhh", x, y, z, 0)
    out += stream
    return bytes(out)


def build_wheel_lump(wheels):
    """LUMP_CAR_WHEELS body: count, 3 offsets, then the wheel blocks.

    `wheels` is a 3-entry list of geom records (or None). Offsets are relative to
    the segment body start, and -1 means that variant is absent - the engine then
    keeps the host level's wheel for it.
    """
    if len(wheels) > MAX_CAR_MODELS:
        raise SystemExit("too many wheel variants")
    body = bytearray()
    body += struct.pack("<i", len(wheels))          # count @ +0
    offsets_at = len(body)
    body += b"\x00" * (len(wheels) * 4)             # offsets @ +4

    offsets = []
    for g in wheels:
        if g is None:
            offsets.append(-1)
            continue
        offsets.append(len(body))                   # relative to the segment body
        body += build_wheel_record(g, g.get("scale", d1pool.SCALE))

    for i, ofs in enumerate(offsets):
        struct.pack_into("<i", body, offsets_at + i * 4, ofs)
    return bytes(body)


def _wheel_is_car_scale(model, scale):
    """A wheel from a car level, not a menu/credits display asset.

    CREDITS and IVIEW name CLEANWHEEL/FASTWHEEL too, at radii 10-20x the car
    levels'. Taking one of those would fit a car with a wheel larger than itself.
    """
    if model is None or not model.vertices or not model.polys:
        return False
    import wheelgeom
    radius = wheelgeom.wheel_radius(model, scale)
    return 20.0 <= radius <= 120.0


def _wheel_slot(levels_dir, level, name, model):
    """The model-table slot a named wheel occupies in a level (0 when unknown)."""
    import wheels1
    blob = open(os.path.join(levels_dir, level + ".LEV"), "rb").read()
    for slot, n, _m in wheels1.wheels_in(blob):
        if n == name:
            return slot
    return 0


def collect_wheels(levels_dir, city=None):
    """The three wheel models an import blob should carry, in WHEEL_ORDER.

    A city's own wheels win when they are car-scale. A missing or display-scale
    variant falls back to wheelgeom.canonical_wheel() - the median car-scale wheel
    across the D1 cities - so MIAMI's missing DAMWHEEL and NEWCASTLE's CREDITS
    source still get a real D1 wheel instead of the host level's.

    Returns a 3-entry list of {'level','slot','name','geom'} or None per variant.
    """
    import lev1
    import wheels1
    import wheelgeom

    source = d1pool.source_file(city) if city else None
    own = {}
    if source:
        blob = open(os.path.join(levels_dir, source + ".LEV"), "rb").read()
        for slot, name, model in wheels1.wheels_in(blob):
            own[name] = (source, slot, model)

    out = []
    for name in WHEEL_ORDER:
        picked = None
        got = own.get(name)

        if got and _wheel_is_car_scale(got[2], d1pool.SCALE):
            picked = got
        else:
            level, model = wheelgeom.canonical_wheel(levels_dir, name)

            if level is not None and model is not None:
                picked = (level, _wheel_slot(levels_dir, level, name, model), model)

        if picked is None:
            out.append(None)
            continue

        level, slot, model = picked
        geom = wheels1.wheel_record(level, slot, name, model)
        geom["sources"] = [[level, slot]]
        geom["scale"] = d1pool.SCALE
        out.append({"level": level, "slot": slot, "name": name, "geom": geom})

    return out


def _geom_records(items, wheels):
    """Every geometry record a page plan has to see: cars first, then wheels."""
    out = []
    for it in items:
        if it.get("geom") is not None:
            out.append(it["geom"])
    for w in (wheels or []):
        if w is not None:
            out.append(w["geom"])
    return out


def apply_page_plan(items, wheels, remap, tex_remap, flat_set, flat_index):
    """Rewrite every car and wheel poly onto the baked set/id numbers."""
    for g in _geom_records(items, wheels):
        level = g["sources"][0][0]
        for p in g["polys"]:
            if p["kind"] in ("F3", "F4"):
                p["set"] = flat_set
                p["id"] = flat_index.get(tuple(p.get("color", (0, 0, 0))), 0)
                p["color"] = list(FLAT_COLOR)
                continue
            key = (level, p["set"])
            if key in remap:
                p["set"] = remap[key]
            if key in tex_remap:
                p["id"] = tex_remap[key].get(p["id"], p["id"])


def build_textureinfo(perms, specs):
    """LUMP_TEXTUREINFO for the pool, in the layout ProcessTextureInfo and
    ParseImportedTextureInfoForCity both walk:

        [int tpage_amount][int texamount]
        [TP tpage_position[tpage_amount + 1]]
        per page: [int texamount][TEXINF x texamount]
        [int nperms][XYPAIR permlist[16-slot reserved region]]
        [int nspecpages][XYPAIR speclist[nspecpages]]

    tpage_amount is 0: the pool's pages need no TEXINF detail lists, because the
    bake has no name table. The generated sets are the two lists.
    """
    body = bytearray()
    body += struct.pack("<ii", 0, 0)                 # tpage_amount, texamount
    body += b"\x00" * 8 * (0 + 1)                    # tpage_position[tpage_amount+1]
    body += struct.pack("<i", len(perms))            # nperms
    for new_set, _lvl, _old, _bytes, size in perms:
        body += struct.pack("<ii", new_set, size)    # XYPAIR {int x; int y}
    # The permanent list occupies a FIXED 16-entry region - the engine reaches the
    # special list via &permlist[16], not &permlist[nperms] (texture.c:1220), so
    # the slots nperms does not use are padding, not extra bytes.
    body += b"\x00" * (16 - len(perms)) * 8
    body += struct.pack("<i", len(specs))            # nspecpages
    for new_set, _lvl, _old, _bytes, size in specs:
        body += struct.pack("<ii", new_set, size)
    return bytes(body)


def build_pallet(page_list, levels_dir, remap, tex_remap):
    """LUMP_PALLET for the pool, in the ENGINE's record shape.

    Driver 1's own LUMP_PALLET is a 44-byte record (three ints + an inline CLUT,
    count exact, no terminator) carrying the COLOUR-VARIANT CLUTs - its palette
    field is already the 0..5 variety index. The engine's ProcessPalletLumpForRows
    wants Driver 2's shape: four ints whose fourth is clut_number, -1 the inline
    form, the run ended by a lone -1. So the bake translates rather than copies.

    The earlier version read the page's base CLUT rows (pages1.read_page) and wrote
    palette=k (the texture id, 0..32). The engine treats palette as a 0..4 variant
    index and clamps slot=palette+1 to 5, so every texture id >= 5 piled its CLUT
    into slot 5 and slots 1..4 stayed empty - the draw then fell back to a
    NEIGHBOURING texture's colours, which is exactly "some surfaces correct, some
    wrong". Reading D1's real pallet fixes the field and the source in one move.
    """
    import pages1

    body = bytearray()
    body += struct.pack("<i", 0)                     # total_cluts: a CLUT count
    count = 0

    by_level = {}
    for new_set, level, old_set, _bytes, _size in page_list:
        if level is None:                        # the synthesized flat-colour page
            continue
        by_level.setdefault(level, []).append((new_set, old_set))

    for level in by_level:
        blob = open(os.path.join(levels_dir, d1pool.source_file(level) + ".LEV"), "rb").read()
        pal = pages1.pallet_lump(blob)
        if not pal:
            continue
        pbody, psize = pal
        _n, recs = pages1.parse_pallet(pbody, psize)
        for r in recs:
            key = (level, r["tpageindex"])
            if key not in remap:
                continue                        # a set this city's cars do not use
            new_set = remap[key]
            texnum = r["texnum"]
            if key in tex_remap:
                texnum = tex_remap[key].get(texnum, texnum)
            body += struct.pack("<4i", r["palette"], texnum, new_set, -1)
            body += r["row"]
            count += 1

    # The flat-colour page: one palette record per CLUT row. Its colours are not
    # read from any source level, so emit them straight from the page entry the
    # planner synthesized.
    for new_set, level, old_set, entry, _size in page_list:
        if level is not None:
            continue
        npal = int.from_bytes(entry[:4], "little")
        for i in range(npal):
            body += struct.pack("<4i", 0, i, new_set, -1)
            body += entry[4 + i * 32:4 + i * 32 + 32]
            count += 1

    body += struct.pack("<i", -1)                    # the lone terminator
    struct.pack_into("<i", body, 0, count)           # total_cluts := rows written
    return bytes(body)


def _compact_page_entry(entry, texmap):
    """Reorder a page entry's CLUT rows so texture id `old` sits at row `new`.

    texmap is {old_id: new_id}. Rows not in the map are dropped; the decomp stream
    (the page image) that follows the rows is carried through unchanged - only the
    CLUT row ORDER changes. That is safe because the id selects the CLUT row only
    (cars.c:1075 texture_cluts[set][id]); the tile image comes from the poly's UVs.
    """
    npal = struct.unpack_from("<i", entry, 0)[0]
    rows = [entry[4 + i * 32:4 + i * 32 + 32] for i in range(npal)]
    page = entry[4 + npal * 32:]
    by_new = sorted((new, old) for old, new in texmap.items())
    out = struct.pack("<i", len(by_new))
    for _new, old in by_new:
        out += rows[old] if old < len(rows) else b"\x00" * 32
    out += page
    return out


def build_page_plan(items, levels_dir, wheels=None):
    """Assign every distinct (source level, texture set) a fresh set number.

    Set numbers are per-level, so the same number means a different page in
    another level - a pool that flattens seven levels must renumber, or two
    cities' cars would share a page.

    A set can live in EITHER of a level's two page lists, and the car pages are
    almost always the SPECIAL ones (FRISCO's car polys paint with sets 61..66 and
    3/4, its special list; only 67/68 are permanent). The engine resolves the two
    lists separately (gCarImportPerms vs gCarImportSpecs) and layers their page
    data one after the other, so the plan keeps them apart too.

    `wheels` (optional) adds the wheel models' pages to the same plan, so an
    imported car's wheels carry their own texture rather than borrowing the host
    level's shared wheel sprite.

    Also compacts each set's texture ids to 0..N-1 when a page carries an id the
    engine cannot hold: Driver 1 pages have up to 33 CLUT rows (texture ids
    0..32), but texture_cluts and civ_clut are [..][32], so id 32 reads out of
    bounds. The ids are remapped and the CLUT rows reordered to match (the tile
    image is untouched - it comes from the UVs). The blanket rule, not a one-off.

    SET NUMBERS START AT 1, NEVER 0. The engine reserves texture_set 0 as "no
    page": the pin walk skips it (texture.c `if (set == 0 ...) continue`) and
    CarSetRemap re-indexes it onto a HOST/world slot - so a car page numbered 0
    is never paged in and every poly on it draws the level's own page instead.
    That is the "the hood is invisible on car 1, and models 1 and 2 are half
    broken" report: the first used page of a bake took 0. Driver 2 never names a
    car page 0 for the same reason. PAGE_SET_BASE keeps the reservation.

    Returns (remap, tex_remap, perms, specs, (flat_set, flat_index)).
    """
    import pages1

    geoms = _geom_records(items, wheels)

    # First pass: every texture id each (level, set) actually uses, across all the
    # city's models, so the compaction is the union of what the cars reference.
    used_ids = {}
    for g in geoms:
        level = g["sources"][0][0]
        for p in g["polys"]:
            if p["kind"] not in ("FT3", "FT4", "GT3", "GT4"):
                continue
            used_ids.setdefault((level, p["set"]), set()).add(p["id"])

    tex_remap = {}
    for key, ids in used_ids.items():
        if max(ids) < 32:
            continue                    # already fits in the engine's 32 rows
        tex_remap[key] = {old: new for new, old in enumerate(sorted(ids))}

    remap = {}
    perms = []
    specs = []
    listed = {}
    for g in geoms:
        level = g["sources"][0][0]
        for p in g["polys"]:
            if p["kind"] not in ("FT3", "FT4", "GT3", "GT4"):
                continue
            key = (level, p["set"])
            if key in remap:
                continue
            if level not in listed:
                blob = open(os.path.join(levels_dir, d1pool.source_file(level) + ".LEV"), "rb").read()
                ofs, entries, ti = pages1.level_pages(blob)
                # The special pages carry no offset of their own: their data runs
                # on from the end of the permanent pages, sector-aligned, in
                # speclist order (LoadPermanentTPages loads the two lists in that
                # sequence). So build their offsets here.
                off = sum((z + 2047) & ~2047 for _s, _o, z in entries)
                spec = {}
                for s, z in (ti["speclist"] if ti else []):
                    spec[s] = (ofs + off, z)
                    off += (z + 2047) & ~2047
                listed[level] = (blob, {s: (o, z) for s, o, z in entries}, spec)
            blob, perm, spec = listed[level]
            if p["set"] in perm:
                o, z = perm[p["set"]]
                target = perms
            elif p["set"] in spec:
                o, z = spec[p["set"]]
                target = specs
            else:
                continue
            remap[key] = PAGE_SET_BASE + len(perms) + len(specs)
            entry = blob[o:o + z]
            tm = tex_remap.get(key)
            if tm:
                entry = _compact_page_entry(entry, tm)
            target.append((remap[key], level, p["set"], entry, len(entry)))

    # Flat F3/F4 polys (the cabin glass/interior) get ONE synthesized solid-colour
    # page: its CLUT holds one row per distinct flat colour, the page image is all
    # index 0, and each flat poly's `id` selects its colour's row. It rides in the
    # permanent list with `level is None`, which build_pallet keys on to emit the
    # flat colours' palette records directly (there is no source level to read).
    flat_colours = []
    flat_index = {}
    for g in geoms:
        for p in g["polys"]:
            if p["kind"] not in ("F3", "F4"):
                continue
            c = tuple(p.get("color", (0, 0, 0)))
            if c not in flat_index:
                flat_index[c] = len(flat_colours)
                flat_colours.append(c)

    flat_set = 0
    if flat_colours:
        if len(flat_colours) > 32:
            raise SystemExit("a city's flat polys use %d distinct colours; the "
                             "engine holds 32 CLUT rows" % len(flat_colours))
        flat_set = PAGE_SET_BASE + len(perms) + len(specs)
        rows = b"".join(_flat_clut_row(c) for c in flat_colours)
        page = pages1.encode_literal(b"\x00" * 0x8000)      # 256x256 4bpp, all index 0
        entry = struct.pack("<i", len(flat_colours)) + rows + page
        perms.append((flat_set, None, None, entry, len(entry)))

    return remap, tex_remap, perms, specs, (flat_set, flat_index)


def build_file(items, perms, specs, levels_dir, remap, tex_remap, wheels=None):
    car = build_car_models_lump(items)

    segs = bytearray()
    tex = build_textureinfo(perms, specs)
    segs += struct.pack("<ii", LUMP_TEXTUREINFO, len(tex)) + tex
    segs += b"\x00" * ((4 - len(segs) % 4) % 4)
    pal = build_pallet(perms + specs, levels_dir, remap, tex_remap)
    segs += struct.pack("<ii", LUMP_PALLET, len(pal)) + pal
    segs += b"\x00" * ((4 - len(segs) % 4) % 4)

    seg = struct.pack("<ii", LUMP_CAR_MODELS, len(car)) + car
    seg += b"\x00" * ((4 - len(seg) % 4) % 4)

    # The optional wheel models ride beside the car models. An engine that does
    # not know this type skips it by its size, exactly like any other segment.
    wheel_geoms = [w["geom"] if w is not None else None for w in (wheels or [])]
    if any(g is not None for g in wheel_geoms):
        wheel = build_wheel_lump(wheel_geoms)
        seg += struct.pack("<ii", LUMP_CAR_WHEELS, len(wheel)) + wheel
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
    for _new_set, _lvl, _old, entry, _size in perms + specs:
        # A D1 page entry is [int npalettes][npalettes*32-byte CLUT rows][decomp_asm
        # stream]. Driver 1 legitimately ships pages with 33 rows, but the engine's
        # texture_cluts table holds 32 (texture.c texture_cluts[128][32]), so a 33rd
        # row would be rejected as corrupt and leave the whole set untextured - the
        # "NEWCASTLE set 4 looks corrupt (33)" failure. Cap at 32 by dropping the last
        # row; the decomp stream that follows is unchanged.
        npal = int.from_bytes(entry[:4], "little")
        if npal > 32:
            entry = struct.pack("<I", 32) + entry[4:4 + 32 * 32] + entry[4 + npal * 32:]
        out += entry
        out += b"\x00" * ((2048 - len(entry) % 2048) % 2048)
    return bytes(out)


def _data1_segment(blob, want):
    """(body_offset, size) of one DATA1 segment, or (None, 0)."""
    d1_off = struct.unpack_from("<I", blob, 8)[0]
    off = d1_off + 8
    while off + 8 <= len(blob):
        t, s = struct.unpack_from("<ii", blob, off)
        if t == 0xFF or s < 0 or off + 8 + s > len(blob):
            break
        if t == want:
            return off + 8, s
        off = off + 8 + ((s + 3) & ~3)
    return None, 0


def verify_wheels(blob):
    """Re-read the optional wheel segment: real FT4 models the engine can draw."""
    import lev1

    body, size = _data1_segment(blob, LUMP_CAR_WHEELS)
    if body is None:
        return None, 0

    n = struct.unpack_from("<i", blob, body)[0]
    if n < 1 or n > len(WHEEL_ORDER):
        return "wheel segment says %d entries (expected 1..%d)" % (n, len(WHEEL_ORDER)), 0

    got = 0
    for i in range(n):
        ofs = struct.unpack_from("<i", blob, body + 4 + i * 4)[0]
        if ofs == -1:
            continue
        if ofs < 0 or ofs >= size:
            return "wheel %d offset %d is outside the segment (%d)" % (i, ofs, size), 0

        model = lev1.decode_model(blob, body + ofs)
        if model.errors:
            return "wheel %d decoded with %d poly errors" % (i, len(model.errors)), 0
        if not model.vertices or not model.polys:
            return "wheel %d is empty" % i, 0
        if len(model.vertices) > 24:
            return "wheel %d has %d vertices (the draw path copies at most 24)" \
                % (i, len(model.vertices)), 0
        for p in model.polys:
            if p.kind != "FT4":
                return "wheel %d carries a %s poly (only FT4 is drawable)" % (i, p.kind), 0
        got += 1

    if got == 0:
        return "wheel segment carries no models", 0
    return None, got


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

    # THE THREE-OFFSET RULE. The engine takes a model only if clean, damaged and
    # low are all present and strictly increasing; a blob that fails this loads,
    # reports its size, and then every model in it is refused - which is exactly
    # how a missing third offset hid for so long. Checked here rather than only in
    # the writer so the self-check keeps saying it.
    for m in range(MAX_CAR_MODELS):
        c, d, l = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        if c == -1:
            continue
        if not (c < d < l):
            return ("model %d has offsets (clean %d, damaged %d, low %d) - the engine "
                    "needs all three strictly increasing, or it refuses the model"
                    % (m, c, d, l)), {}
        if l >= _size:
            return "model %d low offset %d is outside the block (%d)" % (m, l, _size), {}
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

    wheel_problem, wheel_count = verify_wheels(blob)
    if wheel_problem:
        return wheel_problem, {}

    return None, {"models": n, "gt": ngt, "hemisphere": same, "wheels": wheel_count}


def bake_one_city(args, levels_dir):
    """Bake ONE Driver 1 city's own car models, in its own SLOT order.

    An .LCF describes a city's thirteen model slots, so the .LEV beside it must
    carry that city's cars in the SAME slots - a deduped pool would put model 0's
    description on whatever car happened to sort first, and every car in the city
    would then get the wrong colours, wheels and collision box. Slots the city has
    no car in stay empty (-1 offsets), because the .LCF has a record for them too.

    Writes out/d1cities/<CITY>.LEV.
    """
    import lev1
    import d1pool

    blob = open(os.path.join(levels_dir, d1pool.source_file(args.city) + ".LEV"), "rb").read()

    by_slot = {}
    for m, variants in lev1.car_models(blob):
        model = variants.get("clean")
        if model is None and variants:
            model = next(iter(variants.values()))
        if model is None or not model.vertices:
            continue

        # The pool's own non-car test, so a level that ships a prop in its car
        # table (CREDITS slot 0 is a 52x49x48 box) does not get it offered as a car.
        # Reusing MIN_LENGTH_RAW rather than a second threshold is what keeps the .LEV
        # and the .LCF describing the same set of cars.
        _bb = model.bbox()
        if _bb is None or (_bb[5] - _bb[2]) < d1pool.MIN_LENGTH_RAW:
            continue
        g = d1pool.geom_record(m, [(args.city, m)], model)
        by_slot[m] = {"pool_index": m, "geom": g, "scale": g["scale"]}

    # REMAP onto Driver 2's compliant models - do not write at Driver 1's numbers.
    # Driver 1's cars sit on models 0,1,2,5,6,7,8(,9), and 5/6/7 are exactly the
    # three models NO Driver 2 city holds data for: a car written there resolves
    # nothing, the import silently falls back, and the player keeps their own car.
    # So take the city's cars in their own ascending order and lay them on D2's
    # compliant models - the four its frontend offers first, then the extras.
    src_slots = sorted(by_slot)
    if len(src_slots) > len(D2_COMPLIANT_MODELS):
        raise SystemExit("%s ships %d cars but only %d D2-compliant models exist"
                         % (args.city, len(src_slots), len(D2_COMPLIANT_MODELS)))

    empty = {"pool_index": 0, "geom": None, "scale": d1pool.SCALE}
    items = [dict(empty, pool_index=s) for s in range(MAX_CAR_MODELS)]
    for d1_slot, d2_slot in zip(src_slots, D2_COMPLIANT_MODELS):
        it = dict(by_slot[d1_slot])
        it["pool_index"] = d2_slot
        it["d1_slot"] = d1_slot          # provenance: where the car really came from
        items[d2_slot] = it

    # The rule, asserted rather than assumed.
    for gap in D2_GAP_MODELS:
        if items[gap]["geom"] is not None:
            raise SystemExit("%s would write model %d, which no Driver 2 city holds "
                             "data for - the engine could never address it"
                             % (args.city, gap))

    wheels = collect_wheels(levels_dir, args.city)
    remap, tex_remap, perms, specs, (flat_set, flat_index) = build_page_plan(items, levels_dir, wheels)
    for w in wheels:
        if w is None:
            continue
        for p in w["geom"]["polys"]:
            if p["set"] == 255:              # untextured marker: shares the wheel sprite
                continue
            if (w["level"], p["set"]) not in remap:
                raise SystemExit("%s wheel %s names set %d, which is not in that "
                                 "level's page lists" % (args.city, w["name"], p["set"]))

    apply_page_plan(items, wheels, remap, tex_remap, flat_set, flat_index)
    out = build_file(items, perms, specs, levels_dir, remap, tex_remap, wheels)

    if not os.path.isdir(args.out):
        os.makedirs(args.out)
    name = args.name if args.name != "D1POOL.LEV" else args.city + ".LEV"
    path = os.path.join(args.out, name)
    with open(path, "wb") as f:
        f.write(out)

    filled = [it for it in items if it["geom"]]
    print("baked %s: %d of %d slots have a car -> %s (%d bytes)"
          % (args.city, len(filled), MAX_CAR_MODELS, path, len(out)))
    for n, it in enumerate(items):
        g = it["geom"]
        if g is None:
            print("   slot %2d: empty" % n)
            continue
        print("   slot %2d: %d verts, %d tris, normals %s"
              % (n, g["verts"], g["tris"], g.get("normals_source")))
    print("   wheels: %s" % ", ".join(
        ("%s from %s" % (w["name"], w["level"])) if w else "absent" for w in wheels))
    print("   texture sets: %d permanent + %d special" % (len(perms), len(specs)))
    problem, stats = verify(out)
    if stats:
        print("   normals: %d GT polys, table faces the geometry in %.1f%%"
              % (stats["gt"], 100.0 * stats["hemisphere"] / max(stats["gt"], 1)))
    print("   self-check: %s" % (problem if problem else "the blob re-reads cleanly"))
    return 1 if problem else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "out", "pool.json"),
                    help="a pool.json from d1pool.py (not needed with --city)")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(
                        os.path.abspath(__file__)), "out", "bake"),
                    help="output directory (gitignored)")
    ap.add_argument("--first", type=int, default=MAX_CAR_MODELS,
                    help="how many pool items to bake (max %d)" % MAX_CAR_MODELS)
    ap.add_argument("--name", default="D1POOL.LEV", help="output file name")
    ap.add_argument("--levels", default=LEVELS_DIR,
                    help="directory of the source DRIVER/LEVELS/*.LEV files")
    ap.add_argument("--city", default=None,
                    help="bake ONE Driver 1 city's own car models, in SLOT order - "
                         "what that city's .LCF describes (see SPOOFED_CITIES.md)")
    args = ap.parse_args(argv)
    levels_dir = args.levels

    if args.city:
        return bake_one_city(args, levels_dir)

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
    wheels = collect_wheels(levels_dir)
    remap, tex_remap, perms, specs, (flat_set, flat_index) = build_page_plan(items, levels_dir, wheels)
    for w in wheels:
        if w is None:
            continue
        for p in w["geom"]["polys"]:
            if p["set"] == 255:              # untextured marker: shares the wheel sprite
                continue
            if (w["level"], p["set"]) not in remap:
                raise SystemExit("pool wheel %s names set %d, which is not in that "
                                 "level's page lists" % (w["name"], p["set"]))

    apply_page_plan(items, wheels, remap, tex_remap, flat_set, flat_index)
    blob = build_file(items, perms, specs, levels_dir, remap, tex_remap, wheels)

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
    print("   texture sets: %d permanent + %d special, from %d source levels"
          % (len(perms), len(specs),
             len({lvl for _s, lvl, _o, _b, _z in perms + specs})))
    problem, stats = verify(blob)
    if stats:
        print("   normals: %d GT polys, table faces the geometry in %.1f%%"
              % (stats["gt"], 100.0 * stats["hemisphere"] / max(stats["gt"], 1)))
    print("   self-check: %s" % (problem if problem else "the blob re-reads cleanly"))
    return 1 if problem else 0


if __name__ == "__main__":
    sys.exit(main())
