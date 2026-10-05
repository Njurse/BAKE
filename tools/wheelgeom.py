#!/usr/bin/env python3
"""wheelgeom.py - where a wheel goes, and how big it is.

Driver 1 ships no wheel positions - it has no cosmetics at all - so they have to
come from somewhere. They come from Driver 2's own cars, read out of
DRIVER2/LEVELS/*.LCF and put through the engine's own placement arithmetic
(cars.c:1273):

    x =  +17 - wheelDisp.vx     (wheels 0/1)
    x =  -17 - wheelDisp.vx     (wheels 2/3)
    z = -wheelDisp.vz
    y = -wheelSize - wheelDisp.vy - susCompression + 14

Two of the numbers are not free choices, they are measurements:

  * the effective wheel CENTRE sits at +/-105.5 - the disp's 122 pulled in by the
    wheel model's own half width, 17;
  * the wheel's RADIUS is wheelSize, 49, and D1's own wheel model run through the
    same D1->D2 bake scale has radius 49.8. The two games' wheels are the SAME
    size, so nothing has to be invented there either.

Note what `wheelSize` is NOT: it does not scale the wheel. DrawWheelObject draws
the model at its native size (cars.c:1264 uses the model's own vertices); the
radius only sets the axle height. That is why D1's wheel needs the same bake
scale as the body and nothing more.

Usage:
    python wheelgeom.py --donor <DRIVER2/LEVELS>     # derive and print the donor
"""

import argparse
import os
import struct
import sys

# Fields of CAR_COSMETICS (236 bytes), offsets measured from the struct.
OFF_WHEELDISP = 72          # SVECTOR wheelDisp[4], 8 bytes each
OFF_WHEELSIZE = 114         # short wheelSize
COSMETICS_SIZE = 236
LCF_TABLE = 52              # 13 x int32 offsets

# Frozen from Driver 2's own cars. See donor_from_lcfs() - these are derived, not
# chosen, and the function that derives them is the provenance.
DONOR_WHEELBASE = 431       # z distance between the axles: 233 - (-198)
DONOR_WHEEL_RADIUS = 49     # the axle height, and D1's wheel on the D2 scale
DONOR_TYRE_HALFWIDTH = 17   # the wheel model's own half width
DONOR_TRACK_CENTRE = 105    # |x| of the effective wheel centre


def donor_from_lcfs(levels_dir):
    """Derive the donor numbers from Driver 2's own .LCF files.

    Returns (wheelbase, radius, tyre_halfwidth, track_centre, per_city) where
    per_city is {city: [(vx, vy, vz), ...]} for the model-0 car, so the claim
    "D2's common car is like this" can be checked rather than believed.
    """
    per_city = {}
    sizes = []
    bases = []
    centres = []
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LCF"):
            continue
        blob = open(os.path.join(levels_dir, fn), "rb").read()
        offs = struct.unpack_from("<13i", blob, 0)
        city = fn[:-4]
        # model 0 is the common car in every city
        o = offs[0]
        wd = [struct.unpack_from("<hhh", blob, o + OFF_WHEELDISP + i * 8)
              for i in range(4)]
        ws = struct.unpack_from("<h", blob, o + OFF_WHEELSIZE)[0]
        per_city[city] = wd
        zs = [w[2] for w in wd]
        bases.append(max(zs) - min(zs))          # the wheelbase
        sizes.append(ws)
        centres.append(abs(wd[0][0]) - DONOR_TYRE_HALFWIDTH)
    if not per_city:
        return None
    bases.sort()
    sizes.sort()
    centres.sort()
    mid = len(bases) // 2
    return (bases[mid], sizes[mid], DONOR_TYRE_HALFWIDTH, centres[mid], per_city)


def wheel_radii(levels_dir, name="CLEANWHEEL"):
    """{level: raw radius} for every level that names this wheel."""
    import lev1

    out = {}
    for fn in sorted(os.listdir(levels_dir)):
        if not fn.upper().endswith(".LEV"):
            continue
        blob = open(os.path.join(levels_dir, fn), "rb").read()
        if lev1.models_lump(blob) is None:
            continue
        m = lev1.level_model(blob, name)
        if not m or not m.vertices:
            continue
        wy = [v[1] for v in m.vertices]
        wz = [v[2] for v in m.vertices]
        out[fn[:-4]] = max(max(wy) - min(wy), max(wz) - min(wz)) / 2.0
    return out


def canonical_wheel(levels_dir, name="CLEANWHEEL"):
    """The wheel model D1's cars actually share -> (level, Model).

    Most car levels agree EXACTLY - 25 x 73 x 73 on FRISCO, LA, MIAMI, NEWYORK and
    TRAIN - while CREDITS and IVIEW carry far larger versions at a different scale
    from their own car models (CREDITS' is radii out at ~1500, i.e. bigger than
    the car). Taking the median radius keeps those display assets out of the car
    pipeline, instead of letting one level's UI model set the wheels for everyone.
    """
    import lev1

    radii = wheel_radii(levels_dir, name)
    if not radii:
        return None, None
    ordered = sorted(radii, key=lambda lv: radii[lv])
    pick = ordered[len(ordered) // 2]
    blob = open(os.path.join(levels_dir, pick + ".LEV"), "rb").read()
    return pick, lev1.level_model(blob, name)


def placement(body_verts, wheel_verts, scale, wheelbase=DONOR_WHEELBASE):
    """The four wheel centres for one car, and the wheel's own radius.

    `body_verts` and `wheel_verts` are plain (x, y, z) sequences - the car's
    vertices and the wheel model's - and `scale` is the bake scale the body was
    drawn at. The wheels are anchored on the CAR's own bbox, so the tyres sit on
    the ground line rather than wherever the model origin happens to be, and the
    axles straddle that bbox's z centre.

    The track is per car: half the body width less the tyre's half width, so the
    tyre sits flush with the body's side. The WHEELBASE is, by choice, the one
    shared number.
    """
    bx = [v[0] for v in body_verts]
    by = [v[1] for v in body_verts]
    bz = [v[2] for v in body_verts]
    wx = [abs(v[0]) for v in wheel_verts]
    wy = [v[1] for v in wheel_verts]
    wz = [v[2] for v in wheel_verts]

    radius = max(max(wy) - min(wy), max(wz) - min(wz)) / 2.0 * scale
    half_tyre = max(wx) * scale

    half_width = (max(bx) - min(bx)) / 2.0 * scale
    zc = (max(bz) + min(bz)) / 2.0 * scale
    ground = max(by) * scale                      # y is DOWN-positive
    xc = (max(bx) + min(bx)) / 2.0 * scale

    track = max(0.0, half_width - half_tyre)
    z_axle = wheelbase / 2.0
    out = []
    for i in range(4):
        sx = -1 if (i & 2) == 0 else 1            # wheels 0/1 left, 2/3 right
        sz = 1 if (i & 1) == 0 else -1            # 0/2 front, 1/3 rear
        out.append((xc + sx * track, ground - radius, zc + sz * z_axle))
    return out, radius, track


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--donor", default="src_rebuild/bin/Release_dev/DRIVER2/LEVELS",
                    help="a Driver 2 LEVELS dir, to derive the donor numbers from")
    args = ap.parse_args(argv)

    got = donor_from_lcfs(args.donor)
    if not got:
        print("no .LCF files under %s" % args.donor)
        return 1
    wheelbase, radius, half_tyre, centre, per_city = got
    print("derived from %s" % args.donor)
    for city in sorted(per_city):
        wd = per_city[city]
        zs = [w[2] for w in wd]
        print("  %-8s model 0 wheelDisp %s -> wheelbase %d, |x| centre %d"
              % (city, wd, max(zs) - min(zs), abs(wd[0][0]) - half_tyre))
    bases = sorted(max(w[2] for w in per_city[c]) - min(w[2] for w in per_city[c])
                   for c in per_city)
    print()
    print("the four cities' model-0 wheelbases: %s (median %d)" % (bases, wheelbase))
    print("FROZEN: wheelbase %d, radius %d, tyre half-width %d, track centre %d"
          % (DONOR_WHEELBASE, DONOR_WHEEL_RADIUS, DONOR_TYRE_HALFWIDTH,
             DONOR_TRACK_CENTRE))
    print()
    print("The frozen wheelbase is CHICAGO's model 0, which is what the approved")
    print("plan named. The choice is a small one among near-equal values - the")
    print("four cities span %d..%d and their ordinary saloons (model slots 5-7)"
          % (bases[0], bases[-1]))
    print("all share 397 - and it only applies to the wheel-less D1 traffic: a")
    print("real per-car value would come from a D1 cosmetics table, which does")
    print("not exist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
