#!/usr/bin/env python3
"""lev1.py - read a Driver 1 (1999, PSX) LEVELS/*.LEV and pull its car models.

Driver 1 and Driver 2 share the LEV *container*; the car records inside it do
not. This tool reads the D1 side, so a D1 traffic car can be baked into the
engine (see ../docs/DRIVER1.md).

The layout below is not guessed - it is read off the reference loader,
OpenDriver2Tools (DriverLevelTool/driver_routines/{level.cpp,level.h,models.cpp,
d2_types.h}), and re-measured against this install's bytes:

  offset 0        LUMP {u32 type=37 LUMPDESC, u32 size}          (8 bytes)
  offset 8        OUT_CITYLUMP_INFO: eight u32 =
                    loadtime_offset, loadtime_size,
                    tpage_offset,    tpage_size,
                    inmem_offset,    inmem_size,
                    spooled_offset,  spooled_size
  the rest of the LUMPDESC lump is "DES!" fill up to the first offset.

  loadtime  (DATA1) starts with LUMP {type=35 LUMP_LOADTIME_DATA, size}; its
  body is  int32 lump_count  then that many  LUMP {type,size} + body, each
  body padded to a 4-byte boundary.  LUMP_CAR_MODELS (28) lives here.

  inmem     (DATA2) likewise, LUMP {type=36 LUMP_INMEMORY_DATA}.

The car-models lump body (models.cpp LoadCarModelsLump):

  +0    int32 modelCount
  +4    carmodelentry_t[13]   three int32 each: cleanOffset, damOffset, lowOffset
        (-1 = that variant is absent); offsets are relative to `r_ofs`
  +160  r_ofs = the offset base; an int32 pad sits here
        model data: at r_ofs+offset  ->  int32 size, then `size` bytes of MODEL

NOTE ON THE REFERENCE TOOL. OpenDriver2Tools' DriverLevelTool can be pointed at
a D1 level and will emit car-model OBJs, but its -carmodels export complains
("... has invalid indices (or format is unknown)", "... zero size!") on Driver 1
AND Driver 2 alike - 108 such lines on a Driver 2 CHICAGO.LEV in this repo. Its
decode_poly is the D2 decoder and carries a "TODO: D1 and D2 to have different
decoding routines". So those reports are a defect of the tool, not evidence
about the D1 car format, which is why this reader was written independently and
validated against the bytes (every variant of every model walks with 0 errors).

Usage:
    python lev1.py <LEVELS/FRISCO.LEV> [--lumps] [--names] [--models]
"""

import argparse
import struct
import sys

# ---------------------------------------------------------------------------
# LUMP ids (level.h, OpenDriver2Tools)
LUMP_MODELS = 1
LUMP_MODELNAMES = 12
LUMP_PALLET = 25
LUMP_CAR_MODELS = 28
LUMP_TEXTUREINFO = 34
LUMP_LOADTIME_DATA = 35
LUMP_INMEMORY_DATA = 36
LUMP_LUMPDESC = 37

LUMP_NAMES = {
    0: "TEXTURES", 1: "MODELS", 2: "MAP", 3: "UNUSED", 4: "MOVEABLE",
    5: "TEXTURENAMES", 6: "ROADINFO", 7: "ROADMAP", 8: "ROADS",
    9: "JUNCTIONS", 10: "ROADSURF", 11: "ROADHEIGHT", 12: "MODELNAMES",
    13: "EVENTMODELS", 14: "PVS", 15: "REGIONTSETS", 16: "ROADBOUNDS",
    17: "JUNCBOUNDS", 18: "CAMERAPATHS", 19: "LAMPS", 20: "SUBDIVISION",
    21: "LOWDETAILTABLE", 22: "MOTIONCAPTURE", 23: "TEXT", 24: "OVERLAYMAP",
    25: "PALLET", 26: "SPOOLINFO", 28: "CAR_MODELS", 33: "CHAIR",
    34: "TEXTUREINFO", 35: "LOADTIME_DATA", 36: "INMEMORY_DATA",
    37: "LUMPDESC", 40: "STRAIGHTS2", 41: "CURVES2", 42: "JUNCTIONS2",
    43: "JUNCTIONS2_NEW",
}

MAX_CAR_MODELS = 13


def align4(n):
    return (n + 3) & ~3


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def s32(b, o):
    return struct.unpack_from("<i", b, o)[0]


class LevError(Exception):
    pass


def read_header(blob):
    """Parse the LUMPDESC header. Returns the eight citylump ints as a dict."""
    typ, size = u32(blob, 0), u32(blob, 4)
    if typ != LUMP_LUMPDESC:
        raise LevError("not a LEV file: first lump type is %d, expected %d"
                       % (typ, LUMP_LUMPDESC))
    vals = struct.unpack_from("<8I", blob, 8)
    keys = ("loadtime_offset", "loadtime_size", "tpage_offset", "tpage_size",
            "inmem_offset", "inmem_size", "spooled_offset", "spooled_size")
    hdr = dict(zip(keys, vals))
    hdr["lumpdesc_size"] = size
    return hdr


def iter_lumps(blob, container_offset, expect_type):
    """Walk one DATA container's sub-lumps.

    `container_offset` points at the container's LUMP {type,size}; its body is
    `int32 lump_count` then the sub-lumps (D1 only - D2 has no count). Yields
    (type, body_offset, size).
    """
    typ, size = u32(blob, container_offset), u32(blob, container_offset + 4)
    if expect_type is not None and typ != expect_type:
        raise LevError("lump at %d is type %d, expected %d"
                       % (container_offset, typ, expect_type))
    off = container_offset + 8
    count = u32(blob, off)
    off += 4
    for _ in range(count):
        t = u32(blob, off)
        s = u32(blob, off + 4)
        if t == 0xFF:                      # stop marker
            break
        if t == 0 and s == 0:              # zero padding after the last segment
            break
        yield t, off + 8, s
        off = align4(off + 8 + s)


def find_lump(blob, container_offset, expect_type, lump_type):
    for t, body, size in iter_lumps(blob, container_offset, expect_type):
        if t == lump_type:
            return body, size
    return None


def car_models_lump(blob):
    """Return (body, size) of LUMP_CAR_MODELS in DATA1, or None."""
    hdr = read_header(blob)
    return find_lump(blob, hdr["loadtime_offset"], LUMP_LOADTIME_DATA,
                     LUMP_CAR_MODELS)


def model_names_lump(blob):
    hdr = read_header(blob)
    for container, etype in ((hdr["loadtime_offset"], LUMP_LOADTIME_DATA),
                             (hdr["inmem_offset"], LUMP_INMEMORY_DATA)):
        got = find_lump(blob, container, etype, LUMP_MODELNAMES)
        if got:
            return got
    return None


# ---------------------------------------------------------------------------
# Car-model decoding.
#
# MEASURED: a D1 car model uses the SAME poly encoding as Driver 2 - the engine's
# POLYFT3/FT4/GT3/GT4 records, walked by PolySizes - even though the lump
# container differs. The reference tool (DriverLevelTool) applies the D2 decoder
# to D1 models and reports "invalid indices" / "zero size!"; that is a bug in its
# D1 export, not a format difference: every model here walks with 0 zero-steps
# and every vertex index is in range.
#
# Poly id -> (kind, index_offset, n_indices, colour_offset).
# The ids are the engine's spellings (mdl.h / draw.c PolySizes): F3 is 0/8/16/18,
# F4 is 1/17/19, FT3 is 4/10/20, FT4 is 5/7/9/11/21, GT3 is 22, GT4 is 23. The
# offsets are per-type because the same "kind" is stored at different sizes
# (PolySizes), e.g. F3 is 8 bytes as type 0 but 12 as type 16.
POLY_LAYOUT = {
    0: ("F3", 1, 3, 5), 8: ("F3", 1, 3, 5), 16: ("F3", 1, 3, 8), 18: ("F3", 1, 3, 8),
    1: ("F4", 4, 4, 8), 17: ("F4", 4, 4, 8), 19: ("F4", 4, 4, 12),
    4: ("FT3", 4, 3, 16), 10: ("FT3", 4, 3, 12), 20: ("FT3", 4, 3, 16),
    5: ("FT4", 4, 4, 16), 7: ("FT4", 4, 4, 28), 9: ("FT4", 4, 4, 8),
    11: ("FT4", 4, 4, 12), 21: ("FT4", 4, 4, 16),
    22: ("GT3", 4, 3, 20), 23: ("GT4", 4, 4, 20),
}
TEXTURED = ("FT3", "FT4", "GT3", "GT4")
NORMALLED = ("GT3", "GT4")

# draw.c PolySizes[56], indexed by (type & 0x1f)
POLY_SIZES = [8, 12, 16, 24, 20, 20, 28, 32, 8, 12, 16, 16,
              0, 0, 0, 0, 12, 12, 12, 16, 20, 20, 24, 24,
              0, 0, 0, 0, 0, 0, 0, 0, 8, 12, 16, 24,
              20, 20, 28, 32, 0, 0, 0, 0, 0, 0, 0, 0,
              12, 12, 12, 16, 20, 20, 24, 24]

MODEL_FMT = "<hhhBBhhhh"    # shape_flags, flags2, instance_number, tri_verts,
                            # zBias, bounding_sphere, num_point_normals,
                            # num_vertices, num_polys
MODEL_OFFSETS_FMT = "<iiiii"  # vertices, poly_block, normals, point_normals,
                              # collision_block - byte offsets from the MODEL


class Poly(object):
    """One decoded polygon, format-neutral."""
    __slots__ = ("kind", "texture_set", "texture_id", "v", "n", "uv", "color")

    def __init__(self, kind, texture_set, texture_id, v, n, uv, color):
        self.kind = kind              # "F3"/"F4"/"FT3"/"FT4"/"GT3"/"GT4"
        self.texture_set = texture_set
        self.texture_id = texture_id
        self.v = v                    # tuple of vertex indices
        self.n = n                    # tuple of normal indices (GT only), else ()
        self.uv = uv                  # tuple of (u, v) shorts
        self.color = color            # (r, g, b)


class Model(object):
    __slots__ = ("shape_flags", "flags2", "bounding_sphere", "tri_verts",
                 "vertices", "polys", "errors")

    def __init__(self, shape_flags, flags2, bounding_sphere, tri_verts,
                 vertices, polys, errors):
        self.shape_flags = shape_flags
        self.flags2 = flags2
        self.bounding_sphere = bounding_sphere
        self.tri_verts = tri_verts
        self.vertices = vertices      # list of (x, y, z)
        self.polys = polys            # list of Poly
        self.errors = errors          # list of (index, reason)

    def bbox(self):
        if not self.vertices:
            return None
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        zs = [v[2] for v in self.vertices]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _uvs(rec, kind, n):
    """UV pairs. A GT record carries per-vertex normals before the UVs, so its
    UVs start at byte 12; an FT record's start at byte 8. UV_INFO is two
    UNSIGNED bytes (u, v) in 0..255 into the 256x256 page."""
    off = 12 if kind in NORMALLED else 8
    return tuple(struct.unpack_from("<BB", rec, off + i * 2) for i in range(n))


def decode_polys(blob, base, n_polys, num_vertices):
    """Walk `n_polys` polys from `base` by PolySizes; decode each into a Poly."""
    polys = []
    errors = []
    p = base
    for i in range(n_polys):
        if p >= len(blob):
            errors.append((i, "poly stream overruns the file"))
            break
        pt = blob[p] & 0x1F
        size = POLY_SIZES[pt]
        if size == 0:
            errors.append((i, "zero-size poly type %d" % pt))
            break
        layout = POLY_LAYOUT.get(pt)
        if layout is None:
            errors.append((i, "unknown poly type %d" % pt))
            p += size
            continue
        kind, ioff, nind, coff = layout
        rec = blob[p:p + size]
        v = tuple(rec[ioff:ioff + nind])
        n = tuple(rec[8:8 + nind]) if kind in NORMALLED else ()
        uv = _uvs(rec, kind, nind) if kind in TEXTURED else ()
        color = (rec[coff], rec[coff + 1], rec[coff + 2])
        setid, texid = (rec[1], rec[2]) if kind in TEXTURED else (0, 0)

        bad = [x for x in v if x >= num_vertices]
        if bad:
            errors.append((i, "vertex index out of range %s (nv=%d)"
                           % (bad, num_vertices)))
        else:
            polys.append(Poly(kind, setid, texid, v, n, uv, color))
        p += size
    return polys, errors


def decode_model(blob, model_off, polys_from=None):
    """`model_off` points at the MODEL struct (not the 4-byte size that precedes
    it). Returns a Model.

    `polys_from` names a second MODEL whose poly stream should be used instead of
    this one's. MEASURED: a D1 *damaged* model is a MODEL header + its own (dented)
    vertex array only - its block is exactly 36 + num_vertices*8 bytes and its
    poly_block lands outside it. Its poly_block/normals fields are stale; the
    polys are the clean model's (identical num_polys, and the clean stream walks
    with 0 errors for every model). So damaged := clean polys + damaged vertices.
    """
    (shape_flags, flags2, _inst, tri_verts, _zb, bounding_sphere,
     _npn, num_vertices, num_polys) = struct.unpack_from(MODEL_FMT, blob, model_off)
    (vofs, _pblk, _nofs, _pno, _c) = struct.unpack_from(MODEL_OFFSETS_FMT,
                                                        blob, model_off + 16)
    vertices = []
    for i in range(num_vertices):
        x, y, z, _pad = struct.unpack_from("<hhhh", blob, model_off + vofs + i * 8)
        vertices.append((x, y, z))

    src = model_off if polys_from is None else polys_from
    (_sf, _f2, _in, _tv, _zb, _bs, _npn, src_nv, src_npol) = \
        struct.unpack_from(MODEL_FMT, blob, src)
    (_vo, src_pblk, _no, _pn, _co) = \
        struct.unpack_from(MODEL_OFFSETS_FMT, blob, src + 16)
    polys, errors = decode_polys(blob, src + src_pblk, src_npol, src_nv)
    return Model(shape_flags, flags2, bounding_sphere, tri_verts,
                 vertices, polys, errors)


def car_models(blob):
    """Yield (model_index, {variant: Model}) for every model a level ships."""
    body, _size = car_models_lump(blob)
    r_ofs = body + 4 + MAX_CAR_MODELS * 12

    def model_at(ofs):
        return r_ofs + ofs + 4          # skip the per-model 4-byte size

    for m in range(MAX_CAR_MODELS):
        clean, dam, low = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        if clean == -1:
            continue
        variants = {"clean": decode_model(blob, model_at(clean))}
        if dam != -1:
            variants["damaged"] = decode_model(blob, model_at(dam),
                                               polys_from=model_at(clean))
        if low != -1:
            variants["low"] = decode_model(blob, model_at(low))
        yield m, variants


def car_model_entries(blob):
    """Yield (model_index, variant, size, model_off) for every present variant.

    variant is "clean"/"damaged"/"low". The entry table is a FIXED 13 entries
    (three int32 each) at lump_body+4; offsets are relative to r_ofs =
    lump_body + 160 (measured: every model is contiguous with the next).
    """
    body, _size = car_models_lump(blob)
    r_ofs = body + 4 + MAX_CAR_MODELS * 12
    for m in range(MAX_CAR_MODELS):
        clean, dam, low = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        for variant, ofs in (("clean", clean), ("damaged", dam), ("low", low)):
            if ofs == -1:
                continue
            size = struct.unpack_from("<i", blob, r_ofs + ofs)[0]
            yield m, variant, size, r_ofs + ofs + 4


def show_models(blob, path):
    body, size = car_models_lump(blob)
    print("%s  CAR_MODELS lump %d bytes at %d" % (path, size, body))
    n = 0
    nerr = 0
    for m, variants in car_models(blob):
        for variant in ("clean", "damaged", "low"):
            model = variants.get(variant)
            if model is None:
                continue
            bb = model.bbox()
            dims = ("%dx%dx%d" % (bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2])
                    if bb else "-")
            kinds = {}
            for p in model.polys:
                kinds[p.kind] = kinds.get(p.kind, 0) + 1
            nerr += len(model.errors)
            print("  model %2d %-7s verts=%-4d polys=%-4d bound=%-5d size=%-14s %s%s"
                  % (m, variant, len(model.vertices), len(model.polys),
                     model.bounding_sphere, dims,
                     dict(sorted(kinds.items())) if kinds else "{}",
                     "  ERRORS=%s" % model.errors if model.errors else ""))
            n += 1
    print("  %d variants, %d decode errors" % (n, nerr))
    return nerr


def show_lumps(blob, path):
    """Print the D1 section table and the DATA1/DATA2 sub-lumps."""
    hdr = read_header(blob)
    print("%s  (%d bytes)" % (path, len(blob)))
    print("  LUMPDESC: %d bytes" % hdr["lumpdesc_size"])
    for name in ("loadtime", "tpage", "inmem", "spooled"):
        print("  %-9s offset %-9d size %d"
              % (name, hdr[name + "_offset"], hdr[name + "_size"]))
    for name, container, etype in (("DATA1", hdr["loadtime_offset"], LUMP_LOADTIME_DATA),
                                   ("DATA2", hdr["inmem_offset"], LUMP_INMEMORY_DATA)):
        print("  --- %s sub-lumps ---" % name)
        for t, body, size in iter_lumps(blob, container, etype):
            print("    type %-3d %-14s ofs %-9d size %d"
                  % (t, LUMP_NAMES.get(t, "?"), body, size))


def verify(blob, path):
    """Check the reader's invariants on one level. Returns a list of problems.

    - the entry table is MAX_CAR_MODELS (13) entries (models.h);
    - every variant of every model decodes with 0 errors;
    - the damaged-block invariant holds: block size == 36 + num_vertices*8.
    """
    problems = []
    cm = car_models_lump(blob)
    if cm is None:
        print("  %-12s no CAR_MODELS lump (skipped)"
              % path.replace("\\", "/").rsplit("/", 1)[-1])
        return problems
    body, _size = cm
    r_ofs = body + 4 + MAX_CAR_MODELS * 12
    nvar = 0
    for m, variants in car_models(blob):
        for variant, model in variants.items():
            nvar += 1
            if model.errors:
                problems.append("model %d %s: %s" % (m, variant, model.errors[:2]))
            if variant == "damaged":
                _c, dam, _l = struct.unpack_from("<3i", blob, body + 4 + m * 12)
                block = struct.unpack_from("<i", blob, r_ofs + dam)[0]
                nv = struct.unpack_from(MODEL_FMT, blob, r_ofs + dam + 4)[7]
                if block != 36 + nv * 8:
                    problems.append("model %d damaged: block %d != 36+nv*8 (%d)"
                                    % (m, block, 36 + nv * 8))
    print("  %-12s %2d variants, %d problems"
          % (path.replace("\\", "/").rsplit("/", 1)[-1], nvar, len(problems)))
    return problems


def dump_names(blob):
    got = model_names_lump(blob)
    if not got:
        print("no LUMP_MODELNAMES")
        return
    body, size = got
    text = blob[body:body + size]
    names = [s.decode("ascii", "replace") for s in text.split(b"\x00") if s]
    print("model names (%d):" % len(names))
    for i, n in enumerate(names):
        print("  %4d  %s" % (i, n))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lev", nargs="+", help="one or more Driver 1 LEVELS/*.LEV")
    ap.add_argument("--lumps", action="store_true", help="dump the section table")
    ap.add_argument("--names", action="store_true", help="dump the model-names lump")
    ap.add_argument("--models", action="store_true",
                    help="decode every car-model variant and print its stats")
    ap.add_argument("--verify", action="store_true",
                    help="assert the reader's invariants; exit 1 on any problem")
    args = ap.parse_args(argv)

    problems = []
    for path in args.lev:
        with open(path, "rb") as f:
            blob = f.read()

        did = False
        if args.lumps:
            show_lumps(blob, path)
            did = True
        if args.models:
            show_models(blob, path)
            did = True
        if args.names:
            dump_names(blob)
            did = True
        if args.verify:
            problems += verify(blob, path)
            did = True
        if not did:
            cm = car_models_lump(blob)
            if cm is None:
                print("%s: no LUMP_CAR_MODELS in DATA1" % path)
                problems.append(path)
                continue
            body, size = cm
            print("%s: LUMP_CAR_MODELS at %d size %d" % (path, body, size))

    if args.verify:
        print("VERDICT: %s (%d problems)"
              % ("clean" if not problems else "FAILED", len(problems)))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
