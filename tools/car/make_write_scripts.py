"""make_write_scripts.py <bank.json>  (writes rebake.bat, playtest.bat, preview.bat, wizard.bat) [--force] [--mode M] [--args "..."] [--preview "..."]

Writes rebake.bat next to bank.json from template_rebake.bat (the one
canonical rebake). exportd2 calls write() on export; run this to refresh an
existing bank's copy after the template changes.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template_rebake.bat")
SIDECARS = {"rebake.bat": TEMPLATE,
            "playtest.bat": os.path.join(HERE, "template_playtest.bat"),
            "preview.bat": os.path.join(HERE, "template_preview.bat"),
            "wizard.bat": os.path.join(HERE, "template_wizard.bat")}
CITY_KEYS = {"CHICAGO": "0", "HAVANA": "1", "VEGAS": "2", "RIO": "3"}


def _from_bank(path, bank):
    """%BANK%-relative when on the same drive, else absolute - never both."""
    try:
        return "%BANK%\\" + os.path.relpath(path, bank)
    except ValueError:
        return os.path.abspath(path)


def _game_root(start):
    root = os.path.abspath(start)
    while os.path.dirname(root) != root:
        if os.path.isdir(os.path.join(root, "src_rebuild")):
            return root
        root = os.path.dirname(root)
    return None


def _bank_name(bank):
    """Bank name from bank.json (lev, then city) - never the donor city."""
    try:
        raw = json.load(open(os.path.join(bank, "bank.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return os.path.splitext(os.path.basename(raw.get("lev") or ""))[0] \
        or raw.get("city")


def write(bank_dir, name, donor_city="CHICAGO", mode="plain", args="",
          preview="--flip-y", force=False):
    bank = os.path.abspath(bank_dir)
    name = _bank_name(bank) or name
    tools = _from_bank(HERE, bank)          # full value for set "CAR=..."
    mirror, exedir = "", ""
    # game tree = the REDRIVER2 checkout holding src_rebuild; look above the
    # bank first, then above the bake tools (a bank may live outside the tree)
    root = _game_root(bank) or _game_root(HERE)
    if root:
        exedir = _from_bank(os.path.join(root, "src_rebuild", "bin",
                                         "Release_dev"), bank)
        try:
            rel = os.path.relpath(bank, root)
        except ValueError:
            rel = ".."
        if rel.startswith(".."):            # bank outside the tree
            rel = os.path.join("JERICHO", "MODS", os.path.basename(bank))
        mirror = exedir + "\\" + rel
    subs = {"@NAME@": name.upper(), "@TOOLS@": tools, "@MIRROR@": mirror,
                 "@EXEDIR@": exedir, "@MODE@": mode, "@ARGS@": args,
                 "@PREVIEW@": preview,
                 "@DEFCITY@": CITY_KEYS.get(str(donor_city).upper(), "0")}
    written = None
    for out, tpl in SIDECARS.items():   # rebake / playtest / preview / wizard
        path = os.path.join(bank_dir, out)
        if os.path.exists(path) and not force:
            continue
        text = open(tpl, encoding="ascii").read()
        for k, v in subs.items():
            text = text.replace(k, v)
        with open(path, "w", newline="\r\n") as f:
            f.write(text.replace("\r\n", "\n"))
        if out == "rebake.bat":
            written = path
    return written


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bank")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--mode", default="plain")
    ap.add_argument("--args", default="")
    ap.add_argument("--preview", default="--flip-y")
    a = ap.parse_args()
    raw = json.load(open(a.bank, encoding="utf-8"))
    name = os.path.splitext(os.path.basename(raw.get("lev") or ""))[0] \
        or raw.get("city")
    donor = ((raw.get("declare") or {}).get("donor") or {}).get("city", "CHICAGO")
    p = write(os.path.dirname(os.path.abspath(a.bank)), name, donor, a.mode,
              a.args, a.preview, a.force)
    print(p or "rebake.bat exists (use --force); missing sidecars written")


if __name__ == "__main__":
    sys.exit(main())
