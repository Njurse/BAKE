#!/usr/bin/env python3
"""Console wizard for the JMPPED pedestrian authoring project.

Everything routes through jmpped.py so the verbs stay the same as the CLI.
"""

from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import jmpped_workspace as jws


def _run(args: list[str]) -> int:
    cmd = [sys.executable, os.path.join(HERE, "jmpped.py")] + args
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=HERE).returncode


def _run_script(script: str, args: list[str]) -> int:
    cmd = [sys.executable, os.path.join(HERE, script)] + args
    print("+ " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=HERE).returncode


def _status() -> None:
    project = jws.project_root()
    source = jws.source_root()
    st = jws.status(project, source)
    print("\nProject     : %s" % project)
    print("Source      : %s%s" % (st["source"],
                                  "" if st["source_exists"] else " (missing)"))
    print("Manifest    : %s" % ("yes" if st["manifest"] else "no"))
    print("Characters  : %s" % (", ".join(st["characters"])
                                if st["characters"] else "none"))
    if st["base"]:
        print("Base archive: %s (%s)" % (st["base"],
                                         "ok" if st["base_ok"]
                                         else "MISSING/CHANGED"))
    if st["migrate_required"]:
        print("Migrate     : required (run JMPPED.bat migrate)")
    print()


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def main() -> int:
    if "--status" in sys.argv:
        _status()
        return 0

    while True:
        _status()
        print("1) Bake and preview the project")
        print("2) Import a character folder")
        print("3) Add a character")
        print("4) Preview")
        print("5) Split per-material texture crops (crop images only)")
        print("6) Extract a Driver 2 level (source export; not a character export)")
        print("7) Sync source-owned assets")
        print("8) Change project/source path")
        print("9) Show status")
        print("10) Rebuild the textured model (model_textured.obj/.mtl)")
        print("0) Quit")
        choice = _ask("Choice: ")

        if choice == "0":
            return 0
        if choice == "1":
            _run(["bake", "--copy", "--preview"])
        elif choice == "2":
            folder = _ask("Character folder: ").strip('"')
            if folder:
                _run(["import", "--folder", folder])
        elif choice == "3":
            name = _ask("New character display name: ")
            if name:
                _run(["add-character", "--name", name])
        elif choice == "4":
            _run(["preview"])
        elif choice == "5":
            name = _ask("Character name (blank = all): ")
            args = ["--character", name] if name else []
            _run_script("jmpped_textures.py", args)
        elif choice == "6":
            level = _ask("Driver 2 level (blank = default): ").strip('"')
            args = ["extract", "--reset"]
            if level:
                args += ["--source", level]
            _run(args)
        elif choice == "7":
            _run(["sync-source"])
        elif choice == "8":
            which = _ask("Change [p]roject or [s]ource path? ").lower()
            path = _ask("Path: ").strip('"')
            if path and which == "p":
                jws.set_project(path)
            elif path and which == "s":
                jws.set_source(path)
            else:
                print("No change.")
        elif choice == "9":
            _status()
        elif choice == "10":
            name = _ask("Character name (blank = last/custom): ")
            combine = _ask("Combine identical textures into shared crops? [y/N]: ")
            args = ["textured", "--character", name] if name else ["textured"]
            args.append("--combine" if combine.strip().lower() in ("y", "yes")
                        else "--no-combine")
            _run(args)
        else:
            print("Unknown choice.")


if __name__ == "__main__":
    raise SystemExit(main())
