#!/usr/bin/env python3
"""jmpped_sync_source.py - refresh source-owned assets in a project.

The extracted Driver 2 level lives in `out/source/<level>/`. The first
character (Jericho) is the source-owned template; this command copies its
source models, page images and part crops from that export into the character
folder. Custom characters and the current bake are left alone.

The recorded hashes are deliberately not refreshed: the baker must see the
copied files as changed so it rebuilds the affected parts.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_entries as je
import jmpped_workspace as jws


def _copy(src: str, dst: str) -> bool:
    if not os.path.isfile(src):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def sync(source: str | None, project: str) -> int:
    project = os.path.abspath(project)
    manifest = je.load_manifest(project)
    src_root = source or (manifest.get("source") or {}).get("project")
    if src_root and not os.path.isabs(src_root):
        src_root = os.path.join(project, src_root.replace("/", os.sep))
    if src_root and not os.path.isdir(src_root):
        src_root = os.path.join(project, "out", "source")

    jws.backup_workspace(project, "sync-source")
    characters = manifest["characters"]
    if not characters:
        raise jc.JMPPEDError("project has no characters to sync")
    first = characters[0]
    copied = 0

    for part in first.get("parts", []):
        source_name = part.get("source_model")
        rec = manifest.get("source_models", {}).get(source_name)
        if not rec:
            continue
        src_part = next((p for p in rec.get("parts", [])
                         if p.get("part") == part.get("part")), None)
        if not src_part:
            continue
        for key in ("obj_raw", "obj_edit"):
            if part.get(key) and src_part.get(key):
                if _copy(je.abs_path(project, src_part[key]),
                         je.abs_path(project, part[key])):
                    copied += 1
        for crop in part.get("crops", []):
            src_crop = next(
                (c for c in src_part.get("crops", [])
                 if int(c.get("texture", -1)) == int(crop.get("texture", -2))
                 and int(c.get("page_set", -1)) == int(crop.get("page_set", -2))),
                None)
            if src_crop and src_crop.get("rgb") and crop.get("rgb"):
                if _copy(je.abs_path(project, src_crop["rgb"]),
                         je.abs_path(project, crop["rgb"])):
                    copied += 1

    for page in first.get("pages", []):
        setid = int(page["page_set"])
        owner = next((rec for rec in manifest.get("source_models", {}).values()
                      if setid in [int(s) for s in
                                   rec.get("texture_page_sets", [])]), None)
        if not owner or not (owner.get("model") or {}).get("path"):
            continue
        owner_dir = os.path.dirname(je.abs_path(project, owner["model"]["path"]))
        for key in ("png", "indexed_png", "palette_png"):
            rel = page.get(key)
            if not rel:
                continue
            name = os.path.basename(rel)
            candidates = [
                os.path.join(owner_dir, "textures", "pages", name),
                os.path.join(owner_dir, "textures", "palettes", name),
            ]
            src = next((p for p in candidates if os.path.isfile(p)), None)
            if src and _copy(src, je.abs_path(project, rel)):
                copied += 1

    print("synced %d source file(s) into %s" % (copied, first["name"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Refresh source-owned character assets")
    ap.add_argument("--source", default=None)
    ap.add_argument("--project", default=None)
    args = ap.parse_args(argv)
    project = args.project or jws.project_root()
    try:
        return sync(args.source, project)
    except jc.JMPPEDError as e:
        print("jmpped sync-source: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
