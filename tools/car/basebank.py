#!/usr/bin/env python3
"""basebank.py - scaffold a one-car car-data bank for a mod.

A bank is a .LEV + optional .LCF registered from JERICHO/CONFIG/cities.ini.
This tool builds the smallest useful one: ONE donor car from a Driver 2 city,
in one destination model slot, with only that car's model blocks, texture sets,
palette rows and cosmetics. The other model slots stay empty (-1).

The output folder is self-contained:

    NAME.LEV                 the loadable bank
    NAME.LCF                 cosmetics, donor car at the chosen slot
    NAME.json                manifest; also accepted by exportd2 --rebuild-bank
    cities.ini               the [cities] line to paste into CONFIG/cities.ini
    README.txt               what this is and how to install it
    models/*.bin             the donor car's clean/damaged/low blocks
    pages/PAGE_*.bin         page entries for the sets this car uses
    pages/PAGE_*.png         the same pages through CLUT row 0
    pallet.bin               the donor city's palette lump (bank filters it)
    palette.png/.json        resolved car colours for those sets
    source.LCF               the donor city's cosmetics source
    cosmetics/*.cos          the donor car's 236-byte record
    cosmetics.json           every record in that source, for re-baking

The bank folder IS the workspace: those files sit beside NAME.LEV, not
under a raw/ subdirectory.

Usage:
    python basebank.py --city CHICAGO --model 1 --name FIREBIRD \
        --out JERICHO/MODS/betacars/banks/firebird
    python basebank.py --lev LEVELS/CHICAGO.LEV --model 1 --name BASE

Driver 2 only; Driver 1 banks are the bake.py/d1pool.py path. Rebuild after
editing the raw blocks with:
    python exportd2.py --rebuild-bank NAME.json -o .
"""

import argparse
import json
import os
import re
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cosmetics1          # noqa: E402
import exportd2            # noqa: E402
import pages1              # noqa: E402


def _repo_root():
    return os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))


def _default_game_levels():
    return os.path.join(_repo_root(), "src_rebuild", "bin", "Release_dev",
                        "DRIVER2", "LEVELS")


def resolve_level(city, game_data=None):
    """The .LEV for a D2 city, from --game-data or the repo's dev install."""
    city = city.upper()
    candidates = []
    if game_data:
        candidates.append(os.path.join(game_data, city + ".LEV"))
    candidates.append(os.path.join(_default_game_levels(), city + ".LEV"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise SystemExit("no %s.LEV under %s" % (city, " or ".join(
        os.path.dirname(p) for p in candidates)))


def _sanitize_name(name):
    name = name.strip().upper()
    if not re.match(r"^[A-Z0-9_]+$", name):
        raise SystemExit("bank name %r must be letters, digits and underscore"
                         % name)
    return name


def _write_json(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")


def _sets_for(variants):
    out = set()
    for model in variants.values():
        for p in model.polys:
            if (p.kind in exportd2.TEXTURED_KINDS
                    and p.texture_set not in (0, 255)):
                out.add(p.texture_set)
    return out


def _write_palette_json(path, pal, sets):
    rows = [r for r in pal["records"] if r["tpageindex"] in sets]
    _write_json(path, {
        "total_cluts": pal["total_cluts"],
        "sets": sorted({r["tpageindex"] for r in rows}),
        "records": [{
            "palette": r["palette"],
            "texnum": r["texnum"],
            "tpageindex": r["tpageindex"],
            "clut_number": r["clut_number"],
            "clut": r["clut"].hex() if r["clut"] else None,
            "rgb": pages1.clut_rgb(r["clut"]) if r["clut"] else None,
        } for r in rows],
    })


def build_base(args):
    name = _sanitize_name(args.name) if args.name else "BASE"
    if args.out:
        out = os.path.abspath(args.out)
    else:
        out = os.path.join(HERE, "out", "basebank", name)
    if os.path.isdir(out) and os.listdir(out) and not args.force:
        raise SystemExit("%s is not empty; pass --force to overwrite it" % out)

    if args.slot == 0:
        raise SystemExit("model slot 0 is carhacks' 'no car' slot; use 1..12")
    if not 0 <= args.slot < exportd2.MAX_CAR_MODELS:
        raise SystemExit("slot %d is outside 0..%d"
                         % (args.slot, exportd2.MAX_CAR_MODELS - 1))

    lev = os.path.abspath(args.lev) if args.lev else resolve_level(
        args.city, args.game_data)
    with open(lev, "rb") as f:
        blob = f.read()
    if exportd2.level_game(blob) != "d2":
        raise SystemExit("%s is not a Driver 2 level; exportd2/basebank only "
                         "handle D2 banks" % lev)

    table = exportd2.d2_model_table(blob)
    present = [m for m in range(exportd2.MAX_CAR_MODELS)
               if table["offsets"][m][0] >= 0]
    if args.model not in present:
        raise SystemExit("%s has no model %d (present: %s)"
                         % (os.path.basename(lev), args.model,
                            " ".join(str(m) for m in present)))
    blocks = exportd2.model_blocks(blob, table, args.model)
    missing = [v for v in ("clean", "damaged", "low") if v not in blocks]
    if missing:
        raise SystemExit("model %d lacks %s; a bank model needs all three"
                         % (args.model, ", ".join(missing)))

    variants = exportd2.decode_variants(blob, table, args.model)
    sets = _sets_for(variants)
    ti_body, ti_size, ti_bytes, ti = exportd2.d2_texture_info(blob)
    page_base, page_entries = exportd2.d2_page_entries(blob, ti)
    missing_sets = sorted(s for s in sets if s not in page_entries)
    if missing_sets:
        raise SystemExit("model %d names texture set(s) %s that the level does "
                         "not list; choose another model"
                         % (args.model, " ".join(str(s) for s in missing_sets)))

    source_lcf = exportd2.find_lcf(lev)
    if not source_lcf:
        raise SystemExit("no .LCF beside %s; cosmetics are required for a bank"
                         % lev)
    with open(source_lcf, "rb") as f:
        lcf_blob = f.read()
    cos_records = cosmetics1.parse_lcf(lcf_blob)
    if args.model >= len(cos_records):
        raise SystemExit("%s has no cosmetics record for model %d"
                         % (os.path.basename(source_lcf), args.model))

    # The bank folder is the workspace: generated blocks live in models/ and
    # pages/ beside it, not under a raw/ indirection (the author's own source/,
    # textures/ and sounds/ sit there too).
    raw = out
    raw_models = os.path.join(out, "models")
    raw_pages = os.path.join(out, "pages")
    raw_cos = os.path.join(out, "cosmetics")
    for d in (raw_models, raw_pages, raw_cos):
        os.makedirs(d, exist_ok=True)

    # --- raw model blocks -------------------------------------------------
    variant_paths = {}
    for variant in ("clean", "damaged", "low"):
        p = os.path.join(raw_models, "model%d_%s.bin" % (args.model, variant))
        with open(p, "wb") as f:
            f.write(blocks[variant])
        variant_paths[variant] = os.path.relpath(p, out).replace("\\", "/")

    # --- raw pages + viewable PNGs ---------------------------------------
    page_order = [(s, z) for s, z in ti["permlist"] + ti["speclist"]
                  if s in sets]
    pages_json = []
    for s, size in page_order:
        off, actual, _kind = page_entries[s]
        data = blob[off:off + actual]
        bin_path = os.path.join(raw_pages, "PAGE_%d.bin" % s)
        png_path = os.path.join(raw_pages, "PAGE_%d.png" % s)
        with open(bin_path, "wb") as f:
            f.write(data)
        rows, page = pages1.read_page(blob, off, actual)
        exportd2.page_png(png_path, rows, exportd2.page_indexes(page))
        pages_json.append({
            "set": s, "size": actual, "ncluts": len(rows),
            "bin": os.path.relpath(bin_path, out).replace("\\", "/"),
            "png": os.path.relpath(png_path, out).replace("\\", "/"),
        })

    # --- raw lumps --------------------------------------------------------
    raw_paths = {
        "textureinfo": "textureinfo.bin",
        "pallet": "pallet.bin",
        "texturenames": "texturenames.bin",
        "modelnames": "modelnames.bin",
    }
    with open(os.path.join(out, raw_paths["textureinfo"]), "wb") as f:
        f.write(ti_bytes)
    pal = exportd2.d2_palette(blob)
    if pal["size"]:
        with open(os.path.join(out, raw_paths["pallet"]), "wb") as f:
            f.write(blob[pal["body"]:pal["body"] + pal["size"]])
    names = exportd2.texture_names(blob)
    if names:
        with open(os.path.join(out, raw_paths["texturenames"]), "wb") as f:
            f.write(names)
    mnames = exportd2.model_names(blob)
    if mnames:
        with open(os.path.join(out, raw_paths["modelnames"]), "wb") as f:
            f.write(mnames)
    _write_palette_json(os.path.join(out, "palette.json"), pal, sets)
    exportd2.palette_png(os.path.join(out, "palette.png"), pal["records"], sets)

    # --- cosmetics --------------------------------------------------------
    source_lcf_rel = "source.LCF"
    shutil.copyfile(source_lcf, os.path.join(out, source_lcf_rel))
    donor_cos = os.path.join(raw_cos, "model%d.cos" % args.model)
    with open(donor_cos, "wb") as f:
        f.write(cos_records[args.model])
    with open(os.path.join(out, "cosmetics.json"), "w") as f:
        json.dump({
            "source": os.path.abspath(source_lcf),
            "donor_model": args.model,
            "destination_slot": args.slot,
            "records": [{
                "model": i,
                "raw": rec.hex(),
            } for i, rec in enumerate(cos_records)],
        }, f, indent=1)
        f.write("\n")

    # --- manifest shaped like exportd2's, so --rebuild-bank works ---------
    car_id = "%s_model%d" % (name, args.slot)
    car = {
        "id": car_id,
        "model": args.model,          # donor model, for the source LCF lookup
        "destination_slot": args.slot,
        "variants": {v: {"raw": p} for v, p in variant_paths.items()},
        "sets": sorted(sets),
    }
    manifest = {
        "version": exportd2.VERSION,
        "tool": "basebank",
        "game": exportd2.GAME,
        "city": name,
        "source_city": os.path.splitext(os.path.basename(lev))[0].upper(),
        "source_model": args.model,
        "destination_slot": args.slot,
        "city_index": args.city_index,
        "cell_size": exportd2.scalemodels.D2_CELL_SIZE,
        "source": {
            "path": os.path.abspath(lev),
            "size": len(blob),
            "sha256": exportd2._sha256(blob),
        },
        "models": [car],
        "texture_sets": sorted(sets),
        "wheel_sets": [],
        "pages": pages_json,
        "palette_sets": sorted({r["tpageindex"] for r in pal["records"]
                                if r["tpageindex"] in sets}),
        "raw": raw_paths,
        "lcf": source_lcf_rel,
        "cosmetics": "cosmetics.json",
        "bank_order": [{"slot": args.slot, "car": car_id}],
        "warnings": [],
    }
    manifest_path = os.path.join(out, name + ".json")
    _write_json(manifest_path, manifest)

    # build_bank writes NAME.LEV, NAME.LCF and bank.json into `out`.
    report = exportd2.build_bank(manifest, manifest_path, out, None)
    manifest["bank"] = report
    _write_json(manifest_path, manifest)

    # --- install snippet and README --------------------------------------
    game_data = os.path.abspath(args.game_data) if args.game_data else os.path.join(
        _repo_root(), "src_rebuild", "bin", "Release_dev", "DRIVER2")
    rel_lev = os.path.relpath(os.path.join(out, name + ".LEV"), game_data)
    rel_lcf = os.path.relpath(os.path.join(out, name + ".LCF"), game_data)
    rel_lev = rel_lev.replace("\\", "/")
    rel_lcf = rel_lcf.replace("\\", "/")
    ini = (
        "[cities]\n"
        "; Base bank %s, from %s model %d, in destination slot %d.\n"
        "; Paths are relative to the game's DRIVER2 folder.\n"
        "city_%d = %s, %s, %s\n"
        % (name, manifest["source_city"], args.model, args.slot,
           args.city_index, name, rel_lev, rel_lcf)
    )
    with open(os.path.join(out, "cities.ini"), "w") as f:
        f.write(ini)

    readme = (
        "%s - one-car Driver 2 base bank\n"
        "================================\n\n"
        "Source: %s model %d\n"
        "Destination model slot: %d\n"
        "Texture sets: %s\n\n"
        "This bank carries ONE car: clean, damaged and low are the donor's\n"
        "three blocks. Every other model slot is -1.\n\n"
        "Files:\n"
        "  %s.LEV        loadable car-data bank\n"
        "  %s.LCF        cosmetics (donor record at slot %d)\n"
        "  %s.json       manifest, accepted by exportd2 --rebuild-bank\n"
        "  raw/models/   donor car blocks (clean/damaged/low)\n"
        "  raw/pages/    page entries and PNG previews for sets %s\n"
        "  raw/pallet.bin, raw/palette.png/.json, raw/cosmetics/\n\n"
        "Install:\n"
        "  1. Put this folder where cities.ini can reach it.\n"
        "  2. Add the line from cities.ini under [cities] in\n"
        "     JERICHO/CONFIG/cities.ini.\n"
        "  3. Import it with import = <resident slot>:<city index>:<model>.\n"
        "     city_N has city index %d (11 + N).\n\n"
        "Rebuild after editing the raw blocks:\n"
        "  python exportd2.py --rebuild-bank %s.json -o .\n"
        % (name, manifest["source_city"], args.model, args.slot,
           " ".join(str(s) for s in sorted(sets)),
           name, name, args.slot, name,
           " ".join(str(s) for s in sorted(sets)),
           11 + args.city_index, name)
    )
    with open(os.path.join(out, "README.txt"), "w") as f:
        f.write(readme)

    print("%s: one-car bank from %s model %d -> slot %d"
          % (name, manifest["source_city"], args.model, args.slot))
    print("  sets: %s" % " ".join(str(s) for s in sorted(sets)))
    print("  files: %s" % out)
    print("  install: %s" % os.path.join(out, "cities.ini"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scaffold a one-car Driver 2 car-data bank.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lev", help="donor Driver 2 .LEV (overrides --city)")
    ap.add_argument("--city", default="CHICAGO",
                    help="donor Driver 2 city (default CHICAGO)")
    ap.add_argument("--model", type=int, default=1,
                    help="donor car model (default 1)")
    ap.add_argument("--slot", type=int, default=1,
                    help="destination model slot (default 1; 0 is 'no car')")
    ap.add_argument("--name", default="BASE",
                    help="bank name / file stem (default BASE)")
    ap.add_argument("--out", help="output folder (default tools/car/out/basebank/<NAME>)")
    ap.add_argument("--city-index", type=int, default=0,
                    help="cities.ini city_N index (default 0 -> engine city 11)")
    ap.add_argument("--game-data", help="DRIVER2 folder for resolving --city")
    ap.add_argument("--force", action="store_true",
                    help="write into a non-empty output folder")
    args = ap.parse_args(argv)
    if not 0 <= args.city_index <= 15:
        raise SystemExit("--city-index must be 0..15")
    return build_base(args)


if __name__ == "__main__":
    sys.exit(main())
