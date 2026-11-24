#!/usr/bin/env python3
"""copmarkers.py - find a car's police lights by putting them in the model.

Working out where a light goes from numbers is slow: the engine's axes are not
Blender's, the lamp is measured from the car's centre of gravity, and a wrong
sign puts it under the floor. So don't. Put a small marker object where the lamp
should be, export the OBJ as you always do, and let this read it back.

    1. In Blender, add any small object (a cube, a plane, one vertex) at the lamp
       - snap the 3D cursor to the beacon, Add > Mesh > Cube, scale it down.
       - name it COPLIGHT_<anything>, e.g.  COPLIGHT_L_blue   COPLIGHT_R_blue_p128
    2. Export the OBJ as usual. The marker rides along in the same coordinates as
       the car, so no axis or unit conversion is guessed - it is the car's own.
    3. python copmarkers.py cars/<car>/handling.json          (look)
       python copmarkers.py cars/<car>/handling.json --write  (store them)
    4. Rebake. The bake leaves markers out of the car's polys, so they never show.

Marker name -> lamp.  After COPLIGHT, separate the words with _ - . or space:
    red blue amber white   the colour (default red)
    p<n>                   flash phase 0..255 (p128 flashes opposite p0)
    m | mirror             also draw the lamp on the other side (x negated)
    s<n>                   size in percent of a stock lamp (s60 = smaller)
    anything else          just a label, kept as the lamp's `name`

The lamp is stored as `at`, the point in the model, exactly as the OBJ has it.
The engine draws the body at cog + vertex and a light at cog + position, so a
light's vector is its OBJ coordinate: nothing is subtracted or converted.

The scale and flips have to be the ones the bake uses, or the point lands
somewhere else: the scale comes from the bank's declare.scale as the bake reads
it, and --flip-y / --flip-z are the rebake mode's flags.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bankjson   # noqa: E402
import obj2geom   # noqa: E402

D1_OBJ_SCALE = 0.68267      # what carbank_bake uses when a bank names no scale

# Blender names a new object's mesh after its shape; those words say nothing
BLENDER_SHAPES = {"cube", "plane", "sphere", "icosphere", "cylinder", "cone", "mesh",
                  "circle", "torus"}


def lamp_from_marker(group, point, index):
    """One police lamp, as handling.policeLights writes it, from a marker's
    group name and (x, y, z) in engine units."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", group) if w]

    if words and words[0].lower().startswith(obj2geom.MARKER_PREFIX):
        # COPLIGHT, or COPLIGHT_L glued together as COPLIGHTL: keep what follows
        rest = words[0][len(obj2geom.MARKER_PREFIX):]
        words = ([rest] if rest else []) + words[1:]

    color, phase, mirror, size, label = "red", 0, False, 100, []

    for w in words:
        low = w.lower()

        if low in bankjson.POLICE_LIGHT_COLORS:
            color = low
        elif re.fullmatch(r"p\d+", low):
            phase = min(int(low[1:]), 255)
        elif re.fullmatch(r"s\d+", low):
            size = max(1, min(int(low[1:]), 255))
        elif low in ("m", "mirror", "mir"):
            mirror = True
        elif re.fullmatch(r"\d{3}", w):
            continue                      # Blender's .001 duplicate suffix
        elif low in BLENDER_SHAPES or low == obj2geom.MARKER_PREFIX:
            continue                      # a default mesh name, or the prefix again
                                          # (Blender writes object_mesh: both halves)
        elif w not in label:
            label.append(w)               # Blender writes object_mesh: say it once

    lamp = {"name": "_".join(label) or "light%d" % (index + 1),
            "at": [int(round(c)) for c in point], "color": color}

    if phase:
        lamp["phase"] = phase
    if mirror:
        lamp["mirror"] = True
    if size != 100:
        lamp["size"] = size

    return lamp


def to_engine(point, scale, flip_y=False, flip_z=False):
    """The bake's transform (obj2geom.build_geom): flips, then the scale."""
    x, y, z = point
    return (x * scale, y * (-1.0 if flip_y else 1.0) * scale,
            z * (-1.0 if flip_z else 1.0) * scale)


def find_bank_scale(car_json, bank_path=None):
    """(scale, where it came from): the bank's declare.scale, else the Driver 1
    ratio, which is exactly what the bake would do."""
    candidates = [bank_path] if bank_path else [
        os.path.join(os.path.dirname(car_json), "..", "..", "bank.json"),
        os.path.join(os.path.dirname(car_json), "..", "bank.json"),
        os.path.join(os.path.dirname(car_json), "bank.json"),
    ]

    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    decl = json.load(f).get("declare") or {}
            except (OSError, ValueError):
                continue
            if isinstance(decl.get("scale"), (int, float)):
                return float(decl["scale"]), "declare.scale in %s" % os.path.normpath(path)

    return D1_OBJ_SCALE, "the bake's default (the bank names no scale)"


def read_lamps(obj_path, scale, flip_y=False, flip_z=False):
    markers = obj2geom.read_markers(obj_path)
    return [lamp_from_marker(name, to_engine(pt, scale, flip_y, flip_z), i)
            for i, (name, pt) in enumerate(markers)]


def apply_to_handling(data, lamps, replace_style=False):
    """Set data["handling"]["policeLights"]["lights"] from the markers, keeping any
    `speed` the author set. Refuses to quietly throw away a `style`."""
    handling = data.get("handling")

    if not isinstance(handling, dict):
        raise SystemExit("this handling.json has no \"handling\" block to put the "
                         "lamps in")

    spec = handling.get("policeLights")

    if isinstance(spec, dict) and "style" in spec and not replace_style:
        raise SystemExit("this car wears the %s light bar (policeLights.style). "
                         "Pass --replace-style to swap it for the marker lamps."
                         % spec["style"])

    new = {}
    if isinstance(spec, dict) and "speed" in spec:
        new["speed"] = spec["speed"]
    new["lights"] = lamps
    handling["policeLights"] = new

    return bankjson.police_lights("copmarkers", "car", handling)   # validate


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Read COPLIGHT_* marker objects out of a car's OBJ and write "
                    "them as handling.policeLights.")
    ap.add_argument("car", help="cars/<car>/handling.json, or an OBJ to just look")
    ap.add_argument("--write", action="store_true",
                    help="store the lamps in handling.json (default: only show them)")
    ap.add_argument("--replace-style", action="store_true",
                    help="allow --write to replace a policeLights.style")
    ap.add_argument("--variant", default="clean", choices=("clean", "damaged", "low"),
                    help="which OBJ of the car holds the markers (default clean)")
    ap.add_argument("--bank", help="bank.json, to read declare.scale (default: found "
                                   "two folders above the car's handling.json)")
    ap.add_argument("--scale", type=float, help="OBJ to engine units (default: the bank's)")
    ap.add_argument("--flip-y", action="store_true", help="the rebake mode's --flip-y")
    ap.add_argument("--flip-z", action="store_true", help="the rebake mode's --flip-z")
    args = ap.parse_args(argv)

    data = None
    car_json = None

    if args.car.lower().endswith(".json"):
        car_json = os.path.abspath(args.car)
        with open(car_json, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        eol = "\r\n" if "\r\n" in text else "\n"    # --write keeps the file's own
        data = json.loads(text)
        rel = data.get(args.variant)
        if not rel:
            raise SystemExit("%s names no %r OBJ" % (args.car, args.variant))
        obj_path = os.path.normpath(os.path.join(os.path.dirname(car_json), rel))
    else:
        obj_path = args.car

    if not os.path.isfile(obj_path):
        raise SystemExit("no such OBJ: %s" % obj_path)

    if args.scale is not None:
        scale, why = args.scale, "--scale"
    elif car_json:
        scale, why = find_bank_scale(car_json, args.bank)
    else:
        scale, why = 1.0, "no bank given: engine units assumed (pass --scale)"

    lamps = read_lamps(obj_path, scale, args.flip_y, args.flip_z)

    print("%s\n  scale %g (%s)%s%s" % (
        obj_path, scale, why,
        "  flip-y" if args.flip_y else "", "  flip-z" if args.flip_z else ""))

    if not lamps:
        print("  no COPLIGHT_* markers. Add a small object named COPLIGHT_<name> at "
              "each lamp,\n  export the OBJ again and re-run.")
        return 1

    print("  %-16s %-18s %s" % ("lamp", "at (model x, y, z)", "look"))

    for lamp in lamps:
        look = lamp["color"] + ("  phase %d" % lamp["phase"] if "phase" in lamp else "") \
            + ("  mirrored" if lamp.get("mirror") else "") \
            + ("  size %d%%" % lamp["size"] if "size" in lamp else "")
        print("  %-16s %-18s %s" % (lamp["name"], lamp["at"], look))

    if len(lamps) > bankjson.POLICE_LIGHTS_MAX:
        raise SystemExit("%d markers, but a car holds at most %d lamps"
                         % (len(lamps), bankjson.POLICE_LIGHTS_MAX))

    if not args.write:
        print("\n  (nothing written - add --write to store these in handling.json)")
        return 0

    if data is None:
        raise SystemExit("--write needs the car's handling.json, not an OBJ")

    apply_to_handling(data, lamps, args.replace_style)

    out = bankjson.dumps_compact(data) + "\n"
    with open(car_json, "w", encoding="utf-8", newline="") as f:
        f.write(out.replace("\n", eol))

    print("\n  wrote handling.policeLights (%d lamp%s) to %s"
          % (len(lamps), "" if len(lamps) == 1 else "s", car_json))
    print("  rebake to bake them into the bank.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
