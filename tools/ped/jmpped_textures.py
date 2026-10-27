#!/usr/bin/env python3
"""jmpped_textures.py - split a combined part crop into one crop per material.

A character part can use several texture ids, each with its own palette row.
The builder accepts one RGB crop per material (`page_set`/`texture`); this tool
regenerates those crops from the page's indexed pixels and the material's own
palette row. Use it after importing an older combined-crop character.

    python jmpped_textures.py --character "Jericho Driv3r"
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_archive as ja
import jmpped_images as ji
import jmpped_entries as je
import jmpped_bake as jb


def _split_character(project: str, arch: ja.Archive, character: dict) -> int:
    folder = os.path.join(project, character["folder"].replace("/", os.sep))
    crops_dir = os.path.join(folder, "textures", "crops")
    os.makedirs(crops_dir, exist_ok=True)
    pages = {int(p["page_set"]): p for p in character.get("pages", [])}
    states: dict[int, dict] = {}
    changed = 0

    for part in character.get("parts", []):
        materials = part.get("materials") or []
        if not materials:
            continue
        crops = part.get("crops") or []
        if crops and all(c.get("texture") is not None and
                         c.get("palette_row") is not None for c in crops):
            continue
        new_crops = []
        for m in materials:
            page_set = int(m["page_set"])
            texture = int(m["id"])
            page = pages.get(page_set)
            if page is None:
                raise jc.JMPPEDError("%s: no page record for set %d"
                                     % (character["name"], page_set))
            if page_set not in states:
                states[page_set] = jb._entry_page_state(page, arch, project)
            state = states[page_set]
            if texture >= len(state["cluts"]):
                raise jc.JMPPEDError(
                    "%s/%s: texture %d is outside page %d's %d palette rows"
                    % (character["name"], part["part"], texture, page_set,
                       len(state["cluts"])))
            clut = ji.clut_rgb(state["cluts"][texture])
            x0, x1 = (int(v) for v in m["u"])
            y0, y1 = (int(v) for v in m["v"])
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(jc.PAGE_W - 1, x1), min(jc.PAGE_H - 1, y1)
            w, h = x1 - x0 + 1, y1 - y0 + 1
            if w <= 0 or h <= 0:
                continue
            indices = state["indices"]
            pixels = [clut[indices[y * jc.PAGE_W + x]]
                      for y in range(y0, y1 + 1)
                      for x in range(x0, x1 + 1)]
            path = os.path.join(crops_dir, "%s_set%d_t%d.png"
                                % (part["part"], page_set, texture))
            ji.save_rgb_png(path, w, h, pixels)
            new_crops.append({
                "page_set": page_set,
                "texture": texture,
                "palette_row": texture,
                "rect": [x0, y0, w, h],
                "rgb": je.rel(path, project),
                "rgb_sha256": jc.sha256_file(path),
                "polys": int(m.get("polys", 0)),
            })
        if new_crops:
            part["crops"] = new_crops
            changed += len(new_crops)
    if changed:
        jc.save_json(os.path.join(folder, "character.json"), character)
    return changed


def split_textures(project: str, character_name: str | None = None) -> int:
    project = os.path.abspath(project)
    manifest = je.load_manifest(project)
    base = je.abs_path(project, manifest["source"]["base"])
    arch = ja.read_archive(base)
    targets = ([je.character(manifest, character_name)] if character_name
               else manifest["characters"])
    changed = 0
    for character in targets:
        n = _split_character(project, arch, character)
        print("%s: %d per-material crop(s)" % (character["name"], n))
        changed += n
    if changed:
        jc.save_json(os.path.join(project, "project.json"), manifest)
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Split combined JMPPED part crops into per-material crops")
    ap.add_argument("--project", default=None)
    ap.add_argument("--character", default=None,
                    help="character name (default: every character)")
    args = ap.parse_args(argv)
    project = args.project
    if not project:
        import jmpped_workspace as jws
        project = jws.project_root()
    try:
        split_textures(project, args.character)
    except jc.JMPPEDError as e:
        print("jmpped textures: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
