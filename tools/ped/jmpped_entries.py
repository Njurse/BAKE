#!/usr/bin/env python3
"""Helpers shared by the JMPPED authoring tools.

`project.json` is the single description of a project: its source export, the
source models, the selectable characters, the texture pages and the 14 slot
map. A character folder carries the same character record in `character.json`.
"""

from __future__ import annotations

import os
import re
import shutil

import jmpped_core as jc


def rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def load_manifest(out_root: str) -> dict:
    out_root = os.path.abspath(out_root)
    path = os.path.join(out_root, "project.json")
    if not os.path.isfile(path):
        old = os.path.join(out_root, "out", "jmppeds", "v3", "jmpped.json")
        if os.path.isfile(old):
            raise jc.JMPPEDError(
                "old workspace layout at %s; run JMPPED.bat migrate "
                "(or python jmpped.py migrate --apply)" % old)
        old_flat = os.path.join(out_root, "jmpped.json")
        if os.path.isfile(old_flat):
            raise jc.JMPPEDError(
                "old workspace layout at %s; run JMPPED.bat migrate" % old_flat)
        raise jc.JMPPEDError(
            "%s not found; run JMPPED.bat extract first" % path)
    manifest = jc.load_json(path)
    if manifest.get("project_version") != 2:
        raise jc.JMPPEDError("unsupported project version %r; re-extract the "
                             "workspace" % manifest.get("project_version"))
    if not manifest.get("characters"):
        raise jc.JMPPEDError("project has no characters[] table; re-extract")
    return manifest


def characters(manifest: dict) -> list[dict]:
    return manifest["characters"]


def character_index(manifest: dict, name: str | int) -> int:
    if isinstance(name, int):
        if name < 0 or name >= len(manifest["characters"]):
            raise jc.JMPPEDError("character index %d is out of range" % name)
        return name
    for i, c in enumerate(manifest["characters"]):
        if c["name"] == name:
            return i
    raise jc.JMPPEDError("no character named %r (characters: %s)"
                         % (name, ", ".join(c["name"]
                                            for c in manifest["characters"])))


def character(manifest: dict, name: str | int = "Jericho") -> dict:
    return manifest["characters"][character_index(manifest, name)]


def source_models(manifest: dict) -> dict:
    return manifest.get("source_models", {})


def slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name.strip()).strip("-").lower()
    if not slug:
        raise jc.JMPPEDError("character name %r has no usable folder name" % name)
    return slug


def validate_character(manifest: dict, c: dict) -> None:
    if not c.get("name") or not c.get("folder"):
        raise jc.JMPPEDError("character is missing name/folder")
    parts = c.get("parts", [])
    if len(parts) != len(jc.MODEL_NAMES):
        raise jc.JMPPEDError("character %r has %d parts; expected %d"
                             % (c["name"], len(parts), len(jc.MODEL_NAMES)))
    slots = [int(p["slot"]) for p in parts]
    if sorted(slots) != list(range(len(jc.MODEL_NAMES))):
        raise jc.JMPPEDError("character %r slots are not 0..%d"
                             % (c["name"], len(jc.MODEL_NAMES) - 1))
    sets = [int(s) for s in c.get("texture_page_sets", [])]
    if len(sets) != 3:
        raise jc.JMPPEDError("character %r has %d texture page sets; expected 3"
                             % (c["name"], len(sets)))
    page_sets = {int(p["page_set"]) for p in c.get("pages", [])}
    if page_sets != set(sets):
        raise jc.JMPPEDError("character %r page records %s do not match "
                             "texture_page_sets %s"
                             % (c["name"], sorted(page_sets), sorted(sets)))
    if not (c.get("model") or {}).get("path"):
        raise jc.JMPPEDError("character %r has no model path" % c["name"])
    for p in parts:
        for key in ("slot", "part", "obj_raw", "obj_edit"):
            if p.get(key) is None:
                raise jc.JMPPEDError("%s/%s is missing %s"
                                     % (c["name"], p.get("part", "?"), key))


# Compatibility names for the transition; the authoring vocabulary is
# character/part, but a few tools still call these entries/slots.
def entries(manifest: dict) -> list[dict]:
    return characters(manifest)


def entry_index(manifest: dict, name: str | int) -> int:
    return character_index(manifest, name)


def entry(manifest: dict, name: str | int = "Jericho") -> dict:
    return character(manifest, name)


def validate_entry(manifest: dict, e: dict) -> None:
    validate_character(manifest, e)


def map_page_sets(record: dict, mapping: dict[int, int]) -> dict:
    """Rewrite every texture page-set id in a character record."""
    def m(value):
        return int(mapping.get(int(value), int(value)))

    record["texture_page_sets"] = [m(s) for s in
                                   record.get("texture_page_sets", [])]
    for page in record.get("pages", []):
        page["page_set"] = m(page["page_set"])
    for part in record.get("parts", []):
        for material in part.get("materials", []):
            material["page_set"] = m(material["page_set"])
        for crop in part.get("crops", []):
            crop["page_set"] = m(crop["page_set"])
    for crop in record.get("texture_crops", []):
        crop["page_set"] = m(crop["page_set"])
    page_map = {str(k): int(v) for k, v in
                (record.get("texture_page_map") or {}).items()}
    for old, new in mapping.items():
        page_map[str(int(old))] = int(new)
    record["texture_page_map"] = page_map
    return record


def rewrite_paths(record: dict, old_prefix: str, new_prefix: str,
                  old_abs: str | None = None, prefix_relative: bool = False) -> dict:
    """Move a character record from one folder to another, in place."""
    old = old_prefix.rstrip("/") + "/"
    new = new_prefix.rstrip("/") + "/"

    def rw(path):
        if not path:
            return path
        p = path.replace("\\", "/")
        if p.startswith(old):
            return new + p[len(old):]
        if old_abs and os.path.isabs(p):
            try:
                rel = os.path.relpath(p, old_abs).replace("\\", "/")
            except ValueError:
                return path
            if not rel.startswith(".."):
                return new + rel
        if prefix_relative and not os.path.isabs(p) and not p.startswith(".."):
            return new + p
        return path

    record["folder"] = new_prefix
    for key in ("model", "engine_reference"):
        rec = record.get(key)
        if rec and rec.get("path"):
            rec["path"] = rw(rec["path"])
        for field in ("page_path", "mtl"):
            if rec and rec.get(field):
                rec[field] = rw(rec[field])
    for page in record.get("pages", []):
        for key in ("png", "indexed_png", "palette_png"):
            if page.get(key):
                page[key] = rw(page[key])
        for crop in page.get("texture_pages", []):
            if crop.get("png"):
                crop["png"] = rw(crop["png"])
    for part in record.get("parts", []):
        for key in ("obj_raw", "obj_edit"):
            if part.get(key):
                part[key] = rw(part[key])
        for crop in part.get("crops", []):
            if crop.get("rgb"):
                crop["rgb"] = rw(crop["rgb"])
    for crop in record.get("texture_crops", []):
        if crop.get("palette_png"):
            crop["palette_png"] = rw(crop["palette_png"])
    for extra in record.get("extra_materials", []):
        if extra.get("rgb"):
            extra["rgb"] = rw(extra["rgb"])
    return record


def material_map(source_character: dict, target_sets) -> dict[int, int]:
    old = [int(s) for s in source_character["texture_page_sets"]]
    if len(old) != 3:
        raise jc.JMPPEDError("source character %r has %d texture page sets"
                             % (source_character["name"], len(old)))
    return {old[i]: int(target_sets[i]) for i in range(3)}


_MATERIAL = re.compile(r"usemtl s(\d+)_t(\d+)")


def remap_materials(text: str, mapping: dict[int, int]) -> str:
    def sub(m):
        setid = int(m.group(1))
        new = mapping.get(setid)
        if new is None:
            raise jc.JMPPEDError("OBJ references page set %d, which is not in the "
                                 "source character" % setid)
        return "usemtl s%d_t%s" % (new, m.group(2))

    return _MATERIAL.sub(sub, text)


def copy_remapped_text(src: str, dst: str, mapping: dict[int, int]) -> None:
    with open(src, encoding="utf-8") as f:
        text = f.read()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(remap_materials(text, mapping))


def copy_file(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    shutil.copy2(src, dst)


def abs_path(root: str, rel_path: str) -> str:
    return os.path.join(root, rel_path.replace("/", os.sep))
