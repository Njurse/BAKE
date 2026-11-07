#!/usr/bin/env python3
"""preview_bank.py - where a bank declaration says a car's art lives.

The vehicle preview reads `declare.cars.<car>`: a car may be inline or a path
to its own handling.json, its wheel set may live on a different car, and the
wheel layout comes from handling values in ENGINE units. This module turns
that declaration into paths and numbers; it draws nothing.
"""

import json
import os


def load_bank(path):
    """(bank root, parsed bank.json) for a bank.json or its workspace dir."""
    if os.path.isdir(path):
        path = os.path.join(path, "bank.json")

    with open(path, encoding="utf-8") as f:
        bank = json.load(f)

    return os.path.dirname(os.path.abspath(path)), bank


def resolve_entry(bank_root, entry):
    """(root, car dict) for a declare.cars value.

    A car may be a path to its own file (cars/<car>/handling.json), whose paths
    are relative to that file - as bankjson reads it.
    """
    if isinstance(entry, str):
        path = os.path.normpath(os.path.join(bank_root, entry))

        with open(path, encoding="utf-8") as f:
            return os.path.dirname(path), json.load(f)

    return bank_root, entry or {}


def cars_of(bank):
    return (bank.get("declare") or {}).get("cars") or {}


def resolve_car(bank_root, bank, key=None):
    """(car name, root, car) for --car, or the first declared car."""
    cars = cars_of(bank)

    if not cars:
        return None, bank_root, {}

    key = key or next(iter(cars))
    root, car = resolve_entry(bank_root, cars[key])
    return key, root, car


def resolve_wheels(bank_root, bank, car, root):
    """(root, car) that OWNS the wheel set - usually the previewed car.

    The bank's one wheel set lives on one car; a slot without wheels borrows
    the first car that declares them.
    """
    if car.get("wheels"):
        return root, car

    for other in cars_of(bank).values():
        other_root, other_car = resolve_entry(bank_root, other)

        if other_car.get("wheels"):
            return other_root, other_car

    return root, car


def clean_model(root, car):
    """Absolute path to the clean OBJ (the mesh the preview draws)."""
    return os.path.join(root, car.get("clean", ""))


def wheel_layout(car, extents, center, lo, scale, flip_y=False, bake_scale=None,
                 ride=30.0):
    """(tyre radius, [[x, y, z] x4], tyre width) in preview space.

    The car's own handling.json wheelDisp/wheelSize are ENGINE units (Y-down,
    = OBJ * bake --scale), else a guess from the bounding box. The hub formula
    is the one cars.c draws with, so the preview's wheels sit where the game's
    do; susCompression is the settled rest value (`ride`).
    """
    import preview

    disp, wsize, cog = None, None, None
    handling = car.get("handling") or {}

    if handling.get("wheelDisp") and handling.get("wheelSize"):
        disp = handling["wheelDisp"]
        wsize = handling["wheelSize"]
        cog = handling.get("cog")

    # handling colBox is engine half-extents. A Driver 1 OBJ (REDBUICK and the
    # rest of the pool) is ~3000/2048 times that box; treating every mesh longer
    # than 50 as already engine-sized left the wheels on a short wheelbase and
    # the body reading huge next to them. The baked LEV is already scaled, which
    # is why the same car is normal in game.
    col = handling.get("colBox") if handling else None
    S = bake_scale if bake_scale else preview.infer_bake_scale(extents, col)

    if disp:
        rad = wsize / S
        # 1) hub relative to the BODY MODEL ORIGIN, D2 engine space (Y-down),
        #    exactly as cars.c draws them in one frame (same matrix, same pos):
        #      body  (DrawCarObject): model origin = pos + cog
        #      wheel (DrawCarWheels): hub = pos + (17 - disp.x  [wheels 0,1]
        #                                          -17 - disp.x  [wheels 2,3],
        #                                          -wheelSize - disp.y - susCompression + 14,
        #                                          -disp.z)
        #    so hub - body = that vector - cog. (FixCarCos moves disp.z and
        #    cog.z by the same delta, so it cancels here.) susCompression is
        #    the settled rest value -> --ride.
        cg = cog or (0, 0, 0)

        def engine_hub(i, d):
            return ((17 if (i & 2) == 0 else -17) - d[0] - cg[0],
                    -wsize - d[1] - ride + 14 - cg[1],
                    -d[2] - cg[2])

        # 2) ONE conversion into preview/OBJ space (+Y up): the bake's
        #    --flip-y only negates Y, so (x, y, z) -> (x, -y, z). Ride and
        #    suspension therefore only ever move the hub vertically.
        def engine_to_preview(e):
            # --flip-y art is already Y-down engine space and the draw
            # flips it with the body, so don't negate twice
            return (e[0] / S, (e[1] if flip_y else -e[1]) / S, e[2] / S)

        wpos = [engine_to_preview(engine_hub(i, d)) for i, d in enumerate(disp)]
    else:
        rad = extents[1] * 0.3
        wpos = [(sx * extents[0] * 0.45 + center[0], lo[1] + rad,
                 sz * extents[2] * 0.32 + center[2])
                for sx in (1, -1) for sz in (1, -1)]

    rad *= scale
    wpos = [[(p[k] - center[k]) * scale for k in range(3)] for p in wpos]

    return rad, wpos, rad * 0.55
