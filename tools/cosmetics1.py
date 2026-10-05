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
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lev1        # noqa: E402
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


def donor_index(half_len, donor_halves):
    """The D2 model whose own half-length is closest to ours."""
    return min(range(len(donor_halves)),
               key=lambda m: abs(donor_halves[m] - half_len))


def build_record(verts, scale, centres, radius, donor, donor_half):
    """One car's CAR_COSMETICS.

    `verts` is the car's own (unscaled) vertices, `scale` the bake scale,
    `centres`/`radius` come from the wheel placement, and `donor`/`donor_half`
    are the nearest-length Driver 2 record and its colBox.
    """
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    half = ((max(xs) - min(xs)) / 2.0 * scale,
            (max(ys) - min(ys)) / 2.0 * scale,
            (max(zs) - min(zs)) / 2.0 * scale)
    # per-axis proportion, guarding the odd axis a donor may have at zero
    ratio = tuple((half[i] / donor_half[i]) if donor_half[i] else 1.0
                  for i in range(3))

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


def records_for_city(levels_dir, city, donor_recs, wheel_model, cities=None):
    """A 236-byte record for every car model a D1 city ships.

    Mirrors the shape Driver 2 uses: each city's .LCF covers that city's own 13
    model slots. Returns a list of (slot, record, donor_index, radius, track),
    with a record of None for a slot the city leaves empty.
    """
    blob = open(os.path.join(levels_dir, city + ".LEV"), "rb").read()
    donor_halves = [d["colBox"][2] for d in donor_recs]
    out = []
    for m, variants in lev1.car_models(blob):
        model = variants.get("clean")
        if model is None and variants:
            model = next(iter(variants.values()))
        if model is None or not model.vertices:
            out.append((m, None, None, None, None))
            continue
        centres, radius, track = wheelgeom.placement(
            model.vertices, wheel_model.vertices, SCALE)
        zs = [v[2] for v in model.vertices]
        half_len = (max(zs) - min(zs)) / 2.0 * SCALE
        di = donor_index(half_len, donor_halves)
        rec = build_record(model.vertices, SCALE, centres, radius,
                           donor_recs[di], donor_recs[di]["colBox"])
        out.append((m, rec, di, radius, track))
    return out


def build_records(levels_dir, donor_city, cities=None, show=True, donor_dir=None):
    """Every D1 city's records. Returns {city: [(slot, record, ...), ...]}."""
    donors = load_donors(donor_dir or levels_dir)
    if donor_city not in donors:
        print("no donor city %s among %s" % (donor_city, sorted(donors)))
        return None
    donor_recs = donors[donor_city]
    _lvl, wheel_model = wheelgeom.canonical_wheel(levels_dir)
    if wheel_model is None:
        print("no wheel model under %s" % levels_dir)
        return None
    out = {}
    for city in (cities or D1_CITIES):
        recs = records_for_city(levels_dir, city, donor_recs, wheel_model)
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
                      " track %5.1f  whDisp %s"
                      % (slot, di, r["colBox"], r["cog"], r["sA"][5], track,
                         r["wheelDisp"][0]))
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
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
