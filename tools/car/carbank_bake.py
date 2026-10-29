#!/usr/bin/env python3
"""carbank_bake.py - bake an edited bank back into a Driver 2-shaped .LEV.

    python carbank_bake.py banks/firebird/bank.json -o banks/firebird/newbank
    python carbank_bake.py banks/firebird/bank.json -o ... --variants clean,damaged

The bank is a DRIVER 2 car-data bank (LUMP_CAR_MODELS 28, LUMP_PALLET 25,
LUMP_TEXTUREINFO 34), so it is assembled by exportd2.build_bank - the writer that
already reproduces FIREBIRD.LEV byte for byte. The Driver 1 pool assembler
(bake.build_file) is a different shape and is reachable only through --d1, where
the input is explicitly a Driver 1 LEV.

What comes from the declaration (bank.json's `declare` half):

  * each car's clean/damaged/low OBJ, read back with its real texture materials
  * the slot order

A car that declares fewer than the three variants a Driver 2 model needs gets a
WARNING and has the missing ones filled with the CLEAN model. A Driver 2 model
with an empty damaged or low block does not fail to load - the engine builds a
car with no polygons in that state and draws nothing, which reads as "the car
disappeared when it got hit". Substituting clean keeps every state drawable.

Anything the declaration does not name is left exactly as the source bank had
it, and a bake of an unchanged declaration reproduces the bank byte for byte.
"""

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))

import bake
import bankjson
import carbank_import
import exportd2
import struct
import exportd2

VARIANTS = ("clean", "damaged", "low")


def _warn(msg):
    print("carbank_bake: warning: %s" % msg, file=sys.stderr)


def _source_colors(lev_path):
    """{variant: [(r, g, b), ...]} read from the bank being replaced.

    An OBJ has nowhere to put a per-poly colour, so the round trip invents the
    neutral grey a Driver 1 flat poly carries. For a Driver 2 car the poly
    colour is a TINT that multiplies the texture, and the donor's is a real
    dark tint - re-baking with grey made the whole car brighter than the bank
    it replaced. The colours therefore come from the bank's own current polygon
    stream, matched by index (the export is 1:1).
    """
    if not lev_path or not os.path.isfile(lev_path):
        return {}

    try:
        with open(lev_path, "rb") as f:
            blob = f.read()
        table = exportd2.d2_model_table(blob)
        out = {}
        for variant in VARIANTS:
            try:
                model = exportd2.decode_variants(blob, table, 1)[variant]
            except (KeyError, IndexError, ValueError):
                continue
            out[variant] = [tuple(p.color) for p in model.polys]
        return out
    except (OSError, ValueError, struct.error) as exc:
        _warn("could not read poly colours from %s (%s); the bake will use "
              "neutral grey" % (lev_path, exc))
        return {}


def _geom_for(obj_path, scale, default_page=None, flip_y=False, flip_winding=False,
               colors=None, flip_z=False):
    """The geom record for one OBJ, with its materials and normals."""
    geom = carbank_import.import_car(obj_path, scale=scale, default_page=default_page,
                                     flip_y=flip_y, flip_winding=flip_winding,
                                     flip_z=flip_z)

    if colors and len(colors) == len(geom["polys"]):
        for poly, color in zip(geom["polys"], colors):
            poly["color"] = list(color)
    elif colors:
        _warn("%s has %d polys but the bank has %d; keeping the source "
              "colours would put them on the wrong panels, so the neutral "
              "tint is used" % (obj_path, len(geom["polys"]), len(colors)))

    return geom


def _block_for(variant, geom, clean_block, scale, flip_normals=False):
    """One raw model block from a geom record."""
    if variant == "damaged":
        if clean_block is None:
            raise SystemExit("carbank_bake: a damaged block needs its clean "
                             "block first")

        return bake.build_damaged_record(geom["vertices"], scale, clean_block)

    return bake.build_model_record(geom["vertices"], geom["polys"],
                                   geom["face_normals"], geom["point_normals"],
                                   scale, flip_normals=flip_normals)


def car_blocks(car, scale, default_page=None, flip_y=False, flip_winding=False, colors=None,
               flip_normals=False, flip_z=False):
    """{variant: block bytes} for one declared car, plus the warnings it earned.

    A missing damaged or low is filled with the CLEAN model rather than left
    empty: Driver 2 always reads all three blocks, and an empty one draws a car
    with no polygons in that state.
    """
    geoms = {}

    for variant in VARIANTS:
        path = getattr(car, variant, None) if variant != "clean" else car.clean

        if path:
            geoms[variant] = _geom_for(path, scale, default_page, flip_y, flip_winding,
                                       (colors or {}).get(variant), flip_z)

    if "clean" not in geoms:
        raise SystemExit("carbank_bake: car %r has no clean OBJ" % car.label)

    warnings = []
    blocks = {}
    blocks["clean"] = _block_for("clean", geoms["clean"], None, scale,
                                 flip_normals)

    for variant in ("damaged", "low"):
        if variant in geoms:
            blocks[variant] = _block_for(variant, geoms[variant],
                                         blocks["clean"], scale, flip_normals)
        else:
            warnings.append(
                "%s declares no %s OBJ; using the clean model for it so that "
                "state still draws" % (car.label, variant))
            blocks[variant] = blocks["clean"]

    # the low-detail block may legitimately be the clean model at a lower
    # vertex count; when it is absent that is what we just did, deliberately

    return blocks, warnings


def source_lev_of(bank):
    """The LEV whose poly colours this bake should keep.

    Prefer the untouched donor: a bank that has already been baked through the
    OBJ round trip carries the neutral grey an OBJ forces, so reading its own
    polygons would bake grey forever. The donor (or an explicitly declared
    donor.lev) is the only place the original tints survive.
    """
    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    lev = bank.raw.get("lev") or ""
    stem = os.path.splitext(os.path.basename(lev))[0] if lev else ""

    declared = getattr(bank, "declared", None)
    donor_lev = getattr(declared, "donor_lev", None) if declared else None
    candidates = []
    if donor_lev:
        candidates.append(os.path.join(bank_dir, donor_lev))
    if stem:
        candidates.append(os.path.join(bank_dir, stem + ".donor.LEV"))
    candidates.append(os.path.join(bank_dir, lev))

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""


def build_manifest(bank, out_dir, scale, blocks_dir, default_page=None, flip_y=False,
                   flip_winding=False, colors=None, flip_normals=False, flip_z=False):
    """A manifest exportd2.build_bank can assemble, from the declaration."""
    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    lev_name = bank.raw.get("lev") or ""
    source_lev = os.path.join(bank_dir, lev_name)
    # the rebuild manifest is <CITY>.json beside the bank (FIREBIRD.json), not
    # bank.json itself: bank.json carries the declaration, that one the raw blocks
    source_manifest_path = os.path.join(
        bank_dir,
        bank.raw.get("manifest")
        or (os.path.splitext(lev_name)[0] + ".json" if lev_name else ""))

    # start from the SOURCE manifest (it carries the page blobs, the palette
    # sets and the cosmetics record) and replace only the models and the order:
    # a bank is more than its geometry, and a cherry-picked manifest loses the
    # pages silently ("bank page omitted") and writes a bank that cannot draw
    if os.path.isfile(source_manifest_path):
        with open(source_manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {}

    manifest["city"] = bank.raw.get("city") or bank.name
    manifest["source"] = {"path": source_lev}

    # the source manifest's paths are relative to the BANK folder; this one
    # lives in out_dir, so make them absolute rather than copy a broken path
    for key in ("raw",):
        manifest[key] = {k: (v if os.path.isabs(str(v)) else os.path.join(bank_dir, str(v)))
                         for k, v in (manifest.get(key) or {}).items()}

    if manifest.get("pages"):
        manifest["pages"] = [
            dict(page, png=(page.get("png") if not page.get("png") else
                            page["png"] if os.path.isabs(str(page["png"]))
                            else os.path.join(bank_dir, str(page["png"]))))
            for page in manifest["pages"]]
    # Every DECLARED page has to exist in the manifest: the assembler carves a
    # page only when something names it, so a declared-but-unlisted set is baked
    # to disk and pruned straight back out ("1 sets"). The wheel art lives on
    # its own sets, which the donor manifest has never heard of.
    listed_sets = {page.get("set") for page in (manifest.get("pages") or [])}
    for carname in bank.cars:
        for decl_page in bank.cars[carname].pages:
            # bankjson hands pages back as (set, png) pairs
            setid, png = ((decl_page["set"], decl_page.get("png"))
                          if isinstance(decl_page, dict) else decl_page[:2])
            if setid in listed_sets:
                continue
            listed_sets.add(setid)
            manifest.setdefault("pages", []).append({"set": setid, "png": png})

    _sync_page_sizes(manifest, bank_dir, out_dir)


    # the DECLARATION owns the slot order: the source manifest still names the
    # donor ids, which no longer exist once the cars are rebuilt
    manifest["bank_order"] = [{"slot": slot, "car": carname}
                                for slot, carname in bank.order]

    os.makedirs(blocks_dir, exist_ok=True)
    warnings = []

    by_car = {}

    for slot, carname in bank.order:
        car = bank.cars[carname]
        blocks, car_warnings = car_blocks(car, scale, default_page, flip_y, flip_winding, colors,
                                          flip_normals, flip_z)
        warnings.extend(car_warnings)

        by_car[carname] = {"id": carname, "model": slot, "sets": [],
                           "variants": {}}

        for variant, data in blocks.items():
            name = "%d_%s_%s.bin" % (slot, carname, variant)
            path = os.path.join(blocks_dir, name)

            with open(path, "wb") as f:
                f.write(data)

            by_car[carname]["variants"][variant] = {"raw": path}

    manifest["models"] = list(by_car.values())


    # the sets a car names come from its own geom, not the declaration: a car
    # may use any page the source bank carries
    for model in manifest["models"]:
        car = bank.cars[model["id"]]
        geom = _geom_for(car.clean, scale, default_page, flip_y, flip_winding, None, flip_z)
        model["sets"] = sorted({p["set"] for p in geom["polys"]
                                if p["set"] not in (0, 255)})

    # A page the DECLARATION adds but no rebuilt body model samples is a wheel
    # page. This has to run HERE, after the loop above fills model["sets"]:
    # reading it earlier is what made the first attempt treat every page as a
    # wheel page, sweep the donor's set 65 in and grow the bank to 106496 bytes.
    body_sets = set()
    for model in manifest["models"]:
        body_sets.update(model.get("sets") or [])

    declared_sets = set()
    for carname in bank.cars:
        for decl_page in bank.cars[carname].pages:
            declared_sets.add(decl_page["set"] if isinstance(decl_page, dict)
                              else decl_page[0])

    wheel_sets = sorted(declared_sets - body_sets)
    if wheel_sets:
        manifest["wheel_sets"] = wheel_sets

    return manifest, warnings


def _sync_page_sizes(manifest, bank_dir, out_dir):
    """Rebuild LUMP_TEXTUREINFO when a page on disk is not the size it claims.

    A page's size lives in the textureinfo lump, and the bank carves pages from
    it. Replacing PAGE_<set>.bin with an atlas that packs to a different size
    therefore has to restate it - otherwise the bake truncates the new page to
    the old length (measured: a 33060-byte atlas cut to 30960, and only a
    warning to show for it) and the car draws garbage from a half page.
    """
    raw = manifest.get("raw") or {}
    ti_path = raw.get("textureinfo")

    if not ti_path or not os.path.isfile(ti_path):
        return

    with open(ti_path, "rb") as f:
        ti_bytes = f.read()

    head, perms, specs = exportd2.split_textureinfo(ti_bytes)
    changed = False
    sizes = {}

    def fix(entries):
        nonlocal changed
        out = []

        for setid, size in entries:
            path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)

            if os.path.isfile(path):
                actual = os.path.getsize(path)
                sizes[setid] = actual

                if actual != size:
                    changed = True
                    size = actual

            out.append((setid, size))

        return out

    perms = fix(perms)
    specs = fix(specs)

    # a page the declaration adds is not in textureinfo yet, and fix() can only
    # restate the size of one that is: append it, with the size of the file on
    # disk, or the assembler has no length to carve
    listed = {setid for setid, _ in perms}
    for page in manifest.get("pages") or []:
        setid = page.get("set")
        if setid in listed:
            continue
        path = os.path.join(bank_dir, "pages", "PAGE_%d.bin" % setid)
        if os.path.isfile(path):
            size = os.path.getsize(path)
            perms.append((setid, size))
            sizes[setid] = size
            listed.add(setid)
            changed = True

    for page in manifest.get("pages") or []:
        if page.get("set") in sizes:
            page["size"] = sizes[page["set"]]

    if not changed:
        return

    rebuilt = exportd2.build_textureinfo(head, perms, specs)

    ti_out = os.path.join(bank_dir, "textureinfo.bin")

    with open(ti_out, "wb") as f:
        f.write(rebuilt)

    manifest["raw"] = dict(raw, textureinfo=ti_out)
    _warn("page size(s) changed; rebuilt LUMP_TEXTUREINFO to match the files "
          "on disk")

def bake_bank(bank_path, out_dir, scale=0.68267, d1=None, default_page=None,
              flip_y=False, flip_winding=False, flip_normals=False, flip_z=False):
    """Bake the declared bank into out_dir. Returns (ok, warnings)."""
    if d1:
        raise SystemExit("carbank_bake: --d1 bakes a Driver 1 pool LEV with "
                         "bake.build_file; a bank is Driver 2 shaped and goes "
                         "through exportd2.build_bank")

    bank = bankjson.load(bank_path)

    if not bank.declared:
        # nothing declared: the assembly must reproduce the bank untouched
        _warn("%s declares nothing; re-baking the source bank unchanged"
              % bank_path)

    os.makedirs(out_dir, exist_ok=True)
    # the blocks belong in the workspace's models/, so the manifest this writes
    # back still describes the bank after the bake
    blocks_dir = os.path.join(os.path.dirname(os.path.abspath(bank.path)),
                              "models")

    bank_dir = os.path.dirname(os.path.abspath(bank.path))
    # Y mirror and winding are SEPARATE: reversing the winding is how an
    # inside-out car is fixed when the mesh is already the right way up.
    colors = _source_colors(source_lev_of(bank))
    manifest, warnings = build_manifest(bank, out_dir, scale, blocks_dir, default_page, flip_y,
                                       flip_winding,
                                       colors, flip_normals, flip_z)

    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the MANIFEST, so
    # the manifest is written beside the bank (and removed afterwards)
    # build_bank resolves raw/pages/PAGE_<set>.bin relative to the manifest, so it
    # must sit beside the bank; writing it under the CITY name keeps the workspace
    # self-describing (a later --rebuild-bank reproduces what is installed)
    manifest_path = os.path.join(
        bank_dir,
        os.path.splitext(bank.raw.get("lev") or "bank.LEV")[0] + ".json")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)

    exportd2.build_bank(manifest, manifest_path, out_dir, None)

    return True, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("bank", help="the bank.json with the declare block")
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--scale", type=float, default=0.68267,
                    help="OBJ scale (default 0.68267, the D2/D1 cell ratio)")
    ap.add_argument("--default-page", metavar="SET:ID", default=None,
                    help="page for an OBJ whose materials are not set<S>_id<T> "
                         "(an author export); without it they are refused")
    ap.add_argument("--flip-z", action="store_true",
                    help="negate Z as well as Y: the pair is a 180-degree rotation about X, "
                         "which is what a Z-up mesh needs - a mirror alone is inside out")
    ap.add_argument("--flip-normals", action="store_true",
                    help="negate the stored inward normals; a mesh exported with the "
                         "opposite winding convention otherwise shades inside-out")
    ap.add_argument("--flip-y", action="store_true",
                    help="negate Y: the engine's model space is Y-down, a Blender OBJ is Y-up")
    ap.add_argument("--flip-winding", action="store_true",
                    help="reverse every face: required with --flip-y, or the car is inside-out")
    ap.add_argument("--d1", metavar="LEV",
                    help="bake a DRIVER 1 LEV instead (pool shape, bake.build_file)")
    args = ap.parse_args(argv)

    if args.d1:
        raise SystemExit("carbank_bake: --d1 is not implemented in this tool; "
                         "the Driver 1 pool bake is tools/car/bake.py")

    default_page = None
    if args.default_page:
        s_, i_ = args.default_page.split(":")
        default_page = (int(s_), int(i_))

    ok, warnings = bake_bank(args.bank, os.path.abspath(args.out), args.scale,
                             default_page=default_page,
                             flip_y=args.flip_y, flip_winding=args.flip_winding,
                             flip_normals=args.flip_normals, flip_z=args.flip_z)

    print("baked %s -> %s" % (os.path.basename(args.bank), args.out))

    for w in warnings:
        _warn(w)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
