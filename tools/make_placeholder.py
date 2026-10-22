#!/usr/bin/env python3
"""make_placeholder.py - bake PLCHLDR.LEV, a uniform-checker stand-in car.

A player who joins a session where someone drives a vehicle from a city they do
not have installed has no model for it. This bakes ONE placeholder car (model 4
of a donor blob) wrapped in a uniform white/light-grey checker, as its own
car-data bank the engine can fall back to.

The checker UVs are a box projection (each poly projected on the plane its
normal faces), so the squares stay even across the body instead of skewing
through the donor's per-panel UVs.

It can also carry sounds: --voices2 lifts a bank straight out of Driver 2's
VOICES2.BLK. Those lumps are already dynamic car banks (engine, idle, horn), the
exact shape the engine reads for an imported model, so the one named here is
embedded as-is. Lump 12 is CHICAGO model 1.

    python make_placeholder.py --donor D1CARS/BETAMIAMI.LEV \
        --voices2 DRIVER2/SOUND/VOICES2.BLK --sound-lump 12 \
        --out PLCHLDR.LEV
"""

import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bake            # noqa: E402
import add_car_sounds  # noqa: E402  insert_sound_segment, MAX_CAR_MODELS
import d1pool          # noqa: E402
import lev1            # noqa: E402
import pages1          # noqa: E402

SIZE = 256
SQUARE = 16
MODEL_SLOT = 4
CHECKER_SET = bake.PAGE_SET_BASE


def checker_page_4bpp():
    """256x256, 16px squares, 4bpp. Low nibble is the even texel."""
    data = bytearray(0x8000)
    for y in range(SIZE):
        by = (y // SQUARE) & 1
        base = y * 128
        for xb in range(128):
            x0 = xb * 2
            n0 = (by + (x0 // SQUARE)) & 1
            n1 = (by + ((x0 + 1) // SQUARE)) & 1
            data[base + xb] = n0 | (n1 << 4)
    return bytes(data)


def checker_clut_row():
    white, grey = 0x7FFF, 0x5AD6
    return struct.pack("<16H", *[grey if i == 1 else white for i in range(16)])


def _box_uv(vertices, poly, normal):
    """Project this poly on the plane its normal faces, scaled to 0..255."""
    ax = max(range(3), key=lambda i: abs(normal[i]))
    ua, va = {0: (2, 1), 1: (0, 2), 2: (0, 1)}[ax]
    lo = [min(v[i] for v in vertices) for i in range(3)]
    hi = [max(v[i] for v in vertices) for i in range(3)]
    span = [max(hi[i] - lo[i], 1) for i in range(3)]
    out = []
    for vi in poly.v:
        v = vertices[vi]
        out.append([int((v[ua] - lo[ua]) / span[ua] * 255),
                    int((v[va] - lo[va]) / span[va] * 255)])
    return out


def load_donor_model(path, slot):
    import scalemodels as sm
    blob = open(path, "rb").read()
    got = sm.d2_car_lump(blob)
    if not got:
        raise SystemExit("%s has no car-model lump" % path)
    body, _size = got
    clean, _dam, _low = struct.unpack_from("<3i", blob, body + 4 + slot * 12)
    if clean == -1:
        raise SystemExit("%s slot %d is empty" % (path, slot))
    return lev1.decode_model(blob, body + 164 + clean)


def geom_for(model):
    vertices = [list(v) for v in model.vertices]
    face_n, point_n, pn, nsource = d1pool.normals_for(model)

    polys = []
    for k, p in enumerate(model.polys):
        if p.kind not in ("F3", "F4", "FT3", "FT4", "GT3", "GT4"):
            continue
        ns = model.poly_normals(p)
        n = [sum(q[i] for q in ns) / len(ns) for i in range(3)] if ns else (0, 1, 0)
        polys.append({"kind": p.kind if p.kind in ("GT3", "GT4") else "GT3",
                      "set": CHECKER_SET, "id": 0, "v": list(p.v),
                      "n": pn[k], "uv": _box_uv(vertices, p, n),
                      "color": list(p.color)})

    return {
        "version": 1, "pool_index": MODEL_SLOT, "sources": [("PLCHLDR", MODEL_SLOT)],
        "verts": len(vertices), "tris": len(polys),
        "scale": 1.0, "normals_source": nsource,
        "face_normals": face_n, "point_normals": point_n,
        "vertices": vertices, "polys": polys,
    }


def sound_body_from_voices2(path, lump):
    """VOICES2.BLK lump `lump` as a LUMP_CAR_SOUNDS body for MODEL_SLOT.

    VOICES2.BLK is [u32 blockLimit[73]][lump data]; each car lump is already a D2
    dynamic bank (int num_samples, SAMPLE_DATA[], VAG) - the exact shape the
    engine's CarImportSoundEntry reads - so it is embedded unchanged.
    """
    d = open(path, "rb").read()
    limits = struct.unpack_from("<73I", d, 0)
    if lump < 0 or lump + 1 >= len(limits):
        raise SystemExit("VOICES2.BLK has no lump %d" % lump)
    off, end = limits[lump], limits[lump + 1]
    bank = d[off:end]
    n = struct.unpack_from("<i", bank, 0)[0]
    if n != 3:
        raise SystemExit("VOICES2 lump %d carries %d samples; the imported-car bank needs 3"
                         % (lump, n))

    body = bytearray(struct.pack("<ii", 1, add_car_sounds.MAX_CAR_MODELS))
    body += b"\x00" * (add_car_sounds.MAX_CAR_MODELS * 4)   # the offsets table
    offsets = [-1] * add_car_sounds.MAX_CAR_MODELS
    offsets[MODEL_SLOT] = len(body)                         # first bank, after the table
    body += bank
    struct.pack_into("<%di" % add_car_sounds.MAX_CAR_MODELS, body, 8, *offsets)
    return bytes(body)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--donor", required=True, help="a baked D1CARS/*.LEV to take model 4 from")
    ap.add_argument("--out", required=True, help="output PLCHLDR.LEV path")
    ap.add_argument("--voices2", default=None,
                    help="DRIVER2/SOUND/VOICES2.BLK for the placeholder's engine/idle/horn")
    ap.add_argument("--sound-lump", type=int, default=12,
                    help="VOICES2 bank lump (12 = CHICAGO model 1)")
    args = ap.parse_args(argv)

    model = load_donor_model(args.donor, MODEL_SLOT)
    geom = geom_for(model)
    print("donor model %d: %d verts, %d polys" % (MODEL_SLOT, geom["verts"], len(geom["polys"])))

    yes = {"geom": geom, "scale": geom["scale"]}
    empty = {"geom": None, "scale": 1.0}
    items = [dict(empty) for _ in range(bake.MAX_CAR_MODELS)]
    items[MODEL_SLOT] = yes

    # One synthesized page (level=None, like the flat-colour page): the checker
    # image plus a single white/grey CLUT row. build_pallet emits the palette
    # straight from this entry.
    entry = struct.pack("<i", 1) + checker_clut_row() + pages1.encode_literal(checker_page_4bpp())
    perms = [(CHECKER_SET, None, None, entry, len(entry))]

    out = bake.build_file(items, perms, [], HERE, {}, {}, None, None)

    if args.voices2:
        sb = sound_body_from_voices2(args.voices2, args.sound_lump)
        out = add_car_sounds.insert_sound_segment(out, sb)
        print("sounds: VOICES2 lump %d -> model %d" % (args.sound_lump, MODEL_SLOT))

    d = os.path.dirname(os.path.abspath(args.out))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(args.out, "wb") as f:
        f.write(out)
    print("wrote %s (%d bytes) - model %d, set %d, uniform checker"
          % (args.out, len(out), MODEL_SLOT, CHECKER_SET))


if __name__ == "__main__":
    main()
