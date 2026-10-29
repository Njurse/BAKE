#!/usr/bin/env python3
"""carbank_export.py - write a bank in the editable layout bank-schema.md describes.

    python carbank_export.py banks/firebird/FIREBIRD.LEV -o work/firebird
    python carbank_export.py work/firebird/bank.json -o work/firebird --declare

What it writes, per car slot the bank carries:

    cars/<slot>_<name>/clean.obj      GT3/GT4 faces, 0-255 UVs, normals
    cars/<slot>_<name>/damaged.obj    when the bank has one
    cars/<slot>_<name>/low.obj        when the bank has one
    cars/<slot>_<name>/<name>.mtl     one material per (set, id), or "flat"
    pages/page<set>.png               one PNG per 4bpp page
    pages/palettes<set>.png           that page's CLUT rows, as a strip
    wheels/wheels.json                which variants the car carries

and, with --declare, the `declare` block in the bank's bank.json pointing at
those files, validated by bankjson before it is written.

Wheels: the bank's LUMP_CAR_WHEELS segment (type 200) holds wheel MODELS, one
per variant, with -1 for a variant the car does not have - which is exactly the
case bank-schema.md describes for the Firebird (clean and fast, no damaged). The
records are written to `wheels/wheels.json`, and with --declare into the
declaration as `{set, island}`: the page its poly 2 names and the crop rect its
UVs cover. That is what a bake needs to carry the wheel again, so exporting a
bank and re-baking it keeps the wheels rather than quietly dropping them. A bank
that carries no wheel segment has only the author's wheel PNGs, which name no
page, and those are copied as plain paths.

The scale is the one the baker and the engine already use (see obj2geom --scale
and bake.py's model path); the OBJ is written in the bank's own units, so a
re-import with `--scale 1` reproduces the geometry.
"""

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exportd2
import fourbpp                  # noqa: F401  (the page codec lives there now)
import bake
import bankjson
import lev1


def _levels_scale():
    """The scale the bake uses when it reads an OBJ back (obj2geom's default)."""
    return 0.68267


def _page_indices(blob, off, size, where):
    """One page's indices. d2_page_entries' offsets are ABSOLUTE in the blob."""
    raw = blob[off:off + size]

    if not raw:
        raise SystemExit("%s: page runs past the end of the bank" % where)

    if len(raw) == 32768:
        return exportd2.page_indexes(raw)

    idx, _n = fourbpp.decomp_asm(raw, 32768)

    if len(idx) != 32768:
        raise SystemExit("%s: a compressed page decoded to %d bytes, expected 32768"
                         % (where, len(idx)))

    return idx


def _mtl_materials(model):
    names = []

    for p in model.polys:
        if len(p.v) < 3:
            continue

        textured = (p.kind in exportd2.TEXTURED_KINDS
                    and p.texture_set not in (0, 255))
        name = ("set%d_id%d" % (p.texture_set, p.texture_id)
                if textured else "flat")

        if name not in names:
            names.append(name)

    return names


def _write_mtl(path, materials):
    with open(path, "w") as f:
        for name in materials:
            f.write("newmtl %s\n" % name)

            if name == "flat":
                f.write("Kd 0.501961 0.501961 0.501961\n")
            else:
                # the colour comes from the page + CLUT; the .mtl only names it
                f.write("Kd 0.800000 0.800000 0.800000\n")

            f.write("illum 1\n\n")


def _decode_names(raw):
    """The model-name table is NUL-separated ASCII; slot order is the index."""
    out = {}

    if not raw:
        return out

    for i, part in enumerate(raw.split(bytes([0]))):
        name = part.decode("ascii", "replace").strip()

        if name:
            out[i] = name.replace(" ", "_")

    return out

def _wheel_variants(blob):
    """{variant: True/False} from the bank's LUMP_CAR_WHEELS, or None."""
    body, size = bake._data1_segment(blob, bake.LUMP_CAR_WHEELS)

    if body is None:
        return None

    n = struct.unpack_from("<i", blob, body)[0]

    if n < 1 or n > len(bake.WHEEL_ORDER):
        return None

    out = {}

    for i, variant in enumerate(bake.WHEEL_ORDER):
        if i >= n:
            out[variant] = False
            continue

        ofs = struct.unpack_from("<i", blob, body + 4 + i * 4)[0]
        out[variant] = ofs != -1

    return out


VARIANT_NAMES = {"CLEANWHEEL": "clean", "DAMWHEEL": "damaged",
                 "FASTWHEEL": "fast"}


def _wheel_records(blob):
    """{variant: {set, island}} for the wheels the bank carries, or {}.

    A bank with a LUMP_CAR_WHEELS segment says exactly what a declaration needs
    to carry it again: the page (poly 2's texture_set) and the crop rect (poly
    2's UVs). Writing the declaration WITHOUT that would drop the segment on the
    next bake, because a wheel image alone cannot name a page.
    """
    body, size = bake._data1_segment(blob, bake.LUMP_CAR_WHEELS)

    if body is None:
        return {}

    n = struct.unpack_from("<i", blob, body)[0]
    out = {}

    for i, name in enumerate(bake.WHEEL_ORDER):
        if i >= n:
            break

        ofs = struct.unpack_from("<i", blob, body + 4 + i * 4)[0]

        if ofs < 0 or ofs + 36 > size:
            continue

        model = lev1.decode_model(blob, body + ofs)

        if len(model.polys) <= 2 or model.errors:
            continue

        poly = model.polys[2]
        us = [u for u, _v in poly.uv]
        vs = [v for _u, v in poly.uv]

        out[VARIANT_NAMES.get(name, name.lower())] = {
            "set": poly.texture_set,
            # WheelPolyRect adds 1 to the maxima, so the rect it derives - and the
            # declaration states - is exclusive, exactly as it was baked
            "island": [min(us), min(vs), max(us) + 1, max(vs) + 1],
        }

    return out


WHEEL_SOURCES = {
    "clean": ("wheelclean.png", "wheel_clean.png", "cleanwheel.png"),
    "damaged": ("wheeldamaged.png", "wheeldam.png", "wheelsdamaged.png"),
    "fast": ("wheelfast.png", "wheel_fast.png", "fastwheel.png"),
}


def _wheel_pngs_from_sources(bankdir, outdir):
    """Copy the author's wheel PNGs when the bank carries no wheel segment.

    A bank baked from a donor has no LUMP_CAR_WHEELS, so its wheels are the
    source PNGs the author supplied. The Firebird has two (clean and fast) and
    no damaged one, and that absence is preserved: a variant with no file is
    left out of the declaration rather than faked.
    """
    out = {}
    srcdir = os.path.join(bankdir, "source")

    for variant, names in WHEEL_SOURCES.items():
        for name in names:
            for d in (srcdir, os.path.join(bankdir, "textures"), bankdir):
                path = os.path.join(d, name)

                if os.path.isfile(path):
                    dest = os.path.join(outdir, "wheels", "%s.png" % variant)
                    os.makedirs(os.path.dirname(dest), exist_ok=True)

                    with open(path, "rb") as f:
                        data = f.read()

                    with open(dest, "wb") as f:
                        f.write(data)

                    out[variant] = os.path.relpath(dest, outdir)
                    break

            if variant in out:
                break

    return out


def export(path, outdir, declare=False):
    with open(path, "rb") as f:
        blob = f.read()

    _off, _size, _bytes, ti = exportd2.d2_texture_info(blob)
    _page_base, entries = exportd2.d2_page_entries(blob, ti)
    pal = exportd2.d2_palette(blob)
    table = exportd2.d2_model_table(blob)
    names = _decode_names(exportd2.model_names(blob))
    wheels = _wheel_variants(blob)
    # a bank that carries wheel models names their pages and crop rects; one that
    # does not has the author's wheel images and nothing else
    wheel_records = _wheel_records(blob)

    included = sorted(entries)
    scale = _levels_scale()
    cars = {}
    order = []

    pages_dir = os.path.join(outdir, "pages")
    os.makedirs(pages_dir, exist_ok=True)

    page_files = {}

    for setid in included:
        off, size, _kind = entries[setid]
        idx = _page_indices(blob, off, size, path)

        png = os.path.join(pages_dir, "page%d.png" % setid)
        exportd2.page_png(png, _page_rows(blob, pal, setid), idx)
        page_files[setid] = os.path.relpath(png, outdir)

        strip = os.path.join(pages_dir, "palettes%d.png" % setid)
        exportd2.palette_png(strip, pal["records"], [setid])

    for slot in range(len(table["offsets"])):
        models = exportd2.decode_variants(blob, table, slot)

        if not models:
            continue

        name = names.get(slot) or ("model%d" % slot)
        carname = "%d_%s" % (slot, name)
        cardir = os.path.join(outdir, "cars", carname)
        os.makedirs(cardir, exist_ok=True)

        entry = {"pages": [{"set": s, "png": page_files[s]} for s in included],
                 "wheels": {}}

        materials = set()

        for variant, model in models.items():
            materials.update(_mtl_materials(model))
            obj = os.path.join(cardir, "%s.obj" % variant)
            exportd2.write_obj(obj, model, carname, carname + ".mtl")
            entry[variant] = os.path.relpath(obj, outdir)

        _write_mtl(os.path.join(cardir, carname + ".mtl"), sorted(materials))

        entry["wheels"] = wheel_records or {}
        cars[carname] = entry
        order.append({"slot": slot, "car": carname})

    wheels = _wheel_variants(blob)
    wheel_pngs = {}

    if not wheel_records:
        wheel_pngs = _wheel_pngs_from_sources(os.path.dirname(os.path.abspath(path)),
                                             outdir)
    wheels_dir = os.path.join(outdir, "wheels")
    os.makedirs(wheels_dir, exist_ok=True)

    with open(os.path.join(wheels_dir, "wheels.json"), "w") as f:
        json.dump({"variants": wheels, "records": wheel_records or None,
                   "note":
                   "variants: true = the bank carries this wheel model. "
                   "records: the page set and crop rect it names, which is what "
                   "a declaration needs to carry it again"},
                  f, indent=2)

    for carname in cars:
        cars[carname]["wheel_pngs"] = wheel_pngs

    return {"cars": cars, "order": order, "pages": page_files,
            "wheels": wheels, "wheel_pngs": wheel_pngs, "scale": scale}


def _page_rows(blob, pal, setid):
    return [r["clut"] for r in pal["records"]
            if r["tpageindex"] == setid and r["clut"] is not None]


PATH_KEYS = ("clean", "damaged", "low", "atlas")


def _rebase(decl, outdir, base):
    """Make every path in a declaration relative to the bank.json folder."""
    def rel(rel_path):
        return os.path.relpath(os.path.join(outdir, rel_path), base).replace(os.sep, "/")

    for car in decl["cars"].values():
        for key in PATH_KEYS:
            if key in car:
                car[key] = rel(car[key])

        for page in car.get("pages") or []:
            page["png"] = rel(page["png"])

        for variant, rel_path in list((car.get("wheels") or {}).items()):
            # {set, island} entries carry no path to rebase
            if isinstance(rel_path, str):
                car["wheels"][variant] = rel(rel_path)

    return decl


def _declare_block(result, name):
    cars = {}

    for carname, car in result["cars"].items():
        entry = {"pages": car["pages"]}
        entry.update({k: car[k] for k in PATH_KEYS if k in car})

        if car.get("wheels"):
            entry["wheels"] = dict(car["wheels"])
        elif car.get("wheel_pngs"):
            entry["wheels"] = dict(car["wheel_pngs"])

        cars[carname] = entry

    return {"schema": 1, "name": name, "order": result["order"], "cars": cars}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bank", help="a bank/city .LEV, or its bank.json")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--declare", action="store_true",
                    help="write the declare block into the bank's bank.json")
    args = ap.parse_args(argv)

    path = os.path.abspath(args.bank)

    if path.lower().endswith(".json"):
        bank = bankjson.load(path)
        lev = os.path.join(os.path.dirname(path), bank.raw.get("lev") or "")

        if not os.path.isfile(lev):
            raise SystemExit("%s: bank.json names lev %r, which is not there"
                             % (path, bank.raw.get("lev")))

        name = bank.raw.get("city") or bank.name or "bank"
    else:
        lev = path
        name = os.path.splitext(os.path.basename(path))[0]
        path = None

    os.makedirs(args.out, exist_ok=True)
    result = export(lev, os.path.abspath(args.out))

    print("exported %s -> %s" % (os.path.basename(lev), args.out))
    print("  cars:  %s" % ", ".join(sorted(result["cars"])))
    print("  pages: %s" % ", ".join(str(s) for s in sorted(result["pages"])))
    print("  wheels: %s" % json.dumps(result["wheels"] or result["wheel_pngs"]))
    print("  scale: %s (the bake's OBJ scale)" % result["scale"])

    if args.declare:
        if path is None:
            raise SystemExit("--declare needs a bank.json to write into; pass one")
        decl = _rebase(_declare_block(result, name),
                       os.path.abspath(args.out),
                       os.path.dirname(os.path.abspath(path)))
        bankjson.write_declare(path, decl)

        # prove it reads back before claiming it was written
        bankjson.load(path)
        print("  declare: written to %s and re-read clean" % path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
