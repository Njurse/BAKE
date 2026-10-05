#!/usr/bin/env python3
"""spin.py - rotating previews of the baked pool.

Two sets of GIFs, both a full turn at a slight elevation, textured and lit the
same way the static previews are:

    out/spin_baked.gif        every model in one grid, turning together
    out/spin/<n>_<level>_<m>.gif   one GIF per model

The turn is around the vertical axis; the elevation is fixed and small, so the
roof stays visible and the shape reads as a car rather than a plan view.

Usage:
    python spin.py [--baked FILE] [--frames 32] [--cell 200] [--elev 20]
"""

import argparse
import math
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preview     # noqa: E402  (LIGHT, _png)
import textured    # noqa: E402  (baked_pages, baked_items, _fill_tex)
import wheelrender  # noqa: E402

LEVELS_DIR = "src_rebuild/bin/Release_dev/DRIVER/LEVELS"

HERE = os.path.dirname(os.path.abspath(__file__))


def project(v, cy, sy, cp, sp):
    """The static previews' projection with the yaw and pitch supplied.

    Same convention throughout: the game's y is DOWN-positive, so screen y
    follows +y and must not be negated, or every car renders upside down.
    """
    x, y, z = v
    cx = x * cy + z * sy
    cz = -x * sy + z * cy
    return cx, (y * cp - cz * sp), cz


def refs_for(items, pitch, cell, pad, samples=48):
    """A fixed (centre3d, scale) per item, so a turning model does not breathe.

    A car is long and thin, so its PROJECTED span changes with the yaw: wide
    seen from the side, narrow seen head-on. Scaling each frame to its own span
    therefore zooms the model in as it comes round to face the camera. Measure
    the worst case over a full turn once and use it for every frame, and centre
    on the model's own 3D middle rather than on each frame's projected box.
    """
    out = []
    cp, sp = math.cos(pitch), math.sin(pitch)
    for item in items:
        verts = item[2]
        if not verts:
            out.append(None)
            continue
        lo = [min(v[i] for v in verts) for i in range(3)]
        hi = [max(v[i] for v in verts) for i in range(3)]
        centre = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
        worst = 1
        for k in range(samples):
            cy, sy = math.cos(2 * math.pi * k / samples), math.sin(2 * math.pi * k / samples)
            xs = []
            ys = []
            for v in verts:
                px, py, _pz = project(v, cy, sy, cp, sp)
                xs.append(px)
                ys.append(py)
            worst = max(worst, max(xs) - min(xs), max(ys) - min(ys))
        out.append((centre, (cell - 2 * pad) / float(worst)))
    return out


def frame(items, pages, refs, yaw, pitch, cols, cell, pad=10):
    """One RGB frame: every item drawn at this yaw with its fixed framing."""
    rows = (len(items) + cols - 1) // cols
    w, h = cols * cell, rows * cell
    pix = bytearray(w * h * 3)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    for idx, item in enumerate(items):
        _label, _key, verts, faces = item[0], item[1], item[2], item[3]
        if not verts or refs[idx] is None:
            continue
        centre, sc = refs[idx]
        proj = [project(v, cy, sy, cp, sp) for v in verts]
        # the model's own middle, projected - a constant anchor, so the car does
        # not drift around its cell as it turns
        anchor = project(centre, cy, sy, cp, sp)
        ccx = (idx % cols) * cell + cell / 2.0
        ccy = (idx // cols) * cell + cell / 2.0
        scr = [(ccx + (q[0] - anchor[0]) * sc, ccy + (q[1] - anchor[1]) * sc)
               for q in proj]

        order = sorted(range(len(faces)),
                       key=lambda fi: -sum(proj[i][2] for i in faces[fi][0]))
        for fi in order:
            entry = faces[fi]
            ind, uv, setid = entry[0], entry[1], entry[2]
            texid = entry[3] if len(entry) > 3 else -1
            n = entry[4] if len(entry) > 4 else None
            if len(ind) != 3 or setid < 0:
                continue
            page = pages.get(setid)
            if not page:
                continue
            if n is None:
                a, b, c = verts[ind[0]], verts[ind[1]], verts[ind[2]]
                u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
                v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
                n = (v[1] * u[2] - v[2] * u[1], v[2] * u[0] - v[0] * u[2],
                     v[0] * u[1] - v[1] * u[0])
            # the normal turns with the model, so the shading stays put as it spins
            nx = n[0] * cy + n[2] * sy
            ny = n[1]
            nz = -n[0] * sy + n[2] * cy
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            L = preview.LIGHT
            d = max(0.0, (nx * L[0] + ny * L[1] + nz * L[2]) / ln)
            shade = 0.45 + 0.75 * d

            idx_img, crows = page[0], page[1]
            masks = page[2] if len(page) > 2 else None
            pal = crows[texid] if 0 <= texid < len(crows) else crows[0]
            if masks:
                sk = masks[texid] if 0 <= texid < len(masks) else masks[0]
            else:
                sk = None
            textured._fill_tex(pix, w, h,
                               [scr[ind[0]], scr[ind[1]], scr[ind[2]]],
                               uv, idx_img, 256, 256, pal=pal, shade=shade,
                               skip=sk)
    return pix, w, h


def _image(pix, w, h):
    from PIL import Image
    im = Image.frombytes("RGB", (w, h), bytes(pix))
    return im.convert("P", palette=Image.ADAPTIVE, colors=255)


def save_gif(path, frames, duration):
    from PIL import Image
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=duration, loop=0, disposal=2,
                   optimize=False)
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baked", default=os.path.join(HERE, "out", "bake", "D1POOL.LEV"))
    ap.add_argument("--frames", type=int, default=32)
    ap.add_argument("--cell", type=int, default=200)
    ap.add_argument("--elev", type=float, default=20.0,
                    help="elevation in degrees (the plan asked for a slight one)")
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--wheeled", action="store_true",
                    help="draw the wheels on and TURN them a little every frame")
    ap.add_argument("--wheel-turns", type=float, default=3.0,
                    help="whole wheel revolutions per full turn of the car")
    ap.add_argument("--levels-dir", default=LEVELS_DIR,
                    help="source DRIVER/LEVELS, for the wheel model and its texture")
    args = ap.parse_args(argv)

    if not os.path.exists(args.baked):
        print("no baked blob at %s - run bake.py first" % args.baked)
        return 1

    blob = open(args.baked, "rb").read()
    pages = textured.baked_pages(blob)
    items = textured.baked_items(args.baked)
    if not items or not pages:
        print("the baked blob has no models or no pages")
        return 1

    pitch = math.radians(args.elev)
    duration = max(40, int(1000 / max(args.frames / 1.5, 1)))

    # The wheels, if asked for. They are re-made per frame with a spin angle so
    # the tyre texture visibly turns - which is the whole point of having them on.
    wheel_arg = None
    if args.wheeled:
        wm, wlevel, wpage = wheelrender.load_wheel(args.levels_dir)
        if wm is None:
            print("no wheel model under %s" % args.levels_dir)
            return 1
        if wpage:
            pages[wheelrender.WHEEL_SET_BASE] = wpage
        pl = os.path.join(args.out, "wheels", "placement.json")
        if not os.path.exists(pl):
            print("no %s - run wheels1.py --place first" % pl)
            return 1
        wheel_arg = (wm, {i["pool_index"]: i for i in json.load(open(pl))["items"]})
        print("wheels from %s, set %d, %.1f turn(s) of the wheel per turn of the car"
              % (wlevel, wheelrender.WHEEL_SET_BASE, args.wheel_turns))

    def at(spin):
        """The items as they stand at this wheel angle."""
        if wheel_arg is None:
            return items
        wm, recs = wheel_arg
        out = []
        for n, it in enumerate(items):
            r = recs.get(n)
            out.append(wheelrender.wheeled_item(it, wm, r["centres"], r["scale"],
                                                spin=spin) if r else it)
        return out

    turns = 2.0 * math.pi * args.wheel_turns
    stem = "spin_wheeled" if args.wheeled else "spin_baked"

    # one grid GIF, every model turning together
    grid = []
    grefs = refs_for(items, pitch, args.cell, 10)
    for i in range(args.frames):
        yaw = 2.0 * math.pi * i / args.frames
        pix, w, h = frame(at(turns * i / args.frames), pages, grefs, yaw, pitch,
                          args.cols, args.cell)
        grid.append(_image(pix, w, h))
    g = save_gif(os.path.join(args.out, stem + ".gif"), grid, duration)
    print("grid: %s (%dx%d, %d frames, %d shapes)"
          % (g, grid[0].size[0], grid[0].size[1], len(grid), len(items)))

    # and one per model
    d = os.path.join(args.out, stem)
    if not os.path.isdir(d):
        os.makedirs(d)
    for n, item in enumerate(items):
        label = "%d_%s" % (n, str(item[0]).replace("/", "_").replace(" ", ""))
        irefs = refs_for([item], pitch, args.cell, 14)
        fr = []
        for i in range(args.frames):
            yaw = 2.0 * math.pi * i / args.frames
            one = at(turns * i / args.frames)[n]
            pix, w, h = frame([one], pages, irefs, yaw, pitch, 1, args.cell, pad=14)
            fr.append(_image(pix, w, h))
        save_gif(os.path.join(d, label + ".gif"), fr, duration)
    print("per model: %d GIFs in %s" % (len(items), d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
