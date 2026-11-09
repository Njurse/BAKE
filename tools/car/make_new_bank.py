#!/usr/bin/env python3
"""make_new_bank.py - scaffold a one-car Driver 2 car-data bank.

Public command: d2-export.py with --model / --slot / --lev (same arguments).

The output is the SAME bank shape the full exporter writes (and any exported bank
uses), so a bank started here can be edited, re-baked and installed exactly
like one produced by exportd2:

    bank.json                the declaration: slot order, cars, layout
    NAME.LEV / NAME.LCF      installed copies of build/NAME.LEV / .LCF
    cars/<city>_model<slot>/ editable OBJ/MTL, page PNGs and sounds
    build/                   what the bake generates (never edited): NAME.LEV/.LCF,
                             NAME.report.json, NAME.json, pages/, models/,
                             pallet/textureinfo/texturenames/modelnames.bin
    cities.ini, README.txt   install hints
    rebake.bat/playtest.bat/preview.bat/wizard.bat

The donor car's clean/damaged/low blocks are decoded to OBJ, so `carbank_bake`
can rebuild the bank after an edit. This is deliberately the same path as
`exportd2.py --models N`, just for one car and with a destination slot.

Usage:
    python make_new_bank.py --city CHICAGO --model 1 --name HALLCHCG \
        --out JERICHO/MODS/hallgarthcars/banks/hallchcg
    python make_new_bank.py --lev LEVELS/CHICAGO.LEV --model 1 --name BASE
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import exportd2          # noqa: E402


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
    if not name or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
                       for c in name):
        raise SystemExit("bank name %r must be letters, digits and underscore"
                         % name)
    return name


def build_base(args):
    name = _sanitize_name(args.name) if args.name else "BASE"

    if args.slot == 0:
        raise SystemExit(
            "model slot 0 is carhacks' 'no car' slot for a one-car overlay; "
            "use 1..12. A whole-city export (exportd2.py) keeps slot 0 when "
            "the level has a cop — that is the demohavana path")
    if not 0 <= args.slot < exportd2.MAX_CAR_MODELS:
        raise SystemExit("slot %d is outside 0..%d"
                         % (args.slot, exportd2.MAX_CAR_MODELS - 1))

    if args.out:
        out = os.path.abspath(args.out)
    else:
        out = os.path.join(HERE, "out", "basebank", name)

    if os.path.isdir(out) and os.listdir(out) and not args.force:
        raise SystemExit("%s is not empty; pass --force to overwrite it" % out)

    lev = os.path.abspath(args.lev) if args.lev else resolve_level(
        args.city, args.game_data)

    with open(lev, "rb") as f:
        blob = f.read()

    if exportd2.level_game(blob) != "d2":
        raise SystemExit("%s is not a Driver 2 level; make_new_bank/exportd2 "
                         "only handle D2 banks" % lev)

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

    out_root = os.path.dirname(out)
    dir_name = os.path.basename(out)

    if not dir_name:
        raise SystemExit("--out must name the bank folder, not a drive root")

    os.makedirs(out_root, exist_ok=True)

    # One car, all three variants, at the requested destination slot. The
    # export creates bank.json (declare), cars/ and build/, then
    # bakes build/NAME.LEV + NAME.LCF and the bank's scripts.
    entry = exportd2.export_one(
        lev, out_root, {args.model}, ("clean", "damaged", "low"), True,
        dir_name=dir_name, bank_name=name, slot_override=args.slot)

    # The country's normal install points cities.ini at the LEV/LCF beside
    # bank.json; export writes the pair into build/, so keep the root copy in
    # step as well (the same copy rebake.bat refreshes).
    for ext in (".LEV", ".LCF"):
        src = os.path.join(out, "build", name + ext)
        if os.path.isfile(src):
            shutil.copyfile(src, os.path.join(out, name + ext))

    # --- install snippet and README --------------------------------------
    # cities.ini paths are relative to the INSTALLED game's DRIVER2 folder, so
    # they must name where the bank lives in an install - JERICHO/MODS/<mod>/
    # banks/<dir>/ - not where this tree happens to sit next to a Release_dev
    # build (a relpath from here broke every hand install).
    rel_dir = _install_rel_dir(out, args.mod_id, name)
    rel_lev = rel_dir + "\\" + name + ".LEV"
    rel_lcf = rel_dir + "\\" + name + ".LCF"
    donor_city = entry.get("city") or os.path.splitext(
        os.path.basename(lev))[0].upper()

    ini = (
        "[cities]\n"
        "; Base bank %s, from %s model %d, in destination slot %d.\n"
        "; Paths are relative to the game's DRIVER2 folder.\n"
        "city_%d = %s, %s, %s\n"
        % (name, donor_city, args.model, args.slot,
           args.city_index, name, rel_lev, rel_lcf)
    )
    with open(os.path.join(out, "cities.ini"), "w") as f:
        f.write(ini)

    readme = (
        "%s - one-car Driver 2 car-data bank\n"
        "================================\n\n"
        "Source: %s model %d\n"
        "Destination model slot: %d\n\n"
        "This bank carries ONE car: clean, damaged and low. Every other model\n"
        "slot is -1.\n\n"
        "Files:\n"
        "  bank.json          the declaration (slot order, cars)\n"
        "  %s.LEV / .LCF      installed copies of build/ (loadable pair)\n"
        "  cars/              the car's editable OBJ/MTL, page PNGs and sounds\n"
        "  build/             the bake output, never edited (safe to delete)\n\n"
        "Install:\n"
        "  Shipped as a mod (JERICHO/MODS/<id>/banks/<dir>/), carhacks 0.2.7+\n"
        "  registers it on its own when the mod is enabled. By hand:\n"
        "  1. Put this folder where cities.ini can reach it.\n"
        "  2. Add the line from cities.ini under [cities] in\n"
        "     JERICHO/CONFIG/cities.ini.\n"
        "  3. Import it with import = <resident slot>:<city index>:<model>.\n"
        "     city_N has city index %d (11 + N).\n\n"
        "Stage scripts (written beside this file):\n"
        "  wizard.bat    hub for every stage below\n"
        "  rebake.bat    bake + install + register, then preview / ride\n"
        "  preview.bat   pick a slot, endless preview until you quit\n"
        "  playtest.bat  Take a Ride with a car, no bake\n\n"
        "Rebuild after editing the car's OBJ or bank.json:\n"
        "  rebake.bat\n"
        % (name, donor_city, args.model, args.slot, name,
           11 + args.city_index)
    )
    with open(os.path.join(out, "README.txt"), "w") as f:
        f.write(readme)

    print("%s: one-car bank from %s model %d -> slot %d"
          % (name, donor_city, args.model, args.slot))
    print("  %s" % os.path.join(out, "bank.json"))
    print("  rebake with: %s" % os.path.join(out, "rebake.bat"))
    return 0


def _install_rel_dir(out, mod_id, name):
    """The bank folder as a DRIVER2-relative install path,
    ..\\JERICHO\\MODS\\<mod>\\banks\\<dir>.  When `out` already sits inside a
    JERICHO/MODS tree that tail is used as-is; otherwise the bank is assumed to
    ship as MODS/<mod-id or name lowercased>/banks/<out folder name>."""
    parts = os.path.abspath(out).replace("\\", "/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        if parts[i].upper() == "MODS" and parts[i - 1].upper() == "JERICHO" \
                and i + 1 < len(parts):
            return "..\\JERICHO\\MODS\\" + "\\".join(parts[i + 1:])
    mod = mod_id or name.lower()
    return "..\\JERICHO\\MODS\\%s\\banks\\%s" % (mod, parts[-1])


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
    ap.add_argument("--mod-id", help="mod id the bank ships in, for the cities.ini "
                    "paths (..\\JERICHO\\MODS\\<mod-id>\\banks\\<dir>; default: "
                    "taken from --out when it is inside JERICHO/MODS, else NAME lowercased)")
    ap.add_argument("--force", action="store_true",
                    help="write into a non-empty output folder")
    args = ap.parse_args(argv)

    if not 0 <= args.city_index <= 15:
        raise SystemExit("--city-index must be 0..15")

    return build_base(args)


if __name__ == "__main__":
    sys.exit(main())
