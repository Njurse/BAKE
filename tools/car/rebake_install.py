"""rebake_install.py <bank.json>  (writes rebake.bat + playtest.bat) [--force] [--mode M] [--args "..."] [--preview "..."]

Writes rebake.bat next to bank.json from rebake_template.bat (the one
canonical rebake). exportd2 calls write() on export; run this to refresh an
existing bank's copy after the template changes.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "rebake_template.bat")
SIDECARS = {"rebake.bat": TEMPLATE,
            "playtest.bat": os.path.join(HERE, "playtest_template.bat")}
CITY_KEYS = {"CHICAGO": "0", "HAVANA": "1", "VEGAS": "2", "RIO": "3"}


def write(bank_dir, name, donor_city="CHICAGO", mode="plain", args="",
          preview="--flip-y", force=False):
    bank = os.path.abspath(bank_dir)
    try:
        tools = os.path.relpath(HERE, bank)
    except ValueError:
        tools = HERE
    root, mirror, exedir = bank, "", ""
    while os.path.dirname(root) != root:
        if os.path.isdir(os.path.join(root, "src_rebuild")):
            # relative to %BANK%, so the bat stays valid in any checkout
            up = os.path.relpath(root, bank)
            exedir = "%BANK%\\" + os.path.join(up, "src_rebuild", "bin",
                                                "Release_dev")
            mirror = exedir + "\\" + os.path.relpath(bank, root)
            break
        root = os.path.dirname(root)
    subs = {"@NAME@": name.upper(), "@TOOLS@": tools, "@MIRROR@": mirror,
                 "@EXEDIR@": exedir, "@MODE@": mode, "@ARGS@": args,
                 "@PREVIEW@": preview,
                 "@DEFCITY@": CITY_KEYS.get(str(donor_city).upper(), "0")}
    written = None
    for out, tpl in SIDECARS.items():   # rebake.bat + playtest.bat
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
