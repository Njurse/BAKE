#!/usr/bin/env python3
"""jmpped_model.py - split a character's combined model into its 14 parts.

`model.obj` is the one edit surface: the whole character in bind pose. The
splitter reads its object/group names, subtracts each part's bone bind offset,
and writes the per-part engine and Blender caches. The combined model remains
the source of truth; the part files are generated.
"""

from __future__ import annotations

import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jmpped_core as jc
import jmpped_entries as je
import jmpped_models as jm
import jmpped_textured as jt


def _abs(project: str, rel: str) -> str:
    return os.path.join(project, rel.replace("/", os.sep))


def skeleton_for(project: str, character: dict,
                 manifest: dict | None = None) -> dict:
    """Load the character's skeleton variant, bindings and bone offsets.

    Returns a dict with `skeleton`, `variant`, `bindings` (bone -> part),
    `bones` (bone name -> record) and `bone_by_model` (part -> bone).
    """
    manifest = manifest or je.load_manifest(project)
    source = manifest.get("source") or {}
    rel = (source.get("skeleton") or {}).get("json")
    if not rel:
        raise jc.JMPPEDError("project has no source.skeleton.json")
    path = _abs(project, rel)
    if not os.path.isfile(path):
        raise jc.JMPPEDError("skeleton JSON is missing: %s" % path)
    skeleton = jc.load_json(path)
    variant = character.get("skeleton", "jericho")
    variants = skeleton.get("variants") or {}
    if variant not in variants:
        raise jc.JMPPEDError(
            "skeleton has no %r variant (has %s)"
            % (variant, ", ".join(sorted(variants))))
    bindings = variants[variant].get("bindings") or {}
    bones = {b["name"]: b for b in skeleton.get("bones", [])}
    bone_by_model = {model: bone for bone, model in bindings.items()}
    return {
        "skeleton": skeleton,
        "variant": variant,
        "bindings": bindings,
        "bones": bones,
        "bone_by_model": bone_by_model,
    }


def bind_offset(bones: dict, part: str, bone_by_model: dict) -> tuple:
    bone = bone_by_model.get(part)
    if bone is None:
        raise jc.JMPPEDError("part %r has no bone binding" % part)
    rec = bones.get(bone)
    if rec is None:
        raise jc.JMPPEDError("skeleton has no bone %r for part %r"
                             % (bone, part))
    offset = rec.get("engine")
    if not isinstance(offset, list) or len(offset) != 3:
        raise jc.JMPPEDError("bone %r has no three-value engine offset" % bone)
    return tuple(float(v) for v in offset)


def model_path(project: str, character: dict) -> str:
    model = character.get("model") or {}
    rel = model.get("path")
    if not rel:
        raise jc.JMPPEDError("character %r has no model.obj path"
                             % character.get("name", "?"))
    return _abs(project, rel)


def part_names(character: dict) -> list[str]:
    return [p["part"] for p in character.get("parts", [])]


def reference_names(skeleton: dict) -> list[str]:
    return sorted(set((skeleton.get("bindings") or {}).values()))


def _source_page_sets(manifest: dict, character: dict) -> list[int]:
    """Page sets of the character this one was cloned from.

    The first choice is another character named after the source (its 23/25/2
    set is the actual material space of the combined model); the source-model
    block is the fallback for a project without a source character record.
    """
    source_name = (character.get("source_character") or "").lower()
    own_folder = character.get("folder")

    def usable(c):
        return c.get("folder") != own_folder

    for c in manifest.get("characters", []):
        if usable(c) and str(c.get("name", "")).lower() == source_name:
            sets = [int(s) for s in c.get("texture_page_sets", [])]
            if len(sets) == 3:
                return sets
    for c in manifest.get("characters", []):
        if usable(c) and (c.get("source_character") or "").lower() == source_name:
            sets = [int(s) for s in c.get("texture_page_sets", [])]
            if len(sets) == 3:
                return sets
    source = (manifest.get("source_models") or {}).get(
        character.get("source_character"))
    if source:
        sets = [int(s) for s in source.get("texture_page_sets", [])]
        if len(sets) == 3:
            return sets
    return []


def part_obj_text(name: str, verts, faces, *, blender: bool) -> str:
    """Return the deterministic part OBJ text used by write_part_obj."""
    lines = ["# JMPPED part: %s\n" % name, "o %s\n" % name]
    for x, y, z in verts:
        if blender:
            lines.append("v %.6f %.6f %.6f\n" %
                         (x * jc.BLENDER_SCALE, z * jc.BLENDER_SCALE,
                          -y * jc.BLENDER_SCALE))
        else:
            lines.append("v %.6f %.6f %.6f\n" % (x, y, z))
    uv_ids = []
    uv_count = 0
    for face in faces:
        ids = []
        for _vi, uv, _mat in face:
            if uv is None:
                ids.append(None)
            else:
                uv_count += 1
                lines.append("vt %.6f %.6f\n" % (uv[0], uv[1]))
                ids.append(uv_count)
        uv_ids.append(ids)
    for face, ids in zip(faces, uv_ids):
        material = face[0][2] if face else None
        if material is not None:
            lines.append("usemtl %s\n" % material)
        corners = []
        for (vi, _uv, _mat), uv_id in zip(face, ids):
            if uv_id is None:
                corners.append("%d" % (vi + 1))
            else:
                corners.append("%d/%d" % (vi + 1, uv_id))
        lines.append("f " + " ".join(corners) + "\n")
    return "".join(lines)


def _read_text(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def model_pending(project: str, character: dict,
                  manifest: dict | None = None) -> bool:
    """True when the combined model or one of its part caches is missing/changed."""
    model = character.get("model") or {}
    rel = model.get("path")
    if not rel:
        return True
    path = _abs(project, rel)
    if not os.path.isfile(path):
        return True
    if jc.sha256_file(path) != model.get("sha256"):
        return True
    for part in character.get("parts", []):
        for key in ("obj_edit", "obj_raw"):
            if part.get(key) and not os.path.isfile(_abs(project, part[key])):
                return True
    return False


def split_character(project: str, character: dict, scene: str | None = None,
                    raw: bool = False, force: bool = False,
                    manifest: dict | None = None) -> int:
    """Split one character's combined model into its part caches.

    Vertices are already engine units after `read_combined_obj`; subtract the
    part's bone bind offset, write the Blender and engine copies, and leave the
    primary copy's hash stale when its text changed so the baker rebuilds it.
    Returns the number of changed primary parts.
    """
    manifest = manifest or je.load_manifest(project)
    if not force and not model_pending(project, character, manifest):
        return 0

    model = character.get("model") or {}
    model_rel = model.get("path")
    if not model_rel:
        raise jc.JMPPEDError("character %r has no model.obj path"
                             % character.get("name", "?"))
    model_abs = _abs(project, model_rel)
    page_rel = model.get("page_path")
    page_abs = _abs(project, page_rel) if page_rel else None
    textured_character = (model.get("format") == jt.FORMAT
                          or model.get("uv_space") == jt.UV_SPACE)
    scene_rel = scene or model_rel
    scene_abs = (scene_rel if os.path.isabs(scene_rel)
                 else _abs(project, scene_rel))
    if not os.path.isfile(scene_abs):
        raise jc.JMPPEDError("combined model is missing: %s" % scene_abs)
    if os.path.abspath(scene_abs) != os.path.abspath(model_abs):
        if not (textured_character and page_abs
                and os.path.abspath(scene_abs) == os.path.abspath(page_abs)):
            shutil.copy2(scene_abs, model_abs)
            scene_abs = model_abs
    use_textured = textured_character and not (
        page_abs and os.path.abspath(scene_abs) == os.path.abspath(page_abs))
    if use_textured and raw:
        raise jc.JMPPEDError(
            "%s: the textured model is Blender-space; --raw is for page models"
            % character.get("name", "?"))

    data = skeleton_for(project, character, manifest)
    parts = part_names(character)
    names = set(parts) | set(data["bindings"].values())
    if use_textured:
        groups = jt.read_textured(project, character, scene_abs)
        jt.write_page_model(project, character, groups)
    else:
        groups = jm.read_combined_obj(scene_abs, names, blender=not raw)
    missing = sorted(set(parts) - set(groups))
    if missing:
        raise jc.JMPPEDError(
            "%s: combined model is missing part(s) %s; export all 14 objects "
            "named after their parts"
            % (character.get("name", "?"), ", ".join(missing)))

    changed = 0
    for part in character["parts"]:
        name = part["part"]
        offset = bind_offset(data["bones"], name, data["bone_by_model"])
        verts = [(x - offset[0], y - offset[1], z - offset[2])
                 for x, y, z in groups[name]["verts"]]
        faces = groups[name]["faces"]
        edit_path = _abs(project, part["obj_edit"])
        raw_path = _abs(project, part["obj_raw"])
        edit_text = part_obj_text(name, verts, faces, blender=True)
        raw_text = part_obj_text(name, verts, faces, blender=False)
        edit_changed = edit_text != _read_text(edit_path)
        raw_changed = raw_text != _read_text(raw_path)
        if edit_changed:
            _write_text(edit_path, edit_text)
        if raw_changed:
            _write_text(raw_path, raw_text)

        if raw:
            primary_changed = raw_changed
            if not primary_changed:
                part["obj_raw_sha256"] = jc.sha256_file(raw_path)
            else:
                part.setdefault("obj_raw_sha256", "")
            part["obj_edit_sha256"] = jc.sha256_file(edit_path)
        else:
            primary_changed = edit_changed
            if not primary_changed:
                part["obj_edit_sha256"] = jc.sha256_file(edit_path)
            else:
                part.setdefault("obj_edit_sha256", "")
            part["obj_raw_sha256"] = jc.sha256_file(raw_path)
        if primary_changed:
            changed += 1

    part_set = set(parts)
    for name in sorted(set(data["bindings"].values()) - part_set):
        if name not in groups:
            continue
        offset = bind_offset(data["bones"], name, data["bone_by_model"])
        verts = [(x - offset[0], y - offset[1], z - offset[2])
                 for x, y, z in groups[name]["verts"]]
        ref_rel = (character["folder"].rstrip("/") + "/models/reference/"
                   + name + ".obj")
        _write_text(_abs(project, ref_rel),
                    part_obj_text(name, verts, groups[name]["faces"],
                                  blender=True))

    if not character.get("texture_page_map"):
        source_sets = _source_page_sets(manifest, character)
        target_sets = [int(s) for s in
                       character.get("texture_page_sets", [])]
        if (len(source_sets) == 3 and len(target_sets) == 3
                and source_sets != target_sets):
            character["texture_page_map"] = {
                str(source_sets[i]): target_sets[i] for i in range(3)
            }

    canonical_rel = (character.get("model") or {}).get("path")
    canonical_abs = _abs(project, canonical_rel) if canonical_rel else scene_abs
    character.setdefault("model", {})["sha256"] = jc.sha256_file(canonical_abs)
    if page_abs:
        character["model"]["page_sha256"] = jc.sha256_file(page_abs)
    character["model"]["split"] = True
    index = next((i for i, c in enumerate(manifest.get("characters", []))
                  if c.get("name") == character.get("name")
                  or c.get("folder") == character.get("folder")), None)
    if index is None:
        raise jc.JMPPEDError("character %r is not in project.json"
                             % character.get("name", "?"))
    manifest["characters"][index] = character
    jc.save_json(_abs(project, "project.json"), manifest)
    jc.save_json(_abs(project, character["folder"] + "/character.json"),
                 character)
    return changed


def split_pending(project: str, force: bool = False) -> int:
    """Split every character whose combined model is new or whose caches are missing."""
    manifest = je.load_manifest(project)
    total = 0
    for character in manifest.get("characters", []):
        if force or model_pending(project, character, manifest):
            total += split_character(project, character, force=force,
                                     manifest=manifest)
    return total
