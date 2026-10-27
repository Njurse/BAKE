#!/usr/bin/env python3
"""jmpped_export.py - export Driver 2's Tanner and Jericho peds for editing.

The source is the level's model table (HAVANA by default), not a previously
baked JMPPED.LEV. The exporter writes one subexport per ped:

    tanner/  the full Tanner model set, its page 2 texture/palette, per-part crops
    jericho/ the six JERI_* parts, pages 23/25, per-part crops

JMPPED v3 consumes six Jericho parts plus Tanner's eight limbs; the full Tanner
parts are still exported, with `bake_slot: null` for those the archive cannot
hold. A `base.LEV` is generated from the same level by extract_level_peds.py so the
builder has the untouched model/page bytes to preserve.

Usage:
    python jmpped_export.py [--source HAVANA.LEV] [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_archive as ja
import jmpped_models as jm
import jmpped_images as ji
import jmpped_level as src
import jmpped_workspace as jws


HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DEFAULT_SOURCE = os.path.join(REPO, "src_rebuild", "bin", "Release", "DRIVER2",
                              "LEVELS", "HAVANA.LEV")
DEFAULT_OUT = os.path.join(HERE, "out", "jmppeds", "v3")
BAKE = os.path.join(HERE, "extract_level_peds.py")


def _sha(path: str) -> str:
    return jc.sha256_file(path)


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _page_entry(page: ja.Page, root: str, ped: str, display_clut: int = 0) -> tuple[dict, list[tuple[int, int, int]]]:
    indices = ji.page_indices(page)
    cluts = ji.page_cluts(page)
    clut = ji.clut_rgb(cluts[display_clut]) if cluts else [(0, 0, 0)] * 16
    rgb = ji.page_rgb(indices, clut)
    pdir = os.path.join(root, ped, "png")

    idx_png = os.path.join(pdir, "pages", "page_%d_indexed.png" % page.set)
    pal_png = os.path.join(pdir, "palettes", "page_%d.png" % page.set)
    ji.save_indexed_png(idx_png, jc.PAGE_W, jc.PAGE_H, indices, clut)
    if cluts:
        ji.save_rgb_png(pal_png, jc.CLUT_ROW_ENTRIES, len(cluts),
                        [c for row in cluts for c in ji.clut_rgb(row)])
    else:
        ji.save_rgb_png(pal_png, 1, 1, [(0, 0, 0)])

    entry = {
        "set": page.set,
        "ped": ped,
        "storage": "compressed" if page.compressed else "raw",
        "flags": page.flags,
        "npalettes": len(cluts),
        "image_bytes": len(page.image),
        "display_clut": display_clut,
        "indexed_png": _rel(idx_png, root),
        "indexed_png_sha256": _sha(idx_png),
        "palette_png": _rel(pal_png, root),
        "palette_png_sha256": _sha(pal_png),
        "cluts_hex": [row.hex() for row in cluts],
    }
    return entry, rgb


def _part_entry(part: dict, ped: str, root: str,
                page_map: dict[int, tuple[ja.Page, list[tuple[int, int, int]]]]) -> dict:
    model = part["model"]
    name = part["name"]
    pdir = os.path.join(root, ped)
    raw_path = os.path.join(pdir, "obj", "raw", name + ".obj")
    edit_path = os.path.join(pdir, "obj", "edit", name + ".obj")
    jm.write_obj(raw_path, model, blender=False, name=name)
    jm.write_obj(edit_path, model, blender=True, name=name)

    materials: dict[tuple[int, int], dict] = {}
    for p in model.polys:
        if not p.textured:
            continue
        key = (p.set, p.id)
        rec = materials.setdefault(key, {"set": p.set, "id": p.id, "polys": 0,
                                         "u": [255, 0], "v": [255, 0]})
        rec["polys"] += 1
        for u, v in p.uv:
            rec["u"][0] = min(rec["u"][0], u)
            rec["u"][1] = max(rec["u"][1], u)
            rec["v"][0] = min(rec["v"][0], v)
            rec["v"][1] = max(rec["v"][1], v)

    crop_entries = []
    for (page_set, texture), rec in sorted(materials.items()):
        if page_set not in page_map:
            raise jc.JMPPEDError("%s/%s references page %d, which the base archive "
                                 "does not carry" % (ped, name, page_set))
        page = page_map[page_set][0]
        cluts = ji.page_cluts(page)
        # The page holds one CLUT per texture id; using row 0 for every material
        # is what made the exported crops look like the wrong palette.
        row = texture if 0 <= texture < len(cluts) else 0
        clut = ji.clut_rgb(cluts[row]) if cluts else [(0, 0, 0)] * 16
        x0, x1 = rec["u"]
        y0, y1 = rec["v"]
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(jc.PAGE_W - 1, x1)
        y1 = min(jc.PAGE_H - 1, y1)
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if w <= 0 or h <= 0:
            continue
        page_indices = ji.page_indices(page)
        crop_idx_pixels = [page_indices[y * jc.PAGE_W + x]
                           for y in range(y0, y1 + 1)
                           for x in range(x0, x1 + 1)]
        crop_pixels = ji.page_rgb(crop_idx_pixels, clut)
        crop_rgb = os.path.join(pdir, "png", "parts",
                                "%s_set%d_t%d.png" % (name, page_set, texture))
        ji.save_rgb_png(crop_rgb, w, h, crop_pixels)
        crop_entries.append({
            "page_set": page_set,
            "texture": texture,
            "display_clut": row,
            "rect": [x0, y0, w, h],
            "rgb": _rel(crop_rgb, root),
            "rgb_sha256": _sha(crop_rgb),
            "polys": rec["polys"],
        })

    return {
        "name": name,
        "bake_slot": part["bake_slot"],
        "used_in_jmpped": part["bake_slot"] is not None,
        "instance": model.instance_number,
        "vertices": len(model.vertices),
        "polys": len(model.polys),
        "poly_kinds": sorted({p.name for p in model.polys}),
        "page_sets": list(part["page_sets"]),
        "obj_raw": _rel(raw_path, root),
        "obj_raw_sha256": _sha(raw_path),
        "obj_edit": _rel(edit_path, root),
        "obj_edit_sha256": _sha(edit_path),
        # Bake-time part transform. Engine units, +Y down: a negative Y offset
        # moves the part up. The builder applies v' = v * scale + offset.
        "offset": [0, 0, 0],
        "scale": [1, 1, 1],
        "materials": [materials[k] for k in sorted(materials)],
        "crops": crop_entries,
    }


def _write_assembled(path: str, models: list, names: list[str],
                     offsets: dict[int, tuple[float, float, float]],
                     blender: bool) -> dict:
    jm.write_assembled_obj(path, models, names, offsets, blender=blender)
    return {"path": path, "sha256": _sha(path)}


def _scene_entry(raw: dict, edit: dict, root: str) -> dict:
    return {
        "raw": _rel(raw["path"], root),
        "raw_sha256": raw["sha256"],
        "edit": _rel(edit["path"], root),
        "edit_sha256": edit["sha256"],
    }


def _write_skeleton_obj(path: str, bones: dict, blender: bool) -> dict:
    """Write the bind skeleton as parent->child edges.

    Raw mode is engine units with +Y down; Blender mode uses the same (x, z, -y)
    transform and 1/1024 scale as the mesh edit copy.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    idx = 1
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# JMPPED bind skeleton (%s)\n" %
                ("Blender Z-up metres" if blender else "engine units, +Y down"))
        for i, name in enumerate(src.BONE_NAMES):
            parent = src.BONE_PARENTS[i]
            if parent < 0:
                continue
            f.write("o %s\n" % name)
            for raw in (bones["draw"][parent], bones["draw"][i]):
                if blender:
                    f.write("v %.6f %.6f %.6f\n" %
                            (raw[0] * jc.BLENDER_SCALE, raw[2] * jc.BLENDER_SCALE,
                             -raw[1] * jc.BLENDER_SCALE))
                else:
                    f.write("v %d %d %d\n" % raw)
            f.write("l %d %d\n" % (idx, idx + 1))
            idx += 2
    return {"path": path, "sha256": _sha(path)}


def _bindpose(parts_by_name: dict[str, dict], bindings: dict[str, str],
              bones: dict) -> tuple[list, list[str], dict[int, tuple[int, int, int]]]:
    models = []
    names = []
    offsets: dict[int, tuple[int, int, int]] = {}
    for i, bone in enumerate(src.BONE_NAMES):
        model_name = bindings.get(bone)
        if not model_name:
            continue
        part = parts_by_name.get(model_name)
        if part is None:
            continue
        models.append(part["model"])
        names.append(model_name)
        offsets[len(models) - 1] = tuple(bones["draw"][i])
    return models, names, offsets


def _legacy_export(source: str, out_root: str, reset: bool = False) -> dict:
    if not os.path.isfile(source):
        raise jc.JMPPEDError("source level %s does not exist; pass --source"
                             % source)
    out_root = os.path.abspath(out_root)
    manifest_path = os.path.join(out_root, "jmpped.json")
    if os.path.isfile(manifest_path):
        if not reset:
            raise jc.JMPPEDError(
                "%s already has jmpped.json. Refusing to overwrite the "
                "workspace; pass --reset (the contents will be backed up) "
                "or choose another --out." % out_root)
        backup = jws.backup_workspace(out_root, "export reset")
        if backup:
            print("backed up workspace -> %s" % backup)
    os.makedirs(out_root, exist_ok=True)
    base = os.path.join(out_root, "base.LEV")
    cmd = [sys.executable, BAKE, source, base, "--no-copy"]
    print("extracting Driver 2 peds from %s" % source)
    subprocess.run(cmd, check=True)

    arch = ja.read_archive(base)
    if arch.version != jc.VERSION_V3:
        raise jc.JMPPEDError("the source baker produced v%d, expected v3"
                             % arch.version)
    if len(arch.models) != 14:
        raise jc.JMPPEDError("the source baker produced %d models, expected 14"
                             % len(arch.models))
    needed_pages = {2, 23, 25}
    have_pages = {p.set for p in arch.pages}
    if not needed_pages.issubset(have_pages):
        raise jc.JMPPEDError("base archive is missing pages %s"
                             % sorted(needed_pages - have_pages))

    # Canonical v5 form of the untouched source set. A no-edit build of entry 0
    # must reproduce this byte-for-byte.
    v5_entry = ja.Entry("Jericho", arch.models, arch.pages,
                        arch.raw_models, arch.raw_pages)
    v5_arch = ja.Archive(version=jc.VERSION_V5, model_names=jc.MODEL_NAMES,
                         models=arch.models, pages=arch.pages,
                         raw_models=arch.raw_models, raw_pages=arch.raw_pages,
                         entries=[v5_entry])
    base_v5 = os.path.join(out_root, "base_v5.LEV")
    ja.write_archive(base_v5, v5_arch)

    level = src.read_level(source)
    skeleton = src.skeleton_bones(level)
    if skeleton is None:
        raise jc.JMPPEDError("%s has no usable LUMP_MOTIONCAPTURE bind pose"
                             % source)
    skel_dir = os.path.join(out_root, "skeleton")
    skel_json = os.path.join(skel_dir, "skeleton.json")
    skel_data = {
        "source_level": os.path.abspath(source),
        "motion_lump_size": skeleton["motion_size"],
        "coordinate_system": "motion block is +Y UP; the engine draw path and "
                             "the mesh use +Y DOWN via (x, -y, -z); the Blender "
                             "copy uses (x, z, -y) and scale %.10f"
                             % jc.BLENDER_SCALE,
        "bones": [
            {"name": name, "parent": src.BONE_PARENTS[i],
             "motion_raw": list(skeleton["raw"][i]),
             "engine": list(skeleton["draw"][i])}
            for i, name in enumerate(src.BONE_NAMES)
        ],
        "variants": {
            "tanner": {"bindings": src.TANNER_BINDINGS},
            "jericho": {"bindings": src.JERICHO_BINDINGS},
        },
        "note": "engine positions are the motion bind pose with the engine's "
                "Y/Z negation applied; model bindings show which source part "
                "sits on each bone",
    }
    jc.save_json(skel_json, skel_data)
    skel_raw = _write_skeleton_obj(os.path.join(skel_dir, "skeleton_raw.obj"),
                                   skeleton, False)
    skel_edit = _write_skeleton_obj(os.path.join(skel_dir, "skeleton_edit.obj"),
                                    skeleton, True)
    skel_manifest = {
        "json": _rel(skel_json, out_root),
        "json_sha256": _sha(skel_json),
        "raw": _rel(skel_raw["path"], out_root),
        "raw_sha256": skel_raw["sha256"],
        "edit": _rel(skel_edit["path"], out_root),
        "edit_sha256": skel_edit["sha256"],
    }

    ped_parts = {}
    all_parts_by_name = {}
    page_map: dict[int, tuple[ja.Page, list[tuple[int, int, int]]]] = {}
    pages = []
    for ped in ("tanner", "jericho"):
        parts = src.ped_parts(level, ped)
        src.check_level_pages(level, parts, ped)
        ped_parts[ped] = parts
        for part in parts:
            all_parts_by_name[part["name"]] = part
        for page in arch.pages:
            if page.set not in src.PED_PAGES[ped]:
                continue
            entry, rgb = _page_entry(page, out_root, ped)
            pages.append(entry)
            page_map[page.set] = (page, rgb)

    peds = {}
    for ped, parts in ped_parts.items():
        pdir = os.path.join(out_root, ped)
        entries = [_part_entry(p, ped, out_root, page_map) for p in parts]
        by_name = {p["name"]: p for p in parts}
        if ped == "tanner":
            asm_models = [by_name[n]["model"] for n in src.TANNER_ASSEMBLY_ORDER
                          if n in by_name]
            asm_names = [n for n in src.TANNER_ASSEMBLY_ORDER if n in by_name]
            bind_parts = by_name
            bindings = src.TANNER_BINDINGS
        else:
            asm_models = [by_name[n]["model"] for n in src.JERICHO_NAMES
                          if n in by_name]
            asm_names = [n for n in src.JERICHO_NAMES if n in by_name]
            bind_parts = all_parts_by_name
            bindings = src.JERICHO_BINDINGS
        offsets = jm.part_offsets(asm_models)
        assembled = {}
        if asm_models:
            assembled = _scene_entry(
                _write_assembled(os.path.join(pdir, "obj", "assembled_raw.obj"),
                                 asm_models, asm_names, offsets, False),
                _write_assembled(os.path.join(pdir, "obj", "assembled_edit.obj"),
                                 asm_models, asm_names, offsets, True),
                out_root)

        bp_models, bp_names, bp_offsets = _bindpose(bind_parts, bindings, skeleton)
        bindpose = {}
        if bp_models:
            bindpose = _scene_entry(
                _write_assembled(os.path.join(pdir, "obj", "bindpose_raw.obj"),
                                 bp_models, bp_names, bp_offsets, False),
                _write_assembled(os.path.join(pdir, "obj", "bindpose_edit.obj"),
                                 bp_models, bp_names, bp_offsets, True),
                out_root)

        peds[ped] = {
            "label": "Tanner" if ped == "tanner" else "Jericho",
            "pages": sorted(src.PED_PAGES[ped]),
            "parts": entries,
            "assembled": assembled,
            "bindpose": bindpose,
            "skeleton": _rel(skel_json, out_root),
        }
        ped_json = {
            "ped": ped,
            "label": peds[ped]["label"],
            "source_level": os.path.abspath(source),
            "source_sha256": jc.sha256_file(source),
            "pages": peds[ped]["pages"],
            "parts": entries,
            "assembled": assembled,
            "bindpose": bindpose,
            "skeleton": _rel(skel_json, out_root),
            "note": "parts with bake_slot null are exported for reference and "
                    "are not stored in the v3 JMPPED archive",
        }
        jc.save_json(os.path.join(pdir, "ped.json"), ped_json)

    # Verify the source models the archive consumes are the same blobs the base
    # archive carries, so a later edit cannot silently bake the wrong geometry.
    slots = []
    by_slot = {}
    for ped, parts in ped_parts.items():
        for part in parts:
            slot = part["bake_slot"]
            if slot is None:
                continue
            if slot in by_slot:
                raise jc.JMPPEDError("slot %d is claimed by both %s and %s"
                                     % (slot, by_slot[slot], (ped, part["name"])))
            by_slot[slot] = (ped, part["name"])
            if part["blob"] != arch.raw_models[slot]:
                raise jc.JMPPEDError(
                    "%s/%s does not match base slot %d; the level and the base "
                    "archive disagree" % (ped, part["name"], slot))
            slots.append({"slot": slot, "ped": ped, "part": part["name"]})
    slots.sort(key=lambda s: s["slot"])
    if [s["slot"] for s in slots] != list(range(14)):
        raise jc.JMPPEDError("the source does not cover all 14 JMPPED v3 slots")

    # v5 authoring table. Entry 0 is the Jericho set; jmpped_add_entry.py appends
    # later entries with their own page sets and directories. The slots list is
    # the 14 archive slots, not just the six JERI_* source parts: the Tanner
    # limbs are part of the playable Jericho entry.
    part_index = {}
    for ped_name, ped_data in peds.items():
        for part in ped_data["parts"]:
            part_index[(ped_name, part["name"])] = part

    entry_sets = list(jc.entry_page_sets(0))
    entry_pages = [p for p in pages if p["set"] in entry_sets]
    entry_pages.sort(key=lambda p: entry_sets.index(p["set"]))
    if [p["set"] for p in entry_pages] != entry_sets:
        raise jc.JMPPEDError("entry 0 is missing pages %s"
                             % (set(entry_sets) - {p["set"] for p in entry_pages}))

    slot_descriptors = []
    for s in slots:
        part = part_index.get((s["ped"], s["part"]))
        if part is None:
            raise jc.JMPPEDError("slot %d points at missing %s/%s"
                                 % (s["slot"], s["ped"], s["part"]))
        slot_descriptors.append({
            "slot": s["slot"],
            "ped": s["ped"],
            "part": s["part"],
            "page_sets": part["page_sets"],
            "obj_raw": part["obj_raw"],
            "obj_raw_sha256": part["obj_raw_sha256"],
            "obj_edit": part["obj_edit"],
            "obj_edit_sha256": part["obj_edit_sha256"],
            "offset": list(part.get("offset", [0, 0, 0])),
            "scale": list(part.get("scale", [1, 1, 1])),
            "materials": part["materials"],
            "crops": part["crops"],
        })

    palette_crops = [
        {
            "set": p["set"],
            "palette_png": p["palette_png"],
            "palette_png_sha256": p["palette_png_sha256"],
            "cluts_hex": p["cluts_hex"],
            "display_clut": p["display_clut"],
        }
        for p in entry_pages
    ]

    entries = [{
        "index": 0,
        "name": "Jericho",
        "directory": "jericho",
        "source_ped": "jericho",
        "page_sets": entry_sets,
        "pages": entry_pages,
        "slots": slot_descriptors,
        "bindpose": peds["jericho"]["bindpose"],
        "palette_crops": palette_crops,
    }]

    jmpped_dir = os.path.join(out_root, "jmpped")
    jmpped_offsets = jm.part_offsets(arch.models)
    ref_raw = _write_assembled(os.path.join(jmpped_dir, "assembled_raw.obj"),
                               arch.models, arch.model_names, jmpped_offsets, False)
    ref_edit = _write_assembled(os.path.join(jmpped_dir, "assembled_edit.obj"),
                                arch.models, arch.model_names, jmpped_offsets, True)

    manifest = {
        "manifest_version": 2,
        "tool": "jmpped_export.py",
        "source_level": os.path.abspath(source),
        "source_sha256": jc.sha256_file(source),
        "base_archive": "base.LEV",
        "base_archive_sha256": jc.sha256_file(base),
        "archive_version": jc.VERSION_V5,
        "base_v5_archive": "base_v5.LEV",
        "base_v5_archive_sha256": jc.sha256_file(base_v5),
        "peds": peds,
        "entries": entries,
        "pages": pages,
        "skeleton": skel_manifest,
        "bake": {"slots": slots},
        "jmpped_reference": {
            "raw": _rel(ref_raw["path"], out_root),
            "raw_sha256": ref_raw["sha256"],
            "edit": _rel(ref_edit["path"], out_root),
            "edit_sha256": ref_edit["sha256"],
            "note": "visual reference for the 14-slot v3 bake, not the edit input",
        },
    }
    jc.save_json(os.path.join(out_root, "jmpped.json"), manifest)
    return manifest


def _level_slug(source: str) -> str:
    name = os.path.splitext(os.path.basename(source.replace("\\", "/")))[0]
    return name.strip().lower() or "level"


def _skeleton_variant(stage: str, project: dict, character: dict) -> str:
    """Pick the source character's skeleton variant from the staged skeleton."""
    rel = ((project.get("source") or {}).get("skeleton") or {}).get("json")
    if rel:
        path = rel if os.path.isabs(rel) else os.path.join(
            stage, rel.replace("/", os.sep))
        if os.path.isfile(path):
            data = jc.load_json(path)
            variants = sorted((data.get("variants") or {}).keys())
            if "jericho" in variants:
                return "jericho"
            if variants:
                return variants[0]
    return character.get("skeleton") or "jericho"


def _install_dir(src: str, dst: str, protect_name: str | None = None) -> None:
    if not os.path.isdir(src):
        return
    if os.path.exists(dst):
        if protect_name:
            char_path = os.path.join(dst, "character.json")
            if os.path.isfile(char_path):
                existing = jc.load_json(char_path)
                if existing.get("name") != protect_name:
                    raise jc.JMPPEDError(
                        "refusing to replace character folder %s (it contains "
                        "%r); move it first or extract under a different name"
                        % (dst, existing.get("name")))
            elif os.path.isfile(os.path.join(dst, "model_textured.obj")):
                raise jc.JMPPEDError(
                    "refusing to replace custom character folder %s; move it "
                    "first" % dst)
            elif os.path.isfile(os.path.join(dst, "source.json")):
                raise jc.JMPPEDError(
                    "refusing to replace folder %s: it looks like a source "
                    "model, not the source character" % dst)
        shutil.rmtree(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copytree(src, dst)


def extract(source: str | None, project: str, reset: bool = False,
            level: str | None = None) -> int:
    """Extract a Driver 2 level and install it as the project's source export.

    The source extractor still writes the old workspace shape internally; it is
    staged under .state/ and immediately migrated, so the installed tree is the
    new layout. Custom characters are kept; only the source character, the
    source export and the build output are replaced.
    """
    source = os.path.abspath(source or DEFAULT_SOURCE)
    if not os.path.isfile(source):
        raise jc.JMPPEDError("source level %s does not exist" % source)
    project = os.path.abspath(project)
    level = level or _level_slug(source)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stage = os.path.join(project, ".state", "extract-%s" % stamp)
    old_out = os.path.join(stage, "out", "jmppeds", "v3")
    os.makedirs(old_out, exist_ok=True)
    try:
        _legacy_export(source, old_out, reset=True)
        import jmpped_migrate
        jmpped_migrate.migrate(stage, apply=True, level=level)
        new_project = jc.load_json(os.path.join(stage, "project.json"))
        old_path = os.path.join(project, "project.json")
        old_project = jc.load_json(old_path) if os.path.isfile(old_path) else None
        new_chars = new_project["characters"]
        if not new_chars:
            raise jc.JMPPEDError("extract produced no characters")
        source_char = new_chars[0]
        if not source_char.get("skeleton"):
            source_char["skeleton"] = _skeleton_variant(stage, new_project,
                                                       source_char)
        keep = []
        if old_project:
            source_sets = {int(s) for s in source_char["texture_page_sets"]}
            for c in old_project.get("characters", []):
                if c.get("name") == source_char["name"]:
                    continue
                overlap = source_sets & {int(s) for s in
                                         c.get("texture_page_sets", [])}
                if overlap:
                    raise jc.JMPPEDError(
                        "custom character %r owns texture pages %s, which the "
                        "source character needs; move it first"
                        % (c.get("name"), sorted(overlap)))
                keep.append(c)
        new_project["characters"] = [source_char] + keep
        new_project["tool"] = "jmpped.py"
        new_project.pop("migrated_from", None)

        jws.backup_workspace(project, "extract")
        _install_dir(os.path.join(stage, "out", "source", level),
                     os.path.join(project, "out", "source", level))
        _install_dir(os.path.join(stage, "out", "build"),
                     os.path.join(project, "out", "build"))
        slug = os.path.basename(source_char["folder"].rstrip("/"))
        _install_dir(os.path.join(stage, "characters", slug),
                     os.path.join(project, "characters", slug),
                     protect_name=source_char.get("name"))
        jc.save_json(os.path.join(project, "project.json"), new_project)
        jc.save_json(os.path.join(project, source_char["folder"],
                                  "character.json"), source_char)
        jws.set_project(project)
        jws.set_source(os.path.join(project, "out", "source", level))
        print("extracted %s -> %s (%d character(s) kept)"
              % (source, os.path.join(project, "out", "source", level),
                 len(new_project["characters"])))
        return 0
    finally:
        if os.path.isdir(stage):
            shutil.rmtree(stage)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract Driver 2 peds into out/source/<level>")
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="Driver 2 level (default: Release/DRIVER2/LEVELS/HAVANA.LEV)")
    ap.add_argument("--project", default=None,
                    help="project root (default: last-used)")
    ap.add_argument("--level", default=None,
                    help="source export name (default: the level filename)")
    ap.add_argument("--reset", action="store_true",
                    help="replace an existing source export (custom characters are kept)")
    args = ap.parse_args(argv)
    project = args.project or jws.project_root()
    try:
        return extract(args.source, project, args.reset, args.level)
    except jc.JMPPEDError as e:
        print("jmpped extract: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
