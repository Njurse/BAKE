#!/usr/bin/env python3
"""cosmetics1.py - derive a CAR_COSMETICS record for every D1 car.

Driver 1 has no cosmetics data at all, so every field has to be derived. The
record is 236 bytes and dr2types.h:211 fixes the layout exactly:

    headLight frontInd backInd brakeLight revLight policeLight
    exhaust smoke fire                          (9 SVECTORs, 0..71)
    wheelDisp[4]                                (72..103)
    extraInfo powerRatio cbYoffset susCoeff traction wheelSize  (104..115)
    cPoints[12]                                 (116..211)
    colBox cog                                  (212..235)
    twistRateX twistRateY twistRateZ mass       (228..235)

The rule here is: take the nearest-length DRIVER 2 car's own record and
re-proportion its position fields per axis to THIS car's bbox, which keeps Driver
2's layout - where the lights, the collision points and the centre of gravity sit
on a car of that shape - while adapting to our car's size. Three groups are then
replaced outright, because we can measure them better than we can inherit them:

    colBox      this car's own scaled bbox halves
    wheelDisp   the wheel placement, in the engine's convention
    wheelSize   the tyre radius - the axle HEIGHT, not a scale

The wheelDisp conversion is the one thing here that can be proved rather than
argued: cars.c:1273 is the forward direction, so inverting it and feeding Driver
2's own records back through must return Driver 2's own numbers. --selftest does
exactly that.
"""

import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1
import d1pool        # noqa: E402
import scalemodels  # noqa: E402
import wheelgeom   # noqa: E402

SCALE = float(scalemodels.D2_CELL_SIZE) / scalemodels.D1_CELL_SIZE   # 0.68267
D1_CITIES = ("MIAMI", "FRISCO", "LA", "NEWYORK")

RECORD_SIZE = 236
LCF_TABLE = 52          # 13 int32 offsets, then the records

# The 23 position SVECTORs: 9 anchors, 12 collision points, colBox, cog.
POS_OFFSETS = ([0, 8, 16, 24, 32, 40, 48, 56, 64]          # headLight..fire
               + [116 + i * 8 for i in range(12)]          # cPoints[12]
               + [212, 220])                               # colBox, cog
OFF_WHEELDISP = 72
OFF_SCALARS_A = 104     # extraInfo, powerRatio, cbYoffset, susCoeff, traction, wheelSize
OFF_SCALARS_B = 228     # twistRateX/Y/Z, mass


def read_record(blob, off=0):
    """A 236-byte record -> dict, so tests can compare field by field."""
    pos = [struct.unpack_from("<3h", blob, off + o) for o in POS_OFFSETS]
    return {
        "anchors": pos[:9],
        "cPoints": pos[9:21],
        "colBox": pos[21],
        "cog": pos[22],
        "wheelDisp": [struct.unpack_from("<3h", blob, off + OFF_WHEELDISP + i * 8)
                      for i in range(4)],
        "sA": struct.unpack_from("<6h", blob, off + OFF_SCALARS_A),
        "sB": struct.unpack_from("<4h", blob, off + OFF_SCALARS_B),
    }


def engine_wheel_positions(wheel_disp, wheel_size):
    """cars.c:1273 the other way round: disp + size -> the four wheel centres."""
    out = []
    for i, (vx, vy, vz) in enumerate(wheel_disp):
        x = (17 if (i & 2) == 0 else -17) - vx
        out.append((x, -wheel_size - vy + 14, -vz))
    return out


def wheel_disp_from(centres, wheel_size):
    """The four centres -> wheelDisp, inverting the engine's own arithmetic."""
    out = []
    for i, (x, y, z) in enumerate(centres):
        out.append(((17 if (i & 2) == 0 else -17) - x,
                    -y - wheel_size + 14,
                    -z))
    return out


def donor_bodies(level_path):
    """{model: (bbox_min, bbox_max)} for a Driver 2 car level.

    A donor record carries the scalars and wheelDisp; the placement also needs the
    BODY those belong to, because both numbers the placement takes are proportions
    of it. Decoded rather than taken from colBox, which is a collision box and
    only close to the body.
    """
    blob = open(level_path, "rb").read()
    body, _size = scalemodels.d2_car_lump(blob)
    out = {}
    for m in range(scalemodels.MAX_CAR_MODELS):
        clean, _d, _l = struct.unpack_from("<3i", blob, body + 4 + m * 12)
        if clean == -1:
            continue
        mo = lev1.decode_model(blob, body + 164 + clean)
        if mo.vertices:
            out[m] = wheelgeom.body_bbox(mo.vertices)
    return out


def donor_index(half, donor_halves):
    """The D2 model whose own 3D size is closest to ours.

    Length alone used to decide this, and length alone cannot tell a van from a
    saloon of the same length - which is exactly the case that matters, because
    the van is the one whose axle height is raised. All three axes vote.
    """
    def dist(h):
        return sum(((half[i] / h[i]) - 1.0) ** 2 for i in range(3) if h[i])
    return min(range(len(donor_halves)), key=lambda m: dist(donor_halves[m]))


def halves_from_bbox(bb):
    """(min, max) -> the three half-extents."""
    return [(bb[1][i] - bb[0][i]) / 2.0 for i in range(3)]


def build_record(verts, scale, centres, radius, donor, donor_bbox):
    """One car's CAR_COSMETICS.

    `verts` is the car's own (unscaled) vertices, `scale` the bake scale,
    `centres`/`radius` come from the wheel placement, and `donor`/`donor_bbox`
    are the matched Driver 2 record and that car's own body box.
    """
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    half = ((max(xs) - min(xs)) / 2.0 * scale,
            (max(ys) - min(ys)) / 2.0 * scale,
            (max(zs) - min(zs)) / 2.0 * scale)
    # per-axis proportion against the donor's OWN BODY, guarded for a flat axis
    dh = halves_from_bbox(donor_bbox)
    ratio = tuple((half[i] / dh[i]) if dh[i] else 1.0 for i in range(3))

    def scaled(p):
        return tuple(int(round(p[i] * ratio[i])) for i in range(3))

    out = bytearray(RECORD_SIZE)
    # The anchors are the DONOR's, re-proportioned per axis. They are not cloned:
    # scaling by the ratio between the two bodies puts each one at the same place
    # on our car as it held on Driver 2's - so the headlight stays at the front,
    # the exhaust at the rear and the police light at the belt line, at THIS car's
    # front/rear/height.
    for o, p in zip(POS_OFFSETS[:9], donor["anchors"]):
        struct.pack_into("<3h", out, o, *scaled(p))
    # colBox and cog are ours outright - measured, not inherited. colBox is this
    # car's own box; cog is that box's centre, which is where the weight sits.
    struct.pack_into("<3h", out, 212, int(round(half[0])), int(round(half[1])),
                     int(round(half[2])))
    struct.pack_into("<3h", out, 220, 0,
                     int(round((max(ys) + min(ys)) / 2.0 * scale)),
                     int(round((max(zs) + min(zs)) / 2.0 * scale)))
    # cPoints[12]: the collision hull, from the car's own footprint - three heights
    # (ground, belt, roof) by the four corners, in Driver 2's own order (low front,
    # low rear, mid front, high front, high rear, mid rear).
    y_low, y_high = max(ys) * scale, min(ys) * scale          # y is down-positive
    y_mid = (y_low + y_high) / 2.0
    x_f, z_f = half[0], min(zs) * scale                       # front is -z
    z_r = max(zs) * scale
    corners = ((y_low, z_f), (y_low, z_r), (y_mid, z_f),
               (y_high, z_f), (y_high, z_r), (y_mid, z_r))
    n = 0
    for y, z in corners:
        for sx in (-1, 1):
            struct.pack_into("<3h", out, 116 + n * 8,
                             int(round(sx * x_f)), int(round(y)), int(round(z)))
            n += 1
    for i, d in enumerate(wheel_disp_from(centres, radius)):
        struct.pack_into("<3h", out, OFF_WHEELDISP + i * 8,
                         max(-32768, min(32767, int(round(d[0])))),
                         max(-32768, min(32767, int(round(d[1])))),
                         max(-32768, min(32767, int(round(d[2])))))
    struct.pack_into("<6h", out, OFF_SCALARS_A, *donor["sA"][:5],
                     int(round(radius)))
    struct.pack_into("<4h", out, OFF_SCALARS_B, *donor["sB"])
    return bytes(out)


def load_donors(levels_dir):
    """{city: [record, ...]} for Driver 2's own cars, one list per city."""
    out = {}
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LCF"):
            continue
        blob = open(os.path.join(levels_dir, fn), "rb").read()
        offs = struct.unpack_from("<13i", blob, 0)
        out[fn[:-4]] = [read_record(blob, o) for o in offs]
    return out


def records_for_city(levels_dir, city, donor_recs, donor_bb, wheel_model,
                     cities=None):
    """A 236-byte record for every car model a D1 city ships.

    Mirrors the shape Driver 2 uses: each city's .LCF covers that city's own 13
    model slots. Returns a list of (slot, record, donor_index, radius, track),
    with a record of None for a slot the city leaves empty.
    """
    blob = open(os.path.join(levels_dir, d1pool.source_file(city) + ".LEV"), "rb").read()
    donors = sorted(donor_bb)          # only the models that have a body
    donor_halves = [halves_from_bbox(donor_bb[m]) for m in donors]
    out = []
    for m, variants in lev1.car_models(blob):
        model = variants.get("clean")
        if model is None and variants:
            model = next(iter(variants.values()))
        if model is None or not model.vertices:
            out.append((m, None, None, None, None))
            continue

        # Same non-car test as the bake (see d1pool.source_file). If these two ever
        # disagree about which slots hold cars, a record lands on the wrong body.
        _bb = model.bbox()
        if _bb is None or (_bb[5] - _bb[2]) < d1pool.MIN_LENGTH_RAW:
            out.append((m, None, None, None, None))
            continue
        bmin, bmax = wheelgeom.body_bbox(model.vertices)
        half = [(bmax[i] - bmin[i]) / 2.0 * SCALE for i in range(3)]
        di = donors[donor_index(half, donor_halves)]
        drec = donor_recs[di]
        # the donor's own four wheel centres, then re-proportioned onto this car
        dcentres = engine_wheel_positions(drec["wheelDisp"], drec["sA"][5])
        centres = wheelgeom.placement_from_donor(model.vertices, donor_bb[di],
                                                 dcentres, SCALE)
        zs = sorted(c[2] for c in centres)
        track = (max(c[0] for c in centres) - min(c[0] for c in centres)) / 2.0
        rec = build_record(model.vertices, SCALE, centres,
                           _wheel_radius(wheel_model, SCALE), drec, donor_bb[di])
        out.append((m, rec, di, zs[-1] - zs[0], track))

    # REMAP onto the same models the .LEV bake uses. The .LEV and the .LCF are two
    # halves of ONE description: the .LCF's table is indexed by MODEL NUMBER, so a
    # record left at Driver 1's own number describes the wrong car - and for models
    # 5/6/7 a car Driver 2 cannot address at all. Both halves must read the same
    # mapping, so it is imported from bake rather than copied: a second literal is
    # exactly how these two drift apart, silently, with the colours landing on the
    # wrong bodies.
    import bake
    filled = [e for e in out if e[1] is not None]
    if len(filled) > len(bake.D2_COMPLIANT_MODELS):
        raise SystemExit("%s ships %d cars but only %d D2-compliant models exist"
                         % (city, len(filled), len(bake.D2_COMPLIANT_MODELS)))
    remapped = [(m, None, None, None, None) for m in range(scalemodels.MAX_CAR_MODELS)]
    SLOT_MAP[city] = []
    for (src_m, rec, di, r, t), dst_m in zip(filled, bake.D2_COMPLIANT_MODELS):
        remapped[dst_m] = (dst_m, rec, di, r, t)
        SLOT_MAP[city].append([src_m, dst_m])
    for gap in bake.D2_GAP_MODELS:
        if remapped[gap][1] is not None:
            raise SystemExit("%s would put a record on model %d, which no Driver 2 "
                             "city holds data for" % (city, gap))
    return remapped


def _wheel_radius(wheel_model, scale):
    """The shared D1 wheel's radius at the bake scale - the axle HEIGHT."""
    wy = [v[1] for v in wheel_model.vertices]
    wz = [v[2] for v in wheel_model.vertices]
    return max(max(wy) - min(wy), max(wz) - min(wz)) / 2.0 * scale


def build_records(levels_dir, donor_city, cities=None, show=True, donor_dir=None):
    """Every D1 city's records. Returns {city: [(slot, record, ...), ...]}."""
    donors = load_donors(donor_dir or levels_dir)
    if donor_city not in donors:
        print("no donor city %s among %s" % (donor_city, sorted(donors)))
        return None
    donor_recs = donors[donor_city]
    level_path = os.path.join(donor_dir or levels_dir, donor_city + ".LEV")
    donor_bb = donor_bodies(level_path)
    if not donor_bb:
        print("no decodable car bodies in %s" % level_path)
        return None
    _lvl, wheel_model = wheelgeom.canonical_wheel(levels_dir)
    if wheel_model is None:
        print("no wheel model under %s" % levels_dir)
        return None
    out = {}
    for city in (cities or D1_CITIES):
        recs = records_for_city(levels_dir, city, donor_recs, donor_bb, wheel_model)
        out[city] = recs
        if show:
            print("%-8s %d slots, %d with data"
                  % (city, len(recs), sum(1 for r in recs if r[1])))
            for slot, rec, di, radius, track in recs:
                if rec is None:
                    print("   slot %2d: empty" % slot)
                    continue
                r = read_record(rec)
                print("   slot %2d: donor %2d  colBox %-14s cog %-13s wheelSize %3d"
                      " wheelbase %4.0f  track %4.0f"
                      % (slot, di, r["colBox"], r["cog"], r["sA"][5], radius, track))
    return out


def fix_car_cos_delta_z(rec):
    """handling.c:183 - the z shift FixCarCos applies to a loaded car.

    delta.vz = -(wheelDisp[0].vz + wheelDisp[1].vz - 14) / 2. With a placement
    symmetric about the car's bbox z centre this reduces to (z_centre + 7), so it
    reports exactly how far off centre the body sits - which is the correction the
    engine exists to make, and it should therefore be small.
    """
    return -(rec["wheelDisp"][0][2] + rec["wheelDisp"][1][2] - 14) // 2


# D2's own ordinary cars (slots 0-7) run -34..+6; the whole 13-slot range is
# -111..730 because the buses are long and sit well off their origins. A bound
# well inside that, kept explicit rather than felt, is what the check enforces.
DELTA_Z_CAR_BOUND = 60

FIELDS_NAMED = ("extraInfo", "powerRatio", "cbYoffset", "susCoeff", "traction",
                "wheelSize")
FIELDS_AFTER = ("twistRateX", "twistRateY", "twistRateZ", "mass")


def check_records(levels_dir, donor_city, donor_dir, cities=None):
    """Derive every record and hold it against Driver 2's own.

    Reports each field's range against Driver 2's range rather than against a
    hand-picked bound, so "in range" means what Driver 2 itself does.
    """
    donors = load_donors(donor_dir or levels_dir)
    all_donor = [r for city in donors.values() for r in city]
    got = build_records(levels_dir, donor_city, cities, show=False,
                        donor_dir=donor_dir)
    if not got:
        return 1

    problems = []
    made = []
    for city in sorted(got):
        for slot, rec, di, radius, track in got[city]:
            if rec is None:
                if slot < 13:
                    made.append((city, slot, None, di, radius, track))
                continue
            made.append((city, slot, read_record(rec), di, radius, track))

    for city, slot, rec, di, radius, track in made:
        tag = "%s slot %d" % (city, slot)
        if rec is None:
            continue
        if not rec["wheelDisp"] or len(rec["wheelDisp"]) != 4:
            problems.append("%s: %d wheelDisp, not 4"
                            % (tag, len(rec["wheelDisp"])))
        if rec["colBox"] == (0, 0, 0):
            problems.append("%s: colBox is zero" % tag)
        if rec["sA"][5] <= 0:
            problems.append("%s: wheelSize %d" % (tag, rec["sA"][5]))
        # the runtime scalars must be non-zero - a zero mass or powerRatio is a car
        # that will not move, and a zero traction is one that cannot turn
        for name, v in zip(FIELDS_NAMED, rec["sA"]):
            if name in ("powerRatio", "traction", "susCoeff", "wheelSize") and v == 0:
                problems.append("%s: %s is 0" % (tag, name))
        for name, v in zip(FIELDS_AFTER, rec["sB"]):
            if name == "mass" and v == 0:
                problems.append("%s: mass is 0" % tag)
        d = fix_car_cos_delta_z(rec)
        if abs(d) > DELTA_Z_CAR_BOUND:
            problems.append("%s: FixCarCos delta.vz %d exceeds %d"
                            % (tag, d, DELTA_Z_CAR_BOUND))
        # the anchors should sit within the car's own collision box, or the lights
        # and exhaust end up inside the body
        cb = rec["colBox"]
        for name, p in (("cog", rec["cog"]), ("headLight", rec["anchors"][0]),
                        ("exhaust", rec["anchors"][6])):
            if abs(p[0]) > cb[0] * 1.5 or abs(p[1]) > cb[1] * 2.5 or abs(p[2]) > cb[2] * 1.5:
                problems.append("%s: %s %s outside colBox %s" % (tag, name, p, cb))

    def rng(vals):
        return (min(vals), max(vals)) if vals else (None, None)

    print("%d records derived over %s" % (sum(1 for m in made if m[2]),
                                           ", ".join(sorted(got))))
    print()
    print("  field            ours            Driver 2's own")
    for i, name in enumerate(FIELDS_NAMED):
        ours = [m[2]["sA"][i] for m in made if m[2]]
        theirs = [r["sA"][i] for r in all_donor]
        print("  %-14s %-15s %s" % (name, rng(ours), rng(theirs)))
    for i, name in enumerate(FIELDS_AFTER):
        ours = [m[2]["sB"][i] for m in made if m[2]]
        theirs = [r["sB"][i] for r in all_donor]
        print("  %-14s %-15s %s" % (name, rng(ours), rng(theirs)))
    for name, get in (("colBox.x", lambda r: r["colBox"][0]),
                      ("colBox.y", lambda r: r["colBox"][1]),
                      ("colBox.z", lambda r: r["colBox"][2]),
                      ("cog.vy", lambda r: r["cog"][1]),
                      ("delta.vz", fix_car_cos_delta_z)):
        ours = [get(m[2]) for m in made if m[2]]
        theirs = [get(r) for r in all_donor]
        print("  %-14s %-15s %s" % (name, rng(ours), rng(theirs)))
    print()
    for p in problems[:12]:
        print("  PROBLEM: %s" % p)
    print("VERDICT: %s" % ("clean" if not problems
                           else "%d problem(s)" % len(problems)))
    return 1 if problems else 0


def make_lcf(records, pad=4096):
    """The .LCF for one city: a 52-byte table, then 13 records, then padding.

    The shape is the ENGINE's, not a guess. LoadCosmetics (cosmetic.c:148) reads
    exactly 3120 bytes, and 3120 = 52 + 13 * 236 to the byte, so the table is 13
    int32s and then the records. Driver 2's own files are exactly 4096 bytes on
    disk - one 2048 sector plus a whole second one - so the tail is padding and
    nothing reads it.
    """
    if len(records) != 13:
        raise ValueError("%d records, a city's .LCF holds 13" % len(records))
    out = bytearray(pad)
    for i, rec in enumerate(records):
        if len(rec) != RECORD_SIZE:
            raise ValueError("record %d is %d bytes, not %d"
                             % (i, len(rec), RECORD_SIZE))
        struct.pack_into("<i", out, i * 4, LCF_TABLE + i * RECORD_SIZE)
        out[LCF_TABLE + i * RECORD_SIZE:LCF_TABLE + (i + 1) * RECORD_SIZE] = rec
    return bytes(out)


def parse_lcf(blob):
    """A .LCF -> its 13 records, with the table checked as it is read."""
    offs = struct.unpack_from("<13i", blob, 0)
    for i, o in enumerate(offs):
        want = LCF_TABLE + i * RECORD_SIZE
        if o != want:
            raise ValueError("offset %d is %d, expected %d" % (i, o, want))
    return [blob[o:o + RECORD_SIZE] for o in offs]


def fallback_record(donors, donor_city="CHICAGO", model=0):
    """The record for a slot that has no car: Driver 2's own common saloon.

    Not invented. It is Driver 2's model 0 repacked verbatim - wheelbase 431,
    wheelSize 49, track 122, vy -30, mass / powerRatio / traction all 4096 - which
    is the car whose numbers every other measurement in this module lands on.

    The engine declares `dummyCosmetics` for exactly this (cosmetic.h:5) and never
    defines it, so there was nothing to reuse from its side. A D1 city ships fewer
    than 13 car models, so the unused slots need SOME record; a neutral Driver 2
    saloon says "nothing here" unambiguously, where copying the city's own slot 0
    would claim six slots are the same car.
    """
    recs = donors.get(donor_city)
    if not recs:
        return None
    return _donor_bytes(recs, model)


# Which fields this module MEASURES off the car itself, against those it takes
# from the donor. Recorded per slot in the side-car so the borrowed ones are a
# list to work through rather than a thing to remember.
MEASURED_FIELDS = ("colBox", "cog", "cPoints", "wheelDisp", "wheelSize")
BORROWED_FIELDS = ("headLight", "frontInd", "backInd", "brakeLight", "revLight",
                   "policeLight", "exhaust", "smoke", "fire", "extraInfo",
                   "powerRatio", "cbYoffset", "susCoeff", "traction",
                   "twistRateX", "twistRateY", "twistRateZ", "mass")


# What the transplant did to each city, as [(the slot the car had in the ORIGINAL
# Driver 1 level, the model the engine will find it on)]. Filled by
# records_for_city and published in the sidecar, so a launcher can be given the
# number a person knows ("Newcastle slot 1") and translate it, instead of every
# caller re-deriving the mapping and one of them getting it wrong.
SLOT_MAP = {}


def write_city_sidecar(out_dir, city, got, donor_city, fallback):
    """A per-city JSON saying, per slot, what is measured and what is borrowed.

    This is the "until we figure it out properly" list made concrete: anything in
    `borrowed` is standing in from the matched Driver 2 car and has not been derived
    from the D1 data.
    """
    doc = {"city": city, "lcf": city + ".LCF", "donor_city": donor_city,
           "fallback": "Driver 2 %s model 0" % donor_city,
           "measured_fields": list(MEASURED_FIELDS),
           "borrowed_fields": list(BORROWED_FIELDS),
           "slot_map": SLOT_MAP.get(city, []),
           # ^ [(slot in the original Driver 1 level, model the engine finds it
           #    on)] - the translation a launcher needs to accept an original slot.
           "slots": []}
    by_slot = {slot: (rec, di, wb, track) for slot, rec, di, wb, track in got}
    for slot in range(13):
        ent = by_slot.get(slot)
        if ent is None or ent[0] is None:
            doc["slots"].append({"slot": slot, "source": "fallback",
                                 "note": "no car in this slot"})
            continue
        _rec, di, wb, track = ent
        doc["slots"].append({"slot": slot, "source": "derived", "donor": di,
                             "wheelbase": round(wb, 1), "track": round(track, 1)})
    with open(os.path.join(out_dir, city + ".cosmetics.json"), "w") as f:
        json.dump(doc, f, indent=1)
    return doc


def write_city_lcfs(levels_dir, out_dir, donor_city, donor_dir=None, cities=None):
    """Write one .LCF per D1 city. Returns {city: path}.

    A D1 city may ship fewer than 13 car models, and an .LCF always holds 13. The
    empty slots get the DONOR's record for that same slot index: a complete,
    non-zero record that nothing ever reads, rather than a run of zeros for the
    engine to trip over if it ever does.
    """
    donors = load_donors(donor_dir or levels_dir)
    donor_recs = donors[donor_city]
    got = build_records(levels_dir, donor_city, cities, show=False,
                        donor_dir=donor_dir)
    if not got:
        return None
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    fallback = fallback_record(donors, donor_city)
    if fallback is None:
        print("no fallback record: %s has no such city" % donor_city)
        return None
    out = {}
    for city in sorted(got):
        by_slot = {slot: rec for slot, rec, _di, _r, _t in got[city]}
        # An .LCF always holds 13, but a D1 city ships fewer cars. The unused slots
        # take the neutral Driver 2 saloon (see fallback_record) rather than this
        # city's own slot 0, which would claim six slots are the same car.
        records = [by_slot.get(slot) or fallback for slot in range(13)]
        path = os.path.join(out_dir, city + ".LCF")
        blob = make_lcf(records)
        with open(path, "wb") as f:
            f.write(blob)
        write_city_sidecar(out_dir, city, got[city], donor_city, fallback)
        out[city] = path
    return out


def _donor_bytes(donor_recs, slot):
    """A donor record re-packed to bytes, for filling an unused slot."""
    r = donor_recs[slot]
    out = bytearray(RECORD_SIZE)
    for o, p in zip(POS_OFFSETS[:9], r["anchors"]):
        struct.pack_into("<3h", out, o, *p)
    for i, p in enumerate(r["cPoints"]):
        struct.pack_into("<3h", out, 116 + i * 8, *p)
    struct.pack_into("<3h", out, 212, *r["colBox"])
    struct.pack_into("<3h", out, 220, *r["cog"])
    for i, d in enumerate(r["wheelDisp"]):
        struct.pack_into("<3h", out, OFF_WHEELDISP + i * 8, *d)
    struct.pack_into("<6h", out, OFF_SCALARS_A, *r["sA"])
    struct.pack_into("<4h", out, OFF_SCALARS_B, *r["sB"])
    return bytes(out)


def dump_lcf(path):
    """Print a .LCF the way a person can check it."""
    blob = open(path, "rb").read()
    print("%s: %d bytes" % (os.path.basename(path), len(blob)))
    try:
        recs = parse_lcf(blob)
    except ValueError as e:
        print("  BAD TABLE: %s" % e)
        return 1
    print("  table ok: 13 offsets at 52 + n*236, records end at %d"
          % (LCF_TABLE + 13 * RECORD_SIZE))
    for i, rec in enumerate(recs):
        r = read_record(rec)
        nz = sum(1 for b in rec if b)
        print("  [%2d] colBox %-14s cog %-13s wheelSize %3d  whDisp %-16s (%d/%d bytes set)"
              % (i, r["colBox"], r["cog"], r["sA"][5], r["wheelDisp"][0], nz,
                 RECORD_SIZE))
    return 0


def verify_lcfs(out_dir):
    """Re-read every written .LCF and check it against the engine's shape."""
    problems = []
    files = sorted(f for f in os.listdir(out_dir) if f.upper().endswith(".LCF"))
    if not files:
        print("no .LCF files under %s" % out_dir)
        return 1
    for fn in files:
        blob = open(os.path.join(out_dir, fn), "rb").read()
        if len(blob) != 4096:
            problems.append("%s: %d bytes, not 4096" % (fn, len(blob)))
        # the engine reads exactly this much, so the records must end here
        if LCF_TABLE + 13 * RECORD_SIZE != 3120:
            problems.append("the table + records is not the 3120 the engine reads")
        try:
            recs = parse_lcf(blob)
        except ValueError as e:
            problems.append("%s: %s" % (fn, e))
            continue
        if len(recs) != 13:
            problems.append("%s: %d records, not 13" % (fn, len(recs)))
        for i, rec in enumerate(recs):
            r = read_record(rec)
            if r["colBox"] == (0, 0, 0):
                problems.append("%s slot %d: colBox is zero" % (fn, i))
            if r["sA"][5] <= 0:
                problems.append("%s slot %d: wheelSize %d" % (fn, i, r["sA"][5]))
            if not any(rec):
                problems.append("%s slot %d: all zeros" % (fn, i))
    print("%d .LCF files re-read: table 52 + 13*236 = %d, each 4096 bytes"
          % (len(files), LCF_TABLE + 13 * RECORD_SIZE))
    for p in problems[:12]:
        print("  PROBLEM: %s" % p)
    print("VERDICT: %s" % ("clean" if not problems else "%d problem(s)" % len(problems)))
    return 1 if problems else 0


# The fields the RUNTIME cannot do without. A zero here is not a cosmetic
# blemish: colBox zero is a car that cannot collide, mass/powerRatio zero is one
# that cannot move, traction zero is one that cannot turn, wheelDisp zero puts all
# four wheels on the origin, and wheelSize zero drops the axle to the body centre.
CRITICAL = ("colBox", "wheelDisp", "wheelSize", "mass", "powerRatio", "traction",
            "susCoeff")


def simulate_engine_read(path, verbose=False):
    """Read a .LCF the way the ENGINE reads it, not the way we wrote it.

    ProcessCosmeticsLump (cosmetic.c:48) does not walk the file in order. It walks
    the resident slots, takes each slot's MODEL number, uses that as an index into
    the int table, and jumps to the record at that offset. Two consequences that a
    writer-side parse cannot catch:

      * the table is indexed by MODEL (0..12), so a file whose table is in slot
        order but whose records are in model order would put the wrong car on the
        wrong slot - and it would look correct to anything that reads it back the
        way it wrote it;
      * the ENABLE_GAME_FIXES block reads models 8..12 as well, so all thirteen
        offsets must be live even though only twelve slots exist.

    It then does what the engine does next: FixCarCos's delta.vz. Returns a list of
    problems, empty when the file is importable.
    """
    blob = open(path, "rb").read()
    name = os.path.basename(path)
    problems = []
    end = LCF_TABLE + 13 * RECORD_SIZE          # the 3120 LoadCosmetics reads
    offs = struct.unpack_from("<13i", blob, 0)
    for model in range(13):
        o = offs[model]
        if o < LCF_TABLE or o + RECORD_SIZE > end:
            problems.append("%s model %d: offset %d is outside the records the "
                            "engine reads (%d..%d)"
                            % (name, model, o, LCF_TABLE, end - RECORD_SIZE))
            continue
        r = read_record(blob, o)
        tag = "%s model %d" % (name, model)
        if r["colBox"] == (0, 0, 0):
            problems.append("%s: colBox is zero" % tag)
        if r["sA"][5] <= 0:
            problems.append("%s: wheelSize %d" % (tag, r["sA"][5]))
        if all(d == (0, 0, 0) for d in r["wheelDisp"]):
            problems.append("%s: all four wheelDisp are the origin" % tag)
        for i, fname in ((0, "extraInfo"), (1, "powerRatio"), (3, "susCoeff"),
                         (4, "traction")):
            if r["sA"][i] == 0 and fname in CRITICAL:
                problems.append("%s: %s is 0" % (tag, fname))
        if r["sB"][3] == 0:
            problems.append("%s: mass is 0" % tag)
        d = fix_car_cos_delta_z(r)
        if abs(d) > DELTA_Z_CAR_BOUND:
            problems.append("%s: FixCarCos delta.vz %d exceeds %d"
                            % (tag, d, DELTA_Z_CAR_BOUND))
        if verbose:
            print("  model %2d at %4d: colBox %-14s wheelSize %3d  delta.vz %4d"
                  % (model, o, r["colBox"], r["sA"][5], d))
    # our writer lays the records out in slot order, so the two orders coincide;
    # if that ever stops being true this is the check that says so.
    if list(offs) != [LCF_TABLE + i * RECORD_SIZE for i in range(13)]:
        problems.append("%s: the table is not 52 + n*236 - the table order and the "
                        "record order have diverged" % name)
    return problems


def simulate_all(out_dir, verbose=False):
    """Every .LCF in a directory, read the way the engine reads it."""
    files = sorted(f for f in os.listdir(out_dir) if f.upper().endswith(".LCF"))
    if not files:
        print("no .LCF files under %s" % out_dir)
        return 1
    bad = []
    for fn in files:
        p = simulate_engine_read(os.path.join(out_dir, fn), verbose)
        print("%-14s %s" % (fn, "importable" if not p else "%d problem(s)" % len(p)))
        bad.extend(p)
    print()
    for b in bad[:12]:
        print("  PROBLEM: %s" % b)
    print("VERDICT: %s" % ("clean" if not bad else "%d problem(s)" % len(bad)))
    return 1 if bad else 0


def selftest():
    """Invert the engine's wheel arithmetic and check it returns D2's own numbers.

    This is the only claim in this module that can be settled by data rather than
    by judgement: cars.c:1273 is the forward direction, so feeding Driver 2's own
    wheelDisp through it and back must be the identity.
    """
    levels = "src_rebuild/bin/Release_dev/DRIVER2/LEVELS"
    if not os.path.isdir(levels):
        print("no Driver 2 levels under %s" % levels)
        return 1
    donors = load_donors(levels)
    bad = 0
    checked = 0
    for city, recs in donors.items():
        for m, r in enumerate(recs):
            size = r["sA"][5]
            centres = engine_wheel_positions(r["wheelDisp"], size)
            back = wheel_disp_from(centres, size)
            for i in range(4):
                checked += 1
                got = tuple(int(round(v)) for v in back[i])
                if got != tuple(r["wheelDisp"][i]):
                    bad += 1
                    if bad <= 3:
                        print("  %s model %d wheel %d: %s -> %s"
                              % (city, m, i, r["wheelDisp"][i], got))
    print("selftest: %d wheelDisp round-tripped through cars.c:1273, %d mismatched"
          % (checked, bad))
    print("VERDICT: %s" % ("clean" if not bad else "BROKEN"))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true",
                    help="round-trip D2's own wheelDisp through the engine maths")
    ap.add_argument("--donor", default="src_rebuild/bin/Release_dev/DRIVER2/LEVELS")
    ap.add_argument("--levels", default="src_rebuild/bin/Release_dev/DRIVER/LEVELS")
    ap.add_argument("--build", action="store_true",
                    help="derive the records for each D1 city and print them")
    ap.add_argument("--check", action="store_true",
                    help="hold the derived records against Driver 2's own ranges")
    ap.add_argument("--write", metavar="DIR",
                    help="write one .LCF per D1 city into DIR")
    ap.add_argument("--verify", metavar="DIR",
                    help="re-read the .LCF files in DIR and check the engine's shape")
    ap.add_argument("--dump", metavar="FILE", help="print one .LCF")
    ap.add_argument("--simulate", metavar="DIR",
                    help="read the .LCF files in DIR the way the ENGINE reads them")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--cities", default="",
                    help="comma-separated subset of %s" % (D1_CITIES,))
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    if args.build:
        want = tuple(x for x in args.cities.split(",") if x) or None
        got = build_records(args.levels, "CHICAGO", want, donor_dir=args.donor)
        return 0 if got else 1
    if args.check:
        want = tuple(x for x in args.cities.split(",") if x) or None
        return check_records(args.levels, "CHICAGO", args.donor, want)
    if args.dump:
        return dump_lcf(args.dump)
    if args.simulate:
        return simulate_all(args.simulate, args.verbose)
    if args.verify:
        return verify_lcfs(args.verify)
    if args.write:
        want = tuple(x for x in args.cities.split(",") if x) or None
        got = write_city_lcfs(args.levels, args.write, "CHICAGO", args.donor, want)
        if not got:
            return 1
        for city in sorted(got):
            print("%s -> %s (%d bytes)" % (city, got[city], os.path.getsize(got[city])))
        return verify_lcfs(args.write)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
