#!/usr/bin/env python3
"""jmpped_add_character.py - clone a character and allocate its texture pages.

The new character starts as a copy of an existing one: 14 parts, three texture
pages/palette strips, its model and its crops. Materials are remapped to the new
character's own disjoint page sets through `texture_page_map`; the copied OBJ
text is left alone. Replace the copied meshes/textures, then bake.
"""

from __future__ import annotations

import argparse
import copy
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_entries as je


def add_character(project: str, name: str, from_character: str = "Jericho",
                  slug: str | None = None) -> int:
    project = os.path.abspath(project)
    manifest = je.load_manifest(project)
    source = je.character(manifest, from_character)
    index = len(manifest["characters"])
    target_sets = list(jc.entry_page_sets(index))
    slug = slug or je.slugify(name)
    dest_rel = "characters/" + slug
    dest = os.path.join(project, dest_rel.replace("/", os.sep))
    if os.path.exists(dest):
        raise jc.JMPPEDError("%s already exists; choose another slug" % dest)
    src_rel = source["folder"]
    src = os.path.join(project, src_rel.replace("/", os.sep))
    if not os.path.isdir(src):
        raise jc.JMPPEDError("%s is missing" % src)

    shutil.copytree(src, dest)
    data = copy.deepcopy(source)
    mapping = je.material_map(source, target_sets)
    je.map_page_sets(data, mapping)
    je.rewrite_paths(data, src_rel, dest_rel)
    data["index"] = index
    data["name"] = name
    data["folder"] = dest_rel
    je.validate_character(manifest, data)
    manifest["characters"].append(data)
    jc.save_json(os.path.join(project, "project.json"), manifest)
    jc.save_json(os.path.join(dest, "character.json"), data)
    print('added character %d "%s" -> %s (texture pages %s)'
          % (index, name, dest_rel, target_sets))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Clone a character and allocate texture pages")
    ap.add_argument("--name", required=True)
    ap.add_argument("--from-character", default="Jericho")
    ap.add_argument("--slug", default=None)
    ap.add_argument("--project", default=None)
    args = ap.parse_args(argv)
    import jmpped_workspace as jws
    project = args.project or jws.project_root()
    try:
        return add_character(project, args.name, args.from_character, args.slug)
    except jc.JMPPEDError as e:
        print("jmpped add-character: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
