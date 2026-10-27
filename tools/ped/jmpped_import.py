#!/usr/bin/env python3
"""jmpped_import.py - import a character folder into the authoring project.

A character folder is the unit of import: it contains `character.json`,
`model.obj`, `models/{blender,engine}/<part>.obj` and
`textures/{pages,palettes,crops}/`. The folder is copied under
`characters/<slug>/` if it came from outside the project. The character is
added to `project.json`, its page sets are checked for collisions, and the
project is baked unless --no-build is passed.

The folder's `model.obj` is the combined edit surface. The import splits it
into the 14 part caches, updates their hashes, and then bakes the character.
Pass --scene to use another OBJ and --raw when that OBJ is already in engine
units.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_entries as je


def _load_character(folder: str) -> dict:
    path = os.path.join(folder, "character.json")
    if not os.path.isfile(path):
        raise jc.JMPPEDError(
            "%s has no character.json; this folder predates the rename or is "
            "not a character folder" % folder)
    return jc.load_json(path)


def _exists(project: str, rel: str) -> bool:
    return bool(rel) and os.path.isfile(os.path.join(
        project, rel.replace("/", os.sep)))


def _check_files(project: str, c: dict) -> None:
    for key in ("model", "engine_reference"):
        rec = c.get(key)
        if rec and rec.get("path") and not _exists(project, rec["path"]):
            raise jc.JMPPEDError("%s/%s is missing" % (c["name"], rec["path"]))
    for page in c.get("pages", []):
        for key in ("png", "indexed_png", "palette_png"):
            if page.get(key) and not _exists(project, page[key]):
                raise jc.JMPPEDError("%s/page %s/%s is missing"
                                     % (c["name"], page.get("page_set"), key))
        for crop in page.get("texture_pages", []):
            if crop.get("png") and not _exists(project, crop["png"]):
                raise jc.JMPPEDError("%s/page crop %s is missing"
                                     % (c["name"], crop["png"]))
    for part in c.get("parts", []):
        for key in ("obj_raw", "obj_edit"):
            if not _exists(project, part.get(key)):
                raise jc.JMPPEDError("%s/%s is missing"
                                     % (c["name"], part.get(key)))
        for crop in part.get("crops", []):
            if not _exists(project, crop.get("rgb")):
                raise jc.JMPPEDError("%s/crop %s is missing"
                                     % (c["name"], crop.get("rgb")))
    for crop in c.get("texture_crops", []):
        if crop.get("palette_png") and not _exists(project,
                                                   crop["palette_png"]):
            raise jc.JMPPEDError("%s/palette %s is missing"
                                 % (c["name"], crop["palette_png"]))


def import_character(folder: str, project: str, name: str | None = None,
                     build: bool = True, copy: bool = False,
                     use: str = "auto", character: str | None = None,
                     scene: str | None = None, raw: bool = False) -> int:
    project = os.path.abspath(project)
    folder = os.path.abspath(folder)
    manifest = je.load_manifest(project)
    if not os.path.isdir(folder):
        raise jc.JMPPEDError("not a folder: %s" % folder)

    data = _load_character(folder)
    name = name or data.get("name") or os.path.basename(folder.rstrip("\\/"))
    old_folder = (data.get("folder") or "").replace("\\", "/")
    slug = (os.path.basename(old_folder.rstrip("/")) if old_folder
            else je.slugify(name))
    dest_rel = "characters/" + slug
    dest = os.path.join(project, dest_rel.replace("/", os.sep))

    if folder != dest:
        if os.path.exists(dest):
            raise jc.JMPPEDError(
                "%s already exists; move it away or import it in place" % dest)
        shutil.copytree(folder, dest)
        data = _load_character(dest)

    prefix_relative = not old_folder or old_folder in (".", "./")
    je.rewrite_paths(data, old_folder, dest_rel, old_abs=folder,
                     prefix_relative=prefix_relative)
    data["folder"] = dest_rel
    data["name"] = name

    existing = next((i for i, c in enumerate(manifest["characters"])
                     if c.get("name") == name or
                     c.get("folder") == dest_rel), None)
    index = existing if existing is not None else len(manifest["characters"])
    sets = [int(s) for s in data.get("texture_page_sets", [])]
    if len(sets) != 3:
        raise jc.JMPPEDError("%s has %d texture page sets; expected 3"
                             % (name, len(sets)))
    owned = {int(s) for j, c in enumerate(manifest["characters"])
             if j != existing for s in c.get("texture_page_sets", [])}
    if any(s in owned for s in sets):
        target = list(jc.entry_page_sets(index))
        je.map_page_sets(data, {sets[i]: target[i] for i in range(3)})

    data["index"] = index
    je.validate_character(manifest, data)
    if existing is None:
        manifest["characters"].append(data)
    else:
        manifest["characters"][existing] = data
    jc.save_json(os.path.join(project, "project.json"), manifest)
    jc.save_json(os.path.join(dest, "character.json"), data)

    import jmpped_model
    jmpped_model.split_character(project, data, scene=scene, raw=raw,
                                 force=True)
    data = _load_character(dest)
    _check_files(project, data)
    print('imported "%s" as %s (texture pages %s)'
          % (name, dest_rel, data["texture_page_sets"]))

    if build:
        import jmpped_bake
        return jmpped_bake.bake(project, use="raw" if raw else use,
                                copy=copy)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Import a prepared character folder into a JMPPED project")
    ap.add_argument("--folder", required=True)
    ap.add_argument("--project", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--no-copy", action="store_true")
    ap.add_argument("--use", choices=("auto", "edit", "raw"), default="auto")
    ap.add_argument("--scene", default=None,
                    help="combined model OBJ (default: character's model.obj)")
    ap.add_argument("--raw", action="store_true",
                    help="the combined model is in engine units")
    args = ap.parse_args(argv)
    import jmpped_workspace as jws
    project = args.project or jws.project_root()
    try:
        return import_character(args.folder, project, args.name,
                                not args.no_build, not args.no_copy,
                                args.use, scene=args.scene, raw=args.raw)
    except jc.JMPPEDError as e:
        print("jmpped import: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
