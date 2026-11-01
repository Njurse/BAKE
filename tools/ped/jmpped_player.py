#!/usr/bin/env python3
"""jmpped_player.py - build JMPPED.LEV on the player's machine from THEIR game.

JERICHO does not ship Driver 2's Jericho model. The auto re-bake
(BAKE_D1_CARS.bat/.sh, started by the game's re-bake prompt) runs this to
extract the stock Jericho parts and Tanner's limbs from the player's own
DRIVER2/LEVELS/HAVANA.LEV and bake the stock JMPPED.LEV beside it.

    python jmpped_player.py --levels DRIVER2/LEVELS

Only the stock character is built (no custom/Driv3r characters). The work
happens in a throwaway project folder, so the authoring tree's project.json,
characters/ and last-used state are never touched. Exit 0 on success, 2 when
the player's game data is missing, 1 on any other failure.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_archive as ja
import jmpped_workspace as jws


def build(levels: str, dest: str | None = None, keep: bool = False) -> int:
    levels = os.path.abspath(levels)
    source = os.path.join(levels, "HAVANA.LEV")
    if not os.path.isfile(source):
        print("JMPPED: FAILED -- %s is missing." % source)
        print("  Jericho is extracted from your own Driver 2 data; put the game's")
        print("  DRIVER2 folder beside JERICHO so DRIVER2/LEVELS/HAVANA.LEV exists.")
        return 2
    dest = os.path.abspath(dest or os.path.join(levels, "JMPPED.LEV"))

    # Never record the throwaway project as the author's last-used workspace.
    jws.set_project = lambda path: None
    jws.set_source = lambda path: None

    import jmpped_extract
    import jmpped_bake

    work = tempfile.mkdtemp(prefix="jmpped_player_")
    try:
        rc = jmpped_extract.extract(source, work, reset=True, level="havana")
        if rc:
            return rc
        out = os.path.join(work, "out", "build", "JMPPED.LEV")
        rc = jmpped_bake.bake(work, dest=out, copy=False)
        if rc:
            return rc
        arch = ja.read_archive(out)
        if arch.version != jc.VERSION_V5 or len(arch.models) != 14:
            print("JMPPED: FAILED -- unexpected bake (v%d, %d models)"
                  % (arch.version, len(arch.models)))
            return 1
        tmp = dest + ".tmp"
        shutil.copyfile(out, tmp)
        os.replace(tmp, dest)
        print("JMPPED: wrote %s (stock Tanner + Jericho from your HAVANA.LEV)"
              % dest)
        return 0
    finally:
        if keep:
            print("JMPPED: kept work folder %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--levels", default=os.path.join("DRIVER2", "LEVELS"),
                    help="the player's Driver 2 LEVELS folder")
    ap.add_argument("--dest", default=None,
                    help="output archive (default: <levels>/JMPPED.LEV)")
    ap.add_argument("--keep", action="store_true",
                    help="keep the temporary project for inspection")
    args = ap.parse_args(argv)
    try:
        return build(args.levels, args.dest, args.keep)
    except jc.JMPPEDError as e:
        print("JMPPED: FAILED -- %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())