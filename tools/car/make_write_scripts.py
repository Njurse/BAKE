"""make_write_scripts.py <bank.json>  (writes rebake.bat, playtest.bat, preview.bat, wizard.bat) [--force] [--mode M] [--args "..."] [--preview "..."]

Writes rebake.bat next to bank.json from template_rebake.bat (the one
canonical rebake). exportd2 calls write() on export; run this to refresh an
existing bank's copy after the template changes.

--mode, --args and --preview default to "not passed". On --force, a value
that was not passed is read back from the existing rebake.bat so a refresh
does not reset that bank's MODE, carbank_bake flags or preview flags.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "template_rebake.bat")
SIDECARS = {"rebake.bat": TEMPLATE,
            "playtest.bat": os.path.join(HERE, "template_playtest.bat"),
            "preview.bat": os.path.join(HERE, "template_preview.bat"),
            "wizard.bat": os.path.join(HERE, "template_wizard.bat"),
            "resolve_paths.bat": os.path.join(HERE, "template_resolve_paths.bat")}
CITY_KEYS = {"CHICAGO": "0", "HAVANA": "1", "VEGAS": "2", "RIO": "3"}

# What a brand-new bank gets. An existing rebake.bat wins over these when the
# caller did not pass the matching flag.
DEFAULT_MODE = "plain"
DEFAULT_ARGS = ""
DEFAULT_PREVIEW = "--flip-y"

_MODE_RE = re.compile(r'if "%MODE%"=="" set "MODE=([^"]*)"')
_ARGS_RE = re.compile(r'-o "%OUT%"\s*(.*?)\s*%FLIP%')
_PREVIEW_RE = re.compile(r'--stop-file "%STOPF%"\s*(.*?)\s*$', re.M)


def _win(path):
    """Backslashes. The generated files are Windows batch, whatever host writes them."""
    return path.replace("/", "\\")


def _from_bank(path, bank):
    """%BANK%-relative when on the same drive, else absolute - never both."""
    try:
        rel = os.path.relpath(path, bank)
    except ValueError:
        return os.path.abspath(path)
    return "%BANK%\\" + _win(rel)


def _under(path, parent):
    path = os.path.normcase(os.path.abspath(path))
    parent = os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def _game_root(start):
    """The REDRIVER2 checkout that holds src_rebuild\\bin\\Release_dev.

    A bank that already lives in that Release_dev can also see a stray
    Release_dev\\src_rebuild left by an earlier doubled mirror path. That
    directory is not the checkout: prefer the root whose Release_dev actually
    contains the bank.
    """
    bank = os.path.abspath(start)
    root = bank
    fallback = None
    while os.path.dirname(root) != root:
        release = os.path.join(root, "src_rebuild", "bin", "Release_dev")
        if os.path.isdir(release):
            if _under(bank, release):
                return root
            if fallback is None:
                fallback = root
        root = os.path.dirname(root)
    return fallback


def _release_dev(root):
    return os.path.join(root, "src_rebuild", "bin", "Release_dev")


def mirror_and_exedir(bank):
    """(MIRROR, EXEDIR) for a bank folder.

    A bank already under <root>\\src_rebuild\\bin\\Release_dev installs in
    place: MIRROR is the bank, EXEDIR is that Release_dev. Anywhere else,
    EXEDIR is the game's Release_dev and MIRROR is the bank's place under it.
    """
    bank = os.path.abspath(bank)
    root = _game_root(bank) or _game_root(HERE)
    if not root:
        return "", ""
    release = _release_dev(root)
    exedir = _from_bank(release, bank)
    if _under(bank, release):
        return "%BANK%", exedir
    try:
        rel = os.path.relpath(bank, root)
    except ValueError:
        rel = ".."
    if rel.startswith(".."):
        rel = os.path.join("JERICHO", "MODS", os.path.basename(bank))
    return exedir + "\\" + _win(rel), exedir


def _bank_name(bank):
    """Bank name from bank.json (lev, then city) - never the donor city."""
    try:
        with open(os.path.join(bank, "bank.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None
    return os.path.splitext(os.path.basename(raw.get("lev") or ""))[0] \
        or raw.get("city")


def _existing_settings(bank_dir):
    """MODE, carbank_bake args and preview args from an existing rebake.bat.

    Returns None when the file is missing. A field that is not in the file
    comes back as None so the caller can fall through to the default.
    """
    path = os.path.join(bank_dir, "rebake.bat")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    mode = _MODE_RE.search(text)
    args = _ARGS_RE.search(text)
    preview = _PREVIEW_RE.search(text)
    return (mode.group(1) if mode else None,
            args.group(1).strip() if args else None,
            preview.group(1).strip() if preview else None)


def write(bank_dir, name, donor_city="CHICAGO", mode=None, args=None,
          preview=None, force=False):
    bank = os.path.abspath(bank_dir)
    name = _bank_name(bank) or name
    tools = _from_bank(HERE, bank)          # full value for set "CAR=..."
    mirror, exedir = mirror_and_exedir(bank)
    # A refresh must not clobber per-bank knobs the template does not know.
    # An explicit argument wins; everything else is read back from the
    # rebake.bat already in the folder, then the new-bank default.
    if mode is None or args is None or preview is None:
        old = _existing_settings(bank)
        if old:
            if mode is None:
                mode = old[0]
            if args is None:
                args = old[1]
            if preview is None:
                preview = old[2]
    if mode is None:
        mode = DEFAULT_MODE
    if args is None:
        args = DEFAULT_ARGS
    if preview is None:
        preview = DEFAULT_PREVIEW
    subs = {"@NAME@": name.upper(), "@TOOLS@": tools, "@MIRROR@": mirror,
            "@EXEDIR@": exedir, "@MODE@": mode, "@ARGS@": args,
            "@PREVIEW@": preview,
            "@DEFCITY@": CITY_KEYS.get(str(donor_city).upper(), "0")}
    written = None
    for out, tpl in SIDECARS.items():   # rebake / playtest / preview / wizard
        path = os.path.join(bank_dir, out)
        if os.path.exists(path) and not force:
            continue
        with open(tpl, encoding="ascii") as fh:
            text = fh.read()
        for k, v in subs.items():
            text = text.replace(k, v)
        with open(path, "w", newline="\r\n") as f:
            f.write(text.replace("\r\n", "\n"))
        if out == "rebake.bat":
            written = path
    return written


def _coalesce(argv):
    """Join a flag's value when that value itself starts with dashes.

    ``--args --scale 1`` and ``--preview --flip-y`` are one value each.
    ``--scale`` is the bake flag that takes a following number; other dashed
    tokens stand alone, so a bank path after them is not swallowed.
    ``--args="--scale 1"`` is already one token and stays that way.
    """
    owned = {"--force", "--mode", "--args", "--preview", "-h", "--help"}
    takes = {"--mode", "--args", "--preview"}
    needs_value = {"--scale"}
    out = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in takes:
            i += 1
            parts = []
            if i < len(argv) and not argv[i].startswith("-") and argv[i].split("=", 1)[0] not in owned:
                parts.append(argv[i])
                i += 1
            else:
                while i < len(argv) and argv[i].startswith("-") and argv[i].split("=", 1)[0] not in owned:
                    parts.append(argv[i])
                    i += 1
                    name = parts[-1].split("=", 1)[0]
                    if (name in needs_value and "=" not in parts[-1]
                            and i < len(argv)
                            and not argv[i].startswith("-")
                            and argv[i].split("=", 1)[0] not in owned):
                        parts.append(argv[i])
                        i += 1
            out.append(tok + "=" + " ".join(parts))
            continue
        out.append(tok)
        i += 1
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bank")
    ap.add_argument("--force", action="store_true")
    # None means "not passed", so --force can keep the bank's current value.
    ap.add_argument("--mode", default=None)
    ap.add_argument("--args", default=None)
    ap.add_argument("--preview", default=None)
    if argv is None:
        argv = sys.argv[1:]
    a = ap.parse_args(_coalesce(argv))
    with open(a.bank, encoding="utf-8") as fh:
        raw = json.load(fh)
    name = os.path.splitext(os.path.basename(raw.get("lev") or ""))[0] \
        or raw.get("city")
    donor = ((raw.get("declare") or {}).get("donor") or {}).get("city", "CHICAGO")
    p = write(os.path.dirname(os.path.abspath(a.bank)), name, donor, a.mode,
              a.args, a.preview, a.force)
    print(p or "rebake.bat exists (use --force); missing sidecars written")


if __name__ == "__main__":
    sys.exit(main())
