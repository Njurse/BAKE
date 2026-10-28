#!/usr/bin/env python3
"""carbank_import.py - read an edited bank back: OBJ with its real materials, PNGs page by page.

    python carbank_import.py work/cars/1_BASE2/clean.obj -o out/clean_geom.json
    python carbank_import.py work/cars/1_BASE2/clean.obj -o out/clean_geom.json \
        --set-map 65:66 --id-map 12:13

The OBJ half closes the gap obj2geom had: every poly used to come back on one
solid grey page (set=1, id=0), so an edited model lost its texture pages on the
way in. carbank_export writes a `usemtl set<S>_id<T>` group per (set, id) and
`flat` for untextured panels; this reads those names back, so a round trip keeps
the materials the model was exported with. A name that is neither is refused
rather than silently greyed.

The PNG half reads an edited page (or atlas) as RGB, ready for fourbpp's
re-quantiser: `edit_page_rect` for a page that keeps its palette, `paletteize`
when an edit brings colours the existing CLUT cannot express.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))

import fourbpp
import obj2geom


def import_car(obj_path, scale=0.68267, grey=False,
               set_map=None, id_map=None, default_page=None,
               flip_y=False, flip_winding=False):
    """An OBJ (as carbank_export wrote it) -> a geom record bake.py can use.

    `default_page` is (set, id) for an OBJ whose materials are not named
    set<S>_id<T> - an author export from Blender, say. Without it those names are
    refused, because a guessed page draws a panel with the wrong texture.
    """
    verts, uvs, faces, names = obj2geom.parse_obj(obj_path, with_materials=True)
    unmapped = []
    # The kind is encoded in the material NAME, so it has to be read before the
    # names are turned into (set, id) page tuples.
    kinds = [obj2geom.kind_of_material(n) for n in names] if names else None
    mats = obj2geom.materials_to_pages(names, obj_path, default_page, unmapped)

    if unmapped:
        print("carbank_import: %s: %d author material(s) mapped to set%d_id%d: %s"
              % (obj_path, len(unmapped), default_page[0], default_page[1],
                 ", ".join(sorted(set(unmapped)))), file=sys.stderr)

    if set_map or id_map:
        mats = [_remap_page(page, set_map, id_map) for page in mats]

    name = os.path.splitext(os.path.basename(obj_path))[0]

    return obj2geom.build_geom(verts, uvs, faces, scale, grey, name, mats,
                               flip_y, flip_winding, kinds)


def _remap_page(page, set_map, id_map):
    if page is None:
        return None

    setid, texid = page
    setid = (set_map or {}).get(setid, setid)
    texid = (id_map or {}).get(texid, texid)

    return (setid, texid)


def read_page(path, expect=(fourbpp.PAGE_W, fourbpp.PAGE_H)):
    """(width, height, RGB pixels) for an edited page PNG, size-checked."""
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")
        w, h = img.size

        if expect and (w, h) != expect:
            raise ValueError("%s is %dx%d, expected %dx%d (a texture page)"
                             % (path, w, h, expect[0], expect[1]))

        pixels = list(img.getdata())

    return w, h, pixels


def read_rect(path, rect):
    """The RGB pixels inside `rect` (x, y, w, h), row-major.

    This is what an edit needs: the bake re-quantises the rectangle, not the
    whole page, so untouched texels keep their exact indices.
    """
    x, y, w, h = (int(v) for v in rect)
    from PIL import Image

    with Image.open(path) as img:
        img = img.convert("RGB")

        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > img.size[0] \
                or y + h > img.size[1]:
            raise ValueError("%s: rect %r is outside the %dx%d image"
                             % (path, tuple(rect), img.size[0], img.size[1]))

        crop = img.crop((x, y, x + w, y + h))

    return list(crop.getdata())


def _parse_map(text, what):
    out = {}

    for pair in (text or "").split(","):
        pair = pair.strip()

        if not pair:
            continue

        if ":" not in pair:
            raise SystemExit("--%s wants src:dst pairs, got %r" % (what, pair))

        src, dst = pair.split(":", 1)
        out[int(src)] = int(dst)

    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("obj", help="an edited OBJ (usemtl set<S>_id<T> / flat)")
    ap.add_argument("-o", "--out", help="geom.json to write (default <obj>_geom.json)")
    ap.add_argument("--scale", type=float, default=0.68267,
                    help="bake scale (default 0.68267, the D2/D1 cell ratio)")
    ap.add_argument("--grey", action="store_true",
                    help="force every poly onto a solid grey page")
    ap.add_argument("--set-map", default="", help="set remap, e.g. 65:66,1:2")
    ap.add_argument("--id-map", default="", help="id remap, e.g. 12:13")
    args = ap.parse_args(argv)

    rec = import_car(args.obj, args.scale, args.grey,
                     _parse_map(args.set_map, "set-map"),
                     _parse_map(args.id_map, "id-map"))

    out = args.out or (os.path.splitext(args.obj)[0] + "_geom.json")

    with open(out, "w") as f:
        json.dump(rec, f, indent=1)

    pages = sorted({(p["set"], p["id"]) for p in rec["polys"]})

    print("wrote %s (%d verts, %d polys, pages %s)"
          % (out, len(rec["vertices"]), len(rec["polys"]),
             ", ".join("set%d_id%d" % (s, i) for s, i in pages)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
