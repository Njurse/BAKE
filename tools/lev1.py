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


def show_lumps(blob, path):
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lev", help="a Driver 1 LEVELS/*.LEV")
    ap.add_argument("--lumps", action="store_true", help="dump the section table")
    ap.add_argument("--names", action="store_true", help="dump the model-names lump")
    args = ap.parse_args(argv)

    with open(args.lev, "rb") as f:
        blob = f.read()

    did = False
    if args.lumps:
        show_lumps(blob, args.lev)
        did = True

    if args.names:
        got = model_names_lump(blob)
        if not got:
            print("no LUMP_MODELNAMES")
        else:
            body, size = got
            text = blob[body:body + size]
            names = [s.decode("ascii", "replace")
                     for s in text.split(b"\x00") if s]
            print("model names (%d):" % len(names))
            for i, n in enumerate(names):
                print("  %4d  %s" % (i, n))
        did = True

    if not did:
        cm = car_models_lump(blob)
        if cm is None:
            print("no LUMP_CAR_MODELS in DATA1")
            return 1
        body, size = cm
        print("LUMP_CAR_MODELS at %d size %d" % (body, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
